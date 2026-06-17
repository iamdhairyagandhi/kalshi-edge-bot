import os
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

import src.jobs.weather_runner as weather_runner
from src.clients.nws import HourlyForecast
from src.clients.polymarket import PolymarketMarket, PolymarketOutcome
from src.clients.weather_ai import WeatherAIAssessment
from src.paper.executor import PaperExecutor
from src.strategies.weather_edge import WeatherContract, WeatherEstimate


NOW = datetime(2026, 6, 9, 12, tzinfo=timezone.utc)


class FakeKalshi:
    pass


class FakePoly:
    def get_orderbook(self, token_id):
        assert token_id == "YES_T"
        return {"bids": [{"price": "0.45", "size": "100"}], "asks": [{"price": "0.50", "size": "100"}]}


class FakeNWS:
    def hourly_forecast(self, lat, lon):
        return [
            HourlyForecast(
                start_time=NOW + timedelta(hours=i),
                end_time=NOW + timedelta(hours=i + 1),
                temperature_f=90,
                short_forecast="Sunny",
            )
            for i in range(12)
        ]


class FakeOpenMeteo:
    def forecast_set(self, lat, lon):
        class ForecastSet:
            def by_source(self):
                return {
                    "open_meteo_best": [
                        HourlyForecast(
                            start_time=NOW + timedelta(hours=i),
                            end_time=NOW + timedelta(hours=i + 1),
                            temperature_f=89,
                            short_forecast="model",
                        )
                        for i in range(12)
                    ],
                    "noaa_gfs_hrrr": [
                        HourlyForecast(
                            start_time=NOW + timedelta(hours=i),
                            end_time=NOW + timedelta(hours=i + 1),
                            temperature_f=91,
                            short_forecast="model",
                        )
                        for i in range(12)
                    ],
                }

        return ForecastSet()


class FakeAI:
    def assess(self, **kwargs):
        return WeatherAIAssessment(
            probability_adjustment=0.02,
            uncertainty_multiplier=0.9,
            confidence=0.9,
            rationale="stable sunny forecast",
        )


@pytest.mark.asyncio
async def test_weather_runner_persists_ai_estimates(monkeypatch):
    async def fake_kalshi_markets(client, max_markets):
        return []

    def fake_poly_markets(client, max_markets):
        return [
            PolymarketMarket(
                condition_id="0xWX",
                question_id=None,
                slug="wx",
                question="Will Chicago high temperature be above 80 degrees today?",
                closed=False,
                accepting_orders=True,
                outcomes=[
                    PolymarketOutcome(index=0, label="Yes", token_id="YES_T"),
                    PolymarketOutcome(index=1, label="No", token_id="NO_T"),
                ],
            )
        ]

    monkeypatch.setattr(weather_runner, "fetch_weather_kalshi_markets", fake_kalshi_markets)
    monkeypatch.setattr(weather_runner, "fetch_polymarket_markets", fake_poly_markets)

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        db = os.path.join(d, "weather.db")
        summary, estimates = await weather_runner.run_once(
            kalshi=FakeKalshi(),
            polymarket=FakePoly(),
            nws=FakeNWS(),
            open_meteo=FakeOpenMeteo(),
            ai_client=FakeAI(),
            db_path=db,
            max_kalshi_markets=0,
            max_polymarket_markets=1,
            min_edge=0.05,
            safety_buffer=0.03,
            min_confidence=0.65,
        )

        assert summary.parsed_contracts == 1
        assert summary.ai_used == 1
        assert len(estimates) == 1
        assert estimates[0].recommendation == "buy_yes"

        conn = sqlite3.connect(db)
        try:
            row = conn.execute(
                "SELECT venue, market_id, recommendation, ai_used, notes FROM weather_specialist_estimates"
            ).fetchone()
        finally:
            conn.close()
        assert row[0] == "polymarket"
        assert row[1] == "0xWX"
        assert row[2] == "buy_yes"
        assert row[3] == 1
        assert "AI adj=+0.020" in row[4]
        assert "open_meteo_best=89.0" in row[4]
        assert "noaa_gfs_hrrr=91.0" in row[4]


def test_weather_candidates_execute_as_paper_kalshi_trades():
    contract = WeatherContract(
        venue="kalshi",
        market_id="KXHIGHNY-TEST",
        title="Will the high temp in NYC be >82?",
        kind="temp_high",
        city="new york",
        lat=40.7128,
        lon=-74.006,
        timezone_name="America/New_York",
        threshold=82,
        comparator="above",
    )
    estimate = WeatherEstimate(
        contract=contract,
        forecast_value=90,
        sigma=3,
        p_yes=0.90,
        market_yes_ask=0.40,
        market_yes_bid=0.36,
        edge_to_buy_yes=0.47,
        edge_to_buy_no=-0.57,
        recommendation="buy_yes",
        confidence=0.90,
        notes="test",
    )
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        db = os.path.join(d, "weather.db")
        executor = PaperExecutor(db, starting_bankroll=1000.0)
        executed, rejected = weather_runner._execute_weather_candidates(
            executor=executor,
            estimates=[estimate],
            notional_per_trade_usd=5.0,
            run_id="testrun",
        )
        assert executed == 1
        assert rejected == 0

        executed_again, rejected_again = weather_runner._execute_weather_candidates(
            executor=executor,
            estimates=[estimate],
            notional_per_trade_usd=5.0,
            run_id="testrun2",
        )
        assert executed_again == 0
        assert rejected_again == 1

        conn = sqlite3.connect(db)
        try:
            row = conn.execute(
                "SELECT strategy, ticker, side, action, contracts, price FROM paper_trades"
            ).fetchone()
        finally:
            conn.close()
        assert row[0] == "weather_specialist"
        assert row[1] == "KXHIGHNY-TEST"
        assert row[2] == "YES"
        assert row[3] == "buy"
        assert row[4] == 12
        assert row[5] == 0.40
