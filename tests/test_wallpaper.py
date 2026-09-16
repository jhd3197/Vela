"""The desk's custom wallpaper: accepted types, the size cap, and removal.

Vela stores one image in the user's data directory and serves it back. It is
drawn behind the whole shell, so the checks here are about what may be stored
at all rather than about how it looks. Everything uses disposable data.
"""
import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import test_app_contract as base
from fastapi.testclient import TestClient
from vela.api import create_app
from vela.config import Config
from vela.wallpaper import MAX_WALLPAPER_BYTES

ROOT = base.ROOT

# The smallest real files of each accepted type, plus the byte prefixes that
# identify them. The content past the signature never reaches a decoder here.
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
WEBP = b"RIFF" + b"\x00" * 64


class WallpaperApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-wallpaper-")
        self.root = Path(self.temp.name)
        self.apps = self.root / "catalog"
        shutil.copytree(ROOT / "tests/fixtures/chat-fixture", self.apps / "chat-fixture")
        (self.apps / "chat-fixture" / "app.json").write_text(json.dumps(copy.deepcopy(base.FIXTURE)))
        self.config = Config(self.root / "data", self.apps, ROOT / "web/dist")
        self.config.ensure_dirs()
        self.client = TestClient(create_app(self.config))
        token = self.client.get("/api/session", headers={"X-Vela-Bootstrap": "1"}).json()["token"]
        self.hub = {"Authorization": "Bearer " + token}

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def put(self, content, content_type):
        return self.client.put(
            "/api/wallpaper", headers={**self.hub, "Content-Type": content_type}, content=content
        )

    def test_a_fresh_desk_draws_the_default_painted_wallpaper(self):
        # The desk must have something to draw before the user chooses anything,
        # and it is one of the painted set Vela ships rather than stock imagery.
        desk = self.client.get("/api/settings", headers=self.hub).json()["desk"]
        self.assertEqual(desk["wallpaper"], "choroni")
        bundled = ROOT / "web/public/wallpapers"
        self.assertTrue((bundled / "choroni.jpg").is_file())
        self.assertTrue((bundled / "thumbs/choroni.jpg").is_file())
        # The unlicensed stock photograph is gone, not merely unreferenced.
        self.assertFalse((bundled / "lake.jpg").exists())

    def test_every_painted_wallpaper_ships_with_a_thumbnail(self):
        painted = [
            "choroni",
            "paramo",
            "medanos",
            "chiguire",
            "pueblo",
            "avila",
            "castillo",
            "canaima",
        ]
        bundled = ROOT / "web/public/wallpapers"
        for name in painted:
            with self.subTest(wallpaper=name):
                self.assertTrue((bundled / f"{name}.jpg").is_file())
                self.assertTrue((bundled / "thumbs" / f"{name}.jpg").is_file())

    def test_a_desk_still_set_to_the_retired_photograph_draws_the_default(self):
        # The stock photograph was removed, not re-licensed. A settings file
        # written before that must still name a picture the desk can draw.
        self.client.patch("/api/settings", headers=self.hub, json={"desk": {"wallpaper": "night"}})
        stored = json.loads((self.config.data_dir / "settings.json").read_text(encoding="utf-8"))
        stored["desk"]["wallpaper"] = "lake"
        (self.config.data_dir / "settings.json").write_text(json.dumps(stored), encoding="utf-8")
        desk = self.client.get("/api/settings", headers=self.hub).json()["desk"]
        self.assertEqual(desk["wallpaper"], "choroni")

    def test_no_wallpaper_is_a_404_not_an_error(self):
        self.assertEqual(self.client.get("/api/wallpaper", headers=self.hub).status_code, 404)

    def test_a_wallpaper_needs_a_hub_session(self):
        self.assertEqual(self.client.get("/api/wallpaper").status_code, 401)
        self.assertEqual(
            self.client.put("/api/wallpaper", content=JPEG).status_code, 401
        )
        self.assertEqual(self.client.delete("/api/wallpaper").status_code, 401)

    def test_an_accepted_image_is_stored_served_and_recorded_in_settings(self):
        for content, content_type in ((JPEG, "image/jpeg"), (PNG, "image/png"), (WEBP, "image/webp")):
            with self.subTest(content_type=content_type):
                saved = self.put(content, content_type)
                self.assertEqual(saved.status_code, 200, saved.text)
                self.assertEqual(saved.json()["bytes"], len(content))
                served = self.client.get("/api/wallpaper", headers=self.hub)
                self.assertEqual(served.status_code, 200)
                self.assertEqual(served.content, content)
                self.assertEqual(served.headers["content-type"], content_type)
                # Replacing the format must not leave the previous file behind.
                stored = sorted(p.name for p in self.config.data_dir.glob("wallpaper.*"))
                self.assertEqual(len(stored), 1, stored)
        # Choosing an image is what makes the setting say `custom`.
        desk = self.client.get("/api/settings", headers=self.hub).json()["desk"]
        self.assertEqual(desk["wallpaper"], "custom")

    def test_the_type_must_be_supported_and_honest(self):
        refused = self.put(b"<svg/>", "image/svg+xml")
        self.assertEqual(refused.status_code, 415)
        self.assertIn("JPEG, PNG or WebP", refused.json()["detail"])

        # A PNG announced as a JPEG is not stored: the header is checked, not
        # only the claim.
        lying = self.put(PNG, "image/jpeg")
        self.assertEqual(lying.status_code, 422)
        self.assertIn("not the image type it claims", lying.json()["detail"])

        self.assertEqual(self.put(b"", "image/png").status_code, 422)
        self.assertEqual(self.client.get("/api/wallpaper", headers=self.hub).status_code, 404)

    def test_an_image_over_eight_megabytes_is_refused(self):
        oversized = JPEG + b"\x00" * (MAX_WALLPAPER_BYTES + 1 - len(JPEG))
        self.assertGreater(len(oversized), MAX_WALLPAPER_BYTES)
        response = self.put(oversized, "image/jpeg")
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.client.get("/api/wallpaper", headers=self.hub).status_code, 404)

    def test_removing_it_falls_back_to_a_bundled_wallpaper(self):
        self.assertEqual(self.put(JPEG, "image/jpeg").status_code, 200)
        removed = self.client.delete("/api/wallpaper", headers=self.hub)
        self.assertEqual(removed.status_code, 200)
        self.assertTrue(removed.json()["removed"])
        self.assertEqual(self.client.get("/api/wallpaper", headers=self.hub).status_code, 404)
        # The desk must still have something to draw.
        self.assertEqual(
            self.client.get("/api/settings", headers=self.hub).json()["desk"]["wallpaper"],
            "choroni",
        )
        # Removing nothing is not an error.
        again = self.client.delete("/api/wallpaper", headers=self.hub)
        self.assertEqual(again.status_code, 200)
        self.assertFalse(again.json()["removed"])

    def test_choosing_a_bundled_wallpaper_does_not_delete_the_uploaded_one(self):
        self.assertEqual(self.put(JPEG, "image/jpeg").status_code, 200)
        self.client.patch("/api/settings", headers=self.hub, json={"desk": {"wallpaper": "night"}})
        self.assertEqual(
            self.client.get("/api/settings", headers=self.hub).json()["desk"]["wallpaper"], "night"
        )
        # Switching back finds the same image rather than asking for it again.
        self.assertEqual(self.client.get("/api/wallpaper", headers=self.hub).status_code, 200)


if __name__ == "__main__":
    unittest.main()
