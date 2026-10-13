const SERVICE = "rubika-agent";
const PUBLISHING_ENABLED = false;
const MAX_BODY_BYTES = 32 * 1024;
const MAX_TEXT = 5000;
const PLATFORMS = new Set(["tiktok", "youtube", "instagram", "mob_official"]);
const ITEM_STATUSES = new Set([
  "discovered", "filtered", "queued", "downloading", "ready",
  "sending", "sent", "failed", "uncertain"
]);
const REVIEW_STATES = new Set(["pending", "approved", "rejected", "needs_review"]);
const MEDIA_TYPES = new Set(["video", "image", "audio", "carousel", "unknown"]);
const SOURCE_TIERS = new Set(["unknown", "trusted", "official"]);
const ATTEMPT_STATES = new Set(["started", "sent", "failed", "uncertain"]);
const EVENT_LEVELS = new Set(["debug", "info", "warning", "error"]);
const STORAGE_EVENTS = new Set(["uploaded", "downloaded", "verified", "deleted"]);

const json = (data, status = 200) =>
  new Response(JSON.stringify(data, null, 2), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      "x-content-type-options": "nosniff",
      "referrer-policy": "no-referrer",
      "cross-origin-resource-policy": "same-origin"
    }
  });

const text = (value, max = MAX_TEXT) =>
  typeof value === "string" ? value.trim().slice(0, max) : "";

const safeCode = (value, fallback = "unspecified") => {
  if (typeof value !== "string") return fallback;
  const result = value.toLowerCase().replace(/[^a-z0-9_-]/g, "").slice(0, 80);
  return result || fallback;
};

const isObject = (value) =>
  value !== null && typeof value === "object" && !Array.isArray(value);

const boundedInt = (value, min, max, fallback = min) => {
  const number = Number(value);
  return Number.isInteger(number) && number >= min && number <= max ? number : fallback;
};

const validIdentifier = (value) =>
  typeof value === "string" &&
  value.length <= 160 &&
  /^[a-z][a-z0-9_-]{1,20}:[A-Za-z0-9._-]{1,120}$/.test(value);

async function readJson(request) {
  const contentType = request.headers.get("content-type") || "";
  if (!contentType.toLowerCase().includes("application/json")) {
    return { ok: false, status: 415, error: "json_content_type_required" };
  }
  const declared = Number(request.headers.get("content-length") || "0");
  if (declared > MAX_BODY_BYTES) {
    return { ok: false, status: 413, error: "request_too_large" };
  }
  let raw = "";
  try {
    raw = await request.text();
  } catch {
    return { ok: false, status: 400, error: "invalid_request_body" };
  }
  if (new TextEncoder().encode(raw).byteLength > MAX_BODY_BYTES) {
    return { ok: false, status: 413, error: "request_too_large" };
  }
  try {
    const value = JSON.parse(raw);
    if (!isObject(value)) {
      return { ok: false, status: 400, error: "json_object_required" };
    }
    return { ok: true, value };
  } catch {
    return { ok: false, status: 400, error: "invalid_json" };
  }
}

function tokenMatches(provided, expected) {
  if (typeof provided !== "string" || typeof expected !== "string" ||
      provided.length === 0 || expected.length < 32) return false;
  const a = new TextEncoder().encode(provided);
  const b = new TextEncoder().encode(expected);
  let difference = a.length ^ b.length;
  const length = Math.max(a.length, b.length);
  for (let index = 0; index < length; index++) {
    difference |= (a[index] || 0) ^ (b[index] || 0);
  }
  return difference === 0;
}

function authorized(request, env) {
  const expected = env.AGENT_API_TOKEN;
  const match = /^Bearer ([^\s]+)$/.exec(request.headers.get("authorization") || "");
  return Boolean(expected && match && tokenMatches(match[1], expected));
}

function canonicalUrlFor(platform, value) {
  if (typeof value !== "string" || value.length > 2048) return null;
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    return null;
  }
  if (parsed.protocol !== "https:" || parsed.username || parsed.password || parsed.hash) return null;
  const host = parsed.hostname.toLowerCase();
  const hosts = {
    tiktok: ["tiktok.com"],
    youtube: ["youtube.com", "youtu.be"],
    instagram: ["instagram.com"],
    mob_official: ["mobentertainment.com", "poppyplaytime.com", "steamcommunity.com"]
  };
  const allowed = hosts[platform] || [];
  if (!allowed.some(domain => host === domain || host.endsWith("." + domain))) return null;

  if (platform === "youtube") {
    // Only accept the single public video ID parameter; reject other parameters
    // so signed/temporary URLs cannot be stored as canonical source URLs.
    if (host === "youtu.be" || host.endsWith(".youtu.be")) {
      if (parsed.search || !/^\/[A-Za-z0-9_-]{11}\/?$/.test(parsed.pathname)) return null;
      const id = parsed.pathname.split("/").filter(Boolean)[0];
      return "https://www.youtube.com/watch?v=" + id;
    }
    if (parsed.pathname === "/watch") {
      if ([...parsed.searchParams.keys()].some(key => key !== "v") ||
          [...parsed.searchParams.keys()].length !== 1) return null;
      const id = parsed.searchParams.get("v") || "";
      if (!/^[A-Za-z0-9_-]{11}$/.test(id)) return null;
      return "https://www.youtube.com/watch?v=" + id;
    }
    const shorts = /^\/shorts\/([A-Za-z0-9_-]{11})\/?$/.exec(parsed.pathname);
    if (shorts && !parsed.search) return "https://www.youtube.com/shorts/" + shorts[1];
    return null;
  }

  if (parsed.search) return null;
  return parsed.toString();
}

function safeJson(value, maxBytes = 12000) {
  if (!isObject(value)) return null;
  try {
    const encoded = JSON.stringify(value);
    if (new TextEncoder().encode(encoded).byteLength > maxBytes) return null;
    return encoded;
  } catch {
    return null;
  }
}

function safeEventDetails(value) {
  if (!isObject(value)) return {};
  const cleaned = {};
  const deniedKey = /(token|password|secret|cookie|authorization|email|signed.?url|private.?url|file.?path)/i;
  for (const [key, item] of Object.entries(value).slice(0, 12)) {
    if (!/^[a-zA-Z0-9_.-]{1,40}$/.test(key) || deniedKey.test(key)) continue;
    if (typeof item === "string") cleaned[key] = item.slice(0, 240);
    else if (typeof item === "number" && Number.isFinite(item)) cleaned[key] = item;
    else if (typeof item === "boolean" || item === null) cleaned[key] = item;
  }
  return cleaned;
}

async function health(env) {
  try {
    await env.DB.prepare("SELECT 1 AS ok").first();
    return json({
      ok: true,
      service: SERVICE,
      database: "connected",
      mode: "bootstrap",
      publishing: PUBLISHING_ENABLED,
      timestamp: new Date().toISOString()
    });
  } catch {
    return json({ ok: false, service: SERVICE, database: "unavailable" }, 503);
  }
}

async function status(env) {
  try {
    const config = await env.DB
      .prepare("SELECT enabled, started_at, updated_at FROM agent_config WHERE id = 1")
      .first();
    const counts = await env.DB.prepare(
      "SELECT status, COUNT(*) AS total FROM items GROUP BY status"
    ).all();
    const recentRuns = await env.DB.prepare(
      "SELECT id, status, started_at, finished_at, found_count, eligible_count, sent_count, skipped_count, failed_count FROM runs ORDER BY started_at DESC LIMIT 5"
    ).all();
    return json({
      ok: true,
      service: SERVICE,
      agent: config || { enabled: 0, started_at: null, updated_at: null },
      item_counts: counts.results || [],
      recent_runs: recentRuns.results || [],
      capabilities: {
        database: true,
        discovery: true,
        metadata_api: true,
        download: false,
        publishing: false
      },
      publishing: PUBLISHING_ENABLED
    });
  } catch {
    return json({ ok: false, error: "database_not_initialized" }, 503);
  }
}

async function createRun(env, body) {
  const id = text(body.id, 100);
  if (!/^[A-Za-z0-9_-]{8,100}$/.test(id)) {
    return json({ ok: false, error: "invalid_run_id" }, 400);
  }
  const trigger = text(body.trigger_type, 40) || "manual";
  if (!/^[A-Za-z0-9_-]+$/.test(trigger)) {
    return json({ ok: false, error: "invalid_trigger_type" }, 400);
  }
  try {
    const existing = await env.DB.prepare("SELECT id, status FROM runs WHERE id = ?").bind(id).first();
    if (existing) return json({ ok: false, error: "run_id_already_exists" }, 409);
    await env.DB.prepare(
      "INSERT INTO runs (id, trigger_type, status) VALUES (?, ?, 'running')"
    ).bind(id, trigger).run();
    return json({ ok: true, run_id: id, status: "running" }, 201);
  } catch {
    return json({ ok: false, error: "run_create_failed" }, 500);
  }
}

async function finishRun(env, runId, body) {
  if (!/^[A-Za-z0-9_-]{8,100}$/.test(runId)) {
    return json({ ok: false, error: "invalid_run_id" }, 400);
  }
  const runStatus = text(body.status, 20);
  if (!new Set(["ok", "partial", "failed", "cancelled"]).has(runStatus)) {
    return json({ ok: false, error: "invalid_run_status" }, 400);
  }
  const counts = ["found_count", "eligible_count", "sent_count", "skipped_count", "failed_count"]
    .map(key => boundedInt(body[key] ?? 0, 0, 100000, -1));
  if (counts.some(number => number < 0)) return json({ ok: false, error: "invalid_count" }, 400);
  const summary = text(body.summary, 2000);
  try {
    const result = await env.DB.prepare(
      "UPDATE runs SET status = ?, finished_at = strftime('%Y-%m-%dT%H:%M:%fZ','now'), found_count = ?, eligible_count = ?, sent_count = ?, skipped_count = ?, failed_count = ?, summary = ? WHERE id = ? AND status = 'running'"
    ).bind(runStatus, ...counts, summary || null, runId).run();
    if (!result.meta?.changes) return json({ ok: false, error: "running_run_not_found" }, 404);
    return json({ ok: true, run_id: runId, status: runStatus });
  } catch {
    return json({ ok: false, error: "run_finish_failed" }, 500);
  }
}

async function upsertItem(env, body) {
  const platform = text(body.platform, 24);
  const externalId = text(body.external_id, 120);
  if (!PLATFORMS.has(platform) || !/^[A-Za-z0-9._-]{1,120}$/.test(externalId)) {
    return json({ ok: false, error: "invalid_platform_or_external_id" }, 400);
  }
  const canonicalUrl = canonicalUrlFor(platform, body.canonical_url);
  if (!canonicalUrl) return json({ ok: false, error: "invalid_canonical_url" }, 400);
  const sourceKey = text(body.source_key, 200);
  if (!sourceKey) return json({ ok: false, error: "source_key_required" }, 400);
  const statusValue = text(body.status, 20) || "discovered";
  const reviewState = text(body.review_state, 20) || "pending";
  const mediaType = text(body.media_type, 20) || "video";
  const sourceTier = text(body.source_tier, 20) || "unknown";
  const upsertStatuses = new Set(["discovered", "filtered", "queued", "downloading", "ready"]);
  if (!ITEM_STATUSES.has(statusValue)) return json({ ok: false, error: "invalid_item_status" }, 400);
  if (!upsertStatuses.has(statusValue)) {
    return json({ ok: false, error: "delivery_status_requires_attempt_api" }, 400);
  }
  if (!REVIEW_STATES.has(reviewState)) return json({ ok: false, error: "invalid_review_state" }, 400);
  if (!MEDIA_TYPES.has(mediaType)) return json({ ok: false, error: "invalid_media_type" }, 400);
  if (!SOURCE_TIERS.has(sourceTier)) return json({ ok: false, error: "invalid_source_tier" }, 400);

  const hash = text(body.media_sha256, 64);
  if (hash && !/^[a-f0-9]{64}$/i.test(hash)) return json({ ok: false, error: "invalid_sha256" }, 400);
  const bytes = body.media_bytes == null ? null : boundedInt(body.media_bytes, 1, 1000000000, -1);
  if (bytes === -1) return json({ ok: false, error: "invalid_media_bytes" }, 400);
  const storageKey = body.storage_key == null ? null : text(body.storage_key, 512);
  if (storageKey && (!storageKey.startsWith("/Rubika-Agent/") || storageKey.includes(".."))) {
    return json({ ok: false, error: "invalid_storage_key" }, 400);
  }
  const validationJson = body.validation == null ? null : safeJson(body.validation);
  const moderationJson = body.moderation == null ? null : safeJson(body.moderation);
  if (body.validation != null && validationJson == null) return json({ ok: false, error: "invalid_validation_object" }, 400);
  if (body.moderation != null && moderationJson == null) return json({ ok: false, error: "invalid_moderation_object" }, 400);
  if (reviewState === "approved") {
    if (statusValue !== "ready" || !hash || !bytes || !validationJson) {
      return json({ ok: false, error: "approval_requires_validated_ready_media" }, 400);
    }
    let validation;
    try { validation = JSON.parse(validationJson); } catch { validation = null; }
    if (!isObject(validation) || validation.ok !== true) {
      return json({ ok: false, error: "approval_requires_successful_validation" }, 400);
    }
    if (platform === "mob_official") {
      if (sourceTier !== "official" || mediaType === "unknown" || mediaType === "carousel") {
        return json({ ok: false, error: "official_media_requires_verified_track_and_supported_type" }, 400);
      }
    } else {
      const required = [
        "poppy_relevant", "is_edit", "insults_poppy",
        "insults_other_games", "high_effort", "has_music"
      ];
      let moderation;
      try { moderation = JSON.parse(moderationJson || "null"); } catch { moderation = null; }
      if (
        platform === "mob_official" || mediaType !== "video" || !isObject(moderation) ||
        required.some(key => typeof moderation[key] !== "boolean") ||
        moderation.poppy_relevant !== true || moderation.is_edit !== true ||
        moderation.insults_poppy !== false || moderation.insults_other_games !== false ||
        moderation.high_effort !== true || moderation.has_music !== true
      ) {
        return json({ ok: false, error: "edit_approval_requires_all_content_checks" }, 400);
      }
    }
  }

  const id = platform + ":" + externalId;
  const title = text(body.title, 500);
  const caption = text(body.caption, 5000);
  const mime = text(body.media_mime, 128);
  const originalCaption = text(body.original_caption, 5000);
  const translation = text(body.persian_translation, 5000);
  const publishedAt = text(body.published_at, 80);
  try {
    await env.DB.prepare(
      "INSERT INTO items (id, platform, source_key, external_id, canonical_url, title, caption, published_at, status, media_sha256, media_bytes, media_mime, media_type, source_tier, storage_key, validation_json, moderation_json, review_state, original_caption, persian_translation) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(platform, external_id) DO UPDATE SET source_key = excluded.source_key, canonical_url = excluded.canonical_url, title = excluded.title, caption = excluded.caption, published_at = COALESCE(excluded.published_at, items.published_at), status = CASE WHEN items.status IN ('sent','uncertain') AND excluded.status NOT IN ('sent','uncertain') THEN items.status ELSE excluded.status END, media_sha256 = COALESCE(excluded.media_sha256, items.media_sha256), media_bytes = COALESCE(excluded.media_bytes, items.media_bytes), media_mime = COALESCE(excluded.media_mime, items.media_mime), media_type = excluded.media_type, source_tier = excluded.source_tier, storage_key = COALESCE(excluded.storage_key, items.storage_key), validation_json = COALESCE(excluded.validation_json, items.validation_json), moderation_json = COALESCE(excluded.moderation_json, items.moderation_json), review_state = CASE WHEN items.review_state = 'approved' AND excluded.review_state = 'pending' THEN items.review_state ELSE excluded.review_state END, original_caption = COALESCE(excluded.original_caption, items.original_caption), persian_translation = COALESCE(excluded.persian_translation, items.persian_translation)"
    ).bind(
      id, platform, sourceKey, externalId, canonicalUrl, title || null, caption || null,
      publishedAt || null, statusValue, hash.toLowerCase() || null, bytes, mime || null,
      mediaType, sourceTier, storageKey, validationJson, moderationJson, reviewState,
      originalCaption || null, translation || null
    ).run();
    return json({ ok: true, item_id: id, status: statusValue, review_state: reviewState }, 200);
  } catch {
    return json({ ok: false, error: "item_upsert_failed" }, 500);
  }
}

async function recordAttempt(env, itemId, body) {
  if (!validIdentifier(itemId)) return json({ ok: false, error: "invalid_item_id" }, 400);
  const number = boundedInt(body.attempt_number, 1, 3, -1);
  const attemptStatus = text(body.status, 20);
  const errorCode = safeCode(body.error_code, "");
  const messageId = text(body.rubika_message_id, 120);
  if (number < 1 || !ATTEMPT_STATES.has(attemptStatus)) {
    return json({ ok: false, error: "invalid_attempt" }, 400);
  }
  if (attemptStatus === "sent" && !messageId) {
    // Some responses omit message IDs. Explicit sent status is still accepted by the caller.
  }
  try {
    const item = await env.DB.prepare("SELECT id, status FROM items WHERE id = ?").bind(itemId).first();
    if (!item) return json({ ok: false, error: "item_not_found" }, 404);
    await env.DB.prepare(
      "INSERT INTO delivery_attempts (item_id, attempt_number, status, started_at, finished_at, rubika_message_id, error_code) VALUES (?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'), CASE WHEN ? = 'started' THEN NULL ELSE strftime('%Y-%m-%dT%H:%M:%fZ','now') END, ?, ?) ON CONFLICT(item_id, attempt_number) DO UPDATE SET status = excluded.status, finished_at = excluded.finished_at, rubika_message_id = excluded.rubika_message_id, error_code = excluded.error_code"
    ).bind(itemId, number, attemptStatus, attemptStatus, messageId || null, errorCode || null).run();

    let nextStatus = "sending";
    if (attemptStatus === "sent") nextStatus = "sent";
    else if (attemptStatus === "uncertain") nextStatus = "uncertain";
    else if (attemptStatus === "failed") nextStatus = number >= 3 ? "failed" : "queued";

    await env.DB.prepare(
      "UPDATE items SET attempt_count = MAX(attempt_count, ?), last_error_code = ?, last_error_at = CASE WHEN ? IS NULL THEN last_error_at ELSE strftime('%Y-%m-%dT%H:%M:%fZ','now') END, rubika_message_id = COALESCE(?, rubika_message_id), sent_at = CASE WHEN ? = 'sent' THEN strftime('%Y-%m-%dT%H:%M:%fZ','now') ELSE sent_at END, status = CASE WHEN status = 'sent' THEN 'sent' WHEN status = 'uncertain' AND ? NOT IN ('sent','uncertain') THEN 'uncertain' ELSE ? END WHERE id = ?"
    ).bind(number, errorCode || null, errorCode || null, messageId || null, attemptStatus, attemptStatus, nextStatus, itemId).run();
    return json({ ok: true, item_id: itemId, attempt_number: number, status: attemptStatus });
  } catch {
    return json({ ok: false, error: "attempt_record_failed" }, 500);
  }
}

async function updateStorage(env, itemId, body) {
  if (!validIdentifier(itemId)) return json({ ok: false, error: "invalid_item_id" }, 400);
  const event = text(body.event, 20);
  if (!STORAGE_EVENTS.has(event)) return json({ ok: false, error: "invalid_storage_event" }, 400);
  let storageKey = body.storage_key == null ? null : text(body.storage_key, 512);
  if (storageKey && (!storageKey.startsWith("/Rubika-Agent/") || storageKey.includes(".."))) {
    return json({ ok: false, error: "invalid_storage_key" }, 400);
  }
  if (event === "deleted") storageKey = null;
  try {
    const update = await env.DB.prepare(
      "UPDATE items SET storage_key = ? WHERE id = ?"
    ).bind(storageKey, itemId).run();
    if (!update.meta?.changes) return json({ ok: false, error: "item_not_found" }, 404);
    await env.DB.prepare(
      "INSERT INTO brain_events (item_id, level, code, details_json) VALUES (?, 'info', ?, ?)"
    ).bind(itemId, "storage_" + event, JSON.stringify({ storage_key_present: Boolean(storageKey) })).run();
    return json({ ok: true, item_id: itemId, storage_event: event });
  } catch {
    return json({ ok: false, error: "storage_update_failed" }, 500);
  }
}

async function addEvent(env, body) {
  const level = text(body.level, 20) || "info";
  if (!EVENT_LEVELS.has(level)) return json({ ok: false, error: "invalid_event_level" }, 400);
  const code = safeCode(body.code, "");
  if (!code) return json({ ok: false, error: "event_code_required" }, 400);
  const runId = text(body.run_id, 100) || null;
  const itemId = text(body.item_id, 160) || null;
  if (runId && !/^[A-Za-z0-9_-]{8,100}$/.test(runId)) return json({ ok: false, error: "invalid_run_id" }, 400);
  if (itemId && !validIdentifier(itemId)) return json({ ok: false, error: "invalid_item_id" }, 400);
  const details = safeEventDetails(body.details);
  try {
    await env.DB.prepare(
      "INSERT INTO brain_events (run_id, item_id, level, code, details_json) VALUES (?, ?, ?, ?, ?)"
    ).bind(runId, itemId, level, code, JSON.stringify(details)).run();
    return json({ ok: true, event: code });
  } catch {
    return json({ ok: false, error: "event_write_failed" }, 500);
  }
}

async function seenItems(env, url) {
  const limitRaw = Number(url.searchParams.get("limit") || "500");
  const offsetRaw = Number(url.searchParams.get("offset") || "0");
  const limit = Number.isInteger(limitRaw) ? Math.min(500, Math.max(1, limitRaw)) : 500;
  const offset = Number.isInteger(offsetRaw) ? Math.min(1000000, Math.max(0, offsetRaw)) : 0;
  const platform = text(url.searchParams.get("platform"), 24);
  if (platform && !PLATFORMS.has(platform)) {
    return json({ ok: false, error: "invalid_platform_filter" }, 400);
  }
  try {
    const sql = platform
      ? "SELECT id, platform, external_id, media_sha256, status, sent_at, review_state FROM items WHERE platform = ? ORDER BY discovered_at DESC LIMIT ? OFFSET ?"
      : "SELECT id, platform, external_id, media_sha256, status, sent_at, review_state FROM items ORDER BY discovered_at DESC LIMIT ? OFFSET ?";
    const statement = env.DB.prepare(sql);
    const result = platform
      ? await statement.bind(platform, limit, offset).all()
      : await statement.bind(limit, offset).all();
    return json({
      ok: true,
      items: result.results || [],
      page: { limit, offset, returned: (result.results || []).length },
    });
  } catch {
    return json({ ok: false, error: "seen_items_read_failed" }, 500);
  }
}

async function approvedQueue(env, url) {
  const requested = Number(url.searchParams.get("limit") || "25");
  const limit = Number.isInteger(requested) ? Math.min(100, Math.max(1, requested)) : 25;
  try {
    const result = await env.DB.prepare(
      "SELECT id, platform, source_key, external_id, canonical_url, title, caption, published_at, status, media_sha256, media_bytes, media_mime, media_type, source_tier, storage_key, validation_json, moderation_json, original_caption, persian_translation, discovered_at FROM items WHERE review_state = 'approved' AND status = 'ready' ORDER BY discovered_at ASC LIMIT ?"
    ).bind(limit).all();
    return json({ ok: true, items: result.results || [], publishing: PUBLISHING_ENABLED });
  } catch {
    return json({ ok: false, error: "queue_read_failed" }, 500);
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;

    if (path === "/" || path === "/health" || path === "/api/health") {
      if (request.method !== "GET") return json({ ok: false, error: "method_not_allowed" }, 405);
      return health(env);
    }
    if (path === "/api/status") {
      if (request.method !== "GET") return json({ ok: false, error: "method_not_allowed" }, 405);
      return status(env);
    }

    if (!path.startsWith("/api/brain/")) {
      return json({ ok: false, error: "not_found" }, 404);
    }
    if (!authorized(request, env)) {
      return json({ ok: false, error: "unauthorized" }, 401);
    }

    if (path === "/api/brain/queue" && request.method === "GET") {
      return approvedQueue(env, url);
    }
    if (path === "/api/brain/items/seen" && request.method === "GET") {
      return seenItems(env, url);
    }
    if (request.method !== "POST") {
      return json({ ok: false, error: "method_not_allowed" }, 405);
    }
    const parsed = await readJson(request);
    if (!parsed.ok) return json({ ok: false, error: parsed.error }, parsed.status);
    const body = parsed.value;

    if (path === "/api/brain/runs") return createRun(env, body);
    const finish = /^\/api\/brain\/runs\/([^/]+)\/finish$/.exec(path);
    if (finish) return finishRun(env, decodeURIComponent(finish[1]), body);
    if (path === "/api/brain/items/upsert") return upsertItem(env, body);
    const attempts = /^\/api\/brain\/items\/([^/]+)\/attempts$/.exec(path);
    if (attempts) return recordAttempt(env, decodeURIComponent(attempts[1]), body);
    const storage = /^\/api\/brain\/items\/([^/]+)\/storage$/.exec(path);
    if (storage) return updateStorage(env, decodeURIComponent(storage[1]), body);
    if (path === "/api/brain/events") return addEvent(env, body);
    return json({ ok: false, error: "not_found" }, 404);
  }
};
