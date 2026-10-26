// Owner panel stage 2: read-only report screens read straight from D1 (same Worker, no HTTP hop).
// Only panel_links is ever written (list/clear of the owner's own saved X links). Nothing here publishes, queues or edits pipeline rows.
const PAGE = 5;
const QUEUE_STATUSES = ["queued", "processing", "sent", "failed", "uncertain", "skipped"];
const QUEUE_LABEL = { queued: "در انتظار", processing: "در حال پردازش", sent: "ارسال‌شده", failed: "ناموفق", uncertain: "نامشخص", skipped: "ردشده" };
const CAT_ICON = { info: "ℹ️", success: "✅", error: "🚨", ai_success: "🤖", ai_error: "⚠️" };

export const HOME_ROWS = [
  [["📊 وضعیت", "p_status"], ["📅 خلاصهٔ ۲۴ ساعت", "p_digest"]],
  [["📦 صف", "p_qs"], ["🏃 اجراها", "p_runs"]],
  [["🚨 خطاها", "p_errors"], ["📜 تاریخچهٔ AI", "p_ailog"]],
  [["🤖 هوش مصنوعی", "p_ai"], ["🧾 گزارش کامل", "p_report"]],
  [["📰 خبر Mob از لینک X", "p_mob"], ["🔗 لینک‌های X", "p_links"]],
  [["🧰 کارها", "p_jobs"]],
  [["⚙️ تنظیمات", "p_set"]],
  [["❓ راهنما", "p_help"]],
  [["🏠 منوی اصلی", "p_home"]],
];

const SCREEN_ID = /^p_(digest|errors|runs|qs|ailog|links|jobs|links_clear|links_clear_yes|ql_[a-z]+_\d{1,3})$/;
export const isScreenId = (id) => SCREEN_ID.test(String(id || ""));

// ---- safe text helpers (values from D1 never carry rich-text markers into the message)
export const clean = (v, max = 40) => String(v ?? "").replace(/\*\*|__|--|~~|\|\||`|\[|\]/g, "").replace(/\s+/g, " ").trim().slice(0, max);
export const tag = (v, max = 24) => String(v ?? "").toLowerCase().replace(/[^a-z0-9_.:-]/g, "").slice(0, max);
const num = (v) => (Number.isFinite(Number(v)) ? Number(v) : 0);
const TEHRAN = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Tehran", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
export function when(s) {
  if (!s) return "—";
  let t = String(s);
  if (/^\d{4}-\d\d-\d\d \d\d:\d\d/.test(t)) t = t.replace(" ", "T");
  if (!/(Z|[+-]\d\d:?\d\d)$/i.test(t)) t += "Z";
  const d = new Date(t);
  if (Number.isNaN(d.getTime())) return "—";
  const p = Object.fromEntries(TEHRAN.formatToParts(d).map((x) => [x.type, x.value]));
  return `${p.month}/${p.day} ${p.hour}:${p.minute}`;
}

const all = async (env, sql, ...b) => (await env.DB.prepare(sql).bind(...b).all()).results || [];
const one = async (env, sql, ...b) => (await env.DB.prepare(sql).bind(...b).first()) || null;
const nav = (id) => [[["🔄 تازه‌سازی", id], ["🏠 منوی اصلی", "p_home"]]];
const HOME_ONLY = [[["🏠 منوی اصلی", "p_home"]]];
const icon = (c) => CAT_ICON[tag(c)] ?? "•";

export async function homeSummary(env) {
  const results = await env.DB.batch([
    env.DB.prepare(
      "SELECT " +
      "SUM(CASE WHEN status='queued' THEN 1 ELSE 0 END) AS queued, " +
      "SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed, " +
      "SUM(CASE WHEN status='sent' AND substr(sent_at,1,10)=date('now') THEN 1 ELSE 0 END) AS sent_today " +
      "FROM automation_queue"
    ),
    env.DB.prepare("SELECT status FROM runs ORDER BY started_at DESC LIMIT 1"),
    env.DB.prepare(
      "SELECT " +
      "(SELECT COUNT(*) FROM brain_events WHERE lower(level) IN ('error','critical') " +
      "AND julianday(created_at) >= julianday('now','-24 hours')) + " +
      "(SELECT COUNT(*) FROM owner_digest_events WHERE category IN ('error','ai_error') " +
      "AND julianday(occurred_at) >= julianday('now','-24 hours')) AS errors_24h"
    ),
  ]);
  const queue = results?.[0]?.results?.[0] || {};
  const lastRun = results?.[1]?.results?.[0] || {};
  const errorStats = results?.[2]?.results?.[0] || {};
  return [
    "🤖 **پنل مدیریت Rubika-Agent**",
    "",
    "───",
    `📦 صف: ${num(queue.queued)} در انتظار | ${num(queue.failed)} ناموفق`,
    `✅ ارسال امروز (UTC): ${num(queue.sent_today)}`,
    `🏃 آخرین اجرا: ${tag(lastRun.status, 20) || "ثبت نشده"}`,
    `🚨 خطاهای ۲۴ ساعت اخیر: ${num(errorStats.errors_24h)}`,
    "───",
    "",
    "یکی از بخش‌ها را انتخاب کن:",
  ].join("\n");
}

// ---- screens
async function digest(env) {
  const st = await one(env, "SELECT window_id, window_started_at, window_ends_at FROM owner_digest_state ORDER BY updated_at DESC LIMIT 1");
  if (!st) return { text: "📅 **خلاصهٔ ۲۴ ساعت**\nهنوز پنجره‌ای ثبت نشده.", rows: nav("p_digest") };
  const counts = await all(env, "SELECT category, COUNT(*) AS c FROM owner_digest_events WHERE window_id=? GROUP BY category", st.window_id);
  const ev = await all(env, "SELECT occurred_at, category, source, title FROM owner_digest_events WHERE window_id=? ORDER BY id DESC LIMIT 8", st.window_id);
  const line = counts.map((r) => `${icon(r.category)} ${num(r.c)}`).join("   ") || "رخدادی نیست";
  const lines = ev.map((e) => `${icon(e.category)} ${when(e.occurred_at)} — ${clean(e.title, 48)}`);
  return { text: ["📅 **خلاصهٔ ۲۴ ساعت**", `از ${when(st.window_started_at)} تا ${when(st.window_ends_at)}`, line, "", ...(lines.length ? lines : ["رخدادی ثبت نشده."])].join("\n"), rows: nav("p_digest") };
}

async function errors(env) {
  const ev = await all(env, "SELECT occurred_at, source, title, detail FROM owner_digest_events WHERE category IN ('error','ai_error') ORDER BY id DESC LIMIT 8");
  const be = await all(env, "SELECT created_at, code FROM brain_events WHERE lower(level) IN ('error','critical') ORDER BY id DESC LIMIT 5");
  const a = ev.flatMap((e) => [`🚨 ${when(e.occurred_at)} ${clean(e.source, 20)}: ${clean(e.title, 44)}`, ...(e.detail ? [`   ${clean(e.detail, 70)}`] : [])]);
  const b = be.map((e) => `• ${when(e.created_at)} ${tag(e.code, 40)}`);
  return { text: ["🚨 **خطاها**", ...(a.length ? a : ["خطای ثبت‌شده‌ای نیست ✅"]), ...(b.length ? ["", "**رخدادهای مغز**", ...b] : [])].join("\n"), rows: nav("p_errors") };
}

async function runs(env) {
  const r = await all(env, "SELECT trigger_type, status, started_at, found_count, eligible_count, sent_count, skipped_count, failed_count FROM runs ORDER BY started_at DESC LIMIT 6");
  const lines = r.map((x) => `• ${when(x.started_at)} ${tag(x.status, 14)} (${tag(x.trigger_type, 12)}) یافت ${num(x.found_count)} | مجاز ${num(x.eligible_count)} | ارسال ${num(x.sent_count)} | رد ${num(x.skipped_count)} | خطا ${num(x.failed_count)}`);
  return { text: ["🏃 **آخرین اجراها**", ...(lines.length ? lines : ["اجرایی ثبت نشده."])].join("\n"), rows: nav("p_runs") };
}

async function queueSummary(env) {
  const rows = await all(env, "SELECT status, COUNT(*) AS c FROM automation_queue GROUP BY status");
  const m = Object.fromEntries(rows.map((r) => [tag(r.status), num(r.c)]));
  const sentToday = num((await one(env, "SELECT COUNT(*) AS c FROM automation_queue WHERE status='sent' AND substr(sent_at,1,10)=date('now')"))?.c);
  const lines = QUEUE_STATUSES.map((s) => `${QUEUE_LABEL[s]}: ${m[s] ?? 0}`);
  const btns = QUEUE_STATUSES.filter((s) => (m[s] ?? 0) > 0).map((s) => [`${QUEUE_LABEL[s]} (${m[s]})`, `p_ql_${s}_0`]);
  const btnRows = [];
  for (let i = 0; i < btns.length; i += 2) btnRows.push(btns.slice(i, i + 2));
  return { text: ["📦 **صف انتشار**", lines.join(" | "), `ارسال امروز (UTC): ${sentToday}`, "", "برای دیدن فهرست، یکی از وضعیت‌ها را بزن."].join("\n"), rows: [...btnRows, ...nav("p_qs")] };
}

async function queueList(env, id) {
  const m = /^p_ql_([a-z]+)_(\d{1,3})$/.exec(id);
  if (!m || !QUEUE_STATUSES.includes(m[1])) return { text: "گزینهٔ نامعتبر.", rows: HOME_ONLY };
  const st = m[1];
  const page = Number(m[2]);
  const total = num((await one(env, "SELECT COUNT(*) AS c FROM automation_queue WHERE status=?", st))?.c);
  const items = await all(env, "SELECT platform, title, last_error_code, updated_at FROM automation_queue WHERE status=? ORDER BY updated_at DESC LIMIT ? OFFSET ?", st, PAGE, page * PAGE);
  const lines = items.map((x) => `• ${tag(x.platform, 10)} | ${clean(x.title, 34) || "بدون عنوان"} | ${when(x.updated_at)}${x.last_error_code ? ` | ${tag(x.last_error_code, 24)}` : ""}`);
  const pager = [];
  if (page > 0) pager.push(["◀️ قبلی", `p_ql_${st}_${page - 1}`]);
  if ((page + 1) * PAGE < total) pager.push(["▶️ بعدی", `p_ql_${st}_${page + 1}`]);
  return {
    text: [`📦 **${QUEUE_LABEL[st]}** — ${total} مورد (صفحهٔ ${page + 1})`, ...(lines.length ? lines : ["موردی نیست."])].join("\n"),
    rows: [...(pager.length ? [pager] : []), [["↩️ صف", "p_qs"], ["🏠 منوی اصلی", "p_home"]]],
  };
}

async function aiLog(env) {
  const r = await all(env, "SELECT provider, role, status, error_code, started_at FROM ai_provider_calls ORDER BY started_at DESC LIMIT 8");
  const lines = r.map((x) => `• ${when(x.started_at)} ${tag(x.provider, 12)}/${tag(x.role, 18)} ${tag(x.status, 12)}${x.error_code ? ` ${tag(x.error_code, 24)}` : ""}`);
  return { text: ["📜 **تاریخچهٔ تماس‌های AI**", ...(lines.length ? lines : ["تماسی ثبت نشده."])].join("\n"), rows: nav("p_ailog") };
}

const shortLink = (u) => {
  const m = /x\.com\/([A-Za-z0-9_]{1,15})\/status\/(\d+)/.exec(String(u || ""));
  return m ? `@${m[1]} …${m[2].slice(-6)}` : "لینک نامعتبر";
};
async function links(env) {
  const total = num((await one(env, "SELECT COUNT(*) AS c FROM panel_links"))?.c);
  const r = await all(env, "SELECT url, status, created_at, external_id FROM panel_links ORDER BY id DESC LIMIT 8");
  const lines = r.map((x) => `• ${when(x.created_at)} ${shortLink(x.url)} (${tag(x.status, 20)})`);
  return { text: [`🔗 **لینک‌های X** — ${total} مورد`, ...(lines.length ? lines : ["لینکی ذخیره نشده."])].join("\n"), rows: [...(total ? [[["🗑 پاک‌کردن همه", "p_links_clear"]]] : []), ...nav("p_links")] };
}
async function jobs(env) {
  const r = await all(env, "SELECT id, type, status, progress, error, created_at, updated_at FROM console_jobs ORDER BY created_at DESC LIMIT 8");
  const lines = r.map((x) => "• " + when(x.created_at) + " " + tag(x.type, 20) + " — " + tag(x.status, 20) + " (" + num(x.progress) + "%)" + (x.error ? " " + tag(x.error, 28) : ""));
  return { text: ["🧰 **کارهای کنسول**", ...(lines.length ? lines : ["کاری ثبت نشده."])].join("\n"), rows: nav("p_jobs") };
}
async function linksClear(env) {
  const total = num((await one(env, "SELECT COUNT(*) AS c FROM panel_links"))?.c);
  return { text: `🗑 همهٔ ${total} لینک ذخیره‌شده پاک شود؟`, rows: [[["✅ بله، پاک شود", "p_links_clear_yes"], ["✖️ نه", "p_links"]]] };
}
async function linksClearYes(env) {
  await env.DB.prepare("DELETE FROM panel_links").run();
  return { text: "✅ لینک‌های ذخیره‌شده پاک شدند.", rows: [[["🔗 لینک‌ها", "p_links"], ["🏠 منوی اصلی", "p_home"]]] };
}

function withNavigation(id, screen) {
  const rows = Array.isArray(screen?.rows)
    ? screen.rows.filter((row) => Array.isArray(row) && row.some((button) =>
      Array.isArray(button) && button.length >= 2 && String(button[0] || "").trim() && String(button[1] || "").trim()))
    : [];
  if (!rows.length) return { ...screen, rows: HOME_ONLY };
  const buttons = rows.flat().filter((button) => Array.isArray(button) && button.length >= 2);
  const extra = [];
  if (!buttons.some((button) => button[1] === id && String(button[0]).includes("تازه‌سازی"))) {
    extra.push(["🔄 تازه‌سازی", id]);
  }
  if (!buttons.some((button) => button[1] === "p_home" && String(button[0]).includes("منوی اصلی"))) {
    extra.push(["🏠 منوی اصلی", "p_home"]);
  }
  if (extra.length) rows.push(extra);
  return { ...screen, rows };
}

export async function buildScreen(env, id) {
  try {
    let screen;
    if (id === "p_digest") screen = await digest(env);
    else if (id === "p_errors") screen = await errors(env);
    else if (id === "p_runs") screen = await runs(env);
    else if (id === "p_qs") screen = await queueSummary(env);
    else if (id === "p_ailog") screen = await aiLog(env);
    else if (id === "p_links") screen = await links(env);
    else if (id === "p_jobs") screen = await jobs(env);
    else if (id === "p_links_clear") screen = await linksClear(env);
    else if (id === "p_links_clear_yes") screen = await linksClearYes(env);
    else if (/^p_ql_/.test(id)) screen = await queueList(env, id);
    else return { text: "گزینهٔ نامعتبر.", rows: HOME_ONLY };
    return withNavigation(id, screen);
  } catch {
    return { text: "❌ خواندن داده ناموفق بود (d1_error).", rows: HOME_ONLY };
  }
}
