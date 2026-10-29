import os
import unittest
from unittest import mock

from scripts import mob_x_preflight as pf


JOB = {"type": "mob_x_link", "status": "confirmed", "panel_link_id": 7}
LINK = {"id": 7, "status": "confirmed"}


def run_main(fake, link_id=""):
    env = {"AGENT_API_BASE_URL": "https://worker.example", "AGENT_API_TOKEN": "t"}
    if link_id:
        env["MOB_X_PANEL_LINK_ID"] = link_id
    outputs = {}
    with mock.patch.dict(os.environ, env, clear=True), \
            mock.patch.object(pf, "fetch_items", side_effect=fake), \
            mock.patch.object(pf, "_append_output", side_effect=lambda k, v: outputs.__setitem__(k, v)), \
            mock.patch("builtins.print"):
        code = pf.main()
    return code, outputs


class PreflightDegradeTests(unittest.TestCase):
    def test_jobs_failure_scheduled_run_skips_without_error(self):
        def fake(endpoint, *_a, **_k):
            if endpoint == "jobs":
                return [], "endpoint=jobs attempts=3 status=200 error=TimeoutError", 3
            return [LINK], None, 1
        code, out = run_main(fake)
        self.assertEqual(code, 0)
        self.assertEqual(out["has_job"], "false")
        self.assertNotIn("error_detail", out)

    def test_jobs_failure_explicit_link_continues(self):
        def fake(endpoint, *_a, **_k):
            if endpoint == "jobs":
                return [], "endpoint=jobs attempts=3 status=none error=TimeoutError", 3
            return [], "endpoint=links attempts=3 status=none error=TimeoutError", 3
        code, out = run_main(fake, link_id="7")
        self.assertEqual(code, 0)
        self.assertEqual(out["has_job"], "true")

    def test_links_failure_still_degrades_with_confirmed_job(self):
        def fake(endpoint, *_a, **_k):
            if endpoint == "jobs":
                return [JOB], None, 1
            return [], "endpoint=links attempts=3 status=none error=TimeoutError", 3
        code, out = run_main(fake)
        self.assertEqual(code, 0)
        self.assertEqual(out["has_job"], "true")

    def test_healthy_path_finds_job(self):
        def fake(endpoint, *_a, **_k):
            return ([JOB] if endpoint == "jobs" else [LINK]), None, 1
        code, out = run_main(fake)
        self.assertEqual((code, out["has_job"]), (0, "true"))

    def test_healthy_path_wrong_requested_link_is_false(self):
        def fake(endpoint, *_a, **_k):
            return ([JOB] if endpoint == "jobs" else [LINK]), None, 1
        code, out = run_main(fake, link_id="9")
        self.assertEqual((code, out["has_job"]), (0, "false"))


if __name__ == "__main__":
    unittest.main()
