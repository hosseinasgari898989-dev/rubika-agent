from agent.brain.edit_caption import EDIT_CAPTION
from agent.brain.e2e_batch_from_report import _round_robin_candidates
from agent.brain.models import Candidate


def _candidate(platform: str, external_id: str, title: str) -> Candidate:
    return Candidate(
        platform=platform,
        canonical_url=f"https://example.test/{external_id}",
        external_id=external_id,
        title=title,
        caption=title,
        source_key="creator",
        media_type="video",
        source_tier="unknown",
    )


def test_batch_round_robins_tiktok_and_youtube_and_removes_duplicate_ids():
    candidates = [
        _candidate("tiktok", "tt1", "Poppy Playtime edit one"),
        _candidate("tiktok", "tt2", "Huggy Wuggy edit two"),
        _candidate("youtube", "yt1", "CatNap Poppy Playtime edit three"),
        _candidate("youtube", "yt2", "Smiling Critters edit four"),
        _candidate("tiktok", "tt1", "Poppy Playtime edit duplicate"),
        _candidate("youtube", "yt3", "Cooking video unrelated"),
    ]

    ordered = _round_robin_candidates(candidates, platforms=("tiktok", "youtube"))

    assert [(item.platform, item.external_id) for item in ordered] == [
        ("tiktok", "tt1"),
        ("youtube", "yt1"),
        ("tiktok", "tt2"),
        ("youtube", "yt2"),
    ]


def test_edit_caption_has_channel_label_and_standard_branding():
    assert EDIT_CAPTION.startswith("𝔓𝔒𝔓𝔜 𝔓𝔏𝔄𝔜𝔗ℑ𝔐𝔈")
    assert "CHANEL» @poppy_play_time_HV" in EDIT_CAPTION
    assert "ˡⁱᵏᵉ" in EDIT_CAPTION
