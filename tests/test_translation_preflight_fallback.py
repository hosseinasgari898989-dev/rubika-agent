from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from agent.brain.ai_usage_ledger import UsageLedgerError
from agent.brain.mob_translation import translate_to_persian
from scripts import mob_x_preflight

PERSIAN = "ماب انترتینمنت خبر رسمی تازه‌ای منتشر کرد."
SOURCE = "Mob Entertainment published an official update."


class FakeCloud:
    calls = 0

    def __init__(self, *args, **kwargs):
        pass

    def translate_official(self, text, *, kind="caption", run_id=None):
        FakeCloud.calls += 1
        return PERSIAN


class TranslationFallbackTests(unittest.TestCase):
    def setUp(self):
        FakeCloud.calls = 0

    def _run(self, code):
        with patch("agent.brain.ai_provider_clients.call_provider_with_ledger",
                   side_effect=UsageLedgerError(code)) as call, \
             patch("agent.brain.cloud_state.CloudState", FakeCloud):
            return translate_to_persian(SOURCE), call.call_count

    def test_known_budget_code_reaches_worker_ai(self):
        result, calls = self._run("code_ai_translation_daily_budget_exhausted")
        self.assertEqual(result, PERSIAN)
        self.assertEqual(calls, 2)
        self.assertEqual(FakeCloud.calls, 1)

    def test_unknown_code_ai_code_reaches_worker_ai(self):
        result, _ = self._run("code_ai_x")
        self.assertEqual(result, PERSIAN)
        self.assertEqual(FakeCloud.calls, 1)

    def test_ledger_infrastructure_failure_stays_fail_closed(self):
        result, calls = self._run("usage_ledger_unavailable")
        self.assertIsNone(result)
        self.assertEqual(calls, 1)
        self.assertEqual(FakeCloud.calls, 0)

    def test_unexpected_provider_exception_reaches_worker_ai(self):
        with patch("agent.brain.ai_provider_clients.call_provider_with_ledger",
                   side_effect=RuntimeError("boom")), \
             patch("agent.brain.cloud_state.CloudState", FakeCloud):
            self.assertEqual(translate_to_persian(SOURCE), PERSIAN)
        self.assertEqual(FakeCloud.calls, 1)


class PreflightDegradedTests(unittest.TestCase):
    ENV = {"AGENT_API_BASE_URL": "https://worker.example", "AGENT_API_TOKEN": "unit-test-token",
           "MOB_X_PANEL_LINK_ID": ""}

    def _main(self, jobs_result, links_result):
        def fake_fetch(endpoint, *_args, **_kwargs):
            return jobs_result if endpoint == "jobs" else links_result

        out = io.StringIO()
        with patch.dict(os.environ, self.ENV), \
             patch.object(mob_x_preflight, "fetch_items", side_effect=fake_fetch), \
             redirect_stdout(out):
            code = mob_x_preflight.main()
        return code, out.getvalue()

    JOB = [{"type": "mob_x_link", "status": "confirmed", "panel_link_id": 7}]
    LINKS_ERR = ([], "endpoint=links attempts=3 status=none error=TimeoutError", 3)

    def test_links_failure_with_confirmed_job_still_has_job(self):
        code, out = self._main((self.JOB, None, 1), self.LINKS_ERR)
        self.assertEqual(code, 0)
        self.assertIn("has_job=true", out)
        self.assertIn('"degraded": true', out)

    def test_links_failure_without_job_has_no_job(self):
        code, out = self._main(([], None, 1), self.LINKS_ERR)
        self.assertEqual(code, 0)
        self.assertIn("has_job=false", out)

    def test_jobs_failure_still_aborts(self):
        with self.assertRaises(SystemExit) as ctx:
            self._main(([], "endpoint=jobs attempts=3 status=none error=TimeoutError", 3),
                       ([], None, 1))
        self.assertEqual(ctx.exception.code, "mob_x_preflight_worker_api_unavailable")

    def test_healthy_links_path_unchanged(self):
        links = ([{"id": 7, "status": "confirmed"}], None, 1)
        code, out = self._main((self.JOB, None, 1), links)
        self.assertEqual(code, 0)
        self.assertIn("has_job=true", out)
        bad = ([{"id": 7, "status": "published"}], None, 1)
        _, out2 = self._main((self.JOB, None, 1), bad)
        self.assertIn("has_job=false", out2)


if __name__ == "__main__":
    unittest.main()
