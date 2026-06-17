"""
SQLite store for the soccer pipeline.

Schema (kept on its own DB so it doesn't crowd the trading paper.db):
  teams           : id, name, country, elo
  players         : id, team_id, name, position, start_prob, minutes_avg
  fixtures        : id, home_team_id, away_team_id, kickoff_unix, competition,
                    neutral_venue, referee_id, status
  predictions     : id, fixture_id, market_type, leg_json, fair_probability,
                    fair_decimal_odds, book_decimal_odds, pinnacle_close_decimal,
                    edge, kelly_fraction, recommendation, recorded_unix, outcome
  bet_builder_quotes : id, fixture_id, legs_json, fair_probability, book_decimal_odds,
                       edge, kelly_fraction, recommendation, recorded_unix, outcome

Reads can run concurrently with the writer (SQLite WAL).
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, List, Mapping, Optional

from src.sports.soccer.types import Player, Team


_SCHEMA = """
CREATE TABLE IF NOT EXISTS teams (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    country      TEXT,
    elo          REAL DEFAULT 1500.0,
    attack       REAL DEFAULT 0.0,
    defense      REAL DEFAULT 0.0,
    updated_at   INTEGER
);

CREATE TABLE IF NOT EXISTS players (
    id           TEXT PRIMARY KEY,
    team_id      TEXT NOT NULL,
    name         TEXT NOT NULL,
    position     TEXT NOT NULL DEFAULT 'FW',
    start_prob   REAL DEFAULT 0.5,
    minutes_avg  REAL DEFAULT 60.0,
    goal_share   REAL DEFAULT 0.0,
    yellow_per90 REAL DEFAULT 0.18,
    red_per90    REAL DEFAULT 0.005
);

CREATE TABLE IF NOT EXISTS fixtures (
    id              TEXT PRIMARY KEY,
    home_team_id    TEXT NOT NULL,
    away_team_id    TEXT NOT NULL,
    kickoff_unix    INTEGER NOT NULL,
    competition     TEXT,
    neutral_venue   INTEGER NOT NULL DEFAULT 1,
    referee_id      TEXT,
    status          TEXT NOT NULL DEFAULT 'SCHEDULED'
);

CREATE TABLE IF NOT EXISTS predictions (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    fixture_id               TEXT NOT NULL,
    market_type              TEXT NOT NULL,
    leg_json                 TEXT NOT NULL,
    fair_probability         REAL NOT NULL,
    fair_decimal_odds        REAL NOT NULL,
    book_decimal_odds        REAL,
    pinnacle_close_decimal   REAL,
    edge                     REAL,
    kelly_fraction           REAL,
    recommendation           TEXT,
    recorded_unix            INTEGER NOT NULL,
    outcome                  INTEGER
);

CREATE TABLE IF NOT EXISTS bet_builder_quotes (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    fixture_id        TEXT NOT NULL,
    legs_json         TEXT NOT NULL,
    fair_probability  REAL NOT NULL,
    book_decimal_odds REAL,
    edge              REAL,
    kelly_fraction    REAL,
    recommendation    TEXT,
    recorded_unix     INTEGER NOT NULL,
    outcome           INTEGER
);

CREATE TABLE IF NOT EXISTS historical_matches (
    id              TEXT PRIMARY KEY,
    home_team_id    TEXT NOT NULL,
    away_team_id    TEXT NOT NULL,
    home_goals      INTEGER NOT NULL,
    away_goals      INTEGER NOT NULL,
    kickoff_unix    INTEGER NOT NULL,
    neutral_venue   INTEGER NOT NULL DEFAULT 1,
    competition     TEXT
);

CREATE INDEX IF NOT EXISTS idx_predictions_fixture ON predictions(fixture_id);
CREATE INDEX IF NOT EXISTS idx_predictions_market  ON predictions(market_type);
CREATE INDEX IF NOT EXISTS idx_fixtures_kickoff    ON fixtures(kickoff_unix);
CREATE INDEX IF NOT EXISTS idx_history_kickoff     ON historical_matches(kickoff_unix);
"""


@dataclass
class SoccerStore:
    """Read/write SQLite store. Use `with` blocks; commits per write."""
    path: str

    def __post_init__(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA foreign_keys = ON")
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._conn() as c:
            c.executescript(_SCHEMA)
            # Idempotent migrations for columns added after the initial
            # schema was deployed. SQLite raises OperationalError if the
            # column already exists; we swallow that.
            for ddl in (
                "ALTER TABLE historical_matches ADD COLUMN home_xg REAL",
                "ALTER TABLE historical_matches ADD COLUMN away_xg REAL",
            ):
                try:
                    c.execute(ddl)
                except sqlite3.OperationalError:
                    pass

    # ------------------------------------------------------------------
    # writes
    # ------------------------------------------------------------------
    def upsert_teams(self, teams: Iterable[Team], *, now_unix: Optional[int] = None) -> int:
        rows = []
        for t in teams:
            rows.append((t.team_id, t.name, t.country, t.elo, t.attack, t.defense, now_unix))
        with self._conn() as c:
            c.executemany(
                """INSERT INTO teams(id, name, country, elo, attack, defense, updated_at)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     name=excluded.name, country=excluded.country,
                     elo=excluded.elo, attack=excluded.attack,
                     defense=excluded.defense, updated_at=excluded.updated_at""",
                rows,
            )
            return len(rows)

    def upsert_players(self, players: Iterable[Player]) -> int:
        rows = [
            (
                p.player_id, p.team_id, p.name, p.position,
                p.start_prob, p.minutes_avg, p.goal_share,
                p.yellow_rate_per90, p.red_rate_per90,
            )
            for p in players
        ]
        with self._conn() as c:
            c.executemany(
                """INSERT INTO players(id, team_id, name, position, start_prob,
                                       minutes_avg, goal_share, yellow_per90, red_per90)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     team_id=excluded.team_id, name=excluded.name, position=excluded.position,
                     start_prob=excluded.start_prob, minutes_avg=excluded.minutes_avg,
                     goal_share=excluded.goal_share, yellow_per90=excluded.yellow_per90,
                     red_per90=excluded.red_per90""",
                rows,
            )
            return len(rows)

    def upsert_fixtures(self, fixtures: Iterable[Mapping[str, object]]) -> int:
        rows = [
            (
                str(f["fixture_id"]),
                str(f["home_team_id"]),
                str(f["away_team_id"]),
                int(f["kickoff_unix"]),
                str(f.get("competition", "FIFA WC")),
                1 if bool(f.get("neutral_venue", True)) else 0,
                f.get("referee_id"),
                str(f.get("status", "SCHEDULED")),
            )
            for f in fixtures
        ]
        with self._conn() as c:
            c.executemany(
                """INSERT INTO fixtures(id, home_team_id, away_team_id, kickoff_unix,
                                        competition, neutral_venue, referee_id, status)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     home_team_id=excluded.home_team_id,
                     away_team_id=excluded.away_team_id,
                     kickoff_unix=excluded.kickoff_unix,
                     competition=excluded.competition,
                     neutral_venue=excluded.neutral_venue,
                     referee_id=excluded.referee_id,
                     status=excluded.status""",
                rows,
            )
            return len(rows)

    def record_prediction(
        self,
        *,
        fixture_id: str,
        market_type: str,
        leg: Mapping[str, object],
        fair_probability: float,
        fair_decimal_odds: float,
        book_decimal_odds: Optional[float],
        pinnacle_close_decimal: Optional[float],
        edge: Optional[float],
        kelly_fraction: Optional[float],
        recommendation: str,
        recorded_unix: int,
    ) -> int:
        with self._conn() as c:
            cur = c.execute(
                """INSERT INTO predictions(
                       fixture_id, market_type, leg_json, fair_probability,
                       fair_decimal_odds, book_decimal_odds,
                       pinnacle_close_decimal, edge, kelly_fraction,
                       recommendation, recorded_unix)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    fixture_id, market_type, json.dumps(dict(leg)),
                    float(fair_probability), float(fair_decimal_odds),
                    book_decimal_odds, pinnacle_close_decimal,
                    edge, kelly_fraction, recommendation, int(recorded_unix),
                ),
            )
            return int(cur.lastrowid or 0)

    def record_bet_builder(
        self,
        *,
        fixture_id: str,
        legs: List[dict],
        fair_probability: float,
        book_decimal_odds: Optional[float],
        edge: Optional[float],
        kelly_fraction: Optional[float],
        recommendation: str,
        recorded_unix: int,
    ) -> int:
        with self._conn() as c:
            cur = c.execute(
                """INSERT INTO bet_builder_quotes(
                       fixture_id, legs_json, fair_probability,
                       book_decimal_odds, edge, kelly_fraction,
                       recommendation, recorded_unix)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    fixture_id, json.dumps(legs), float(fair_probability),
                    book_decimal_odds, edge, kelly_fraction, recommendation,
                    int(recorded_unix),
                ),
            )
            return int(cur.lastrowid or 0)

    # ------------------------------------------------------------------
    # reads
    # ------------------------------------------------------------------
    def fixtures_upcoming(self, since_unix: int, limit: int = 50) -> List[dict]:
        with self._conn() as c:
            rows = c.execute(
                """SELECT id, home_team_id, away_team_id, kickoff_unix,
                          competition, neutral_venue, referee_id, status
                   FROM fixtures
                   WHERE kickoff_unix >= ? AND status != 'CLOSED'
                   ORDER BY kickoff_unix ASC LIMIT ?""",
                (since_unix, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def teams_all(self) -> List[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM teams ORDER BY elo DESC").fetchall()
            return [dict(r) for r in rows]

    def players_for_team(self, team_id: str) -> List[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM players WHERE team_id = ? ORDER BY position, name",
                (team_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def predictions_resolved(
        self, market_type: Optional[str] = None
    ) -> List[dict]:
        sql = "SELECT * FROM predictions WHERE outcome IS NOT NULL"
        args: list = []
        if market_type:
            sql += " AND market_type = ?"
            args.append(market_type)
        sql += " ORDER BY recorded_unix DESC"
        with self._conn() as c:
            return [dict(r) for r in c.execute(sql, args).fetchall()]

    def predictions_for_fixture(self, fixture_id: str) -> List[dict]:
        """All recorded model predictions for a given fixture (resolved
        or not). Used by the resolution loop to grade legs after a
        match ends."""
        with self._conn() as c:
            return [
                dict(r) for r in c.execute(
                    "SELECT * FROM predictions WHERE fixture_id = ? ORDER BY id ASC",
                    (str(fixture_id),),
                ).fetchall()
            ]

    def bet_builder_for_fixture(self, fixture_id: str) -> List[dict]:
        with self._conn() as c:
            return [
                dict(r) for r in c.execute(
                    "SELECT * FROM bet_builder_quotes WHERE fixture_id = ? ORDER BY id ASC",
                    (str(fixture_id),),
                ).fetchall()
            ]

    def set_prediction_outcome(self, prediction_id: int, outcome: Optional[int]) -> None:
        """Mark a single prediction as resolved (1=hit, 0=miss). Pass
        outcome=None to clear (e.g. if we determine the market is void)."""
        with self._conn() as c:
            c.execute(
                "UPDATE predictions SET outcome = ? WHERE id = ?",
                (int(outcome) if outcome is not None else None, int(prediction_id)),
            )

    def set_bet_builder_outcome(self, quote_id: int, outcome: Optional[int]) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE bet_builder_quotes SET outcome = ? WHERE id = ?",
                (int(outcome) if outcome is not None else None, int(quote_id)),
            )

    def set_prediction_closing_line(
        self, prediction_id: int, pinnacle_close_decimal: Optional[float]
    ) -> None:
        """Snapshot Pinnacle's closing decimal odds for a recorded
        prediction so we can compute CLV after settlement."""
        with self._conn() as c:
            c.execute(
                "UPDATE predictions SET pinnacle_close_decimal = ? WHERE id = ?",
                (
                    float(pinnacle_close_decimal) if pinnacle_close_decimal is not None else None,
                    int(prediction_id),
                ),
            )

    def fixture_get(self, fixture_id: str) -> Optional[dict]:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM fixtures WHERE id = ?",
                (str(fixture_id),),
            ).fetchone()
            return dict(row) if row else None

    def fixture_set_status(self, fixture_id: str, status: str) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE fixtures SET status = ? WHERE id = ?",
                (str(status), str(fixture_id)),
            )

    # ------------------------------------------------------------------
    # historical matches (used to refit Dixon-Coles + Elo from real data)
    # ------------------------------------------------------------------
    def upsert_historical_matches(
        self, rows: Iterable[Mapping[str, object]]
    ) -> int:
        out_rows = []
        for r in rows:
            mid = str(r.get("match_id") or
                      f"{r['home_team_id']}-{r['away_team_id']}-{r['kickoff_unix']}")
            out_rows.append((
                mid,
                str(r["home_team_id"]),
                str(r["away_team_id"]),
                int(r["home_goals"]),
                int(r["away_goals"]),
                int(r["kickoff_unix"]),
                1 if bool(r.get("neutral_venue", True)) else 0,
                str(r.get("competition", "")),
            ))
        with self._conn() as c:
            c.executemany(
                """INSERT INTO historical_matches(
                       id, home_team_id, away_team_id,
                       home_goals, away_goals, kickoff_unix,
                       neutral_venue, competition)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     home_team_id=excluded.home_team_id,
                     away_team_id=excluded.away_team_id,
                     home_goals=excluded.home_goals,
                     away_goals=excluded.away_goals,
                     kickoff_unix=excluded.kickoff_unix,
                     neutral_venue=excluded.neutral_venue,
                     competition=excluded.competition""",
                out_rows,
            )
            return len(out_rows)

    def historical_matches(
        self, since_unix: Optional[int] = None
    ) -> List[dict]:
        sql = "SELECT * FROM historical_matches"
        args: list = []
        if since_unix is not None:
            sql += " WHERE kickoff_unix >= ?"
            args.append(int(since_unix))
        sql += " ORDER BY kickoff_unix ASC"
        with self._conn() as c:
            rows = c.execute(sql, args).fetchall()
            return [dict(r) for r in rows]

    def historical_match_count(self) -> int:
        with self._conn() as c:
            row = c.execute("SELECT COUNT(*) AS n FROM historical_matches").fetchone()
            return int(row["n"]) if row else 0

    def delete_synthetic_fixtures(self) -> int:
        """Delete the demo/synthetic fixtures seeded by `seed_demo`.

        Called after a real-data fit so the dashboard is no longer cluttered
        with demo-* placeholder games whose team IDs may not exist in the
        real-data team table. Returns the number of rows deleted.
        """
        with self._conn() as c:
            cur = c.execute("DELETE FROM fixtures WHERE id LIKE 'demo-%'")
            return int(cur.rowcount or 0)

    def upsert_match_xg(
        self, rows: Iterable[Mapping[str, object]]
    ) -> int:
        """Update home_xg / away_xg for existing historical_matches rows.

        Each row must carry `match_id` (or `id`) plus `home_xg`, `away_xg`.
        Rows whose match_id does not exist in the table are silently ignored
        (UPDATE with zero rows affected). Returns the number of UPDATE
        statements issued (not the number of rows actually changed).
        """
        payload = []
        for r in rows:
            mid = str(r.get("match_id") or r.get("id") or "")
            if not mid:
                continue
            try:
                hxg = float(r["home_xg"])
                axg = float(r["away_xg"])
            except (KeyError, TypeError, ValueError):
                continue
            payload.append((hxg, axg, mid))
        if not payload:
            return 0
        with self._conn() as c:
            c.executemany(
                """UPDATE historical_matches
                      SET home_xg = ?, away_xg = ?
                    WHERE id = ?""",
                payload,
            )
        return len(payload)

    def historical_matches_xg_coverage(self) -> dict:
        """Return {total, with_xg, missing_xg} for monitoring."""
        with self._conn() as c:
            row = c.execute(
                """SELECT
                      COUNT(*)                                  AS total,
                      SUM(CASE WHEN home_xg IS NOT NULL
                                AND away_xg IS NOT NULL
                               THEN 1 ELSE 0 END)               AS with_xg
                     FROM historical_matches"""
            ).fetchone()
            total = int(row["total"]) if row else 0
            with_xg = int(row["with_xg"] or 0) if row else 0
        return {"total": total, "with_xg": with_xg, "missing_xg": total - with_xg}
