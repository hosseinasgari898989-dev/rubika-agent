from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent.lifecycle import (
    LifecyclePolicyError,
    candidate_passes_birth_cutoff,
    filter_candidates_by_birth,
    load_lifecycle_policy,
)


class LifecyclePolicyTests(unittest.TestCase):
    def policy_file(self, folder: str, data: dict) -> Path:
        path = Path(folder) / "agent_lifecycle.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_test_mode_allows_historical_and_undated_candidates(self):
        policy = {
            "schema_version": 1,
            "mode": "test",
            "born_at": None,
        }
        old = {"id": "old", "published_at": "2019-01-01T00:00:00Z"}
        unknown = {"id": "unknown", "published_at": None}
        self.assertEqual(candidate_passes_birth_cutoff(old, policy=policy), (True, "test_mode_no_birth_cutoff"))
        self.assertEqual(candidate_passes_birth_cutoff(unknown, policy=policy), (True, "test_mode_no_birth_cutoff"))

    def test_production_accepts_only_verified_posts_at_or_after_birth(self):
        policy = {
            "schema_version": 1,
            "mode": "production",
            "born_at": "2026-10-14T12:00:00Z",
            "birth_datetime": None,
        }
        candidates = [
            {"id": "before", "published_at": "2026-10-14T11:59:59Z"},
            {"id": "at", "published_at": "2026-10-14T12:00:00Z"},
            {"id": "after", "published_at": "2026-10-14T12:01:00+00:00"},
            {"id": "unknown", "published_at": None},
        ]
        allowed, rejected = filter_candidates_by_birth(candidates, policy=policy)
        self.assertEqual([row["id"] for row in allowed], ["at", "after"])
        self.assertEqual(
            {row["id"]: row["reason"] for row in rejected},
            {
                "before": "published_before_agent_birth",
                "unknown": "publication_time_unverified",
            },
        )

    def test_production_requires_explicit_valid_birth_timestamp(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.policy_file(folder, {
                "schema_version": 1,
                "mode": "production",
                "born_at": None,
            })
            with self.assertRaisesRegex(LifecyclePolicyError, "production_birth_timestamp"):
                load_lifecycle_policy(path)

    def test_current_repository_lifecycle_uses_the_official_mob_birth_boundary(self):
        policy = load_lifecycle_policy()
        self.assertEqual(policy["mode"], "production")
        self.assertEqual(policy["born_at"], "2026-10-20T00:00:00Z")
        self.assertIsNotNone(policy["birth_datetime"])


if __name__ == "__main__":
    unittest.main()
