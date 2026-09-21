"""Exercise the configured event fetch with real Git, without GitHub access."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


class DroneCheckoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.remote = self.root / "remote.git"
        self.seed = self.root / "seed"
        self.checkout = self.root / "checkout"
        self.git(self.root, "init", "--bare", "--initial-branch=main", str(self.remote))
        self.git(self.root, "init", "--initial-branch=main", str(self.seed))
        self.git(self.seed, "config", "user.name", "Checkout test")
        self.git(self.seed, "config", "user.email", "checkout@example.invalid")
        self.git(self.seed, "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "main")
        self.main = self.git(self.seed, "rev-parse", "HEAD").stdout.strip()
        self.git(self.seed, "tag", "v1.0.0")
        self.git(self.seed, "push", str(self.remote), "main", "--tags")
        self.git(self.seed, "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "fork-only")
        self.pull = self.git(self.seed, "rev-parse", "HEAD").stdout.strip()
        self.git(self.seed, "push", str(self.remote), "HEAD:refs/pull/7/head")
        # Disable local object sharing: only advertised branches/tags are fetched.
        self.git(self.root, "clone", "--no-local", str(self.remote), str(self.checkout))
        pipeline = Path(__file__).resolve().parents[1] / "drone.yml"
        commands = [
            line.strip().removeprefix("- ")
            for line in pipeline.read_text().splitlines()
            if line.strip().startswith(("- git fetch origin ", "- git checkout --detach "))
        ]
        self.assertEqual(len(commands), 2)
        self.commands = "\n".join(commands)

    @staticmethod
    def git(cwd, *args, check=True):
        return subprocess.run(
            ["git", *args], cwd=cwd, check=check, capture_output=True, text=True,
        )

    def event_checkout(self, ref, commit):
        return subprocess.run(
            ["sh", "-ec", self.commands], cwd=self.checkout,
            env={**os.environ, "DRONE_COMMIT_REF": ref, "DRONE_COMMIT": commit},
            capture_output=True, text=True,
        )

    def test_fork_only_commit_is_fetched_and_checked_out_exactly(self):
        missing = self.git(self.checkout, "cat-file", "-e", self.pull, check=False)
        self.assertNotEqual(missing.returncode, 0)
        result = self.event_checkout("refs/pull/7/head", self.pull)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git(self.checkout, "rev-parse", "HEAD").stdout.strip(), self.pull)
        self.assertEqual(self.git(self.checkout, "tag", "--list").stdout.strip(), "v1.0.0")
        self.assertEqual(self.git(self.checkout, "rev-parse", "--is-shallow-repository").stdout.strip(), "false")

    def test_branch_and_tag_events_still_work(self):
        for ref in ("refs/heads/main", "refs/tags/v1.0.0"):
            with self.subTest(ref=ref):
                result = self.event_checkout(ref, self.main)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.git(self.checkout, "rev-parse", "HEAD").stdout.strip(), self.main)

    def test_missing_ref_fails_even_if_commit_exists(self):
        result = self.event_checkout("refs/pull/404/head", self.main)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("couldn't find remote ref", result.stderr)

    def test_missing_commit_does_not_fall_back_to_ref_tip(self):
        result = self.event_checkout("refs/pull/7/head", "0" * 40)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.git(self.checkout, "rev-parse", "HEAD").stdout.strip(), self.main)


if __name__ == "__main__":
    unittest.main()
