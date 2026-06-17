import httpx

from src.clients.open_meteo import OpenMeteoClient


def test_open_meteo_forecast_set_parses_best_and_noaa_models():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "hourly": {
                    "time": ["2026-06-09T00:00", "2026-06-09T01:00"],
                    "temperature_2m": [80.0, 82.0],
                    "precipitation_probability": [10, 20],
                }
            },
        )

    client = OpenMeteoClient(http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    forecast = client.forecast_set(41.8781, -87.6298, forecast_days=2)

    assert calls == ["/v1/forecast", "/v1/gfs"]
    assert len(forecast.best_match) == 2
    assert len(forecast.noaa_gfs_hrrr) == 2
    assert forecast.best_match[1].temperature_f == 82.0
    assert forecast.noaa_gfs_hrrr[0].probability_of_precipitation == 10.0
    assert set(forecast.by_source()) == {"open_meteo_best", "noaa_gfs_hrrr"}
