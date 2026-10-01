"""Run the Python suite in parallel.

    python scripts/run-python-tests.py [--workers N] [pattern ...]

The same tests `python -m unittest discover -s tests` runs, spread over several
long-lived worker processes that each take the next piece of work as they free
up. Run serially the suite takes over twenty minutes, most of it spent waiting:
a managed-app test starts a real service and waits for it to stop. Those waits
overlap here instead of adding up.

A piece of work is one test, or a whole class when that class has class-level
setup, so a `setUpClass` still runs once rather than once per test. The slowest
pieces from the previous run are handed out first, from a timing file kept in
the ignored `.local/` directory, so one long class does not start last and set
the finish time on its own.

Every test uses its own temporary data directory and its own ports, which is
what makes running them side by side safe. The few that change a file in the
repository itself are listed in `ALONE` and run on their own at the end. Patterns, when given,
choose modules by file name the way `discover -p` does (`test_managed*.py`).

`python -m unittest discover -s tests` keeps working unchanged; this is a
faster way to run the same thing, not a different suite.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import atexit
import multiprocessing
import os
import shutil
import sys
import tempfile
import time
import traceback
import unittest
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"
TIMINGS = ROOT / ".local" / "python-test-durations.json"

#: Tests that change something outside their own temporary directory, so
#: nothing else may run while they do. They run alone, after everything else.
#: Each entry is a test id prefix and the reason it is here.
ALONE = {
    "test_agent_packaging.ProvenanceTests": (
        "rewrites scripts/browser-worker/provenance.json, which the browser "
        "runtime tests read, and restores it afterwards"
    ),
}


def _paths() -> None:
    for entry in (str(TESTS), str(ROOT)):
        if entry not in sys.path:
            sys.path.insert(0, entry)


# ------------------------------------------------------------------ worker --


class _Collect(unittest.TestResult):
    """Results as plain data, so they cross the process boundary."""

    def __init__(self):
        super().__init__()
        self.buffer = True  # a test's own output is kept with its failure
        self.problems: list[tuple[str, str, str]] = []
        self.ran_ids: list[str] = []

    def startTest(self, test):
        super().startTest(test)
        self.ran_ids.append(test.id())

    def addError(self, test, err):
        super().addError(test, err)
        self.problems.append(("ERROR", str(test), self.errors[-1][1]))

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.problems.append(("FAIL", str(test), self.failures[-1][1]))

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None:
            kind = "FAIL" if issubclass(err[0], test.failureException) else "ERROR"
            text = (self.failures if kind == "FAIL" else self.errors)[-1][1]
            self.problems.append((kind, str(subtest), text))

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self.problems.append(("UNEXPECTED SUCCESS", str(test), ""))


def worker_start() -> None:
    """Give this worker a data directory of its own.

    Importing `vela.api` builds a default app in `VELA_DATA_DIR`, and the
    parent set that variable while it imported the suite to plan it. Every
    worker inheriting the same directory would have them all create the same
    databases at once, so each gets a fresh one instead.
    """
    data = tempfile.mkdtemp(prefix="vela-test-worker-")
    os.environ["VELA_DATA_DIR"] = data
    atexit.register(shutil.rmtree, data, ignore_errors=True)
    _paths()


def run_unit(names: list[str]) -> dict:
    """Run one piece of work in this worker and report what happened."""
    _paths()
    started = time.perf_counter()
    result = _Collect()
    try:
        suite = unittest.defaultTestLoader.loadTestsFromNames(names)
        suite.run(result)
    except Exception:  # noqa: BLE001 - reported, never swallowed
        result.problems.append(("ERROR", ", ".join(names), traceback.format_exc()))
    return {
        "names": names,
        "ran": result.testsRun,
        "skipped": len(result.skipped),
        "expected_failures": len(result.expectedFailures),
        "problems": result.problems,
        "seconds": time.perf_counter() - started,
    }


# ------------------------------------------------------------------ parent --


def _flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _flatten(item)
        else:
            yield item


def _has_class_setup(cls) -> bool:
    base = unittest.TestCase
    return (
        cls.setUpClass.__func__ is not base.setUpClass.__func__
        or cls.tearDownClass.__func__ is not base.tearDownClass.__func__
    )


def _has_module_setup(module_name: str) -> bool:
    module = sys.modules.get(module_name)
    return bool(module and (hasattr(module, "setUpModule") or hasattr(module, "tearDownModule")))


def plan(patterns: list[str]) -> tuple[list[list[str]], list[tuple[str, str, str]]]:
    """Discover the suite and cut it into pieces of work."""
    _paths()
    loader = unittest.TestLoader()
    suite = loader.discover(str(TESTS), pattern="test*.py", top_level_dir=str(TESTS))
    units: dict[str, list[str]] = {}
    broken: list[tuple[str, str, str]] = []
    for test in _flatten(suite):
        test_id = test.id()
        module = test_id.split(".", 1)[0]
        if patterns and not any(fnmatch.fnmatch(f"{module}.py", p) for p in patterns):
            continue
        # A module that could not even be imported is reported as it is.
        if isinstance(test, unittest.loader._FailedTest):  # noqa: SLF001
            message = getattr(test, "_exception", "could not be imported")
            broken.append(("ERROR", test_id, str(message)))
            continue
        cls = type(test)
        if _has_module_setup(cls.__module__):
            key = cls.__module__
        elif _has_class_setup(cls):
            key = f"{cls.__module__}.{cls.__qualname__}"
        else:
            key = test_id
        units.setdefault(key, []).append(test_id)
    return list(units.values()), broken


def _load_timings() -> dict[str, float]:
    try:
        return json.loads(TIMINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_timings(timings: dict[str, float]) -> None:
    try:
        TIMINGS.parent.mkdir(parents=True, exist_ok=True)
        TIMINGS.write_text(json.dumps(timings, indent=0, sort_keys=True), encoding="utf-8")
    except OSError:
        pass  # a faster next run is a convenience, never a reason to fail


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--workers",
        type=int,
        default=int(os.environ.get("VELA_TEST_WORKERS", 0))
        or min(12, max(2, (os.cpu_count() or 4) // 2)),
        help="worker processes (default: half the CPUs, at most 12; or VELA_TEST_WORKERS)",
    )
    parser.add_argument("patterns", nargs="*", help="module file patterns, like discover -p")
    options = parser.parse_args()

    started = time.perf_counter()
    units, problems = plan(options.patterns)
    timings = _load_timings()
    # Longest first. A piece never timed before is assumed slow, so new tests
    # do not end up as the last thing the run waits for.
    units.sort(key=lambda names: -timings.get(names[0], 60.0))
    total = sum(len(names) for names in units)
    print(f"Running {total} tests in {len(units)} pieces on {options.workers} workers", flush=True)

    alone = [names for names in units if names[0].startswith(tuple(ALONE))]
    together = [names for names in units if not names[0].startswith(tuple(ALONE))]

    counts = {"ran": 0, "skipped": 0, "expected": 0}

    def drain(pool, batch):
        futures = {pool.submit(run_unit, names): names for names in batch}
        for future in as_completed(futures):
            names = futures[future]
            try:
                report = future.result()
            except Exception:  # noqa: BLE001 - a worker that died is a failure
                problems.append(("ERROR", ", ".join(names), traceback.format_exc()))
                continue
            counts["ran"] += report["ran"]
            counts["skipped"] += report["skipped"]
            counts["expected"] += report["expected_failures"]
            problems.extend(report["problems"])
            timings[names[0]] = round(report["seconds"], 3)
            print("F" if report["problems"] else ".", end="", flush=True)

    # Spawned, never forked, on every platform: the parent has imported the
    # whole suite to plan it, and a forked child would inherit that state.
    context = multiprocessing.get_context("spawn")
    pools = {"mp_context": context, "initializer": worker_start}
    with ProcessPoolExecutor(max_workers=options.workers, **pools) as pool:
        drain(pool, together)
    if alone:
        with ProcessPoolExecutor(max_workers=1, **pools) as pool:
            drain(pool, alone)
    print()
    ran, skipped, expected = counts["ran"], counts["skipped"], counts["expected"]

    _save_timings(timings)
    for kind, name, text in problems:
        print("=" * 70)
        print(f"{kind}: {name}")
        print("-" * 70)
        print(text)
    elapsed = time.perf_counter() - started
    print("-" * 70)
    print(f"Ran {ran} tests in {elapsed:.1f}s on {options.workers} workers")
    extras = []
    if skipped:
        extras.append(f"skipped={skipped}")
    if expected:
        extras.append(f"expected failures={expected}")
    suffix = f" ({', '.join(extras)})" if extras else ""
    if problems:
        print(f"FAILED (problems={len(problems)}){suffix}")
        return 1
    print(f"OK{suffix}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
