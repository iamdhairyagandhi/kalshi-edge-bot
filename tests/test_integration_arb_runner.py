"""End-to-end integration test: mock the Kalshi API, run a full scan pass,
verify arb opportunities get caught and (paper) executed."""

import os
import tempfile

import httpx
import pytest

from src.clients.kalshi import KalshiClient
from src.jobs.arb_runner import run_once
from src.paper.executor import PaperExecutor
from src.risk.gates import GateConfig


@pytest.fixture
def mock_kalshi():
    """A mock httpx transport that fakes Kalshi market + orderbook responses."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/trade-api/v2/markets":
            return httpx.Response(200, json={
                "markets": [
                    {"ticker": "KXARB-1", "volume_24h": 5000, "status": "open"},
                    {"ticker": "KXNOARB-1", "volume_24h": 5000, "status": "open"},
                    {"ticker": "KXTHIN-1", "volume_24h": 10, "status": "open"},
                ],
                "cursor": "",
            })
        if path == "/trade-api/v2/markets/KXARB-1/orderbook":
            # Juicy arb: YES bid 40¢ size 30, NO bid 40¢ size 30
            #   -> YES ask = 1-0.40 = 0.60, NO ask = 1-0.40 = 0.60
            #   Wait — that gives sum=1.20, NO arb.
            # We want sum of ASKS < 1. Asks come from opposing bids.
            # If yes_bid=30, no_bid=30 -> yes_ask=70, no_ask=70 (no arb).
            # We need HIGH bids -> LOW asks. yes_bid=60, no_bid=60 ->
            # yes_ask=40, no_ask=40 -> sum=80. 20¢ gross arb. 
            return httpx.Response(200, json={"orderbook": {
                "yes": [[60, 30]],
                "no":  [[60, 30]],
            }})
        if path == "/trade-api/v2/markets/KXNOARB-1/orderbook":
            # YES ask = 0.55, NO ask = 0.55, sum = 1.10 -> no arb
            return httpx.Response(200, json={"orderbook": {
                "yes": [[45, 20]],
                "no":  [[45, 20]],
            }})
        if path == "/trade-api/v2/markets/KXTHIN-1/orderbook":
            # Same juicy arb pricing but low 24h volume -> gates should reject
            return httpx.Response(200, json={"orderbook": {
                "yes": [[60, 30]],
                "no":  [[60, 30]],
            }})
        return httpx.Response(404, json={"error": "not found"})

    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport, base_url="https://api.elections.kalshi.com")


@pytest.mark.asyncio
async def test_full_scan_pass_executes_qualifying_arb(mock_kalshi):
    with tempfile.TemporaryDirectory() as d:
        client = KalshiClient(
            base_url="https://api.elections.kalshi.com",
            http_client=mock_kalshi,
        )
        # Disable the per-request sleep for fast tests
        client.rate_limit_delay = 0.0

        executor = PaperExecutor(os.path.join(d, "test.db"), starting_bankroll=10000.0)

        # Use generous gate config so the KXARB-1 trade passes:
        # cost = (0.45 + 0.50) * 30 = $28.50, well under 2% of $10k = $200
        summary = await run_once(client, executor, max_markets=10)

        assert summary["markets_scanned"] == 3
        assert summary["books_observed"] == 3
        assert summary["opportunities"] >= 1
        # KXARB-1 should be executed; KXTHIN-1 rejected on volume
        assert summary["executed"] == 1
        assert "kalshi:KXARB-1:YES" in executor.portfolio.positions
        assert "kalshi:KXARB-1:NO" in executor.portfolio.positions
        # KXNOARB has no opportunity
        assert "kalshi:KXNOARB-1:YES" not in executor.portfolio.positions

        await client.close()


@pytest.mark.asyncio
async def test_gates_block_arb_on_low_volume(mock_kalshi):
    """Even with juicy arb pricing, low 24h volume should block the trade."""
    with tempfile.TemporaryDirectory() as d:
        client = KalshiClient(
            base_url="https://api.elections.kalshi.com",
            http_client=mock_kalshi,
        )
        client.rate_limit_delay = 0.0
        executor = PaperExecutor(os.path.join(d, "t.db"), starting_bankroll=10000.0)

        # KXTHIN-1 has the same juicy book as KXARB-1 but only $10 24h volume.
        # Our default gate requires $500. So it should NOT be executed.
        summary = await run_once(client, executor, max_markets=10)

        # KXTHIN-1 position should NOT exist
        assert "kalshi:KXTHIN-1:YES" not in executor.portfolio.positions

        await client.close()
