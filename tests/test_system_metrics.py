"""The desk's host metrics: the snapshot shape, the psutil-less fallback and
volume validation.

psutil is stubbed everywhere so the assertions are about Vela's own behaviour
rather than about this machine's CPU, and so the suite reports the same thing
on a runner without psutil installed. Everything uses disposable data.
"""
import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import test_app_contract as base
from fastapi.testclient import TestClient
from vela.api import create_app
from vela.config import Config
from vela.settings import SettingsStore
from vela.system_metrics import SystemMetrics, format_uptime, validate_volumes

ROOT = base.ROOT


class FakePsutil:
    """Just the surface `system_metrics` uses, with readings we control."""

    def __init__(self, samples=(11.0, 22.0, 33.0), usage=None, unreachable=()):
        self._samples = list(samples)
        self._taken = 0
        self._usage = usage
        self._unreachable = set(unreachable)

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
            settings["desk"], {"volumes": [], "wallpaper": "choroni", "dim": True, "labels": True}
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


if __name__ == "__main__":
    unittest.main()
