"""Every script CI runs must load the way CI runs it.

`tests/test_release_automation.py` reaches `release.py` with `from scripts
import release`, which puts the repository root on `sys.path`. The workflows
run `python scripts/release.py`, which puts `scripts/` there instead and the
root nowhere. The two disagree, so an entry point can import cleanly in the
tests and still die on its first line in CI — which is exactly how a release
reached `main` and failed at `ModuleNotFoundError: No module named 'vela'`.

So these checks load each script the way a workflow invokes it, with the
working directory absent from `sys.path`. The list is read out of the
workflows rather than written down here: a script added to CI tomorrow is
covered without anyone remembering to add it.
"""
import os
from pathlib import Path
import re
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / '.github/workflows'

# Loadable only on Windows: it imports `ctypes.wintypes` and `winreg` at
# module level. CI gates it to Windows runners for the same reason.
WINDOWS_ONLY = {'test-windows-distribution.py'}

MISSING = re.compile(r"ModuleNotFoundError: No module named '([\w.]+)'")

# Runs the script's module level — its imports, the part that broke — but not
# its work: every entry point guards that behind `if __name__ == '__main__'`.
LOAD = """
import runpy, sys
from pathlib import Path
script = Path(sys.argv[1]).resolve()
# What `python <script>` does: the script's own folder leads sys.path and the
# working directory is not on it at all.
sys.path[0] = str(script.parent)
runpy.run_path(str(script), run_name='__vela_entry_check__')
"""


def invoked_scripts():
    """Every `scripts/*.py` the workflows run, in the order they appear."""
    found = []
    for workflow in sorted(WORKFLOWS.glob('*.yml')):
        text = workflow.read_text(encoding='utf-8')
        for name in re.findall(r'python\s+scripts/([\w.-]+\.py)', text):
            if name not in found:
                found.append(name)
    return found


class ScriptEntryPointTests(unittest.TestCase):
    def test_the_workflows_reference_scripts_that_exist(self):
        names = invoked_scripts()
        self.assertTrue(names, 'no scripts found in the workflows; has the parsing drifted?')
        for name in names:
            with self.subTest(script=name):
                self.assertTrue((ROOT / 'scripts' / name).is_file(),
                                f'{name} is run by a workflow but is not in scripts/')

    def test_every_script_ci_runs_loads_the_way_ci_runs_it(self):
        environment = {key: value for key, value in os.environ.items() if key != 'PYTHONPATH'}
        for name in invoked_scripts():
            if name in WINDOWS_ONLY and sys.platform != 'win32':
                continue
            with self.subTest(script=name):
                done = subprocess.run([sys.executable, '-c', LOAD, str(ROOT / 'scripts' / name)],
                                      cwd=ROOT, env=environment, capture_output=True, text=True,
                                      timeout=120)
                if done.returncode == 0:
                    continue
                # A packaging dependency that is not installed here says
                # nothing about the entry point: the workflow step that runs
                # the script for real installs those and would fail on them.
                # Vela's own package is the one this is looking for.
                absent = MISSING.search(done.stderr)
                if absent and absent[1].split('.')[0] != 'vela':
                    self.skipTest(f'{name} needs {absent[1]}, which is not installed here')
                self.fail(f'`python scripts/{name}` fails before it does anything:\n'
                          f'{done.stdout}{done.stderr}')


if __name__ == '__main__':
    unittest.main()
