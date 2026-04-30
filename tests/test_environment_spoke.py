"""Tests for the environment spoke agent."""
from __future__ import annotations

from unittest.mock import patch


def test_build_spoke_tools_returns_delegate_environment():
    from prax.agent.spokes.environment import build_spoke_tools

    tools = build_spoke_tools()
    assert len(tools) == 1
    assert tools[0].name == "delegate_environment"


def test_all_spoke_tools_includes_environment():
    from prax.agent.spokes import build_all_spoke_tools

    names = {tool.name for tool in build_all_spoke_tools()}
    assert "delegate_environment" in names


def test_environment_tools_not_on_orchestrator_as_raw_tools():
    from prax.agent.tools import build_default_tools

    names = {tool.name for tool in build_default_tools()}
    assert "delegate_environment" in names
    assert "environment_resolve_location" not in names
    assert "environment_current_weather" not in names


def test_resolve_location_from_user_notes(monkeypatch):
    from prax.agent.spokes.environment import agent
    from prax.agent.user_context import current_user_id

    monkeypatch.setattr(agent, "_location_from_user_notes", lambda _uid: "Los Angeles, CA")
    monkeypatch.setattr(agent, "_known_timezone", lambda _uid: "")
    token = current_user_id.set("user1")
    try:
        result = agent.environment_resolve_location.invoke({"location_hint": ""})
    finally:
        current_user_id.reset(token)

    assert "CONFIRMED_LOCATION" in result
    assert "Los Angeles, CA" in result
    assert "user_notes.md" in result


def test_timezone_only_is_uncertain(monkeypatch):
    from prax.agent.spokes.environment import agent
    from prax.agent.user_context import current_user_id

    monkeypatch.setattr(agent, "_location_from_user_notes", lambda _uid: "")
    monkeypatch.setattr(agent, "_known_timezone", lambda _uid: "America/Los_Angeles")
    token = current_user_id.set("user1")
    try:
        result = agent.environment_resolve_location.invoke({"location_hint": ""})
    finally:
        current_user_id.reset(token)

    assert "LOCATION_UNCERTAIN" in result
    assert "timezone" in result.lower()
    assert "What city/region" in result


def test_weather_rejects_timezone_as_location():
    from prax.agent.spokes.environment import agent

    result = agent.environment_current_weather.invoke({"location": "America/Los_Angeles"})
    assert "LOCATION_UNCERTAIN" in result
    assert "timezone" in result.lower()


def test_weather_fetch_success(monkeypatch):
    from prax.agent.spokes.environment import agent

    def fake_fetch_json(url, timeout=12):
        if "geocoding-api.open-meteo.com" in url:
            return {
                "results": [{
                    "name": "Los Angeles",
                    "admin1": "California",
                    "country": "United States",
                    "latitude": 34.05,
                    "longitude": -118.24,
                    "population": 3898747,
                }]
            }
        if "api.open-meteo.com" in url:
            return {
                "current": {
                    "time": "2026-04-27T09:00",
                    "temperature_2m": 72.1,
                    "relative_humidity_2m": 45,
                    "precipitation": 0,
                    "weather_code": 1,
                    "wind_speed_10m": 6.4,
                },
                "current_units": {
                    "temperature_2m": "°F",
                    "relative_humidity_2m": "%",
                    "precipitation": "inch",
                    "wind_speed_10m": "mph",
                },
            }
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(agent, "_fetch_json", fake_fetch_json)

    result = agent.environment_current_weather.invoke({"location": "Los Angeles, CA"})
    assert "VERIFIED_WEATHER" in result
    assert "Los Angeles, California, United States" in result
    assert "72.1 °F" in result
    assert "api.open-meteo.com" in result


def test_weather_geocode_retries_city_with_state_filter(monkeypatch):
    from prax.agent.spokes.environment import agent

    def fake_fetch_json(url, timeout=12):
        if "geocoding-api.open-meteo.com" in url and "Irvine%2C+CA" in url:
            return {}
        if "geocoding-api.open-meteo.com" in url and "Irvine" in url:
            return {
                "results": [{
                    "name": "Irvine",
                    "admin1": "California",
                    "country": "United States",
                    "country_code": "US",
                    "latitude": 33.6846,
                    "longitude": -117.8265,
                    "population": 307670,
                }]
            }
        if "api.open-meteo.com" in url:
            return {
                "current": {
                    "time": "2026-04-27T09:00",
                    "temperature_2m": 70,
                    "relative_humidity_2m": 50,
                    "precipitation": 0,
                    "weather_code": 0,
                    "wind_speed_10m": 5,
                },
                "current_units": {
                    "temperature_2m": "°F",
                    "relative_humidity_2m": "%",
                    "precipitation": "inch",
                    "wind_speed_10m": "mph",
                },
            }
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(agent, "_fetch_json", fake_fetch_json)

    result = agent.environment_current_weather.invoke({"location": "Irvine, CA"})
    assert "VERIFIED_WEATHER" in result
    assert "Irvine, California, United States" in result


def test_weather_geocodes_us_zip(monkeypatch):
    from prax.agent.spokes.environment import agent

    def fake_fetch_json(url, timeout=12):
        if "api.zippopotam.us/us/92780" in url:
            return {
                "country": "United States",
                "country abbreviation": "US",
                "places": [{
                    "place name": "Tustin",
                    "state": "California",
                    "latitude": "33.7372",
                    "longitude": "-117.8198",
                }],
            }
        if "api.open-meteo.com" in url:
            return {
                "current": {
                    "time": "2026-04-27T09:00",
                    "temperature_2m": 71,
                    "relative_humidity_2m": 43,
                    "precipitation": 0,
                    "weather_code": 1,
                    "wind_speed_10m": 10,
                },
                "current_units": {
                    "temperature_2m": "°F",
                    "relative_humidity_2m": "%",
                    "precipitation": "inch",
                    "wind_speed_10m": "mph",
                },
            }
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(agent, "_fetch_json", fake_fetch_json)

    result = agent.environment_current_weather.invoke({"location": "Tustin, CA 92780"})
    assert "VERIFIED_WEATHER" in result
    assert "Tustin, California, United States" in result
    assert "api.zippopotam.us/us/92780" in result


def test_weather_forecast_tomorrow(monkeypatch):
    from prax.agent.spokes.environment import agent

    def fake_fetch_json(url, timeout=12):
        if "geocoding-api.open-meteo.com" in url:
            return {
                "results": [{
                    "name": "Irvine",
                    "admin1": "California",
                    "country": "United States",
                    "country_code": "US",
                    "latitude": 33.6846,
                    "longitude": -117.8265,
                    "population": 307670,
                }]
            }
        if "api.open-meteo.com" in url:
            return {
                "daily": {
                    "time": ["2026-04-28", "2026-04-29"],
                    "weather_code": [0, 1],
                    "temperature_2m_max": [72, 74],
                    "temperature_2m_min": [55, 56],
                    "precipitation_probability_max": [5, 10],
                    "precipitation_sum": [0, 0],
                    "wind_speed_10m_max": [9, 11],
                },
                "daily_units": {
                    "temperature_2m_max": "°F",
                    "temperature_2m_min": "°F",
                    "precipitation_probability_max": "%",
                    "precipitation_sum": "inch",
                    "wind_speed_10m_max": "mph",
                },
            }
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(agent, "_fetch_json", fake_fetch_json)

    result = agent.environment_weather_forecast.invoke({
        "location": "Irvine, CA",
        "target": "tomorrow",
    })
    assert "VERIFIED_FORECAST" in result
    assert "target: tomorrow" in result
    assert "date: 2026-04-29" in result
    assert "high: 74 °F" in result
    assert "precipitation_probability: 10 %" in result


def test_weather_asks_on_ambiguous_geocode(monkeypatch):
    from prax.agent.spokes.environment import agent

    def fake_fetch_json(url, timeout=12):
        return {
            "results": [
                {"name": "Springfield", "admin1": "Illinois", "country": "United States", "population": 114394},
                {"name": "Springfield", "admin1": "Missouri", "country": "United States", "population": 169176},
            ]
        }

    monkeypatch.setattr(agent, "_fetch_json", fake_fetch_json)

    result = agent.environment_current_weather.invoke({"location": "Springfield"})
    assert "LOCATION_UNCERTAIN" in result
    assert "ambiguous" in result.lower()
    assert "Springfield, Illinois" in result


def test_delegate_environment_calls_run_spoke():
    with patch("prax.agent.spokes.environment.agent.run_spoke") as mock_run:
        mock_run.return_value = "LOCATION_UNCERTAIN\nask_user: What city/region?"
        from prax.agent.spokes.environment.agent import delegate_environment

        result = delegate_environment.invoke({"task": "What's my weather?"})

        assert "LOCATION_UNCERTAIN" in result
        mock_run.assert_called_once()
        kwargs = mock_run.call_args.kwargs
        assert kwargs["config_key"] == "subagent_environment"
        assert kwargs["role_name"] == "Environment"
        assert "weather" in kwargs["task"].lower()
        assert "VERIFIED_WEATHER" in kwargs["preserve_tool_result_prefixes"]
        assert "VERIFIED_FORECAST" in kwargs["preserve_tool_result_prefixes"]
