"""Deterministic replay backtester."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterator, List, Optional

from src.paper.executor import PaperExecutor
from src.risk.gates import GateConfig, derive_event_family, evaluate_gates
from src.strategies.overround_arb import Orderbook, scan_orderbooks


@dataclass
class ReplaySnapshot:
    timestamp: float
    books: List[Orderbook]
    markets: Dict[str, dict] = field(default_factory=dict)


@dataclass
class ReplayResult:
    snapshots_processed: int
    opportunities_seen: int
    trades_executed: int
    final_cash: float
    final_realized_pnl: float
    final_positions: int


def replay(
    snapshots: Iterator[ReplaySnapshot],
    executor: PaperExecutor,
    gate_config: Optional[GateConfig] = None,
) -> ReplayResult:
    if gate_config is None:
        gate_config = GateConfig()

    seen = 0
    opps = 0
    executed = 0

    for snap in snapshots:
        seen += 1
        opportunities = scan_orderbooks(snap.books)
        opps += len(opportunities)

        for opp in opportunities:
            market = snap.markets.get(opp.ticker, {})
            volume = float(market.get("volume_24h") or market.get("volume") or 0.0)
            cost = (opp.yes_price + opp.no_price) * opp.contracts
            family = derive_event_family(opp.ticker)
            kelly_proxy = opp.net_edge_per_contract / max(opp.yes_price + opp.no_price, 0.01)

            gate_state = executor.portfolio.to_gate_state()
            decision = evaluate_gates(
                portfolio=gate_state,
                ticker=opp.ticker,
                event_family=family,
                proposed_cost=cost,
                market_volume_24h=volume,
                kelly_fraction_value=kelly_proxy,
                config=gate_config,
            )
            if not decision.passed:
                continue
            try:
                executor.execute_arb(opp)
                executed += 1
            except Exception:
                pass

    return ReplayResult(
        snapshots_processed=seen,
        opportunities_seen=opps,
        trades_executed=executed,
        final_cash=executor.portfolio.cash,
        final_realized_pnl=getattr(executor.portfolio, "realized_pnl", 0.0),
        final_positions=len(executor.portfolio.positions),
    )


def load_jsonl_snapshots(path: Path) -> Iterator[ReplaySnapshot]:
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            books = [Orderbook(**b) for b in d.get("books", [])]
            yield ReplaySnapshot(
                timestamp=float(d["timestamp"]),
                books=books,
                markets=d.get("markets", {}),
            )
