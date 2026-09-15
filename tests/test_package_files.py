"""Run with: python -m unittest discover -s tests. Uses disposable data only."""
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vela import package_files
from vela.package_files import replace_dir


class ReplaceDirTests(unittest.TestCase):
    """Installing a package must survive a transient Windows file lock."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-replace-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "staged"
        self.source.mkdir()
        (self.source / "app.json").write_text("{}", encoding="utf-8")
        self.target = self.root / "installed"

    def test_moves_the_directory(self):
        replace_dir(self.source, self.target)
        self.assertTrue((self.target / "app.json").is_file())
        self.assertFalse(self.source.exists())

    def test_retries_a_transient_permission_error(self):
        real = Path.rename
        attempts = []

        def flaky(self, target):
            attempts.append(target)
            if len(attempts) < 3:
                raise PermissionError(5, "Access is denied")
            return real(self, target)

        with unittest.mock.patch.object(Path, "rename", flaky):
            replace_dir(self.source, self.target)
        self.assertEqual(len(attempts), 3)
        self.assertTrue((self.target / "app.json").is_file())

    def test_gives_up_on_a_persistent_permission_error(self):
        def blocked(self, target):
            raise PermissionError(5, "Access is denied")

        with unittest.mock.patch.object(Path, "rename", blocked):
            with unittest.mock.patch.object(package_files, "RENAME_RETRY_SECONDS", 0.05):
                with self.assertRaises(PermissionError):
                    replace_dir(self.source, self.target)
        self.assertTrue(self.source.exists())


if __name__ == "__main__":
    unittest.main()
