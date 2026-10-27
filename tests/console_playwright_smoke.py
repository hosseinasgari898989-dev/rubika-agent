#!/usr/bin/env python3
"""Offline Playwright smoke test for the Rubika-Agent v2 web console.

All Worker API requests are intercepted and answered by this test. No live
Worker, D1 database, Rubika channel, or credential is contacted.
"""
from __future__ import annotations

import json
import sys
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright


MOCK_BASE = "https://mock-worker.test"
CONSOLE_URL = "http://127.0.0.1:8765/docs/console/v2.html"
PAGES = [
    ("dash", "داشبورد"),
    ("items", "آیتم‌ها"),
    ("queue", "صف انتشار"),
    ("hist", "تاریخچه و لاگ"),
    ("ai", "تماس‌های AI"),
    ("src", "منابع و لینک‌ها"),
    ("owners", "مالک‌ها"),
    ("rules", "قوانین"),
    ("chan", "کانال‌ها"),
    ("jobs", "کارها"),
    ("inst", "حالت فوری"),
    ("d1", "دیتابیس"),
    ("soon", "به‌زودی"),
    ("set", "اتصال"),
]
NOW = "2026-10-27T00:00:00Z"
CORS = {
    "access-control-allow-origin": "*",
    "access-control-allow-methods": "GET,POST,PATCH,DELETE,OPTIONS",
    "access-control-allow-headers": "authorization,content-type",
}


def fixture_api(path: str, method: str, body: dict) -> dict:
    """Deterministic mock responses matching the console's API contracts."""
    if path == "/api/status":
        return {
            "ok": True,
            "agent": {"enabled": 1},
            "item_counts": [{"total": 3}],
            "recent_runs": [{
                "started_at": NOW, "trigger_type": "fixture", "status": "succeeded",
                "found_count": 1, "eligible_count": 1, "sent_count": 0,
                "skipped_count": 0, "failed_count": 0, "summary": "mock only",
            }],
        }
    if path == "/api/brain/owner-console/queue-summary":
        return {"ok": True, "queue_counts": {"queued": 1, "failed": 0, "uncertain": 0}}
    if path == "/api/brain/ai-provider/status":
        return {
            "ok": True, "ready": True, "cooldown_active": False,
            "calls_used": 1, "daily_limit": 100, "remaining": 99,
            "last_error_code": None,
        }
    if path == "/api/console/items":
        return {"ok": True, "items": [{
            "id": "fixture-item-1", "platform": "youtube", "title": "Fixture video",
            "caption": "Fixture caption", "persian_translation": "ترجمهٔ آزمایشی",
            "canonical_url": "https://youtube.com/watch?v=fixture000",
            "status": "discovered", "review_state": "needs_review",
            "updated_at": NOW, "discovered_at": NOW,
        }], "total": 1}
    if path == "/api/console/d1/query":
        sql = str(body.get("sql", "")).lower()
        if "from automation_queue" in sql:
            return {
                "ok": True,
                "columns": ["id", "platform", "title", "status", "last_error_code", "attempt_count", "updated_at"],
                "rows": [["fixture-queue-1", "youtube", "Fixture queued video", "queued", None, 0, NOW]],
                "duration_ms": 1,
            }
        if "from \"items\"" in sql or "from items" in sql:
            return {
                "ok": True, "columns": ["id", "title", "platform"],
                "rows": [["fixture-item-1", "Fixture video", "youtube"]], "duration_ms": 1,
            }
        return {"ok": True, "columns": ["id"], "rows": [], "duration_ms": 1}
    if path == "/api/console/runs":
        return {"ok": True, "items": [{
            "started_at": NOW, "trigger_type": "fixture", "status": "succeeded",
            "found_count": 1, "eligible_count": 1, "sent_count": 0,
            "skipped_count": 0, "failed_count": 0, "summary": "mock only",
        }]}
    if path == "/api/console/events":
        return {"ok": True, "items": [{
            "occurred_at": NOW, "category": "fixture", "source": "mock",
            "title": "Fixture event", "detail": "Mock response only",
        }]}
    if path == "/api/console/brain-events":
        return {"ok": True, "items": [{
            "created_at": NOW, "level": "info", "code": "fixture", "details_json": "{}",
        }]}
    if path == "/api/console/delivery-attempts":
        return {"ok": True, "items": [{
            "started_at": NOW, "item_id": "fixture-item-1",
            "attempt_number": 1, "status": "skipped", "error_code": None,
        }]}
    if path == "/api/console/audit":
        return {"ok": True, "items": [{
            "created_at": NOW, "code": "fixture", "details_json": "{}",
        }]}
    if path == "/api/console/ai/calls":
        return {"ok": True, "items": [{
            "started_at": NOW, "provider": "groq", "role": "translate_official",
            "status": "succeeded", "error_code": None, "total_tokens": 12,
            "cooldown_until": None,
        }]}
    if path == "/api/console/sources":
        if method == "GET":
            return {"ok": True, "items": [{
                "id": 1, "platform": "tiktok", "source_key": "@fixture",
                "display_name": "Fixture source", "enabled": 1, "last_checked_at": NOW,
            }]}
        return {"ok": True, "id": 2}
    if path == "/api/console/links":
        if method == "GET":
            return {"ok": True, "items": [{
                "id": 1, "url": "https://x.com/mobgamesstudios/status/123456789",
                "status": "pending", "created_at": NOW,
            }]}
        return {"ok": True, "link": {
            "id": 2, "url": body.get("url", "https://x.com/mobgamesstudios/status/123456789"),
            "status": "pending", "created_at": NOW,
        }}
    if path == "/api/console/links/clear":
        return {"ok": True, "removed": 1}
    if path.startswith("/api/console/links/") and path.endswith("/confirm"):
        return {"ok": True, "job": {"id": "fixture-job-2"}}
    if path.startswith("/api/console/links/") and path.endswith("/cancel"):
        return {"ok": True}
    if path == "/api/console/owners/invite":
        return {"ok": True, "invite_code": "FIXTURE-INVITE", "expires_at": NOW}
    if path == "/api/console/owners":
        if method == "GET":
            return {"ok": True, "items": [{
                "id": 1, "chat_id": "mock-chat-1234", "label": "Fixture owner",
                "role": "owner", "enabled": 1, "last_seen_at": NOW,
            }]}
        return {"ok": True, "id": 2}
    if path.startswith("/api/console/owners/"):
        return {"ok": True}
    if path == "/api/console/rules":
        return {
            "ok": True, "version": 1,
            "rules": {
                "daily_success_cap": 4, "daily_edit_count": 6,
                "mob_posts_per_run": 1, "freshness_hours": 24,
                "ai_daily_limit_groq": 100, "ai_daily_limit_openrouter": 100,
                "caption_max_chars": 500, "ai_provider_order": ["groq", "openrouter"],
                "quiet_hours": {"start": "00:30", "end": "07:00", "enabled": True},
                "digest_window_utc": "07:05", "mob_since_cutoff": "2026-10-20T00:00:00Z",
                "translate_enabled": True,
            },
        }
    if path == "/api/console/channels":
        if method == "GET":
            return {"ok": True, "items": [{
                "id": 1, "label": "Fixture TEST channel",
                "rubika_channel_id": "mock-test-channel", "role": "test",
                "enabled": 1, "created_at": NOW,
            }]}
        return {"ok": True, "id": 2}
    if path == "/api/console/jobs":
        if method == "GET":
            return {"ok": True, "items": [{
                "id": "fixture-job-1", "type": "mob_x_link", "status": "queued",
                "progress": 0, "created_at": NOW, "error": None,
                "panel_link_id": 1, "result": {},
            }]}
        return {"ok": True, "job": {"id": "fixture-job-2", "status": "queued"}}
    if path.startswith("/api/console/jobs/"):
        return {"ok": True, "job": {
            "id": path.rsplit("/", 1)[-1], "type": "mob_x_link",
            "status": "queued", "progress": 0, "created_at": NOW,
            "error": None, "panel_link_id": 1, "result": {},
        }}
    if path == "/api/console/d1/tables":
        return {"ok": True, "items": [
            {"name": "items", "row_count": 1, "columns": [{"name": "id"}, {"name": "title"}]},
            {"name": "automation_queue", "row_count": 1, "columns": [{"name": "id"}, {"name": "status"}]},
        ]}
    return {"ok": True, "items": []}


def main() -> int:
    failures: list[str] = []
    browser_errors: list[str] = []
    failed_requests: list[str] = []
    active_page = ["startup"]
    mocked_requests: list[str] = []

    def mock_worker(route):
        req = route.request
        parts = urlsplit(req.url)
        mocked_requests.append(f"{req.method} {parts.path}")
        if req.method == "OPTIONS":
            route.fulfill(status=204, headers=CORS, body="")
            return
        try:
            body = req.post_data_json if req.post_data else {}
            if not isinstance(body, dict):
                body = {}
            payload = fixture_api(parts.path, req.method, body)
            route.fulfill(
                status=200, content_type="application/json",
                headers=CORS, body=json.dumps(payload, ensure_ascii=False),
            )
        except Exception as exc:  # Keep the failure visible in the test report.
            route.fulfill(
                status=500, content_type="application/json",
                headers=CORS,
                body=json.dumps({"ok": False, "error": f"mock_fixture_error:{exc}"}),
            )
            failures.append(f"mock Worker {req.method} {parts.path}: {exc}")

    def click_action(page, selector: str, label: str, *, first: bool = True) -> None:
        page.locator("#toast").evaluate("(el) => el.innerHTML = ''")
        target = page.locator(selector)
        if first:
            target = target.first
        target.click(timeout=2500)
        page.wait_for_timeout(120)
        bad = page.locator("#toast .t.bad").all_text_contents()
        if bad:
            failures.append(f"{active_page[0]} action '{label}' returned error toast: {' | '.join(bad)}")

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context()
        context.add_init_script(
            "localStorage.setItem('rc_base', 'https://mock-worker.test');"
            "localStorage.setItem('rc_token', 'playwright-fixture-only');"
        )
        page = context.new_page()
        page.route(f"{MOCK_BASE}/**", mock_worker)
        page.on("pageerror", lambda exc: browser_errors.append(f"{active_page[0]} uncaught JS: {exc}"))
        page.on(
            "console",
            lambda msg: browser_errors.append(f"{active_page[0]} console.error: {msg.text}")
            if msg.type == "error" and any(
                word in msg.text for word in (
                    "SyntaxError", "TypeError", "ReferenceError", "Uncaught",
                    "CORS", "Failed to fetch", "net::ERR",
                )
            ) else None,
        )
        page.on(
            "requestfailed",
            lambda req: failed_requests.append(f"{active_page[0]} request failed: {req.method} {req.url}")
            if "mock-worker.test" in req.url else None,
        )

        try:
            page.goto(CONSOLE_URL, wait_until="domcontentloaded", timeout=15000)
            page.wait_for_function("() => !!window.RC && !!window.RC.A && !!window.RC.V", timeout=10000)
            page.wait_for_function("() => !document.querySelector('#view .sk')", timeout=10000)

            for page_id, title in PAGES:
                active_page[0] = page_id
                try:
                    nav = page.locator(f'#nav button[data-id="{page_id}"]')
                    if nav.count() != 1:
                        failures.append(f"{page_id}: expected one navigation entry, found {nav.count()}")
                        continue
                    nav.click(timeout=2500)
                    page.wait_for_function(
                        "(expected) => document.querySelector('#ttl')?.textContent === expected",
                        arg=title, timeout=5000,
                    )
                    page.wait_for_function("() => !document.querySelector('#view .sk')", timeout=5000)
                    page.wait_for_timeout(40)

                    text = page.locator("#view").inner_text().strip()
                    if not text:
                        failures.append(f"{page_id}: page rendered empty content")
                    if "⚠ خطا" in page.locator("#view").inner_text():
                        failures.append(f"{page_id}: page rendered the console error panel")

                    # Every enabled control advertised as an action must have a real handler.
                    controls = page.locator("#view [data-a]:not(:disabled)")
                    for i in range(controls.count()):
                        action = controls.nth(i).get_attribute("data-a") or ""
                        bound = page.evaluate(
                            "(action) => typeof window.RC?.A?.[action] === 'function'", action
                        )
                        if not bound:
                            failures.append(f"{page_id}: control data-a='{action}' has no handler")

                except Exception as exc:
                    failures.append(f"{page_id}: could not open/render page: {exc}")

            # Exercise a representative real control on every API-backed page.
            actions = [
                ("dash", lambda: click_action(page, '.top button[data-a="theme"]', "theme")),
                ("items", lambda: click_action(page, '#view button[data-a="iFilter"]', "filter items")),
                ("queue", lambda: click_action(page, '#view button[data-a="qF"][data-s="queued"]', "queue filter")),
                ("hist", lambda: click_action(page, '#view button[data-a="hTab"][data-k="events"]', "history tab")),
                ("ai", lambda: click_action(page, '#view button[data-a="aF"]', "AI filter")),
            ]
            for page_id, action in actions:
                active_page[0] = page_id
                try:
                    page.locator(f'#nav button[data-id="{page_id}"]').click()
                    page.wait_for_function(
                        "(expected) => document.querySelector('#ttl')?.textContent === expected",
                        arg=dict(PAGES)[page_id], timeout=5000,
                    )
                    page.wait_for_function("() => !document.querySelector('#view .sk')", timeout=5000)
                    action()
                except Exception as exc:
                    failures.append(f"{page_id}: interaction failed: {exc}")

            # Source create + X-link create; fake endpoint means no persistent changes.
            active_page[0] = "src"
            try:
                page.locator('#nav button[data-id="src"]').click()
                page.locator("#s_k").fill("@playwright-fixture")
                page.locator("#s_d").fill("Fixture source")
                click_action(page, '#view button[data-a="sAdd"]', "add source")
                page.locator("#l_u").fill("https://x.com/mobgamesstudios/status/123456789")
                click_action(page, '#view button[data-a="lAdd"]', "add X link")
            except Exception as exc:
                failures.append(f"src: create action failed: {exc}")

            active_page[0] = "owners"
            try:
                page.locator('#nav button[data-id="owners"]').click()
                page.locator("#o_c").fill("mock-chat-9876")
                page.locator("#o_l").fill("Playwright fixture")
                click_action(page, '#view button[data-a="oAdd"]', "add owner")
            except Exception as exc:
                failures.append(f"owners: create action failed: {exc}")

            active_page[0] = "rules"
            try:
                page.locator('#nav button[data-id="rules"]').click()
                page.locator('#view button[data-a="pSet"]').first.click()
                click_action(page, '#view button[data-a="rSave"]', "save rules")
            except Exception as exc:
                failures.append(f"rules: save action failed: {exc}")

            active_page[0] = "chan"
            try:
                page.locator('#nav button[data-id="chan"]').click()
                page.locator("#c_i").fill("mock-test-channel-new")
                page.locator("#c_l").fill("Fixture test channel")
                click_action(page, '#view button[data-a="cAdd"]', "add test channel")
            except Exception as exc:
                failures.append(f"chan: create action failed: {exc}")

            active_page[0] = "jobs"
            try:
                page.locator('#nav button[data-id="jobs"]').click()
                page.locator('#view button[data-a="jView"]').first.click()
                page.wait_for_timeout(80)
                if page.locator("#modal").is_visible():
                    click_action(page, '#modal button[data-a="close"]', "close job details")
                page.locator('#nav button[data-id="jobs"]').click()
                page.locator('#view button[data-a="jAdd"]').click()
                page.wait_for_timeout(120)
                if page.locator("#toast .t.bad").count():
                    failures.append("jobs: add job returned an error toast")
            except Exception as exc:
                failures.append(f"jobs: details/create action failed: {exc}")

            active_page[0] = "inst"
            try:
                page.locator('#nav button[data-id="inst"]').click()
                page.locator("#ins_u").fill("https://x.com/mobgamesstudios/status/123456789")
                click_action(page, '#view button[data-a="insAdd"]', "save instant Mob X link")
                click_action(page, '#view button[data-a="insOk"]', "confirm instant Mob X link")
            except Exception as exc:
                failures.append(f"inst: link confirmation failed: {exc}")

            active_page[0] = "d1"
            try:
                page.locator('#nav button[data-id="d1"]').click()
                click_action(page, '#view button[data-tbl="items"]', "select D1 table")
                click_action(page, '#view button[data-a="dRun"]', "run mocked SELECT")
            except Exception as exc:
                failures.append(f"d1: SELECT interaction failed: {exc}")

            active_page[0] = "set"
            try:
                page.locator('#nav button[data-id="set"]').click()
                click_action(page, '#view button[data-a="sTest"]', "test mocked Worker connection")
            except Exception as exc:
                failures.append(f"set: connection test failed: {exc}")

            page.wait_for_timeout(150)
        finally:
            context.close()
            browser.close()

    failures.extend(browser_errors)
    failures.extend(failed_requests)
    report = {
        "result": "PASS" if not failures else "FAIL",
        "pages_checked": len(PAGES),
        "mock_worker_requests": len(mocked_requests),
        "js_errors": browser_errors,
        "failed_mock_requests": failed_requests,
        "failures": failures,
        "network": "all Worker API calls mocked; no live Worker/D1/Rubika calls",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
