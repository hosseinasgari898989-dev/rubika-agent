// Owner panel on Cloudflare (stage 1): instant Rubika webhook, owner claim, menu, live screens.
// Heavy work (video, edits, YouTube) stays on GitHub Actions; this module never runs shell or publishes.
const RUBIKA_API = "https://botapi.rubika.ir/v3";
const HOOK_TYPES = ["ReceiveUpdate", "ReceiveInlineMessage", "ReceiveQuery"];
const MAX_BODY = 64 * 1024;
const PRIVATE_TYPES = new Set(["private", "privatechat", "user", "userchat", "personal"]);
const X_LINK = /https?:\/\/(?:(?:www|mobile)\.)?(?:x\.com|twitter\.com)\/([A-Za-z0-9_]{1,15})\/status\/([0-9]{15,22})/i;
const te = new TextEncoder();

export class PanelError extends Error {
  constructor(code) {
    const c = String(code || "").toLowerCase();
    super(/^[a-z0-9_-]{1,80}$/.test(c) ? c : "panel_error");
    this.code = this.message;
  }
}

const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json" } });

export function safeEqual(a, b) {
  const x = te.encode(String(a ?? ""));
  const y = te.encode(String(b ?? ""));
  let d = x.length ^ y.length;
  const n = Math.max(x.length, y.length);
  for (let i = 0; i < n; i++) d |= (x[i] ?? 0) ^ (y[i] ?? 0);
  return d === 0;
}

export async function hookSecret(env) {
  if (!env.RUBIKA_TOKEN || env.RUBIKA_TOKEN.length < 16) throw new PanelError("rubika_token_missing");
  const key = await crypto.subtle.importKey("raw", te.encode(env.RUBIKA_TOKEN), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = new Uint8Array(await crypto.subtle.sign("HMAC", key, te.encode("owner-panel-webhook-v1")));
  return [...sig].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 40);
}

// ---------- rich text: **bold** __italic__ --underline-- ~~strike~~ ||spoiler|| `mono` ```pre``` [text](url)
const MD = /```([\s\S]*?)```|\*\*(.+?)\*\*|`([^`\n]+?)`|__(.+?)__|--(.+?)--|~~(.+?)~~|\|\|(.+?)\|\||\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/gs;
const MD_TYPES = ["Pre", "Bold", "Mono", "Italic", "Underline", "Strike", "Spoiler"];
export function md(src) {
  src = String(src ?? "");
  let out = "";
  let last = 0;
  const parts = [];
  for (const m of src.matchAll(MD)) {
    out += src.slice(last, m.index);
    let type;
    let inner;
    let extra = {};
    if (m[8] !== undefined) {
      type = "Link"; inner = m[8]; extra = { link_url: m[9] };
    } else {
      const i = m.findIndex((v, k) => k >= 1 && k <= 7 && v !== undefined) - 1;
      type = MD_TYPES[i]; inner = m[i + 1];
    }
    parts.push({ type, from_index: out.length, length: inner.length, ...extra });
    out += inner;
    last = m.index + m[0].length;
  }
  out += src.slice(last);
  return parts.length ? { text: out, metadata: { meta_data_parts: parts } } : { text: out };
}
const esc = (s) => String(s ?? "").replace(/\*\*|__|--|~~|\|\||`|\[|\]/g, "");

// ---------- keypads
const kb = (rows) => ({ rows: rows.map((r) => ({ buttons: r.map(([t, id]) => ({ id, type: "Simple", button_text: t })) })) });
const HOME_KB = kb([
  [["📊 وضعیت", "p_status"], ["📦 صف", "p_queue"]],
  [["🤖 هوش مصنوعی", "p_ai"], ["🧾 گزارش کامل", "p_report"]],
  [["📰 خبر Mob از لینک X", "p_mob"]],
  [["❓ راهنما", "p_help"]],
]);
const BACK_KB = kb([[["🏠 منوی اصلی", "p_home"]]]);
const CANCEL_KB = kb([[["✖️ لغو", "p_home"]]]);
const refreshKb = (id) => kb([[["🔄 تازه‌سازی", id], ["🏠 منوی اصلی", "p_home"]]]);
const HOME_TEXT = "🤖 **پنل مدیریت Rubika-Agent**\n\nیکی از بخش‌ها را انتخاب کن:";
const HELP_TEXT = "❓ **راهنما**\n\n• /menu یا «منو»: منوی اصلی\n• /status وضعیت، /queue صف، /ai هوش مصنوعی، /report گزارش کامل\n• /debug: خطای آخر و مشخصات امن رویداد آخر\n• /register ثبت دوبارهٔ اتصال لحظه‌ای\n\nاین نسخه فقط گزارش می‌دهد و لینک X را ذخیره می‌کند. بخش‌های بعدی مرحله‌به‌مرحله اضافه می‌شوند.";

// ---------- event normalisation (webhook shapes: {update}, {inline_message}, or a bare update)
const bad = (o, k) => o && typeof o === "object" && o[k] === true;
export function normalize(body) {
  if (!body || typeof body !== "object") return null;
  const u = body.update && typeof body.update === "object" ? body.update : body;
  const im = body.inline_message ?? u.inline_message;
  let m;
  let kind;
  if (im && typeof im === "object") { m = im; kind = "click"; }
  else if (u.type === "NewMessage" && u.new_message && typeof u.new_message === "object") {
    m = u.new_message;
    kind = m.aux_data && m.aux_data.button_id ? "click" : "text";
  } else return null;
  const chat = u.chat_id ?? m.chat_id ?? body.chat_id;
  const button = String((m.aux_data && m.aux_data.button_id) ?? m.button_id ?? "").replace(/[^A-Za-z0-9_-]/g, "").slice(0, 60);
  const types = [u.chat_type, m.chat_type, u.chat && u.chat.type, u.chat && u.chat.chat_type]
    .filter((v) => typeof v === "string" && v.trim())
    .map((v) => v.trim().toLowerCase().replace(/[_ ]/g, ""));
  return {
    kind, chat: typeof chat === "string" ? chat : "", mid: typeof m.message_id === "string" ? m.message_id : "",
    text: typeof m.text === "string" ? m.text.trim().slice(0, 3400) : "", button, types,
    isGroupish: [u, m, u.chat].some((o) => bad(o, "is_group") || bad(o, "is_channel")),
    botAuthored: String(m.sender_type || u.sender_type || "").toLowerCase() === "bot"
      || [u, m].some((o) => bad(o, "is_bot") || bad(o, "is_self") || bad(o, "is_me")),
  };
}
export function isPrivateChat(ev) {
  if (!ev.chat || ev.isGroupish || ev.botAuthored) return false;
  const l = ev.chat.toLowerCase();
  if (l.startsWith("g0") || l.startsWith("c0")) return false;
  if (ev.types.length) return ev.types.every((t) => PRIVATE_TYPES.has(t));
  return l.startsWith("b0");
}
export function parseXLink(text) {
  const m = X_LINK.exec(String(text || ""));
  return m ? `https://x.com/${m[1]}/status/${m[2]}` : null;
}

// ---------- Rubika Bot API (token never appears in errors/logs)
async function rubika(env, method, payload) {
  const methodName = String(method || "unknown").toLowerCase().replace(/[^a-z0-9_-]/g, "").slice(0, 40) || "unknown";
  let res;
  try {
    res = await fetch(RUBIKA_API + "/" + env.RUBIKA_TOKEN + "/" + method, {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(payload),
    });
  } catch { throw new PanelError("rubika_" + methodName + "_network"); }
  let data = null;
  try { data = await res.json(); } catch { /* ignore */ }
  if (!res.ok || !data || data.status !== "OK") {
    const statusCode = res.ok
      ? String(data && data.status || "bad").toLowerCase().replace(/[^a-z0-9_-]/g, "_").slice(0, 30)
      : "http_" + res.status;
    throw new PanelError("rubika_" + methodName + "_" + statusCode);
  }
  return data;
}
const responseMessageId = (response) => {
  const mid = response && response.data && response.data.message_id;
  if (typeof mid !== "string" || !mid) throw new PanelError("rubika_sendmessage_no_message_id");
  return mid;
};
async function send(env, chat, src, keypad) {
  const f = md(src);
  const hasKeypad = Boolean(keypad && Array.isArray(keypad.rows) &&
    keypad.rows.some((row) => Array.isArray(row.buttons) && row.buttons.length > 0));
  const response = await rubika(env, "sendMessage", {
    chat_id: chat, text: f.text, ...(f.metadata ? { metadata: f.metadata } : {}),
    ...(hasKeypad ? { inline_keypad: keypad } : {}),
  });
  const mid = response && response.data && response.data.message_id;
  if (typeof mid === "string" && mid) {
    try { await setState(env, "panel_mid", mid); } catch { /* keep delivery successful if state storage is temporarily unavailable */ }
  }
  return response;
}
async function deletePreviousPanel(env, chat, oldMid, newMid) {
  if (!oldMid || !newMid || oldMid === newMid) return;
  try { await rubika(env, "deleteMessage", { chat_id: chat, message_id: oldMid }); } catch { /* best effort: keep navigation available */ }
}
async function sendFreshPanel(ui, src, keypad, oldMid) {
  const response = await send(ui.env, ui.chat, src, keypad);
  await deletePreviousPanel(ui.env, ui.chat, oldMid, responseMessageId(response));
  return response;
}
async function bumpCounter(env, key) {
  const raw = await getState(env, key, "0");
  const current = Number(raw);
  const next = (Number.isSafeInteger(current) && current >= 0 ? current : 0) + 1;
  await setState(env, key, next);
  return next;
}
async function edit(env, chat, mid, src, keypad) {
  const f = md(src);
  const storedMid = await getState(env, "panel_mid", "");
  const targetMid = storedMid || mid;
  const base = { chat_id: chat, message_id: targetMid, text: f.text };
  const hasKeypad = Boolean(keypad && Array.isArray(keypad.rows) &&
    keypad.rows.some((row) => Array.isArray(row.buttons) && row.buttons.length > 0));
  try {
    await rubika(env, "editMessageText", f.metadata ? { ...base, metadata: f.metadata } : base);
    if (hasKeypad) await rubika(env, "editMessageKeypad", { chat_id: chat, message_id: targetMid, inline_keypad: keypad });
    try { await bumpCounter(env, "panel_edit_ok"); } catch { /* diagnostics must not block panel rendering */ }
    return targetMid;
  } catch (e) {
    const code = e && typeof e.code === "string" && /^[a-z0-9_-]{1,80}$/i.test(e.code)
      ? e.code.toLowerCase() : "panel_edit_failed";
    try { await setState(env, "last_error", code); } catch { /* keep the response fallback available */ }
    try { await bumpCounter(env, "panel_edit_fail"); } catch { /* diagnostics must not block panel rendering */ }
    const replacement = await send(env, chat, src, hasKeypad ? keypad : undefined);
    const replacementMid = responseMessageId(replacement);
    await deletePreviousPanel(env, chat, targetMid, replacementMid);
    return replacementMid;
  }
}
async function registerWebhook(env, origin) {
  const s = await hookSecret(env);
  for (const type of HOOK_TYPES) await rubika(env, "updateBotEndpoints", { url: `${origin}/panel/hook/${s}/${type}`, type });
  return HOOK_TYPES;
}

// ---------- D1 helpers
const getOwner = async (env) => (await env.DB.prepare("SELECT chat_id FROM panel_owner WHERE id=1").first())?.chat_id ?? null;
const getState = async (env, k, d) => (await env.DB.prepare("SELECT value FROM panel_state WHERE key=?").bind(k).first())?.value ?? d;
const setState = (env, k, v) => env.DB.prepare(
  "INSERT INTO panel_state(key,value,updated_at) VALUES(?,?,datetime('now')) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
).bind(k, String(v)).run();
async function firstSeen(env, key) {
  const r = await env.DB.prepare("INSERT OR IGNORE INTO panel_events(event_key) VALUES(?)").bind(key).run();
  if (Math.random() < 0.05) await env.DB.prepare("DELETE FROM panel_events WHERE created_at < datetime('now','-2 days')").run();
  return r.meta?.changes === 1;
}

// ---------- report formatting
const n = (v) => (Number.isInteger(v) && v >= 0 ? v : 0);
const clean = (v, max = 20) => String(v ?? "").toLowerCase().replace(/[^a-z0-9_-]/g, "").slice(0, max);
export function fmtStatus(s) {
  if (!s || s.ok !== true) return "❌ وضعیت Worker فعلاً در دسترس نیست.";
  const agent = s.agent && typeof s.agent === "object" ? s.agent : {};
  const pub = (s.capabilities && s.capabilities.publishing === true) || s.publishing === true;
  const rows = Array.isArray(s.item_counts) ? s.item_counts.slice(0, 8) : [];
  const counts = rows.filter((r) => r && typeof r === "object" && clean(r.status)).map((r) => `${clean(r.status)}: ${n(r.total)}`);
  const run = Array.isArray(s.recent_runs) && s.recent_runs[0] && typeof s.recent_runs[0] === "object" ? s.recent_runs[0] : null;
  return [
    "📊 **وضعیت Rubika-Agent**",
    `• پایگاه داده: ${s.database === "connected" ? "متصل" : "نامشخص"}`,
    `• عامل: ${agent.enabled === 1 ? "روشن" : "خاموش"}`,
    `• انتشار از این API: ${pub ? "فعال" : "غیرفعال"}`,
    `• آیتم‌ها: ${counts.length ? counts.join(" | ") : "داده‌ای ثبت نشده"}`,
    `• آخرین اجرا: ${run ? clean(run.status) || "unknown" : "ندارد"}`,
  ].join("\n");
}
export function fmtQueue(q) {
  if (!q || q.ok !== true) return "❌ خلاصهٔ صف فعلاً در دسترس نیست.";
  const qc = q.queue_counts && typeof q.queue_counts === "object" ? q.queue_counts : {};
  const names = [["queued", "در انتظار"], ["processing", "در حال پردازش"], ["sent", "ارسال‌شده"], ["failed", "ناموفق"], ["uncertain", "نامشخص"], ["skipped", "ردشده"]];
  return ["📦 **صف انتشار**", names.map(([k, l]) => `${l}: ${n(qc[k])}`).join(" | "), `• ارسال‌شده امروز: ${n(q.sent_today)}`].join("\n");
}
export function fmtAi(p, d) {
  const L = p.toUpperCase();
  if (!d || d.ok !== true) return `🤖 ${L}: در دسترس نیست (${clean(d && d.error, 60) || "unknown"})`;
  const state = d.cooldown_active === true ? "در دورهٔ توقف" : d.ready === true ? "آماده طبق ledger" : "غیرآماده";
  return `🤖 **${L}**: ${state}\nسهمیهٔ امروز: ${n(d.calls_used)}/${n(d.daily_limit)}، باقی‌مانده: ${n(d.remaining)}\nخطای اخیر: ${clean(d.last_error_code, 60) || "ندارد"}`;
}

// ---------- screens
async function api(ui, path) {
  try {
    const res = await ui.selfFetch(new Request(ui.origin + path, { headers: { authorization: `Bearer ${ui.env.AGENT_API_TOKEN}`, accept: "application/json" } }));
    return await res.json();
  } catch { return { ok: false }; }
}
const aiBlock = async (ui) => (await Promise.all(["groq", "openrouter"].map(async (p) => fmtAi(p, await api(ui, `/api/brain/ai-provider/status?provider=${p}`))))).join("\n\n");

async function debugText(env) {
  const lastError = clean(await getState(env, "last_error", "none"), 80) || "none";
  const readCounter = async (key) => {
    const value = Number(await getState(env, key, "0"));
    return Number.isSafeInteger(value) && value >= 0 ? value : 0;
  };
  const editOk = await readCounter("panel_edit_ok");
  const editFail = await readCounter("panel_edit_fail");
  let event = null;
  try { event = JSON.parse(await getState(env, "last_event", "null")); } catch { /* invalid older state */ }
  const types = Array.isArray(event && event.types)
    ? event.types.map((value) => clean(value, 24)).filter(Boolean).slice(0, 4).join(", ") || "none"
    : "none";
  return [
    "🛠️ **Debug پنل**",
    "",
    "last_error: " + lastError,
    "panel_edit_ok: " + editOk,
    "panel_edit_fail: " + editFail,
    "last_event:",
    "kind: " + (clean(event && event.kind, 16) || "none"),
    "types: " + types,
    "chat_tail: " + String(event && event.chat_tail || "none").slice(-4),
    "message_id_tail: " + String(event && event.message_id_tail || "none").slice(-4),
    "button: " + (clean(event && event.button, 60) || "none"),
    "botAuthored: " + (event && event.botAuthored === true ? "true" : "false"),
  ].join("\n");
}

async function renderData(ui, id, { fresh = false, clickedMid = "" } = {}) {
  const env = ui.env;
  const chat = ui.chat;
  const previousMid = await getState(env, "panel_mid", "");
  let mid;
  if (fresh || !previousMid) {
    const response = fresh
      ? await sendFreshPanel(ui, "⏳ در حال دریافت…", refreshKb(id), previousMid)
      : await send(env, chat, "⏳ در حال دریافت…", refreshKb(id));
    mid = responseMessageId(response);
  } else {
    const targetMid = clickedMid && clickedMid === previousMid ? clickedMid : previousMid;
    mid = await edit(env, chat, targetMid, "⏳ در حال دریافت…", refreshKb(id));
  }

  let out;
  if (id === "p_status") out = fmtStatus(await api(ui, "/api/status"));
  else if (id === "p_queue") out = fmtQueue(await api(ui, "/api/brain/owner-console/queue-summary"));
  else if (id === "p_ai") out = "🤖 **دفتر سهمیهٔ هوش مصنوعی**\n\n" + await aiBlock(ui);
  else {
    const parts = ["🧾 **گزارش Rubika-Agent**"];
    mid = await edit(env, chat, mid, "⏳ ۱ از ۳ — وضعیت Worker…", refreshKb(id));
    parts.push(fmtStatus(await api(ui, "/api/status")));
    mid = await edit(env, chat, mid, "⏳ ۲ از ۳ — صف انتشار…", refreshKb(id));
    parts.push(fmtQueue(await api(ui, "/api/brain/owner-console/queue-summary")));
    mid = await edit(env, chat, mid, "⏳ ۳ از ۳ — هوش مصنوعی…", refreshKb(id));
    parts.push(await aiBlock(ui));
    out = parts.join("\n\n");
  }
  await edit(env, chat, mid, out, refreshKb(id));
}

async function render(ui, id, { fresh = false, clickedMid = "" } = {}) {
  const env = ui.env;
  const chat = ui.chat;
  if (id !== "p_mob") await setState(env, "mode", "idle");
  const statics = {
    p_home: [HOME_TEXT, HOME_KB], p_help: [HELP_TEXT, BACK_KB],
    p_mob: ["📰 **خبر Mob از لینک X**\n\nلینک پست X را بفرست (x.com/نام/status/شناسه).", CANCEL_KB],
  };
  if (id === "p_mob") await setState(env, "mode", "waiting_link");
  const previousMid = await getState(env, "panel_mid", "");
  const targetMid = clickedMid && clickedMid === previousMid ? clickedMid : previousMid;
  const s = statics[id];
  if (s) {
    if (fresh) return sendFreshPanel(ui, s[0], s[1], previousMid);
    if (targetMid) return edit(env, chat, targetMid, s[0], s[1]);
    return send(env, chat, s[0], s[1]);
  }
  if (id === "p_debug") {
    const text = await debugText(env);
    if (fresh) return sendFreshPanel(ui, text, BACK_KB, previousMid);
    if (targetMid) return edit(env, chat, targetMid, text, BACK_KB);
    return send(env, chat, text, BACK_KB);
  }
  if (["p_status", "p_queue", "p_ai", "p_report"].includes(id)) return renderData(ui, id, { fresh, clickedMid });
  return render(ui, "p_home", { fresh, clickedMid });
}

const COMMANDS = {
  "/start": "p_home", "/menu": "p_home", "منو": "p_home", "/help": "p_help", "/status": "p_status",
  "/queue": "p_queue", "/ai": "p_ai", "/ai-status": "p_ai", "/report": "p_report", "/debug": "p_debug",
};

async function tryClaim(env, ev) {
  const m = /^\/claim\s+(\S{1,128})\s*$/.exec(ev.text);
  if (!m) return;
  if (Number(await getState(env, "claim_fails", "0")) >= 5) return;
  const code = env.OWNER_SETUP_CODE || "";
  if (code.length < 8) { await send(env, ev.chat, "⚠️ کد راه‌اندازی در Worker تنظیم نشده یا کوتاه‌تر از ۸ نویسه است."); return; }
  if (!safeEqual(m[1], code)) {
    await setState(env, "claim_fails", Number(await getState(env, "claim_fails", "0")) + 1);
    await send(env, ev.chat, "❌ کد نادرست است.");
    return;
  }
  const r = await env.DB.prepare("INSERT OR IGNORE INTO panel_owner(id,chat_id) VALUES(1,?)").bind(ev.chat).run();
  if (r.meta?.changes !== 1) return;
  try { await rubika(env, "deleteMessage", { chat_id: ev.chat, message_id: ev.mid }); } catch { /* best effort */ }
  await send(env, ev.chat, "✅ **مالک ثبت شد.** از حالا فقط همین چت به پنل دسترسی دارد.\n\n" + HOME_TEXT, HOME_KB);
}

const idTail = (value) => String(value ?? "").slice(-4);
async function processEvent(env, selfFetch, origin, ev) {
  if (!ev) return;
  const owner = await getOwner(env);
  if (!owner) {
    if (ev.kind === "text" && !ev.botAuthored && isPrivateChat(ev)) await tryClaim(env, ev);
    return;
  }
  if (!safeEqual(owner, ev.chat)) return;
  const lastEvent = {
    kind: clean(ev.kind, 16) || "unknown",
    types: Array.isArray(ev.types) ? ev.types.slice(0, 4).map((value) => clean(value, 24)).filter(Boolean) : [],
    chat_tail: idTail(ev.chat),
    message_id_tail: idTail(ev.mid),
    button: clean(ev.button, 60),
    botAuthored: ev.botAuthored === true,
  };
  try { await setState(env, "last_event", JSON.stringify(lastEvent)); } catch { /* diagnostics must not block owner commands */ }
  if (ev.botAuthored || !isPrivateChat(ev)) return;
  const key = ev.kind === "click"
    ? "c|" + ev.chat + "|" + ev.mid + "|" + ev.button + "|" + Math.floor(Date.now() / 4000)
    : "t|" + ev.chat + "|" + ev.mid;
  if (!(await firstSeen(env, key))) return;
  const ui = { env, chat: ev.chat, selfFetch, origin };
  try {
    if (ev.kind === "click") { await render(ui, ev.button, { clickedMid: ev.mid }); return; }
    const word = ev.text.split(/\s+/)[0].split("@")[0].toLowerCase();
    if (word === "/register") { await registerWebhook(env, origin); await send(env, ev.chat, "✅ اتصال لحظه‌ای دوباره ثبت شد."); return; }
    if (COMMANDS[word]) { await render(ui, COMMANDS[word], { fresh: true }); return; }
    if ((await getState(env, "mode", "idle")) === "waiting_link") {
      const link = parseXLink(ev.text);
      if (!link) { await send(env, ev.chat, "لینک معتبر نیست. شکل درست: x.com/نام/status/شناسه", CANCEL_KB); return; }
      await env.DB.prepare("INSERT OR IGNORE INTO panel_links(url) VALUES(?)").bind(link).run();
      await setState(env, "mode", "idle");
      await send(env, ev.chat, "✅ لینک ذخیره شد:\n" + String.fromCharCode(96) + link + String.fromCharCode(96) + "\nپردازش (ترجمه، دانلود، انتشار) در مرحلهٔ بعد اضافه می‌شود.", HOME_KB);
      return;
    }
    await send(env, ev.chat, "برای شروع «منو» را بفرست.", HOME_KB);
  } catch (e) {
    try { await send(env, ev.chat, "❌ خطا: " + esc(e && e.code || "unknown"), BACK_KB); } catch { /* ignore */ }
  }
}

// ---------- HTTP entry. Wire in index.js: if (url.pathname.startsWith("/panel/")) return handleOwnerPanel(request, env, ctx, (r) => <worker>.fetch(r, env, ctx));
export async function handleOwnerPanel(request, env, ctx, selfFetch) {
  const url = new URL(request.url);
  if (!env.DB) return json({ ok: false, error: "db_missing" }, 500);
  if (url.pathname === "/panel/setup" && request.method === "GET") {
    const owner = await getOwner(env);
    const bearer = (request.headers.get("authorization") || "").replace(/^Bearer\s+/i, "");
    const codeOk = !owner && (env.OWNER_SETUP_CODE || "").length >= 8 && safeEqual(url.searchParams.get("code") || "", env.OWNER_SETUP_CODE);
    const tokenOk = Boolean(env.AGENT_API_TOKEN) && safeEqual(bearer, env.AGENT_API_TOKEN);
    if (!codeOk && !tokenOk) {
      return json({ ok: false, error: !owner && (env.OWNER_SETUP_CODE || "").length < 8 ? "setup_code_missing_or_short" : "forbidden" }, 403);
    }
    try { return json({ ok: true, registered: await registerWebhook(env, url.origin), owner_claimed: Boolean(owner) }); }
    catch (e) { return json({ ok: false, error: (e && e.code) || "error" }, 502); }
  }
  const m = /^\/panel\/hook\/([a-f0-9]{40})\/(ReceiveUpdate|ReceiveInlineMessage|ReceiveQuery)$/.exec(url.pathname);
  if (!m || request.method !== "POST") return json({ ok: false, error: "not_found" }, 404);
  let expected;
  try { expected = await hookSecret(env); } catch { return json({ ok: false, error: "not_found" }, 404); }
  if (!safeEqual(m[1], expected)) return json({ ok: false, error: "not_found" }, 404);
  if (Number(request.headers.get("content-length") || 0) > MAX_BODY) return json({ ok: false, error: "too_large" }, 413);
  let body = null;
  try { body = await request.json(); } catch { return json({ ok: true }); }
  const ev = normalize(body);
  ctx.waitUntil(processEvent(env, selfFetch, url.origin, ev).catch(() => {}));
  return json({ ok: true });
}
