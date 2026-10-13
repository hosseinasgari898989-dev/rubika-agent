"""Termux entry point: discover metadata on the phone and push only the report.

Daily use:
    python -m termux.daily_discovery --push

This command collects public metadata only. It does not download media, use paid APIs,
log into social networks, bypass challenges, or publish to Rubika.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.discovery.termux_instagram import discover_instagram_hashtags
from agent.discovery.termux_sources import discover_tiktok_hashtags, discover_youtube_metadata


ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "artifacts" / "termux_discovery.json"
TARGET_BRANCH = "agent-brain-mega-v1"
EXPECTED_REPO_SUFFIX = "hosseinasgari898989-dev/rubika-agent"


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            temp_path = Path(stream.name)
        temp_path.replace(path)
    except OSError:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise


def collect_daily_report(*, output_path: str | Path = REPORT_PATH) -> dict[str, Any]:
    """Collect phone-side public metadata, dedupe by platform ID, and save JSON locally."""
    print("1/3 TikTok: checking selected public creator feeds with yt-dlp (metadata only)...", flush=True)
    tiktok_candidates, tiktok_attempts = discover_tiktok_hashtags()
    print("2/3 Instagram: searching public web indexes for Reel links (metadata only)...", flush=True)
    instagram_candidates, instagram_attempts = discover_instagram_hashtags()
    print("3/3 YouTube: searching Shorts metadata with yt-dlp (no download)...", flush=True)
    youtube_candidates, youtube_attempts = discover_youtube_metadata()

    lists = {
        "tiktok": tiktok_candidates,
        "instagram": instagram_candidates,
        "youtube": youtube_candidates,
    }
    normalized: dict[str, list[dict[str, Any]]] = {}
    for platform, rows in lists.items():
        seen: set[str] = set()
        normalized[platform] = []
        for row in rows:
            if not isinstance(row, dict) or row.get("platform") != platform:
                continue
            item_id, url = row.get("id"), row.get("url")
            if not isinstance(item_id, str) or not item_id or not isinstance(url, str):
                continue
            if item_id in seen:
                continue
            seen.add(item_id)
            normalized[platform].append(row)

    now = datetime.now(timezone.utc)
    report = {
        "schema_version": 1,
        "generated_at": now.isoformat(),
        "source": "termux_phone",
        "mode": "metadata_only_no_download_no_publish",
        "summary": {
            "tiktok_candidates": len(normalized["tiktok"]),
            "instagram_candidates": len(normalized["instagram"]),
            "youtube_candidates": len(normalized["youtube"]),
            "tiktok_method": "yt_dlp_public_creator_profiles",
            "instagram_method": "public_search_index_bing_rss_ddg_fallback",
            "paid_providers_used": [],
            "ready_to_publish": False,
        },
        "candidates": normalized,
        "attempts": [*tiktok_attempts, *instagram_attempts, *youtube_attempts],
        "limits": {
            "instagram_max_candidates_per_run": 5,
            "instagram_search_queries": 3,
            "instagram_search_engines": 2,
            "instagram_max_age_days": 90,
            "tiktok_max_creator_profiles": 10,
            "tiktok_max_age_days": 90,
            "tiktok_max_results": 25,
            "youtube_searches": 4,
            "youtube_max_age_days": 90,
            "media_downloads": 0,
            "rubika_posts": 0,
        },
    }
    _atomic_json_write(Path(output_path), report)
    return report


def _git(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def push_report_to_experimental_branch(report_path: str | Path = REPORT_PATH) -> None:
    """Push the report only, refusing main, other repositories, and unrelated staged files."""
    branch = _git(["branch", "--show-current"])
    if branch.returncode != 0 or branch.stdout.strip() != TARGET_BRANCH:
        raise RuntimeError("wrong_branch")
    remote = _git(["remote", "get-url", "origin"])
    if remote.returncode != 0:
        raise RuntimeError("origin_missing")
    remote_url = remote.stdout.strip().lower().removesuffix(".git")
    remote_url = remote_url.replace("git@github.com:", "https://github.com/")
    if EXPECTED_REPO_SUFFIX not in remote_url:
        raise RuntimeError("unexpected_origin")

    relative_path = str(Path(report_path).resolve().relative_to(ROOT.resolve()))
    added = _git(["add", "--", relative_path])
    if added.returncode != 0:
        raise RuntimeError("git_add_failed")

    staged_all = _git(["diff", "--cached", "--name-only"])
    if staged_all.returncode != 0:
        raise RuntimeError("git_diff_failed")
    staged_paths = {line.strip() for line in staged_all.stdout.splitlines() if line.strip()}
    if staged_paths - {relative_path}:
        raise RuntimeError("unrelated_changes_staged")
    staged = _git(["diff", "--cached", "--quiet", "--", relative_path])
    if staged.returncode == 0:
        print("GitHub: report has no changes; nothing to push.", flush=True)
        return
    if staged.returncode != 1:
        raise RuntimeError("git_diff_failed")

    day = datetime.now(timezone.utc).date().isoformat()
    committed = _git(["commit", "-m", f"Add Termux metadata discovery report {day}", "--", relative_path])
    if committed.returncode != 0:
        raise RuntimeError("git_commit_failed")
    pushed = _git(["push", "origin", f"HEAD:refs/heads/{TARGET_BRANCH}"])
    if pushed.returncode != 0:
        raise RuntimeError("git_push_failed")
    print(f"GitHub: metadata report pushed to {TARGET_BRANCH}.", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--push",
        action="store_true",
        help="commit and push only artifacts/termux_discovery.json to the experimental branch",
    )
    args = parser.parse_args(argv)

    try:
        report = collect_daily_report()
    except Exception as exc:
        print(f"Discovery failed safely: {type(exc).__name__}", file=sys.stderr, flush=True)
        return 2

    print(json.dumps(report["summary"], ensure_ascii=False, sort_keys=True), flush=True)
    print(f"Metadata report saved: {REPORT_PATH}", flush=True)
    if args.push:
        try:
            push_report_to_experimental_branch()
        except Exception as exc:
            print(f"GitHub push failed safely: {str(exc)}", file=sys.stderr, flush=True)
            return 3
    print("Done: metadata only. No video downloaded and nothing published to Rubika.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
