// Failed Mob X job retry and selective link actions, shared by the web console API and the PV panel.
// DB state only: nothing here publishes or sends. A retried job goes back to 'confirmed' and is picked up by the
// 5-minute mob-x-link-processor schedule (same path as a freshly confirmed link).
const ACTIVE = "'confirmed','queued','processing','ready_for_review','approved','sending'";
export const MAX_IDS = 50;
const NOW = "strftime('%Y-%m-%dT%H:%M:%fZ','now')";

const randomHex = (size = 12) =>
  Array.from(crypto.getRandomValues(new Uint8Array(size)), (b) => b.toString(16).padStart(2, "0")).join("");
const rows = async (env, sql, ...b) => (await env.DB.prepare(sql).bind(...b).all()).results || [];
const placeholders = (n) => Array(n).fill("?").join(",");
const uniq = (ids) => [...new Set(ids)].slice(0, MAX_IDS);
const audit = async (env, code, details) => {
  try {
    await env.DB.prepare("INSERT INTO brain_events (item_id, level, code, details_json) VALUES (NULL, 'info', ?, ?)")
      .bind(code, JSON.stringify(details || {}).slice(0, 900)).run();
  } catch { /* audit must never break the action */ }
};

// ---- failed jobs: only jobs whose saved link still exists and is failed (orphans are never retried)
export const listRetryableJobs = (env, limit = MAX_IDS) => rows(env,
  "SELECT j.id, j.panel_link_id, j.error, j.updated_at, p.external_id FROM console_jobs j " +
  "JOIN panel_links p ON p.id = j.panel_link_id " +
  "WHERE j.type = 'mob_x_link' AND j.status = 'failed' AND p.status = 'failed' " +
  "ORDER BY j.updated_at DESC, j.id DESC LIMIT ?", limit);

export async function retryFailedJobs(env, ids) {
  const list = uniq(ids);
  if (!list.length) return { retried: [], skipped: 0 };
  const now = new Date().toISOString();
  const stmts = list.flatMap((id) => [
    env.DB.prepare(
      "UPDATE console_jobs SET status = 'confirmed', progress = 0, result_json = NULL, error = NULL, updated_at = ? " +
      "WHERE id = ? AND type = 'mob_x_link' AND status = 'failed' " +
      "AND EXISTS (SELECT 1 FROM panel_links WHERE id = console_jobs.panel_link_id AND status = 'failed')"
    ).bind(now, id),
    env.DB.prepare(
      "UPDATE panel_links SET status = 'confirmed', updated_at = ? WHERE id = " +
      "(SELECT panel_link_id FROM console_jobs WHERE id = ? AND type = 'mob_x_link' AND status = 'confirmed') " +
      "AND status = 'failed' AND changes() > 0"
    ).bind(now, id),
  ]);
  const res = await env.DB.batch(stmts);
  const retried = list.filter((_, i) => Number(res[i * 2]?.meta?.changes || 0) > 0);
  if (retried.length) await audit(env, "console_jobs_retry", { retried: retried.length, skipped: list.length - retried.length });
  return { retried, skipped: list.length - retried.length };
}

// ---- saved links
export const listInactiveLinks = (env, limit = MAX_IDS) => rows(env,
  `SELECT id, url, status, created_at FROM panel_links WHERE status NOT IN (${ACTIVE}) ORDER BY id DESC LIMIT ?`, limit);

export async function deleteInactiveLinks(env, ids) {
  const list = uniq(ids);
  if (!list.length) return { removed: [], skipped: 0 };
  const res = await env.DB.batch(list.map((id) =>
    env.DB.prepare(`DELETE FROM panel_links WHERE id = ? AND status NOT IN (${ACTIVE})`).bind(id)));
  const removed = list.filter((_, i) => Number(res[i]?.meta?.changes || 0) > 0);
  if (removed.length) await audit(env, "console_links_bulk_delete", { removed: removed.length, skipped: list.length - removed.length });
  return { removed, skipped: list.length - removed.length };
}

export async function cancelPendingLinks(env, ids) {
  const list = uniq(ids);
  if (!list.length) return { cancelled: [], skipped: 0 };
  const res = await env.DB.batch(list.map((id) =>
    env.DB.prepare(`UPDATE panel_links SET status = 'rejected', updated_at = ${NOW} WHERE id = ? AND status = 'pending'`).bind(id)));
  const cancelled = list.filter((_, i) => Number(res[i]?.meta?.changes || 0) > 0);
  if (cancelled.length) await audit(env, "console_links_bulk_cancel", { cancelled: cancelled.length });
  return { cancelled, skipped: list.length - cancelled.length };
}

export async function confirmLinks(env, ids) {
  const list = uniq(ids);
  if (!list.length) return { confirmed: [], skipped: 0 };
  const q = placeholders(list.length);
  const links = await rows(env, `SELECT id, url, external_id, status FROM panel_links WHERE id IN (${q})`, ...list);
  const have = new Set((await rows(env, `SELECT panel_link_id FROM console_jobs WHERE panel_link_id IN (${q})`, ...list))
    .map((r) => Number(r.panel_link_id)));
  const ok = links.filter((l) => ["pending", "confirmed"].includes(l.status) &&
    /^\d{15,22}$/.test(String(l.external_id || "")) && !have.has(Number(l.id)));
  if (!ok.length) return { confirmed: [], skipped: list.length };
  const stmts = ok.flatMap((l) => [
    env.DB.prepare(
      `UPDATE panel_links SET status = 'confirmed', confirmed_at = COALESCE(confirmed_at, ${NOW}), updated_at = ${NOW} ` +
      "WHERE id = ? AND status IN ('pending', 'confirmed')"
    ).bind(l.id),
    env.DB.prepare(
      "INSERT INTO console_jobs (id, type, status, progress, params_json, result_json, error, panel_link_id) " +
      "SELECT ?, 'mob_x_link', 'confirmed', 0, ?, NULL, NULL, ? WHERE EXISTS (SELECT 1 FROM panel_links WHERE id = ? AND status = 'confirmed') " +
      "ON CONFLICT(panel_link_id) WHERE type = 'mob_x_link' AND panel_link_id IS NOT NULL DO NOTHING"
    ).bind("job_" + randomHex(12), JSON.stringify({ url: l.url, panel_link_id: l.id }), l.id, l.id),
  ]);
  await env.DB.batch(stmts);
  const confirmed = ok.map((l) => Number(l.id));
  await audit(env, "console_links_bulk_confirm", { confirmed: confirmed.length, skipped: list.length - confirmed.length });
  return { confirmed, skipped: list.length - confirmed.length };
}

// ---- web console endpoints: POST /api/console/jobs/retry and POST /api/console/links/bulk
// Returns a Response, or null when the route is not one of these two.
export async function handleConsoleRetry(request, env, h, sub, m) {
  if (m !== "POST" || (sub !== "/jobs/retry" && sub !== "/links/bulk")) return null;
  const { json, readJson } = h;
  const fail = (e, status = 400) => json({ ok: false, error: e }, status);
  const parsed = await readJson(request);
  if (!parsed.ok) return fail(parsed.error, parsed.status);
  const b = parsed.value;
  if (!b || typeof b !== "object" || Array.isArray(b)) return fail("body_object_required");
  const pick = (list, valid) => {
    if (!Array.isArray(list) || !list.length || list.length > MAX_IDS) return null;
    const out = [...new Set(list)];
    return out.every(valid) ? out : null;
  };
  try {
    if (sub === "/jobs/retry") {
      let ids;
      if (b.all_failed === true) {
        ids = (await listRetryableJobs(env, MAX_IDS)).map((r) => r.id);
      } else {
        ids = pick(b.job_ids, (v) => typeof v === "string" && /^job_[A-Fa-f0-9]{8,80}$/.test(v));
        if (!ids) return fail("invalid_job_ids");
      }
      const r = await retryFailedJobs(env, ids);
      return json({ ok: true, retried: r.retried.length, skipped: r.skipped, job_ids: r.retried });
    }
    const ids = pick(b.ids, (v) => Number.isSafeInteger(v) && v >= 1);
    if (!ids) return fail("invalid_ids");
    if (b.action === "delete") {
      if (b.confirm !== true) return fail("confirm_true_required");
      const r = await deleteInactiveLinks(env, ids);
      return json({ ok: true, action: "delete", done: r.removed.length, skipped: r.skipped, ids: r.removed });
    }
    if (b.action === "cancel") {
      const r = await cancelPendingLinks(env, ids);
      return json({ ok: true, action: "cancel", done: r.cancelled.length, skipped: r.skipped, ids: r.cancelled });
    }
    if (b.action === "confirm") {
      const r = await confirmLinks(env, ids);
      return json({ ok: true, action: "confirm", done: r.confirmed.length, skipped: r.skipped, ids: r.confirmed });
    }
    return fail("invalid_action");
  } catch {
    return fail("console_db_error", 503);
  }
}
