"""Environment spoke agent — local conditions and situational awareness.

This spoke owns "what is happening around the user right now" queries:
weather, local hazards, time/location context, and other common
survival-adjacent signals.  It deliberately treats timezone as insufficient
for weather.  If it cannot resolve a concrete location, it asks.
"""
from __future__ import annotations

import json
import logging
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from langchain_core.tools import tool

from prax.agent.spokes._runner import run_spoke
from prax.settings import settings

logger = logging.getLogger(__name__)

_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_US_ZIP_URL = "https://api.zippopotam.us/us"

_LOCATION_KEY_RE = re.compile(
    r"^\s*(?:[-*]\s*)?"
    r"(?:weather[_ -]?location|current[_ -]?location|home[_ -]?location|"
    r"location|city|home|based in|lives in|live in)\s*[:=-]\s*(.+?)\s*$",
    re.IGNORECASE,
)
_LOCATION_PHRASE_RE = re.compile(
    r"\b(?:weather|forecast|conditions|temperature|air quality)\s+"
    r"(?:in|for|near|at)\s+([^?.!\n]+)",
    re.IGNORECASE,
)
_TIMEZONE_RE = re.compile(r"\b[A-Z][A-Za-z_]+/[A-Za-z0-9_+\-/]+\b")
_AMBIGUOUS_SHORT_CODES = {"la", "ny", "sf", "dc", "wa", "pa", "ga", "in", "or", "ok"}
_US_ZIP_RE = re.compile(r"\b(\d{5})(?:-\d{4})?\b")
_US_STATE_ABBR = {
    "AL": "Alabama",
    "AK": "Alaska",
    "AZ": "Arizona",
    "AR": "Arkansas",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DE": "Delaware",
    "FL": "Florida",
    "GA": "Georgia",
    "HI": "Hawaii",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "IA": "Iowa",
    "KS": "Kansas",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "ME": "Maine",
    "MD": "Maryland",
    "MA": "Massachusetts",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MS": "Mississippi",
    "MO": "Missouri",
    "MT": "Montana",
    "NE": "Nebraska",
    "NV": "Nevada",
    "NH": "New Hampshire",
    "NJ": "New Jersey",
    "NM": "New Mexico",
    "NY": "New York",
    "NC": "North Carolina",
    "ND": "North Dakota",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "RI": "Rhode Island",
    "SC": "South Carolina",
    "SD": "South Dakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VT": "Vermont",
    "VA": "Virginia",
    "WA": "Washington",
    "WV": "West Virginia",
    "WI": "Wisconsin",
    "WY": "Wyoming",
    "DC": "District of Columbia",
}


@dataclass
class LocationResolution:
    status: str
    location: str = ""
    source: str = ""
    reason: str = ""

    def to_text(self) -> str:
        if self.status == "confirmed":
            return (
                "CONFIRMED_LOCATION\n"
                f"location: {self.location}\n"
                f"source: {self.source}"
            )
        return (
            "LOCATION_UNCERTAIN\n"
            f"reason: {self.reason or 'No concrete city/region is known.'}\n"
            "ask_user: What city/region should I use for local conditions?"
        )


SYSTEM_PROMPT = """\
You are the Environment Agent for {agent_name}. You handle local, current,
situational-awareness questions: weather, local conditions, time/location
context, hazards, and other common external signals around the user.

## Core rule
Never infer weather from timezone, memory vibes, or generic web snippets.
Weather requires a concrete location plus live source data. If location is not
known, ask for the city/region.

## Required workflow for weather/local conditions
1. Call `environment_resolve_location` first. Pass any explicit location from
   the task; if none exists, pass an empty string so it can inspect profile and
   user notes.
2. If it returns `LOCATION_UNCERTAIN`, stop and ask the location question.
3. If it returns `CONFIRMED_LOCATION`, pass that exact location to:
   - `environment_weather_forecast` for forecast, tomorrow, this week, high/low,
     rain chance, or planning-ahead questions.
   - `environment_current_weather` for right-now conditions.
4. Relay the source and confidence plainly. Do not embellish.

Timezone helps choose a clock. It is not a weather location.
"""


def _fetch_json(url: str, timeout: int = 12) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "Prax Environment Agent"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed HTTPS API URLs
        return json.loads(resp.read().decode("utf-8"))


def _clean_location(value: str) -> str:
    value = value.strip().strip("`'\"")
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"\s+(today|tomorrow|tonight|this morning|this afternoon)$", "", value, flags=re.IGNORECASE)
    return value.strip(" ,.;")


def _explicit_location_from_text(text: str) -> str:
    text = text or ""
    match = _LOCATION_PHRASE_RE.search(text)
    if match:
        return _clean_location(match.group(1))
    # If the caller passes only a likely location, accept it as a hint.
    candidate = _clean_location(text)
    if (
        2 <= len(candidate) <= 80
        and not re.search(r"\b(weather|forecast|conditions|temperature|briefing|user|their|my)\b", candidate, re.I)
    ):
        return candidate
    return ""


def _location_from_user_notes(user_id: str) -> str:
    if not user_id:
        return ""
    try:
        from prax.services.workspace_service import read_user_notes
        notes = read_user_notes(user_id)
    except Exception:
        return ""
    for line in notes.splitlines():
        if "timezone" in line.lower():
            continue
        match = _LOCATION_KEY_RE.match(line)
        if match:
            candidate = _clean_location(match.group(1))
            if candidate:
                return candidate
    return ""


def _known_timezone(user_id: str) -> str:
    try:
        from prax.agent.user_context import current_user
        user = current_user.get()
        if user and user.timezone:
            return user.timezone
    except Exception:
        pass
    if user_id:
        try:
            from prax.services.identity_service import get_user
            user = get_user(user_id)
            if user and user.timezone:
                return user.timezone
        except Exception:
            pass
    return ""


def _looks_ambiguous_location(location: str) -> str:
    normalized = location.strip().lower().replace(".", "")
    if normalized in _AMBIGUOUS_SHORT_CODES:
        return f"'{location}' is too ambiguous for weather; use city + state/country."
    if _TIMEZONE_RE.search(location):
        return f"'{location}' is a timezone, not a weather location."
    return ""


def _resolve_location(location_hint: str, user_id: str) -> LocationResolution:
    explicit = _explicit_location_from_text(location_hint)
    if explicit:
        reason = _looks_ambiguous_location(explicit)
        if reason:
            return LocationResolution(status="uncertain", reason=reason)
        return LocationResolution(status="confirmed", location=explicit, source="explicit task")

    notes_location = _location_from_user_notes(user_id)
    if notes_location:
        reason = _looks_ambiguous_location(notes_location)
        if reason:
            return LocationResolution(status="uncertain", reason=f"user_notes.md has ambiguous location: {reason}")
        return LocationResolution(status="confirmed", location=notes_location, source="user_notes.md")

    timezone = _known_timezone(user_id)
    if timezone:
        return LocationResolution(
            status="uncertain",
            reason=f"I know the timezone ({timezone}) but not a concrete city/region.",
        )

    return LocationResolution(status="uncertain", reason="No saved location or explicit location was found.")


def _display_location(result: dict[str, Any]) -> str:
    parts = [str(result.get("name") or "").strip()]
    admin = str(result.get("admin1") or "").strip()
    country = str(result.get("country") or "").strip()
    if admin:
        parts.append(admin)
    if country:
        parts.append(country)
    return ", ".join(p for p in parts if p)


def _extract_us_zip(location: str) -> str:
    match = _US_ZIP_RE.search(location or "")
    return match.group(1) if match else ""


def _state_hint(location: str) -> str:
    text = location or ""
    parts = [_clean_location(part) for part in text.split(",")]
    for part in parts[1:]:
        upper = part.upper()
        if upper in _US_STATE_ABBR:
            return _US_STATE_ABBR[upper]
        for name in _US_STATE_ABBR.values():
            if part.lower() == name.lower():
                return name
    for token in re.findall(r"\b[A-Z]{2}\b", text):
        if token in _US_STATE_ABBR:
            return _US_STATE_ABBR[token]
    for name in _US_STATE_ABBR.values():
        if re.search(rf"\b{re.escape(name)}\b", text, re.IGNORECASE):
            return name
    return ""


def _city_geocode_query(location: str) -> str:
    candidate = _US_ZIP_RE.sub("", location or "")
    candidate = candidate.split(",", 1)[0]
    state = _state_hint(location)
    if state:
        candidate = re.sub(rf"\b{re.escape(state)}\b", "", candidate, flags=re.IGNORECASE)
        for abbr, name in _US_STATE_ABBR.items():
            if name == state:
                candidate = re.sub(rf"\b{re.escape(abbr)}\b", "", candidate)
                break
    return _clean_location(candidate)


def _open_meteo_geocode_query(query: str) -> tuple[list[dict[str, Any]], str]:
    params = urllib.parse.urlencode({"name": query, "count": 10, "language": "en", "format": "json"})
    url = f"{_GEOCODING_URL}?{params}"
    data = _fetch_json(url)
    return data.get("results") or [], url


def _geocode_us_zip(location: str) -> tuple[dict[str, Any] | None, str]:
    zip_code = _extract_us_zip(location)
    if not zip_code:
        return None, ""

    url = f"{_US_ZIP_URL}/{zip_code}"
    data = _fetch_json(url)
    places = data.get("places") or []
    if not places:
        return None, f"No ZIP geocoding result for {zip_code!r}. Source: {url}"

    place = places[0]
    geo = {
        "name": place.get("place name") or zip_code,
        "admin1": place.get("state") or "",
        "country": data.get("country") or "United States",
        "country_code": data.get("country abbreviation") or "US",
        "latitude": float(place.get("latitude")),
        "longitude": float(place.get("longitude")),
        "population": 0,
    }
    return geo, url


def _filter_geocode_results(
    results: list[dict[str, Any]],
    location: str,
) -> list[dict[str, Any]]:
    state = _state_hint(location)
    if not state:
        return results

    filtered = []
    for result in results:
        country = str(result.get("country") or "").lower()
        country_code = str(result.get("country_code") or "").upper()
        admin = str(result.get("admin1") or "").lower()
        if (
            admin == state.lower()
            and (country_code == "US" or country == "united states")
        ):
            filtered.append(result)
    return filtered


def _choose_geocode_result(
    results: list[dict[str, Any]],
    location: str,
    source: str,
) -> tuple[dict[str, Any] | None, str]:
    if not results:
        return None, f"No geocoding result for {location!r}. Source: {source}"

    candidates = _filter_geocode_results(results, location)
    state = _state_hint(location)
    if state and not candidates:
        return None, f"No geocoding result for {location!r} in {state}. Source: {source}"
    if state and candidates:
        return candidates[0], source

    if len(candidates) == 1 or "," in location:
        return candidates[0], source

    first = candidates[0]
    first_pop = int(first.get("population") or 0)
    second_pop = int(candidates[1].get("population") or 0) if len(candidates) > 1 else 0
    if first_pop and first_pop >= max(1, second_pop) * 5:
        return first, source

    options = "; ".join(_display_location(r) for r in candidates[:3])
    return None, (
        f"Location {location!r} is ambiguous. Matching options: {options}. "
        "Ask the user for city + state/country."
    )


def _geocode(location: str) -> tuple[dict[str, Any] | None, str]:
    attempted: list[str] = []

    if _extract_us_zip(location):
        try:
            geo, source = _geocode_us_zip(location)
            if geo:
                return geo, source
            if source:
                attempted.append(source)
        except Exception as exc:
            attempted.append(f"ZIP geocoder failed for {_extract_us_zip(location)!r}: {exc}")

    queries = []
    for query in [location, _city_geocode_query(location)]:
        query = _clean_location(query)
        if query and query not in queries:
            queries.append(query)

    for query in queries:
        results, source = _open_meteo_geocode_query(query)
        attempted.append(source)
        geo, detail = _choose_geocode_result(results, location, source)
        if geo:
            return geo, detail
        if "ambiguous" in detail.lower():
            return None, detail

    return None, (
        f"No geocoding result for {location!r}. Sources tried: "
        + "; ".join(attempted)
    )


def _weather_code_label(code: int | None) -> str:
    labels = {
        0: "clear sky",
        1: "mainly clear",
        2: "partly cloudy",
        3: "overcast",
        45: "fog",
        48: "depositing rime fog",
        51: "light drizzle",
        53: "moderate drizzle",
        55: "dense drizzle",
        61: "slight rain",
        63: "moderate rain",
        65: "heavy rain",
        71: "slight snow",
        73: "moderate snow",
        75: "heavy snow",
        80: "slight rain showers",
        81: "moderate rain showers",
        82: "violent rain showers",
        95: "thunderstorm",
    }
    return labels.get(code, f"weather code {code}" if code is not None else "unknown")


@tool
def environment_resolve_location(location_hint: str = "") -> str:
    """Resolve the user's concrete location for local conditions.

    Pass an explicit city/region if the user gave one. If omitted, this checks
    user notes and identity timezone. Timezone alone returns LOCATION_UNCERTAIN.
    """
    from prax.agent.user_context import current_user_id
    uid = current_user_id.get() or ""
    return _resolve_location(location_hint, uid).to_text()


@tool
def environment_current_weather(location: str) -> str:
    """Fetch current weather for a concrete city/region using live data.

    Rejects missing, timezone-only, or ambiguous locations. Uses Open-Meteo
    geocoding + forecast APIs because they require no per-user API key.
    """
    location = _clean_location(location)
    if not location:
        return (
            "LOCATION_REQUIRED\n"
            "reason: No concrete city/region was provided.\n"
            "ask_user: What city/region should I use for weather?"
        )
    reason = _looks_ambiguous_location(location)
    if reason:
        return (
            "LOCATION_UNCERTAIN\n"
            f"reason: {reason}\n"
            "ask_user: What city/region should I use for weather?"
        )

    try:
        geo, geo_source = _geocode(location)
        if not geo:
            return f"LOCATION_UNCERTAIN\nreason: {geo_source}"

        lat = geo.get("latitude")
        lon = geo.get("longitude")
        params = urllib.parse.urlencode({
            "latitude": lat,
            "longitude": lon,
            "current": ",".join([
                "temperature_2m",
                "relative_humidity_2m",
                "precipitation",
                "weather_code",
                "wind_speed_10m",
            ]),
            "temperature_unit": "fahrenheit",
            "wind_speed_unit": "mph",
            "precipitation_unit": "inch",
            "timezone": "auto",
        })
        weather_source = f"{_FORECAST_URL}?{params}"
        data = _fetch_json(weather_source)
    except Exception as exc:
        logger.warning("Environment weather fetch failed: %s", exc, exc_info=True)
        return f"WEATHER_UNAVAILABLE\nreason: Could not fetch live weather data: {exc}"

    current = data.get("current") or {}
    units = data.get("current_units") or {}
    code = current.get("weather_code")
    try:
        code_int = int(code) if code is not None else None
    except (TypeError, ValueError):
        code_int = None

    return (
        "VERIFIED_WEATHER\n"
        f"location: {_display_location(geo)}\n"
        f"observed_at: {current.get('time', 'unknown')}\n"
        f"conditions: {_weather_code_label(code_int)}\n"
        f"temperature: {current.get('temperature_2m', 'unknown')} {units.get('temperature_2m', '°F')}\n"
        f"humidity: {current.get('relative_humidity_2m', 'unknown')} {units.get('relative_humidity_2m', '%')}\n"
        f"precipitation: {current.get('precipitation', 'unknown')} {units.get('precipitation', 'in')}\n"
        f"wind: {current.get('wind_speed_10m', 'unknown')} {units.get('wind_speed_10m', 'mph')}\n"
        f"sources: {geo_source} ; {weather_source}"
    )


def _daily_value(daily: dict[str, Any], key: str, index: int) -> Any:
    values = daily.get(key) or []
    if not isinstance(values, list) or index < 0 or index >= len(values):
        return "unknown"
    return values[index]


def _forecast_target_index(target: str) -> int | None:
    normalized = (target or "").strip().lower()
    if not normalized or normalized in {"tomorrow", "next day"}:
        return 1
    if normalized in {"today", "now"}:
        return 0
    return None


@tool
def environment_weather_forecast(location: str, target: str = "tomorrow") -> str:
    """Fetch a daily forecast for a concrete city/region using live data.

    Use this for tomorrow, weekly forecast, high/low, rain chance, and other
    planning-ahead weather questions. Set target to "today", "tomorrow", or
    "week".
    """
    location = _clean_location(location)
    if not location:
        return (
            "LOCATION_REQUIRED\n"
            "reason: No concrete city/region was provided.\n"
            "ask_user: What city/region should I use for weather?"
        )
    reason = _looks_ambiguous_location(location)
    if reason:
        return (
            "LOCATION_UNCERTAIN\n"
            f"reason: {reason}\n"
            "ask_user: What city/region should I use for weather?"
        )

    try:
        geo, geo_source = _geocode(location)
        if not geo:
            return f"LOCATION_UNCERTAIN\nreason: {geo_source}"

        lat = geo.get("latitude")
        lon = geo.get("longitude")
        params = urllib.parse.urlencode({
            "latitude": lat,
            "longitude": lon,
            "daily": ",".join([
                "weather_code",
                "temperature_2m_max",
                "temperature_2m_min",
                "precipitation_probability_max",
                "precipitation_sum",
                "wind_speed_10m_max",
            ]),
            "temperature_unit": "fahrenheit",
            "wind_speed_unit": "mph",
            "precipitation_unit": "inch",
            "timezone": "auto",
            "forecast_days": 7,
        })
        weather_source = f"{_FORECAST_URL}?{params}"
        data = _fetch_json(weather_source)
    except Exception as exc:
        logger.warning("Environment forecast fetch failed: %s", exc, exc_info=True)
        return f"WEATHER_UNAVAILABLE\nreason: Could not fetch live forecast data: {exc}"

    daily = data.get("daily") or {}
    units = data.get("daily_units") or {}
    target_index = _forecast_target_index(target)
    target_label = (target or "tomorrow").strip().lower()

    def line_for(index: int) -> str:
        code = _daily_value(daily, "weather_code", index)
        try:
            code_int = int(code) if code != "unknown" else None
        except (TypeError, ValueError):
            code_int = None
        return (
            f"- date: {_daily_value(daily, 'time', index)}\n"
            f"  conditions: {_weather_code_label(code_int)}\n"
            f"  high: {_daily_value(daily, 'temperature_2m_max', index)} {units.get('temperature_2m_max', '°F')}\n"
            f"  low: {_daily_value(daily, 'temperature_2m_min', index)} {units.get('temperature_2m_min', '°F')}\n"
            f"  precipitation_probability: {_daily_value(daily, 'precipitation_probability_max', index)} {units.get('precipitation_probability_max', '%')}\n"
            f"  precipitation: {_daily_value(daily, 'precipitation_sum', index)} {units.get('precipitation_sum', 'in')}\n"
            f"  wind_max: {_daily_value(daily, 'wind_speed_10m_max', index)} {units.get('wind_speed_10m_max', 'mph')}"
        )

    dates = daily.get("time") or []
    if target_index is not None and target_index < len(dates):
        forecast_body = line_for(target_index)
    else:
        forecast_body = "\n".join(line_for(i) for i in range(min(7, len(dates))))

    return (
        "VERIFIED_FORECAST\n"
        f"location: {_display_location(geo)}\n"
        f"target: {target_label}\n"
        "forecast:\n"
        f"{forecast_body}\n"
        f"sources: {geo_source} ; {weather_source}"
    )


def build_tools() -> list:
    """Return tools available to the environment spoke."""
    from prax.agent.tools import get_current_datetime

    return [
        environment_resolve_location,
        environment_current_weather,
        environment_weather_forecast,
        get_current_datetime,
    ]


@tool
def delegate_environment(task: str) -> str:
    """Delegate local conditions and situational-awareness work.

    Use this for:
    - weather, forecast, local conditions, or "what should I know before going out?"
    - local hazard/alert context when it depends on the user's location
    - tasks that need the user's timezone/location interpreted carefully

    The Environment Agent must resolve a concrete city/region before weather.
    If it only knows timezone or has no saved location, it asks instead of
    inventing a forecast.
    """
    prompt = SYSTEM_PROMPT.format(agent_name=settings.agent_name)
    return run_spoke(
        task=task,
        system_prompt=prompt,
        tools=build_tools(),
        config_key="subagent_environment",
        role_name="Environment",
        channel=None,
        recursion_limit=10,
    )


def build_spoke_tools() -> list:
    return [delegate_environment]
