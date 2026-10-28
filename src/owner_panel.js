// Owner panel on Cloudflare (stage 1): instant Rubika webhook, owner claim, menu, live screens.
// Heavy work (video, edits, YouTube) stays on GitHub Actions; this module never runs shell or publishes.
import { HOME_ROWS, isScreenId, buildScreen, homeSummary } from "./panel_screens.js";
import { isSettingsId, applySettingsAction, settingsScreen } from "./panel_settings.js";

const RUBIKA_API = "https://botapi.rubika.ir/v3";
const HOOK_TYPES = ["ReceiveUpdate", "ReceiveInlineMessage", "ReceiveQuery"];
const MAX_BODY = 64 * 1024;
const PRIVATE_TYPES = new Set(["private", "privatechat", "user", "userchat", "personal"]);
const X_LINK = /^https:\/\/(?:(?:www|mobile)\.)?(?:x\.com|twitter\.com)\/mobgamesstudios\/status\/([0-9]{15,22})\/?$/i;
const te = new TextEncoder();
const PANEL_TIMING = Symbol("panel_timing");
const PANEL_SEQ = Symbol("panel_seq");
const elapsedMs = (startedAt) => Math.max(0, Math.round(performance.now() - startedAt));

export class PanelError extends Error {
  constructor(code) {
    const c = String(code || "").toLowerCase();
    super(/^[a-z0-9_-]{1,80}$/.test(c) ? c : "panel_error");
    this.code = this.message;
  }
}

class StalePanelEvent extends Error {
  constructor() {
    super("panel_stale_event");
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
const HOME_ONLY = [[["🏠 منوی اصلی", "p_home"]]];
const HOME_KB = kb(HOME_ROWS);
const refreshKb = (id) => kb([[["🔄 تازه‌سازی", id], ["🏠 منوی اصلی", "p_home"]]]);
const BACK_KB = refreshKb("p_help");
const CANCEL_KB = kb([
  [["🔄 تازه‌سازی", "p_mob"], ["🏠 منوی اصلی", "p_home"]],
  [["✖️ لغو", "p_home"]],
]);
const HOME_TEXT = "🤖 **پنل مدیریت Rubika-Agent**\n\nیکی از بخش‌ها را انتخاب کن:";
async function homeText(env) {
  try { return await homeSummary(env); } catch { return HOME_TEXT; }
}
const HELP_TEXT = "❓ **راهنما**\n\n• /menu یا «منو»: منوی اصلی\n• /status وضعیت، /queue صف، /ai هوش مصنوعی، /report گزارش کامل\n• /digest خلاصهٔ ۲۴ ساعت، /errors خطاها، /runs اجراها\n• /links لینک‌های X، /ailog تاریخچهٔ AI\n• /debug: خطای آخر و مشخصات امن رویداد آخر\n• /panelmode atomic|inplace: حالت هماهنگی پنل (مالک اصلی)\n• /register ثبت دوبارهٔ اتصال لحظه‌ای\n\nاین نسخه گزارش می‌دهد و لینک X را ذخیره می‌کند؛ انتشار خودکار از این پنل انجام نمی‌شود."; 

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
    time: typeof m.time === "number" && Number.isFinite(m.time) ? m.time : null,
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
  const m = X_LINK.exec(String(text || "").trim());
  return m ? "https://x.com/mobgamesstudios/status/" + m[1] : null;
}

const GITHUB_DISPATCH_WORKFLOWS = new Set(["mob-x-link-processor.yml", "mob-x-news-production.yml"]);

export async function dispatchMobXWorkflow(env, workflow, inputs) {
  if (!GITHUB_DISPATCH_WORKFLOWS.has(String(workflow || ""))) return { ok: false, error: "github_dispatch_workflow_not_allowed" };
  const token = typeof env?.GITHUB_DISPATCH_TOKEN === "string" ? env.GITHUB_DISPATCH_TOKEN.trim() : "";
  if (!token) return { ok: false, error: "github_dispatch_not_configured" };
  let cleanInputs;
  if (workflow === "mob-x-link-processor.yml") {
    const panelLinkId = Number(inputs && inputs.panel_link_id);
    if (!Number.isSafeInteger(panelLinkId) || panelLinkId < 1) return { ok: false, error: "github_dispatch_input_invalid" };
    cleanInputs = { panel_link_id: String(panelLinkId) };
  } else {
    const jobId = String(inputs && inputs.panel_link_job_id || "").trim();
    if (!/^job_[A-Fa-f0-9]{8,80}$/.test(jobId)) return { ok: false, error: "github_dispatch_input_invalid" };
    cleanInputs = { panel_link_job_id: jobId };
  }
  const endpoint = "https://api.github.com/repos/hosseinasgari898989-dev/rubika-agent/actions/workflows/" + workflow + "/dispatches";
  let lastError = "github_dispatch_network";
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    let response;
    try {
      response = await fetch(endpoint, {
        method: "POST",
        headers: {
          authorization: "Bearer " + token,
          accept: "application/vnd.github+json",
          "x-github-api-version": "2022-11-28",
          "content-type": "application/json",
        },
        body: JSON.stringify({ ref: "main", inputs: cleanInputs }),
        signal: AbortSignal.timeout(2500),
      });
    } catch (error) {
      lastError = error && (error.name === "TimeoutError" || error.name === "AbortError") ? "github_dispatch_timeout" : "github_dispatch_network";
      if (attempt < 3) await new Promise(resolve => setTimeout(resolve, attempt * 120));
      continue;
    }
    if (response.ok) return { ok: true };
    const status = Number(response.status) || 0;
    lastError = status ? "github_dispatch_http_" + status : "github_dispatch_http_unknown";
    if (status !== 429 && status < 500) return { ok: false, error: lastError };
    if (attempt < 3) await new Promise(resolve => setTimeout(resolve, attempt * 120));
  }
  return { ok: false, error: lastError };
}

export async function dispatchMobXJobOnce(env, jobId, markerKey, workflow, inputs, allowedStatuses) {
  if (!["link_dispatch_state", "publish_dispatch_state"].includes(markerKey)) return { ok: false, error: "mob_x_dispatch_marker_invalid" };
  const allowed = Array.isArray(allowedStatuses) ? allowedStatuses.filter(status => ["confirmed", "queued", "approved"].includes(status)) : [];
  if (!allowed.length) return { ok: false, error: "mob_x_dispatch_status_invalid" };
  const row = await env.DB.prepare("SELECT id, status, params_json FROM console_jobs WHERE id = ? AND type = 'mob_x_link'").bind(jobId).first();
  if (!row || !allowed.includes(row.status)) return { ok: true, skipped: true };
  const rawParams = typeof row.params_json === "string" && row.params_json ? row.params_json : "{}";
  let params = {};
  try { params = JSON.parse(rawParams); } catch {}
  if (!params || typeof params !== "object" || Array.isArray(params)) params = {};
  if (params[markerKey] === "dispatched" || params[markerKey] === "dispatching") return { ok: true, skipped: true };
  const reservedParams = { ...params, [markerKey]: "dispatching" };
  delete reservedParams[markerKey + "_error"];
  const reservedJson = JSON.stringify(reservedParams);
  const statusSql = allowed.map(status => "'" + status + "'").join(",");
  const reserved = await env.DB.prepare(
    "UPDATE console_jobs SET params_json = ?, updated_at = ? WHERE id = ? AND status IN (" + statusSql + ") AND params_json = ?"
  ).bind(reservedJson, new Date().toISOString(), jobId, rawParams).run();
  if (Number(reserved && reserved.meta && reserved.meta.changes || 0) !== 1) return { ok: true, skipped: true };
  const result = await dispatchMobXWorkflow(env, workflow, inputs);
  const finalParams = { ...reservedParams, [markerKey]: result.ok ? "dispatched" : "failed" };
  if (!result.ok) finalParams[markerKey + "_error"] = result.error;
  try {
    await env.DB.prepare("UPDATE console_jobs SET params_json = ?, updated_at = ? WHERE id = ?")
      .bind(JSON.stringify(finalParams), new Date().toISOString(), jobId).run();
  } catch { /* keep errors sanitized; scheduled fallback remains available */ }
  return { ...result, skipped: false };
}

// ---------- Rubika Bot API (token never appears in errors/logs)
async function rubika(env, method, payload) {
  const methodName = String(method || "unknown").toLowerCase().replace(/[^a-z0-9_-]/g, "").slice(0, 40) || "unknown";
  const timing = env && env[PANEL_TIMING];
  if (timing) timing.rubika_calls += 1;
  const startedAt = performance.now();
  try {
    if (method !== "deleteMessage") await assertPanelEventCurrent(env);
    let res;
    try {
      res = await fetch(RUBIKA_API + "/" + env.RUBIKA_TOKEN + "/" + method, {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify(payload), signal: AbortSignal.timeout(12_000),
      });
    } catch (error) {
      if (error instanceof PanelError) throw error;
      if (error && (error.name === "TimeoutError" || error.name === "AbortError")) {
        throw new PanelError("rubika_" + methodName + "_timeout");
      }
      throw new PanelError("rubika_" + methodName + "_network");
    }
    let data = null;
    try { data = await res.json(); } catch { /* ignore */ }
    if (!res.ok || !data || data.status !== "OK") {
      const statusCode = res.ok
        ? String(data && data.status || "bad").toLowerCase().replace(/[^a-z0-9_-]/g, "_").slice(0, 30)
        : "http_" + res.status;
      throw new PanelError("rubika_" + methodName + "_" + statusCode);
    }
    if (method !== "deleteMessage") {
      try {
        await assertPanelEventCurrent(env);
      } catch (error) {
        // A sendMessage may have succeeded after a newer panel event overtook it.
        // Remove that late response so its stale keypad cannot remain actionable.
        const staleMid = data && data.data && data.data.message_id;
        if (error instanceof StalePanelEvent && method === "sendMessage" &&
            typeof staleMid === "string" && staleMid) {
          try { await rubika(env, "deleteMessage", { chat_id: payload && payload.chat_id, message_id: staleMid }); } catch {}
        }
        throw error;
      }
    }
    return data;
  } finally {
    if (timing) timing.rubika_ms += elapsedMs(startedAt);
  }
}
const responseMessageId = (response) => {
  const mid = response && response.data && response.data.message_id;
  if (typeof mid !== "string" || !mid) throw new PanelError("rubika_sendmessage_no_message_id");
  return mid;
};
async function send(env, chat, src, keypad) {
  await assertPanelEventCurrent(env);
  const f = md(src);
  const hasKeypad = Boolean(keypad && Array.isArray(keypad.rows) &&
    keypad.rows.some((row) => Array.isArray(row.buttons) && row.buttons.length > 0));
  const response = await rubika(env, "sendMessage", {
    chat_id: chat, text: f.text, ...(f.metadata ? { metadata: f.metadata } : {}),
    ...(hasKeypad ? { inline_keypad: keypad } : {}),
  });
  const mid = response && response.data && response.data.message_id;
  try {
    await assertPanelEventCurrent(env);
  } catch (error) {
    if (error instanceof StalePanelEvent && typeof mid === "string" && mid) {
      try { await rubika(env, "deleteMessage", { chat_id: chat, message_id: mid }); } catch {}
    }
    throw error;
  }
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
  await assertPanelEventCurrent(env);
  const f = md(src);
  const storedMid = await getState(env, "panel_mid", "");
  const targetMid = storedMid || mid;
  const base = { chat_id: chat, message_id: targetMid, text: f.text };
  const hasKeypad = Boolean(keypad && Array.isArray(keypad.rows) &&
    keypad.rows.some((row) => Array.isArray(row.buttons) && row.buttons.length > 0));
  const syncMode = await getState(env, "panel_sync_mode", "atomic");
  // Atomic mode sends the full text+keypad together. Only explicit "inplace" opts into two edits.
  if (syncMode !== "inplace") {
    try {
      const replacement = await send(env, chat, src, hasKeypad ? keypad : undefined);
      const replacementMid = responseMessageId(replacement);
      await assertPanelEventCurrent(env);
      await deletePreviousPanel(env, chat, targetMid, replacementMid);
      try { await bumpCounter(env, "panel_edit_ok"); } catch { /* metrics must never block panel sync */ }
      try { await bumpCounter(env, "atomic_ok"); } catch { /* metrics must never block panel sync */ }
      return replacementMid;
    } catch (error) {
      if (error instanceof StalePanelEvent) throw error;
      const code = error && typeof error.code === "string" && /^[a-z0-9_-]{1,80}$/i.test(error.code)
        ? error.code.toLowerCase() : "panel_atomic_failed";
      try { await setState(env, "last_error", code); } catch { /* best effort diagnostics */ }
      try { await bumpCounter(env, "panel_edit_fail"); } catch { /* best effort diagnostics */ }
      try { await bumpCounter(env, "atomic_fail"); } catch { /* best effort diagnostics */ }
      // Do not fall back to in-place edits: the current panel remains intact on send failure.
      return targetMid;
    }
  }
  try {
    const updateKeypad = async () => {
      if (!hasKeypad) return;
      try {
        await rubika(env, "editMessageKeypad", { chat_id: chat, message_id: targetMid, inline_keypad: keypad });
      } catch (firstKeypadError) {
        if (firstKeypadError instanceof StalePanelEvent) throw firstKeypadError;
        try {
          await assertPanelEventCurrent(env);
          await rubika(env, "editMessageKeypad", { chat_id: chat, message_id: targetMid, inline_keypad: keypad });
        } catch (retryKeypadError) {
          throw retryKeypadError || firstKeypadError;
        }
      }
    };
    const textPromise = rubika(env, "editMessageText", f.metadata ? { ...base, metadata: f.metadata } : base);
    const results = await Promise.allSettled([textPromise, updateKeypad()]);
    const stale = results.find((result) => result.status === "rejected" && result.reason instanceof StalePanelEvent);
    if (stale) throw stale.reason;
    const failed = results.find((result) => result.status === "rejected");
    if (failed) throw failed.reason;
    await assertPanelEventCurrent(env);
    try { await bumpCounter(env, "panel_edit_ok"); } catch { /* diagnostics must not block panel rendering */ }
    return targetMid;
  } catch (e) {
    if (e instanceof StalePanelEvent) throw e;
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
const hasOwnerAccess = async (env, chatId, primaryOwner) => {
  try {
    const row = await env.DB.prepare("SELECT id FROM console_owners WHERE chat_id = ? AND enabled = 1").bind(chatId).first();
    if (row) return true;
    const known = await env.DB.prepare("SELECT id, enabled FROM console_owners WHERE chat_id = ?").bind(chatId).first();
    if (known) return false;
    // Keep the legacy singleton owner usable until migration 0010 has populated console_owners.
    return safeEqual(primaryOwner || "", chatId);
  } catch {
    return safeEqual(primaryOwner || "", chatId);
  }
};
const sha256Hex = async (value) => {
  const digest = await crypto.subtle.digest("SHA-256", te.encode(value));
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
};
const getState = async (env, k, d) => (await env.DB.prepare("SELECT value FROM panel_state WHERE key=?").bind(k).first())?.value ?? d;
async function nextPanelSeq(env) {
  const row = await env.DB.prepare(
    "INSERT INTO panel_state(key,value,updated_at) VALUES('panel_seq','1',datetime('now')) " +
    "ON CONFLICT(key) DO UPDATE SET value = CAST(panel_state.value AS INTEGER) + 1, updated_at = datetime('now') RETURNING value",
  ).first();
  const seq = Number(row && row.value);
  if (!Number.isSafeInteger(seq) || seq < 1) throw new PanelError("panel_seq_unavailable");
  return seq;
}
async function assertPanelEventCurrent(env) {
  if (typeof env[PANEL_SEQ] !== "number") return;
  const current = Number(await getState(env, "panel_seq", "0"));
  if (current === env[PANEL_SEQ]) return;
  try { await bumpCounter(env, "panel_stale_skip"); } catch { /* stale skip diagnostics are best effort */ }
  throw new StalePanelEvent();
}
const setState = (env, k, v) => env.DB.prepare(
  "INSERT INTO panel_state(key,value,updated_at) VALUES(?,?,datetime('now')) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
).bind(k, String(v)).run();

function timedStatement(statement, timing) {
  return new Proxy(statement, {
    get(target, property) {
      const value = Reflect.get(target, property, target);
      if (property === "bind" && typeof value === "function") {
        return (...args) => timedStatement(value.apply(target, args), timing);
      }
      if (["first", "run", "all", "raw", "exec"].includes(String(property)) && typeof value === "function") {
        return async (...args) => {
          const startedAt = performance.now();
          try { return await value.apply(target, args); }
          finally { timing.d1_ms += elapsedMs(startedAt); }
        };
      }
      return typeof value === "function" ? value.bind(target) : value;
    },
  });
}
function withPanelTiming(env, timing) {
  const scoped = Object.create(env);
  Object.defineProperty(scoped, PANEL_TIMING, { value: timing });
  if (env.DB) {
    const db = env.DB;
    Object.defineProperty(scoped, "DB", {
      value: new Proxy(db, {
        get(target, property) {
          const value = Reflect.get(target, property, target);
          if (property === "prepare" && typeof value === "function") {
            return (...args) => timedStatement(value.apply(target, args), timing);
          }
          if (["batch", "exec"].includes(String(property)) && typeof value === "function") {
            return async (...args) => {
              const startedAt = performance.now();
              try { return await value.apply(target, args); }
              finally { timing.d1_ms += elapsedMs(startedAt); }
            };
          }
          return typeof value === "function" ? value.bind(target) : value;
        },
      }),
    });
  }
  return scoped;
}
function messageAgeSeconds(messageTime, receivedAt) {
  if (typeof messageTime !== "number" || !Number.isFinite(messageTime)) return null;
  const messageMs = Math.abs(messageTime) >= 1e12 ? messageTime : messageTime * 1000;
  const age = (receivedAt - messageMs) / 1000;
  return Number.isFinite(age) ? Math.round(age * 1000) / 1000 : null;
}
async function recordPanelTiming(env, timing, msgAge) {
  try {
    const raw = await getState(env, "last_timings", "[]");
    let history = [];
    try { history = JSON.parse(raw); } catch { /* discard malformed prior diagnostics */ }
    if (!Array.isArray(history)) history = [];
    history = history.filter((item) => item && typeof item === "object" && !Array.isArray(item))
      .map((item) => ({
        msg_age_s: typeof item.msg_age_s === "number" && Number.isFinite(item.msg_age_s) ? item.msg_age_s : null,
        total_ms: Number.isFinite(item.total_ms) && item.total_ms >= 0 ? item.total_ms : 0,
        rubika_calls: Number.isSafeInteger(item.rubika_calls) && item.rubika_calls >= 0 ? item.rubika_calls : 0,
        rubika_ms: Number.isFinite(item.rubika_ms) && item.rubika_ms >= 0 ? item.rubika_ms : 0,
        d1_ms: Number.isFinite(item.d1_ms) && item.d1_ms >= 0 ? item.d1_ms : 0,
      })).slice(-4);
    const row = {
      msg_age_s: msgAge,
      total_ms: elapsedMs(timing.startedAt),
      rubika_calls: timing.rubika_calls,
      rubika_ms: timing.rubika_ms,
      d1_ms: timing.d1_ms,
    };
    await setState(env, "last_timings", JSON.stringify([...history, row].slice(-5)));
  } catch { /* diagnostics must never break owner commands */ }
}
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
  const staleSkip = await readCounter("panel_stale_skip");
  const atomicOk = await readCounter("atomic_ok");
  const atomicFail = await readCounter("atomic_fail");
  const storedMode = await getState(env, "panel_sync_mode", "atomic");
  const panelSyncMode = storedMode === "inplace" ? "inplace" : "atomic";
  let timings = [];
  try {
    const parsed = JSON.parse(await getState(env, "last_timings", "[]"));
    if (Array.isArray(parsed)) timings = parsed.slice(-5).map((item) => ({
      msg_age_s: typeof item?.msg_age_s === "number" && Number.isFinite(item.msg_age_s) ? item.msg_age_s : null,
      total_ms: Number.isFinite(item?.total_ms) && item.total_ms >= 0 ? item.total_ms : 0,
      rubika_calls: Number.isSafeInteger(item?.rubika_calls) && item.rubika_calls >= 0 ? item.rubika_calls : 0,
      rubika_ms: Number.isFinite(item?.rubika_ms) && item.rubika_ms >= 0 ? item.rubika_ms : 0,
      d1_ms: Number.isFinite(item?.d1_ms) && item.d1_ms >= 0 ? item.d1_ms : 0,
    }));
  } catch { /* invalid or old timing state */ }
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
    "panel_stale_skip: " + staleSkip,
    "panel_sync_mode: " + panelSyncMode,
    "atomic_ok: " + atomicOk,
    "atomic_fail: " + atomicFail,
    "last_timings: " + JSON.stringify(timings),
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
  const targetMid = clickedMid && clickedMid === previousMid ? clickedMid : previousMid;

  if (id !== "p_report") {
    let out;
    if (id === "p_status") out = fmtStatus(await api(ui, "/api/status"));
    else if (id === "p_queue") out = fmtQueue(await api(ui, "/api/brain/owner-console/queue-summary"));
    else out = "🤖 **دفتر سهمیهٔ هوش مصنوعی**\n\n" + await aiBlock(ui);

    if (fresh) return sendFreshPanel(ui, out, refreshKb(id), previousMid);
    if (targetMid) return edit(env, chat, targetMid, out, refreshKb(id));
    return send(env, chat, out, refreshKb(id));
  }

  const [statusPayload, queuePayload, aiText] = await Promise.all([api(ui, "/api/status"), api(ui, "/api/brain/owner-console/queue-summary"), aiBlock(ui)]);
  const report = [
    "🧾 **گزارش Rubika-Agent**",
    fmtStatus(statusPayload),
    fmtQueue(queuePayload),
    aiText,
  ].join("\n\n");
  if (fresh) return sendFreshPanel(ui, report, refreshKb(id), previousMid);
  if (targetMid) return edit(env, chat, targetMid, report, refreshKb(id));
  return send(env, chat, report, refreshKb(id));
}

async function render(ui, id, { fresh = false, clickedMid = "" } = {}) {
  const env = ui.env;
  const chat = ui.chat;
  if (id !== "p_mob") await setState(env, "mode", "idle");

  const mobAction = /^p_mob_(publish|edit|reject|manualconfirm)_(job_[A-Fa-f0-9]{8,80})$/.exec(id);
  if (mobAction || id === "p_mob_edit_cancel") {
    if (id === "p_mob_edit_cancel") {
      await setState(env, "mode", "idle");
      await setState(env, "panel_mob_edit_job_id", "");
      await send(env, chat, "✏️ ویرایش کپشن لغو شد.", HOME_KB);
      return;
    }
    const action = mobAction[1];
    const jobId = mobAction[2];
    const row = await env.DB.prepare(
      "SELECT id, type, status, panel_link_id, params_json, result_json FROM console_jobs WHERE id = ? AND type = 'mob_x_link'"
    ).bind(jobId).first();
    if (!row) {
      await send(env, chat, "کار خبر Mob پیدا نشد.", HOME_KB);
      return;
    }
    let result = {};
    try { result = row.result_json ? JSON.parse(row.result_json) : {}; } catch {}
    if (!result || typeof result !== "object" || Array.isArray(result)) result = {};

    if (action === "edit") {
      if (row.status !== "ready_for_review" || !result.rubika_file_id) {
        await send(env, chat, "این پیش‌نمایش دیگر قابل ویرایش نیست؛ وضعیت کار: " + esc(row.status), HOME_KB);
        return;
      }
      await setState(env, "panel_mob_edit_job_id", jobId);
      await setState(env, "mode", "waiting_mob_caption_edit");
      await send(env, chat, "✏️ کپشن نهایی را به‌صورت متن ساده بفرست. پیش‌نمایش قبلی تا ثبت کپشن جدید معتبر می‌ماند.", kb([[["لغو ویرایش", "p_mob_edit_cancel"]]]));
      return;
    }

    if (action === "reject") {
      if (row.status !== "ready_for_review") {
        await send(env, chat, "فقط پیش‌نمایش آمادهٔ بررسی را می‌توان رد کرد. وضعیت فعلی: " + esc(row.status), HOME_KB);
        return;
      }
      const now = new Date().toISOString();
      const updated = await env.DB.batch([
        env.DB.prepare("UPDATE console_jobs SET status = 'rejected', progress = 100, error = NULL, updated_at = ? WHERE id = ? AND type = 'mob_x_link' AND status = 'ready_for_review'").bind(now, jobId),
        env.DB.prepare("UPDATE panel_links SET status = 'rejected', updated_at = ? WHERE id = ? AND status = 'ready_for_review' AND changes() > 0").bind(now, row.panel_link_id),
        env.DB.prepare("UPDATE items SET review_state = 'rejected' WHERE id = ? AND platform = 'mob_official' AND status = 'ready' AND review_state = 'pending' AND changes() > 0").bind(result.item_id),
      ]);
      if (!updated?.[0]?.meta?.changes) {
        await send(env, chat, "وضعیت کار هم‌زمان تغییر کرده؛ هیچ اقدامی انجام نشد.", HOME_KB);
        return;
      }
      await send(env, chat, "✖️ خبر رد شد و در کانال منتشر نمی‌شود.", HOME_KB);
      return;
    }

    if (action === "publish") {
      if (row.status === "approved") {
        const dispatchResult = await dispatchMobXJobOnce(
          env, jobId, "publish_dispatch_state", "mob-x-news-production.yml",
          { panel_link_job_id: jobId }, ["approved"],
        );
        if (!dispatchResult.ok && !dispatchResult.skipped) {
          await send(env, chat, "⚠️ تأیید قبلاً ثبت شده، اما اجرای خودکار شروع نشد (" +
            dispatchResult.error + "). مقصد همچنان فقط کانال تست است.", HOME_KB);
        } else {
          await send(env, chat, "✅ تأیید قبلاً ثبت شده است؛ اجرای خودکار برای کانال تست " +
            (dispatchResult.skipped ? "قبلاً آغاز یا ثبت شده." : "راه‌اندازی شد."), HOME_KB);
        }
        return;
      }
      if (row.status !== "ready_for_review" || !result.rubika_file_id || result.preview_sent !== true) {
        await send(env, chat, "پیش‌نمایش هنوز آمادهٔ تأیید نهایی نیست؛ لطفاً کمی بعد وضعیت را بررسی کن.", HOME_KB);
        return;
      }
      // This Worker does not receive GitHub Actions secrets. This button only records
      // owner approval; the GitHub publisher independently verifies the SHA-256 pin
      // for secrets.RUBIKA_CHANNEL_GUID immediately before any channel send.
      const now = new Date().toISOString();
      const updated = await env.DB.batch([
        env.DB.prepare(
          "UPDATE console_jobs SET status = 'approved', progress = 100, error = NULL, updated_at = ? " +
          "WHERE id = ? AND type = 'mob_x_link' AND status = 'ready_for_review' " +
          "AND EXISTS (SELECT 1 FROM items WHERE id = ? AND platform = 'mob_official' AND status = 'ready' AND review_state = 'pending')"
        ).bind(now, jobId, result.item_id),
        env.DB.prepare("UPDATE panel_links SET status = 'approved', updated_at = ? WHERE id = ? AND status = 'ready_for_review' AND changes() > 0").bind(now, row.panel_link_id),
        env.DB.prepare("UPDATE items SET review_state = 'approved' WHERE id = ? AND platform = 'mob_official' AND status = 'ready' AND review_state = 'pending' AND changes() > 0").bind(result.item_id),
      ]);
      if (!updated?.[0]?.meta?.changes || !updated?.[2]?.meta?.changes) {
        await send(env, chat, "تأیید انتشار ثبت نشد؛ وضعیت کار یا آیتم تغییر کرده است.", HOME_KB);
        return;
      }
      try {
        await env.DB.prepare("INSERT INTO brain_events (item_id, level, code, details_json) VALUES (?, 'info', 'mob_x_owner_approved_test_publish', ?)").bind(
          result.item_id, JSON.stringify({ job_id: jobId, test_channel_only: true, owner_clicked: true }),
        ).run();
      } catch {}
      const dispatchResult = await dispatchMobXJobOnce(
        env, jobId, "publish_dispatch_state", "mob-x-news-production.yml",
        { panel_link_job_id: jobId }, ["approved"],
      );
      if (!dispatchResult.ok && !dispatchResult.skipped) {
        await send(env, chat, "✅ تأیید مالک ثبت شد؛ اما اجرای خودکار شروع نشد (" +
          dispatchResult.error + "). مقصد فقط کانال تست است؛ ارسال زمان‌بندی‌شده همچنان فعال می‌ماند.", HOME_KB);
        return;
      }
      await send(
        env, chat,
        "✅ تأیید مالک ثبت شد و اجرای خودکار " +
          (dispatchResult.skipped ? "قبلاً راه‌اندازی شده است." : "درخواست شد.") +
          " مقصد فقط کانال تست است؛ اگر نتیجهٔ ارسال نامشخص شود، ارسال دوباره انجام نمی‌شود.",
        HOME_KB,
      );
      return;
    }

    if (action === "manualconfirm") {
      if (row.status !== "uncertain" || result.delivery_status !== "uncertain") {
        await send(env, chat, "این کار در وضعیت ارسال نامشخص نیست؛ هیچ ارسال جدیدی انجام نشد.", HOME_KB);
        return;
      }
      const now = new Date().toISOString();
      result.delivery_status = "sent";
      result.manual_confirmation = true;
      const saved = await env.DB.batch([
        env.DB.prepare("UPDATE console_jobs SET status = 'sent', progress = 100, result_json = ?, error = NULL, updated_at = ? WHERE id = ? AND type = 'mob_x_link' AND status = 'uncertain'").bind(JSON.stringify(result), now, jobId),
        env.DB.prepare("UPDATE panel_links SET status = 'sent', updated_at = ? WHERE id = ? AND status = 'uncertain' AND changes() > 0").bind(now, row.panel_link_id),
        env.DB.prepare("UPDATE items SET status = 'sent', sent_at = ? WHERE id = ? AND platform = 'mob_official' AND status = 'uncertain' AND changes() > 0").bind(now, result.item_id),
      ]);
      if (!saved?.[0]?.meta?.changes) {
        await send(env, chat, "تأیید دستی ثبت نشد؛ وضعیت کار تغییر کرده است.", HOME_KB);
        return;
      }
      await send(env, chat, "✅ تأیید دستی ثبت شد؛ وضعیت ارسال «ارسال‌شده» شد. هیچ ارسال جدیدی انجام نشد.", HOME_KB);
      return;
    }
  }
  if (id === "p_mob_confirm" || id === "p_mob_cancel") {
    const rawLinkId = await getState(env, "panel_mob_pending_link_id", "");
    const linkId = Number(rawLinkId);
    if (!Number.isSafeInteger(linkId) || linkId < 1) {
      await send(env, chat, "این تأیید منقضی شده است؛ لینک را دوباره بفرست.", HOME_KB);
      return;
    }
    const link = await env.DB.prepare("SELECT id, url, external_id, status FROM panel_links WHERE id = ?").bind(linkId).first();
    if (!link) {
      await setState(env, "panel_mob_pending_link_id", "");
      await send(env, chat, "لینک پیدا نشد؛ دوباره ارسالش کن.", HOME_KB);
      return;
    }
    if (id === "p_mob_cancel") {
      const canceled = await env.DB.prepare("UPDATE panel_links SET status = 'rejected', updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ? AND status = 'pending'").bind(linkId).run();
      await setState(env, "panel_mob_pending_link_id", "");
      if (!canceled?.meta?.changes && link.status !== "rejected") {
        await send(env, chat, "وضعیت لینک تغییر کرده؛ بخش «لینک‌های X» را تازه‌سازی کن.", HOME_KB);
        return;
      }
      return render(ui, "p_links", { clickedMid });
    }

    if (!/^\d{15,22}$/.test(String(link.external_id || ""))) {
      await send(env, chat, "شناسهٔ پست معتبر نیست؛ لینک دوباره بررسی شود.", HOME_KB);
      return;
    }
    const existing = await env.DB.prepare("SELECT id FROM console_jobs WHERE panel_link_id = ?").bind(linkId).first();
    if (!existing) {
      if (!["pending", "confirmed"].includes(link.status)) {
        await send(env, chat, "این لینک دیگر در وضعیت قابل تأیید نیست.", HOME_KB);
        return;
      }
      const jobId = "job_" + Array.from(crypto.getRandomValues(new Uint8Array(12)), value => value.toString(16).padStart(2, "0")).join("");
      await env.DB.batch([
        env.DB.prepare("UPDATE panel_links SET status = 'confirmed', confirmed_at = COALESCE(confirmed_at, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')), updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ? AND status IN ('pending', 'confirmed')").bind(linkId),
        env.DB.prepare("INSERT INTO console_jobs (id, type, status, progress, params_json, result_json, error, panel_link_id) SELECT ?, 'mob_x_link', 'confirmed', 0, ?, NULL, NULL, ? WHERE EXISTS (SELECT 1 FROM panel_links WHERE id = ? AND status = 'confirmed') ON CONFLICT(panel_link_id) WHERE type = 'mob_x_link' AND panel_link_id IS NOT NULL DO NOTHING").bind(jobId, JSON.stringify({ url: link.url, panel_link_id: linkId }), linkId, linkId),
      ]);
    }
    const job = await env.DB.prepare(
      "SELECT id, status, params_json FROM console_jobs WHERE panel_link_id = ? AND type = 'mob_x_link'"
    ).bind(linkId).first();
    if (!job) {
      await send(env, chat, "ثبت کار انجام نشد؛ خطا: mob_x_link_job_create_failed", HOME_KB);
      return;
    }
    const dispatchResult = await dispatchMobXJobOnce(
      env, job.id, "link_dispatch_state", "mob-x-link-processor.yml",
      { panel_link_id: linkId }, ["confirmed", "queued"],
    );
    await setState(env, "panel_mob_pending_link_id", "");
    if (!dispatchResult.ok && !dispatchResult.skipped) {
      await send(env, chat, "⚠️ راه‌اندازی خودکار پردازش انجام نشد (" + dispatchResult.error +
        "). پردازش زمان‌بندی‌شده همچنان فعال است.", HOME_KB);
      return;
    }
    return render(ui, "p_links", { clickedMid });
  }
  if (id !== "p_links_clear" && id !== "p_links_clear_yes") {
    await setState(env, "panel_links_clear_pending", "no");
  }
  const statics = {
    p_help: [HELP_TEXT, BACK_KB],
    p_mob: ["📰 **خبر رسمی Mob از لینک X**\n\nلینک https://x.com/mobgamesstudios/status/شناسه را بفرست.", CANCEL_KB],
  };
  if (id === "p_mob") await setState(env, "mode", "waiting_link");
  const previousMid = await getState(env, "panel_mid", "");
  const targetMid = clickedMid && clickedMid === previousMid ? clickedMid : previousMid;
  if (isSettingsId(id)) {
    if (!safeEqual((await getOwner(env)) || "", chat)) {
      try { await rubika(env, "sendMessage", { chat_id: chat, text: "فقط مالک اصلی اجازهٔ مشاهده و تغییر این تنظیمات را دارد." }); } catch {}
      return;
    }
    const notice = await applySettingsAction(env, id);
    const screen = await settingsScreen(env, notice || "");
    const keypad = kb(screen.rows);
    if (fresh) return sendFreshPanel(ui, screen.text, keypad, previousMid);
    if (targetMid) return edit(env, chat, targetMid, screen.text, keypad);
    return send(env, chat, screen.text, keypad);
  }
  if (id === "p_home") {
    const text = await homeText(env);
    if (fresh) return sendFreshPanel(ui, text, HOME_KB, previousMid);
    if (targetMid) return edit(env, chat, targetMid, text, HOME_KB);
    return send(env, chat, text, HOME_KB);
  }
  if (isScreenId(id)) {
    let screenId = id;
    if (id === "p_links_clear_yes") {
      const pending = await getState(env, "panel_links_clear_pending", "no");
      await setState(env, "panel_links_clear_pending", "no");
      if (pending !== "yes") screenId = "p_links";
    }
    const s = await buildScreen(env, screenId);
    if (id === "p_links_clear" && !s.text.includes("d1_error")) {
      await setState(env, "panel_links_clear_pending", "yes");
    }
    const rows = Array.isArray(s.rows) && s.rows.some((row) => Array.isArray(row) && row.length > 0)
      ? s.rows : HOME_ONLY;
    const keypad = kb(rows);
    if (fresh && ["p_errors", "p_runs", "p_links", "p_digest"].includes(id)) return sendFreshPanel(ui, s.text, keypad, previousMid);
    if (targetMid) return edit(env, chat, targetMid, s.text, keypad);
    return send(env, chat, s.text, keypad);
  }
  const s = statics[id];
  if (s) {
    if (fresh) return sendFreshPanel(ui, s[0], s[1], previousMid);
    if (targetMid) return edit(env, chat, targetMid, s[0], s[1]);
    return send(env, chat, s[0], s[1]);
  }
  if (id === "p_debug") {
    const text = await debugText(env);
    const keypad = refreshKb("p_debug");
    if (fresh) return sendFreshPanel(ui, text, keypad, previousMid);
    if (targetMid) return edit(env, chat, targetMid, text, keypad);
    return send(env, chat, text, keypad);
  }
  if (["p_status", "p_queue", "p_ai", "p_report"].includes(id)) return renderData(ui, id, { fresh, clickedMid });
  return render(ui, "p_home", { fresh, clickedMid });
}

const COMMANDS = {
  "/start": "p_home", "/menu": "p_home", "منو": "p_home", "/help": "p_help", "/status": "p_status",
  "/queue": "p_qs", "/ai": "p_ai", "/ai-status": "p_ai", "/report": "p_report", "/debug": "p_debug",
  "/digest": "p_digest", "/errors": "p_errors", "/runs": "p_runs",
  "/links": "p_links", "/ailog": "p_ailog", "/settings": "p_set",
};

async function tryClaim(env, ev) {
  const m = /^\/claim\s+(\S{1,128})\s*$/.exec(ev.text);
  if (!m) return;
  if (Number(await getState(env, "claim_fails", "0")) >= 5) return;
  const supplied = m[1];
  const primaryOwner = await getOwner(env);
  const setupCode = env.OWNER_SETUP_CODE || "";

  // The bootstrap secret is accepted only while the singleton owner has not been claimed.
  if (!primaryOwner && setupCode.length >= 8 && safeEqual(supplied, setupCode)) {
    const r = await env.DB.prepare("INSERT OR IGNORE INTO panel_owner(id,chat_id) VALUES(1,?)").bind(ev.chat).run();
    if (r.meta?.changes !== 1) return;
    try {
      await env.DB.batch([
        env.DB.prepare("INSERT OR IGNORE INTO console_owners (chat_id, label, role, enabled, claimed_at, last_seen_at) VALUES (?, 'مالک اصلی', 'owner', 1, datetime('now'), datetime('now'))")
          .bind(ev.chat),
        env.DB.prepare("INSERT INTO brain_events (item_id, level, code, details_json) SELECT NULL, 'info', 'console_owner_claim_bootstrap', ? WHERE changes() > 0")
          .bind(JSON.stringify({ method: "setup", role: "owner" })),
      ]);
    } catch { /* migration 0010 may not be applied yet; panel_owner remains the compatibility fallback */ }
    try { await rubika(env, "deleteMessage", { chat_id: ev.chat, message_id: ev.mid }); } catch { /* best effort */ }
    await send(env, ev.chat, "✅ **مالک ثبت شد.** از حالا چت تأییدشده به پنل دسترسی دارد.\\n\\n" + HOME_TEXT, HOME_KB);
    return;
  }

  // Invites only grant access after the original owner setup is complete.
  if (!primaryOwner) {
    await setState(env, "claim_fails", Number(await getState(env, "claim_fails", "0")) + 1);
    await send(env, ev.chat, "❌ کد دعوت نامعتبر است یا راه‌اندازی اولیه هنوز انجام نشده.");
    return;
  }
  try {
    const existing = await env.DB.prepare("SELECT id FROM console_owners WHERE chat_id = ?").bind(ev.chat).first();
    if (existing) {
      await send(env, ev.chat, "این چت قبلاً ثبت شده است.");
      return;
    }
    const codeHash = await sha256Hex(supplied);
    const now = new Date().toISOString();
    const invite = await env.DB.prepare("SELECT id FROM console_owner_invites WHERE code_hash = ? AND used_at IS NULL AND expires_at > ?")
      .bind(codeHash, now).first();
    if (!invite) {
      await setState(env, "claim_fails", Number(await getState(env, "claim_fails", "0")) + 1);
      await send(env, ev.chat, "❌ کد دعوت نامعتبر یا منقضی شده است.");
      return;
    }
    const label = "ناظر " + idTail(ev.chat);
    const results = await env.DB.batch([
      env.DB.prepare("UPDATE console_owner_invites SET used_at = ?, used_by = ? WHERE id = ? AND used_at IS NULL AND expires_at > ?")
        .bind(now, ev.chat, invite.id, now),
      env.DB.prepare("INSERT INTO console_owners (chat_id, label, role, enabled, claimed_at, last_seen_at) SELECT ?, ?, 'viewer', 1, ?, ? WHERE changes() = 1")
        .bind(ev.chat, label, now, now),
      env.DB.prepare("INSERT INTO brain_events (item_id, level, code, details_json) SELECT NULL, 'info', 'console_owner_claim', ? WHERE changes() > 0")
        .bind(JSON.stringify({ method: "invite", role: "viewer" })),
    ]);
    if (!results[0]?.meta?.changes || !results[1]?.meta?.changes) {
      await send(env, ev.chat, "❌ این کد قبلاً استفاده شده یا منقضی شده است.");
      return;
    }
    try { await rubika(env, "deleteMessage", { chat_id: ev.chat, message_id: ev.mid }); } catch { /* best effort */ }
    await send(env, ev.chat, "✅ **دعوت پذیرفته شد.** دسترسی شما ثبت شد.\\n\\n" + HOME_TEXT, HOME_KB);
  } catch {
    await send(env, ev.chat, "⚠️ امکان بررسی کد دعوت نیست؛ پایگاه داده یا migration لازم آماده نیست.");
  }
}

const idTail = (value) => String(value ?? "").slice(-4);
async function processEvent(env, selfFetch, origin, ev) {
  if (!ev) return;
  const timing = { startedAt: performance.now(), rubika_calls: 0, rubika_ms: 0, d1_ms: 0 };
  const receivedAt = Date.now();
  const timedEnv = withPanelTiming(env, timing);
  const owner = await getOwner(timedEnv);
  if (ev.kind === "text" && !ev.botAuthored && isPrivateChat(ev) && /^\/claim\s+\S{1,128}\s*$/.test(ev.text)) {
    await tryClaim(timedEnv, ev);
    return;
  }
  if (!owner) {
    if (ev.kind === "text" && !ev.botAuthored && isPrivateChat(ev)) await tryClaim(timedEnv, ev);
    return;
  }
  if (!(await hasOwnerAccess(timedEnv, ev.chat, owner))) return;
  try {
    await timedEnv.DB.prepare("UPDATE console_owners SET last_seen_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE chat_id = ? AND enabled = 1")
      .bind(ev.chat).run();
  } catch { /* migration 0010 may not be applied yet; keep existing panel behavior */ }
  const msgAge = messageAgeSeconds(ev.time, receivedAt);
  try {
    const lastEvent = {
      kind: clean(ev.kind, 16) || "unknown",
      types: Array.isArray(ev.types) ? ev.types.slice(0, 4).map((value) => clean(value, 24)).filter(Boolean) : [],
      chat_tail: idTail(ev.chat),
      message_id_tail: idTail(ev.mid),
      button: clean(ev.button, 60),
      botAuthored: ev.botAuthored === true,
    };
    try { await setState(timedEnv, "last_event", JSON.stringify(lastEvent)); } catch { /* diagnostics must not block owner commands */ }
    if (ev.botAuthored || !isPrivateChat(ev)) return;
    const key = ev.kind === "click"
      ? "c|" + ev.chat + "|" + ev.mid + "|" + ev.button + "|" + Math.floor(Date.now() / 4000)
      : "t|" + ev.chat + "|" + ev.mid;
    if (!(await firstSeen(timedEnv, key))) return;
    const seq = await nextPanelSeq(timedEnv);
    Object.defineProperty(timedEnv, PANEL_SEQ, { value: seq });
    const ui = { env: timedEnv, chat: ev.chat, selfFetch, origin };
    if (ev.kind === "click") {
      const currentPanelMid = await getState(timedEnv, "panel_mid", "");
      const isMobJobAction = /^p_mob_(?:publish|edit|reject|manualconfirm)_job_[A-Fa-f0-9]{8,80}$/.test(String(ev.button || ""));
      if (String(ev.mid || "") !== currentPanelMid && !isMobJobAction) {
        try { if (ev.mid) await rubika(timedEnv, "deleteMessage", { chat_id: ev.chat, message_id: ev.mid }); } catch {}
        const staleButton = String(ev.button || "");
        // Never execute confirmation/destructive actions from an old message.
        let refreshId = staleButton;
        if (["p_mob_confirm", "p_mob_cancel", "p_links_clear", "p_links_clear_yes"].includes(staleButton)) refreshId = "p_links";
        else if (staleButton === "p_mob_edit_cancel") refreshId = "p_mob";
        else if (!isScreenId(staleButton) &&
          !["p_home", "p_status", "p_queue", "p_qs", "p_ai", "p_report", "p_mob", "p_help", "p_debug"].includes(staleButton)) {
          refreshId = "p_home";
        }
        await render(ui, refreshId, { fresh: true });
        return;
      }
      await render(ui, ev.button, { clickedMid: ev.mid });
      return;
    }
    const word = ev.text.split(/\s+/)[0].split("@")[0].toLowerCase();
    if (word === "/register") { await registerWebhook(timedEnv, origin); await send(timedEnv, ev.chat, "✅ اتصال لحظه‌ای دوباره ثبت شد."); return; }
    if (word === "/panelmode") {
      const match = /^\/panelmode\s+(atomic|inplace)\s*$/i.exec(ev.text);
      if (!safeEqual(owner || "", ev.chat)) {
        try { await rubika(timedEnv, "sendMessage", { chat_id: ev.chat, text: "فقط مالک اصلی اجازهٔ تغییر حالت پنل را دارد." }); } catch {}
        return;
      }
      if (!match) {
        const currentMode = (await getState(timedEnv, "panel_sync_mode", "atomic")) === "inplace" ? "inplace" : "atomic";
        const currentMid = await getState(timedEnv, "panel_mid", "");
        await sendFreshPanel(ui, "حالت فعلی پنل: " + currentMode + "\nاستفاده: /panelmode atomic یا /panelmode inplace", HOME_KB, currentMid);
        return;
      }
      const mode = match[1].toLowerCase();
      await setState(timedEnv, "panel_sync_mode", mode);
      const currentMid = await getState(timedEnv, "panel_mid", "");
      await sendFreshPanel(ui, "✅ حالت همگام‌سازی پنل تغییر کرد: **" + mode + "**", HOME_KB, currentMid);
      return;
    }
    if (COMMANDS[word]) { await render(ui, COMMANDS[word], { fresh: true }); return; }
    const activeMode = await getState(timedEnv, "mode", "idle");
    if (activeMode === "waiting_mob_caption_edit") {
      const jobId = await getState(timedEnv, "panel_mob_edit_job_id", "");
      if (!/^job_[A-Fa-f0-9]{8,80}$/.test(jobId)) {
        await setState(timedEnv, "mode", "idle");
        await send(timedEnv, ev.chat, "ویرایش منقضی شده است؛ دوباره از پیش‌نمایش اقدام کن.", HOME_KB);
        return;
      }
      const row = await timedEnv.DB.prepare(
        "SELECT id, status, result_json FROM console_jobs WHERE id = ? AND type = 'mob_x_link'"
      ).bind(jobId).first();
      if (!row || row.status !== "ready_for_review") {
        await setState(timedEnv, "mode", "idle");
        await setState(timedEnv, "panel_mob_edit_job_id", "");
        await send(timedEnv, ev.chat, "این کار دیگر برای ویرایش باز نیست.", HOME_KB);
        return;
      }
      let result = {};
      try { result = row.result_json ? JSON.parse(row.result_json) : {}; } catch {}
      if (!result || typeof result !== "object" || Array.isArray(result) || !result.rubika_file_id) {
        await send(timedEnv, ev.chat, "فایل پیش‌نمایش در دسترس نیست؛ خطا: mob_x_preview_file_id_missing", HOME_KB);
        return;
      }
      const rulesRow = await timedEnv.DB.prepare("SELECT rules_json FROM console_rules WHERE id = 1").first();
      let rules = {};
      try { rules = rulesRow?.rules_json ? JSON.parse(rulesRow.rules_json) : {}; } catch {}
      const rawLimit = Number(rules.caption_max_chars ?? 900);
      const captionMax = Number.isFinite(rawLimit) ? Math.max(1, Math.min(4000, Math.trunc(rawLimit))) : 900;
      const caption = String(ev.text || "").replace(/[*_~]/g, "").replaceAll(String.fromCharCode(96), "").replace(/\u0000/g, "").trim();
      if (!caption) {
        await send(timedEnv, ev.chat, "کپشن خالی است؛ متن سادهٔ کپشن را بفرست.", kb([[["لغو ویرایش", "p_mob_edit_cancel"]]]));
        return;
      }
      if (caption.length > captionMax) {
        await send(timedEnv, ev.chat, "کپشن بیش از سقف مجاز " + captionMax + " نویسه است؛ کوتاه‌ترش کن.", kb([[["لغو ویرایش", "p_mob_edit_cancel"]]]));
        return;
      }
      const previousPreviewId = typeof result.preview_message_id === "string" ? result.preview_message_id : "";
      result.caption = caption;
      result.preview_sent = false;
      delete result.preview_message_id;
      const now = new Date().toISOString();
      const saved = await timedEnv.DB.prepare(
        "UPDATE console_jobs SET result_json = ?, updated_at = ? WHERE id = ? AND type = 'mob_x_link' AND status = 'ready_for_review'"
      ).bind(JSON.stringify(result), now, jobId).run();
      if (!saved?.meta?.changes) {
        await send(timedEnv, ev.chat, "ذخیرهٔ کپشن انجام نشد؛ وضعیت کار تغییر کرده است.", HOME_KB);
        return;
      }
      await timedEnv.DB.prepare("UPDATE items SET caption = ? WHERE id = ? AND platform = 'mob_official' AND status = 'ready'").bind(caption, result.item_id).run();
      try {
        const preview = await rubika(timedEnv, "sendFile", {
          chat_id: ev.chat,
          file_id: result.rubika_file_id,
          text: caption,
          inline_keypad: kb([[
            ["✅ انتشار در کانال تست", "p_mob_publish_" + jobId],
            ["✏️ ویرایش کپشن", "p_mob_edit_" + jobId],
            ["✖️ رد", "p_mob_reject_" + jobId],
          ]]),
          disable_notification: false,
          chat_keypad_type: "None",
        });
        const messageId = preview?.data?.message_id;
        result.preview_sent = true;
        if (typeof messageId === "string" && messageId) result.preview_message_id = messageId;
        await timedEnv.DB.prepare(
          "UPDATE console_jobs SET result_json = ?, updated_at = ? WHERE id = ? AND type = 'mob_x_link' AND status = 'ready_for_review'"
        ).bind(JSON.stringify(result), new Date().toISOString(), jobId).run();
        if (previousPreviewId && messageId && previousPreviewId !== messageId) {
          try { await rubika(timedEnv, "deleteMessage", { chat_id: ev.chat, message_id: previousPreviewId }); } catch {}
        }
        await setState(timedEnv, "mode", "idle");
        await setState(timedEnv, "panel_mob_edit_job_id", "");
        await send(timedEnv, ev.chat, "✅ کپشن ویرایش شد و پیش‌نمایش جدید ارسال شد.", HOME_KB);
      } catch {
        await send(timedEnv, ev.chat, "کپشن ذخیره شد، اما ارسال پیش‌نمایش تازه ناموفق بود؛ انتشار را تأیید نکن تا پیش‌نمایش دوباره آماده شود.", HOME_KB);
      }
      return;
    }
    if (activeMode === "waiting_link") {
      const link = parseXLink(ev.text);
      if (!link) {
        await send(timedEnv, ev.chat, "فقط لینک پست حساب رسمی mobgamesstudios در x.com یا twitter.com پذیرفته می‌شود.", CANCEL_KB);
        return;
      }
      const externalId = link.split("/").pop();
      await timedEnv.DB.prepare("INSERT OR IGNORE INTO panel_links (url, external_id, status) VALUES (?, ?, 'pending')").bind(link, externalId).run();
      const saved = await timedEnv.DB.prepare("SELECT id, url, external_id, status FROM panel_links WHERE external_id = ?").bind(externalId).first();
      if (!saved) throw new PanelError("mob_x_link_save_failed");
      if (saved.status !== "pending") {
        await setState(timedEnv, "mode", "idle");
        await send(timedEnv, ev.chat, "این پست قبلاً ثبت شده است؛ وضعیت فعلی: " + esc(saved.status) + ".", HOME_KB);
        return;
      }
      await setState(timedEnv, "panel_mob_pending_link_id", String(saved.id));
      await setState(timedEnv, "mode", "idle");
      const confirmKb = kb([[["✅ تأیید", "p_mob_confirm"], ["✖️ لغو", "p_mob_cancel"]]]);
      await send(timedEnv, ev.chat, "این لینک است؟\n" + saved.url, confirmKb);
      return;
    }
    await send(timedEnv, ev.chat, "برای شروع «منو» را بفرست.", HOME_KB);
  } catch (e) {
    if (e instanceof StalePanelEvent) return;
    try { await send(timedEnv, ev.chat, "❌ خطا: " + esc(e && e.code || "unknown"), BACK_KB); } catch { /* ignore */ }
  } finally {
    await recordPanelTiming(timedEnv, timing, msgAge);
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
