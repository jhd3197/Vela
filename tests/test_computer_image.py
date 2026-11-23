"""The `computer` image target: what the Dockerfile promises, without Docker.

These read the Dockerfile as text. The suite runs on machines that have no
Docker, and a hermetic test cannot build an image anyway; what it can pin down
is the contract a built image keeps: the `computer` target exists, runs
unprivileged, declares its volumes and healthcheck, starts `vela.computer`,
never carries the controller token, and leaves the default `vela.container`
target as the last stage, untouched.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def stages(text):
    """Split a Dockerfile into stages: base image, optional name, body lines."""
    found = []
    current = None
    for line in text.splitlines():
        match = re.match(r"^FROM\s+(\S+)(?:\s+[Aa][Ss]\s+(\S+))?\s*$", line)
        if match:
            current = {"base": match.group(1), "name": match.group(2), "lines": []}
            found.append(current)
        elif current is not None:
            current["lines"].append(line)
    return found


def logical_lines(lines):
    """Join backslash continuations and drop comments and blank lines."""
    out = []
    for raw in lines:
        line = raw.rstrip()
        if out and out[-1].endswith("\\"):
            out[-1] = out[-1][:-1] + " " + line.lstrip()
        else:
            out.append(line)
    return [line for line in out if line.strip() and not line.lstrip().startswith("#")]


class ComputerImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        cls.all_stages = stages(cls.text)
        cls.by_name = {stage["name"]: stage for stage in cls.all_stages if stage["name"]}
        cls.computer = cls.by_name.get("computer")
        cls.last = cls.all_stages[-1]

    def computer_lines(self):
        self.assertIsNotNone(self.computer, "the Dockerfile has no `computer` target")
        return logical_lines(self.computer["lines"])

    def test_the_computer_target_exists_on_the_python_slim_base(self):
        self.assertIsNotNone(self.computer)
        self.assertEqual(self.computer["base"], "python:3.12-slim-bookworm")

    def test_the_computer_target_is_not_the_default_one(self):
        # `docker build` without --target keeps building the last stage.
        self.assertIsNotNone(self.computer)
        self.assertIsNot(self.computer, self.last)

    def test_it_runs_as_a_fixed_non_root_user(self):
        lines = self.computer_lines()
        users = [line for line in lines if line.startswith("USER ")]
        self.assertTrue(users, "the computer stage never drops root")
        for line in users:
            account = line.split()[1]
            uid = account.split(":")[0]
            self.assertNotIn(uid, ("0", "root"), f"{line} runs the container as root")

    def test_it_declares_data_and_workspace_volumes(self):
        volumes = [line for line in self.computer_lines() if line.startswith("VOLUME")]
        self.assertEqual(len(volumes), 1)
        self.assertIn('"/data"', volumes[0])
        self.assertIn('"/workspace"', volumes[0])

    def test_it_has_a_healthcheck_on_the_health_route(self):
        checks = [line for line in self.computer_lines() if line.startswith("HEALTHCHECK")]
        self.assertEqual(len(checks), 1)
        self.assertIn("/api/health", checks[0])
        self.assertIn("python", checks[0])
        self.assertNotIn("curl", checks[0])

    def test_it_starts_the_computer_entry_point(self):
        starters = [line for line in self.computer_lines()
                    if line.startswith("ENTRYPOINT") or line.startswith("CMD ")]
        self.assertEqual(starters, ['ENTRYPOINT ["python", "-m", "vela.computer"]'])

    def test_it_exposes_the_gateway_port(self):
        self.assertIn("EXPOSE 7700", self.computer_lines())

    def test_the_token_never_appears_as_an_env_or_build_arg(self):
        for stage in self.all_stages:
            for line in logical_lines(stage["lines"]):
                if line.startswith(("ENV", "ARG")):
                    # The file the token is read from may be named; the token
                    # itself may not. A word boundary already lets the
                    # _FILE variant through, since `_` is a word character.
                    self.assertIsNone(
                        re.search(r"\bVELA_CONTROLLER_TOKEN\b", line),
                        f"{line} carries the controller token",
                    )

    def test_the_worker_node_runtime_comes_from_the_official_node_image(self):
        node_names = {stage["name"] for stage in self.all_stages
                      if stage["base"] == "node:22-bookworm-slim" and stage["name"]}
        self.assertTrue(node_names, "no named node:22-bookworm-slim stage to copy from")
        copies = [line for line in self.computer_lines() if line.startswith("COPY ")]
        self.assertTrue(
            any(any(f"--from={name}" in line for name in node_names)
                and "/usr/local/bin/node" in line
                for line in copies),
            "the computer stage does not copy node from the official image",
        )

    def test_it_reuses_the_dashboard_build_stage(self):
        copies = [line for line in self.computer_lines() if line.startswith("COPY ")]
        self.assertTrue(any("--from=dashboard" in line for line in copies))

    def test_it_installs_the_browser_worker_the_way_the_setup_script_does(self):
        copies = [line for line in self.computer_lines() if line.startswith("COPY ")]
        self.assertTrue(any("scripts/browser-worker/src" in line for line in copies))
        self.assertTrue(any("setup-browser-worker.py" in line for line in copies))
        runs = [line for line in self.computer_lines() if line.startswith("RUN ")]
        # Pinned playwright-core straight from the lockfile, and Chromium with
        # the system libraries a slim base lacks.
        self.assertTrue(any("npm ci --omit=dev" in line for line in runs),
                        "the worker is not installed from the lockfile")
        self.assertTrue(any("playwright-core install --with-deps chromium" in line for line in runs),
                        "Chromium is not installed with its system libraries")
        # provenance.json is written by the one script that owns its format;
        # --skip-browser because the browser is deliberately put there first.
        scripts = [line for line in runs if "scripts/setup-browser-worker.py" in line]
        self.assertTrue(scripts, "the computer stage never runs the setup script")
        for line in scripts:
            self.assertIn("--skip-browser", line)

    def test_the_default_target_is_still_the_container(self):
        self.assertEqual(self.last["base"], "python:3.12-slim-bookworm")
        lines = logical_lines(self.last["lines"])
        self.assertIn('CMD ["python", "-m", "vela.container"]', lines)
        self.assertIn("EXPOSE 7700", lines)
        # What the default image never did, the new target must not add.
        for keyword in ("USER", "HEALTHCHECK", "VOLUME", "ENTRYPOINT"):
            self.assertFalse(
                [line for line in lines if line == keyword or line.startswith(keyword + " ")],
                f"the default target gained a {keyword} instruction",
            )


if __name__ == "__main__":
    unittest.main()
