// Records originals that could not be sent to Rubika and were preserved in Mega failed/.
// Route: POST /api/console/failed-media/record (auth is enforced by index.js before handleConsoleApi).
// Returns a Response, or null when the route is not this one. DB state only: nothing is sent or deleted here.
const SOURCES = new Set(["tiktok", "mob_x"]);
const MEDIA_TYPES = new Set(["video", "image"]);
const KEY_RE = /^\/Rubika-Agent\/failed\/[A-Za-z0-9._-]+$/;
const URL_RE = /^https:\/\/mega\.(?:nz|io)\/[^\s]+$/;
const REASON_RE = /^[a-z0-9_-]{1,80}$/;
const REF_RE = /^[A-Za-z0-9_.:@-]{1,160}$/;

const randomHex = (size = 12) =>
  Array.from(crypto.getRandomValues(new Uint8Array(size)), (b) => b.toString(16).padStart(2, "0")).join("");

export async function handleFailedMedia(request, env, h, sub, m) {
  if (m !== "POST" || sub !== "/failed-media/record") return null;
  const { json, readJson } = h;
  const fail = (e, status = 400) => json({ ok: false, error: e }, status);
  const parsed = await readJson(request);
  if (!parsed.ok) return fail(parsed.error, parsed.status);
  const b = parsed.value;
  if (!b || typeof b !== "object" || Array.isArray(b)) return fail("body_object_required");

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
      "updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')"
    ).bind(id, b.source, jobId, itemId, b.media_type, b.storage_key, shareUrl, caption, b.reason, size).run();
    const row = await env.DB.prepare("SELECT id, share_url FROM failed_media WHERE storage_key = ?")
      .bind(b.storage_key).first();
    try {
      await env.DB.prepare("INSERT INTO brain_events (item_id, level, code, details_json) VALUES (NULL, 'info', ?, ?)")
        .bind("console_failed_media_record", JSON.stringify({ source: b.source, media_type: b.media_type }).slice(0, 900)).run();
    } catch { /* audit must never break the action */ }
    return json({ ok: true, id: row?.id || id, storage_key: b.storage_key, share_url: row?.share_url ?? shareUrl });
  } catch {
    return fail("console_db_error", 503);
  }
}
