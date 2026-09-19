"""The managed-app contract: what a package may declare, and what is refused.

These tests are about the boundary between a package author and this host. They
do not start anything. The expensive ones -- installing, running, proxying --
live in the other managed suites; what is proved here is that a package which
should never get that far does not.

Run with: python -m unittest discover -s tests. Uses disposable data only.
"""

import copy
import hashlib
import json
import shutil
import stat
import tarfile
import unittest
import zipfile
from pathlib import Path

import managed_support as support
from managed_support import APP_ID, ManagedTestCase

from vela.errors_http import Conflict, NotFound, TooLarge
from vela.managed import packages
from vela.managed.contract import (
    HOST_MANAGED_SERVICE_REVISION,
    ManagedManifestError,
    describe_target,
    host_target,
    load_managed_manifest,
    validate_managed_manifest,
)
from vela.managed.packages import ArtifactError
from vela.managed.store import AppPaths, safe_app_id
from vela.manifest import ManifestError, validate_manifest

ROOT = Path(__file__).resolve().parent.parent
CONTRACTS = ROOT.parent / "vela-contracts/tests/fixtures/manifest-v3"


def base_manifest() -> dict:
    """A complete v3 manifest, as the canonical positive fixture writes it."""
    return json.loads(
        (CONTRACTS / "valid/bundled-archive.json").read_text(encoding="utf-8")
    )


class SchemaTests(unittest.TestCase):
    """The vendored schema, and the rules that live beside it rather than in it."""

    def validate(self, data, folder="notebook"):
        return validate_managed_manifest(data, folder=folder, path=Path(folder))

    def test_a_complete_manifest_validates(self):
        manifest = self.validate(base_manifest())
        self.assertEqual(manifest.id, "notebook")
        self.assertEqual(manifest.schema_version, 3)
        self.assertEqual(
            [artifact.label for artifact in manifest.artifacts],
            ["windows x64", "linux arm64"],
        )
        self.assertEqual(manifest.data["directory"], "store")
        self.assertEqual(manifest.endpoint["basePath"], "/")

    def test_defaults_are_filled_where_the_schema_allows_them_to_be_absent(self):
        data = base_manifest()
        del data["service"]["lifetime"]
        del data["service"]["readiness"]["expectStatus"]
        del data["view"]["chrome"]
        manifest = self.validate(data)
        self.assertEqual(manifest.readiness["expectStatus"], [200])
        self.assertEqual(manifest.lifetime["startWithVela"], False)
        self.assertEqual(manifest.lifetime["restart"]["maxRetries"], 3)
        self.assertEqual(manifest.view["chrome"], "compact")
        self.assertEqual(manifest.view["embedding"], "auto")

    def test_the_vendored_schema_is_the_reviewed_sibling_snapshot(self):
        """`scripts/sync-runtime-assets.py` is the only way this file changes."""
        versions = json.loads(
            (ROOT / "vela/assets/versions.json").read_text(encoding="utf-8")
        )
        record = versions["manifest-v3.schema.json"]
        self.assertEqual(record["repository"], "vela-contracts")
        vendored = (ROOT / "vela/assets/manifest-v3.schema.json").read_bytes()
        self.assertEqual(hashlib.sha256(vendored).hexdigest(), record["sha256"])

    def test_every_canonical_refusal_is_refused_here_too(self):
        """The negative fixtures in `vela-contracts` are this host's cases too.

        A schema that drifts from the one the packaging tools validate against
        is worse than no schema: a package would pass its author's check and
        fail on the machine that matters.
        """
        found = sorted((CONTRACTS / "invalid").glob("*.json"))
        self.assertGreaterEqual(len(found), 20)
        for path in found:
            with self.subTest(case=path.stem):
                data = json.loads(path.read_text(encoding="utf-8"))
                with self.assertRaises(ManagedManifestError):
                    self.validate(data, folder=data.get("id", "notebook"))

    def test_a_v3_manifest_is_not_an_app_manifest_and_never_becomes_one(self):
        """The v1/v2 loader refuses it by version, which is the old-host answer."""
        with self.assertRaises(ManifestError) as caught:
            validate_manifest(base_manifest(), folder="notebook", path=Path("notebook"))
        self.assertIn("unsupported manifest schema version", str(caught.exception))

    def test_a_package_for_a_different_host_revision_is_refused(self):
        data = base_manifest()
        data["compatibility"]["managedService"] = HOST_MANAGED_SERVICE_REVISION + 1
        with self.assertRaises(ManagedManifestError) as caught:
            self.validate(data)
        self.assertIn("this Vela implements", str(caught.exception))

    def test_two_artifacts_cannot_claim_the_same_target(self):
        data = base_manifest()
        second = copy.deepcopy(data["service"]["artifacts"][0])
        second["file"] = "artifacts/other.zip"
        data["service"]["artifacts"].append(second)
        with self.assertRaises(ManagedManifestError) as caught:
            self.validate(data)
        self.assertIn("exactly one archive", str(caught.exception))

    def test_an_argument_cannot_ask_for_a_value_the_host_cannot_fill(self):
        data = base_manifest()
        data["service"]["command"]["args"].append("--secret={velaToken}")
        with self.assertRaises(ManagedManifestError) as caught:
            self.validate(data)
        self.assertIn("{velaToken}", str(caught.exception))

    def test_an_environment_value_cannot_either(self):
        data = base_manifest()
        data["service"]["environment"] = {"LEAK": "{hubToken}"}
        with self.assertRaises(ManagedManifestError):
            self.validate(data)

    def test_paths_are_checked_against_their_parts_not_only_their_pattern(self):
        for field, value in (
            ("executable", "sub/../../escape"),
            ("file", "a/./../../b.zip"),
        ):
            with self.subTest(field=field):
                data = base_manifest()
                data["service"]["artifacts"][0][field] = value
                with self.assertRaises(ManagedManifestError):
                    self.validate(data)

    def test_artifact_selection_needs_both_operating_system_and_architecture(self):
        manifest = self.validate(base_manifest())
        self.assertIsNotNone(manifest.artifact_for(("windows", "x64")))
        # The same OS, a different CPU: no artifact, and therefore no execution.
        self.assertIsNone(manifest.artifact_for(("windows", "arm64")))
        self.assertIsNone(manifest.artifact_for(("linux", "x64")))
        self.assertFalse(manifest.supports(("linux", "x64")))

    def test_the_review_states_native_trust_and_grants_nothing(self):
        review = self.validate(base_manifest()).review(("windows", "x64"))
        self.assertEqual(review["trust"]["execution"], "trusted-native")
        self.assertIn("your own permissions", review["trust"]["summary"])
        self.assertEqual(review["trust"]["grants"], [])
        self.assertEqual(
            review["integration"], {"sdk": False, "agent": False, "storage": False}
        )

    def test_this_computer_can_name_itself(self):
        system, arch = host_target()
        self.assertIn(system, ("windows", "macos", "linux"))
        self.assertIn(arch, ("x64", "arm64", "armv7"))
        self.assertEqual(describe_target((system, arch)), f"{system} {arch}")
        self.assertEqual(describe_target((None, None)), "unknown unknown")


class ArchiveSafetyTests(unittest.TestCase):
    """Nothing an archive says about where its files go is believed."""

    def setUp(self):
        import tempfile

        self.temp = tempfile.TemporaryDirectory(prefix="vela-archive-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def zip_with(self, entries, *, name="bad.zip"):
        archive = self.root / name
        with zipfile.ZipFile(archive, "w") as zipped:
            for member, data, mode in entries:
                info = zipfile.ZipInfo(member)
                info.external_attr = mode << 16
                zipped.writestr(info, data)
        return archive

    def extract(self, archive, *, format="zip", limit=None):
        target = self.root / "out"
        shutil.rmtree(target, ignore_errors=True)
        packages.extract_artifact(archive, format=format, target=target, limit=limit)
        return target

    def test_an_ordinary_archive_expands(self):
        archive = self.zip_with([
            ("bin/app", b"#!/bin/sh\n", stat.S_IFREG | 0o755),
            ("README", b"hello", stat.S_IFREG | 0o644),
        ])
        target = self.extract(archive)
        self.assertEqual((target / "README").read_bytes(), b"hello")

    def test_a_member_cannot_climb_out_of_the_destination(self):
        for member in ("../escape.txt", "a/../../escape.txt", "/etc/passwd"):
            with self.subTest(member=member):
                archive = self.zip_with([(member, b"x", stat.S_IFREG | 0o644)])
                with self.assertRaises(ArtifactError):
                    self.extract(archive)
                self.assertFalse((self.root / "escape.txt").exists())

    def test_a_windows_style_path_is_refused_on_every_platform(self):
        archive = self.zip_with([("..\\escape.txt", b"x", stat.S_IFREG | 0o644)])
        with self.assertRaises(ArtifactError):
            self.extract(archive)

    def test_a_reserved_windows_device_name_is_refused(self):
        archive = self.zip_with([("COM1", b"x", stat.S_IFREG | 0o644)])
        with self.assertRaises(ArtifactError):
            self.extract(archive)

    def test_a_symlink_member_is_refused_rather_than_followed(self):
        archive = self.zip_with([("link", b"/etc/passwd", stat.S_IFLNK | 0o777)])
        with self.assertRaises(ArtifactError) as caught:
            self.extract(archive)
        self.assertIn("link", str(caught.exception))

    def test_a_tar_symlink_is_refused(self):
        archive = self.root / "bad.tar.gz"
        with tarfile.open(archive, "w:gz") as tarred:
            info = tarfile.TarInfo("link")
            info.type = tarfile.SYMTYPE
            info.linkname = "../../etc/passwd"
            tarred.addfile(info)
        with self.assertRaises(ArtifactError):
            self.extract(archive, format="tar.gz")

    def test_a_tar_member_cannot_climb_out_either(self):
        archive = self.root / "climb.tar.gz"
        with tarfile.open(archive, "w:gz") as tarred:
            info = tarfile.TarInfo("../escape.txt")
            info.size = 1
            tarred.addfile(info, __import__("io").BytesIO(b"x"))
        with self.assertRaises(ArtifactError):
            self.extract(archive, format="tar.gz")

    def test_an_archive_that_expands_past_its_limit_is_refused(self):
        archive = self.zip_with([("big", b"0" * 200_000, stat.S_IFREG | 0o644)])
        with self.assertRaises(TooLarge):
            self.extract(archive, limit=1024)

    def test_a_declared_executable_must_be_inside_the_extracted_tree(self):
        archive = self.zip_with([("bin/app", b"x", stat.S_IFREG | 0o755)])
        target = self.extract(archive)
        self.assertTrue(packages.executable_in(target, "bin/app").is_file())
        with self.assertRaises(ArtifactError):
            packages.executable_in(target, "../outside")
        with self.assertRaises(Conflict):
            packages.executable_in(target, "bin/missing")

    def test_a_digest_mismatch_refuses_before_anything_is_expanded(self):
        archive = self.zip_with([("bin/app", b"x", stat.S_IFREG | 0o644)])
        with self.assertRaises(ArtifactError) as caught:
            packages.verify_artifact(archive, sha256="0" * 64, size=archive.stat().st_size)
        self.assertIn("Nothing was installed", str(caught.exception))

    def test_a_size_mismatch_refuses_too(self):
        archive = self.zip_with([("bin/app", b"x", stat.S_IFREG | 0o644)])
        digest = packages.digest_file(archive)
        with self.assertRaises(ArtifactError) as caught:
            packages.verify_artifact(archive, sha256=digest, size=1)
        self.assertIn("the package declared 1", str(caught.exception))

    def test_a_tree_digest_notices_any_edited_byte(self):
        tree = self.root / "tree"
        (tree / "nested").mkdir(parents=True)
        (tree / "nested/one.txt").write_text("one")
        (tree / "two.txt").write_text("two")
        before = packages.digest_tree(tree)
        self.assertEqual(before, packages.digest_tree(tree))
        (tree / "two.txt").write_text("two ")
        self.assertNotEqual(before, packages.digest_tree(tree))

    def test_a_download_must_be_https(self):
        with self.assertRaises(ArtifactError):
            packages.download_artifact(
                "http://example.invalid/a.zip", sha256="0" * 64, size=1,
                destination=self.root / "a.zip",
            )


class StorageBoundaryTests(unittest.TestCase):
    """An app's directories are built from its identity, and checked anyway."""

    def test_an_app_id_that_could_address_a_path_is_refused(self):
        for value in ("../escape", "a/b", "A", "", "x" * 60, None, 3):
            with self.subTest(value=value), self.assertRaises(NotFound):
                safe_app_id(value)
        self.assertEqual(safe_app_id("fixture-notes"), "fixture-notes")

    def test_a_data_directory_cannot_leave_the_apps_own_storage(self):
        paths = AppPaths(Path("/tmp/managed/notes").resolve())
        inside = paths.data_directory("store")
        self.assertTrue(inside.is_relative_to(paths.data.resolve()))
        for relative in ("../../elsewhere", "..", "a/../../b"):
            with self.subTest(relative=relative), self.assertRaises(Conflict):
                paths.data_directory(relative)


class PackageReviewTests(ManagedTestCase):
    """Reviewing a real package, with a real archive, before anything runs."""

    def test_a_review_describes_the_bytes_that_would_run(self):
        review = self.review()
        self.assertEqual(review["id"], APP_ID)
        self.assertEqual(review["operation"], "install")
        self.assertTrue(review["supported"])
        self.assertEqual(review["trust"]["execution"], "trusted-native")
        self.assertEqual(review["artifact"]["os"], host_target()[0])
        self.assertEqual(review["artifact"]["arch"], host_target()[1])
        self.assertEqual(review["artifact"]["delivery"], "bundled")
        self.assertRegex(review["artifactDigest"], r"^[a-f0-9]{64}$")
        self.assertIn("MIT License", review["licenseText"])
        self.assertEqual(review["integration"]["sdk"], False)
        # Nothing has been installed by looking.
        self.assertEqual(self.client.get("/api/managed", headers=self.hub).json()["apps"], [])

    def test_a_review_names_this_computers_address_for_the_app(self):
        review = self.review()
        addresses = {entry["scope"]: entry for entry in review["addresses"]}
        self.assertEqual(addresses["this computer"]["url"], f"http://{APP_ID}.apps.localhost")
        other = addresses["other devices on this network"]
        self.assertIn("apps.vela.invalid", other["url"])
        self.assertIn("DNS entry", other["requires"])

    def test_a_package_with_no_build_for_this_computer_refuses_before_download(self):
        package = self.build_package()
        data = self.read_manifest(package)
        data["service"]["artifacts"][0]["os"] = "macos" if host_target()[0] != "macos" else "linux"
        self.write_manifest(package, data)
        response = self.client.post(
            "/api/managed/review", headers=self.hub, json={"folder": str(package)}
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["code"], "managed.unsupported_target")

    def test_a_tampered_archive_refuses_with_its_reason(self):
        package = self.build_package()
        archive = package / "artifacts/fixture-release.zip"
        archive.write_bytes(archive.read_bytes() + b"\0")
        response = self.client.post(
            "/api/managed/review", headers=self.hub, json={"folder": str(package)}
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("Nothing was installed", response.json()["detail"])

    def test_a_package_that_needs_websockets_is_refused_and_says_why(self):
        response = self.client.post(
            "/api/managed/review", headers=self.hub,
            json={"folder": str(self.build_package(websocket="required"))},
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["code"], "managed.websocket_required")
        self.assertIn("WebSocket", response.json()["detail"])

    def test_an_uploaded_package_archive_reviews_the_same_way(self):
        archive = self.build_archive()
        response = self.client.post(
            "/api/managed/review/upload", headers=self.hub, content=archive.read_bytes()
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["id"], APP_ID)
        self.assertEqual(response.json()["source"]["kind"], "archive")

    def test_a_review_can_be_cancelled_and_leaves_nothing_staged(self):
        review = self.review()
        staging = self.config.data_dir / "staging"
        self.assertTrue(any(staging.iterdir()))
        response = self.client.delete(
            f"/api/managed/review/{review['review']}", headers=self.hub
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(list(staging.iterdir()), [])

    def test_vela_data_cannot_be_imported_as_an_app(self):
        response = self.client.post(
            "/api/managed/review", headers=self.hub,
            json={"folder": str(self.config.data_dir)},
        )
        self.assertEqual(response.status_code, 422, response.text)

    def test_managing_an_app_needs_a_vela_session(self):
        self.assertEqual(self.client.get("/api/managed").status_code, 401)
        for path in (
            "/api/managed/review",
            f"/api/managed/{APP_ID}/start",
            f"/api/managed/{APP_ID}/launch",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.post(path, json={}).status_code, 401)


if __name__ == "__main__":
    unittest.main()
