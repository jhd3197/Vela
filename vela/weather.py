"""The one request the desk makes to the internet, and only if asked to.

Everything else on the desk is drawn from this computer. The weather cannot be,
so it is off until the user turns it on, and the copy beside the switch says so
in those words. When it is off this module makes no request at all — the tests
assert that by handing it a transport that fails if it is ever called.

Open-Meteo needs no key and no account. Two calls are used:

* geocoding, once, when the user types a place name. The coordinates are stored
  and the place is never sent again.
* the current temperature, at most once every fifteen minutes.

What leaves this computer is a latitude and a longitude, rounded to two decimal
places — roughly a kilometre, enough for the weather and not enough for an
address. No identifier, no version, nothing about the apps or the user.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import httpx

LOG = logging.getLogger("vela.weather")

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT_SECONDS = 6.0

#: How long a reading is reused. The desk polls far more often than the weather
#: changes, and Open-Meteo is a free service being asked a favour.
CACHE_SECONDS = 15 * 60

#: Coordinates are stored and sent at this precision: about a kilometre.
COORD_PRECISION = 2

# WMO weather interpretation codes, in the words someone would actually use.
# https://open-meteo.com/en/docs — the table Open-Meteo documents for `weathercode`.
WMO = {
    0: "clear",
    1: "mostly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "fog",
    48: "freezing fog",
    51: "light drizzle",
    53: "drizzle",
    55: "heavy drizzle",
    56: "freezing drizzle",
    57: "freezing drizzle",
    61: "light rain",
    63: "rain",
    65: "heavy rain",
    66: "freezing rain",
    67: "freezing rain",
    71: "light snow",
    73: "snow",
    75: "heavy snow",
    77: "snow grains",
    80: "light showers",
    81: "showers",
    82: "heavy showers",
    85: "snow showers",
    86: "heavy snow showers",
    95: "thunderstorms",
    96: "thunderstorms with hail",
    99: "thunderstorms with hail",
}


class WeatherError(Exception):
    """A geocoding failure, with the reason to show the user."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def describe(code: Any) -> str:
    """The WMO code in plain words, or "" for a code Open-Meteo did not send."""
    try:
        return WMO.get(int(code), "")
    except (TypeError, ValueError):
        return ""


def _round(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not -90 <= number <= 180:
        return None
    return round(number, COORD_PRECISION)


class Weather:
    """The desk's opt-in weather line, cached and off by default."""

    def __init__(self, settings):
        self._settings = settings
        self._lock = threading.Lock()
        self._cache: dict[str, Any] | None = None
        self._cached_at = 0.0
        self._cache_key: tuple[float, float] | None = None

    # ------------------------------------------------------------ settings --

    def preferences(self) -> dict[str, Any]:
        desk = self._settings.get("desk") or {}
        weather = desk.get("weather") if isinstance(desk, dict) else None
        if not isinstance(weather, dict):
            weather = {}
        return {
            "enabled": bool(weather.get("enabled")),
            "latitude": _round(weather.get("latitude")),
            "longitude": _round(weather.get("longitude")),
            "label": str(weather.get("label") or "")[:80],
        }

    # ----------------------------------------------------------- geocoding --

    def locate(self, place: str, *, transport=None) -> dict[str, Any]:
        """Turn a typed place name into coordinates, once.

        The name is sent to Open-Meteo's geocoding service at this moment and
        not stored as something to look up again; what is kept is the answer.
        """
        query = (place or "").strip()
        if not query:
            raise WeatherError(422, "Type the name of a town or city.")
        if len(query) > 80:
            raise WeatherError(422, "That place name is too long.")
        args: dict[str, Any] = {"timeout": TIMEOUT_SECONDS}
        if transport is not None:
            args["transport"] = transport
        try:
            with httpx.Client(**args) as client:
                response = client.get(
                    GEOCODE_URL,
                    params={"name": query, "count": 1, "format": "json"},
                    headers={"User-Agent": "vela-server"},
                )
                response.raise_for_status()
                body = response.json()
        except Exception as exc:  # noqa: BLE001 - offline and refused look the same here
            LOG.info("geocoding failed: %s", exc)
            raise WeatherError(502, "Could not look that place up. Check the connection.") from exc

        results = body.get("results") if isinstance(body, dict) else None
        first = results[0] if isinstance(results, list) and results else None
        if not isinstance(first, dict):
            raise WeatherError(404, f"No place called “{query}” was found.")
        latitude, longitude = _round(first.get("latitude")), _round(first.get("longitude"))
        if latitude is None or longitude is None:
            raise WeatherError(502, "That place came back without coordinates.")
        label = ", ".join(
            str(part)
            for part in (first.get("name"), first.get("country_code") or first.get("country"))
            if part
        )
        return {"latitude": latitude, "longitude": longitude, "label": label[:80] or query}

    # ------------------------------------------------------------ readings --

    def current(self, *, transport=None, force: bool = False) -> dict[str, Any]:
        """The temperature now, or why there is none.

        With the switch off this returns immediately and makes no request.
        """
        preferences = self.preferences()
        if not preferences["enabled"]:
            return {"enabled": False}
        latitude, longitude = preferences["latitude"], preferences["longitude"]
        if latitude is None or longitude is None:
            return {"enabled": True, "error": "No place set."}

        key = (latitude, longitude)
        with self._lock:
            fresh = (
                self._cache is not None
                and self._cache_key == key
                and time.time() - self._cached_at < CACHE_SECONDS
            )
            if fresh and not force:
                return {**self._cache, "label": preferences["label"]}

        args: dict[str, Any] = {"timeout": TIMEOUT_SECONDS}
        if transport is not None:
            args["transport"] = transport
        try:
            with httpx.Client(**args) as client:
                response = client.get(
                    FORECAST_URL,
                    params={
                        "latitude": latitude,
                        "longitude": longitude,
                        "current_weather": "true",
                    },
                    headers={"User-Agent": "vela-server"},
                )
                response.raise_for_status()
                body = response.json()
        except Exception as exc:  # noqa: BLE001 - every failure reads the same
            LOG.info("weather fetch failed: %s", exc)
            with self._lock:
                # Being offline for a minute is not a reason to blank a reading
                # that was true a quarter of an hour ago.
                if self._cache is not None and self._cache_key == key:
                    return {**self._cache, "stale": True, "label": preferences["label"]}
            return {"enabled": True, "error": "Could not reach the weather service."}

        current = body.get("current_weather") if isinstance(body, dict) else None
        if not isinstance(current, dict):
            return {"enabled": True, "error": "The weather service sent nothing usable."}
        try:
            temperature = round(float(current.get("temperature")))
        except (TypeError, ValueError):
            return {"enabled": True, "error": "The weather service sent nothing usable."}

        reading = {
            "enabled": True,
            "temperature": temperature,
            "unit": "°C",
            "description": describe(current.get("weathercode")),
            "observedAt": str(current.get("time") or ""),
        }
        with self._lock:
            self._cache = reading
            self._cached_at = time.time()
            self._cache_key = key
        return {**reading, "label": preferences["label"]}
