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
CONSOLE_URL = "http://127.0.0.1:8765/console/"
LEGACY_URL = "http://127.0.0.1:8765/docs/console/legacy.html"
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
    ("report", "گزارش"),
    ("inst", "حالت فوری"),
    ("d1", "دیتابیس"),
    ("soon", "به‌زودی"),
    ("set", "اتصال"),
    ("unsent", "ارسال‌نشده‌ها"),
    ("tkaccounts", "حساب‌های TikTok"),
    ("tiktok-unsent", "تیک‌تاک - ارسال‌نشده"),
]
NOW = "2026-10-27T00:00:00Z"
MEGA_KEY = "A" * 43
MEGA_URL = f"https://mega.nz/file/AbCd1234#{MEGA_KEY}"
MEGA_EMBED_URL = f"https://mega.nz/embed/AbCd1234#{MEGA_KEY}"
CAPTION_LITERAL = "Special & ' < > literal"
CORS = {
    "access-control-allow-origin": "*",
    "access-control-allow-methods": "GET,POST,PATCH,DELETE,OPTIONS",
    "access-control-allow-headers": "authorization,content-type",
}


MOB_REVIEW_JOBS: dict[str, dict] = {}
MOB_REVIEW_ACTIONS: list[dict] = []
TIKTOK_DELETE_BODIES: list[dict] = []
MOB_NO_CAPTION = "**ماب انترتینمنت در پیج خود پستی منتشر کرد.**"


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
    if path == "/api/console/digest":
        return {
            "ok": True,
            "window": {
                "started_at": "2026-11-02T07:05:00Z",
                "ends_at": "2026-11-03T07:05:00Z",
            },
            "counts": [
                {"category": "success", "count": 4},
                {"category": "ai_error", "count": 1},
            ],
            "events": [
                {
                    "occurred_at": NOW, "category": "success",
                    "source": "x", "title": f"Mob X report fixture {CAPTION_LITERAL}",
                },
                {
                    "occurred_at": NOW, "category": "ai_error",
                    "source": "openrouter", "title": "Translation report fixture",
                },
            ],
        }
    if path == "/api/console/items":
        return {"ok": True, "items": [{
            "id": "fixture-item-1", "platform": "tiktok", "title": "Fixture video",
            "caption": "Fixture caption", "persian_translation": "ترجمهٔ آزمایشی",
            "canonical_url": "https://www.tiktok.com/@fixture/video/7616247520657771796",
            "status": "discovered", "review_state": "needs_review",
            "updated_at": NOW, "discovered_at": NOW,
        }], "total": 1}
    if path == "/api/console/d1/query":
        sql = str(body.get("sql", "")).lower()
        if "from automation_queue" in sql:
            return {
                "ok": True,
                "columns": ["id", "platform", "title", "status", "last_error_code", "attempt_count", "updated_at"],
                "rows": [["fixture-queue-1", "tiktok", "Fixture queued video", "queued", None, 0, NOW]],
                "duration_ms": 1,
            }
        if "from \"items\"" in sql or "from items" in sql:
            return {
                "ok": True, "columns": ["id", "title", "platform"],
                "rows": [["fixture-item-1", "Fixture video", "tiktok"]], "duration_ms": 1,
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
                "id": "tiktok:fixture", "platform": "tiktok", "source_key": "fixture",
                "display_name": "Fixture source", "enabled": 1,
                "created_at": "2026-11-05T18:00:00Z", "last_checked_at": NOW,
                "managed": True,
            }]}
        return {"ok": True, "id": "tiktok:newfixture"}
    if path.startswith("/api/console/sources/") and method == "DELETE":
        return {"ok": True, "history_preserved": True}
    if path == "/api/console/tiktok-unsent":
        return {"ok": True, "items": [{
            "id": "tiktok:7616247520657771796", "item_id": "tiktok:7616247520657771796",
            "account": "fixture", "caption": "Fixture failed TikTok video",
            "reason": "rubika_rejected_upload_failed", "created_at": NOW,
            "status": "stored", "queue_status": "failed", "already_sent": 0, "can_resend": 1,
            "failed_media_id": "fm_" + "a" * 24, "original_share_url": MEGA_URL, "failed_media_status": "stored",
        }, {
            "id": "tiktok:7616247520657771797", "item_id": "tiktok:7616247520657771797",
            "account": "fixture", "caption": "Fixture already sent video",
            "reason": "delivery_receipt_missing", "created_at": NOW,
            "status": "stored", "queue_status": "sent", "already_sent": 1, "can_resend": 0,
            "failed_media_id": "fm_" + "b" * 24, "original_share_url": "https://example.com/file/AbCd1234#bad", "failed_media_status": "stored",
        }]}
    if path.startswith("/api/console/tiktok-unsent/") and path.endswith("/resend") and method == "POST":
        return {"ok": True, "queued": True, "status": "queued"}
    if path == "/api/console/failed-media/delete-request" and method == "POST":
        TIKTOK_DELETE_BODIES.append(dict(body))
        if "confirmation" in body:
            return {"ok": True, "requested": 1, "dispatched": True}
        return {"ok": True, "requested": 1, "confirmation_required": True, "confirmation": "fixture-signed-delete-challenge"}
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
    if path.startswith("/api/console/jobs/") and path.endswith("/mob-x-review") and method == "POST":
        job_id = path.split("/")[-2]
        job = MOB_REVIEW_JOBS.get(job_id)
        if not job or job["status"] != "ready_for_review":
            return {"ok": False, "error": "invalid_state_for_action"}
        action = body.get("action")
        if action == "edit_caption" and isinstance(body.get("caption"), str) and body["caption"].strip():
            job["result"]["caption"] = body["caption"].strip()
            job["result"]["caption_mode"] = "edited"
        elif action == "no_caption":
            job["result"]["caption"] = MOB_NO_CAPTION
            job["result"]["caption_mode"] = "no_caption"
            job["result"]["original_caption"] = ""
            job["result"]["persian_translation"] = ""
        elif action == "reject":
            job["status"] = "rejected"
            job["link_status"] = "rejected"
            job["item_review_state"] = "rejected"
        else:
            return {"ok": False, "error": "invalid_action"}
        if action in ("edit_caption", "no_caption"):
            job["result"]["preview_sent"] = False
            job["result"]["caption_refresh_state"] = "not_refreshed"
        MOB_REVIEW_ACTIONS.append({"job_id": job_id, "action": action, "caption": body.get("caption")})
        return {"ok": True, "action": action, "status": job["status"], "caption": job["result"].get("caption")}
    if path == "/api/console/jobs":
        if method == "GET":
            return {"ok": True, "items": [
                {
                    "id": "fixture-job-1", "type": "mob_x_link", "status": "queued",
                    "progress": 0, "created_at": NOW, "error": None,
                    "panel_link_id": 1, "result": {},
                },
                {
                    "id": "fixture-mob-ready", "type": "mob_x_link", "status": "ready_for_review",
                    "progress": 100, "created_at": NOW, "error": None,
                    "panel_link_id": 2, "result": MOB_REVIEW_JOBS["fixture-mob-ready"]["result"],
                },
            ]}
        return {"ok": True, "job": {"id": "fixture-job-2", "status": "queued"}}
    if path.startswith("/api/console/jobs/"):
        job_id = path.rsplit("/", 1)[-1]
        job = MOB_REVIEW_JOBS.get(job_id)
        if job:
            return {"ok": True, "job": {
                "id": job_id, "type": job["type"], "status": job["status"],
                "progress": job["progress"], "created_at": NOW, "error": None,
                "panel_link_id": job["panel_link_id"], "result": job["result"],
            }}
        return {"ok": True, "job": {
            "id": job_id, "type": "mob_x_link", "status": "queued", "progress": 0,
            "created_at": NOW, "error": None, "panel_link_id": 1, "result": {},
        }}
    if path == "/api/console/failed-media/list":
        return {"ok": True, "total": 2, "items": [
            {
                "id": "fixture-failed-valid", "status": "stored", "source": "mob_x",
                "media_type": "video", "caption": "Preview fixture",
                "reason": "rubika_send_failed", "size_bytes": 1048576,
                "created_at": NOW, "share_url": MEGA_URL,
            },
            {
                "id": "fixture-failed-invalid", "status": "stored", "source": "mob_x",
                "media_type": "video", "caption": "Invalid link fixture",
                "reason": "rubika_send_failed", "size_bytes": 2048,
                "created_at": NOW,
                "share_url": "https://example.com/file/AbCd1234#ABCDEFGHIJKLMNOPQRSTUV",
            },
        ]}
    if path == "/api/console/d1/tables":
        return {"ok": True, "items": [
            {"name": "items", "row_count": 1, "columns": [{"name": "id"}, {"name": "title"}]},
            {"name": "automation_queue", "row_count": 1, "columns": [{"name": "id"}, {"name": "status"}]},
        ]}
    return {"ok": True, "items": []}


def main() -> int:
    global MOB_REVIEW_JOBS, MOB_REVIEW_ACTIONS, TIKTOK_DELETE_BODIES
    MOB_REVIEW_ACTIONS = []
    TIKTOK_DELETE_BODIES = []
    MOB_REVIEW_JOBS = {
        "fixture-mob-ready": {
            "type": "mob_x_link", "status": "ready_for_review", "progress": 100,
            "panel_link_id": 2, "link_status": "ready_for_review", "item_review_state": "pending",
            "result": {
                "item_id": "mob_official:playwright-review", "external_id": "2107878755079213476",
                "rubika_file_id": "mock-rubika-file", "media_type": "image",
                "caption": CAPTION_LITERAL, "caption_mode": "generated",
                "original_caption": "Original text", "persian_translation": "ترجمهٔ اولیه",
                "preview_sent": True,
            },
        },
    }
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
        if parts.path.startswith("/api/console/"):
            runner_posts = {
                "/api/console/mob-x/claim", "/api/console/mob-x/publish-claim",
                "/api/console/failed-media/record", "/api/console/failed-media/list",
                "/api/console/failed-media/pending-delete", "/api/console/failed-media/delete-result",
                "/api/console/d1/query",
            }
            owner_mutation = req.method not in ("GET", "HEAD", "OPTIONS") and parts.path not in runner_posts
            if parts.path.startswith("/api/console/mob-x/jobs/") and parts.path.rsplit("/", 1)[-1] in ("transition", "preview", "delivery"):
                owner_mutation = False
            expected = "Bearer playwright-owner-fixture-only" if owner_mutation else "Bearer playwright-fixture-only"
            if req.headers.get("authorization") != expected:
                failures.append(f"wrong bearer for {req.method} {parts.path}")
                route.fulfill(
                    status=403, content_type="application/json", headers=CORS,
                    body=json.dumps({"ok": False, "error": "wrong_fixture_bearer"}),
                )
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
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        context.add_init_script(
            "localStorage.setItem('rc_base', 'https://mock-worker.test');"
            "localStorage.setItem('rc_token', 'playwright-fixture-only');"
            "localStorage.setItem('rc_owner_token', 'playwright-owner-fixture-only');"
        )
        page = context.new_page()
        page.route(f"{MOCK_BASE}/**", mock_worker)
        # Never contact the live MEGA embed from the mocked smoke test.
        page.route(
            "https://mega.nz/embed/**",
            lambda route: route.fulfill(
                status=200, content_type="text/html",
                body="<title>Mock MEGA embed</title>",
            ),
        )
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
            page.goto(f"{CONSOLE_URL}#/dash", wait_until="domcontentloaded", timeout=15000)
            page.wait_for_function("() => !!window.RC && !!window.RC.A && !!window.RC.V", timeout=10000)
            entry_path, entry_hash = page.evaluate("() => [location.pathname, location.hash]")
            if not entry_path.endswith("/docs/console/v2.html"):
                failures.append(f"console entrypoint did not redirect to v2.html: {entry_path}")
            if entry_hash != "#/dash":
                failures.append(f"console entrypoint did not preserve the requested hash: {entry_hash}")
            page.wait_for_function("() => { const v = document.querySelector('#view'); return !!v && !v.querySelector('.sk') && !!v.querySelector('.card') && (v.innerText || '').trim().length > 0; }", timeout=10000)

            # Leave the auto-selected startup route so every page check exercises a real navigation event.
            page.locator('#nav button[data-id="set"]').click()
            page.wait_for_function(
                "(expected) => document.querySelector('#ttl')?.textContent === expected",
                arg="اتصال", timeout=5000,
            )
            page.wait_for_function("() => { const v = document.querySelector('#view'); return !!v && !v.querySelector('.sk') && !!v.querySelector('.card') && (v.innerText || '').trim().length > 0; }", timeout=5000)

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
                    page.wait_for_function("() => { const v = document.querySelector('#view'); return !!v && !v.querySelector('.sk') && !!v.querySelector('.card') && (v.innerText || '').trim().length > 0; }", timeout=5000)
                    page.wait_for_timeout(40)

                    text = page.locator("#view").inner_text().strip()
                    if not text:
                        failures.append(f"{page_id}: page rendered empty content")
                    if "⚠ خطا" in page.locator("#view").inner_text():
                        failures.append(f"{page_id}: page rendered the console error panel")
                    if page_id == "soon":
                        legacy_link = page.locator('#view a[href="legacy.html"]')
                        if legacy_link.count() != 1 or legacy_link.inner_text().strip() != "نسخهٔ قدیمی کنسول (legacy)":
                            failures.append("soon: the legacy link is missing or misplaced")

                    # Every enabled control advertised as an action must have a real handler.
                    controls = page.locator("#view [data-a]:not(:disabled)")
                    for i in range(controls.count()):
                        action = controls.nth(i).get_attribute("data-a") or ""
                        bound = page.evaluate(
                            "(action) => typeof window.RC?.A?.[action] === 'function'", action
                        )
                        if not bound:
                            failures.append(f"{page_id}: control data-a='{action}' has no handler")

                    dimensions = page.evaluate("() => ({scrollWidth: document.documentElement.scrollWidth, innerWidth: window.innerWidth})")
                    if dimensions["scrollWidth"] > dimensions["innerWidth"]:
                        failures.append(f"{page_id}: horizontal overflow at 1280x800 ({dimensions['scrollWidth']} > {dimensions['innerWidth']})")

                except Exception as exc:
                    failures.append(f"{page_id}: could not open/render page: {exc}")

            # Repeat route overflow coverage at the mobile viewport.
            page.set_viewport_size({"width": 390, "height": 844})
            for page_id, title in PAGES:
                active_page[0] = f"mobile-{page_id}"
                try:
                    page.evaluate("(id) => { location.hash = '#/' + id; }", page_id)
                    page.wait_for_function(
                        "(expected) => document.querySelector('#ttl')?.textContent === expected",
                        arg=title, timeout=5000,
                    )
                    page.wait_for_function("() => { const v = document.querySelector('#view'); return !!v && !v.querySelector('.sk') && !!v.querySelector('.card') && (v.innerText || '').trim().length > 0; }", timeout=5000)
                    dimensions = page.evaluate("() => ({scrollWidth: document.documentElement.scrollWidth, innerWidth: window.innerWidth})")
                    if dimensions["scrollWidth"] > dimensions["innerWidth"]:
                        failures.append(f"{page_id}: horizontal overflow at 390x844 ({dimensions['scrollWidth']} > {dimensions['innerWidth']})")
                except Exception as exc:
                    failures.append(f"{page_id}: mobile route/overflow check failed: {exc}")

            # Direct initial loads must keep the requested route and render its own view.
            direct_routes = [
                ("inst", "حالت فوری"),
                ("report", "گزارش"),
                ("unsent", "ارسال‌نشده‌ها"),
                ("tkaccounts", "حساب‌های TikTok"),
                ("tiktok-unsent", "تیک‌تاک - ارسال‌نشده"),
            ]
            for viewport in ({"width": 390, "height": 844}, {"width": 1280, "height": 800}):
                page.set_viewport_size(viewport)
                for route_id, title in direct_routes:
                    active_page[0] = f"direct-{route_id}-{viewport['width']}"
                    try:
                        page.goto(
                            f"{CONSOLE_URL}?direct={route_id}-{viewport['width']}#/{route_id}",
                            wait_until="domcontentloaded", timeout=15000,
                        )
                        page.wait_for_function("() => !!window.RC && !!window.RC.V && !!window.RC.A", timeout=10000)
                        page.wait_for_function(
                            "(expected) => document.querySelector('#ttl')?.textContent === expected",
                            arg=title, timeout=7000,
                        )
                        page.wait_for_function("() => { const v = document.querySelector('#view'); return !!v && !v.querySelector('.sk') && !!v.querySelector('.card') && (v.innerText || '').trim().length > 0; }", timeout=7000)
                        view_text = page.locator("#view").inner_text()
                        if route_id == "inst" and page.locator("#view #ins_u").count() != 1:
                            failures.append(f"direct inst at {viewport['width']}: instant-link form is missing")
                        if route_id == "report" and "خلاصهٔ ۲۴ ساعت" not in view_text:
                            failures.append(f"direct report at {viewport['width']}: report body missing; dashboard fallback suspected")
                        if route_id == "unsent":
                            if page.locator('#view button[data-a="fmPreview"]').count() != 2:
                                failures.append(f"direct unsent at {viewport['width']}: failed-media controls are missing")
                            if "TikTok" in view_text or "تیک‌تاک" in view_text:
                                failures.append(f"direct unsent at {viewport['width']}: TikTok row leaked into generic unsent section")
                        if route_id == "tkaccounts" and page.locator('#view #tk_account').count() != 1:
                            failures.append(f"direct account page at {viewport['width']}: TikTok account input missing")
                        if route_id == "tiktok-unsent":
                            if page.locator('#view button[data-a="tkPreview"]').count() != 2:
                                failures.append(f"direct TikTok unsent at {viewport['width']}: original preview controls missing")
                            if not page.locator('#view button[data-a="tkPreview"]').nth(0).is_enabled():
                                failures.append(f"direct TikTok unsent at {viewport['width']}: valid MEGA preview is disabled")
                            if page.locator('#view button[data-a="tkPreview"]').nth(1).is_enabled():
                                failures.append(f"direct TikTok unsent at {viewport['width']}: invalid MEGA preview is enabled")
                            if "Fixture failed TikTok video" not in view_text and page.locator('#view button[data-a="tkResend"]').count() != 1:
                                failures.append(f"direct TikTok unsent at {viewport['width']}: dedicated item/action missing")
                            if page.locator('#view button[data-a="tkResend"]').count() != 1:
                                failures.append(f"direct TikTok unsent at {viewport['width']}: sent item must hide its resend button")
                        dimensions = page.evaluate("() => ({scrollWidth: document.documentElement.scrollWidth, innerWidth: window.innerWidth})")
                        if dimensions["scrollWidth"] > dimensions["innerWidth"]:
                            failures.append(f"direct {route_id} at {viewport['width']}: horizontal overflow ({dimensions['scrollWidth']} > {dimensions['innerWidth']})")
                        if route_id == "unsent" and viewport["width"] == 390:
                            checkboxes = page.locator('#view input[type="checkbox"]')
                            if checkboxes.count() == 0:
                                failures.append("mobile unsent: no checkboxes rendered for hit-target checks")
                            for i in range(checkboxes.count()):
                                box = checkboxes.nth(i).bounding_box()
                                if not box or box["width"] < 22 or box["height"] < 22:
                                    failures.append(f"mobile unsent: checkbox {i} is smaller than 22x22 CSS pixels")
                            small_buttons = page.locator("#view .btn.sm")
                            if small_buttons.count() == 0:
                                failures.append("mobile unsent: no .btn.sm controls rendered for hit-target checks")
                            for i in range(small_buttons.count()):
                                box = small_buttons.nth(i).bounding_box()
                                if not box or box["height"] < 40:
                                    failures.append(f"mobile unsent: .btn.sm control {i} is shorter than 40 CSS pixels")
                    except Exception as exc:
                        failures.append(f"direct {route_id} at {viewport['width']} failed: {exc}")

            page.set_viewport_size({"width": 1280, "height": 800})

            # Account add/remove and the dedicated TikTok unsent action use the owner token.
            active_page[0] = "tkaccounts-actions"
            page.locator('#nav button[data-id="tkaccounts"]').click()
            page.wait_for_selector("#tk_account", timeout=5000)
            page.locator("#tk_account").fill("@newfixture")
            click_action(page, '#view button[data-a="tkAdd"]', "add TikTok account")
            if "POST /api/console/sources" not in mocked_requests:
                failures.append("TikTok account add did not use the existing console sources API")
            page.locator('#nav button[data-id="tkaccounts"]').click()
            page.wait_for_selector('#view button[data-a="tkRemove"]', timeout=5000)
            page.once("dialog", lambda dialog: dialog.accept())
            click_action(page, '#view button[data-a="tkRemove"]', "remove TikTok account")
            if "DELETE /api/console/sources/tiktok%3Afixture" not in mocked_requests:
                failures.append("TikTok account remove did not use the existing source API")

            active_page[0] = "tiktok-unsent-actions"
            page.locator('#nav button[data-id="tiktok-unsent"]').click()
            page.wait_for_function(
                "(expected) => document.querySelector('#ttl')?.textContent === expected",
                arg="تیک‌تاک - ارسال‌نشده", timeout=5000,
            )
            page.wait_for_selector('#view button[data-a="tkResend"]', timeout=5000)
            resend = page.locator('#view button[data-a="tkResend"]')
            resend.click()
            page.wait_for_function("() => document.querySelector('#view')?.innerText.includes('در صف')", timeout=5000)
            if sum(1 for item in mocked_requests if item.startswith("POST /api/console/tiktok-unsent/")) != 1:
                failures.append("TikTok resend did not issue exactly one POST")
            if page.locator('#view button[data-a="tkResend"]').count() != 0:
                failures.append("TikTok resend button remained visible after being queued")
            if page.locator('#view .bd.info').filter(has_text="در صف").count() != 1:
                failures.append("TikTok resend did not render exactly one queued status")

            # The dedicated preview accepts only the same strict MEGA share URL as generic unsent.
            tiktok_previews = page.locator('#view button[data-a="tkPreview"]')
            if tiktok_previews.count() != 2 or tiktok_previews.nth(0).is_disabled() or not tiktok_previews.nth(1).is_disabled():
                failures.append("TikTok original preview did not accept only the strict MEGA share URL")
            else:
                tiktok_previews.nth(0).click()
                page.wait_for_selector('#modal iframe.fm-preview-frame', timeout=5000)
                preview_frame = page.locator('#modal iframe.fm-preview-frame')
                if preview_frame.get_attribute("src") != MEGA_EMBED_URL:
                    failures.append("TikTok preview iframe did not use the validated MEGA embed URL")
                if preview_frame.get_attribute("sandbox") != "allow-scripts allow-same-origin":
                    failures.append("TikTok preview iframe sandbox is not restricted")
                page.locator('#modal button[data-a="tkPreviewClose"]').click()
                page.wait_for_function("() => !document.querySelector('#modal')?.classList.contains('on')", timeout=3000)

            # Delete must call phase one and signed phase two of the existing endpoint.
            page.once("dialog", lambda dialog: dialog.accept("DELETE"))
            click_action(page, '#view button[data-a="tkDelete"]', "delete TikTok original")
            page.wait_for_function("() => document.querySelector('#toast')?.innerText.includes('درخواست حذف نسخهٔ اصلی')", timeout=5000)
            if len(TIKTOK_DELETE_BODIES) != 2:
                failures.append(f"TikTok delete did not make exactly two signed-flow requests: {len(TIKTOK_DELETE_BODIES)}")
            elif TIKTOK_DELETE_BODIES[0].get("source") != "tiktok" or TIKTOK_DELETE_BODIES[0].get("ids") != ["fm_" + "a" * 24]:
                failures.append("TikTok deletion phase one was not scoped to the selected TikTok original")
            elif TIKTOK_DELETE_BODIES[1].get("phrase") != "DELETE" or "source" in TIKTOK_DELETE_BODIES[1]:
                failures.append("TikTok deletion phase two did not consume the signed confirmation payload")

            # Reject-post styling must differ from a primary action on both target viewports.
            for viewport in ({"width": 390, "height": 844}, {"width": 1280, "height": 800}):
                page.set_viewport_size(viewport)
                active_page[0] = f"danger-style-{viewport['width']}"
                try:
                    page.goto(f"{CONSOLE_URL}?danger={viewport['width']}#/jobs", wait_until="domcontentloaded", timeout=15000)
                    page.wait_for_function(
                        "(expected) => document.querySelector('#ttl')?.textContent === expected",
                        arg="کارها", timeout=7000,
                    )
                    page.wait_for_selector('#view button[data-a="jView"][data-id="fixture-mob-ready"]', timeout=7000)
                    page.locator('#view button[data-a="jView"][data-id="fixture-mob-ready"]').click()
                    page.wait_for_selector('#modal button[data-a="jMobReject"]', timeout=5000)
                    reject = page.locator('#modal button[data-a="jMobReject"]')
                    primary = page.locator('#modal button.btn:not(.danger):not(.g)').first
                    if reject.count() != 1 or primary.count() != 1:
                        failures.append(f"jobs at {viewport['width']}: reject/primary comparison controls not found")
                    else:
                        if "danger" not in (reject.get_attribute("class") or "").split():
                            failures.append(f"jobs at {viewport['width']}: reject control lost the danger class")
                        reject_background = reject.evaluate("(el) => getComputedStyle(el).background")
                        primary_background = primary.evaluate("(el) => getComputedStyle(el).background")
                        if reject_background == primary_background:
                            failures.append(f"jobs at {viewport['width']}: reject background matches primary background")
                    page.keyboard.press("Escape")
                    page.wait_for_function("() => !document.querySelector('#modal')?.classList.contains('on')", timeout=3000)
                except Exception as exc:
                    failures.append(f"danger styling at {viewport['width']} failed: {exc}")
            page.set_viewport_size({"width": 1280, "height": 800})

            # The read-only report combines the current 24h digest with Worker, queue and AI status.
            active_page[0] = "report"
            try:
                page.locator('#nav button[data-id="report"]').click()
                page.wait_for_function(
                    "(expected) => document.querySelector('#ttl')?.textContent === expected",
                    arg="گزارش", timeout=5000,
                )
                page.wait_for_function("() => document.querySelector('#view')?.innerText.includes('Mob X report fixture')", timeout=5000)
                report_text = page.locator("#view").inner_text()
                for expected_text in (
                    "خلاصهٔ ۲۴ ساعت", "Mob X report fixture",
                    "Translation report fixture", "وضعیت Worker",
                    "صف انتشار", "هوش مصنوعی", "4", "1",
                ):
                    if expected_text not in report_text:
                        failures.append(f"report: expected rendered text not found: {expected_text}")
                if any(word in report_text.lower() for word in ("private details", "api key", "window-private-id")):
                    failures.append("report: private digest fields leaked into UI")
                if CAPTION_LITERAL not in report_text or "&amp;" in report_text:
                    failures.append("report: caption/report text was double-escaped")
            except Exception as exc:
                failures.append(f"report: could not render report page: {exc}")

            # The failed-media preview modal is tested with a valid fixture and a hostile URL.
            active_page[0] = "unsent"
            try:
                page.locator('#nav button[data-id="unsent"]').click()
                page.wait_for_function(
                    "(expected) => document.querySelector('#ttl')?.textContent === expected",
                    arg="ارسال‌نشده‌ها", timeout=5000,
                )
                page.wait_for_selector('#view button[data-a="fmPreview"]', timeout=5000)
                preview_buttons = page.locator('#view button[data-a="fmPreview"]')
                if preview_buttons.count() != 2:
                    failures.append(f"unsent: expected two preview buttons, found {preview_buttons.count()}")
                if preview_buttons.nth(0).is_disabled():
                    failures.append("unsent: valid MEGA preview button is disabled")
                if not preview_buttons.nth(1).is_disabled():
                    failures.append("unsent: non-MEGA preview button is enabled")
                preview_buttons.nth(0).click()
                page.wait_for_function(
                    "() => document.querySelector('#modal')?.classList.contains('on') && !!document.querySelector('#modal iframe.fm-preview-frame')",
                    timeout=5000,
                )
                frame = page.locator('#modal iframe.fm-preview-frame')
                if frame.get_attribute("src") != MEGA_EMBED_URL:
                    failures.append("unsent: iframe src is not the expected validated MEGA embed URL")
                if frame.get_attribute("sandbox") != "allow-scripts allow-same-origin":
                    failures.append("unsent: iframe sandbox is not restricted as expected")
                if frame.get_attribute("allowfullscreen") is None:
                    failures.append("unsent: iframe does not allow fullscreen")
                fallback = page.locator('#modal a[data-a="fmOpenMega"]')
                if fallback.get_attribute("href") != MEGA_URL:
                    failures.append("unsent: fallback href is not the fixed MEGA share URL")
                if fallback.get_attribute("target") != "_blank" or fallback.get_attribute("rel") != "noopener noreferrer":
                    failures.append("unsent: fallback link security attributes are missing")
                page.locator('#modal button[data-a="fmPreviewClose"]').click()
                page.wait_for_function(
                    "() => !document.querySelector('#modal')?.classList.contains('on') && document.querySelectorAll('#modal iframe').length === 0",
                    timeout=3000,
                )
                preview_buttons.nth(0).click()
                page.wait_for_function(
                    "() => document.querySelector('#modal')?.classList.contains('on') && !!document.querySelector('#modal iframe.fm-preview-frame')",
                    timeout=5000,
                )
                page.keyboard.press("Escape")
                page.wait_for_function(
                    "() => !document.querySelector('#modal')?.classList.contains('on') && document.querySelectorAll('#modal iframe').length === 0",
                    timeout=3000,
                )
            except Exception as exc:
                failures.append(f"unsent: preview open/close smoke failed: {exc}")

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
                    page.wait_for_function("() => { const v = document.querySelector('#view'); return !!v && !v.querySelector('.sk') && !!v.querySelector('.card') && (v.innerText || '').trim().length > 0; }", timeout=5000)
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
                # The queued fixture is not eligible and must not expose any Mob X review actions.
                page.locator('#view button[data-a="jView"][data-id="fixture-job-1"]').click()
                page.wait_for_function("() => document.querySelector('#modal')?.classList.contains('on')", timeout=5000)
                if page.locator('#modal button[data-a^="jMob"]').count() != 0:
                    failures.append("jobs: review controls appeared for a queued job")
                click_action(page, '#modal button[data-a="close"]', "close queued job details")
                page.wait_for_function("() => !document.querySelector('#modal')?.classList.contains('on')", timeout=3000)

                # The eligible Mob X job exposes edit, no-caption and reject in its existing detail modal.
                page.locator('#view button[data-a="jView"][data-id="fixture-mob-ready"]').click()
                page.wait_for_function("() => document.querySelector('#modal')?.classList.contains('on')", timeout=5000)
                for action in ("jMobEdit", "jMobNoCaption", "jMobReject"):
                    if page.locator('#modal button[data-a="' + action + '"]').count() != 1:
                        failures.append(f"jobs: expected eligible Mob X control {action}")
                job_details_text = page.locator("#modal").inner_text()
                if CAPTION_LITERAL not in job_details_text or "&amp;" in job_details_text:
                    failures.append("jobs: caption text was double-escaped")
                if page.locator('#modal button[data-a*="publish"], #modal button[data-a*="approve"]').count():
                    failures.append("jobs: web review details exposed an approval/publish control")
                if page.locator('#modal iframe, #modal video').count():
                    failures.append("jobs: web review details exposed a video preview")

                page.locator('#modal button[data-a="jMobEdit"]').click()
                page.wait_for_selector("#j_mob_caption", timeout=5000)
                page.locator("#j_mob_caption").fill("کپشن فارسی فقط برای همین کار")
                click_action(page, '#modal button[data-a="jMobSaveCaption"]', "save Persian Mob X caption")
                page.wait_for_function("() => document.querySelector('#modal')?.classList.contains('on')", timeout=5000)

                click_action(page, '#modal button[data-a="jMobNoCaption"]', "apply no-caption to this Mob X job")
                page.wait_for_function("() => document.querySelector('#modal')?.innerText.includes('ماب انترتینمنت')", timeout=5000)
                if "پیش‌نمایش تازه را در PV آماده کن" not in page.locator("#modal").inner_text():
                    failures.append("jobs: stale preview was not clearly marked as requiring PV refresh")

                page.once("dialog", lambda dialog: dialog.accept())
                click_action(page, '#modal button[data-a="jMobReject"]', "reject this Mob X job")
                page.wait_for_function("() => { const m = document.querySelector('#modal'); return !!m && m.querySelectorAll('button[data-a^=jMob]').length === 0; }", timeout=5000)
                if page.locator('#modal button[data-a^="jMob"]').count() != 0:
                    failures.append("jobs: review controls remained visible after rejection")
                if [x["action"] for x in MOB_REVIEW_ACTIONS] != ["edit_caption", "no_caption", "reject"]:
                    failures.append("jobs: mocked Worker did not receive all three caption/reject actions in order")
                if MOB_REVIEW_ACTIONS and MOB_REVIEW_ACTIONS[0]["caption"] != "کپشن فارسی فقط برای همین کار":
                    failures.append("jobs: Persian caption was not sent unchanged to the Worker")
                if len(MOB_REVIEW_ACTIONS) > 1 and MOB_REVIEW_ACTIONS[1]["caption"] != None:
                    failures.append("jobs: no-caption action unexpectedly sent a duplicate caption value")
                if MOB_REVIEW_ACTIONS and any(action["job_id"] != "fixture-mob-ready" for action in MOB_REVIEW_ACTIONS):
                    failures.append("jobs: a review action targeted a different job")

                click_action(page, '#modal button[data-a="close"]', "close Mob X job details")
                page.wait_for_function("() => !document.querySelector('#modal')?.classList.contains('on')", timeout=3000)
                page.locator('#view button[data-a="jAdd"]').click()
                page.wait_for_timeout(120)
                if page.locator("#toast .t.bad").count():
                    failures.append("jobs: add job returned an error toast")
            except Exception as exc:
                failures.append(f"jobs: Mob X caption/reject Playwright smoke failed: {exc}")

            active_page[0] = "inst"
            try:
                page.locator('#nav button[data-id="inst"]').click()
                page.locator("#ins_u").fill("https://x.com/mobgamesstudios/status/123456789")
                click_action(page, '#view button[data-a="insAdd"]', "save instant Mob X link")
                page.wait_for_function(
                    "() => document.querySelector('#view')?.innerText.includes('مرحلهٔ ۲')",
                    timeout=5000,
                )
                if "مرحلهٔ ۲" not in page.locator("#view").inner_text():
                    failures.append("inst: adding a link did not display the مرحلهٔ ۲ confirmation card")
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
                original_base = page.evaluate("() => localStorage.getItem('rc_base')")
                page.locator("#s_b").fill("http://insecure.example")
                page.locator('#view button[data-a="sSave"]').click()
                page.wait_for_function("() => document.querySelector('#toast')?.innerText.includes('https://')")
                if page.evaluate("() => localStorage.getItem('rc_base')") != original_base:
                    failures.append("set: non-HTTPS Worker URL was persisted")
                page.locator("#s_b").fill("https://mock-worker.test")
                click_action(page, '#view button[data-a="sSave"]', "restore valid mocked Worker URL")
                click_action(page, '#view button[data-a="sTest"]', "test mocked Worker connection")
            except Exception as exc:
                failures.append(f"set: connection test failed: {exc}")

            # The former directory index remains reachable at the explicit legacy URL.
            active_page[0] = "legacy"
            legacy_page = context.new_page()
            try:
                # All external requests are blocked or mocked; the legacy page is only opened, not exercised.
                legacy_page.route("https://**/*", lambda route: route.abort())
                legacy_page.route(f"{MOCK_BASE}/**", mock_worker)
                legacy_page.route("https://rubika-agent.h98609294.workers.dev/**", mock_worker)
                legacy_page.goto(LEGACY_URL, wait_until="domcontentloaded", timeout=15000)
                if legacy_page.title() != "کنسول مالک — Rubika-Agent":
                    failures.append(f"legacy: unexpected page title: {legacy_page.title()}")
                if legacy_page.locator("#app").count() != 1:
                    failures.append("legacy: former console application root did not open")
            except Exception as exc:
                failures.append(f"legacy: could not open legacy.html: {exc}")
            finally:
                legacy_page.close()

            page.wait_for_timeout(150)
        finally:
            context.close()
            browser.close()

    if len(MOB_REVIEW_ACTIONS) != 3:
        failures.append(f"jobs: expected three owner-only mocked review mutations, found {len(MOB_REVIEW_ACTIONS)}")
    if any("publish" in str(action.get("action", "")) for action in MOB_REVIEW_ACTIONS):
        failures.append("jobs: mocked review flow reached a publish action")
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
