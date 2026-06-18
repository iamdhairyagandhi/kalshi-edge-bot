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
  soccer_bets     : Phase-2 bet journal — what was actually placed and at what
                    price. Distinct from `predictions` (model picks) and
                    `bet_builder_quotes` (priced quotes). Stores stake, source
                    (live/pasted/manual), qualification snapshot, status
                    (open/won/lost/pushed/void/cashed_out), placed/created
                    timestamps, optional closing-line snapshot for CLV, and
                    optional pnl_usd written either by the resolution loop or
                    by manual settlement.

Reads can run concurrently with the writer (SQLite WAL).
"""

from __future__ import annotations

import json
import math
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Mapping, Optional

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

CREATE TABLE IF NOT EXISTS soccer_bets (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    slip_id                  TEXT,
    fixture_id               TEXT NOT NULL,
    match_label              TEXT,
    slip_type                TEXT,
    title                    TEXT,
    legs_json                TEXT NOT NULL,
    model_probability        REAL NOT NULL,
    fair_decimal_odds        REAL NOT NULL,
    placed_decimal_odds      REAL NOT NULL,
    stake_usd                REAL NOT NULL,
    expected_value_usd       REAL,
    edge                     REAL,
    kelly_fraction           REAL,
    qualification_status     TEXT NOT NULL,
    qualification_checks_json TEXT,
    source                   TEXT NOT NULL DEFAULT 'manual',
    bookmaker                TEXT,
    notes                    TEXT,
    status                   TEXT NOT NULL DEFAULT 'open',
    created_unix             INTEGER NOT NULL,
    placed_unix              INTEGER NOT NULL,
    settled_unix             INTEGER,
    pnl_usd                  REAL,
    actual_return_usd        REAL,
    closing_decimal          REAL,
    closing_source           TEXT,
    closing_unix             INTEGER,
    clv_pct                  REAL
);

CREATE INDEX IF NOT EXISTS idx_predictions_fixture ON predictions(fixture_id);
CREATE INDEX IF NOT EXISTS idx_predictions_market  ON predictions(market_type);
CREATE INDEX IF NOT EXISTS idx_fixtures_kickoff    ON fixtures(kickoff_unix);
CREATE INDEX IF NOT EXISTS idx_history_kickoff     ON historical_matches(kickoff_unix);
CREATE INDEX IF NOT EXISTS idx_soccer_bets_fixture ON soccer_bets(fixture_id);
CREATE INDEX IF NOT EXISTS idx_soccer_bets_status  ON soccer_bets(status);
CREATE INDEX IF NOT EXISTS idx_soccer_bets_placed  ON soccer_bets(placed_unix);
"""


# Allowed terminal states for a bet. `open` is the initial state. Settled
# states fix the bet permanently; `void` and `cashed_out` are user-driven
# terminals that don't get auto-graded by the resolution loop.
SOCCER_BET_STATUSES: tuple[str, ...] = (
    "open", "won", "lost", "pushed", "void", "cashed_out",
)
SOCCER_BET_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"won", "lost", "pushed", "void", "cashed_out"}
)
SOCCER_BET_SOURCES: tuple[str, ...] = ("live", "pasted", "manual")


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
                # soccer_bets: cover the case where an older deployment
                # has the table but is missing one of the newer columns.
                # CREATE TABLE IF NOT EXISTS does not patch column lists,
                # so we re-add each column under a try/except.
                "ALTER TABLE soccer_bets ADD COLUMN bookmaker TEXT",
                "ALTER TABLE soccer_bets ADD COLUMN qualification_checks_json TEXT",
                "ALTER TABLE soccer_bets ADD COLUMN actual_return_usd REAL",
                "ALTER TABLE soccer_bets ADD COLUMN closing_decimal REAL",
                "ALTER TABLE soccer_bets ADD COLUMN closing_source TEXT",
                "ALTER TABLE soccer_bets ADD COLUMN closing_unix INTEGER",
                "ALTER TABLE soccer_bets ADD COLUMN clv_pct REAL",
                "ALTER TABLE soccer_bets ADD COLUMN settled_unix INTEGER",
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

    # ------------------------------------------------------------------
    # soccer_bets — Phase 2 bet journal
    # ------------------------------------------------------------------
    def record_soccer_bet(
        self,
        *,
        fixture_id: str,
        legs: List[dict],
        model_probability: float,
        fair_decimal_odds: float,
        placed_decimal_odds: float,
        stake_usd: float,
        qualification_status: str,
        source: str,
        created_unix: int,
        placed_unix: Optional[int] = None,
        slip_id: Optional[str] = None,
        match_label: Optional[str] = None,
        slip_type: Optional[str] = None,
        title: Optional[str] = None,
        expected_value_usd: Optional[float] = None,
        edge: Optional[float] = None,
        kelly_fraction: Optional[float] = None,
        qualification_checks: Optional[List[Mapping[str, object]]] = None,
        bookmaker: Optional[str] = None,
        notes: Optional[str] = None,
        status: str = "open",
    ) -> int:
        """Persist a placed bet row. Returns the new bet id.

        `legs` is the canonical BetLeg list (kind/params/label) — the same
        shape the resolution loop expects so we can auto-grade after the
        fixture closes. `qualification_checks` is the JSON-able snapshot
        of the gate result so we can show "why it qualified" in the
        journal even after the underlying odds change.
        """
        if status not in SOCCER_BET_STATUSES:
            raise ValueError(f"unknown bet status: {status!r}")
        if source not in SOCCER_BET_SOURCES:
            raise ValueError(f"unknown bet source: {source!r}")
        if placed_decimal_odds <= 1.0:
            raise ValueError("placed_decimal_odds must be > 1.0")
        if stake_usd < 0:
            raise ValueError("stake_usd must be non-negative")
        placed_unix = int(placed_unix if placed_unix is not None else created_unix)
        with self._conn() as c:
            cur = c.execute(
                """INSERT INTO soccer_bets(
                       slip_id, fixture_id, match_label, slip_type, title,
                       legs_json, model_probability, fair_decimal_odds,
                       placed_decimal_odds, stake_usd, expected_value_usd,
                       edge, kelly_fraction, qualification_status,
                       qualification_checks_json, source, bookmaker, notes,
                       status, created_unix, placed_unix)
                   VALUES (?,?,?,?,?, ?,?,?,?,?, ?,?,?,?,?, ?,?,?, ?,?,?)""",
                (
                    slip_id, str(fixture_id), match_label, slip_type, title,
                    json.dumps(legs),
                    float(model_probability), float(fair_decimal_odds),
                    float(placed_decimal_odds), float(stake_usd),
                    None if expected_value_usd is None else float(expected_value_usd),
                    None if edge is None else float(edge),
                    None if kelly_fraction is None else float(kelly_fraction),
                    str(qualification_status),
                    json.dumps(list(qualification_checks)) if qualification_checks else None,
                    str(source), bookmaker, notes, str(status),
                    int(created_unix), int(placed_unix),
                ),
            )
            return int(cur.lastrowid or 0)

    def soccer_bet_get(self, bet_id: int) -> Optional[dict]:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM soccer_bets WHERE id = ?", (int(bet_id),),
            ).fetchone()
            return dict(row) if row else None

    def soccer_bets_list(
        self,
        *,
        status: Optional[str] = None,
        fixture_id: Optional[str] = None,
        since_unix: Optional[int] = None,
        limit: int = 200,
    ) -> List[dict]:
        """Return bets ordered most-recent-placed first. `status` accepts
        a single status string or the special value 'all'."""
        sql = "SELECT * FROM soccer_bets WHERE 1=1"
        args: list = []
        if status and status.lower() != "all":
            sql += " AND status = ?"
            args.append(str(status))
        if fixture_id:
            sql += " AND fixture_id = ?"
            args.append(str(fixture_id))
        if since_unix is not None:
            sql += " AND placed_unix >= ?"
            args.append(int(since_unix))
        sql += " ORDER BY placed_unix DESC, id DESC LIMIT ?"
        args.append(max(1, int(limit)))
        with self._conn() as c:
            return [dict(r) for r in c.execute(sql, args).fetchall()]

    def soccer_bets_for_fixture(
        self, fixture_id: str, *, only_open: bool = False
    ) -> List[dict]:
        sql = "SELECT * FROM soccer_bets WHERE fixture_id = ?"
        args: list = [str(fixture_id)]
        if only_open:
            sql += " AND status = 'open'"
        sql += " ORDER BY id ASC"
        with self._conn() as c:
            return [dict(r) for r in c.execute(sql, args).fetchall()]

    def soccer_bet_update(
        self,
        bet_id: int,
        *,
        status: Optional[str] = None,
        placed_decimal_odds: Optional[float] = None,
        notes: Optional[str] = None,
        pnl_usd: Optional[float] = None,
        actual_return_usd: Optional[float] = None,
        settled_unix: Optional[int] = None,
        closing_decimal: Optional[float] = None,
        closing_source: Optional[str] = None,
        closing_unix: Optional[int] = None,
        clv_pct: Optional[float] = None,
    ) -> Optional[dict]:
        """Partial update. Returns the updated row, or None if the id is
        unknown. Validates status transitions and recomputes CLV when both
        a placed and a closing decimal are present."""
        existing = self.soccer_bet_get(bet_id)
        if existing is None:
            return None
        if status is not None:
            if status not in SOCCER_BET_STATUSES:
                raise ValueError(f"unknown bet status: {status!r}")
            current = str(existing.get("status") or "open")
            if current != "open" and status != current and current in SOCCER_BET_TERMINAL_STATUSES:
                raise ValueError(
                    f"cannot transition bet {bet_id} from terminal status "
                    f"{current!r} to {status!r}"
                )
        sets: list[str] = []
        args: list = []
        if status is not None:
            sets.append("status = ?")
            args.append(str(status))
            # auto-stamp settled_unix when transitioning to a terminal
            # state and the caller didn't pass one in.
            if status in SOCCER_BET_TERMINAL_STATUSES and settled_unix is None and existing.get("settled_unix") is None:
                import time as _time
                settled_unix = int(_time.time())
        if placed_decimal_odds is not None:
            if placed_decimal_odds <= 1.0:
                raise ValueError("placed_decimal_odds must be > 1.0")
            sets.append("placed_decimal_odds = ?")
            args.append(float(placed_decimal_odds))
        if notes is not None:
            sets.append("notes = ?")
            args.append(str(notes))
        if pnl_usd is not None:
            sets.append("pnl_usd = ?")
            args.append(float(pnl_usd))
        if actual_return_usd is not None:
            sets.append("actual_return_usd = ?")
            args.append(float(actual_return_usd))
        if settled_unix is not None:
            sets.append("settled_unix = ?")
            args.append(int(settled_unix))
        if closing_decimal is not None:
            sets.append("closing_decimal = ?")
            args.append(float(closing_decimal))
        if closing_source is not None:
            sets.append("closing_source = ?")
            args.append(str(closing_source))
        if closing_unix is not None:
            sets.append("closing_unix = ?")
            args.append(int(closing_unix))
        if clv_pct is not None:
            sets.append("clv_pct = ?")
            args.append(float(clv_pct))
        if not sets:
            return existing
        sql = f"UPDATE soccer_bets SET {', '.join(sets)} WHERE id = ?"
        args.append(int(bet_id))
        with self._conn() as c:
            c.execute(sql, args)
        # Auto-fill CLV if we now have both prices and the caller didn't
        # set it explicitly.
        updated = self.soccer_bet_get(bet_id)
        if (
            updated is not None
            and clv_pct is None
            and updated.get("closing_decimal") is not None
            and updated.get("placed_decimal_odds") is not None
        ):
            try:
                placed_d = float(updated["placed_decimal_odds"])
                close_d = float(updated["closing_decimal"])
                if close_d > 1.0 and placed_d > 1.0:
                    placed_imp = 1.0 / placed_d
                    close_imp = 1.0 / close_d
                    new_clv = (close_imp - placed_imp) / max(placed_imp, 1e-9)
                    with self._conn() as c:
                        c.execute(
                            "UPDATE soccer_bets SET clv_pct = ? WHERE id = ?",
                            (float(new_clv), int(bet_id)),
                        )
                    updated["clv_pct"] = new_clv
            except (TypeError, ValueError):
                pass
        return updated

    def soccer_bets_open_stake_by_fixture(self) -> Dict[str, float]:
        """Open-stake aggregate keyed by fixture_id for Phase 10 exposure
        cap checks. Distinct from :meth:`soccer_bets_open_exposure` which
        groups by match_label for the journal header."""
        with self._conn() as c:
            rows = c.execute(
                """SELECT fixture_id, COALESCE(SUM(stake_usd), 0) AS stake
                   FROM soccer_bets
                   WHERE status = 'open'
                   GROUP BY fixture_id"""
            ).fetchall()
        return {str(r["fixture_id"]): float(r["stake"] or 0.0) for r in rows}

    def soccer_bets_open_exposure(self) -> dict:
        """Aggregate stake / max-return on currently-open bets, grouped by
        match_label so the journal can show concentration risk at a glance."""
        with self._conn() as c:
            total_row = c.execute(
                """SELECT
                       COUNT(*)                                              AS n,
                       COALESCE(SUM(stake_usd), 0)                            AS stake,
                       COALESCE(SUM(stake_usd * placed_decimal_odds), 0)      AS max_return
                   FROM soccer_bets
                   WHERE status = 'open'"""
            ).fetchone()
            by_match_rows = c.execute(
                """SELECT
                       COALESCE(match_label, fixture_id)                      AS match_label,
                       COUNT(*)                                               AS n,
                       COALESCE(SUM(stake_usd), 0)                            AS stake,
                       COALESCE(SUM(stake_usd * placed_decimal_odds), 0)      AS max_return
                   FROM soccer_bets
                   WHERE status = 'open'
                   GROUP BY COALESCE(match_label, fixture_id)
                   ORDER BY stake DESC"""
            ).fetchall()
        return {
            "open_count": int(total_row["n"]) if total_row else 0,
            "open_stake_usd": float(total_row["stake"]) if total_row else 0.0,
            "open_max_return_usd": float(total_row["max_return"]) if total_row else 0.0,
            "by_match": [
                {
                    "match_label": str(r["match_label"]),
                    "n": int(r["n"]),
                    "stake_usd": float(r["stake"]),
                    "max_return_usd": float(r["max_return"]),
                }
                for r in by_match_rows
            ],
        }

    def soccer_bets_settled_summary(self) -> dict:
        """Aggregate settled / cashed-out PnL for the journal header."""
        with self._conn() as c:
            row = c.execute(
                """SELECT
                       SUM(CASE WHEN status = 'won'    THEN 1 ELSE 0 END) AS won,
                       SUM(CASE WHEN status = 'lost'   THEN 1 ELSE 0 END) AS lost,
                       SUM(CASE WHEN status = 'pushed' THEN 1 ELSE 0 END) AS pushed,
                       SUM(CASE WHEN status = 'void'   THEN 1 ELSE 0 END) AS void,
                       SUM(CASE WHEN status = 'cashed_out' THEN 1 ELSE 0 END) AS cashed_out,
                       COALESCE(SUM(pnl_usd), 0)                          AS net_pnl
                   FROM soccer_bets
                   WHERE status != 'open'"""
            ).fetchone()
        return {
            "won": int(row["won"] or 0) if row else 0,
            "lost": int(row["lost"] or 0) if row else 0,
            "pushed": int(row["pushed"] or 0) if row else 0,
            "void": int(row["void"] or 0) if row else 0,
            "cashed_out": int(row["cashed_out"] or 0) if row else 0,
            "net_pnl_usd": float(row["net_pnl"] or 0.0) if row else 0.0,
        }

    def soccer_bets_clv_summary(self) -> dict:
        """Aggregate CLV (closing-line value) statistics for the journal.

        Returns an overall block plus four bucket breakdowns: by market
        (first-leg kind, or 'parlay' for multi-leg slips), by slip type,
        by source, and by model-rating bucket. Each bucket reports count,
        count_with_clv, avg_clv_pct and positive_clv_share so the journal
        can flag negative-CLV markets at a glance.

        CLV is the per-bet field already stored on soccer_bets.clv_pct
        (auto-filled when both placed and closing decimal odds exist).
        """
        rows = self._all_bets_for_clv()
        return _build_clv_summary(rows)

    def soccer_bets_calibration_summary(self) -> dict:
        """Phase 4: model calibration aggregate for the journal dashboard.

        Filters to settled won/lost bets (binary outcomes) and reports
        Brier score, log-loss, hit rate, average predicted probability,
        model EV per $1, realized PnL and ROI — overall plus bucket
        breakdowns by market/rating/odds/slip_type/qualification_status,
        plus a 10-bucket reliability curve (predicted prob deciles vs
        observed hit rate).

        Pushed/void/cashed_out and open bets are excluded — they don't
        produce a clean 0/1 outcome the model can be scored against.
        """
        rows = self._all_bets_for_calibration()
        return _build_calibration_summary(rows)

    def _all_bets_for_calibration(self) -> List[dict]:
        with self._conn() as c:
            return [
                dict(r)
                for r in c.execute(
                    """SELECT id, slip_type, source, legs_json,
                              model_probability, fair_decimal_odds,
                              placed_decimal_odds, stake_usd,
                              qualification_status,
                              status, pnl_usd, placed_unix, settled_unix
                       FROM soccer_bets
                       WHERE status IN ('won', 'lost')"""
                ).fetchall()
            ]

    def _all_bets_for_clv(self) -> List[dict]:
        with self._conn() as c:
            return [
                dict(r)
                for r in c.execute(
                    """SELECT id, slip_type, source, legs_json,
                              model_probability, placed_decimal_odds,
                              closing_decimal, clv_pct, pnl_usd, status,
                              placed_unix
                       FROM soccer_bets"""
                ).fetchall()
            ]


def _market_kind_for_bet(row: Mapping[str, object]) -> str:
    """Derive a coarse market label for CLV bucketing.

    Single-leg bets are labelled by their leg `kind`; multi-leg bets are
    'parlay'. Unparseable rows fall back to 'unknown'.
    """
    try:
        legs = json.loads(str(row.get("legs_json") or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return "unknown"
    if not isinstance(legs, list) or not legs:
        return "unknown"
    if len(legs) > 1:
        return "parlay"
    first = legs[0]
    if isinstance(first, Mapping):
        kind = first.get("kind")
        if isinstance(kind, str) and kind.strip():
            return kind.strip()
    return "unknown"


_RATING_BUCKETS = (
    ("longshot",  0.00, 0.20),
    ("underdog",  0.20, 0.40),
    ("coinflip",  0.40, 0.60),
    ("favorite",  0.60, 0.80),
    ("heavy_fav", 0.80, 1.01),
)


def _rating_bucket_for(model_probability: object) -> str:
    try:
        p = float(model_probability)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "unknown"
    for name, lo, hi in _RATING_BUCKETS:
        if lo <= p < hi:
            return name
    return "unknown"


def _bucket_stats(rows: List[dict]) -> dict:
    """Common per-bucket aggregate: counts + avg CLV + positive share."""
    n = len(rows)
    clvs = [
        float(r["clv_pct"])
        for r in rows
        if r.get("clv_pct") is not None
    ]
    n_clv = len(clvs)
    avg = sum(clvs) / n_clv if n_clv else None
    positive = sum(1 for v in clvs if v > 0)
    positive_share = (positive / n_clv) if n_clv else None
    stakes_with_clv = [
        float(r.get("placed_decimal_odds") or 0.0)
        for r in rows
        if r.get("clv_pct") is not None
    ]
    # We use stake_usd elsewhere; here we just report counts so the
    # frontend can compute weighted averages later if it needs to.
    pnl_rows = [
        float(r["pnl_usd"]) for r in rows if r.get("pnl_usd") is not None
    ]
    net_pnl = sum(pnl_rows) if pnl_rows else 0.0
    return {
        "count": n,
        "count_with_clv": n_clv,
        "avg_clv_pct": avg,
        "positive_clv_share": positive_share,
        "net_pnl_usd": net_pnl,
        # placeholder for future stake-weighting; keeps the shape stable.
        "_stake_proxy": float(sum(stakes_with_clv)),
    }


def _build_clv_summary(rows: List[dict]) -> dict:
    """Group bet rows into the CLV summary structure consumed by the API."""
    overall = _bucket_stats(rows)
    overall.pop("_stake_proxy", None)

    by_market: Dict[str, List[dict]] = {}
    by_slip_type: Dict[str, List[dict]] = {}
    by_source: Dict[str, List[dict]] = {}
    by_rating: Dict[str, List[dict]] = {}
    for r in rows:
        by_market.setdefault(_market_kind_for_bet(r), []).append(r)
        slip_type = str(r.get("slip_type") or "untyped")
        by_slip_type.setdefault(slip_type, []).append(r)
        source = str(r.get("source") or "manual")
        by_source.setdefault(source, []).append(r)
        by_rating.setdefault(_rating_bucket_for(r.get("model_probability")), []).append(r)

    def _to_list(d: Dict[str, List[dict]]) -> List[dict]:
        out: List[dict] = []
        for key, bucket_rows in d.items():
            stats = _bucket_stats(bucket_rows)
            stats.pop("_stake_proxy", None)
            stats["bucket"] = key
            out.append(stats)
        out.sort(
            key=lambda b: (-(b["count_with_clv"] or 0), -(b["count"] or 0), b["bucket"])
        )
        return out

    return {
        "overall": overall,
        "by_market": _to_list(by_market),
        "by_slip_type": _to_list(by_slip_type),
        "by_source": _to_list(by_source),
        "by_rating_bucket": _to_list(by_rating),
    }


_ODDS_BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("le_1_50",  1.00,  1.50),
    ("1_50_2",   1.50,  2.00),
    ("2_3",      2.00,  3.00),
    ("3_5",      3.00,  5.00),
    ("5_10",     5.00, 10.00),
    ("ge_10",   10.00, math.inf),
)


def _odds_bucket_for(decimal_odds: object) -> str:
    """Bucket placed decimal odds into a coarse price band for calibration."""
    try:
        v = float(decimal_odds)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "unknown"
    if not math.isfinite(v) or v <= 1.0:
        return "unknown"
    for name, lo, hi in _ODDS_BUCKETS:
        if lo <= v < hi:
            return name
    return "unknown"


def _calibration_bucket_stats(rows: List[dict]) -> dict:
    """Compute calibration metrics for a list of settled won/lost bets.

    Inputs: rows with status in ('won','lost'), model_probability,
    placed_decimal_odds, stake_usd, pnl_usd. Output is a flat dict so it
    can be wrapped into bucket lists or used as the overall stats block.

    Metrics:
      - count, won, hit_rate (won / count)
      - avg_predicted (mean model_probability)
      - brier (mean (y - p)^2)  -- lower is better, perfect = 0
      - log_loss (mean -[y*ln(p) + (1-y)*ln(1-p)])  -- lower is better
      - ev_per_dollar (mean p*(decimal-1) - (1-p))  -- model-implied EV
      - total_stake_usd, net_pnl_usd, roi (net / stake when stake > 0)
    """
    n = len(rows)
    if n == 0:
        return {
            "count": 0, "won": 0, "hit_rate": None,
            "avg_predicted": None, "brier": None, "log_loss": None,
            "ev_per_dollar": None,
            "total_stake_usd": 0.0, "net_pnl_usd": 0.0, "roi": None,
        }
    eps = 1e-9
    won = 0
    sum_p = 0.0
    sum_brier = 0.0
    sum_logloss = 0.0
    sum_ev = 0.0
    n_ev = 0
    total_stake = 0.0
    net_pnl = 0.0
    for r in rows:
        try:
            p = float(r.get("model_probability"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            p = float("nan")
        if not math.isfinite(p):
            continue
        p_clamped = min(max(p, eps), 1.0 - eps)
        y = 1 if str(r.get("status")) == "won" else 0
        won += y
        sum_p += p
        sum_brier += (y - p) ** 2
        sum_logloss += -(y * math.log(p_clamped) + (1 - y) * math.log(1 - p_clamped))
        try:
            decimal = float(r.get("placed_decimal_odds"))  # type: ignore[arg-type]
            if math.isfinite(decimal) and decimal > 1.0:
                sum_ev += p * (decimal - 1.0) - (1.0 - p)
                n_ev += 1
        except (TypeError, ValueError):
            pass
        try:
            stake = float(r.get("stake_usd") or 0.0)
            total_stake += max(stake, 0.0)
        except (TypeError, ValueError):
            pass
        try:
            pnl = float(r.get("pnl_usd") or 0.0)
            net_pnl += pnl
        except (TypeError, ValueError):
            pass
    return {
        "count": n,
        "won": won,
        "hit_rate": (won / n) if n else None,
        "avg_predicted": (sum_p / n) if n else None,
        "brier": (sum_brier / n) if n else None,
        "log_loss": (sum_logloss / n) if n else None,
        "ev_per_dollar": (sum_ev / n_ev) if n_ev else None,
        "total_stake_usd": total_stake,
        "net_pnl_usd": net_pnl,
        "roi": (net_pnl / total_stake) if total_stake > 0 else None,
    }


_RELIABILITY_BUCKETS: tuple[tuple[str, float, float], ...] = tuple(
    (f"p_{int(i*10):02d}_{int((i+1)*10):02d}", i / 10.0, (i + 1) / 10.0)
    for i in range(10)
)


def _reliability_curve(rows: List[dict]) -> List[dict]:
    """Group settled bets into 10 predicted-probability deciles.

    Each point reports the bucket label, predicted-probability range,
    bet count, mean predicted, observed hit rate, and the gap (observed -
    predicted). Empty buckets are still emitted so the dashboard renders
    a stable shape; their stats are None.
    """
    buckets: Dict[str, List[dict]] = {name: [] for name, _, _ in _RELIABILITY_BUCKETS}
    for r in rows:
        try:
            p = float(r.get("model_probability"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if not math.isfinite(p):
            continue
        for name, lo, hi in _RELIABILITY_BUCKETS:
            upper_inclusive = (hi >= 1.0)
            if lo <= p < hi or (upper_inclusive and p == hi):
                buckets[name].append(r)
                break
    out: List[dict] = []
    for name, lo, hi in _RELIABILITY_BUCKETS:
        bucket_rows = buckets[name]
        n = len(bucket_rows)
        if n == 0:
            out.append({
                "bucket": name, "p_lo": lo, "p_hi": hi,
                "count": 0, "avg_predicted": None,
                "observed_hit_rate": None, "gap": None,
            })
            continue
        s = _calibration_bucket_stats(bucket_rows)
        avg_p = s["avg_predicted"]
        obs = s["hit_rate"]
        gap = (obs - avg_p) if (avg_p is not None and obs is not None) else None
        out.append({
            "bucket": name, "p_lo": lo, "p_hi": hi,
            "count": n,
            "avg_predicted": avg_p,
            "observed_hit_rate": obs,
            "gap": gap,
        })
    return out


def _build_calibration_summary(rows: List[dict]) -> dict:
    """Group settled bet rows into the calibration summary structure."""
    overall = _calibration_bucket_stats(rows)

    by_market: Dict[str, List[dict]] = {}
    by_rating: Dict[str, List[dict]] = {}
    by_odds: Dict[str, List[dict]] = {}
    by_slip_type: Dict[str, List[dict]] = {}
    by_qual: Dict[str, List[dict]] = {}
    for r in rows:
        by_market.setdefault(_market_kind_for_bet(r), []).append(r)
        by_rating.setdefault(_rating_bucket_for(r.get("model_probability")), []).append(r)
        by_odds.setdefault(_odds_bucket_for(r.get("placed_decimal_odds")), []).append(r)
        by_slip_type.setdefault(str(r.get("slip_type") or "untyped"), []).append(r)
        by_qual.setdefault(str(r.get("qualification_status") or "unknown"), []).append(r)

    def _to_list(d: Dict[str, List[dict]]) -> List[dict]:
        out: List[dict] = []
        for key, bucket_rows in d.items():
            stats = _calibration_bucket_stats(bucket_rows)
            stats["bucket"] = key
            out.append(stats)
        out.sort(key=lambda b: (-(b["count"] or 0), b["bucket"]))
        return out

    return {
        "overall": overall,
        "by_market": _to_list(by_market),
        "by_rating_bucket": _to_list(by_rating),
        "by_odds_bucket": _to_list(by_odds),
        "by_slip_type": _to_list(by_slip_type),
        "by_qualification_status": _to_list(by_qual),
        "reliability_curve": _reliability_curve(rows),
    }


