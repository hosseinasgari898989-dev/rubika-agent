// Owner PV settings screen: view/change the daily success cap (console_rules.daily_success_cap).
// Same rules row, same optimistic lock and same audit code as PUT /api/console/rules, so PV and web stay consistent.
// publishing_enabled stays false and test_channel_only stays true; this module never publishes and never touches secrets.
// Wiring (owner_panel.js render + HOME_ROWS) is done separately; see isSettingsId / settingsScreen / applySettingsAction.

const CAP_MIN = 0;
const CAP_MAX = 50;
const DEFAULT_CAP = 4;
const TEHRAN_OFFSET_MIN = 210; // Iran uses a fixed UTC+03:30
const TIME_RE = /^([01]\d|2[0-3]):[0-5]\d$/;
const ACTION_RE = /^p_set_cap_(dec|inc|\d{1,2})$/;

export const SETTINGS_ROWS = [
  [["➖ ۱", "p_set_cap_dec"], ["➕ ۱", "p_set_cap_inc"]],
  [["۴", "p_set_cap_4"], ["۷", "p_set_cap_7"], ["۱۰", "p_set_cap_10"]],
  [["🔄 تازه‌سازی", "p_set"], ["🏠 منوی اصلی", "p_home"]],
];

export const isSettingsId = (id) => id === "p_set" || ACTION_RE.test(String(id || ""));

const toMinutes = (hhmm) => Number(hhmm.slice(0, 2)) * 60 + Number(hhmm.slice(3, 5));
const fromMinutes = (total) => {
  const v = ((total % 1440) + 1440) % 1440;
  return String(Math.floor(v / 60)).padStart(2, "0") + ":" + String(v % 60).padStart(2, "0");
};
export const utcToTehran = (hhmm) => fromMinutes(toMinutes(hhmm) + TEHRAN_OFFSET_MIN);

async function readRules(env) {
  const row = await env.DB.prepare("SELECT version, rules_json FROM console_rules WHERE id = 1").first();
  if (!row) return null;
  let rules;
  try { rules = JSON.parse(row.rules_json); } catch { return null; }
  if (!rules || typeof rules !== "object" || Array.isArray(rules)) return null;
  return { version: Number(row.version), rules };
}

const capOf = (rules) => (Number.isInteger(rules.daily_success_cap) ? rules.daily_success_cap : DEFAULT_CAP);

// Returns a short Persian notice, or null when the id is not a settings action.
export async function applySettingsAction(env, id) {
  const m = ACTION_RE.exec(String(id || ""));
  if (!m) return null;
  const current = await readRules(env);
  if (!current) return "تنظیمات در دسترس نیست.";
  const old = capOf(current.rules);
  const next = m[1] === "dec" ? old - 1 : m[1] === "inc" ? old + 1 : Number(m[1]);
  if (!Number.isInteger(next) || next < CAP_MIN || next > CAP_MAX) {
    return "عدد خارج از محدودهٔ مجاز (" + CAP_MIN + " تا " + CAP_MAX + ") است.";
  }
  if (next === old) return "تغییری انجام نشد؛ همین مقدار فعلی است.";
  const rules = { ...current.rules, daily_success_cap: next, publishing_enabled: false, test_channel_only: true };
  const now = new Date().toISOString();
  const result = await env.DB.batch([
    env.DB.prepare("UPDATE console_rules SET version = version + 1, rules_json = ?, updated_at = ? WHERE id = 1 AND version = ?")
      .bind(JSON.stringify(rules), now, current.version),
    env.DB.prepare("INSERT INTO brain_events (item_id, level, code, details_json) SELECT NULL, 'info', 'console_rules_update', ? WHERE changes() > 0")
      .bind(JSON.stringify({ from_version: current.version, fields: ["daily_success_cap"], source: "pv", from: old, to: next })),
  ]);
  if (!result?.[0]?.meta?.changes) return "هم‌زمان تغییر کرد؛ دوباره امتحان کن.";
  return "سقف روزانه از " + old + " به " + next + " تغییر کرد.";
}

async function sentToday(env) {
  const dayStart = new Date();
  dayStart.setUTCHours(0, 0, 0, 0);
  try {
    const row = await env.DB.prepare("SELECT COUNT(*) AS c FROM items WHERE status = 'sent' AND sent_at >= ?").bind(dayStart.toISOString()).first();
    return Number(row && row.c) || 0;
  } catch { return null; }
}

export async function settingsScreen(env, notice = "") {
  const current = await readRules(env);
  if (!current) return { text: "⚙️ **تنظیمات انتشار**\n\nتنظیمات فعلاً در دسترس نیست.", rows: SETTINGS_ROWS.slice(2) };
  const { rules } = current;
  const cap = capOf(rules);
  const sent = await sentToday(env);
  const q = rules.quiet_hours && typeof rules.quiet_hours === "object" ? rules.quiet_hours : null;
  const quiet = q && TIME_RE.test(String(q.start || "")) && TIME_RE.test(String(q.end || ""))
    ? "ساعت سکوت: " + q.start + " تا " + q.end + " UTC (تهران " + utcToTehran(q.start) + " تا " + utcToTehran(q.end) + ")"
    : "ساعت سکوت: تنظیم نشده";
  const lines = ["⚙️ **تنظیمات انتشار**"];
  if (notice) lines.push("", "✅ " + notice);
  lines.push(
    "",
    "• سقف ارسال موفق امروز (فقط تیک‌تاک): **" + cap + "**",
    "• ارسال‌شده امروز: " + (sent === null ? "نامشخص" : sent) + (sent === null ? "" : "، باقی‌مانده: " + Math.max(0, cap - sent)),
    "• شروع روز بعد: ۰۳:۳۰ به وقت تهران",
    "• " + quiet,
    "• مقصد: **کانال تست** (کانال اصلی قفل است)",
    "",
    "اگر سقف تیک‌تاک امروز پر شود، ویرایش‌های بعدی فردا ادامه پیدا می‌کنند؛ خبرهای Mob X معاف‌اند.",
  );
  return { text: lines.join("\n"), rows: SETTINGS_ROWS };
}
