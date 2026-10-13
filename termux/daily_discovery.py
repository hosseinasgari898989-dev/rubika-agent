"""Termux entry point: collect public metadata and optionally push it to the test branch.

Run from the repository root with:
    python -m termux.daily_discovery --push

The command never downloads media and never publishes to Rubika.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.discovery.apify_instagram import discover_instagram_reels
from agent.discovery.termux_sources import discover_tiktok_hashtags, discover_youtube_metadata


ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "artifacts" / "termux_discovery.json"
APIFY_STATE_PATH = Path.home() / ".local" / "state" / "rubika-agent" / "apify-instagram-state.json"
TARGET_BRANCH = "agent-brain-mega-v1"
EXPECTED_REPO_SUFFIX = "hosseinasgari898989-dev/rubika-agent"


def load_apify_token() -> str:
    """Read token from the environment or a private file; never print it."""
    token = os.getenv("APIFY_API_TOKEN", "").strip()
    if token:
        return token
    configured_path = os.getenv("APIFY_TOKEN_FILE", "").strip()
    path = Path(configured_path).expanduser() if configured_path else (
        Path.home() / ".config" / "rubika-agent" / "apify_token"
    )
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


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


def collect_daily_report(
    *,
    apify_token: str | None = None,
    output_path: str | Path = REPORT_PATH,
    apify_state_path: str | Path = APIFY_STATE_PATH,
) -> dict[str, Any]:
    """Collect metadata from the phone's network, with no media download."""
    tiktok_candidates, tiktok_attempts = discover_tiktok_hashtags()
    youtube_candidates, youtube_attempts = discover_youtube_metadata()
    instagram_report = discover_instagram_reels(
        token=apify_token if apify_token is not None else load_apify_token(),
        state_path=apify_state_path,
        max_results=2,
    )

    # Keep one row per platform + stable ID. Invalid or missing IDs are never generated.
    lists = {
        "tiktok": tiktok_candidates,
        "youtube": youtube_candidates,
        "instagram": instagram_report.get("candidates", []),
    }
    normalized: dict[str, list[dict[str, Any]]] = {}
    for platform, rows in lists.items():
        seen: set[str] = set()
        normalized[platform] = []
        for row in rows:
            if not isinstance(row, dict) or row.get("platform") != platform:
                continue
            identity = row.get("id")
            url = row.get("url")
            if not isinstance(identity, str) or not identity or not isinstance(url, str):
                continue
            if identity in seen:
                continue
            seen.add(identity)
            normalized[platform].append(row)

    now = datetime.now(timezone.utc)
    report = {
        "schema_version": 1,
        "generated_at": now.isoformat(),
        "source": "termux_phone",
        "mode": "metadata_only_no_download_no_publish",
        "summary": {
            "tiktok_candidates": len(normalized["tiktok"]),
            "youtube_candidates": len(normalized["youtube"]),
            "instagram_candidates": len(normalized["instagram"]),
            "instagram_apify_status": instagram_report.get("status", "unknown"),
            "ready_to_publish": False,
        },
        "candidates": normalized,
        "attempts": [
            *tiktok_attempts,
            *youtube_attempts,
            *instagram_report.get("attempts", []),
        ],
        "provider_reports": {
            "instagram_apify": {
                key: instagram_report.get(key)
                for key in ("status", "message", "usage")
                if key in instagram_report
            }
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
        timeout=45,
    )


def push_report_to_experimental_branch(report_path: str | Path = REPORT_PATH) -> None:
    """Push only the discovery report, and only when this checkout is on the test branch."""
    branch = _git(["branch", "--show-current"])
    if branch.returncode != 0 or branch.stdout.strip() != TARGET_BRANCH:
        raise RuntimeError("wrong_branch")
    remote = _git(["remote", "get-url", "origin"])
    if remote.returncode != 0:
        raise RuntimeError("origin_missing")
    remote_url = remote.stdout.strip().lower().removesuffix(".git")
    if EXPECTED_REPO_SUFFIX not in remote_url:
        raise RuntimeError("unexpected_origin")

    relative_path = str(Path(report_path).resolve().relative_to(ROOT.resolve()))
    added = _git(["add", "--", relative_path])
    if added.returncode != 0:
        raise RuntimeError("git_add_failed")
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
        # Do not print raw git output because remote URLs could contain credentials.
        raise RuntimeError("git_push_failed")
    print(f"GitHub: metadata report pushed to {TARGET_BRANCH}.", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--push",
        action="store_true",
        help="commit the metadata report and push it to the experimental GitHub branch",
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
    print("Done: metadata only; no videos downloaded and nothing published to Rubika.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
