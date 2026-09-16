"""The desk's opt-in weather: the switch, the cache and what leaves the computer.

Every request here goes through a stub transport, so nothing in this file
reaches the internet. The important case is the one that asserts a *negative*:
with the switch off, the transport is one that fails the test if it is called at
all, because "no request is made" is the whole promise of the switch.
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import httpx
import test_app_contract as base
from fastapi.testclient import TestClient
from vela.api import create_app
from vela.config import Config
from vela.settings import SettingsStore
from vela.weather import CACHE_SECONDS, Weather, WeatherError, describe

ROOT = base.ROOT


class Recorder:
    """An httpx transport that answers from a script and remembers every call."""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls = []

    def transport(self):
        def handle(request):
            self.calls.append(request)
            if not self._responses:
                raise AssertionError(f"unexpected request to {request.url}")
            status, body = self._responses.pop(0)
            if isinstance(body, Exception):
                raise body
            return httpx.Response(status, json=body)

        return httpx.MockTransport(handle)


def refusing():
    """A transport that fails the test the moment anything tries to use it."""

    def handle(request):
        raise AssertionError(f"the weather must not be fetched, but {request.url} was requested")

    return httpx.MockTransport(handle)


FORECAST = {"current_weather": {"temperature": 14.4, "weathercode": 45, "time": "2026-09-16T04:00"}}
GEOCODE = {
    "results": [
        {"name": "Choroní", "country_code": "VE", "latitude": 10.49788, "longitude": -67.61234}
    ]
}


class WeatherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-weather-")
        self.settings = SettingsStore(Path(self.temp.name) / "settings.json")
        self.weather = Weather(self.settings)

    def tearDown(self):
        self.temp.cleanup()

    def enable(self, **overrides):
        self.settings.patch(
            {
                "desk": {
                    "weather": {
                        "enabled": True,
                        "latitude": 10.5,
                        "longitude": -67.61,
                        "label": "Choroní",
                        **overrides,
                    }
                }
            }
        )

    # ---------------------------------------------------------- the switch --

    def test_the_weather_is_off_until_it_is_turned_on(self):
        self.assertEqual(self.weather.preferences()["enabled"], False)
        self.assertEqual(self.weather.current(transport=refusing()), {"enabled": False})

    def test_turning_it_off_again_stops_the_request(self):
        self.enable()
        recorder = Recorder((200, FORECAST))
        self.assertEqual(self.weather.current(transport=recorder.transport())["temperature"], 14)
        self.settings.patch({"desk": {"weather": {"enabled": False}}})
        # Not merely "the cached value is hidden": nothing is asked at all.
        self.assertEqual(self.weather.current(transport=refusing()), {"enabled": False})

    def test_enabled_without_a_place_asks_nothing_and_says_so(self):
        self.settings.patch({"desk": {"weather": {"enabled": True}}})
        answer = self.weather.current(transport=refusing())
        self.assertEqual(answer, {"enabled": True, "error": "No place set."})

    # --------------------------------------------------------- the reading --

    def test_a_reading_is_the_temperature_and_the_code_in_words(self):
        self.enable()
        recorder = Recorder((200, FORECAST))
        answer = self.weather.current(transport=recorder.transport())
        self.assertEqual(answer["temperature"], 14)
        self.assertEqual(answer["unit"], "°C")
        self.assertEqual(answer["description"], "fog")
        self.assertEqual(answer["label"], "Choroní")

    def test_only_rounded_coordinates_leave_the_computer(self):
        # Two decimal places is about a kilometre: enough for the weather and
        # not enough for an address.
        self.enable(latitude=10.497881234, longitude=-67.6123456)
        recorder = Recorder((200, FORECAST))
        self.weather.current(transport=recorder.transport())
        sent = dict(httpx.URL(str(recorder.calls[0].url)).params)
        self.assertEqual(sent["latitude"], "10.5")
        self.assertEqual(sent["longitude"], "-67.61")
        # The place name is not sent with the reading; only the coordinates are.
        self.assertNotIn("Choroní", str(recorder.calls[0].url))

    def test_a_second_look_inside_the_window_reuses_the_reading(self):
        self.enable()
        recorder = Recorder((200, FORECAST))
        first = self.weather.current(transport=recorder.transport())
        second = self.weather.current(transport=refusing())
        self.assertEqual(first["temperature"], second["temperature"])
        self.assertEqual(len(recorder.calls), 1)
        self.assertGreater(CACHE_SECONDS, 60)

    def test_moving_the_place_fetches_again_rather_than_showing_the_old_town(self):
        self.enable()
        recorder = Recorder((200, FORECAST), (200, {"current_weather": {"temperature": -3.2, "weathercode": 73, "time": "x"}}))
        self.weather.current(transport=recorder.transport())
        self.enable(latitude=4.6, longitude=-74.08, label="Bogotá")
        moved = self.weather.current(transport=recorder.transport())
        self.assertEqual(moved["temperature"], -3)
        self.assertEqual(moved["description"], "snow")
        self.assertEqual(len(recorder.calls), 2)

    def test_being_offline_keeps_the_last_reading_and_marks_it_stale(self):
        self.enable()
        good = Recorder((200, FORECAST))
        self.weather.current(transport=good.transport())
        broken = Recorder((0, httpx.ConnectError("offline")))
        answer = self.weather.current(transport=broken.transport(), force=True)
        self.assertEqual(answer["temperature"], 14)
        self.assertTrue(answer["stale"])

    def test_an_unusable_answer_is_reported_rather_than_invented(self):
        self.enable()
        recorder = Recorder((200, {"current_weather": {"temperature": "warm"}}))
        answer = self.weather.current(transport=recorder.transport())
        self.assertIn("error", answer)
        self.assertNotIn("temperature", answer)

    def test_an_unknown_code_has_no_description_rather_than_a_guess(self):
        self.assertEqual(describe(45), "fog")
        self.assertEqual(describe(999), "")
        self.assertEqual(describe(None), "")

    # -------------------------------------------------------- geocoding --

    def test_a_place_name_becomes_coordinates_once(self):
        recorder = Recorder((200, GEOCODE))
        found = self.weather.locate("Choroní", transport=recorder.transport())
        self.assertEqual(found["latitude"], 10.5)
        self.assertEqual(found["longitude"], -67.61)
        self.assertEqual(found["label"], "Choroní, VE")

    def test_an_empty_or_unknown_place_is_refused_with_a_reason(self):
        with self.assertRaises(WeatherError) as blank:
            self.weather.locate("  ", transport=refusing())
        self.assertEqual(blank.exception.status, 422)

        recorder = Recorder((200, {"results": []}))
        with self.assertRaises(WeatherError) as missing:
            self.weather.locate("Nowheresville", transport=recorder.transport())
        self.assertEqual(missing.exception.status, 404)

    def test_a_geocoding_failure_says_so_instead_of_storing_nothing(self):
        recorder = Recorder((0, httpx.ConnectError("offline")))
        with self.assertRaises(WeatherError) as failed:
            self.weather.locate("Choroní", transport=recorder.transport())
        self.assertEqual(failed.exception.status, 502)


class WeatherApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-weather-api-")
        self.root = Path(self.temp.name)
        apps = self.root / "catalog"
        shutil.copytree(ROOT / "tests/fixtures/chat-fixture", apps / "chat-fixture")
        self.config = Config(self.root / "data", apps, ROOT / "web/dist")
        self.config.ensure_dirs()
        self.client = TestClient(create_app(self.config))
        token = self.client.get("/api/session", headers={"X-Vela-Bootstrap": "1"}).json()["token"]
        self.hub = {"Authorization": "Bearer " + token}

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def test_the_weather_needs_a_hub_session(self):
        self.assertEqual(self.client.get("/api/weather").status_code, 401)
        self.assertEqual(self.client.post("/api/weather/locate", json={"place": "x"}).status_code, 401)

    def test_a_fresh_server_reports_the_weather_as_off(self):
        answer = self.client.get("/api/weather", headers=self.hub)
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer.json(), {"enabled": False})

    def test_the_setting_is_stored_and_read_back(self):
        saved = self.client.patch(
            "/api/settings",
            headers=self.hub,
            json={
                "desk": {
                    "weather": {
                        "enabled": True,
                        "latitude": 10.5,
                        "longitude": -67.61,
                        "label": "Choroní",
                    }
                }
            },
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        desk = self.client.get("/api/settings", headers=self.hub).json()["desk"]
        self.assertEqual(desk["weather"]["enabled"], True)
        self.assertEqual(desk["weather"]["label"], "Choroní")
        # The wallpaper choice is untouched by a weather patch.
        self.assertEqual(desk["wallpaper"], "choroni")


if __name__ == "__main__":
    unittest.main()
