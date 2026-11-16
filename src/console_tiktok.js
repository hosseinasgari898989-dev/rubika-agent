// Owner console TikTok account management and isolated failed-send queue.
import { getPublishingControl, publishingControlReasonFa } from "./publishing_control.js";
export const LEGACY_TIKTOK_PROFILES = Object.freeze([
  "meloaep_", "smyxae", "menezxl9", "rickkzn_", "saylex.aep812", "poppytv_ae",
]);
const LEGACY = new Set(LEGACY_TIKTOK_PROFILES);
const QUEUE_ID_RE = /^tiktok:[A-Za-z0-9._-]{1,120}$/;
const HANDLE_RE = /^(?=.{2,24}$)[A-Za-z0-9._]+$/;
const URL_HOSTS = new Set(["tiktok.com", "www.tiktok.com", "m.tiktok.com"]);
const NOW_SQL = "strftime('%Y-%m-%dT%H:%M:%fZ','now')";

export function normalizeTikTokAccount(value) {
  if (typeof value !== "string") return null;
  const raw = value.trim();
  if (!raw || raw.length > 300) return null;
  let handle = "";
  if (/^https?:\/\//i.test(raw)) {
    let parsed;
    try { parsed = new URL(raw); } catch { return null; }
    const host = String(parsed.hostname || "").toLowerCase().replace(/\.$/, "");
    if (parsed.protocol !== "https:" || !URL_HOSTS.has(host) ||
        parsed.username || parsed.password ||
        (parsed.port && parsed.port !== "443")) return null;
    const match = /^\/@([A-Za-z0-9._]{2,24})\/?$/.exec(parsed.pathname);
    if (!match) return null;
    handle = match[1];
  } else {
    const match = /^@?([A-Za-z0-9._]{2,24})$/.exec(raw);
    if (!match) return null;
    handle = match[1];
  }
  if (!HANDLE_RE.test(handle) || handle.startsWith(".") ||
      handle.endsWith(".") || handle.includes("..")) return null;
  return handle.toLowerCase();
}

const publicSource = (row) => ({
  id: String(row.id),
  platform: "tiktok",
  source_key: String(row.source_key || "").replace(/^@/, ""),
  display_name: row.display_name || null,
  enabled: Number(row.enabled) === 1 ? 1 : 0,
  created_at: row.created_at || null,
  last_checked_at: row.last_checked_at || null,
  last_seen_published_at: row.last_seen_published_at || null,
  managed: true,
});

async function listSources(request, env, h) {
  const result = await env.DB.prepare(
    "SELECT id, platform, source_key, display_name, enabled, created_at, last_checked_at, last_seen_published_at " +
    "FROM sources WHERE platform IN ('tiktok','mob_official') ORDER BY platform, source_key LIMIT 500"
  ).bind().all();
  const dbRows = result?.results || [];
  const tiktokRows = dbRows.filter(row => row.platform === "tiktok");
  const byHandle = new Map();
  for (const row of tiktokRows) {
    const handle = normalizeTikTokAccount(String(row.source_key || "").replace(/^@/, ""));
    if (handle) byHandle.set(handle, row);
  }
  const items = [];
  for (const handle of LEGACY_TIKTOK_PROFILES) {
    const row = byHandle.get(handle);
    items.push(row ? publicSource(row) : {
      id: "tiktok:" + handle, platform: "tiktok", source_key: handle,
      display_name: null, enabled: 1, created_at: null,
      last_checked_at: null, last_seen_published_at: null, managed: false,
    });
  }
  for (const row of tiktokRows) {
    const handle = normalizeTikTokAccount(String(row.source_key || "").replace(/^@/, ""));
    if (handle && !LEGACY.has(handle)) items.push(publicSource(row));
  }
  for (const row of dbRows) if (row.platform === "mob_official") items.push(row);
  const requested = new URL(request.url).searchParams.get("platform");
  if (!requested) return h.json({ ok: true, items: dbRows });
  const platform = requested === "x" ? "mob_official" : requested;
  return h.json({
    ok: true,
    items: items.filter(row => row.platform === platform),
  });
}

async function addTikTokSource(request, env, h) {
  let body;
  try { body = await request.clone().json(); }
  catch { return h.json({ ok: false, error: "invalid_json" }, 400); }
  const handle = normalizeTikTokAccount(body.source_key ?? body.username);
  if (!handle) return h.json({ ok: false, error: "invalid_tiktok_account" }, 400);
  const existing = await env.DB.prepare(
    "SELECT id, enabled FROM sources WHERE platform = 'tiktok' " +
    "AND lower(replace(source_key, '@', '')) = ? LIMIT 1"
  ).bind(handle).first();
  if (existing && Number(existing.enabled) === 1) {
    return h.json({ ok: false, error: "source_already_exists" }, 409);
  }
  if (!existing && LEGACY.has(handle)) {
    return h.json({ ok: false, error: "source_already_exists" }, 409);
  }
  const id = existing?.id || ("tiktok:" + handle);
  const displayName = typeof body.display_name === "string"
    ? body.display_name.trim().slice(0, 120) : "";
  if (existing) {
    await env.DB.prepare(
      "UPDATE sources SET source_key = ?, display_name = ?, enabled = 1, " +
      "created_at = " + NOW_SQL + ", last_checked_at = NULL, last_seen_published_at = NULL " +
      "WHERE id = ? AND platform = 'tiktok' AND enabled = 0"
    ).bind(handle, displayName || null, id).run();
  } else {
    await env.DB.prepare(
      "INSERT INTO sources (id, platform, source_key, display_name, enabled, created_at) " +
      "VALUES (?, 'tiktok', ?, ?, 1, " + NOW_SQL + ")"
    ).bind(id, handle, displayName || null).run();
  }
  return h.json({
    ok: true, id, platform: "tiktok", source_key: handle, enabled: true,
    discovery_starts_at: "created_at",
  }, 201);
}

async function removeTikTokSource(env, h, id) {
  const handle = normalizeTikTokAccount(id.slice("tiktok:".length));
  if (!handle) return h.json({ ok: false, error: "invalid_source_id" }, 400);
  const existing = await env.DB.prepare(
    "SELECT id, enabled FROM sources WHERE platform = 'tiktok' " +
    "AND lower(replace(source_key, '@', '')) = ? LIMIT 1"
  ).bind(handle).first();
  if (!existing && !LEGACY.has(handle)) {
    return h.json({ ok: false, error: "source_not_found" }, 404);
  }
  if (existing) {
    await env.DB.prepare(
      "UPDATE sources SET enabled = 0 WHERE id = ? AND platform = 'tiktok'"
    ).bind(existing.id).run();
  } else {
    await env.DB.prepare(
      "INSERT INTO sources (id, platform, source_key, enabled, created_at) " +
      "VALUES (?, 'tiktok', ?, 0, " + NOW_SQL + ")"
    ).bind("tiktok:" + handle, handle).run();
  }
  // Soft-disable only: queue items, item rows, attempts and Mega ledger history remain.
  return h.json({ ok: true, id: "tiktok:" + handle, enabled: false, history_preserved: true });
}

async function listTikTokUnsent(env, h) {
  // One SELECT returns all failed/not-sent queue rows plus their item, archive and receipt state.
  const result = await env.DB.prepare(
    "SELECT q.id, q.external_id, q.canonical_url, q.caption, " +
    "COALESCE(i.caption, q.caption) AS display_caption, q.source_key AS account, " +
    "COALESCE(NULLIF(fm.reason, ''), NULLIF(q.last_error_code, ''), NULLIF(i.last_error_code, ''), " +
    "CASE WHEN q.status = 'skipped' THEN 'not_sent' ELSE 'send_failed' END) AS reason, " +
    "COALESCE(fm.created_at, q.updated_at, q.created_at) AS created_at, " +
    "fm.id AS failed_media_id, fm.share_url AS original_share_url, fm.status AS failed_media_status, " +
    "q.status AS queue_status, i.status AS item_status, i.sent_at, i.rubika_message_id, " +
    "CASE WHEN q.status IN ('sent','processing','uncertain') OR " +
    "i.status IN ('sent','sending','uncertain') OR i.sent_at IS NOT NULL OR " +
    "i.rubika_message_id IS NOT NULL OR EXISTS (" +
    "SELECT 1 FROM delivery_attempts da WHERE da.item_id = i.id " +
    "AND da.status IN ('sent','uncertain','started')) THEN 1 ELSE 0 END AS already_sent, " +
    "CASE WHEN q.status IN ('failed','skipped') AND i.id IS NOT NULL AND " +
    "COALESCE(i.status,'') NOT IN ('sent','sending','uncertain') AND " +
    "i.sent_at IS NULL AND i.rubika_message_id IS NULL AND NOT EXISTS (" +
    "SELECT 1 FROM delivery_attempts da WHERE da.item_id = i.id " +
    "AND da.status IN ('sent','uncertain','started')) THEN 1 ELSE 0 END AS can_resend " +
    "FROM automation_queue q " +
    "LEFT JOIN items i ON i.platform = q.platform AND i.external_id = q.external_id " +
    "LEFT JOIN failed_media fm ON fm.id = (SELECT fm2.id FROM failed_media fm2 " +
    "WHERE fm2.source = 'tiktok' AND fm2.item_id = i.id " +
    "ORDER BY fm2.created_at DESC, fm2.id DESC LIMIT 1) " +
    "WHERE q.platform = 'tiktok' AND (q.status IN ('failed','skipped','uncertain') OR fm.id IS NOT NULL) " +
    "ORDER BY COALESCE(fm.created_at, q.updated_at, q.created_at) DESC, q.id DESC LIMIT 100"
  ).bind().all();
  return h.json({ ok: true, items: result?.results || [] });
}

async function resendTikTokUnsent(env, h, id) {
  if (!QUEUE_ID_RE.test(id)) return h.json({ ok: false, error: "invalid_tiktok_queue_id" }, 400);
  // One conditional D1 UPDATE; sent/uncertain/started ledger records win over this retry.
  const result = await env.DB.prepare(
    "UPDATE automation_queue SET status = 'queued', last_error_code = NULL, " +
    "updated_at = " + NOW_SQL + " WHERE id = ? AND platform = 'tiktok' " +
    "AND status IN ('failed','skipped') " +
    "AND EXISTS (SELECT 1 FROM items i WHERE i.platform = 'tiktok' " +
    "AND i.external_id = automation_queue.external_id) " +
    "AND NOT EXISTS (SELECT 1 FROM items i WHERE i.platform = 'tiktok' " +
    "AND i.external_id = automation_queue.external_id AND (" +
    "i.status IN ('sent','sending','uncertain') OR i.sent_at IS NOT NULL OR i.rubika_message_id IS NOT NULL)) " +
    "AND NOT EXISTS (SELECT 1 FROM delivery_attempts da JOIN items i ON i.id = da.item_id " +
    "WHERE i.platform = 'tiktok' AND i.external_id = automation_queue.external_id " +
    "AND da.status IN ('sent','uncertain','started'))"
  ).bind(id).run();
  if (Number(result?.meta?.changes || 0) !== 1) {
    return h.json({ ok: false, error: "tiktok_item_already_sent_or_not_retryable" }, 409);
  }
  return h.json({ ok: true, id, queued: true, status: "queued" });
}


const OWNER_REVIEW_MAX_LINES = 20;
const OWNER_REVIEW_CHALLENGE_MS = 10 * 60 * 1000;
const OWNER_REVIEW_REASON_CODES = new Set([
  "music_probability_ambiguous", "edit_signal_not_found_by_brain",
]);

async function ownerReviewSignature(env, actor, challengeId, expiresAt) {
  const ownerIdentity = actor?.kind === "rubika"
    ? "rubika:" + String(actor.chat_id || "")
    : "web:owner";
  const secret = actor?.kind === "rubika"
    ? String(env.AGENT_API_TOKEN || "")
    : String(env.OWNER_WEB_TOKEN || "");
  if (secret.length < 32) throw new Error("owner_review_signing_key_unavailable");
  const key = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign"],
  );
  const payload = [
    "rubika-agent-tiktok-owner-review-v1", ownerIdentity, challengeId, expiresAt,
  ].join("\n");
  const signed = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(payload));
  return Array.from(new Uint8Array(signed), value => value.toString(16).padStart(2, "0")).join("");
}

function constantTimeHexEqual(left, right) {
  if (typeof left !== "string" || typeof right !== "string" ||
      !/^[a-f0-9]{64}$/.test(left) || !/^[a-f0-9]{64}$/.test(right)) return false;
  let difference = 0;
  for (let index = 0; index < left.length; index++) {
    difference |= left.charCodeAt(index) ^ right.charCodeAt(index);
  }
  return difference === 0;
}

function parseOwnerReviewDecisionLine(line) {
  const match = /^(\S+)\s+(قبول|رد)$/u.exec(line.trim());
  if (!match) return null;
  let url;
  try { url = new URL(match[1]); } catch { return null; }
  const host = String(url.hostname || "").toLowerCase().replace(/\.$/, "");
  if (url.protocol !== "https:" || !URL_HOSTS.has(host) ||
      url.username || url.password || (url.port && url.port !== "443")) return null;
  const video = /^\/@([A-Za-z0-9._]{2,24})\/video\/([0-9]{10,25})\/?$/.exec(url.pathname);
  if (!video || !normalizeTikTokAccount(video[1])) return null;
  return {
    handle: video[1].toLowerCase(),
    external_id: video[2],
    decision: match[2] === "قبول" ? "approve" : "reject",
    canonical_url: "https://www.tiktok.com/@" + video[1].toLowerCase() + "/video/" + video[2],
  };
}

async function expireTikTokOwnerReviews(env) {
  const result = await env.DB.prepare(
    "UPDATE automation_queue SET status = 'skipped', last_error_code = 'owner_review_expired', " +
    "metadata_json = json_set(metadata_json, '$.owner_review.state', 'expired', " +
    "'$.owner_review.expired_at', strftime('%Y-%m-%dT%H:%M:%fZ','now')), " +
    "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') " +
    "WHERE platform = 'tiktok' AND status = 'failed' AND last_error_code = 'needs_owner_review' " +
    "AND json_extract(metadata_json, '$.owner_review.state') = 'pending' " +
    "AND julianday(json_extract(metadata_json, '$.owner_review.created_at')) <= julianday('now', '-3 days')"
  ).run();
  return Number(result?.meta?.changes || 0);
}

async function listTikTokOwnerReview(env, h) {
  await expireTikTokOwnerReviews(env);
  const result = await env.DB.prepare(
    "SELECT q.id, q.external_id, q.canonical_url, q.source_key AS account, " +
    "COALESCE(q.caption, q.title, '') AS caption_excerpt, q.created_at, " +
    "json_extract(q.metadata_json, '$.owner_review.reason') AS reason, " +
    "json_extract(q.metadata_json, '$.owner_review.music_probability') AS music_probability " +
    "FROM automation_queue q WHERE q.platform = 'tiktok' AND q.status = 'failed' " +
    "AND q.last_error_code = 'needs_owner_review' " +
    "AND json_extract(q.metadata_json, '$.owner_review.state') = 'pending' " +
    "ORDER BY q.created_at ASC, q.id ASC LIMIT 100"
  ).all();
  return h.json({ ok: true, items: (result?.results || []).map(row => ({
    id: String(row.id),
    external_id: String(row.external_id),
    url: String(row.canonical_url),
    account: String(row.account || ""),
    caption_excerpt: String(row.caption_excerpt || "").slice(0, 180),
    created_at: row.created_at || null,
    reason: OWNER_REVIEW_REASON_CODES.has(row.reason) ? row.reason : "needs_owner_review",
    music_probability: typeof row.music_probability === "number" ? row.music_probability : null,
  })) });
}

async function previewTikTokOwnerReview(env, h, actor, body) {
  if (!body || typeof body !== "object" || Array.isArray(body) ||
      Object.keys(body).some(key => !["lines", "chat_id"].includes(key)) ||
      typeof body.lines !== "string" || body.lines.length > 16000) {
    return h.json({ ok: false, error: "tiktok_owner_review_input_invalid" }, 400);
  }
  if (actor?.kind === "rubika" && body.chat_id !== actor.chat_id) {
    return h.json({ ok: false, error: "tiktok_owner_review_owner_mismatch" }, 403);
  }
  const rawLines = body.lines.split(/\r?\n/).map(value => value.trim()).filter(Boolean);
  const lines = rawLines.slice(0, OWNER_REVIEW_MAX_LINES);
  let invalidCount = Math.max(0, rawLines.length - lines.length);
  const decisions = [];
  const seen = new Set();
  for (const line of lines) {
    const parsed = parseOwnerReviewDecisionLine(line);
    if (!parsed || seen.has(parsed.external_id)) {
      invalidCount += 1;
      continue;
    }
    const isLegacy = LEGACY.has(parsed.handle);
    let tracked = isLegacy;
    if (!tracked) {
      const source = await env.DB.prepare(
        "SELECT id FROM sources WHERE platform = 'tiktok' AND enabled = 1 " +
        "AND lower(replace(source_key, '@', '')) = ? LIMIT 1"
      ).bind(parsed.handle).first();
      tracked = Boolean(source);
    }
    if (!tracked) {
      invalidCount += 1;
      continue;
    }
    const row = await env.DB.prepare(
      "SELECT q.id, q.source_key, q.status, q.last_error_code, q.metadata_json, " +
      "i.status AS item_status, i.sent_at, i.rubika_message_id " +
      "FROM automation_queue q LEFT JOIN items i ON i.platform = q.platform AND i.external_id = q.external_id " +
      "WHERE q.id = ? AND q.platform = 'tiktok' AND q.external_id = ? " +
      "AND q.status = 'failed' AND q.last_error_code = 'needs_owner_review'"
    ).bind("tiktok:" + parsed.external_id, parsed.external_id).first();
    let metadata = {};
    try { metadata = row?.metadata_json ? JSON.parse(row.metadata_json) : {}; } catch {}
    const review = metadata && typeof metadata === "object" ? metadata.owner_review : null;
    const itemBlocked = row && (
      ["sent", "sending", "uncertain"].includes(row.item_status) ||
      row.sent_at != null || row.rubika_message_id != null
    );
    const attempt = await env.DB.prepare(
      "SELECT 1 AS found FROM delivery_attempts da JOIN items i ON i.id = da.item_id " +
      "WHERE i.platform = 'tiktok' AND i.external_id = ? " +
      "AND (da.status IN ('sent','uncertain','started') OR da.rubika_message_id IS NOT NULL) LIMIT 1"
    ).bind(parsed.external_id).first();
    if (!row || !review || review.state !== "pending" ||
        !OWNER_REVIEW_REASON_CODES.has(review.reason) ||
        String(row.source_key || "").replace(/^@/, "").toLowerCase() !== parsed.handle ||
        itemBlocked || attempt) {
      invalidCount += 1;
      continue;
    }
    seen.add(parsed.external_id);
    decisions.push({ ...parsed, id: String(row.id) });
  }
  if (!decisions.length) {
    return h.json({
      ok: true, challenge_id: null, expected_count: 0,
      approved_count: 0, rejected_count: 0, invalid_count: invalidCount,
      summary_fa: "۰ قبول، ۰ رد، " + invalidCount.toLocaleString("fa-IR") + " نامعتبر — مورد قابل اجرا نیست",
    });
  }
  const challengeId = Array.from(crypto.getRandomValues(new Uint8Array(24)),
    value => value.toString(16).padStart(2, "0")).join("");
  const expiresAt = new Date(Date.now() + OWNER_REVIEW_CHALLENGE_MS).toISOString();
  const signature = await ownerReviewSignature(env, actor, challengeId, expiresAt);
  const statements = decisions.map(decision => env.DB.prepare(
    "UPDATE automation_queue SET metadata_json = json_set(metadata_json, " +
    "'$.owner_review.pending_confirmation', json(?)), " +
    "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') " +
    "WHERE id = ? AND platform = 'tiktok' AND status = 'failed' " +
    "AND last_error_code = 'needs_owner_review' " +
    "AND json_extract(metadata_json, '$.owner_review.state') = 'pending' " +
    "AND NOT EXISTS (SELECT 1 FROM items i WHERE i.platform = 'tiktok' " +
    "AND i.external_id = automation_queue.external_id AND " +
    "(i.status IN ('sent','sending','uncertain') OR i.sent_at IS NOT NULL OR i.rubika_message_id IS NOT NULL)) " +
    "AND NOT EXISTS (SELECT 1 FROM delivery_attempts da JOIN items i ON i.id = da.item_id " +
    "WHERE i.platform = 'tiktok' AND i.external_id = automation_queue.external_id " +
    "AND (da.status IN ('sent','uncertain','started') OR da.rubika_message_id IS NOT NULL))"
  ).bind(JSON.stringify({
    challenge_id: challengeId,
    owner_signature: signature,
    decision: decision.decision,
    expected_count: decisions.length,
    expires_at: expiresAt,
  }), decision.id));
  const writes = await env.DB.batch(statements);
  if (!Array.isArray(writes) || writes.length !== decisions.length ||
      writes.some(result => Number(result?.meta?.changes || 0) !== 1)) {
    return h.json({ ok: false, error: "tiktok_owner_review_preview_stale", no_change: true }, 409);
  }
  const approvedCount = decisions.filter(item => item.decision === "approve").length;
  const rejectedCount = decisions.length - approvedCount;
  return h.json({
    ok: true,
    challenge_id: challengeId,
    expected_count: decisions.length,
    approved_count: approvedCount,
    rejected_count: rejectedCount,
    invalid_count: invalidCount,
    expires_at: expiresAt,
    summary_fa: approvedCount.toLocaleString("fa-IR") + " قبول، " +
      rejectedCount.toLocaleString("fa-IR") + " رد، " +
      invalidCount.toLocaleString("fa-IR") + " نامعتبر — برای اجرا بنویس: تأیید",
  });
}

async function confirmTikTokOwnerReview(env, h, actor, body) {
  if (!body || typeof body !== "object" || Array.isArray(body) ||
      Object.keys(body).some(key => !["challenge_id", "confirm", "chat_id"].includes(key)) ||
      body.confirm !== true ||
      typeof body.challenge_id !== "string" || !/^[a-f0-9]{48}$/.test(body.challenge_id)) {
    return h.json({ ok: false, error: "tiktok_owner_review_confirmation_invalid" }, 400);
  }
  if (actor?.kind === "rubika" && body.chat_id !== actor.chat_id) {
    return h.json({ ok: false, error: "tiktok_owner_review_owner_mismatch" }, 403);
  }
  const sample = await env.DB.prepare(
    "SELECT json_extract(metadata_json, '$.owner_review.pending_confirmation.expected_count') AS expected_count, " +
    "json_extract(metadata_json, '$.owner_review.pending_confirmation.decision') AS decision, " +
    "json_extract(metadata_json, '$.owner_review.pending_confirmation.expires_at') AS expires_at, " +
    "json_extract(metadata_json, '$.owner_review.pending_confirmation.owner_signature') AS owner_signature " +
    "FROM automation_queue WHERE platform = 'tiktok' AND status = 'failed' " +
    "AND last_error_code = 'needs_owner_review' " +
    "AND json_extract(metadata_json, '$.owner_review.pending_confirmation.challenge_id') = ? LIMIT 1"
  ).bind(body.challenge_id).first();
  const expected = Number(sample?.expected_count || 0);
  if (!Number.isInteger(expected) || expected < 1 || expected > OWNER_REVIEW_MAX_LINES ||
      typeof sample?.expires_at !== "string" || sample.expires_at <= new Date().toISOString()) {
    return h.json({ ok: false, error: "tiktok_owner_review_challenge_stale", changed_count: 0 }, 409);
  }
  const expectedSignature = await ownerReviewSignature(env, actor, body.challenge_id, sample.expires_at);
  if (!constantTimeHexEqual(sample.owner_signature, expectedSignature)) {
    return h.json({ ok: false, error: "tiktok_owner_review_signature_invalid", changed_count: 0 }, 403);
  }
  if (sample.decision === "approve") {
    const publishingControl = await getPublishingControl(env.DB);
    if (!publishingControl.ok || publishingControl.paused) {
      return h.json({ ok: false, error: publishingControl.error || "publishing_paused",
        reason_fa: publishingControlReasonFa(publishingControl), changed_count: 0 }, 409);
    }
  }
  const now = new Date().toISOString();
  const update = await env.DB.prepare(
    "UPDATE automation_queue SET " +
    "status = CASE json_extract(metadata_json, '$.owner_review.pending_confirmation.decision') " +
    "WHEN 'approve' THEN 'queued' ELSE 'skipped' END, " +
    "last_error_code = CASE json_extract(metadata_json, '$.owner_review.pending_confirmation.decision') " +
    "WHEN 'approve' THEN NULL ELSE 'owner_rejected' END, " +
    "metadata_json = json_remove(json_set(metadata_json, " +
    "'$.owner_review.state', CASE json_extract(metadata_json, '$.owner_review.pending_confirmation.decision') " +
    "WHEN 'approve' THEN 'approved' ELSE 'rejected' END, " +
    "'$.owner_review.decided_at', ?, " +
    "'$.owner_review.owner_override', CASE json_extract(metadata_json, '$.owner_review.pending_confirmation.decision') " +
    "WHEN 'approve' THEN json('true') ELSE json('false') END, " +
    "'$.owner_review.owner_override_reason', CASE json_extract(metadata_json, '$.owner_review.pending_confirmation.decision') " +
    "WHEN 'approve' THEN json_extract(metadata_json, '$.owner_review.reason') ELSE NULL END), " +
    "'$.owner_review.pending_confirmation'), " +
    "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') " +
    "WHERE platform = 'tiktok' AND status = 'failed' AND last_error_code = 'needs_owner_review' " +
    "AND json_extract(metadata_json, '$.owner_review.state') = 'pending' " +
    "AND json_extract(metadata_json, '$.owner_review.pending_confirmation.challenge_id') = ? " +
    "AND json_extract(metadata_json, '$.owner_review.pending_confirmation.owner_signature') = ? " +
    "AND json_extract(metadata_json, '$.owner_review.pending_confirmation.expires_at') > ? " +
    "AND NOT EXISTS (SELECT 1 FROM items i WHERE i.platform = 'tiktok' " +
    "AND i.external_id = automation_queue.external_id AND " +
    "(i.status IN ('sent','sending','uncertain') OR i.sent_at IS NOT NULL OR i.rubika_message_id IS NOT NULL)) " +
    "AND NOT EXISTS (SELECT 1 FROM delivery_attempts da JOIN items i ON i.id = da.item_id " +
    "WHERE i.platform = 'tiktok' AND i.external_id = automation_queue.external_id " +
    "AND (da.status IN ('sent','uncertain','started') OR da.rubika_message_id IS NOT NULL)) " +
    "AND (SELECT COUNT(*) FROM automation_queue q2 WHERE q2.platform = 'tiktok' " +
    "AND q2.status = 'failed' AND q2.last_error_code = 'needs_owner_review' " +
    "AND json_extract(q2.metadata_json, '$.owner_review.state') = 'pending' " +
    "AND json_extract(q2.metadata_json, '$.owner_review.pending_confirmation.challenge_id') = ? " +
    "AND json_extract(q2.metadata_json, '$.owner_review.pending_confirmation.owner_signature') = ? " +
    "AND json_extract(q2.metadata_json, '$.owner_review.pending_confirmation.expires_at') > ? " +
    "AND NOT EXISTS (SELECT 1 FROM items i WHERE i.platform = 'tiktok' " +
    "AND i.external_id = q2.external_id AND " +
    "(i.status IN ('sent','sending','uncertain') OR i.sent_at IS NOT NULL OR i.rubika_message_id IS NOT NULL)) " +
    "AND NOT EXISTS (SELECT 1 FROM delivery_attempts da JOIN items i ON i.id = da.item_id " +
    "WHERE i.platform = 'tiktok' AND i.external_id = q2.external_id " +
    "AND (da.status IN ('sent','uncertain','started') OR da.rubika_message_id IS NOT NULL))) = ? " +
    "RETURNING id, status, external_id"
  ).bind(
    now, body.challenge_id, sample.owner_signature, now,
    body.challenge_id, sample.owner_signature, now, expected,
  ).all();
  const rows = update?.results || [];
  if (rows.length !== expected) {
    return h.json({
      ok: false, error: "tiktok_owner_review_challenge_stale",
      expected_count: expected, changed_count: rows.length, no_change: rows.length === 0,
    }, 409);
  }
  return h.json({
    ok: true,
    changed_count: rows.length,
    approved_count: rows.filter(row => row.status === "queued").length,
    rejected_count: rows.filter(row => row.status === "skipped").length,
    rejected_error_code: "owner_rejected",
    permanent_rejection: true,
  });
}

export async function handleTikTokOwnerReviewApi(request, env, h, action, method, actor) {
  try {
    await expireTikTokOwnerReviews(env);
    if (action === "" && method === "GET") return await listTikTokOwnerReview(env, h);
    if (action === "/preview" && method === "POST") {
      const parsed = await request.clone().json().catch(() => null);
      return parsed ? await previewTikTokOwnerReview(env, h, actor, parsed)
        : h.json({ ok: false, error: "invalid_json" }, 400);
    }
    if (action === "/confirm" && method === "POST") {
      const parsed = await request.clone().json().catch(() => null);
      return parsed ? await confirmTikTokOwnerReview(env, h, actor, parsed)
        : h.json({ ok: false, error: "invalid_json" }, 400);
    }
    return h.json({ ok: false, error: "not_found" }, 404);
  } catch {
    return h.json({ ok: false, error: "tiktok_owner_review_unavailable" }, 503);
  }
}

export async function handleTikTokConsoleApi(request, env, h, sub, method) {
  if (sub === "/tiktok-owner-review" || sub.startsWith("/tiktok-owner-review/")) {
    const action = sub.slice("/tiktok-owner-review".length);
    return handleTikTokOwnerReviewApi(request, env, h, action, method, { kind: "web" });
  }
  if (method === "GET" && sub === "/sources") {
    try { return await listSources(request, env, h); }
    catch { return h.json({ ok: false, error: "console_db_error" }, 503); }
  }
  if (method === "POST" && sub === "/sources") {
    let body;
    try { body = await request.clone().json(); }
    catch { return null; }
    if (body?.platform !== "tiktok") return null;
    try { return await addTikTokSource(request, env, h); }
    catch (error) {
      if (String(error?.message || "").toLowerCase().includes("unique")) {
        return h.json({ ok: false, error: "source_already_exists" }, 409);
      }
      return h.json({ ok: false, error: "console_db_error" }, 503);
    }
  }
  const sourceMatch = /^\/sources\/([^/]+)$/.exec(sub);
  if (sourceMatch && method === "DELETE") {
    let id;
    try { id = decodeURIComponent(sourceMatch[1]); }
    catch { return h.json({ ok: false, error: "invalid_source_id" }, 400); }
    if (!id.startsWith("tiktok:")) return null;
    try { return await removeTikTokSource(env, h, id); }
    catch { return h.json({ ok: false, error: "console_db_error" }, 503); }
  }
  if (method === "GET" && sub === "/tiktok-unsent") {
    try { return await listTikTokUnsent(env, h); }
    catch { return h.json({ ok: false, error: "console_db_error" }, 503); }
  }
  const resendMatch = /^\/tiktok-unsent\/([^/]+)\/resend$/.exec(sub);
  if (resendMatch && method === "POST") {
    let id;
    try { id = decodeURIComponent(resendMatch[1]); }
    catch { return h.json({ ok: false, error: "invalid_failed_media_id" }, 400); }
    try { return await resendTikTokUnsent(env, h, id); }
    catch { return h.json({ ok: false, error: "console_db_error" }, 503); }
  }
  return null;
}
