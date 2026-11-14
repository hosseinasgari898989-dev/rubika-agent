from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "termux" / "pkg_index_retry.sh"


class TermuxPackageIndexRetryTests(unittest.TestCase):
    def run_retry(self, fail_first: int, fail_status: int = 100):
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            bin_dir = temp / "bin"
            bin_dir.mkdir()
            calls_file = temp / "pkg-calls"
            sleeps_file = temp / "sleep-calls"

            pkg = bin_dir / "pkg"
            pkg.write_text(
                """#!/bin/sh
count=0
if [ -f "$PKG_CALLS_FILE" ]; then
  count="$(cat "$PKG_CALLS_FILE")"
fi
count=$((count + 1))
printf '%s' "$count" > "$PKG_CALLS_FILE"
if [ "$1" != "update" ] || [ "$2" != "-y" ]; then
  exit 97
fi
if [ "$count" -le "$PKG_FAIL_FIRST" ]; then
  exit "$PKG_FAIL_STATUS"
fi
exit 0
""",
                encoding="utf-8",
            )
            pkg.chmod(0o755)

            sleep = bin_dir / "sleep"
            sleep.write_text(
                """#!/bin/sh
printf '%s\n' "$1" >> "$SLEEP_CALLS_FILE"
exit 0
""",
                encoding="utf-8",
            )
            sleep.chmod(0o755)

            env = dict(os.environ)
            env.update(
                {
                    "PATH": str(bin_dir) + os.pathsep + env.get("PATH", ""),
                    "PKG_CALLS_FILE": str(calls_file),
                    "SLEEP_CALLS_FILE": str(sleeps_file),
                    "PKG_FAIL_FIRST": str(fail_first),
                    "PKG_FAIL_STATUS": str(fail_status),
                }
            )
            result = subprocess.run(
                ["sh", str(SCRIPT)],
                check=False,
                capture_output=True,
                text=True,
                env=env,
                timeout=20,
            )
            calls = int(calls_file.read_text(encoding="utf-8")) if calls_file.exists() else 0
            sleeps = sleeps_file.read_text(encoding="utf-8").splitlines() if sleeps_file.exists() else []
            return result, calls, sleeps

    def test_transient_mirror_failures_retry_with_bounded_exponential_backoff(self):
        result, calls, sleeps = self.run_retry(fail_first=2)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, 3)
        self.assertEqual(sleeps, ["2", "4"])
        for attempt in ("1/3", "2/3", "3/3"):
            self.assertIn(f"TERMUX_PKG_INDEX_FETCH_ATTEMPT={attempt}", result.stdout)
        self.assertIn("TERMUX_PKG_INDEX_FETCH_STATUS=success", result.stdout)
        self.assertNotIn("TERMUX_ERROR_CODE=", result.stderr)

    def test_three_failed_index_fetches_preserve_failure_and_stop(self):
        result, calls, sleeps = self.run_retry(fail_first=3)
        self.assertEqual(result.returncode, 100)
        self.assertEqual(calls, 3)
        self.assertEqual(sleeps, ["2", "4"])
        self.assertIn("TERMUX_ERROR_CODE=TERMUX_PKG_INDEX_FETCH_FAILED", result.stderr)
        self.assertIn("TERMUX_PKG_INDEX_FETCH_ATTEMPT=3/3", result.stdout)

    def test_success_on_first_attempt_does_not_sleep_or_retry(self):
        result, calls, sleeps = self.run_retry(fail_first=0)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, 1)
        self.assertEqual(sleeps, [])
        self.assertIn("TERMUX_PKG_INDEX_FETCH_ATTEMPT=1/3", result.stdout)


if __name__ == "__main__":
    unittest.main()
