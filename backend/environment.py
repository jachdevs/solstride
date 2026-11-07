"""Current conditions at a location: weather (Open-Meteo), sun position, suggested sun mode."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import httpx

from sun import sun_position

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_CACHE: dict[tuple[float, float], tuple[float, dict]] = {}
_TTL_S = 600

# WMO weather codes -> short label
_WMO = {
    0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Fog", 51: "Drizzle", 53: "Drizzle", 55: "Drizzle",
    61: "Rain", 63: "Rain", 65: "Heavy rain", 71: "Snow", 73: "Snow", 75: "Heavy snow",
    80: "Showers", 81: "Showers", 82: "Heavy showers", 95: "Thunderstorm",
}


def _fetch_weather(lat: float, lng: float) -> dict:
    key = (round(lat, 2), round(lng, 2))
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < _TTL_S:
        return hit[1]
    params = {
        "latitude": lat,
        "longitude": lng,
        "current": "temperature_2m,apparent_temperature,uv_index,cloud_cover,is_day,weather_code",
        "daily": "sunrise,sunset",
        "timezone": "auto",
        "forecast_days": 1,
        "temperature_unit": "fahrenheit",
    }
    r = httpx.get(FORECAST_URL, params=params, timeout=6)
    r.raise_for_status()
    data = r.json()
    _CACHE[key] = (time.time(), data)
    return data


def suggest_sun_mode(feels_like_f: float | None, uv: float | None, sun_alt: float) -> tuple[str, str]:
    """Return (mode, reason). mode is 'min' or 'max'."""
    if sun_alt <= 0:
        return "max", "Sun is down, so shade won't change much"
    uv = uv or 0
    if feels_like_f is not None and feels_like_f >= 75:
        return "min", f"Feels like {feels_like_f:.0f}°F, so stay in the shade"
    if uv >= 6:
        return "min", f"UV index {uv:.0f} is high, so stay in the shade"
    if feels_like_f is not None and feels_like_f <= 55:
        return "max", f"Feels like {feels_like_f:.0f}°F, so soak up the sun"
    if uv >= 3:
        return "min", f"Moderate UV ({uv:.0f}), so lean toward shade"
    return "max", "Mild and low UV, so enjoy the sun"


def get_environment(lat: float, lng: float) -> dict:
    now = datetime.now(timezone.utc)
    alt, az = sun_position(lat, lng, now)

    weather_ok = True
    try:
        w = _fetch_weather(lat, lng)
        cur = w.get("current", {})
        offset = timedelta(seconds=w.get("utc_offset_seconds", 0))
        sunset_local = datetime.fromisoformat(w["daily"]["sunset"][0])
        sunset_utc = (sunset_local - offset).replace(tzinfo=timezone.utc)
        minutes_to_sunset = round((sunset_utc - now).total_seconds() / 60)
        temp = cur.get("temperature_2m")
        feels = cur.get("apparent_temperature")
        uv = cur.get("uv_index")
        cloud = cur.get("cloud_cover")
        condition = _WMO.get(cur.get("weather_code"), "—")
        sunset_str = sunset_local.strftime("%-I:%M %p")
        tz = w.get("timezone")
    except Exception:  # weather is nice-to-have; sun math still works offline
        weather_ok = False
        temp = feels = uv = cloud = None
        minutes_to_sunset = None
        condition = "Unavailable"
        sunset_str = None
        tz = None

    mode, reason = suggest_sun_mode(feels, uv, alt)

    notes = []
    if alt <= 0:
        notes.append("The sun is down, so sun and shade routing has little effect right now.")
    elif cloud is not None and cloud >= 80:
        notes.append("Cloudy now, so sun mode has little effect.")
    elif alt < 10:
        notes.append("The sun is low, so shadows are long and most streets are shaded.")

    return {
        "lat": lat,
        "lng": lng,
        "weather_ok": weather_ok,
        "temperature_f": temp,
        "feels_like_f": feels,
        "uv_index": uv,
        "cloud_cover": cloud,
        "condition": condition,
        "sun_altitude": round(alt, 1),
        "sun_azimuth": round(az, 1),
        "sunset_local": sunset_str,
        "minutes_to_sunset": minutes_to_sunset,
        "timezone": tz,
        "suggested_sun": mode,
        "suggestion_reason": reason,
        "notes": notes,
    }
