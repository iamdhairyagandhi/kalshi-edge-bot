"""
Pre-trade risk gates.

Every contemplated trade must pass ALL gates before an order is sent.
Inspired by the Octagon CLI's 5-gate model; tuned for our risk appetite.

Gates are intentionally simple and deterministic — no ML, no thresholds
that change based on "feel". Each gate has a single tunable parameter
documented inline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class PortfolioState:
    """Snapshot of portfolio for gate evaluation."""
    bankroll: float                                # cash + open position value
    cash: float
    open_positions: Dict[str, float]               # ticker -> capital deployed
    open_positions_by_family: Dict[str, int]       # event_family -> count
    rolling_30d_peak: float
    current_equity: float


@dataclass
class GateConfig:
    max_position_pct: float = 0.02        # 2% of bankroll per position
    max_total_deployed_pct: float = 0.25  # 25% of bankroll deployed total
    max_per_family: int = 5               # max open positions per event family
    drawdown_kill_pct: float = 0.10       # halt at 10% drawdown from rolling 30d peak
    min_market_volume_24h: float = 500.0  # require at least $500 24h volume
    min_kelly_fraction: float = 0.005     # don't bother if Kelly < 0.5%


@dataclass
class GateResult:
    passed: bool
    failures: List[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.passed


def evaluate_gates(
    *,
    portfolio: PortfolioState,
    ticker: str,
    event_family: str,
    proposed_cost: float,
    market_volume_24h: float,
    kelly_fraction_value: float,
    config: Optional[GateConfig] = None,
) -> GateResult:
    """
    Run all gates. Returns GateResult.passed = True only if ALL pass.
    """
    cfg = config or GateConfig()
    failures: List[str] = []

    # Gate 1: Drawdown kill switch
    if portfolio.rolling_30d_peak > 0:
        drawdown = 1.0 - (portfolio.current_equity / portfolio.rolling_30d_peak)
        if drawdown >= cfg.drawdown_kill_pct:
            failures.append(
                f"drawdown {drawdown:.1%} >= kill threshold {cfg.drawdown_kill_pct:.1%}"
            )

    # Gate 2: Per-position cap
    pos_pct = proposed_cost / portfolio.bankroll if portfolio.bankroll > 0 else 1.0
    if pos_pct > cfg.max_position_pct:
        failures.append(
            f"position size {pos_pct:.2%} > cap {cfg.max_position_pct:.2%}"
        )

    # Gate 3: Total deployment cap
    deployed_now = sum(portfolio.open_positions.values())
    new_deployed_pct = (deployed_now + proposed_cost) / portfolio.bankroll \
        if portfolio.bankroll > 0 else 1.0
    if new_deployed_pct > cfg.max_total_deployed_pct:
        failures.append(
            f"total deployment {new_deployed_pct:.2%} > cap {cfg.max_total_deployed_pct:.2%}"
        )

    # Gate 4: Correlation (event-family concentration)
    family_count = portfolio.open_positions_by_family.get(event_family, 0)
    if family_count >= cfg.max_per_family:
        failures.append(
            f"event family '{event_family}' already has {family_count} positions "
            f"(cap {cfg.max_per_family})"
        )

    # Gate 5: Liquidity
    if market_volume_24h < cfg.min_market_volume_24h:
        failures.append(
            f"24h volume ${market_volume_24h:.0f} < min ${cfg.min_market_volume_24h:.0f}"
        )

    # Gate 6: Cash availability
    if proposed_cost > portfolio.cash:
        failures.append(f"insufficient cash: need ${proposed_cost:.2f}, have ${portfolio.cash:.2f}")

    # Gate 7: Meaningful Kelly
    if kelly_fraction_value < cfg.min_kelly_fraction:
        failures.append(
            f"kelly fraction {kelly_fraction_value:.4f} below min {cfg.min_kelly_fraction:.4f}"
        )

    return GateResult(passed=(len(failures) == 0), failures=failures)


def derive_event_family(ticker: str) -> str:
    """
    Group correlated markets. Kalshi tickers look like KXBTC-26APR-B95000.
    The family is the series prefix before the date (e.g. KXBTC). Markets
    in the same family are highly correlated (different strikes / dates on
    the same underlying) and should be capped together.
    """
    parts = ticker.upper().split("-")
    return parts[0] if parts else ticker.upper()
