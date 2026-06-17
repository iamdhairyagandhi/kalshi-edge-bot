"""
StatsBomb open-data downloader.

Reads the public GitHub repo:
    https://github.com/statsbomb/open-data

We only pull what the model layer needs:
  - matches/{competition_id}/{season_id}.json
  - lineups/{match_id}.json
  - events/{match_id}.json   (optional, for xG / shot data)

The class supports an optional offline cache directory: if a file exists
under cache_dir at the same relative path, we read from it; otherwise we
fetch and (when cache_dir is writable) persist.

This is read-only and respects StatsBomb's open-data terms (free for
non-commercial use; attribution required).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional

import httpx


_LOG = logging.getLogger(__name__)
_BASE = "https://raw.githubusercontent.com/statsbomb/open-data/master/data"


def _make_verify() -> object:
    """Return an httpx-compatible `verify` value that respects the system
    trust store (handles corp self-signed proxies via `truststore`)."""
    try:
        import ssl

        import truststore  # type: ignore

        ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        return ctx
    except ImportError:
        return True


@dataclass
class StatsBombOpenData:
    cache_dir: Optional[str] = None
    timeout_s: float = 15.0
    user_agent: str = "kalshi-edge-bot/0.2 (+https://github.com/iamdhairyagandhi/kalshi-edge-bot)"

    _client: Optional[httpx.Client] = field(default=None, init=False, repr=False)

    # ------------------------------------------------------------------
    def __enter__(self) -> "StatsBombOpenData":
        self._client = httpx.Client(
            timeout=self.timeout_s,
            headers={"User-Agent": self.user_agent},
            verify=_make_verify(),
            follow_redirects=True,
        )
        return self

    def __exit__(self, *exc) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    # ------------------------------------------------------------------
    def _client_or_make(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=self.timeout_s,
                headers={"User-Agent": self.user_agent},
                verify=_make_verify(),
                follow_redirects=True,
            )
        return self._client

    def _cache_path(self, rel: str) -> Optional[Path]:
        if not self.cache_dir:
            return None
        p = Path(self.cache_dir) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def _get_json(self, rel_path: str) -> object:
        cache = self._cache_path(rel_path)
        if cache is not None and cache.exists():
            with cache.open("r", encoding="utf-8") as fh:
                return json.load(fh)
        url = f"{_BASE}/{rel_path}"
        _LOG.debug("statsbomb fetch %s", url)
        r = self._client_or_make().get(url)
        r.raise_for_status()
        data = r.json()
        if cache is not None:
            try:
                with cache.open("w", encoding="utf-8") as fh:
                    json.dump(data, fh)
            except OSError as e:  # pragma: no cover
                _LOG.warning("failed to write statsbomb cache %s: %s", cache, e)
        return data

    # ------------------------------------------------------------------
    # public read API
    # ------------------------------------------------------------------
    def competitions(self) -> List[dict]:
        return list(self._get_json("competitions.json"))  # type: ignore[arg-type]

    def matches(self, competition_id: int, season_id: int) -> List[dict]:
        return list(self._get_json(f"matches/{competition_id}/{season_id}.json"))  # type: ignore[arg-type]

    def lineups(self, match_id: int) -> List[dict]:
        return list(self._get_json(f"lineups/{match_id}.json"))  # type: ignore[arg-type]

    def events(self, match_id: int) -> List[dict]:
        return list(self._get_json(f"events/{match_id}.json"))  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    # adapters that conform to our internal schema
    # ------------------------------------------------------------------
    def matches_for_dixon_coles(
        self,
        competition_id: int,
        season_id: int,
        *,
        neutral_default: bool = False,
    ) -> List[Dict[str, object]]:
        """Convert StatsBomb match rows to {match_id, home_team_id, away_team_id,
        home_goals, away_goals, kickoff_unix, neutral_venue, competition,
        home_team_name, away_team_name, home_country, away_country}."""
        out: List[Dict[str, object]] = []
        for m in self.matches(competition_id, season_id):
            try:
                kickoff = m.get("kick_off") or "00:00:00"
                date = m.get("match_date")
                # cheap unix-ish ts from date+time, minute-precision is fine
                from datetime import datetime, timezone
                dt = datetime.fromisoformat(f"{date}T{kickoff}").replace(tzinfo=timezone.utc)
                unix = int(dt.timestamp())
                home = m["home_team"]
                away = m["away_team"]
                out.append({
                    "match_id": str(m.get("match_id", "")),
                    "home_team_id": str(home["home_team_id"]),
                    "away_team_id": str(away["away_team_id"]),
                    "home_team_name": str(home.get("home_team_name", "")),
                    "away_team_name": str(away.get("away_team_name", "")),
                    "home_country": str(home.get("country", {}).get("name", "")) if isinstance(home.get("country"), dict) else "",
                    "away_country": str(away.get("country", {}).get("name", "")) if isinstance(away.get("country"), dict) else "",
                    "home_goals": int(m.get("home_score", 0)),
                    "away_goals": int(m.get("away_score", 0)),
                    "kickoff_unix": unix,
                    "neutral_venue": bool(m.get("neutral", neutral_default)),
                    "competition": str(m.get("competition", {}).get("competition_name", "")),
                    "season": str(m.get("season", {}).get("season_name", "")),
                })
            except (KeyError, ValueError) as e:  # pragma: no cover
                _LOG.warning("skipping malformed statsbomb match: %s", e)
        return out

    def lineups_for_player_share(self, match_id: int) -> List[Dict[str, str]]:
        """Flatten lineups into {team_id, player_id, position} rows."""
        out: List[Dict[str, str]] = []
        for team in self.lineups(match_id):
            tid = str(team["team_id"])
            for p in team.get("lineup", []):
                pos = ""
                # StatsBomb position id 1=GK, 2-9 = DF/MF, 10-25 = MF, etc.
                # We accept either "position_id" or a textual hint and bucket it.
                if "positions" in p and p["positions"]:
                    name = str(p["positions"][0].get("position", "")).lower()
                else:
                    name = ""
                if "goalkeeper" in name:
                    pos = "GK"
                elif "back" in name or "defender" in name:
                    pos = "DF"
                elif "midfield" in name or "wing" in name:
                    pos = "MF"
                elif "forward" in name or "striker" in name or "centre forward" in name:
                    pos = "FW"
                else:
                    pos = "MF"
                out.append({
                    "team_id": tid,
                    "player_id": str(p["player_id"]),
                    "name": str(p.get("player_name", "")),
                    "position": pos,
                })
        return out

    def player_goals_history(
        self,
        match_ids: Iterable[int],
    ) -> List[Dict[str, object]]:
        """Aggregate goals + minutes per (team, player) across many matches.

        Returns rows compatible with PlayerShareModel.fit.
        """
        agg: Dict[tuple, Dict[str, object]] = {}
        for mid in match_ids:
            try:
                events = self.events(mid)
            except (httpx.HTTPError, OSError):  # pragma: no cover
                _LOG.warning("could not load events for %s", mid)
                continue
            # crude minutes: assume 95 for everyone in lineups; refine if we
            # later parse Substitution events.
            try:
                lineups = self.lineups(mid)
            except (httpx.HTTPError, OSError):  # pragma: no cover
                lineups = []
            for team in lineups:
                tid = str(team["team_id"])
                for p in team.get("lineup", []):
                    pid = str(p["player_id"])
                    key = (tid, pid)
                    pos = ""
                    if "positions" in p and p["positions"]:
                        nm = str(p["positions"][0].get("position", "")).lower()
                        if "goalkeeper" in nm:
                            pos = "GK"
                        elif "back" in nm or "defender" in nm:
                            pos = "DF"
                        elif "midfield" in nm or "wing" in nm:
                            pos = "MF"
                        elif "forward" in nm or "striker" in nm:
                            pos = "FW"
                        else:
                            pos = "MF"
                    row = agg.setdefault(key, {
                        "team_id": tid, "player_id": pid,
                        "position": pos, "goals": 0, "minutes": 0,
                    })
                    row["minutes"] = int(row["minutes"]) + 95  # approx
            for ev in events:
                # StatsBomb shot event with outcome=Goal counts as a goal.
                if ev.get("type", {}).get("name") == "Shot":
                    shot = ev.get("shot", {})
                    if shot.get("outcome", {}).get("name") == "Goal":
                        tid = str(ev.get("team", {}).get("id", ""))
                        pid = str(ev.get("player", {}).get("id", ""))
                        if not tid or not pid:
                            continue
                        key = (tid, pid)
                        row = agg.setdefault(key, {
                            "team_id": tid, "player_id": pid,
                            "position": "FW", "goals": 0, "minutes": 0,
                        })
                        row["goals"] = int(row["goals"]) + 1
        return list(agg.values())
