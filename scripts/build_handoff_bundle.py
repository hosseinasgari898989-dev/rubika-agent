#!/usr/bin/env python3
"""Build a sanitized snapshot bundle with a rubka library ZIP and safe Actions history."""
from __future__ import annotations
import argparse, hashlib, json, os, re, shutil, time, urllib.request, zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

REPO = "hosseinasgari898989-dev/rubika-agent"
API = "https://api.github.com"
KNOWN_SENSITIVE_NAMES = {
    "AGENT_API_TOKEN", "GROQ_API_KEY", "OPENROUTER_API_KEY", "RUBIKA_TOKEN",
    "RUBIKA_OWNER_CHAT_ID", "RUBIKA_CHANNEL_GUID", "MEGA_EMAIL", "MEGA_PASSWORD",
    "CLOUDFLARE_API_TOKEN", "GITHUB_DISPATCH_TOKEN", "YOUTUBE_COOKIES", "RUBIKA_BOT_TOKEN",
}
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
             ".mypy_cache", ".ruff_cache", ".wrangler", ".cache", "dist", "build",
             "playwright-report", "test-results"}
SKIP_NAMES = {".DS_Store", ".dev.vars", "credentials.json", "service-account.json",
              "id_rsa", "id_ed25519", "known_hosts", ".coverage"}
ENV_FILE_RE = re.compile(r"^\.env(?:$|\.)", re.IGNORECASE)
GUID_RE = re.compile(r"""(?i)(\bRUBIKA_CHANNEL_GUID\b["']?\s*[:=]\s*["']?)[A-Za-z0-9_+/=-]{16,}(["']?)""")
TOKEN_PATTERNS = [
    re.compile(r"\b(?:gsk_[A-Za-z0-9_-]{20,}|sk-or-v1-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{20,})\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{24,}"),
]
PRIVATE_KEY_RE = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")
AUTO_SECTIONS = {
    "secrets": ("<!-- AUTO:SECRETS:START -->", "<!-- AUTO:SECRETS:END -->"),
    "vars": ("<!-- AUTO:VARS:START -->", "<!-- AUTO:VARS:END -->"),
    "history": ("<!-- AUTO:HISTORY:START -->", "<!-- AUTO:HISTORY:END -->"),
}


def is_excluded_path(rel: str) -> bool:
    path = PurePosixPath(rel)
    if any(part in SKIP_DIRS for part in path.parts):
        return True
    name = path.name
    if ENV_FILE_RE.match(name) or name in SKIP_NAMES:
        return True
    if name.endswith((".pem", ".key", ".p12", ".pfx", ".der", ".sqlite", ".sqlite3", ".db")):
        return True
    return name.lower() in {"secrets.json", "token.json", "auth.json", "credentials.yaml", "credentials.yml"}


def discover_secret_var_names(source: Path) -> tuple[set[str], set[str]]:
    secrets: set[str] = set()
    variables: set[str] = {"RUBIKA_AGENT_MOB_X_PUBLISH_GATE_20261014", "PUBLISHING_ENABLED"}
    secret_rx = re.compile(r"\$\{\{\s*secrets\.([A-Z][A-Z0-9_]*)\s*\}\}", re.I)
    vars_rx = re.compile(r"\$\{\{\s*vars\.([A-Z][A-Z0-9_]*)\s*\}\}", re.I)
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(source).as_posix()
        if is_excluded_path(rel) or ".git" in path.parts:
            continue
        try:
            body = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        secrets.update(x.upper() for x in secret_rx.findall(body))
        variables.update(x.upper() for x in vars_rx.findall(body))
    return secrets, variables


def scrub_text(body: str, secret_names: set[str]) -> str:
    if PRIVATE_KEY_RE.search(body):
        raise RuntimeError("private_key_detected")
    for pattern in TOKEN_PATTERNS:
        body = pattern.sub("[REDACTED_SECRET]", body)
    names = set(secret_names) | set(KNOWN_SENSITIVE_NAMES)
    names.update(n for n in re.findall(r"\b[A-Z][A-Z0-9_]{2,}\b", body)
                 if n.endswith(("_TOKEN", "_API_KEY", "_SECRET", "_PASSWORD", "_COOKIE", "_PRIVATE_KEY")))
    names.update({"RUBIKA_CHANNEL_GUID", "RUBIKA_OWNER_CHAT_ID", "MEGA_EMAIL"})
    joined = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    assign = re.compile(
        r"(?i)(\b(?:" + joined + r")\b[\"']?\s*[:=]\s*)([\"']?)(?!\$\{\{|\{\{)([^\s,;\"'#}]+)([\"']?)"
    )
    body = assign.sub(lambda m: m.group(1) + m.group(2) + "[REDACTED_VALUE]" + m.group(4), body)
    return GUID_RE.sub(r"\1[REDACTED_GUID]\2", body)


def suspicious_text(body: str) -> list[str]:
    findings = []
    if PRIVATE_KEY_RE.search(body):
        findings.append("private_key")
    if any(pattern.search(body) for pattern in TOKEN_PATTERNS):
        findings.append("provider_or_bearer_token")
    for line in body.splitlines():
        if chr(36) + chr(123) in line or "$RUBIKA_CHANNEL_GUID" in line:
            continue
        assignment = re.search(
            r"""(?i)(?:^\s*(?:(?:const|let|var|export\s+const)\s+)?["']?RUBIKA_CHANNEL_GUID["']?\s*[:=]\s*|[,{]\s*["']?RUBIKA_CHANNEL_GUID["']?\s*:\s*)(.*)$""",
            line,
        )
        if not assignment:
            continue
        rhs = assignment.group(1).strip()
        if rhs and not rhs.startswith("[REDACTED"):
            if "[REDACTED_VALUE]" not in rhs and "[REDACTED_GUID]" not in rhs:
                findings.append("literal_channel_guid_assignment")
                break
    return sorted(set(findings))


def sanitize_zip_file(path: Path, secret_names: set[str]) -> bool:
    try:
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                nested = info.filename.replace("\\", "/")
                if is_excluded_path(nested):
                    return False
                try:
                    text = zf.read(info).decode("utf-8")
                except (UnicodeDecodeError, OSError, RuntimeError):
                    continue
                if suspicious_text(scrub_text(text, secret_names)):
                    return False
    except (zipfile.BadZipFile, OSError):
        return False
    return True


def api_get(url: str, token: str) -> dict[str, Any]:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
               "User-Agent": "RubikaAgent-HandoffBundle/1.0"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=18) as response:
        raw = response.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise RuntimeError("github_api_response_too_large")
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("github_api_response_invalid")
    return value


def cell(value: Any, limit: int = 140) -> str:
    return " ".join(str(value or "").split()).replace("|", "/").replace(chr(96), "'")[:limit]


def failed_steps(run_id: int, token: str) -> str:
    try:
        result = api_get(f"{API}/repos/{REPO}/actions/runs/{run_id}/jobs?per_page=100", token)
    except Exception:
        return "جزئیات job قابل دریافت نبود."
    failed = []
    for job in result.get("jobs", []):
        if not isinstance(job, dict):
            continue
        steps = [cell(s.get("name"), 90) for s in job.get("steps", [])
                 if isinstance(s, dict) and s.get("conclusion") in {"failure", "cancelled", "timed_out"}]
        if job.get("conclusion") not in {"success", "skipped"} or steps:
            name = cell(job.get("name") or "job", 90)
            failed.append(f"{name}: " + (", ".join(steps[:5]) if steps else cell(job.get("conclusion"))))
    return "; ".join(failed)[:500] if failed else "گامی با نتیجهٔ شکست ثبت نشده است."


def collect_history(source: Path, token: str, current_run_id: str) -> str:
    folder = source / ".github" / "workflows"
    files = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in {".yml", ".yaml"}) if folder.is_dir() else []
    if not files:
        return "فایل workflow در snapshot پیدا نشد."
    try:
        result = api_get(f"{API}/repos/{REPO}/actions/workflows?per_page=100", token)
        ids = {str(w.get("path")): int(w["id"]) for w in result.get("workflows", [])
               if isinstance(w, dict) and w.get("path") and str(w.get("id", "")).isdigit()}
    except Exception:
        return "سوابق اجرا قابل دریافت نبود؛ مجوز read-only به Actions بررسی شود."
    out = ["_خلاصهٔ امن اجراهای شاخهٔ main؛ لاگ خام یا مقادیر env کپی نمی‌شوند._", ""]
    for path in files:
        rel = path.relative_to(source).as_posix()
        wid = ids.get(rel)
        out.append(f"### {rel}")
        if wid is None:
            out.extend(["- شناسهٔ workflow در API پیدا نشد.", ""])
            continue
        try:
            listing = api_get(f"{API}/repos/{REPO}/actions/workflows/{wid}/runs?branch=main&per_page=10", token)
            runs = [r for r in listing.get("workflow_runs", [])
                    if isinstance(r, dict) and str(r.get("id", "")) != str(current_run_id)][:5]
        except Exception:
            out.extend(["- اجرای قبلی دریافت نشد (خطای API/مجوز).", ""])
            continue
        if not runs:
            out.extend(["- اجرای قابل گزارش در main موجود نیست.", ""])
            continue
        out.extend(["| زمان UTC | رویداد | نتیجه | SHA | job/step شکست‌خورده | اجرا |",
                    "|---|---|---|---|---|---|"])
        for run in runs:
            conclusion = cell(run.get("conclusion") or run.get("status") or "unknown", 24)
            rid = run.get("id")
            url = str(run.get("html_url") or "")
            if not re.fullmatch(r"https://github\.com/hosseinasgari898989-dev/rubika-agent/actions/runs/\d+", url):
                url = ""
            detail = failed_steps(int(rid), token) if rid and conclusion in {"failure", "cancelled", "timed_out"} else "—"
            link = f"[#{rid}]({url})" if url else f"#{rid}"
            out.append(f"| {cell(run.get('created_at'),30)} | {cell(run.get('event'),24)} | {conclusion} | {cell(str(run.get('head_sha') or '')[:8],8)} | {cell(detail,480)} | {link} |")
        out.append("")
        time.sleep(0.06)
    return "\n".join(out).strip()


def replace_section(doc: str, section: str, content: str) -> str:
    start, end = AUTO_SECTIONS[section]
    if start not in doc or end not in doc:
        return doc + "\n\n" + content + "\n"
    before, rest = doc.split(start, 1)
    _, after = rest.split(end, 1)
    return before + start + "\n" + content.strip() + "\n" + end + after


def make_rubka_zip(package_dir: Path, dest: Path, secret_names: set[str]) -> None:
    if not package_dir.is_dir():
        raise RuntimeError("rubka_package_directory_missing")
    files = sorted(p for p in package_dir.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc")
    if not files:
        raise RuntimeError("rubka_package_empty")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in files:
            rel = Path("rubka") / path.relative_to(package_dir)
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                zf.write(path, rel.as_posix())
                continue
            cleaned = scrub_text(text, secret_names)
            if suspicious_text(cleaned):
                raise RuntimeError("rubka_package_secret_scan_failed")
            zf.writestr(rel.as_posix(), cleaned.encode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--rubka-path", required=True, type=Path)
    parser.add_argument("--source-sha", default="unknown")
    args = parser.parse_args()
    source, output = args.source.resolve(), args.output.resolve()
    if not source.is_dir() or output == source or source in output.parents:
        raise SystemExit("invalid source/output paths")
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    secret_names, variable_names = discover_secret_var_names(source)
    copied, excluded = [], 0
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(source).as_posix()
        if is_excluded_path(rel) or ".git" in path.parts:
            excluded += 1
            continue
        dest = output / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            raw = path.read_bytes()
        except OSError:
            excluded += 1
            continue
        if path.suffix.lower() == ".zip":
            if not sanitize_zip_file(path, secret_names):
                excluded += 1
                continue
            shutil.copyfile(path, dest)
        else:
            try:
                decoded = raw.decode("utf-8")
                text_file = "\x00" not in decoded
            except UnicodeDecodeError:
                text_file, decoded = False, ""
            if text_file:
                cleaned = scrub_text(decoded, secret_names)
                findings = suspicious_text(cleaned)
                if findings:
                    raise SystemExit(f"security scan blocked bundle: {rel} ({','.join(findings)})")
                dest.write_text(cleaned, encoding="utf-8", newline="")
            else:
                binary = raw.decode("latin-1", errors="ignore")
                if PRIVATE_KEY_RE.search(binary) or any(p.search(binary) for p in TOKEN_PATTERNS):
                    excluded += 1
                    continue
                shutil.copyfile(path, dest)
        copied.append(rel)

    handoff = output / "docs" / "HANDOFF_FULL.md"
    if not handoff.exists():
        raise SystemExit("docs/HANDOFF_FULL.md missing from source")
    doc = handoff.read_text(encoding="utf-8")
    doc = re.sub(r"(?m)^- SHA دقیق، زمان تولید، شمار فایل‌ها و نتیجهٔ اسکن امنیتی در نسخهٔ داخل Artifact درج می‌شود\.$",
                 f"- SHA دقیق منبع: {args.source_sha}؛ زمان تولید UTC: {datetime.now(timezone.utc).isoformat(timespec='seconds')}.", doc)
    doc = replace_section(doc, "secrets", "\n".join(f"- {x}" for x in sorted(secret_names)) or "- ارجاعی کشف نشد.")
    doc = replace_section(doc, "vars", "\n".join(f"- {x}" for x in sorted(variable_names)) or "- ارجاعی کشف نشد.")
    doc = replace_section(doc, "history", collect_history(source, os.getenv("GITHUB_TOKEN", ""), os.getenv("GITHUB_RUN_ID", "")))
    handoff.write_text(doc, encoding="utf-8", newline="")
    rubka_zip = output / "vendor" / "rubka-library.zip"
    make_rubka_zip(args.rubka_path.resolve(), rubka_zip, secret_names)
    if suspicious_text(handoff.read_text(encoding="utf-8")) or not sanitize_zip_file(rubka_zip, secret_names):
        raise SystemExit("security scan blocked generated handoff output")

    manifest = {
        "repository": REPO, "source_branch": "main", "source_sha": args.source_sha,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "files_copied": len(copied) + 2, "files_excluded": excluded,
        "security_scan": "passed", "secrets_values_embedded": False, "raw_git_history_included": False,
        "rubka_library": "rubka==8.1.7",
        "rubka_library_zip_sha256": hashlib.sha256(rubka_zip.read_bytes()).hexdigest(),
        "workflow_history": "last five main-branch runs per workflow; safe summaries only",
    }
    (output / "HANDOFF_MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"ok": True, "source_sha": args.source_sha, "files_copied": manifest["files_copied"],
                      "files_excluded": excluded, "security_scan": "passed",
                      "rubka_library_zip_bytes": rubka_zip.stat().st_size}, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
