"""Test-channel destination pinning without storing the raw channel GUID."""
from __future__ import annotations

import hashlib
import hmac

# Computed once from the owner-confirmed RUBIKA_CHANNEL_GUID secret via the
# manual-only hash-audit workflow. Never replace this with the raw identifier.
EXPECTED_TEST_CHANNEL_GUID_SHA256 = "0c939688dd5536447389134ba514e9353a6ef97214de3c4f848adb53e1cd4ab8"


def channel_guid_sha256(value: object) -> str:
    normalized = str(value or "").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def is_test_channel_guid(value: object) -> bool:
    normalized = str(value or "").strip()
    if not normalized:
        return False
    actual = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return hmac.compare_digest(actual, EXPECTED_TEST_CHANNEL_GUID_SHA256)


def assert_test_channel_guid(value: object) -> str:
    normalized = str(value or "").strip()
    if not is_test_channel_guid(normalized):
        raise ValueError("rubika_destination_not_test_channel")
    return normalized
