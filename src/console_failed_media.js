// Failed-media console routes (auth is enforced by index.js before handleConsoleApi). All POST:
//   /failed-media/record          runner records a preserved original (upsert on storage_key)
//   /failed-media/list            owner console lists items {limit?, offset?}
//   /failed-media/delete-request  marks items 'delete_requested' {ids:[...]} or {all:true, confirm:"DELETE_ALL"},
//                                 then dispatches the delete workflow (best effort)
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
const MAX_IDS = 100;
const NOW_SQL = "strftime('%Y-%m-%dT%H:%M:%SZ','now')";
const REPO = "hosseinasgari898989-dev/rubika-agent";
const DELETE_WORKFLOW = "failed-media-delete.yml";

const randomHex = (size = 12) =>
  Array.from(crypto.getRandomValues(new Uint8Array(size)), (b) => b.toString(16).padStart(2, "0")).join("");

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
  const { b, res } = await readBody(request, h, fail);
  if (res) return res;
  const eligible = "status IN ('stored','delete_failed')";
  let sql;
  let args = [];
  if (b.all === true) {
    // Two-step confirmation: the client must echo the exact phrase.
    if (b.confirm !== "DELETE_ALL") return fail("confirmation_required");
    sql = `UPDATE failed_media SET status = 'delete_requested', updated_at = ${NOW_SQL} WHERE ${eligible}`;
  } else {
    if (!Array.isArray(b.ids) || b.ids.length < 1 || b.ids.length > MAX_IDS) return fail("invalid_ids");
    if (!b.ids.every((id) => typeof id === "string" && ID_RE.test(id))) return fail("invalid_ids");
    args = [...new Set(b.ids)];
    sql = `UPDATE failed_media SET status = 'delete_requested', updated_at = ${NOW_SQL} ` +
      `WHERE id IN (${args.map(() => "?").join(",")}) AND ${eligible}`;
  }
  try {
    const out = await env.DB.prepare(sql).bind(...args).run();
    const requested = out?.meta?.changes ?? 0;
    await audit(env, "console_failed_media_delete_request", { all: b.all === true, requested });
    const dispatched = requested > 0 ? await dispatchDelete(env) : false;
    return h.json({ ok: true, requested, dispatched });
  } catch {
    return fail("console_db_error", 503);
  }
}

async function pendingDelete(request, env, h, fail) {
  try {
    const rows = await env.DB.prepare(
      "SELECT id, storage_key FROM failed_media WHERE status = 'delete_requested' ORDER BY updated_at, id LIMIT 50"
    ).bind().all();
    return h.json({ ok: true, items: rows?.results || [] });
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
    // The row is removed only when the runner confirms the Mega file is gone; failures stay visible.
    const sql = b.ok
      ? "DELETE FROM failed_media WHERE id = ? AND status = 'delete_requested'"
      : `UPDATE failed_media SET status = 'delete_failed', updated_at = ${NOW_SQL} WHERE id = ? AND status = 'delete_requested'`;
    const out = await env.DB.prepare(sql).bind(b.id).run();
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
