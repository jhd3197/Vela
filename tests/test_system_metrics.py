"""The desk's host metrics: the snapshot shape, the psutil-less fallback and
volume validation.

psutil is stubbed everywhere so the assertions are about Vela's own behaviour
rather than about this machine's CPU, and so the suite reports the same thing
on a runner without psutil installed. Everything uses disposable data.
"""
import copy
import json
import os
import shutil
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import test_app_contract as base
from fastapi.testclient import TestClient
from vela.api import create_app
from vela.config import Config
from vela.settings import SettingsStore
from vela.system_metrics import NET_KEEP_DAYS, SystemMetrics, format_uptime, validate_volumes

ROOT = base.ROOT


class FakePsutil:
    """Just the surface `system_metrics` uses, with readings we control."""

    def __init__(self, samples=(11.0, 22.0, 33.0), usage=None, unreachable=(), net=None):
        self._samples = list(samples)
        self._taken = 0
        self._usage = usage
        self._unreachable = set(unreachable)
        # Cumulative byte counters, read in order, the last one repeating.
        self._net = list(net or [(0, 0)])
        self._net_taken = 0

    def net_io_counters(self):
        reading = self._net[min(self._net_taken, len(self._net) - 1)]
        self._net_taken += 1
        return SimpleNamespace(bytes_sent=reading[0], bytes_recv=reading[1])

    def cpu_percent(self, interval=None):
        assert interval is None, "a request must never block on a CPU reading"
        value = self._samples[min(self._taken, len(self._samples) - 1)]
        self._taken += 1
        return value

    def cpu_count(self, logical=True):
        return 8 if logical else 4

    def virtual_memory(self):
        return SimpleNamespace(total=16 * 1024**3, used=6 * 1024**3, percent=37.5)

    def boot_time(self):
        # Fixed far in the past; the assertions only check the shape.
        return 1_700_000_000.0

    def disk_usage(self, path):
        if path in self._unreachable:
            raise PermissionError(path)
        total, used = self._usage or (500 * 1024**3, 214 * 1024**3)
        return SimpleNamespace(total=total, used=used, free=total - used, percent=round(used / total * 100, 1))


def metrics_for(tmp: Path, psutil, desk=None):
    config = Config(tmp / "data", tmp / "catalog", ROOT / "web/dist")
    config.ensure_dirs()
    settings = SettingsStore(config.settings_file)
    if desk is not None:
        settings.set("desk", desk)
    store = SystemMetrics(config, settings)
    return store, config


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-metrics-")
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_snapshot_reports_cpu_memory_uptime_and_the_data_volume(self):
        psutil = FakePsutil()
        store, config = metrics_for(self.root, psutil)
        with mock.patch("vela.system_metrics._psutil", return_value=psutil):
            snapshot = store.snapshot()
        self.assertTrue(snapshot["available"])
        self.assertEqual(snapshot["cpu"]["cores"], 8)
        self.assertEqual(snapshot["memory"]["total"], 16 * 1024**3)
        self.assertEqual(snapshot["memory"]["percent"], 37.5)
        self.assertGreater(snapshot["uptime"]["seconds"], 0)
        self.assertTrue(snapshot["uptime"]["since"].endswith("+00:00"))
        # No volume was configured, so only the one Vela's own data sits on.
        self.assertEqual([disk["label"] for disk in snapshot["disks"]], ["Vela data"])
        disk = snapshot["disks"][0]
        self.assertEqual(disk["path"], str(config.data_dir))
        self.assertTrue(disk["reachable"])
        self.assertEqual(disk["used"], 214 * 1024**3)

    def test_configured_volumes_are_listed_and_an_unreachable_one_says_so(self):
        media = self.root / "media"
        media.mkdir()
        psutil = FakePsutil(unreachable={str(self.root / "gone")})
        store, _ = metrics_for(
            self.root,
            psutil,
            desk={"volumes": [
                {"path": str(media), "label": "Media"},
                {"path": str(self.root / "gone"), "label": "Backup drive"},
            ]},
        )
        with mock.patch("vela.system_metrics._psutil", return_value=psutil):
            disks = store.snapshot()["disks"]
        self.assertEqual([disk["label"] for disk in disks], ["Media", "Backup drive", "Vela data"])
        self.assertTrue(disks[0]["reachable"])
        # A drive that is not plugged in keeps its place on the board.
        self.assertFalse(disks[1]["reachable"])
        self.assertNotIn("percent", disks[1])

    def test_history_records_samples_and_the_api_never_500s_without_psutil(self):
        psutil = FakePsutil(samples=(5.0, 15.0, 25.0))
        store, _ = metrics_for(self.root, psutil)
        with mock.patch("vela.system_metrics._psutil", return_value=psutil):
            store.sample()
            store.sample()
            history = store.history()
        self.assertEqual([point["cpu"] for point in history], [5.0, 15.0])
        self.assertTrue(all(point["t"].endswith("+00:00") for point in history))

        with mock.patch("vela.system_metrics._psutil", return_value=None):
            self.assertIsNone(store.sample())
            snapshot = store.snapshot()
        self.assertEqual(snapshot["available"], False)
        self.assertEqual(snapshot["disks"], [])
        self.assertEqual(snapshot["history"], [])
        self.assertIn("host", snapshot)

    def test_uptime_is_written_the_way_the_widget_reads_it(self):
        self.assertEqual(format_uptime(0), "0m")
        self.assertEqual(format_uptime(59), "0m")
        self.assertEqual(format_uptime(90), "1m")
        self.assertEqual(format_uptime(3 * 86400 + 4 * 3600 + 5 * 60), "3d 4h 5m")
        self.assertEqual(format_uptime(-10), "0m")


class VolumeValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-volumes-")
        self.root = Path(self.temp.name)
        (self.root / "media").mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def test_a_volume_must_name_a_folder_that_exists(self):
        media = str(self.root / "media")
        self.assertEqual(
            validate_volumes([{"path": media, "label": "Media"}]),
            [{"path": str(Path(media)), "label": "Media"}],
        )
        # A missing label falls back to the folder's own name.
        self.assertEqual(validate_volumes([{"path": media}])[0]["label"], "media")
        for bad in (
            "not-a-list",
            [{"path": str(self.root / "missing")}],
            [{"path": str(self.root / "media" / "nope")}],
            [{"path": ""}],
            ["just-a-string"],
            [{"path": media}, {"path": media}],
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_volumes(bad)


class SettingsEndpointTests(unittest.TestCase):
    """The API surface: the metrics endpoint needs a hub session, and a bad
    volume is refused with 422 rather than stored and failing in a widget."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-desk-settings-")
        self.root = Path(self.temp.name)
        self.apps = self.root / "catalog"
        shutil.copytree(ROOT / "tests/fixtures/chat-fixture", self.apps / "chat-fixture")
        manifest = copy.deepcopy(base.FIXTURE)
        (self.apps / "chat-fixture" / "app.json").write_text(json.dumps(manifest))
        self.config = Config(self.root / "data", self.apps, ROOT / "web/dist")
        self.config.ensure_dirs()
        self.client = TestClient(create_app(self.config))
        token = self.client.get("/api/session", headers={"X-Vela-Bootstrap": "1"}).json()["token"]
        self.hub = {"Authorization": "Bearer " + token}

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def test_metrics_need_a_hub_session(self):
        self.assertEqual(self.client.get("/api/system/metrics").status_code, 401)
        response = self.client.get("/api/system/metrics", headers=self.hub)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("available", response.json())

    def test_desk_settings_default_and_reject_an_unknown_volume_path(self):
        settings = self.client.get("/api/settings", headers=self.hub).json()
        self.assertEqual(
            settings["desk"],
            {
                "volumes": [],
                "wallpaper": "choroni",
                "dim": True,
                "labels": True,
                # The weather is off and has no place until the user names one.
                "weather": {
                    "enabled": False,
                    "latitude": None,
                    "longitude": None,
                    "label": "",
                },
            },
        )
        missing = str(self.root / "nowhere")
        response = self.client.patch(
            "/api/settings", headers=self.hub, json={"desk": {"volumes": [{"path": missing}]}}
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("not a folder", response.json()["detail"])
        self.assertEqual(
            self.client.get("/api/settings", headers=self.hub).json()["desk"]["volumes"], []
        )

        media = self.root / "media"
        media.mkdir()
        ok = self.client.patch(
            "/api/settings",
            headers=self.hub,
            json={"desk": {"volumes": [{"path": str(media), "label": "Media"}]}},
        )
        self.assertEqual(ok.status_code, 200, ok.text)
        stored = self.client.get("/api/settings", headers=self.hub).json()["desk"]
        self.assertEqual(stored["volumes"], [{"path": str(media), "label": "Media"}])
        # Patching one desk key leaves the others alone.
        self.assertEqual(stored["wallpaper"], "choroni")



class NetworkTodayTests(unittest.TestCase):
    """Daily network totals: deltas, the file, the rollover and a counter reset."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-net-")
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def stored(self, config, day):
        return json.loads((config.data_dir / "metrics" / f"net-{day}.json").read_text("utf-8"))

    def test_only_the_difference_between_readings_counts_toward_today(self):
        # The counters are cumulative since boot, so the first reading is a
        # baseline rather than a day's worth of traffic.
        psutil = FakePsutil(net=[(1_000, 5_000), (1_500, 9_000), (1_800, 9_400)])
        store, _ = metrics_for(self.root, psutil)
        with mock.patch("vela.system_metrics._psutil", return_value=psutil):
            store.sample()
            self.assertEqual(store.network_today()["total"], 0)
            store.sample()
            store.sample()
        today = store.network_today()
        self.assertEqual(today["bytesSent"], 800)
        self.assertEqual(today["bytesRecv"], 4_400)
        self.assertEqual(today["total"], 5_200)
        self.assertEqual(today["day"], date.today().isoformat())

    def test_the_days_total_is_written_where_a_restart_can_find_it(self):
        psutil = FakePsutil(net=[(0, 0), (400, 600)])
        store, config = metrics_for(self.root, psutil)
        with mock.patch("vela.system_metrics._psutil", return_value=psutil):
            store.sample()
            store.sample()
        # Samples inside the flush interval stay in memory, so ask for the write
        # the way shutdown does.
        with store._lock:
            store._flush_locked()
        self.assertEqual(
            self.stored(config, date.today().isoformat()),
            {"bytes_sent": 400, "bytes_recv": 600},
        )

        # A second run on the same day resumes from that file rather than zero.
        again = SystemMetrics(config, SettingsStore(config.settings_file))
        later = FakePsutil(net=[(9_000, 9_000), (9_100, 9_250)])
        with mock.patch("vela.system_metrics._psutil", return_value=later):
            again.sample()
            again.sample()
        self.assertEqual(again.network_today()["bytesSent"], 500)
        self.assertEqual(again.network_today()["bytesRecv"], 850)

    def test_midnight_starts_a_new_day_and_leaves_the_old_one_written(self):
        psutil = FakePsutil(net=[(0, 0), (700, 300), (900, 400)])
        store, config = metrics_for(self.root, psutil)
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        with mock.patch("vela.system_metrics._psutil", return_value=psutil):
            with mock.patch("vela.system_metrics.date") as clock:
                clock.today.return_value = date.today() - timedelta(days=1)
                store.sample()
                store.sample()
                with store._lock:
                    store._flush_locked()
            # The real clock is back, so the next sample is past midnight.
            store.sample()
        self.assertEqual(self.stored(config, yesterday)["bytes_sent"], 700)
        # Today starts from nothing rather than inheriting yesterday's total.
        self.assertEqual(store.network_today()["total"], 0)

    def test_counters_that_go_backwards_are_not_counted_as_traffic(self):
        # A reboot or an interface reset restarts the counters. What happened in
        # between is unknowable, so it is skipped rather than guessed.
        psutil = FakePsutil(net=[(5_000, 5_000), (5_400, 5_500), (10, 20), (60, 120)])
        store, _ = metrics_for(self.root, psutil)
        with mock.patch("vela.system_metrics._psutil", return_value=psutil):
            for _ in range(4):
                store.sample()
        self.assertEqual(store.network_today()["bytesSent"], 400 + 50)
        self.assertEqual(store.network_today()["bytesRecv"], 500 + 100)

    def test_a_day_older_than_the_window_is_deleted_on_a_flush(self):
        psutil = FakePsutil(net=[(0, 0)])
        store, config = metrics_for(self.root, psutil)
        metrics = config.data_dir / "metrics"
        metrics.mkdir(parents=True, exist_ok=True)
        stale = (date.today() - timedelta(days=NET_KEEP_DAYS + 2)).isoformat()
        (metrics / f"net-{stale}.json").write_text('{"bytes_sent": 1, "bytes_recv": 1}', "utf-8")
        with mock.patch("vela.system_metrics._psutil", return_value=psutil):
            store.sample()
        self.assertFalse((metrics / f"net-{stale}.json").exists())

    def test_a_damaged_day_file_reads_as_nothing_rather_than_failing(self):
        psutil = FakePsutil(net=[(0, 0), (100, 100)])
        store, config = metrics_for(self.root, psutil)
        metrics = config.data_dir / "metrics"
        metrics.mkdir(parents=True, exist_ok=True)
        (metrics / f"net-{date.today().isoformat()}.json").write_text("{not json", "utf-8")
        with mock.patch("vela.system_metrics._psutil", return_value=psutil):
            store.sample()
            store.sample()
        self.assertEqual(store.network_today()["total"], 200)


class ConnectionModeTests(unittest.TestCase):
    """What the desk may claim about how this server can be reached."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-mode-")
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def store(self, *, remote=False, secure=None):
        config = Config(
            self.root / "data", self.root / "catalog", ROOT / "web/dist", remote_access=remote
        )
        config.ensure_dirs()
        return SystemMetrics(config, SettingsStore(config.settings_file), secure=secure)

    def test_loopback_is_local_and_a_wider_bind_is_lan(self):
        with mock.patch.dict("os.environ", {"VELA_HOST": "127.0.0.1"}, clear=False):
            os.environ.pop("VELA_CERT_FILE", None)
            self.assertEqual(self.store().connection_mode(), "local")
        with mock.patch.dict("os.environ", {"VELA_HOST": "0.0.0.0"}, clear=False):
            os.environ.pop("VELA_CERT_FILE", None)
            self.assertEqual(self.store().connection_mode(), "lan")

    def test_a_certificate_makes_it_https_whatever_it_is_bound_to(self):
        with mock.patch.dict(
            "os.environ",
            {"VELA_HOST": "127.0.0.1", "VELA_CERT_FILE": "/tmp/server.pem"},
            clear=False,
        ):
            self.assertEqual(self.store().connection_mode(), "https")

    def test_the_wifi_listener_counts_as_https_too(self):
        with mock.patch.dict("os.environ", {"VELA_HOST": "127.0.0.1"}, clear=False):
            os.environ.pop("VELA_CERT_FILE", None)
            self.assertEqual(self.store(secure=lambda: True).connection_mode(), "https")
            self.assertEqual(self.store(secure=lambda: False).connection_mode(), "local")

    def test_a_secure_check_that_raises_does_not_break_the_snapshot(self):
        def broken():
            raise RuntimeError("no listener")

        with mock.patch.dict("os.environ", {"VELA_HOST": "127.0.0.1"}, clear=False):
            os.environ.pop("VELA_CERT_FILE", None)
            self.assertEqual(self.store(secure=broken).connection_mode(), "local")

if __name__ == "__main__":
    unittest.main()
