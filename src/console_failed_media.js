// Failed-media console routes (auth is enforced by index.js before handleConsoleApi). All POST:
//   /failed-media/record          runner records a preserved original (upsert on storage_key)
//   /failed-media/list            owner console lists items {limit?, offset?}
//   /failed-media/delete-request  phase 1 {ids:[...]} or {all:true} returns a short-lived signed challenge;
//                                 phase 2 {confirmation:"..."} marks at most 50 rows and dispatches the workflow
//   /failed-media/pending-delete  delete runner reads rows waiting for deletion
//   /failed-media/delete-result   delete runner reports {id, ok}: ok removes the row (only AFTER mega-rm succeeded),
//                                 failure sets status 'delete_failed'
// Returns a Response, or null when the route is not one of these. This Worker never touches Mega.
const SOURCES = new Set(["tiktok", "mob_x"]);
const MEDIA_TYPES = new Set(["video", "image"]);
const KEY_RE = /^\/Rubika-Agent\/failed\/[A-Za-z0-9._-]+$/;
const URL_RE = /^https:\/\/mega\.(?:nz|io)\/[^\s]+$/;
const REASON_RE = /^[a-z0-9_-]{1,80}$/;
const REF_RE = /^[A-Za-z0-9_.:@-]{1,160}$/;
const ID_RE = /^fm_[0-9a-f]{24}$/;
const MAX_IDS = 50;
const OWNER_CONFIRM_TTL_MS = 5 * 60 * 1000;
const NOW_SQL = "strftime('%Y-%m-%dT%H:%M:%SZ','now')";
const REPO = "hosseinasgari898989-dev/rubika-agent";
const DELETE_WORKFLOW = "failed-media-delete.yml";

const randomHex = (size = 12) =>
  Array.from(crypto.getRandomValues(new Uint8Array(size)), (b) => b.toString(16).padStart(2, "0")).join("");

const encodeBase64Url = (bytes) => {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
};
const decodeBase64Url = (value) => {
  if (typeof value !== "string" || !/^[A-Za-z0-9_-]+$/.test(value)) return null;
  try {
    const padded = value.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - value.length % 4) % 4);
    const binary = atob(padded);
    const decoded = Uint8Array.from(binary, (char) => char.charCodeAt(0));
    // Reject alternate base64url spellings whose unused trailing bits decode to the same bytes.
    return encodeBase64Url(decoded) === value ? decoded : null;
  } catch {
    return null;
  }
};

// Defense in depth for the owner-only deletion request. The router also gates all
// owner mutations; this check deliberately uses only OWNER_WEB_TOKEN and fails closed.
async function hasOwnerBearer(request, env) {
  const expected = typeof env.OWNER_WEB_TOKEN === "string" ? env.OWNER_WEB_TOKEN : "";
  const match = /^Bearer ([^\s]+)$/.exec(request.headers.get("authorization") || "");
  if (!expected || expected.length < 32 || expected === env.AGENT_API_TOKEN || !match) return false;
  const encoder = new TextEncoder();
  const [providedDigest, expectedDigest] = await Promise.all([
    crypto.subtle.digest("SHA-256", encoder.encode(match[1])),
    crypto.subtle.digest("SHA-256", encoder.encode(expected)),
  ]);
  const providedBytes = new Uint8Array(providedDigest);
  const expectedBytes = new Uint8Array(expectedDigest);
  let difference = 0;
  for (let index = 0; index < 32; index += 1) difference |= providedBytes[index] ^ expectedBytes[index];
  const fixedTimeMatch = difference === 0;
  if (typeof crypto.subtle.timingSafeEqual === "function") {
    return crypto.subtle.timingSafeEqual(providedDigest, expectedDigest) && fixedTimeMatch;
  }
  return fixedTimeMatch;
}

async function issueDeleteConfirmation(env, rows) {
  const payload = {
    version: 1,
    expires_at: Date.now() + OWNER_CONFIRM_TTL_MS,
    rows: rows.map((row) => ({
      id: row.id,
      storage_key: row.storage_key,
      status: row.status,
      updated_at: row.updated_at ?? "",
    })),
  };
  const payloadPart = encodeBase64Url(new TextEncoder().encode(JSON.stringify(payload)));
  const key = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(env.AGENT_API_TOKEN),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign"],
  );
  const signature = new Uint8Array(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(payloadPart)));
  return payloadPart + "." + encodeBase64Url(signature);
}

async function verifyDeleteConfirmation(env, token) {
  if (typeof token !== "string" || token.length > 16000) return null;
  const parts = token.split(".");
  if (parts.length !== 2) return null;
  const payloadBytes = decodeBase64Url(parts[0]);
  const signature = decodeBase64Url(parts[1]);
  if (!payloadBytes || !signature || signature.length !== 32) return null;
  try {
    const key = await crypto.subtle.importKey(
      "raw", new TextEncoder().encode(env.AGENT_API_TOKEN),
      { name: "HMAC", hash: "SHA-256" }, false, ["verify"],
    );
    const valid = await crypto.subtle.verify("HMAC", key, signature, new TextEncoder().encode(parts[0]));
    if (!valid) return null;
    const payload = JSON.parse(new TextDecoder().decode(payloadBytes));
    if (payload?.version !== 1 || !Number.isSafeInteger(payload.expires_at) ||
        payload.expires_at < Date.now() || !Array.isArray(payload.rows) ||
        payload.rows.length < 1 || payload.rows.length > MAX_IDS) return null;
    for (const row of payload.rows) {
      if (!row || typeof row.id !== "string" || !ID_RE.test(row.id) ||
          typeof row.storage_key !== "string" || !KEY_RE.test(row.storage_key) ||
          !["stored", "delete_failed", "delete_requested"].includes(row.status) ||
          typeof row.updated_at !== "string" || row.updated_at.length > 40) return null;
    }
    if (new Set(payload.rows.map((row) => row.id)).size !== payload.rows.length) return null;
    return payload;
  } catch {
    return null;
  }
}

async function audit(env, code, details) {
  try {
    await env.DB.prepare("INSERT INTO brain_events (item_id, level, code, details_json) VALUES (NULL, 'info', ?, ?)")
      .bind(code, JSON.stringify(details).slice(0, 900)).run();
  } catch { /* audit must never break the action */ }
}

// Starts the delete workflow through the existing GITHUB_DISPATCH_TOKEN. Never throws; a failed dispatch leaves
// the rows in 'delete_requested' and the next request dispatches again.
async function dispatchDelete(env) {
  const token = env.GITHUB_DISPATCH_TOKEN;
  if (!token) return false;
  try {
    const res = await fetch(
      `https://api.github.com/repos/${REPO}/actions/workflows/${DELETE_WORKFLOW}/dispatches`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`, Accept: "application/vnd.github+json",
          "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "rubika-agent-worker",
        },
        body: JSON.stringify({ ref: "main" }),
      }
    );
    return res.status === 204;
  } catch {
    return false;
  }
}

async function readBody(request, h, fail) {
  const parsed = await h.readJson(request);
  if (!parsed.ok) return { res: fail(parsed.error, parsed.status) };
  const b = parsed.value;
  if (!b || typeof b !== "object" || Array.isArray(b)) return { res: fail("body_object_required") };
  return { b };
}

async function record(request, env, h, fail) {
  const { b, res } = await readBody(request, h, fail);
  if (res) return res;
  if (!SOURCES.has(b.source)) return fail("invalid_source");
  if (!MEDIA_TYPES.has(b.media_type)) return fail("invalid_media_type");
  if (typeof b.storage_key !== "string" || !KEY_RE.test(b.storage_key) || /\/\.{1,2}$/.test(b.storage_key)) {
    return fail("invalid_storage_key");
  }
  let shareUrl = null;
  if (b.share_url !== undefined && b.share_url !== null && b.share_url !== "") {
    if (typeof b.share_url !== "string" || b.share_url.length > 500 || !URL_RE.test(b.share_url)) {
      return fail("invalid_share_url");
    }
    shareUrl = b.share_url;
  }
  if (typeof b.reason !== "string" || !REASON_RE.test(b.reason)) return fail("invalid_reason");
  const caption = b.caption === undefined || b.caption === null ? "" : b.caption;
  if (typeof caption !== "string" || caption.length > 5000) return fail("invalid_caption");
  const ref = (v) => (v === undefined || v === null || v === "" ? null : v);
  const jobId = ref(b.job_id);
  const itemId = ref(b.item_id);
  if ((jobId !== null && (typeof jobId !== "string" || !REF_RE.test(jobId))) ||
      (itemId !== null && (typeof itemId !== "string" || !REF_RE.test(itemId)))) {
    return fail("invalid_reference");
  }
  const size = b.size_bytes === undefined || b.size_bytes === null ? null : b.size_bytes;
  if (size !== null && (!Number.isSafeInteger(size) || size < 0)) return fail("invalid_size_bytes");

  try {
    const id = "fm_" + randomHex(12);
    await env.DB.prepare(
      "INSERT INTO failed_media (id, source, job_id, item_id, media_type, storage_key, share_url, caption, reason, size_bytes) " +
      "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) " +
      "ON CONFLICT(storage_key) DO UPDATE SET " +
      "share_url = COALESCE(excluded.share_url, failed_media.share_url), " +
      "caption = excluded.caption, reason = excluded.reason, " +
      "size_bytes = COALESCE(excluded.size_bytes, failed_media.size_bytes), " +
      `updated_at = ${NOW_SQL}`
    ).bind(id, b.source, jobId, itemId, b.media_type, b.storage_key, shareUrl, caption, b.reason, size).run();
    const row = await env.DB.prepare("SELECT id, share_url FROM failed_media WHERE storage_key = ?")
      .bind(b.storage_key).first();
    await audit(env, "console_failed_media_record", { source: b.source, media_type: b.media_type });
    return h.json({ ok: true, id: row?.id || id, storage_key: b.storage_key, share_url: row?.share_url ?? shareUrl });
  } catch {
    return fail("console_db_error", 503);
  }
}

async function list(request, env, h, fail) {
  const { b, res } = await readBody(request, h, fail);
  if (res) return res;
  const num = (v, def, min, max) => {
    if (v === undefined || v === null) return def;
    return Number.isInteger(v) && v >= min && v <= max ? v : null;
  };
  const limit = num(b.limit, 50, 1, 100);
  const offset = num(b.offset, 0, 0, 100000);
  if (limit === null) return fail("invalid_limit");
  if (offset === null) return fail("invalid_offset");
  try {
    const rows = await env.DB.prepare(
      "SELECT id, source, job_id, item_id, media_type, storage_key, share_url, caption, reason, size_bytes, status, created_at " +
      "FROM failed_media ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?"
    ).bind(limit, offset).all();
    const total = await env.DB.prepare("SELECT COUNT(*) AS n FROM failed_media").bind().first();
    return h.json({ ok: true, items: rows?.results || [], total: total?.n ?? 0, limit, offset });
  } catch {
    return fail("console_db_error", 503);
  }
}

async function deleteRequest(request, env, h, fail) {
  if (!(await hasOwnerBearer(request, env))) return fail("owner_required", 403);
  const { b, res } = await readBody(request, h, fail);
  if (res) return res;

  // Phase 2: consume a short-lived HMAC confirmation bound to the exact row snapshots.
  if (Object.hasOwn(b, "confirmation")) {
    if (Object.keys(b).length !== 2 || typeof b.confirmation !== "string" || b.phrase !== "DELETE") {
      return fail("confirmation_phrase_required");
    }
    const payload = await verifyDeleteConfirmation(env, b.confirmation);
    if (!payload) return fail("confirmation_invalid_or_expired", 409);
    const rows = payload.rows;
    const predicate = rows.map(() =>
      "(id = ? AND status = ? AND COALESCE(updated_at, '') = ? AND storage_key = ?)"
    ).join(" OR ");
    const values = rows.flatMap((row) => [row.id, row.status, row.updated_at, row.storage_key]);
    const sql = "UPDATE failed_media SET status = 'delete_requested', " +
      "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') " +
      `WHERE (${predicate}) AND (SELECT COUNT(*) FROM failed_media WHERE (${predicate})) = ?`;
    try {
      const out = await env.DB.prepare(sql).bind(...values, ...values, rows.length).run();
      const requested = Number(out?.meta?.changes || 0);
      if (requested !== rows.length) return fail("confirmation_stale", 409);
      await audit(env, "console_failed_media_delete_request", { all: rows.length === MAX_IDS, requested });
      const dispatched = await dispatchDelete(env);
      return h.json({ ok: true, requested, dispatched, batch_limit: MAX_IDS });
    } catch {
      return fail("console_db_error", 503);
    }
  }

  // Phase 1: identify eligible rows, validate paths, and issue a signed server-side challenge.
  const keys = Object.keys(b).sort();
  let rows;
  let all = false;
  if (b.all === true && keys.length === 1 && keys[0] === "all") {
    all = true;
    try {
      const result = await env.DB.prepare(
        "SELECT id, storage_key, status, updated_at FROM failed_media " +
        "WHERE status IN ('stored','delete_failed','delete_requested') " +
        "ORDER BY updated_at, id LIMIT ?"
      ).bind(MAX_IDS).all();
      rows = result?.results || [];
    } catch {
      return fail("console_db_error", 503);
    }
  } else {
    if (keys.length !== 1 || keys[0] !== "ids" ||
        !Array.isArray(b.ids) || b.ids.length < 1) return fail("invalid_ids");
    if (b.ids.length > MAX_IDS) return fail("too_many_ids");
    if (!b.ids.every((id) => typeof id === "string" && ID_RE.test(id))) return fail("invalid_ids");
    const ids = [...new Set(b.ids)];
    if (ids.length !== b.ids.length) return fail("duplicate_ids");
    try {
      const result = await env.DB.prepare(
        "SELECT id, storage_key, status, updated_at FROM failed_media " +
        `WHERE id IN (${ids.map(() => "?").join(",")}) ORDER BY id`
      ).bind(...ids).all();
      rows = result?.results || [];
    } catch {
      return fail("console_db_error", 503);
    }
    if (rows.length !== ids.length ||
        rows.some((row) => !["stored", "delete_failed", "delete_requested"].includes(row.status))) {
      return fail("items_not_eligible", 409);
    }
  }

  if (!rows.length) return h.json({ ok: true, requested: 0, confirmation_required: false, dispatched: false });
  if (rows.length > MAX_IDS || rows.some((row) =>
    typeof row.id !== "string" || !ID_RE.test(row.id) ||
    typeof row.storage_key !== "string" || !KEY_RE.test(row.storage_key) ||
    !["stored", "delete_failed", "delete_requested"].includes(row.status) ||
    typeof row.updated_at !== "string" || row.updated_at.length > 40
  )) return fail("invalid_storage_key", 409);

  try {
    const confirmation = await issueDeleteConfirmation(env, rows);
    return h.json({
      ok: true, confirmation_required: true, confirmation,
      requested: rows.length, limited: all && rows.length === MAX_IDS,
      expires_in_seconds: OWNER_CONFIRM_TTL_MS / 1000,
    });
  } catch {
    return fail("confirmation_unavailable", 503);
  }
}

async function pendingDelete(request, env, h, fail) {
  try {
    const rows = await env.DB.prepare(
      "SELECT id, storage_key FROM failed_media WHERE status = 'delete_requested' ORDER BY updated_at, id LIMIT 50"
    ).bind().all();
    const items = rows?.results || [];
    if (items.some((row) =>
      typeof row.id !== "string" || !ID_RE.test(row.id) ||
      typeof row.storage_key !== "string" || !KEY_RE.test(row.storage_key)
    )) return fail("invalid_storage_key", 409);
    return h.json({ ok: true, items });
  } catch {
    return fail("console_db_error", 503);
  }
}

async function deleteResult(request, env, h, fail) {
  const { b, res } = await readBody(request, h, fail);
  if (res) return res;
  if (typeof b.id !== "string" || !ID_RE.test(b.id)) return fail("invalid_id");
  if (typeof b.ok !== "boolean") return fail("invalid_ok");
  try {
    const current = await env.DB.prepare(
      "SELECT id, storage_key, status FROM failed_media WHERE id = ?"
    ).bind(b.id).first();
    if (!current || current.status !== "delete_requested") return h.json({ ok: true, changed: 0 });
    if (typeof current.storage_key !== "string" || !KEY_RE.test(current.storage_key)) {
      return fail("invalid_storage_key", 409);
    }
    // The row is removed only when the runner confirms the Mega file is gone; failures stay visible.
    const sql = b.ok
      ? "DELETE FROM failed_media WHERE id = ? AND status = 'delete_requested' AND storage_key = ?"
      : `UPDATE failed_media SET status = 'delete_failed', updated_at = ${NOW_SQL} WHERE id = ? AND status = 'delete_requested' AND storage_key = ?`;
    const out = await env.DB.prepare(sql).bind(b.id, current.storage_key).run();
    await audit(env, "console_failed_media_delete_result", { ok: b.ok });
    return h.json({ ok: true, changed: out?.meta?.changes ?? 0 });
  } catch {
    return fail("console_db_error", 503);
  }
}

const ROUTES = {
  "/failed-media/record": record,
  "/failed-media/list": list,
  "/failed-media/delete-request": deleteRequest,
  "/failed-media/pending-delete": pendingDelete,
  "/failed-media/delete-result": deleteResult,
};

export async function handleFailedMedia(request, env, h, sub, m) {
  const route = Object.hasOwn(ROUTES, sub) ? ROUTES[sub] : null;
  if (m !== "POST" || !route) return null;
  const fail = (e, status = 400) => h.json({ ok: false, error: e }, status);
  return route(request, env, h, fail);
}
