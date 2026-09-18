"""The bundled wallpapers: pinned download, local derivation, skip-when-present.

The painted set publishes as one 4x master per wallpaper; the first start
downloads them and derives the desk picture and picker thumbnail on the
machine they will run on. Everything here uses disposable synthetic art and a
stand-in download transport — the real masters never move in a test.
"""
import hashlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import httpx
import test_app_contract as base
from fastapi.testclient import TestClient
from PIL import Image

from vela import bundled_wallpapers
from vela.api import create_app
from vela.config import Config

ROOT = base.ROOT


def fake_master(seed: int) -> bytes:
    image = Image.new("RGB", (320, 180), (seed, 128, 255 - seed))
    out = io.BytesIO()
    image.save(out, "JPEG")
    return out.getvalue()


def fake_manifest(files: dict[str, bytes]) -> dict:
    return {
        "version": 1,
        "base_url": "https://example.test/masters",
        "wallpapers": [
            {
                "id": name,
                "file": f"wallpaper-{name}-4x.jpg",
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
            }
            for name, data in files.items()
        ],
    }


class CountingTransport(httpx.MockTransport):
    def __init__(self, files: dict[str, bytes]):
        self.requests: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            filename = request.url.path.rsplit("/", 1)[-1]
            name = filename.removeprefix("wallpaper-").removesuffix("-4x.jpg")
            self.requests.append(filename)
            if name in files:
                return httpx.Response(200, content=files[name])
            return httpx.Response(404)

        super().__init__(handler)


class BundledWallpapersTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-bundled-wallpapers-")
        self.data_dir = Path(self.temp.name) / "data"
        self.files = {"andes": fake_master(17), "costa": fake_master(90)}
        self.transport = CountingTransport(self.files)
        self.client = httpx.Client(transport=self.transport)
        self.manifest = fake_manifest(self.files)

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def ensure(self, **kwargs):
        return bundled_wallpapers.ensure(
            self.data_dir, manifest=self.manifest, client=self.client, **kwargs
        )

    def test_first_start_downloads_and_derives_every_size(self):
        result = self.ensure()
        self.assertEqual(sorted(result["downloaded"]), ["andes", "costa"])
        self.assertEqual(sorted(result["derived"]), ["andes", "costa"])
        self.assertEqual(result["failed"], {})
        root = self.data_dir / "wallpapers"
        for name in ("andes", "costa"):
            with self.subTest(wallpaper=name):
                with Image.open(root / f"{name}.jpg") as display:
                    self.assertEqual(display.size, bundled_wallpapers.DISPLAY_SIZE)
                with Image.open(root / "thumbs" / f"{name}.jpg") as thumb:
                    self.assertEqual(thumb.size, bundled_wallpapers.THUMB_SIZE)
        # The master is kept so a lost derived file never needs the network.
        self.assertEqual(
            (root / "masters" / "wallpaper-andes-4x.jpg").read_bytes(),
            self.files["andes"],
        )

    def test_a_start_with_everything_in_place_touches_nothing(self):
        self.ensure()
        self.transport.requests.clear()
        result = self.ensure()
        self.assertEqual(result, {"downloaded": [], "derived": [], "failed": {}})
        self.assertEqual(self.transport.requests, [])

    def test_a_kept_master_rederives_without_a_download(self):
        self.ensure()
        root = self.data_dir / "wallpapers"
        (root / "thumbs" / "andes.jpg").unlink()
        self.transport.requests.clear()
        result = self.ensure()
        self.assertEqual(result["derived"], ["andes"])
        self.assertEqual(self.transport.requests, [])
        self.assertTrue((root / "thumbs" / "andes.jpg").is_file())

    def test_a_download_that_misses_its_pin_is_refused_not_kept(self):
        self.manifest["wallpapers"][0]["sha256"] = "0" * 64
        result = self.ensure()
        self.assertEqual(list(result["failed"]), ["andes"])
        self.assertEqual(result["derived"], ["costa"])
        root = self.data_dir / "wallpapers"
        self.assertFalse((root / "masters" / "wallpaper-andes-4x.jpg").exists())
        self.assertFalse((root / "masters" / "wallpaper-andes-4x.part").exists())
        self.assertFalse((root / "andes.jpg").exists())

    def test_an_unreachable_master_leaves_the_rest_and_the_server_alone(self):
        del self.files["andes"]  # the release 404s it
        result = self.ensure()
        self.assertEqual(list(result["failed"]), ["andes"])
        self.assertEqual(result["derived"], ["costa"])


class BundledWallpaperRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-wallpaper-route-")
        self.root = Path(self.temp.name)
        self.apps = self.root / "catalog"
        shutil.copytree(ROOT / "tests/fixtures/chat-fixture", self.apps / "chat-fixture")
        (self.apps / "chat-fixture" / "app.json").write_text(json.dumps(base.FIXTURE))
        self.config = Config(self.root / "data", self.apps, ROOT / "web/dist")
        self.config.ensure_dirs()
        self.client = TestClient(create_app(self.config))

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def test_wallpapers_are_served_from_the_data_directory(self):
        andes, costa = fake_master(17), fake_master(90)
        derived = self.config.data_dir / "wallpapers"
        (derived / "thumbs").mkdir(parents=True)
        (derived / "andes.jpg").write_bytes(andes)
        (derived / "thumbs" / "andes.jpg").write_bytes(costa)
        self.assertEqual(self.client.get("/wallpapers/andes.jpg").content, andes)
        self.assertEqual(self.client.get("/wallpapers/thumbs/andes.jpg").content, costa)

    def test_an_unknown_wallpaper_is_a_404_not_the_dashboard(self):
        self.assertEqual(self.client.get("/wallpapers/nope.jpg").status_code, 404)

    def test_the_route_cannot_escape_the_wallpapers_directory(self):
        (self.config.data_dir / "settings.json").write_text("{}")
        response = self.client.get("/wallpapers/%2E%2E/settings.json")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
