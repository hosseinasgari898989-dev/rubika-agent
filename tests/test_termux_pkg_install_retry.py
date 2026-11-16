from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "termux" / "pkg_install_retry.sh"


class TermuxPackageInstallRetryTests(unittest.TestCase):
    def run_retry(self, fail_first: int, fail_status: int = 124):
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            bin_dir = temp / "bin"
            bin_dir.mkdir()
            pkg_calls = temp / "pkg-calls"
            timeout_calls = temp / "timeout-calls"
            sleep_calls = temp / "sleep-calls"

            timeout = bin_dir / "timeout"
            timeout.write_text(
                """#!/bin/sh
duration="$1"
shift
printf '%s\\n' "$duration" >> "$TIMEOUT_CALLS_FILE"
if [ "$duration" != "900s" ]; then
  exit 98
fi
exec "$@"
""",
                encoding="utf-8",
            )
            timeout.chmod(0o755)

            pkg = bin_dir / "pkg"
            pkg.write_text(
                """#!/bin/sh
count=0
if [ -f "$PKG_CALLS_FILE" ]; then
  count="$(cat "$PKG_CALLS_FILE")"
fi
count=$((count + 1))
printf '%s' "$count" > "$PKG_CALLS_FILE"
if [ "$1" != "install" ] || [ "$2" != "-y" ] || [ "$3" != "python" ] || [ "$#" -ne 3 ]; then
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
printf '%s\\n' "$1" >> "$SLEEP_CALLS_FILE"
exit 0
""",
                encoding="utf-8",
            )
            sleep.chmod(0o755)

            env = dict(os.environ)
            env.update(
                {
                    "PATH": str(bin_dir) + os.pathsep + env.get("PATH", ""),
                    "PKG_CALLS_FILE": str(pkg_calls),
                    "TIMEOUT_CALLS_FILE": str(timeout_calls),
                    "SLEEP_CALLS_FILE": str(sleep_calls),
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
            calls = int(pkg_calls.read_text(encoding="utf-8")) if pkg_calls.exists() else 0
            timeouts = timeout_calls.read_text(encoding="utf-8").splitlines() if timeout_calls.exists() else []
            sleeps = sleep_calls.read_text(encoding="utf-8").splitlines() if sleep_calls.exists() else []
            return result, calls, timeouts, sleeps

    def test_transient_install_failures_retry_with_bounded_backoff_and_900s_timeout(self):
        result, calls, timeouts, sleeps = self.run_retry(fail_first=2)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, 3)
        self.assertEqual(timeouts, ["900s", "900s", "900s"])
        self.assertEqual(sleeps, ["2", "4"])
        for attempt in ("1/3", "2/3", "3/3"):
            self.assertIn(f"TERMUX_PKG_INSTALL_ATTEMPT={attempt}", result.stdout)
        self.assertIn("TERMUX_PKG_INSTALL_STATUS=success", result.stdout)
        self.assertNotIn("TERMUX_ERROR_CODE=", result.stderr)

    def test_final_failure_stops_after_three_attempts_and_preserves_exit_code(self):
        result, calls, timeouts, sleeps = self.run_retry(fail_first=3, fail_status=124)
        self.assertEqual(result.returncode, 124)
        self.assertEqual(calls, 3)
        self.assertEqual(timeouts, ["900s", "900s", "900s"])
        self.assertEqual(sleeps, ["2", "4"])
        self.assertIn("TERMUX_ERROR_CODE=TERMUX_PKG_INSTALL_FAILED", result.stderr)
        self.assertIn("TERMUX_PKG_INSTALL_ATTEMPT=3/3", result.stdout)

    def test_first_attempt_success_does_not_retry_or_sleep(self):
        result, calls, timeouts, sleeps = self.run_retry(fail_first=0)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, 1)
        self.assertEqual(timeouts, ["900s"])
        self.assertEqual(sleeps, [])
        self.assertIn("TERMUX_PKG_INSTALL_ATTEMPT=1/3", result.stdout)


if __name__ == "__main__":
    unittest.main()
