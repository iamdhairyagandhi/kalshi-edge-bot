"""
Paper-trading executor + portfolio state.

Simulates fills against the orderbook we observed. Conservative
assumptions:
- For maker (resting) orders: assume NOT filled unless price subsequently
  crosses (simulation only — we just log the intent).
- For taker (crossing) orders: assume immediate full fill at the quoted
  ask/bid up to the displayed size. If our intended size > displayed
  size, we shrink to displayed size (no walking the book in v1).
- Fees applied per src.utils.fees.

This is intentionally pessimistic. Real fills are usually worse than
optimistic backtests; we want our paper P&L to be a *lower bound*.
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterator, List, Optional

from src.paper.migrations import run_all as run_migrations
from src.risk.gates import PortfolioState, derive_event_family
from src.strategies.overround_arb import ArbOpportunity
from src.utils.fee_models import FeeModel, KalshiFeeModel, get_fee_model


SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    placed_at TEXT NOT NULL,
    strategy TEXT NOT NULL,
    venue TEXT NOT NULL DEFAULT 'kalshi',
    ticker TEXT NOT NULL,
    side TEXT NOT NULL,            -- 'YES' or 'NO'
    action TEXT NOT NULL,          -- 'buy' or 'sell'
    contracts INTEGER NOT NULL,
    price REAL NOT NULL,
    is_maker INTEGER NOT NULL,
    fees REAL NOT NULL,
    cost REAL NOT NULL,            -- price * contracts (collateral)
    notes TEXT
);

CREATE TABLE IF NOT EXISTS paper_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    venue TEXT NOT NULL DEFAULT 'kalshi',
    ticker TEXT NOT NULL,
    side TEXT NOT NULL,
    contracts INTEGER NOT NULL,
    avg_price REAL NOT NULL,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    realized_pnl REAL DEFAULT 0,
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_paper_positions_open ON paper_positions(closed_at);
CREATE INDEX IF NOT EXISTS idx_paper_trades_venue ON paper_trades(venue);
CREATE INDEX IF NOT EXISTS idx_paper_positions_venue ON paper_positions(venue);
"""


@dataclass
class PaperPortfolio:
    starting_bankroll: float
    cash: float
    positions: Dict[str, "PaperPosition"] = field(default_factory=dict)
    realized_pnl: float = 0.0

    @property
    def bankroll(self) -> float:
        """Cash + open-position collateral. Conservative (ignores MTM gains)."""
        return self.cash + sum(p.cost for p in self.positions.values())

    def to_gate_state(self) -> PortfolioState:
        families: Dict[str, int] = {}
        deployed: Dict[str, float] = {}
        for key, pos in self.positions.items():
            fam = derive_event_family(pos.ticker)
            families[fam] = families.get(fam, 0) + 1
            deployed[key] = pos.cost
        return PortfolioState(
            bankroll=self.bankroll,
            cash=self.cash,
            open_positions=deployed,
            open_positions_by_family=families,
            rolling_30d_peak=max(self.starting_bankroll, self.bankroll),
            current_equity=self.bankroll,
        )


@dataclass
class PaperPosition:
    ticker: str
    side: str            # "YES" or "NO" for binary venues
    contracts: int
    avg_price: float
    opened_at: str
    venue: str = "kalshi"

    @property
    def cost(self) -> float:
        return self.contracts * self.avg_price

    @property
    def key(self) -> str:
        """Stable key uniqueness across venues. Old code that constructed
        `f"{ticker}:{side}"` must migrate; the venue prefix prevents
        accidental cross-venue collisions where ticker strings overlap."""
        return f"{self.venue}:{self.ticker}:{self.side}"


def position_key(venue: str, ticker: str, side: str) -> str:
    """Public helper so callers don't reinvent the format."""
    return f"{venue}:{ticker}:{side}"


class PaperExecutor:
    """
    Records simulated trades to SQLite and updates an in-memory portfolio.

    `fee_models` lets a caller inject venue-specific fee logic. When a leg
    is executed with a venue we have no model for, we fall back to
    `get_fee_model(venue)` and cache it.
    """

    def __init__(
        self,
        db_path: str,
        starting_bankroll: float,
        fee_models: Optional[Dict[str, FeeModel]] = None,
    ):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self.portfolio = PaperPortfolio(starting_bankroll=starting_bankroll, cash=starting_bankroll)
        self._fee_models: Dict[str, FeeModel] = dict(fee_models or {})
        self._fee_models.setdefault("kalshi", KalshiFeeModel())
        with self._conn() as c:
            # Migrations first: existing legacy tables (no venue column) need
            # to be upgraded before the SCHEMA below tries to build indexes
            # that reference the venue column.
            run_migrations(c)
            c.executescript(SCHEMA)

    def _fee_model_for(self, venue: str) -> FeeModel:
        v = venue.lower()
        if v not in self._fee_models:
            self._fee_models[v] = get_fee_model(v)
        return self._fee_models[v]

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def execute_leg(
        self,
        *,
        strategy: str,
        ticker: str,
        side: str,                  # "YES" or "NO"
        action: str,                # "buy" or "sell"
        contracts: int,
        price: float,               # in dollars 0.01-0.99
        is_maker: bool = False,
        notes: Optional[str] = None,
        venue: str = "kalshi",
    ) -> Dict:
        """Simulate a fill for a single leg. Returns dict with trade details."""
        side_u = side.upper()
        action_l = action.lower()
        venue_l = venue.lower()
        cost = contracts * price
        fees = self._fee_model_for(venue_l).fee(contracts=contracts, price=price, is_maker=is_maker)

        if action_l == "buy":
            if cost + fees > self.portfolio.cash:
                raise ValueError(f"Insufficient paper cash: need ${cost + fees:.2f}, have ${self.portfolio.cash:.2f}")
            self.portfolio.cash -= (cost + fees)
            key = position_key(venue_l, ticker, side_u)
            if key in self.portfolio.positions:
                existing = self.portfolio.positions[key]
                total_contracts = existing.contracts + contracts
                blended = ((existing.avg_price * existing.contracts) + (price * contracts)) / total_contracts
                existing.contracts = total_contracts
                existing.avg_price = blended
            else:
                self.portfolio.positions[key] = PaperPosition(
                    ticker=ticker, side=side_u, contracts=contracts,
                    avg_price=price, opened_at=self._now(), venue=venue_l,
                )
        elif action_l == "sell":
            key = position_key(venue_l, ticker, side_u)
            if key not in self.portfolio.positions:
                raise ValueError(f"No position to sell: {key}")
            pos = self.portfolio.positions[key]
            if contracts > pos.contracts:
                raise ValueError(f"Selling more than held: {contracts} > {pos.contracts}")
            proceeds = cost - fees
            self.portfolio.cash += proceeds
            pnl = (price - pos.avg_price) * contracts - fees
            self.portfolio.realized_pnl += pnl
            pos.contracts -= contracts
            if pos.contracts == 0:
                del self.portfolio.positions[key]
        else:
            raise ValueError(f"Unknown action: {action}")

        with self._conn() as c:
            c.execute(
                """INSERT INTO paper_trades
                   (placed_at, strategy, venue, ticker, side, action, contracts, price, is_maker, fees, cost, notes)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (self._now(), strategy, venue_l, ticker, side_u, action_l, contracts, price,
                 1 if is_maker else 0, fees, cost, notes),
            )

        return {
            "venue": venue_l,
            "ticker": ticker, "side": side_u, "action": action_l,
            "contracts": contracts, "price": price, "fees": fees,
            "cash_after": self.portfolio.cash,
        }

    def execute_arb(self, opp: ArbOpportunity, strategy: str = "overround_arb", venue: str = "kalshi") -> List[Dict]:
        """Execute both legs of an arbitrage opportunity (Kalshi-style binary)."""
        notes = f"arb_id={uuid.uuid4().hex[:8]} edge_per={opp.net_edge_per_contract:.4f}"
        if opp.direction == "buy_both":
            yes = self.execute_leg(strategy=strategy, ticker=opp.ticker, side="YES",
                                    action="buy", contracts=opp.contracts,
                                    price=opp.yes_price, is_maker=False, notes=notes, venue=venue)
            no = self.execute_leg(strategy=strategy, ticker=opp.ticker, side="NO",
                                   action="buy", contracts=opp.contracts,
                                   price=opp.no_price, is_maker=False, notes=notes, venue=venue)
            return [yes, no]
        else:
            # "sell_both" arb: model as buying NO at (1 - yes_bid) and buying YES at (1 - no_bid)
            yes_take_price = 1.0 - opp.no_price   # crossing the YES side
            no_take_price = 1.0 - opp.yes_price   # crossing the NO side
            yes = self.execute_leg(strategy=strategy, ticker=opp.ticker, side="YES",
                                    action="buy", contracts=opp.contracts,
                                    price=yes_take_price, is_maker=False, notes=notes, venue=venue)
            no = self.execute_leg(strategy=strategy, ticker=opp.ticker, side="NO",
                                   action="buy", contracts=opp.contracts,
                                   price=no_take_price, is_maker=False, notes=notes, venue=venue)
            return [yes, no]
