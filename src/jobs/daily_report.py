"""
Daily reconciliation report.

Reads from the paper_trades / paper_positions SQLite db plus optional
calibration db (strategy_state). Generates a human-readable summary.

Use case: read this every morning before deciding to keep the bot live.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterator, List, Optional


@dataclass
class DailyReport:
    as_of: str
    cash: float
    open_positions: int
    open_positions_cost: float
    n_trades_today: int
    fills_by_strategy: Dict[str, dict] = field(default_factory=dict)
    strategy_states: List[dict] = field(default_factory=list)
    invariant_ok: bool = True
    invariant_issues: List[str] = field(default_factory=list)

    def to_text(self) -> str:
        lines = [
            f"=== KALSHI EDGE BOT — DAILY REPORT ===",
            f"As of: {self.as_of}",
            f"",
            f"Cash:           ${self.cash:,.2f}",
            f"Open positions: {self.open_positions} (cost ${self.open_positions_cost:,.2f})",
            f"Fills today:    {self.n_trades_today}",
            f"",
            f"By strategy:",
        ]
        for strat, d in sorted(self.fills_by_strategy.items()):
            lines.append(
                f"  {strat:30s} fills={d.get('fills',0):4d}  "
                f"fees=${d.get('fees',0):,.2f}  cost=${d.get('cost',0):,.2f}"
            )
        lines.append("")
        if self.strategy_states:
            lines.append("Strategy state:")
            for s in self.strategy_states:
                flag = "ENABLED " if s["enabled"] else "DISABLED"
                b = s.get("last_brier")
                n = s.get("last_n_samples")
                brier_str = f"Brier={b:.4f} n={n}" if b is not None else "no eval yet"
                reason = f" — {s['disabled_reason']}" if s.get("disabled_reason") else ""
                lines.append(f"  {flag}  {s['strategy']:30s}  {brier_str}{reason}")
        lines.append("")
        lines.append("Invariants: " + ("OK" if self.invariant_ok else "VIOLATIONS"))
        for v in self.invariant_issues:
            lines.append(f"  ! {v}")
        return "\n".join(lines)


@contextmanager
def _conn(db_path: str) -> Iterator[sqlite3.Connection]:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def generate_report(
    paper_db_path: str,
    cash: float,
    open_positions_cost: float,
    starting_bankroll: float,
    realized_pnl: float = 0.0,
    calibration_db_path: Optional[str] = None,
    as_of: Optional[datetime] = None,
) -> DailyReport:
    """
    Build a DailyReport.

    Caller passes in the live portfolio numbers (we don't have a portfolio
    table — those live in the in-memory PaperPortfolio object).
    """
    if as_of is None:
        as_of = datetime.now(timezone.utc)
    day_start = as_of.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

    fills_by_strategy: Dict[str, dict] = {}
    n_trades_today = 0
    n_open = 0
    with _conn(paper_db_path) as c:
        for row in c.execute(
            "SELECT strategy, COUNT(*) AS fills, "
            "       COALESCE(SUM(fees), 0) AS fees, "
            "       COALESCE(SUM(cost), 0) AS cost "
            "FROM paper_trades WHERE placed_at >= ? "
            "GROUP BY strategy",
            (day_start,),
        ):
            n_trades_today += row["fills"]
            fills_by_strategy[row["strategy"]] = {
                "fills": row["fills"],
                "fees": row["fees"] or 0.0,
                "cost": row["cost"] or 0.0,
            }
        r = c.execute(
            "SELECT COUNT(*) AS n FROM paper_positions WHERE closed_at IS NULL"
        ).fetchone()
        n_open = (r["n"] if r else 0) or 0

    strategy_states: List[dict] = []
    if calibration_db_path and Path(calibration_db_path).exists():
        try:
            with _conn(calibration_db_path) as c:
                for row in c.execute(
                    "SELECT strategy, enabled, last_brier, last_n_samples, "
                    "disabled_reason FROM strategy_state"
                ):
                    strategy_states.append({
                        "strategy": row["strategy"],
                        "enabled": bool(row["enabled"]),
                        "last_brier": row["last_brier"],
                        "last_n_samples": row["last_n_samples"],
                        "disabled_reason": row["disabled_reason"],
                    })
        except sqlite3.OperationalError:
            pass

    from src.risk.invariants import check_portfolio_accounting
    inv = check_portfolio_accounting(
        starting_bankroll=max(starting_bankroll, 1.0),
        cash=cash, open_positions_cost=open_positions_cost,
        realized_pnl=realized_pnl,
    )

    return DailyReport(
        as_of=as_of.isoformat(),
        cash=cash,
        open_positions=n_open,
        open_positions_cost=open_positions_cost,
        n_trades_today=n_trades_today,
        fills_by_strategy=fills_by_strategy,
        strategy_states=strategy_states,
        invariant_ok=inv.passed,
        invariant_issues=inv.violations,
    )
