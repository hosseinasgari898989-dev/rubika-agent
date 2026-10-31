from __future__ import annotations

import subprocess
import unittest

from scripts import failed_media_delete as fmd

ID1 = "fm_" + "a" * 24
ID2 = "fm_" + "b" * 24
KEY = "/Rubika-Agent/failed/mob_x_job_abcdef12.mp4"


def cp(rc=0, out=""):
    return subprocess.CompletedProcess([], rc, out, "")


class RemoveRemoteTests(unittest.TestCase):
    def test_rm_success(self):
        calls = []
        self.assertTrue(fmd.remove_remote(KEY, lambda a, **k: calls.append(a) or cp(0)))
        self.assertEqual(calls, [["mega-rm", "-f", KEY]])

    def test_rm_fails_but_listing_proves_file_gone(self):
        replies = [cp(1), cp(0, "other.mp4\n")]
        self.assertTrue(fmd.remove_remote(KEY, lambda a, **k: replies.pop(0)))

    def test_rm_fails_and_file_still_listed_is_failure(self):
        replies = [cp(1), cp(0, "mob_x_job_abcdef12.mp4\n")]
        self.assertFalse(fmd.remove_remote(KEY, lambda a, **k: replies.pop(0)))

    def test_rm_fails_and_listing_fails_is_failure(self):
        replies = [cp(1), cp(1)]
        self.assertFalse(fmd.remove_remote(KEY, lambda a, **k: replies.pop(0)))

    def test_runner_exception_is_failure(self):
        def boom(*a, **k):
            raise OSError("x")
        self.assertFalse(fmd.remove_remote(KEY, boom))

    def test_bad_keys_never_reach_mega(self):
        def never(*a, **k):
            raise AssertionError("runner must not be called")
        for key in ("", "/other/x.mp4", "/Rubika-Agent/failed/..", "/Rubika-Agent/failed/a/b.mp4", "/Rubika-Agent/official/x.mp4"):
            self.assertFalse(fmd.remove_remote(key, never), key)


class ProcessTests(unittest.TestCase):
    def test_reports_each_result_and_counts(self):
        sent = []

        def post(path, payload):
            sent.append((path, payload))
            if path.endswith("pending-delete"):
                return {"ok": True, "items": [
                    {"id": ID1, "storage_key": KEY},
                    {"id": ID2, "storage_key": "/Rubika-Agent/failed/keep.mp4"},
                    {"id": "bad", "storage_key": KEY},
                ]}
            return {"ok": True}

        def runner(args, **k):
            if args[0] == "mega-rm":
                return cp(0 if args[-1] == KEY else 1)
            return cp(0, "keep.mp4\n")  # listing: second file still present

        counts = fmd.process(post, runner)
        self.assertEqual(counts, {"pending": 3, "deleted": 1, "failed": 2})
        results = [p for path, p in sent if path.endswith("delete-result")]
        self.assertEqual(results, [{"id": ID1, "ok": True}, {"id": ID2, "ok": False}])

    def test_nothing_pending(self):
        self.assertEqual(fmd.process(lambda p, b: {"ok": True, "items": []}, lambda *a, **k: cp(0)),
                         {"pending": 0, "deleted": 0, "failed": 0})


if __name__ == "__main__":
    unittest.main()
