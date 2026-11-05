// Owner console TikTok account management and isolated failed-send queue.
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
        parsed.username || parsed.password || parsed.search || parsed.hash ||
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
      "INSERT INTO sources (id, platform, source_key, display_name, enabled) " +
      "VALUES (?, 'tiktok', ?, ?, 1)"
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
      "INSERT INTO sources (id, platform, source_key, enabled) VALUES (?, 'tiktok', ?, 0)"
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

export async function handleTikTokConsoleApi(request, env, h, sub, method) {
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
