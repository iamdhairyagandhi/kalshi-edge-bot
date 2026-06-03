"""Tests for the venue-agnostic fee model layer."""

from __future__ import annotations

import pytest

from src.utils.fee_models import (
    KalshiFeeModel,
    PolymarketFeeModel,
    get_fee_model,
)
from src.utils.fees import taker_fee


class TestKalshiFeeModel:
    def test_taker_matches_legacy(self) -> None:
        m = KalshiFeeModel()
        for n, p in [(1, 0.5), (10, 0.3), (100, 0.05), (50, 0.97)]:
            assert m.fee(contracts=n, price=p, is_maker=False) == taker_fee(n, p)

    def test_maker_is_zero_fee(self) -> None:
        """Executor's historical contract: maker fills are logged-only and
        cost nothing in the simulation."""
        m = KalshiFeeModel()
        assert m.fee(contracts=10, price=0.5, is_maker=True) == 0.0
        assert m.fee(contracts=100, price=0.3, is_maker=True) == 0.0

    def test_zero_or_negative_contracts(self) -> None:
        m = KalshiFeeModel()
        assert m.fee(contracts=0, price=0.5, is_maker=False) == 0.0
        assert m.fee(contracts=-5, price=0.5, is_maker=True) == 0.0

    def test_venue_label(self) -> None:
        assert KalshiFeeModel().venue == "kalshi"


class TestPolymarketFeeModel:
    def test_flat_gas_applied_per_fill(self) -> None:
        m = PolymarketFeeModel(gas_usd=0.10)
        # Size and price don't matter; cost is per-fill gas.
        assert m.fee(contracts=1, price=0.5, is_maker=False) == 0.10
        assert m.fee(contracts=1000, price=0.01, is_maker=True) == 0.10

    def test_zero_contracts_means_no_fee(self) -> None:
        m = PolymarketFeeModel(gas_usd=0.10)
        assert m.fee(contracts=0, price=0.5, is_maker=False) == 0.0

    def test_default_gas_is_nonzero(self) -> None:
        # Sanity: we want to charge *something* by default so paper P&L
        # isn't accidentally optimistic about gas-free fills.
        assert PolymarketFeeModel().fee(contracts=1, price=0.5, is_maker=False) > 0

    def test_zero_gas_override(self) -> None:
        assert PolymarketFeeModel(gas_usd=0.0).fee(contracts=10, price=0.5, is_maker=False) == 0.0

    def test_venue_label(self) -> None:
        assert PolymarketFeeModel().venue == "polymarket"


class TestFactory:
    def test_known_venues(self) -> None:
        assert isinstance(get_fee_model("kalshi"), KalshiFeeModel)
        assert isinstance(get_fee_model("polymarket"), PolymarketFeeModel)

    def test_case_insensitive(self) -> None:
        assert isinstance(get_fee_model("KALSHI"), KalshiFeeModel)
        assert isinstance(get_fee_model("Polymarket"), PolymarketFeeModel)

    def test_unknown_venue_raises(self) -> None:
        with pytest.raises(ValueError):
            get_fee_model("predictit")
