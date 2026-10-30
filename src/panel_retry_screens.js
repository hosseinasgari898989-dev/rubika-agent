// Owner PV screens: multi-select retry of failed Mob X jobs and multi-select removal of inactive saved links.
// Selection is stateless: the callback id carries a bit mask of the (max 5) rows on screen plus a short key of those
// rows, so a stale button can never act on different rows. DB state only; nothing here sends or publishes.
import { listRetryableJobs, retryFailedJobs, listInactiveLinks, deleteInactiveLinks, MAX_IDS } from "./console_retry.js";

const PAGE = 5;
const ID = /^p_(?:(?:fail|lsel)(?:_\d{1,2}_[0-9a-f]{0,5})?|(?:failgo|lseldel)_\d{1,2}_[0-9a-f]{0,5}|failall(?:_yes)?)$/;
export const isRetryScreenId = (id) => ID.test(String(id || ""));
export const RETRY_HOME_ROW = [["🔁 تلاش مجدد ناموفق‌ها", "p_fail"], ["☑️ انتخاب لینک‌ها", "p_lsel"]];

const tag = (v, max = 24) => String(v ?? "").toLowerCase().replace(/[^a-z0-9_.:-]/g, "").slice(0, max);
const TEHRAN = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Tehran", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
const when = (s) => {
  if (!s) return "—";
  let t = String(s);
  if (/^\d{4}-\d\d-\d\d \d\d:\d\d/.test(t)) t = t.replace(" ", "T");
  if (!/(Z|[+-]\d\d:?\d\d)$/i.test(t)) t += "Z";
  const d = new Date(t);
  if (Number.isNaN(d.getTime())) return "—";
  const p = Object.fromEntries(TEHRAN.formatToParts(d).map((x) => [x.type, x.value]));
  return `${p.month}/${p.day} ${p.hour}:${p.minute}`;
};
const shortLink = (u) => {
  const m = /x\.com\/([A-Za-z0-9_]{1,15})\/status\/(\d+)/.exec(String(u || ""));
  return m ? `@${m[1]} …${m[2].slice(-6)}` : "لینک نامعتبر";
};

const HOME = ["🏠 منوی اصلی", "p_home"];
const bit = (mask, i) => (mask & (1 << i)) !== 0;
const keyOf = (list, pick) => list.slice(0, PAGE).map((r) => String(pick(r)).slice(-1).toLowerCase()).join("");
const chunk = (arr, n) => { const out = []; for (let i = 0; i < arr.length; i += n) out.push(arr.slice(i, i + n)); return out; };
const parse = (id) => {
  const m = /^p_(fail|failgo|lsel|lseldel)(?:_(\d{1,2})_([0-9a-f]{0,5}))?$/.exec(id);
  if (!m) return null;
  const mask = m[2] === undefined ? 0 : Number(m[2]);
  return { kind: m[1], mask: mask >= 0 && mask < 32 ? mask : 0, key: m[3] === undefined ? null : m[3] };
};

async function jobsScreen(env, selfId, mask, key, note) {
  const jobs = await listRetryableJobs(env, MAX_IDS);
  const cur = keyOf(jobs, (j) => j.id);
  let m = mask, msg = note || "";
  if (key !== null && key !== cur) { m = 0; msg = "فهرست تغییر کرد؛ انتخاب‌ها پاک شد."; }
  if (!jobs.length) {
    return { text: ["🔁 **تلاش مجدد کارهای ناموفق**", ...(msg ? [msg] : []), "کار ناموفقی که لینکش باقی مانده باشد نیست."].join("\n"), rows: [[["🧰 کارها", "p_jobs"], HOME]] };
  }
  const page = jobs.slice(0, PAGE);
  const chosen = page.filter((_, i) => bit(m, i)).length;
  const lines = page.map((j, i) => `${bit(m, i) ? "☑️" : "⬜"} ${i + 1}) …${tag(j.external_id, 22).slice(-6)} | ${when(j.updated_at)} | ${tag(j.error, 28) || "—"}`);
  const toggles = chunk(page.map((_, i) => [`${bit(m, i) ? "☑️" : "⬜"} ${i + 1}`, `p_fail_${m ^ (1 << i)}_${cur}`]), 3);
  return {
    text: ["🔁 **تلاش مجدد کارهای ناموفق**", ...(msg ? [msg] : []), `${jobs.length} کار ناموفق${jobs.length > PAGE ? ` (${PAGE} مورد اول نمایش داده می‌شود)` : ""}`, ...lines, "", "ردیف‌ها را انتخاب کن؛ کار انتخاب‌شده به صف برمی‌گردد و تا ۵ دقیقه دیگر پردازش می‌شود."].join("\n"),
    rows: [
      ...toggles,
      ...(chosen > 0 ? [[[`🔁 تلاش مجدد انتخاب‌شده‌ها (${chosen})`, `p_failgo_${m}_${cur}`]]] : []),
      [[`🔁 همهٔ ناموفق‌ها (${jobs.length})`, "p_failall"]],
      [["🔄 تازه‌سازی", selfId], HOME],
    ].map((r) => (Array.isArray(r[0]) ? r : [r])).map((r) => (Array.isArray(r[0]) ? r : [r])),
  };
}

async function jobsGo(env, mask, key) {
  const jobs = await listRetryableJobs(env, MAX_IDS);
  const cur = keyOf(jobs, (j) => j.id);
  if (key !== cur) return jobsScreen(env, "p_fail", 0, null, "فهرست تغییر کرد؛ دوباره انتخاب کن.");
  const ids = jobs.slice(0, PAGE).filter((_, i) => bit(mask, i)).map((j) => j.id);
  if (!ids.length) return jobsScreen(env, "p_fail", 0, null, "چیزی انتخاب نشده بود.");
  const r = await retryFailedJobs(env, ids);
  return {
    text: `✅ ${r.retried.length} کار به صف برگشت؛ تا ۵ دقیقه دیگر پردازش می‌شود.${r.skipped ? ` (${r.skipped} مورد قابل‌برگشت نبود)` : ""}`,
    rows: [[["🔁 ناموفق‌ها", "p_fail"], ["🧰 کارها", "p_jobs"]], [HOME]],
  };
}

async function jobsAll(env) {
  const n = (await listRetryableJobs(env, MAX_IDS)).length;
  if (!n) return jobsScreen(env, "p_fail", 0, null, "");
  return { text: `🔁 ${n} کار ناموفق دوباره در صف قرار بگیرند؟ (حداکثر ${MAX_IDS} کار در هر بار)`, rows: [[["✅ بله", "p_failall_yes"], ["✖️ نه", "p_fail"]]] };
}

async function jobsAllYes(env) {
  const ids = (await listRetryableJobs(env, MAX_IDS)).map((j) => j.id);
  const r = await retryFailedJobs(env, ids);
  return {
    text: `✅ ${r.retried.length} کار به صف برگشت؛ تا ۵ دقیقه دیگر پردازش می‌شود.${r.skipped ? ` (${r.skipped} مورد قابل‌برگشت نبود)` : ""}`,
    rows: [[["🔁 ناموفق‌ها", "p_fail"], ["🧰 کارها", "p_jobs"]], [HOME]],
  };
}

async function linksScreen(env, selfId, mask, key, note) {
  const links = await listInactiveLinks(env, MAX_IDS);
  const cur = keyOf(links, (l) => l.id);
  let m = mask, msg = note || "";
  if (key !== null && key !== cur) { m = 0; msg = "فهرست تغییر کرد؛ انتخاب‌ها پاک شد."; }
  if (!links.length) {
    return { text: ["☑️ **انتخاب لینک‌ها**", ...(msg ? [msg] : []), "لینک غیرفعالی برای حذف نیست."].join("\n"), rows: [[["🔗 لینک‌ها", "p_links"], HOME]] };
  }
  const page = links.slice(0, PAGE);
  const chosen = page.filter((_, i) => bit(m, i)).length;
  const lines = page.map((l, i) => `${bit(m, i) ? "☑️" : "⬜"} ${i + 1}) ${when(l.created_at)} ${shortLink(l.url)} (${tag(l.status, 20)})`);
  const toggles = chunk(page.map((_, i) => [`${bit(m, i) ? "☑️" : "⬜"} ${i + 1}`, `p_lsel_${m ^ (1 << i)}_${cur}`]), 3);
  return {
    text: ["☑️ **انتخاب لینک‌های غیرفعال**", ...(msg ? [msg] : []), `${links.length} لینک غیرفعال${links.length > PAGE ? ` (${PAGE} مورد اول نمایش داده می‌شود)` : ""}`, ...lines, "", "فقط لینک‌های غیرفعال حذف می‌شوند؛ لینک‌های در حال کار هرگز حذف نمی‌شوند."].join("\n"),
    rows: [
      ...toggles,
      ...(chosen > 0 ? [[[`🗑 حذف انتخاب‌شده‌ها (${chosen})`, `p_lseldel_${m}_${cur}`]]] : []),
      [["🔄 تازه‌سازی", selfId], HOME],
    ],
  };
}

async function linksDel(env, mask, key) {
  const links = await listInactiveLinks(env, MAX_IDS);
  const cur = keyOf(links, (l) => l.id);
  if (key !== cur) return linksScreen(env, "p_lsel", 0, null, "فهرست تغییر کرد؛ دوباره انتخاب کن.");
  const ids = links.slice(0, PAGE).filter((_, i) => bit(mask, i)).map((l) => Number(l.id));
  if (!ids.length) return linksScreen(env, "p_lsel", 0, null, "چیزی انتخاب نشده بود.");
  const r = await deleteInactiveLinks(env, ids);
  return { text: `✅ ${r.removed.length} لینک غیرفعال پاک شد.${r.skipped ? ` (${r.skipped} مورد دیگر غیرفعال نبود)` : ""}`, rows: [[["☑️ انتخاب لینک‌ها", "p_lsel"], ["🔗 لینک‌ها", "p_links"]], [HOME]] };
}

export async function buildRetryScreen(env, id) {
  if (id === "p_failall") return jobsAll(env);
  if (id === "p_failall_yes") return jobsAllYes(env);
  const p = parse(id);
  if (!p) return { text: "گزینهٔ نامعتبر.", rows: [[HOME]] };
  if (p.kind === "fail") return jobsScreen(env, id, p.mask, p.key, "");
  if (p.kind === "failgo") return jobsGo(env, p.mask, p.key ?? "");
  if (p.kind === "lsel") return linksScreen(env, id, p.mask, p.key, "");
  return linksDel(env, p.mask, p.key ?? "");
}
