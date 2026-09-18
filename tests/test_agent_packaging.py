"""What a Vela download carries for agent desktops, and what it refuses to do.

Packaging is where a feature stops being something that works on the machine it
was written on. Three things have to be true of a download, and none of them is
visible from a passing test suite in a checkout:

**The worker ships, and its provenance ships with it.** A download that carries
the Python half and not the Node half fails at the moment somebody first tries
to use the feature, with a message about a missing file.

**A mismatch is refused before anything starts.** The two halves speak a
versioned protocol. If they are from different builds, the failure reads like a
bug in whatever the agent was doing rather than like a broken download, so it is
turned into a sentence about reinstalling instead.

**Nothing downloads a browser during a task.** The build records which revision
it needs and says so. The one thing a packaged Vela must never do is fetch
"whatever Chromium is current" while somebody is waiting for a task.

The build itself is not run here — it takes minutes and needs PyInstaller — so
what is checked is the build script's own account of what it includes, and the
runtime's behaviour against a provenance record that is manipulated on purpose.
"""

import ast
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from vela.config import REPO_ROOT
from vela.desktops import runtime

BUILD = REPO_ROOT / "scripts/build-server.py"
WORKER = REPO_ROOT / "scripts/browser-worker"


class BuildScriptTests(unittest.TestCase):
    """What the build script says it puts in a download."""

    @classmethod
    def setUpClass(cls):
        cls.source = BUILD.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_the_build_script_still_parses(self):
        self.assertTrue(self.tree.body)

    def test_the_worker_is_added_to_the_bundle(self):
        self.assertIn("scripts/browser-worker", self.source)
        functions = {
            node.name for node in ast.walk(self.tree) if isinstance(node, ast.FunctionDef)
        }
        self.assertIn("browser_assets", functions)
        self.assertIn("browser_notices", functions)

    def test_a_download_without_the_runtime_says_so_rather_than_shipping_quietly(self):
        # The same shape as the automation runtime's note, so a maintainer
        # reading the build output learns one convention rather than two.
        self.assertIn("cannot run agent desktops", self.source)
        self.assertIn("setup-browser-worker.py", self.source)

    def test_the_browser_build_is_deliberately_not_in_the_download(self):
        # Stated in the script, because the alternative — somebody adding it
        # later without noticing why it was left out — is how a download
        # quietly becomes several hundred megabytes larger.
        self.assertIn("browser build is fetched on the computer that runs Vela", self.source)

    def test_the_licence_notices_travel_with_what_they_cover(self):
        self.assertIn("playwright-core", self.source)
        self.assertIn("Apache-2.0", self.source)
        self.assertIn("Vela source remains MIT licensed", self.source)


class ProvenanceTests(unittest.TestCase):
    """What the runtime does with the record the build left it."""

    def setUp(self):
        self.path = WORKER / "provenance.json"
        self.original = self.path.read_text(encoding="utf-8") if self.path.is_file() else None
        if self.original is None:
            self.skipTest("the browser worker has not been set up on this machine")
        self.addCleanup(lambda: self.path.write_text(self.original, encoding="utf-8"))

    def record(self, **changes):
        document = json.loads(self.original)
        document.update(changes)
        self.path.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")
        return document

    def test_the_digest_is_over_the_files_the_worker_actually_runs(self):
        computed = hashlib.sha256()
        for path in sorted((WORKER / "src").rglob("*.mjs")):
            computed.update(path.relative_to(WORKER).as_posix().encode("utf-8"))
            computed.update(path.read_bytes())
        self.assertEqual(runtime.source_digest(), computed.hexdigest())

    def test_a_worker_that_does_not_match_its_record_is_refused_with_a_sentence(self):
        self.record(sources={"algorithm": "sha256", "digest": "f" * 64})
        state = runtime.availability()
        self.assertFalse(state["available"])
        self.assertIn("does not match the rest of this Vela", state["detail"])
        # Actionable, not a diagnosis: the person reading this has a download,
        # not a debugger.
        self.assertIn("Reinstall", state["detail"])

    def test_a_record_from_before_this_check_existed_still_works(self):
        # Refusing to start over a field an older install never wrote would
        # break an upgrade for no safety gained; every other check still runs.
        document = json.loads(self.original)
        document.pop("sources", None)
        self.path.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")
        self.assertTrue(runtime.availability()["available"])

    def test_a_missing_browser_says_which_command_fetches_it(self):
        self.record(browser={"name": "chromium", "executable": None, "present": False})
        state = runtime.availability()
        self.assertFalse(state["available"])
        self.assertIn("setup-browser-worker.py", state["detail"])

    def test_the_record_names_the_version_and_the_platform_it_was_built_on(self):
        document = json.loads(self.original)
        self.assertTrue(document["installed"]["playwright-core"])
        self.assertTrue(document["platform"]["system"])
        self.assertTrue(document["platform"]["machine"])
        self.assertEqual(document["browser"]["name"], "chromium")


class WorkerContentsTests(unittest.TestCase):
    """The files a download has to carry for the worker to be a worker."""

    def test_every_module_the_worker_imports_is_beside_it(self):
        sources = sorted((WORKER / "src").glob("*.mjs"))
        self.assertTrue(sources, "the worker has no sources")
        names = {path.name for path in sources}
        for path in sources:
            for line in path.read_text(encoding="utf-8").splitlines():
                if "from './" not in line:
                    continue
                imported = line.split("from './")[1].split("'")[0]
                self.assertIn(imported, names, f"{path.name} imports {imported}")

    def test_the_worker_can_be_copied_without_its_installed_packages(self):
        # What the build adds is the directory; this is a cheap check that the
        # directory is self-contained enough to be copied at all.
        with tempfile.TemporaryDirectory(prefix="vela-worker-copy-") as temporary:
            target = Path(temporary) / "browser-worker"
            shutil.copytree(
                WORKER, target, ignore=shutil.ignore_patterns("node_modules", "*.log")
            )
            self.assertTrue((target / "src/index.mjs").is_file())
            self.assertTrue((target / "package.json").is_file())


if __name__ == "__main__":
    unittest.main()
