"""
Venue-agnostic fee models.

The original Kalshi fee math lives in `src.utils.fees` (unchanged for
back-compat). This module wraps it behind a `FeeModel` protocol so the
paper executor can charge fees per venue without hard-coding Kalshi.

A `FeeModel` answers a single question:
    "Given a fill's venue/action/contracts/price, what fee in USD?"

For Polymarket we model fee = flat gas cost per fill (CLOB taker fee is
currently 0 on Polymarket; gas is the real cost). The default is a
configurable per-fill USD amount; override per environment.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from src.utils.fees import maker_fee as kalshi_maker_fee  # noqa: F401  (re-exported intentionally for callers)
from src.utils.fees import taker_fee as kalshi_taker_fee


class FeeModel(Protocol):
    """Per-venue fee calculator. All fees in USD."""

    venue: str

    def fee(self, *, contracts: float, price: float, is_maker: bool) -> float:
        ...


@dataclass(frozen=True)
class KalshiFeeModel:
    """Wraps the existing Kalshi fee math.

    Note: the paper executor historically treated maker fills as zero-fee
    because maker resting orders are logged-only and assumed not to be
    real fills. We preserve that exactly: `is_maker=True` returns 0.

    Callers who want the real Kalshi maker fee should call
    `src.utils.fees.maker_fee` directly.
    """

    venue: str = "kalshi"

    def fee(self, *, contracts: float, price: float, is_maker: bool) -> float:
        n = int(contracts)
        if n <= 0 or is_maker:
            return 0.0
        return kalshi_taker_fee(n, price)


@dataclass(frozen=True)
class PolymarketFeeModel:
    """
    Polymarket CLOB currently charges 0 taker/maker fees on filled orders.
    The real cost is Polygon gas per transaction. We model gas as a flat
    USD amount per fill (tunable). Set `gas_usd=0` to disable.

    Note: when live execution lands, this should be replaced/augmented with
    a model that estimates gas from `eth_gasPrice` * MATIC/USD.
    """

    gas_usd: float = 0.05
    venue: str = "polymarket"

    def fee(self, *, contracts: float, price: float, is_maker: bool) -> float:
        if contracts <= 0:
            return 0.0
        return max(0.0, float(self.gas_usd))


def get_fee_model(venue: str) -> FeeModel:
    """Factory used by the paper executor when no explicit model is injected."""
    v = venue.lower()
    if v == "kalshi":
        return KalshiFeeModel()
    if v == "polymarket":
        return PolymarketFeeModel()
    raise ValueError(f"Unknown venue: {venue!r}")
