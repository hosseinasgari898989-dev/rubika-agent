// Owner panel on Cloudflare (stage 1): instant Rubika webhook, owner claim, menu, live screens.
// Heavy work (video, edits, YouTube) stays on GitHub Actions; this module never runs shell or publishes.
import { homeSummary } from "./panel_screens.js";
import { isScreenIdExt as isScreenId, buildScreenExt as buildScreen, HOME_ROWS_EXT as HOME_ROWS } from "./panel_screens_ext.js";
import { isSettingsId, applySettingsAction, settingsScreen } from "./panel_settings.js";
import { buildMobXNoCaptionCaption, maxMobXCaptionChars, normalizeMobXCaption } from "./mob_x_caption.js";
import { normalizeMobXLink } from "./mob_x_link.js";
import { findMobXDuplicate, normalizeMobXExternalId } from "./mob_x_identity.js";
import { isBotOff, setBotPower } from "./bot_power.js";

const RUBIKA_API = "https://botapi.rubika.ir/v3";
const HOOK_TYPES = ["ReceiveUpdate", "ReceiveInlineMessage", "ReceiveQuery"];
const MAX_BODY = 64 * 1024;
const PRIVATE_TYPES = new Set(["private", "privatechat", "user", "userchat", "personal"]);
const te = new TextEncoder();
const PANEL_TIMING = Symbol("panel_timing");
const PANEL_STATE_CACHE = Symbol("panel_state_cache");
const PANEL_STATE_MISSING = Symbol("panel_state_missing");
const PANEL_SEQ = Symbol("panel_seq");
const PANEL_STATE_PREFETCH_KEYS = Object.freeze([
  "panel_mid", "panel_mob_temp_mid", "mode", "panel_mob_edit_job_id",
  "panel_mob_pending_link_id", "panel_links_clear_pending", "last_error",
  "last_timings", "last_event", "panel_edit_ok", "panel_edit_fail",
  "panel_stale_skip", "atomic_ok", "atomic_fail", "claim_fails",
]);
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
const LOADING_KB = kb([[['🏠 منوی اصلی', 'p_home']]]);
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
const HELP_TEXT = "❓ **راهنما**\n\n• /menu یا «منو»: منوی اصلی\n• /status وضعیت، /queue صف، /ai هوش مصنوعی، /report گزارش کامل\n• /digest خلاصهٔ ۲۴ ساعت، /errors خطاها، /runs اجراها\n• /links لینک‌های X، /ailog تاریخچهٔ AI\n• /debug: خطای آخر و مشخصات امن رویداد آخر\n• /panelmode atomic|inplace: حالت هماهنگی پنل (مالک اصلی)\n• /register ثبت دوبارهٔ اتصال لحظه‌ای\n\nاین نسخه گزارش می‌دهد و لینک X را ذخیره می‌کند؛ انتشار خودکار از این پنل انجام نمی‌شود.\n\nدستورهای متنی (هر کدام همان دکمه است):\n/اسکن_و_انتشار — 🚀 اسکن و انتشار (اسکن عمیق)\n/اسکن — 🔎 اسکن فوری، فقط گزارش\n/بازبینی — 🎵 تیک‌تاک، در انتظار تأیید شما\n/توقف — 🛑 توقف اضطراری انتشار\n/ربات — ⏻ خاموش/روشن کردن ربات\n/تلاش_مجدد — 🔁 تلاش مجدد ناموفق‌ها (فقط لینک‌های Mob)\n/وضعیت /صف /گزارش /خطاها /راهنما /منو — صفحه‌های همنام\n/fix یا /تازه — ارسال پنل تازه وقتی دکمه‌ها به‌روز نمی‌شوند\n(نیم‌فاصله/فاصله در دستور ممکن نیست؛ از زیرخط استفاده کن.)"; 

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
  return normalizeMobXLink(text)?.canonicalUrl || null;
}

const GITHUB_DISPATCH_WORKFLOWS = new Set([
  "termux-docker-discovery.yml",
  "daily-queue-processor.yml",
  "mob-x-news-production.yml",
  "mob-x-link-processor.yml",
]);

export async function dispatchMobXWorkflow(env, workflow, inputs, options = {}) {
  if (!GITHUB_DISPATCH_WORKFLOWS.has(String(workflow || ""))) {
    return { ok: false, error: "github_dispatch_workflow_not_allowed" };
  }
  const token = typeof env?.GITHUB_DISPATCH_TOKEN === "string" ? env.GITHUB_DISPATCH_TOKEN.trim() : "";
  if (!token) return { ok: false, error: "github_dispatch_not_configured" };

  let cleanInputs = null;
  if (workflow === "termux-docker-discovery.yml" && inputs != null) {
    const dryRun = inputs && (inputs.dry_run === true || inputs.dry_run === "true");
    const ignoreAddedDate = inputs && (inputs.ignore_source_created_at === true || inputs.ignore_source_created_at === "true");
    const deepScan = inputs && (inputs.deep_scan === true || inputs.deep_scan === "true");
    if (dryRun) cleanInputs = { dry_run: "true" };
    else if (ignoreAddedDate || deepScan) {
      cleanInputs = { dry_run: "false", ignore_source_created_at: "true" };
      if (deepScan) cleanInputs.deep_scan = "true";
    }
    else return { ok: false, error: "github_dispatch_input_invalid" };
  } else if (workflow === "termux-docker-discovery.yml" && inputs == null) {
    // Preserve the existing scheduled Worker-Cron behavior when no inputs are supplied.
    cleanInputs = { dry_run: "false" };
  } else if (workflow === "mob-x-link-processor.yml") {
    const rawLinkId = inputs && inputs.panel_link_id;
    if (rawLinkId != null && String(rawLinkId).trim() !== "") {
      const panelLinkId = Number(rawLinkId);
      if (!Number.isSafeInteger(panelLinkId) || panelLinkId < 1) {
        return { ok: false, error: "github_dispatch_input_invalid" };
      }
      cleanInputs = { panel_link_id: String(panelLinkId) };
    }
  } else if (workflow === "mob-x-news-production.yml") {
    const jobId = String(inputs && inputs.panel_link_job_id || "").trim();
    if (jobId) {
      if (!/^job_[A-Fa-f0-9]{8,80}$/.test(jobId)) {
        return { ok: false, error: "github_dispatch_input_invalid" };
      }
      cleanInputs = { panel_link_job_id: jobId };
    }
  } else if (workflow === "daily-queue-processor.yml") {
    const confirmed = inputs && (
      inputs.publish_four_tiktok_test === true ||
      inputs.publish_four_tiktok_test === "true"
    );
    if (!confirmed) return { ok: false, error: "github_dispatch_input_invalid" };
    cleanInputs = { publish_four_tiktok_test: "true" };
  }

  const endpoint = "https://api.github.com/repos/hosseinasgari898989-dev/rubika-agent/actions/workflows/" + workflow + "/dispatches";
  let lastError = "github_dispatch_network";
  let lastErrorDetail = null;
  let lastStatus = null;
  const maxAttempts = options && options.maxAttempts === 1 ? 1 : 3;
  const safeStatusOnly = Boolean(options && options.safeStatusOnly);

  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    let response;
    const timing = env && env[PANEL_TIMING];
    const externalStartedAt = performance.now();
    if (timing) timing.external_calls += 1;
    try {
      const payload = workflow === "mob-x-link-processor.yml" || cleanInputs === null
        ? { ref: "main" }
        : { ref: "main", inputs: cleanInputs };
      response = await fetch(endpoint, {
        method: "POST",
        headers: {
          "User-Agent": "rubika-agent-worker",
          Accept: "application/vnd.github+json",
          "X-GitHub-Api-Version": "2022-11-28",
          Authorization: "Bearer " + token,
          "Content-Type": "application/json",
        },
        body: JSON.stringify(payload),
        signal: AbortSignal.timeout(2500),
      });
    } catch (error) {
      lastError = error && (error.name === "TimeoutError" || error.name === "AbortError")
        ? "github_dispatch_timeout"
        : "github_dispatch_network";
      lastErrorDetail = null;
      if (attempt < maxAttempts) await new Promise(resolve => setTimeout(resolve, attempt * 120));
      continue;
    } finally {
      if (timing) timing.external_ms += elapsedMs(externalStartedAt);
    }
    lastStatus = Number(response.status) || null;
    if (response.ok) return safeStatusOnly ? { ok: true, status: lastStatus } : { ok: true };
    lastError = lastStatus ? "github_dispatch_http_" + lastStatus : "github_dispatch_http_unknown";
    if (safeStatusOnly) {
      return { ok: false, error: lastError, status: lastStatus };
    }
    let responseBody = "";
    try { responseBody = await response.text(); } catch { /* keep HTTP status diagnostic if body is unreadable */ }
    responseBody = String(responseBody)
      .replaceAll(token, "[REDACTED]")
      .replace(/Bearer\s+[^\s"')]+/gi, "Bearer [REDACTED]")
      .slice(0, 200);
    lastErrorDetail = "status=" + lastStatus + " body=" + responseBody;
    if (lastStatus !== 429 && lastStatus < 500) {
      return { ok: false, error: lastError, error_detail: lastErrorDetail };
    }
    if (attempt < maxAttempts) await new Promise(resolve => setTimeout(resolve, attempt * 120));
  }
  return safeStatusOnly
    ? { ok: false, error: lastError, status: lastStatus }
    : { ok: false, error: lastError, ...(lastErrorDetail ? { error_detail: lastErrorDetail } : {}) };
}

export async function dispatchTermuxDiscoveryOnce(env, { publish = false } = {}) {
  const token = typeof env?.GITHUB_DISPATCH_TOKEN === "string" ? env.GITHUB_DISPATCH_TOKEN.trim() : "";
  if (!token) return { ok: false, error: "github_dispatch_not_configured" };

  const endpoint = "https://api.github.com/repos/hosseinasgari898989-dev/rubika-agent/actions/workflows/termux-docker-discovery.yml/runs?per_page=100";
  let response;
  const timing = env && env[PANEL_TIMING];
  const startedAt = performance.now();
  if (timing) timing.external_calls += 1;
  try {
    response = await fetch(endpoint, {
      method: "GET",
      headers: {
        "User-Agent": "rubika-agent-worker",
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        Authorization: "Bearer " + token,
      },
      signal: AbortSignal.timeout(5000),
    });
  } catch (error) {
    return {
      ok: false,
      error: error && (error.name === "TimeoutError" || error.name === "AbortError")
        ? "github_discovery_status_timeout"
        : "github_discovery_status_network",
    };
  } finally {
    if (timing) timing.external_ms += elapsedMs(startedAt);
  }
  if (!response || !response.ok) {
    const status = Number(response && response.status) || null;
    return { ok: false, error: status ? "github_discovery_status_http_" + status : "github_discovery_status_unavailable", status };
  }

  let payload = null;
  try { payload = await response.json(); } catch { payload = null; }
  if (!payload || !Array.isArray(payload.workflow_runs)) {
    return { ok: false, error: "github_discovery_status_invalid" };
  }
  if (payload.workflow_runs.some(run =>
    run && (run.status === "queued" || run.status === "in_progress")
  )) {
    return { ok: true, skipped: true, reason: "workflow_active" };
  }

  return dispatchMobXWorkflow(
    env,
    "termux-docker-discovery.yml",
    publish === true ? { ignore_source_created_at: true, deep_scan: true } : { dry_run: true },
    { maxAttempts: 1, safeStatusOnly: true },
  );
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
  delete reservedParams[markerKey + "_error_detail"];
  const reservedJson = JSON.stringify(reservedParams);
  const statusSql = allowed.map(status => "'" + status + "'").join(",");
  const reserved = await env.DB.prepare(
    "UPDATE console_jobs SET params_json = ?, updated_at = ? WHERE id = ? AND status IN (" + statusSql +
      ") AND (CASE WHEN json_valid(params_json) THEN COALESCE(json_extract(params_json, '$." + markerKey +
      "'), '') ELSE '' END) NOT IN ('dispatching','dispatched')"
  ).bind(reservedJson, new Date().toISOString(), jobId).run();
  if (Number(reserved && reserved.meta && reserved.meta.changes || 0) !== 1) return { ok: true, skipped: true };
  const result = await dispatchMobXWorkflow(env, workflow, inputs);
  const finalParams = { ...reservedParams, [markerKey]: result.ok ? "dispatched" : "failed" };
  if (!result.ok) {
    finalParams[markerKey + "_error"] = result.error;
    if (result.error_detail) finalParams[markerKey + "_error_detail"] = result.error_detail;
  }
  try {
    await env.DB.prepare("UPDATE console_jobs SET params_json = ?, updated_at = ? WHERE id = ?")
      .bind(JSON.stringify(finalParams), new Date().toISOString(), jobId).run();
  } catch { /* keep errors sanitized; scheduled fallback remains available */ }
  return { ...result, skipped: false };
}

// ---------- Rubika Bot API (token never appears in errors/logs)
async function rubika(env, method, payload, { skipFreshnessCheck = false } = {}) {
  const methodName = String(method || "unknown").toLowerCase().replace(/[^a-z0-9_-]/g, "").slice(0, 40) || "unknown";
  const timing = env && env[PANEL_TIMING];
  if (method !== "deleteMessage" && !skipFreshnessCheck) await assertPanelEventCurrent(env);
  if (timing) timing.rubika_calls += 1;
  let res;
  const requestStartedAt = performance.now();
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
  } finally {
    if (timing) timing.rubika_ms += elapsedMs(requestStartedAt);
  }
  let data = null;
  const bodyStartedAt = performance.now();
  try { data = await res.json(); } catch { /* ignore */ }
  finally { if (timing) timing.rubika_ms += elapsedMs(bodyStartedAt); }
  if (!res.ok || !data || data.status !== "OK") {
    const statusCode = res.ok
      ? String(data && data.status || "bad").toLowerCase().replace(/[^a-z0-9_-]/g, "_").slice(0, 30)
      : "http_" + res.status;
    throw new PanelError("rubika_" + methodName + "_" + statusCode);
  }
  if (method !== "deleteMessage" && !skipFreshnessCheck) {
    try {
      await assertPanelEventCurrent(env);
    } catch (error) {
      const staleMid = data && data.data && data.data.message_id;
      if (error instanceof StalePanelEvent && method === "sendMessage" &&
          typeof staleMid === "string" && staleMid) {
        try { await rubika(env, "deleteMessage", { chat_id: payload && payload.chat_id, message_id: staleMid }); } catch {}
      }
      throw error;
    }
  }
  return data;
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
async function edit(env, chat, mid, src, keypad, { countSuccess = true } = {}) {
  await assertPanelEventCurrent(env);
  const f = md(src);
  const storedMid = await getState(env, "panel_mid", "");
  const targetMid = storedMid || mid;
  const base = { chat_id: chat, message_id: targetMid, text: f.text };
  const hasKeypad = Boolean(keypad && Array.isArray(keypad.rows) &&
    keypad.rows.some((row) => Array.isArray(row.buttons) && row.buttons.length > 0));
  // Ignore legacy panel_sync_mode values: normal panel navigation always edits this message.
  try {
    const updateKeypad = async () => {
      if (!hasKeypad) return;
      try {
        await rubika(env, "editMessageKeypad", { chat_id: chat, message_id: targetMid, inline_keypad: keypad }, { skipFreshnessCheck: true });
      } catch (firstKeypadError) {
        if (firstKeypadError instanceof StalePanelEvent) throw firstKeypadError;
        try {
          await assertPanelEventCurrent(env);
          await rubika(env, "editMessageKeypad", { chat_id: chat, message_id: targetMid, inline_keypad: keypad }, { skipFreshnessCheck: true });
        } catch (retryKeypadError) {
          throw retryKeypadError || firstKeypadError;
        }
      }
    };
    const textPromise = rubika(env, "editMessageText", f.metadata ? { ...base, metadata: f.metadata } : base, { skipFreshnessCheck: true });
    const results = await Promise.allSettled([textPromise, updateKeypad()]);
    const stale = results.find((result) => result.status === "rejected" && result.reason instanceof StalePanelEvent);
    if (stale) throw stale.reason;
    const failed = results.find((result) => result.status === "rejected");
    if (failed) throw failed.reason;
    await assertPanelEventCurrent(env);
    if (countSuccess) {
      try { await bumpCounter(env, "panel_edit_ok"); } catch { /* diagnostics must not block panel rendering */ }
    }
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
const MOB_TEMP_MID_KEY = "panel_mob_temp_mid";
async function deleteMobTemp(env, chat, expectedMid = "") {
  const mid = await getState(env, MOB_TEMP_MID_KEY, "");
  if (!mid || (expectedMid && mid !== expectedMid)) return false;
  try { await rubika(env, "deleteMessage", { chat_id: chat, message_id: mid }); } catch { /* best effort cleanup */ }
  if (await getState(env, MOB_TEMP_MID_KEY, "") === mid) await setState(env, MOB_TEMP_MID_KEY, "");
  return true;
}
async function sendMobTemp(env, chat, src, keypad) {
  await deleteMobTemp(env, chat);
  await assertPanelEventCurrent(env);
  const f = md(src);
  const hasKeypad = Boolean(keypad && Array.isArray(keypad.rows) &&
    keypad.rows.some((row) => Array.isArray(row.buttons) && row.buttons.length > 0));
  const response = await rubika(env, "sendMessage", {
    chat_id: chat, text: f.text, ...(f.metadata ? { metadata: f.metadata } : {}),
    ...(hasKeypad ? { inline_keypad: keypad } : {}),
  });
  const mid = responseMessageId(response);
  try { await setState(env, MOB_TEMP_MID_KEY, mid); }
  catch (error) {
    try { await rubika(env, "deleteMessage", { chat_id: chat, message_id: mid }); } catch {}
    throw error;
  }
  return response;
}
async function editMobTemp(env, chat, src, keypad) {
  const mid = await getState(env, MOB_TEMP_MID_KEY, "");
  if (!mid) return sendMobTemp(env, chat, src, keypad);
  const f = md(src);
  const includeKeypad = Boolean(keypad && Array.isArray(keypad.rows) &&
    keypad.rows.some((row) => Array.isArray(row.buttons) && row.buttons.length > 0));
  try {
    await rubika(env, "editMessageText", {
      chat_id: chat, message_id: mid, text: f.text, ...(f.metadata ? { metadata: f.metadata } : {}),
    });
    if (includeKeypad) {
      await rubika(env, "editMessageKeypad", { chat_id: chat, message_id: mid, inline_keypad: keypad });
    }
    return { data: { message_id: mid } };
  } catch (error) {
    if (error instanceof StalePanelEvent) throw error;
    await deleteMobTemp(env, chat, mid);
    return sendMobTemp(env, chat, src, keypad);
  }
}
async function updateMobMain(ui, src, keypad = HOME_KB) {
  const mid = await getState(ui.env, "panel_mid", "");
  if (mid) return edit(ui.env, ui.chat, mid, src, keypad);
  return send(ui.env, ui.chat, src, keypad);
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

const hasOwnerRole = async (env, chatId, primaryOwner) => {
  try {
    const row = await env.DB.prepare("SELECT role, enabled FROM console_owners WHERE chat_id = ?").bind(chatId).first();
    if (row) return row.role === "owner" && Number(row.enabled) === 1;
    // The singleton row remains the trusted compatibility fallback before migration 0010.
    return safeEqual(primaryOwner || "", chatId);
  } catch {
    return safeEqual(primaryOwner || "", chatId);
  }
};

const isOwnerOnlyPanelAction = (id) => {
  const value = String(id || "");
  return isSettingsId(value) ||
    value === "p_mob" ||
    value === "p_mob_confirm" ||
    value === "p_mob_cancel" ||
    value === "p_mob_edit_cancel" ||
    /^p_mob_(?:publish|edit|nocap|reject|manualconfirm)_job_[A-Fa-f0-9]{8,80}$/.test(value) ||
    value === "p_tiktok_review" ||
    value === "p_tiktok_review_input" ||
    value === "p_scan" ||
    value === "p_scan_confirm" ||
    value === "p_scan_cancel" ||
    value === "p_scan_pub" ||
    value === "p_scan_pub_confirm" ||
    value === "p_pub_control" ||
    /^p_bot_(?:power|off_ask|off_yes|on_ask|on_yes)$/.test(value) ||
    value === "p_pub_pause" ||
    value === "p_pub_resume" ||
    /^p_pub_confirm_(?:pause|resume)_[a-f0-9]{32}$/.test(value) ||
    value === "p_links_clear" ||
    value === "p_links_clear_yes" ||
    value === "p_failall" ||
    value === "p_failall_yes" ||
    /^p_(?:failgo|lseldel)_\d{1,2}_[0-9a-f]{0,5}$/.test(value);
};

// Screen callbacks are rendered back into the tracked main message; show a loading state
// before their D1-backed screen builders or settings actions start.
// These static screens render immediately, so they must not race a loading edit against the final edit.
const isStaticMainPanelScreenClick = (id) =>
  ["p_home", "p_help"].includes(String(id || ""));
const isMainPanelScreenClick = (id) =>
  ["p_home", "p_help", "p_debug", "p_status", "p_queue", "p_ai", "p_report",
    "p_tiktok_review", "p_tiktok_review_input",
    "p_scan", "p_scan_confirm", "p_scan_cancel", "p_scan_pub", "p_scan_pub_confirm", "p_bot_power", "p_bot_off_ask", "p_bot_off_yes", "p_bot_on_ask", "p_bot_on_yes", "p_pub_control", "p_pub_pause", "p_pub_resume"].includes(String(id || "")) ||
  /^p_pub_confirm_(?:pause|resume)_[a-f0-9]{32}$/.test(String(id || "")) ||
  isScreenId(String(id || "")) ||
  isSettingsId(String(id || ""));
const sha256Hex = async (value) => {
  const digest = await crypto.subtle.digest("SHA-256", te.encode(value));
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
};
const getState = async (env, k, d) => {
  const cache = env[PANEL_STATE_CACHE];
  if (cache?.has(k)) {
    const cached = cache.get(k);
    return cached === PANEL_STATE_MISSING ? d : cached;
  }
  const value = (await env.DB.prepare("SELECT value FROM panel_state WHERE key=?").bind(k).first())?.value ?? d;
  if (cache) cache.set(k, value);
  return value;
};
function primePanelStateCache(env, rows) {
  const cache = env[PANEL_STATE_CACHE];
  if (!cache) return;
  const values = new Map((Array.isArray(rows) ? rows : [])
    .filter((row) => row && typeof row.key === "string")
    .map((row) => [row.key, row.value]));
  for (const key of PANEL_STATE_PREFETCH_KEYS) {
    cache.set(key, values.has(key) ? values.get(key) : PANEL_STATE_MISSING);
  }
}
const PANEL_STATE_ACTOR_SQL =
  "SELECT (SELECT chat_id FROM panel_owner WHERE id = 1) AS primary_owner, " +
  "co.id AS console_owner_id, co.role AS console_owner_role, co.enabled AS console_owner_enabled " +
  "FROM (SELECT 1) AS singleton LEFT JOIN console_owners co ON co.chat_id = ?";
const PANEL_STATE_PREFETCH_SQL =
  "SELECT key, value FROM panel_state WHERE key IN (" +
  PANEL_STATE_PREFETCH_KEYS.map(() => "?").join(",") + ")";
async function nextPanelSeq(env) {
  const cache = env[PANEL_STATE_CACHE];
  cache?.delete("panel_seq");
  const row = await env.DB.prepare(
    "INSERT INTO panel_state(key,value,updated_at) VALUES('panel_seq','1',datetime('now')) " +
    "ON CONFLICT(key) DO UPDATE SET value = CAST(panel_state.value AS INTEGER) + 1, updated_at = datetime('now') RETURNING value",
  ).first();
  cache?.delete("panel_seq");
  const seq = Number(row && row.value);
  if (!Number.isSafeInteger(seq) || seq < 1) throw new PanelError("panel_seq_unavailable");
  return seq;
}
async function assertPanelEventCurrent(env) {
  if (typeof env[PANEL_SEQ] !== "number") return;
  // This guard must bypass the event cache so interleaved clicks see the latest sequence.
  const cache = env[PANEL_STATE_CACHE];
  cache?.delete("panel_seq");
  const current = Number(await getState(env, "panel_seq", "0"));
  cache?.delete("panel_seq");
  if (current === env[PANEL_SEQ]) return;
  try { await bumpCounter(env, "panel_stale_skip"); } catch { /* stale skip diagnostics are best effort */ }
  throw new StalePanelEvent();
}
const setState = async (env, k, v) => {
  const cache = env[PANEL_STATE_CACHE];
  cache?.delete(k);
  try {
    return await env.DB.prepare(
      "INSERT INTO panel_state(key,value,updated_at) VALUES(?,?,datetime('now')) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
    ).bind(k, String(v)).run();
  } finally {
    cache?.delete(k);
  }
};
async function setStates(env, entries) {
  if (!Array.isArray(entries) || entries.length === 0) return [];
  const cache = env[PANEL_STATE_CACHE];
  for (const [key] of entries) cache?.delete(key);
  try {
    return await env.DB.batch(entries.map(([key, value]) => env.DB.prepare(
      "INSERT INTO panel_state(key,value,updated_at) VALUES(?,?,datetime('now')) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
    ).bind(key, String(value))));
  } finally {
    for (const [key] of entries) cache?.delete(key);
  }
}

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
          finally {
            timing.d1_queries += 1;
            timing.d1_ms += elapsedMs(startedAt);
          }
        };
      }
      return typeof value === "function" ? value.bind(target) : value;
    },
  });
}
function withPanelTiming(env, timing) {
  const scoped = Object.create(env);
  Object.defineProperty(scoped, PANEL_TIMING, { value: timing });
  Object.defineProperty(scoped, PANEL_STATE_CACHE, { value: new Map() });
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
              finally {
                timing.d1_queries += Array.isArray(args[0]) ? args[0].length : 1;
                timing.d1_ms += elapsedMs(startedAt);
              }
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
async function writePanelStateValues(env, entries) {
  if (!Array.isArray(entries) || entries.length === 0) return;
  const cache = env[PANEL_STATE_CACHE];
  for (const [key] of entries) cache?.delete(key);
  const placeholders = entries.map(() => "(?,?,datetime('now'))").join(", ");
  const bindings = entries.flatMap(([key, value]) => [key, String(value)]);
  try {
    return await env.DB.prepare(
      "INSERT INTO panel_state(key,value,updated_at) VALUES " + placeholders +
      " ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at"
    ).bind(...bindings).run();
  } finally {
    for (const [key] of entries) cache?.delete(key);
  }
}

async function recordPanelTiming(env, timing, msgAge) {
  try {
    const raw = await getState(env, "last_timings", "[]");
    let history = [];
    try { history = JSON.parse(raw); } catch { /* discard malformed prior diagnostics */ }
    if (!Array.isArray(history)) history = [];
    history = history.filter((item) => item && typeof item === "object" && !Array.isArray(item))
      .map((item) => ({
        event_kind: item.event_kind === "click" || item.event_kind === "text" ? item.event_kind : "unknown",
        msg_age_s: typeof item.msg_age_s === "number" && Number.isFinite(item.msg_age_s) ? item.msg_age_s : null,
        total_ms: Number.isFinite(item.total_ms) && item.total_ms >= 0 ? item.total_ms : 0,
        d1_queries: Number.isSafeInteger(item.d1_queries) && item.d1_queries >= 0 ? item.d1_queries : 0,
        d1_ms: Number.isFinite(item.d1_ms) && item.d1_ms >= 0 ? item.d1_ms : 0,
        rubika_calls: Number.isSafeInteger(item.rubika_calls) && item.rubika_calls >= 0 ? item.rubika_calls : 0,
        rubika_ms: Number.isFinite(item.rubika_ms) && item.rubika_ms >= 0 ? item.rubika_ms : 0,
        worker_api_calls: Number.isSafeInteger(item.worker_api_calls) && item.worker_api_calls >= 0 ? item.worker_api_calls : 0,
        worker_api_ms: Number.isFinite(item.worker_api_ms) && item.worker_api_ms >= 0 ? item.worker_api_ms : 0,
        external_calls: Number.isSafeInteger(item.external_calls) && item.external_calls >= 0 ? item.external_calls : 0,
        external_ms: Number.isFinite(item.external_ms) && item.external_ms >= 0 ? item.external_ms : 0,
      })).slice(-4);
    const row = {
      event_kind: timing.event_kind,
      msg_age_s: msgAge,
      total_ms: elapsedMs(timing.startedAt),
      d1_queries: timing.d1_queries,
      d1_ms: timing.d1_ms,
      rubika_calls: timing.rubika_calls,
      rubika_ms: timing.rubika_ms,
      worker_api_calls: timing.worker_api_calls,
      worker_api_ms: timing.worker_api_ms,
      external_calls: timing.external_calls,
      external_ms: timing.external_ms,
    };
    const writes = [["last_timings", JSON.stringify([...history, row].slice(-5))]];
    if (typeof timing.last_event_json === "string") writes.unshift(["last_event", timing.last_event_json]);
    await writePanelStateValues(env, writes);
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
    `• توقف اضطراری: ${s.publishing_control_available === true ? (s.publishing_paused === true ? "فعال — ارسال مسدود" : "غیرفعال") : "نامعلوم — fail-closed"}`,
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
  if (!d) return `🤖 ${L}: موقتاً در دسترس نیست (provider_missing)`;
  if (d.ok !== true) return `🤖 ${L}: در دسترس نیست (${clean(d.error, 60) || "unknown"})`;
  const state = d.cooldown_active === true ? "در دورهٔ توقف" : d.ready === true ? "آماده طبق ledger" : "غیرآماده";
  return `🤖 **${L}**: ${state}\nسهمیهٔ امروز: ${n(d.calls_used)}/${n(d.daily_limit)}، باقی‌مانده: ${n(d.remaining)}\nخطای اخیر: ${clean(d.last_error_code, 60) || "ندارد"}`;
}

// ---------- screens
async function api(ui, path) {
  const timing = ui.env && ui.env[PANEL_TIMING];
  if (timing) timing.worker_api_calls += 1;
  const startedAt = performance.now();
  try {
    const res = await ui.selfFetch(new Request(ui.origin + path, { headers: { authorization: `Bearer ${ui.env.AGENT_API_TOKEN}`, accept: "application/json" } }));
    return await res.json();
  } catch { return { ok: false }; }
  finally { if (timing) timing.worker_api_ms += elapsedMs(startedAt); }
}
async function apiPost(ui, path, body) {
  const timing = ui.env && ui.env[PANEL_TIMING];
  if (timing) timing.worker_api_calls += 1;
  const startedAt = performance.now();
  try {
    const payload = { ...body, chat_id: ui.chat };
    const res = await ui.selfFetch(new Request(ui.origin + path, {
      method: "POST",
      headers: {
        authorization: `Bearer ${ui.env.AGENT_API_TOKEN}`,
        accept: "application/json",
        "content-type": "application/json",
      },
      body: JSON.stringify(payload),
    }));
    return await res.json();
  } catch { return { ok: false }; }
  finally { if (timing) timing.worker_api_ms += elapsedMs(startedAt); }
}

async function renderPublishingControl(ui, actionId) {
  const match = /^p_pub_confirm_(pause|resume)_([a-f0-9]{32})$/.exec(actionId);
  if (actionId === "p_pub_control") {
    const control = await api(ui, "/api/brain/publishing-control");
    const available = control && control.ok === true && typeof control.paused === "boolean";
    const paused = available && control.paused === true;
    const body = available
      ? [
          "🛑 **کنترل اضطراری انتشار**",
          "",
          paused ? "⛔ وضعیت: **متوقف**" : "✅ وضعیت: **فعال**",
          paused ? "همهٔ مسیرهای انتشار باید ارسال جدید را رد کنند." : "در حال حاضر توقف اضطراری فعال نیست.",
          String(control.reason_fa || ""),
          "",
          "هر تغییر به دو تأیید جداگانه نیاز دارد و درخواست حداکثر دو دقیقه معتبر است.",
        ].filter(Boolean).join("\n")
      : "⚠️ **وضعیت توقف قابل‌بررسی نیست.** برای ایمنی انتشار باید متوقف بماند. پس از رفع خطا دوباره تازه‌سازی کن.";
    const rows = available
      ? kb([
          [[paused ? "▶️ درخواست ازسرگیری" : "⏸ درخواست توقف", paused ? "p_pub_resume" : "p_pub_pause"]],
          [["🔄 تازه‌سازی", "p_pub_control"], ["🏠 منوی اصلی", "p_home"]],
        ])
      : kb([[["🔄 تازه‌سازی", "p_pub_control"], ["🏠 منوی اصلی", "p_home"]]]);
    await updateMobMain(ui, body, rows);
    return;
  }
  if (actionId === "p_pub_pause" || actionId === "p_pub_resume") {
    const action = actionId === "p_pub_pause" ? "pause" : "resume";
    const preview = await apiPost(ui, "/api/brain/owner-console/publishing-control/preview", { action });
    if (!preview || preview.ok !== true || !/^[a-f0-9]{32}$/.test(String(preview.challenge_id || ""))) {
      await updateMobMain(ui, "⚠️ پیش‌درخواست تأیید ساخته نشد؛ هیچ تغییری اعمال نشد. خطا: " +
        esc(preview && preview.error || "publishing_control_challenge_unavailable"),
        kb([[["🔄 بازگشت", "p_pub_control"], ["🏠 منوی اصلی", "p_home"]]]));
      return;
    }
    const question = action === "pause"
      ? "توقف اضطراری انتشار فعال شود؟ همهٔ مسیرهای ارسال باید متوقف شوند."
      : "توقف اضطراری لغو شود؟ مسیرهای انتشار دوباره اجازهٔ بررسی ارسال خواهند داشت.";
    await updateMobMain(ui, "⚠️ **تأیید دوم لازم است**\n\n" + question +
      "\nدرخواست فقط دو دقیقه اعتبار دارد؛ تا تأیید دوم هیچ وضعیتی تغییر نمی‌کند.",
      kb([
        [[action === "pause" ? "⛔ تأیید توقف" : "▶️ تأیید ازسرگیری", "p_pub_confirm_" + action + "_" + preview.challenge_id]],
        [["لغو", "p_pub_control"], ["🏠 منوی اصلی", "p_home"]],
      ]));
    return;
  }
  if (match) {
    const result = await apiPost(ui, "/api/brain/owner-console/publishing-control/confirm", {
      challenge_id: match[2], confirm: true,
    });
    const message = result && result.ok === true
      ? (result.paused === true ? "⛔ **توقف اضطراری فعال شد.** ارسال جدید باید مسدود باشد." : "✅ **توقف اضطراری لغو شد.**")
      : "⚠️ تأیید نامعتبر یا منقضی است؛ هیچ تغییری اعمال نشد. خطا: " +
        esc(result && result.error || "publishing_control_confirmation_failed");
    await updateMobMain(ui, message,
      kb([[["🔄 بررسی وضعیت", "p_pub_control"], ["🏠 منوی اصلی", "p_home"]]]));
  }
}

function mobPreviewKeypad(jobId) {
  return kb([
    [["✅ انتشار در کانال", "p_mob_publish_" + jobId], ["✏️ ویرایش کپشن", "p_mob_edit_" + jobId]],
    [["🚫 بدون کپشن", "p_mob_nocap_" + jobId], ["✖️ رد", "p_mob_reject_" + jobId]],
  ]);
}

async function saveMobCaptionAndRefresh(ui, jobId, row, result, caption, {
  clearSource = false, clearEditMode = false, successText = "✅ کپشن ویرایش شد و پیش‌نمایش جدید ارسال شد.",
} = {}) {
  const env = ui.env;
  const chat = ui.chat;
  const previousPreviewId = typeof result.preview_message_id === "string" ? result.preview_message_id : "";
  const nextResult = {
    ...result,
    caption,
    caption_mode: clearSource ? "no_caption" : "edited",
    caption_refresh_state: "pending",
    preview_sent: false,
  };
  if (clearSource) {
    nextResult.original_caption = "";
    nextResult.persian_translation = "";
  }
  delete nextResult.preview_message_id;
  if (clearEditMode) {
    await setStates(env, [["mode", "idle"], ["panel_mob_edit_job_id", ""]]);
    await deleteMobTemp(env, chat);
  }
  const pendingJson = JSON.stringify(nextResult);
  const now = new Date().toISOString();
  const claimed = await env.DB.prepare(
    "UPDATE console_jobs SET result_json = ?, updated_at = ? " +
    "WHERE id = ? AND type = 'mob_x_link' AND status = 'ready_for_review' AND result_json = ?"
  ).bind(pendingJson, now, jobId, row.result_json).run();
  if (!claimed?.meta?.changes) {
    await updateMobMain(ui, "ذخیرهٔ کپشن انجام نشد؛ وضعیت کار یا کپشن هم‌زمان تغییر کرده است.", HOME_KB);
    return false;
  }

  try {
    if (clearSource) {
      await env.DB.prepare(
        "UPDATE items SET caption = ?, original_caption = '', persian_translation = '' " +
        "WHERE id = ? AND platform = 'mob_official' AND status = 'ready'"
      ).bind(caption, nextResult.item_id).run();
    } else {
      await env.DB.prepare(
        "UPDATE items SET caption = ? WHERE id = ? AND platform = 'mob_official' AND status = 'ready'"
      ).bind(caption, nextResult.item_id).run();
    }

    const formatted = md(caption);
    const preview = await rubika(env, "sendFile", {
      chat_id: chat,
      file_id: nextResult.rubika_file_id,
      text: formatted.text,
      ...(formatted.metadata ? { metadata: formatted.metadata } : {}),
      inline_keypad: mobPreviewKeypad(jobId),
      disable_notification: false,
      chat_keypad_type: "None",
    });
    const messageId = preview?.data?.message_id;
    nextResult.preview_sent = true;
    nextResult.caption_refresh_state = "done";
    if (typeof messageId === "string" && messageId) nextResult.preview_message_id = messageId;
    const finishedJson = JSON.stringify(nextResult);
    const finished = await env.DB.prepare(
      "UPDATE console_jobs SET result_json = ?, updated_at = ? " +
      "WHERE id = ? AND type = 'mob_x_link' AND status = 'ready_for_review' AND result_json = ?"
    ).bind(finishedJson, new Date().toISOString(), jobId, pendingJson).run();
    if (!finished?.meta?.changes) {
      await updateMobMain(ui, "پیش‌نمایش ارسال شد، اما وضعیت کار هم‌زمان تغییر کرد؛ آن را تأیید نکن و وضعیت را تازه‌سازی کن.", HOME_KB);
      return false;
    }
    if (previousPreviewId && messageId && previousPreviewId !== messageId) {
      try { await rubika(env, "deleteMessage", { chat_id: chat, message_id: previousPreviewId }); } catch {}
    }
    await updateMobMain(ui, successText, HOME_KB);
    return true;
  } catch {
    const failedResult = { ...nextResult, preview_sent: false, caption_refresh_state: "failed" };
    await env.DB.prepare(
      "UPDATE console_jobs SET result_json = ?, updated_at = ? " +
      "WHERE id = ? AND type = 'mob_x_link' AND status = 'ready_for_review' AND result_json = ?"
    ).bind(JSON.stringify(failedResult), new Date().toISOString(), jobId, pendingJson).run();
    await updateMobMain(ui, "کپشن ذخیره شد، اما پیش‌نمایش تازه ناموفق بود؛ انتشار را تأیید نکن تا پیش‌نمایش دوباره آماده شود.", HOME_KB);
    return false;
  }
}

async function debugText(env) {
  let channelEvent = null;
  try { channelEvent = JSON.parse(await getState(env, "last_channel_event", "null")); } catch { /* ignore */ }
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
  const panelSyncMode = "inplace";
  let timings = [];
  try {
    const parsed = JSON.parse(await getState(env, "last_timings", "[]"));
    if (Array.isArray(parsed)) timings = parsed.slice(-5).map((item) => ({
      event_kind: item?.event_kind === "click" || item?.event_kind === "text" ? item.event_kind : "unknown",
      msg_age_s: typeof item?.msg_age_s === "number" && Number.isFinite(item.msg_age_s) ? item.msg_age_s : null,
      total_ms: Number.isFinite(item?.total_ms) && item.total_ms >= 0 ? item.total_ms : 0,
      d1_queries: Number.isSafeInteger(item?.d1_queries) && item.d1_queries >= 0 ? item.d1_queries : 0,
      d1_ms: Number.isFinite(item?.d1_ms) && item.d1_ms >= 0 ? item.d1_ms : 0,
      rubika_calls: Number.isSafeInteger(item?.rubika_calls) && item.rubika_calls >= 0 ? item.rubika_calls : 0,
      rubika_ms: Number.isFinite(item?.rubika_ms) && item.rubika_ms >= 0 ? item.rubika_ms : 0,
      worker_api_calls: Number.isSafeInteger(item?.worker_api_calls) && item.worker_api_calls >= 0 ? item.worker_api_calls : 0,
      worker_api_ms: Number.isFinite(item?.worker_api_ms) && item.worker_api_ms >= 0 ? item.worker_api_ms : 0,
      external_calls: Number.isSafeInteger(item?.external_calls) && item.external_calls >= 0 ? item.external_calls : 0,
      external_ms: Number.isFinite(item?.external_ms) && item.external_ms >= 0 ? item.external_ms : 0,
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
    "last_channel_event: " + (channelEvent ? "`" + JSON.stringify(channelEvent) + "`" : "none"),
  ].join("\n");
}

async function renderData(ui, id, { fresh = false, clickedMid = "" } = {}) {
  const env = ui.env;
  const chat = ui.chat;
  const previousMid = await getState(env, "panel_mid", "");
  const targetMid = clickedMid && clickedMid === previousMid ? clickedMid : previousMid;
  const view = id === "p_report" ? "report" : id === "p_ai" ? "ai" : "status";
  const payload = await api(ui, "/api/brain/owner-console/panel-summary?view=" + view);
  const statusPayload = payload && payload.status;
  const queuePayload = payload && payload.queue;
  const providers = payload && payload.ai && payload.ai.providers || {};
  let out;
  if (id === "p_status") out = fmtStatus(statusPayload);
  else if (id === "p_queue") out = fmtQueue(queuePayload);
  else if (id === "p_ai") {
    out = "🤖 **دفتر سهمیهٔ هوش مصنوعی**\n\n" +
      ["groq", "openrouter"].map((provider) => fmtAi(provider, providers[provider])).join("\n\n");
  } else {
    out = [
      "🧾 **گزارش Rubika-Agent**",
      fmtStatus(statusPayload),
      fmtQueue(queuePayload),
      ["groq", "openrouter"].map((provider) => fmtAi(provider, providers[provider])).join("\n\n"),
    ].join("\n\n");
  }
  if (fresh) return sendFreshPanel(ui, out, refreshKb(id), previousMid);
  if (targetMid) return edit(env, chat, targetMid, out, refreshKb(id));
  return send(env, chat, out, refreshKb(id));
}

async function render(ui, id, { fresh = false, clickedMid = "" } = {}) {
  const env = ui.env;
  const chat = ui.chat;
  if (id !== "p_mob" && await getState(env, "mode", "idle") !== "idle") await setState(env, "mode", "idle");

  const mobAction = /^p_mob_(publish|edit|nocap|reject|manualconfirm)_(job_[A-Fa-f0-9]{8,80})$/.exec(id);
  if (mobAction || id === "p_mob_edit_cancel") {
    if (id === "p_mob_edit_cancel") {
      await setStates(env, [["mode", "idle"], ["panel_mob_edit_job_id", ""]]);
      await deleteMobTemp(env, chat, clickedMid);
      await updateMobMain(ui, "✏️ ویرایش کپشن لغو شد.", HOME_KB);
      return;
    }
    const action = mobAction[1];
    const jobId = mobAction[2];
    const row = await env.DB.prepare(
      "SELECT id, type, status, panel_link_id, params_json, result_json FROM console_jobs WHERE id = ? AND type = 'mob_x_link'"
    ).bind(jobId).first();
    if (!row) {
      await updateMobMain(ui, "کار خبر Mob پیدا نشد.", HOME_KB);
      return;
    }
    let result = {};
    try { result = row.result_json ? JSON.parse(row.result_json) : {}; } catch {}
    if (!result || typeof result !== "object" || Array.isArray(result)) result = {};

    if (action === "edit") {
      if (row.status !== "ready_for_review" || !result.rubika_file_id) {
        await updateMobMain(ui, "این پیش‌نمایش دیگر قابل ویرایش نیست؛ وضعیت کار: " + esc(row.status), HOME_KB);
        return;
      }
      await setStates(env, [["panel_mob_edit_job_id", jobId], ["mode", "waiting_mob_caption_edit"]]);
      await sendMobTemp(env, chat, "✏️ کپشن نهایی را به‌صورت متن ساده بفرست. پیش‌نمایش قبلی تا ثبت کپشن جدید معتبر می‌ماند.", kb([[["لغو ویرایش", "p_mob_edit_cancel"]]]));
      await updateMobMain(ui, "✅ درخواست ویرایش آماده است؛ کپشن را در پیام جداگانه بفرست.", HOME_KB);
      return;
    }

    if (action === "nocap") {
      if (row.status !== "ready_for_review" || !result.rubika_file_id) {
        await updateMobMain(ui, "این پیش‌نمایش دیگر برای تغییر کپشن باز نیست؛ وضعیت کار: " + esc(row.status), HOME_KB);
        return;
      }
      if (result.caption_mode === "no_caption" &&
          result.caption === buildMobXNoCaptionCaption() &&
          result.preview_sent === true) {
        await updateMobMain(ui, "این کپشن قبلاً برای همین پست اعمال شده است؛ تأیید انتشار همچنان جداگانه انجام می‌شود.", HOME_KB);
        return;
      }
      if (result.caption_refresh_state === "pending") {
        await updateMobMain(ui, "به‌روزرسانی پیش‌نمایش همین پست هنوز در حال انجام است؛ دوباره کلیک نکن.", HOME_KB);
        return;
      }
      await saveMobCaptionAndRefresh(
        ui, jobId, row, result, buildMobXNoCaptionCaption(),
        { clearSource: true, successText: "✅ کپشن حذف و متن ثابت فقط برای همین پست جایگزین شد؛ پیش‌نمایش جدید آمادهٔ بررسی است." },
      );
      return;
    }

    if (action === "reject") {
      if (row.status !== "ready_for_review") {
        await updateMobMain(ui, "فقط پیش‌نمایش آمادهٔ بررسی را می‌توان رد کرد. وضعیت فعلی: " + esc(row.status), HOME_KB);
        return;
      }
      const now = new Date().toISOString();
      const updated = await env.DB.batch([
        env.DB.prepare("UPDATE console_jobs SET status = 'rejected', progress = 100, error = NULL, updated_at = ? WHERE id = ? AND type = 'mob_x_link' AND status = 'ready_for_review'").bind(now, jobId),
        env.DB.prepare("UPDATE panel_links SET status = 'rejected', updated_at = ? WHERE id = ? AND status = 'ready_for_review' AND changes() > 0").bind(now, row.panel_link_id),
        env.DB.prepare("UPDATE items SET review_state = 'rejected' WHERE id = ? AND platform = 'mob_official' AND status = 'ready' AND review_state = 'pending' AND changes() > 0").bind(result.item_id),
      ]);
      if (!updated?.[0]?.meta?.changes) {
        await updateMobMain(ui, "وضعیت کار هم‌زمان تغییر کرده؛ هیچ اقدامی انجام نشد.", HOME_KB);
        return;
      }
      await updateMobMain(ui, "✖️ خبر رد شد و در کانال منتشر نمی‌شود.", HOME_KB);
      return;
    }

    if (action === "publish") {
      const publishingControl = await api(ui, "/api/brain/publishing-control");
      if (publishingControl.ok !== true || publishingControl.publishing_paused === true || publishingControl.paused === true) {
        await updateMobMain(ui, String(publishingControl.reason_fa || "انتشار متوقف است یا وضعیت توقف قابل‌بررسی نیست؛ هیچ تأییدی ثبت نشد."), HOME_KB);
        return;
      }
      const duplicate = await findMobXDuplicate(env.DB, result.external_id || result.item_id, {
        excludeItemId: result.item_id,
        excludeJobId: jobId,
        excludePanelLinkId: row.panel_link_id,
      });
      if (duplicate.invalid || duplicate.duplicate) {
        await updateMobMain(ui, "این پست قبلاً ارسال شده", HOME_KB);
        return;
      }
      if (row.status === "approved") {
        const dispatchResult = await dispatchMobXJobOnce(
          env, jobId, "publish_dispatch_state", "mob-x-news-production.yml",
          { panel_link_job_id: jobId }, ["approved"],
        );
        if (!dispatchResult.ok && !dispatchResult.skipped) {
          await updateMobMain(ui, "⚠️ تأیید قبلاً ثبت شده، اما اجرای خودکار شروع نشد (" +
            dispatchResult.error + ")." +
            (dispatchResult.error_detail ? "\n" + dispatchResult.error_detail : "") +
            " مقصد طبق gate مستقل Mob تعیین می‌شود.", HOME_KB);
        } else {
          await updateMobMain(ui, "✅ تأیید قبلاً ثبت شده است؛ اجرای خودکار انتشار " +
            (dispatchResult.skipped ? "قبلاً آغاز یا ثبت شده." : "راه‌اندازی شد."), HOME_KB);
        }
        return;
      }
      if (row.status !== "ready_for_review" || !result.rubika_file_id || result.preview_sent !== true) {
        await updateMobMain(ui, "پیش‌نمایش هنوز آمادهٔ تأیید نهایی نیست؛ لطفاً کمی بعد وضعیت را بررسی کن.", HOME_KB);
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
        await updateMobMain(ui, "تأیید انتشار ثبت نشد؛ وضعیت کار یا آیتم تغییر کرده است.", HOME_KB);
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
        await updateMobMain(ui, "✅ تأیید مالک ثبت شد؛ اما اجرای خودکار شروع نشد (" +
          dispatchResult.error + ")." +
          (dispatchResult.error_detail ? "\n" + dispatchResult.error_detail : "") +
          " مقصد طبق gate مستقل Mob تعیین می‌شود؛ ارسال زمان‌بندی‌شده همچنان فعال می‌ماند.", HOME_KB);
        return;
      }
      await updateMobMain(
        ui,
        "✅ تأیید مالک ثبت شد و اجرای خودکار " +
          (dispatchResult.skipped ? "قبلاً راه‌اندازی شده است." : "درخواست شد.") +
          " مقصد طبق gate مستقل Mob تعیین می‌شود؛ اگر نتیجهٔ ارسال نامشخص شود، ارسال دوباره انجام نمی‌شود.",
        HOME_KB,
      );
      return;
    }

    if (action === "manualconfirm") {
      if (row.status !== "uncertain" || result.delivery_status !== "uncertain") {
        await updateMobMain(ui, "این کار در وضعیت ارسال نامشخص نیست؛ هیچ ارسال جدیدی انجام نشد.", HOME_KB);
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
        await updateMobMain(ui, "تأیید دستی ثبت نشد؛ وضعیت کار تغییر کرده است.", HOME_KB);
        return;
      }
      await updateMobMain(ui, "✅ تأیید دستی ثبت شد؛ وضعیت ارسال «ارسال‌شده» شد. هیچ ارسال جدیدی انجام نشد.", HOME_KB);
      return;
    }
  }
  if (id === "p_mob_confirm" || id === "p_mob_cancel") {
    await deleteMobTemp(env, chat, clickedMid);
    const rawLinkId = await getState(env, "panel_mob_pending_link_id", "");
    const linkId = Number(rawLinkId);
    if (!Number.isSafeInteger(linkId) || linkId < 1) {
      await updateMobMain(ui, "این تأیید منقضی شده است؛ لینک را دوباره بفرست.", HOME_KB);
      return;
    }
    const link = await env.DB.prepare("SELECT id, url, external_id, status FROM panel_links WHERE id = ?").bind(linkId).first();
    if (!link) {
      await setState(env, "panel_mob_pending_link_id", "");
      await updateMobMain(ui, "لینک پیدا نشد؛ دوباره ارسالش کن.", HOME_KB);
      return;
    }
    if (id === "p_mob_cancel") {
      const canceled = await env.DB.prepare("UPDATE panel_links SET status = 'rejected', updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ? AND status = 'pending'").bind(linkId).run();
      await setState(env, "panel_mob_pending_link_id", "");
      if (!canceled?.meta?.changes && link.status !== "rejected") {
        await updateMobMain(ui, "وضعیت لینک تغییر کرده؛ بخش «لینک‌های X» را تازه‌سازی کن.", HOME_KB);
        return;
      }
      return render(ui, "p_links", { clickedMid });
    }

    if (!normalizeMobXExternalId(link.external_id)) {
      await updateMobMain(ui, "شناسهٔ پست معتبر نیست؛ لینک دوباره بررسی شود.", HOME_KB);
      return;
    }
    const existing = await env.DB.prepare("SELECT id FROM console_jobs WHERE panel_link_id = ?").bind(linkId).first();
    if (!existing) {
      const duplicate = await findMobXDuplicate(env.DB, link.external_id, { excludePanelLinkId: linkId });
      if (duplicate.invalid || duplicate.duplicate) {
        await setState(env, "panel_mob_pending_link_id", "");
        await updateMobMain(ui, "این پست قبلاً ارسال شده", HOME_KB);
        return;
      }
      if (!["pending", "confirmed"].includes(link.status)) {
        await updateMobMain(ui, "این لینک دیگر در وضعیت قابل تأیید نیست.", HOME_KB);
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
      await updateMobMain(ui, "ثبت کار انجام نشد؛ خطا: mob_x_link_job_create_failed", HOME_KB);
      return;
    }
    const dispatchResult = await dispatchMobXJobOnce(
      env, job.id, "link_dispatch_state", "mob-x-link-processor.yml",
      { panel_link_id: linkId }, ["confirmed", "queued"],
    );
    await setState(env, "panel_mob_pending_link_id", "");
    if (!dispatchResult.ok && !dispatchResult.skipped) {
      await updateMobMain(ui, "⚠️ راه‌اندازی خودکار پردازش انجام نشد (" + dispatchResult.error +
        ")." + (dispatchResult.error_detail ? "\n" + dispatchResult.error_detail : "") +
        " پردازش زمان‌بندی‌شده همچنان فعال است.", HOME_KB);
      return;
    }
    return render(ui, "p_links", { clickedMid });
  }
  if (id !== "p_links_clear" && id !== "p_links_clear_yes") {
    await setState(env, "panel_links_clear_pending", "no");
  }
  const previousMid = await getState(env, "panel_mid", "");
  const targetMid = clickedMid && clickedMid === previousMid ? clickedMid : previousMid;
  if (id === "p_tiktok_review_input") {
    await setStates(env, [["mode", "waiting_tiktok_review"], ["tiktok_review_challenge", ""]]);
    const prompt = "📝 تصمیم گروهی تیک‌تاک\n\nهر تصمیم را در یک خط بفرست:\n" +
      "لینک TikTok + «قبول» یا «رد»\nحداکثر ۲۰ خط. پس از پیش‌نمایش، تصمیم فقط با پاسخ «تأیید» اعمال می‌شود.";
    const keypad = kb([[["✖️ لغو", "p_tiktok_review"]]]);
    if (targetMid) return edit(env, chat, targetMid, prompt, keypad);
    return send(env, chat, prompt, keypad);
  }
  if (id === "p_tiktok_review") {
    await setState(env, "mode", "idle");
    const result = await api(ui, "/api/brain/owner-console/tiktok-review?chat_id=" + encodeURIComponent(chat));
    const items = Array.isArray(result && result.items) ? result.items.slice(0, 12) : [];
    const reasonFa = (reason, probability) => reason === "music_probability_ambiguous" && typeof probability === "number"
      ? "موسیقی نامشخص (" + probability.toFixed(2).replace(".", "٫").replace(/[0-9]/g, digit => "۰۱۲۳۴۵۶۷۸۹"[Number(digit)]) + ")"
      : reason === "edit_signal_not_found_by_brain" ? "نشانهٔ ادیت پیدا نشد"
        : reason === "music_not_detected" ? "موسیقی تشخیص داده نشد یا صدا کم است" : "نیازمند بررسی مالک";
    const out = !result || result.ok !== true
      ? "❌ فهرست موارد بازبینی فعلاً در دسترس نیست."
      : !items.length
        ? "🎵 در انتظار تأیید شما\n\nموردی برای بازبینی وجود ندارد."
        : "🎵 در انتظار تأیید شما\n\n" + items.map((item, index) =>
          (index + 1) + ". @" + esc(item.account || "unknown") + "\n" +
          esc(String(item.caption_excerpt || "").slice(0, 100)) + "\n" +
          esc(reasonFa(item.reason, item.music_probability)) + "\n" + String(item.url || "")
        ).join("\n\n") + "\n\nبرای تصمیم گروهی، دکمهٔ پایین را بزن.";
    const keypad = kb([
      [["📝 ثبت تصمیم گروهی", "p_tiktok_review_input"]],
      [["🔄 تازه‌سازی", "p_tiktok_review"], ["🏠 منوی اصلی", "p_home"]],
    ]);
    if (fresh) return sendFreshPanel(ui, out, keypad, previousMid);
    if (targetMid) return edit(env, chat, targetMid, out, keypad);
    return send(env, chat, out, keypad);
  }
  if (id === "p_scan") {
    const text = [
      "🔎 **اسکن فوری — فقط گزارش**",
      "",
      "این اجرا فقط Termux metadata را جمع می‌کند و artifact گزارش می‌سازد.",
      "در حالت امنِ این دکمه:",
      "• هیچ گزارشی روی شاخهٔ main نوشته نمی‌شود.",
      "• هیچ ردیفی به D1 یا صف انتشار اضافه نمی‌شود.",
      "• صف TikTok اجرا نمی‌شود و هیچ پیامی منتشر نمی‌شود.",
      "",
      "تأیید می‌کنی اسکن گزارش‌محور شروع شود؟",
    ].join("\n");
    const keypad = kb([
      [["▶️ شروع اسکن فقط‌گزارش", "p_scan_confirm"]],
      [["✖️ لغو", "p_scan_cancel"], ["🏠 منوی اصلی", "p_home"]],
    ]);
    if (fresh) return sendFreshPanel(ui, text, keypad, previousMid);
    if (targetMid) return edit(env, chat, targetMid, text, keypad);
    return send(env, chat, text, keypad);
  }
  if (id === "p_scan_cancel") {
    return render(ui, "p_home", { fresh, clickedMid });
  }
  if (id === "p_scan_confirm") {
    const result = await dispatchTermuxDiscoveryOnce(env);
    let text;
    if (result.skipped) {
      text = "⏸️ اسکن جدید شروع نشد؛ یک اجرای Termux از قبل در صف یا در حال اجراست. برای جلوگیری از اجرای تکراری، همان اجرا حفظ شد.";
    } else if (result.ok) {
      text = "✅ اسکن فقط‌گزارش به GitHub Actions سپرده شد. پس از پایان، artifact با نام online-termux-discovery در اجرای Online Termux discovery and queue sync قرار می‌گیرد. این مسیر D1، صف یا انتشار را اجرا نمی‌کند.";
    } else {
      text = "❌ شروع اسکن تأیید نشد: " + esc(result.error || "github_dispatch_failed") +
        (result.status ? " (HTTP " + Number(result.status) + ")" : "") +
        ". هیچ اجرای جایگزینی آغاز نشد.";
    }
    const keypad = kb([
      [["🔄 بازگشت به پنل", "p_home"]],
    ]);
    if (fresh) return sendFreshPanel(ui, text, keypad, previousMid);
    if (targetMid) return edit(env, chat, targetMid, text, keypad);
    return send(env, chat, text, keypad);
  }
  if (id === "p_scan_pub" || id === "p_scan_pub_confirm") {
    const control = await api(ui, "/api/brain/publishing-control");
    const paused = !control || control.ok !== true || control.publishing_paused === true || control.paused === true;
    let text;
    let keypad = kb([[["🔄 بازگشت به پنل", "p_home"]]]);
    if (paused) {
      text = "⛔ **اسکن و انتشار** اجرا نشد: توقف اضطراری فعال است یا وضعیتش قابل‌بررسی نیست. هیچ اسکنی شروع نشد.";
    } else if (id === "p_scan_pub") {
      text = [
        "🚀 **اسکن و انتشار**",
        "",
        "این دکمه همان اجرای عادی را همین حالا شروع می‌کند:",
        "• اسکن عمیق: تا ۱۰۰ ویدیوی آخر هر پیج بررسی می‌شود (اجرای خودکار فقط ۸ ویدیوی آخر).",
        "• فقط ویدیوهای مرتبط با Poppy وارد صف می‌شوند و در هر بار حداکثر ۲۰ مورد جدید اضافه می‌شود.",
        "• هر مورد واجد شرایط وارد صف می‌شود و صف TikTok اجرا می‌شود.",
        "• مقصد را gate مستقل TikTok تعیین می‌کند؛ اگر باز باشد، کانال اصلی.",
        "• برای توقف فوری، «توقف اضطراری انتشار» را بزن.",
        "",
        "تأیید می‌کنی؟",
      ].join("\n");
      keypad = kb([
        [["▶️ شروع اسکن و انتشار", "p_scan_pub_confirm"]],
        [["✖️ لغو", "p_scan_cancel"], ["🏠 منوی اصلی", "p_home"]],
      ]);
    } else {
      const result = await dispatchTermuxDiscoveryOnce(env, { publish: true });
      if (result.skipped) {
        text = "⏸️ اسکن جدید شروع نشد؛ یک اجرای Termux از قبل در صف یا در حال اجراست. همان اجرا حفظ شد.";
      } else if (result.ok) {
        text = "✅ اسکن و انتشار به GitHub Actions سپرده شد. ویدیوهای واجد شرایط وارد صف می‌شوند و صف TikTok اجرا می‌شود؛ مقصد طبق gate TikTok تعیین می‌شود. برای توقف فوری از «توقف اضطراری انتشار» استفاده کن.";
      } else {
        text = "❌ شروع اسکن و انتشار تأیید نشد: " + esc(result.error || "github_dispatch_failed") +
          (result.status ? " (HTTP " + Number(result.status) + ")" : "") +
          ". هیچ اجرای جایگزینی آغاز نشد.";
      }
    }
    if (fresh) return sendFreshPanel(ui, text, keypad, previousMid);
    if (targetMid) return edit(env, chat, targetMid, text, keypad);
    return send(env, chat, text, keypad);
  }
  if (/^p_bot_(?:power|off_ask|off_yes|on_ask|on_yes)$/.test(id)) {
    const off = await isBotOff(env.DB);
    let text;
    let keypad = kb([[["🔄 تازه‌سازی", "p_bot_power"], ["🏠 منوی اصلی", "p_home"]]]);
    if (id === "p_bot_power") {
      text = [
        "⏻ **خاموش/روشن کردن ربات**",
        "",
        off ? "⛔ وضعیت: **خاموش**" : "✅ وضعیت: **روشن**",
        "",
        "خاموش‌کردن: انتشار متوقف می‌شود و کارهای زمان‌بندی‌شدهٔ GitHub و Worker (کشف، صف، خبر Mob، خلاصهٔ روزانه) غیرفعال می‌شوند. پنل روشن می‌ماند.",
        "روشن‌کردن: همهٔ آن‌ها دوباره فعال می‌شوند و توقف انتشار برداشته می‌شود.",
      ].join("\n");
      keypad = kb([
        [[off ? "▶️ درخواست روشن‌کردن" : "⏻ درخواست خاموش‌کردن", off ? "p_bot_on_ask" : "p_bot_off_ask"]],
        [["🔄 تازه‌سازی", "p_bot_power"], ["🏠 منوی اصلی", "p_home"]],
      ]);
    } else if (id === "p_bot_off_ask" || id === "p_bot_on_ask") {
      const turningOff = id === "p_bot_off_ask";
      text = turningOff
        ? "⚠️ **تأیید خاموش‌کردن**\n\nانتشار، کشف و صف متوقف و کارهای زمان‌بندی‌شده غیرفعال می‌شوند. ادامه می‌دهی؟"
        : "⚠️ **تأیید روشن‌کردن**\n\nکارهای زمان‌بندی‌شده دوباره فعال و توقف انتشار برداشته می‌شود؛ ربات طبق gateها دوباره انتشار می‌کند. ادامه می‌دهی؟";
      keypad = kb([
        [[turningOff ? "⛔ تأیید خاموش‌کردن" : "▶️ تأیید روشن‌کردن", turningOff ? "p_bot_off_yes" : "p_bot_on_yes"]],
        [["✖️ لغو", "p_bot_power"], ["🏠 منوی اصلی", "p_home"]],
      ]);
    } else {
      const result = await setBotPower(env, id === "p_bot_on_yes");
      if (!result.ok) {
        text = "⚠️ تغییر انجام نشد: " + esc(result.error || "bot_power_failed") + ". وضعیت را از همین صفحه تازه‌سازی کن.";
      } else {
        const failed = (result.results || []).filter((item) => !item.ok).map((item) => item.workflow.replace(".yml", ""));
        text = (result.bot_off ? "⛔ **ربات خاموش شد.**" : "✅ **ربات روشن شد.**") +
          (!result.workflows_checked
            ? "\nتوکن GitHub تنظیم نیست؛ فقط توقف انتشار و Worker اعمال شد."
            : failed.length
              ? "\n⚠️ این کارها تغییر نکردند (دسترسی GitHub را بررسی کن): " + esc(failed.join("، "))
              : "\nهمهٔ کارهای زمان‌بندی‌شده تغییر کردند.");
      }
    }
    if (fresh) return sendFreshPanel(ui, text, keypad, previousMid);
    if (targetMid) return edit(env, chat, targetMid, text, keypad);
    return send(env, chat, text, keypad);
  }
  if (id === "p_pub_control" || id === "p_pub_pause" || id === "p_pub_resume" || /^p_pub_confirm_(?:pause|resume)_[a-f0-9]{32}$/.test(id)) {
    await renderPublishingControl(ui, id);
    return;
  }
  const statics = {
    p_help: [HELP_TEXT, BACK_KB],
    p_mob: ["📰 **خبر رسمی Mob از لینک X**\n\nلینک https://x.com/mobgamesstudios/status/شناسه را بفرست.", CANCEL_KB],
  };
  if (id === "p_mob") await setState(env, "mode", "waiting_link");
  if (isSettingsId(id)) {
    if (!(await hasOwnerRole(env, chat, await getOwner(env)))) {
      try { await rubika(env, "sendMessage", { chat_id: chat, text: "فقط نقش مالک اجازهٔ مشاهده و تغییر این تنظیمات را دارد." }); } catch {}
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
  "/scan": "p_scan", "/اسکن": "p_scan",
  "/scanpub": "p_scan_pub", "/اسکن_انتشار": "p_scan_pub", "/اسکن_و_انتشار": "p_scan_pub",
  "/review": "p_tiktok_review", "/بازبینی": "p_tiktok_review",
  "/pause": "p_pub_control", "/توقف": "p_pub_control",
  "/power": "p_bot_power", "/ربات": "p_bot_power", "/خاموش": "p_bot_power", "/روشن": "p_bot_power",
  "/وضعیت": "p_status", "/صف": "p_qs", "/گزارش": "p_report", "/خطاها": "p_errors", "/راهنما": "p_help", "/منو": "p_home",
  "/retry": "p_fail", "/تلاش_مجدد": "p_fail",
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
    await send(env, ev.chat, "✅ **مالک ثبت شد.** از حالا چت تأییدشده به پنل دسترسی دارد.\n\n" + HOME_TEXT, HOME_KB);
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
    await send(env, ev.chat, "✅ **دعوت پذیرفته شد.** دسترسی شما ثبت شد.\n\n" + HOME_TEXT, HOME_KB);
  } catch {
    await send(env, ev.chat, "⚠️ امکان بررسی کد دعوت نیست؛ پایگاه داده یا migration لازم آماده نیست.");
  }
}

const idTail = (value) => String(value ?? "").slice(-4);
const CHANNEL_HASH_LABELS = { "83dc2fe315b4": "main", "0c939688dd55": "test" };
async function recordChannelProbe(env, ev) {
  try {
    if (!ev || typeof ev.chat !== "string" || !ev.chat) return;
    const lower = ev.chat.toLowerCase();
    const channelish = lower.startsWith("c0") || ev.isGroupish === true ||
      (Array.isArray(ev.types) && ev.types.some((t) => String(t).includes("channel")));
    if (!channelish) return;
    const hash12 = (await sha256Hex(ev.chat)).slice(0, 12);
    const match = CHANNEL_HASH_LABELS[hash12] || "other";
    const record = {
      at: new Date().toISOString(),
      kind: clean(ev.kind, 16) || "unknown",
      first2: lower.slice(0, 2),
      len: ev.chat.length,
      hash12,
      match,
      types: Array.isArray(ev.types) ? ev.types.slice(0, 4).map((v) => clean(v, 24)).filter(Boolean) : [],
      botAuthored: ev.botAuthored === true,
    };
    if (match === "other") record.chat_id = String(ev.chat).replace(/[^A-Za-z0-9_-]/g, "").slice(0, 64);
    await setState(env, "last_channel_event", JSON.stringify(record));
  } catch { /* diagnostics only; never affect the panel */ }
}
async function processEvent(env, selfFetch, origin, ev) {
  if (!ev) return;
  const timing = {
    startedAt: performance.now(),
    event_kind: ev.kind === "click" ? "click" : "text",
    d1_queries: 0, d1_ms: 0,
    rubika_calls: 0, rubika_ms: 0,
    worker_api_calls: 0, worker_api_ms: 0,
    external_calls: 0, external_ms: 0,
  };
  const receivedAt = Date.now();
  const timedEnv = withPanelTiming(env, timing);
  await recordChannelProbe(timedEnv, ev);
  if (ev.kind === "text" && !ev.botAuthored && isPrivateChat(ev) && /^\/claim\s+\S{1,128}\s*$/.test(ev.text)) {
    await tryClaim(timedEnv, ev);
    return;
  }
  let actor = null;
  try {
    const [actorResult, stateResult] = await timedEnv.DB.batch([
      timedEnv.DB.prepare(PANEL_STATE_ACTOR_SQL).bind(ev.chat),
      timedEnv.DB.prepare(PANEL_STATE_PREFETCH_SQL).bind(...PANEL_STATE_PREFETCH_KEYS),
    ]);
    const actorRow = actorResult && Array.isArray(actorResult.results) ? actorResult.results[0] : null;
    const primaryOwner = actorRow && actorRow.primary_owner != null ? String(actorRow.primary_owner) : null;
    const hasConsoleOwner = Boolean(actorRow && actorRow.console_owner_id != null);
    const enabled = hasConsoleOwner && Number(actorRow.console_owner_enabled) === 1;
    actor = {
      primaryOwner,
      hasAccess: hasConsoleOwner ? enabled : safeEqual(primaryOwner || "", ev.chat),
      hasOwnerRole: hasConsoleOwner
        ? enabled && actorRow.console_owner_role === "owner"
        : safeEqual(primaryOwner || "", ev.chat),
    };
    primePanelStateCache(timedEnv, stateResult && stateResult.results);
  } catch {
    const primaryOwner = await getOwner(timedEnv);
    actor = {
      primaryOwner,
      hasAccess: await hasOwnerAccess(timedEnv, ev.chat, primaryOwner),
      hasOwnerRole: null,
    };
  }
  const owner = actor.primaryOwner;
  if (!owner) {
    if (ev.kind === "text" && !ev.botAuthored && isPrivateChat(ev)) await tryClaim(timedEnv, ev);
    return;
  }
  if (!actor.hasAccess) return;
  try {
    await timedEnv.DB.prepare("UPDATE console_owners SET last_seen_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE chat_id = ? AND enabled = 1")
      .bind(ev.chat).run();
  } catch { /* migration 0010 may not be applied yet; keep existing panel behavior */ }
  const msgAge = messageAgeSeconds(ev.time, receivedAt);
  let mobTextMode = "";
  try {
    const lastEvent = {
      kind: clean(ev.kind, 16) || "unknown",
      types: Array.isArray(ev.types) ? ev.types.slice(0, 4).map((value) => clean(value, 24)).filter(Boolean) : [],
      chat_tail: idTail(ev.chat),
      message_id_tail: idTail(ev.mid),
      button: clean(ev.button, 60),
      botAuthored: ev.botAuthored === true,
    };
    timing.last_event_json = JSON.stringify(lastEvent);
    // Keep the current event visible to same-event readers; persistence stays batched at event end.
    timedEnv[PANEL_STATE_CACHE].set("last_event", timing.last_event_json);
    if (ev.botAuthored || !isPrivateChat(ev)) return;
    const key = ev.kind === "click"
      ? "c|" + ev.chat + "|" + ev.mid + "|" + ev.button + "|" + Math.floor(Date.now() / 4000)
      : "t|" + ev.chat + "|" + ev.mid;
    if (!(await firstSeen(timedEnv, key))) return;
    const commandWord = ev.kind === "text"
      ? ev.text.split(/\s+/)[0].split("@")[0].toLowerCase()
      : "";
    const sensitiveFlowMode = ev.kind === "text" ? await getState(timedEnv, "mode", "idle") : "idle";
    const sensitiveCallback = ev.kind === "click" && isOwnerOnlyPanelAction(ev.button);
    const sensitiveText = ev.kind === "text" && (
      commandWord === "/register" ||
      commandWord === "/panelmode" ||
      isOwnerOnlyPanelAction(COMMANDS[commandWord]) ||
      sensitiveFlowMode === "waiting_link" ||
      sensitiveFlowMode === "waiting_mob_caption_edit" ||
      sensitiveFlowMode === "waiting_tiktok_review" ||
      sensitiveFlowMode === "waiting_tiktok_review_confirm"
    );
    const isOwner = actor.hasOwnerRole == null
      ? await hasOwnerRole(timedEnv, ev.chat, owner)
      : actor.hasOwnerRole;
    if ((sensitiveCallback || sensitiveText) && !isOwner) {
      try {
        await rubika(timedEnv, "sendMessage", {
          chat_id: ev.chat,
          text: "این عملیات فقط برای نقش مالک فعال است.",
        });
      } catch {}
      return;
    }
    const seq = await nextPanelSeq(timedEnv);
    Object.defineProperty(timedEnv, PANEL_SEQ, { value: seq });
    const ui = { env: timedEnv, chat: ev.chat, selfFetch, origin };
    if (ev.kind === "click") {
      const currentPanelMid = await getState(timedEnv, "panel_mid", "");
      const isMobJobAction = /^p_mob_(?:publish|edit|nocap|reject|manualconfirm)_job_[A-Fa-f0-9]{8,80}$/.test(String(ev.button || ""));
      const tempMid = await getState(timedEnv, MOB_TEMP_MID_KEY, "");
      const isMobPromptButton = ["p_mob_confirm", "p_mob_cancel", "p_mob_edit_cancel"].includes(String(ev.button || ""));
      const isCurrentMobPromptAction = isMobPromptButton && Boolean(tempMid) && String(ev.mid || "") === tempMid;
      if (String(ev.mid || "") !== currentPanelMid && !isMobJobAction && !isCurrentMobPromptAction) {
        try { if (ev.mid) await rubika(timedEnv, "deleteMessage", { chat_id: ev.chat, message_id: ev.mid }); } catch {}
        const staleButton = String(ev.button || "");
        // Never execute confirmation/destructive actions from an old message.
        let refreshId = staleButton;
        if (["p_mob_confirm", "p_mob_cancel", "p_links_clear", "p_links_clear_yes"].includes(staleButton)) refreshId = "p_links";
        else if (staleButton === "p_mob_edit_cancel") refreshId = "p_mob";
        else if (!isScreenId(staleButton) &&
          !["p_home", "p_status", "p_queue", "p_qs", "p_ai", "p_report", "p_mob", "p_help", "p_debug",
            "p_tiktok_review", "p_tiktok_review_input", "p_pub_control", "p_pub_pause", "p_pub_resume"].includes(staleButton) &&
          !/^p_pub_confirm_(?:pause|resume)_[a-f0-9]{32}$/.test(staleButton)) {
          refreshId = "p_home";
        }
        if (currentPanelMid && !isStaticMainPanelScreenClick(refreshId)) {
          await edit(timedEnv, ev.chat, currentPanelMid, "⏳ در حال انجام…", LOADING_KB, { countSuccess: false });
        }
        await render(ui, refreshId, { clickedMid: currentPanelMid });
        return;
      }
      if (!isStaticMainPanelScreenClick(ev.button) &&
          (isMainPanelScreenClick(ev.button) || isMobJobAction || isCurrentMobPromptAction) && currentPanelMid) {
        await edit(timedEnv, ev.chat, currentPanelMid, "⏳ در حال انجام…", LOADING_KB, { countSuccess: false });
      }
      await render(ui, ev.button, { clickedMid: ev.mid });
      return;
    }
    const word = ev.text.split(/\s+/)[0].split("@")[0].toLowerCase();
    if (word === "/register") { await registerWebhook(timedEnv, origin); await send(timedEnv, ev.chat, "✅ اتصال لحظه‌ای دوباره ثبت شد."); return; }
    if (word === "/panelmode") {
      if (!(await hasOwnerRole(timedEnv, ev.chat, owner))) {
        try { await rubika(timedEnv, "sendMessage", { chat_id: ev.chat, text: "فقط نقش مالک اجازهٔ مشاهدهٔ حالت پنل را دارد." }); } catch {}
        return;
      }
      const currentMid = await getState(timedEnv, "panel_mid", "");
      const requestedInplace = /^\/panelmode\s+inplace\s*$/i.test(ev.text);
      const notice = requestedInplace
        ? "✅ پنل همیشه متن و دکمه‌ها را روی همان پیام ویرایش می‌کند."
        : "حالت atomic غیرفعال است؛ پنل همیشه با حالت inplace ویرایش می‌شود. برای بررسی: /panelmode inplace";
      if (currentMid) await edit(timedEnv, ev.chat, currentMid, notice, HOME_KB);
      else await send(timedEnv, ev.chat, notice, HOME_KB);
      return;
    }
    if (word === "/fix" || word === "/تازه") {
      await setStates(timedEnv, [["mode", "idle"], ["panel_mid", ""]]);
      await render(ui, "p_home", { fresh: true });
      return;
    }
    if (COMMANDS[word]) { await render(ui, COMMANDS[word], { fresh: true }); return; }
    const activeMode = await getState(timedEnv, "mode", "idle");
    mobTextMode = activeMode;
    if (activeMode === "waiting_mob_caption_edit") {
      const jobId = await getState(timedEnv, "panel_mob_edit_job_id", "");
      if (!/^job_[A-Fa-f0-9]{8,80}$/.test(jobId)) {
        await setState(timedEnv, "mode", "idle");
        await deleteMobTemp(timedEnv, ev.chat);
        await updateMobMain(ui, "ویرایش منقضی شده است؛ دوباره از پیش‌نمایش اقدام کن.", HOME_KB);
        return;
      }
      const row = await timedEnv.DB.prepare(
        "SELECT id, status, result_json FROM console_jobs WHERE id = ? AND type = 'mob_x_link'"
      ).bind(jobId).first();
      if (!row || row.status !== "ready_for_review") {
        await setStates(timedEnv, [["mode", "idle"], ["panel_mob_edit_job_id", ""]]);
        await deleteMobTemp(timedEnv, ev.chat);
        await updateMobMain(ui, "این کار دیگر برای ویرایش باز نیست.", HOME_KB);
        return;
      }
      let result = {};
      try { result = row.result_json ? JSON.parse(row.result_json) : {}; } catch {}
      if (!result || typeof result !== "object" || Array.isArray(result) || !result.rubika_file_id) {
        await setStates(timedEnv, [["mode", "idle"], ["panel_mob_edit_job_id", ""]]);
        await deleteMobTemp(timedEnv, ev.chat);
        await updateMobMain(ui, "فایل پیش‌نمایش در دسترس نیست؛ خطا: mob_x_preview_file_id_missing", HOME_KB);
        return;
      }
      const rulesRow = await timedEnv.DB.prepare("SELECT rules_json FROM console_rules WHERE id = 1").first();
      let rules = {};
      try { rules = rulesRow?.rules_json ? JSON.parse(rulesRow.rules_json) : {}; } catch {}
      const captionMax = maxMobXCaptionChars(rules.caption_max_chars);
      const caption = normalizeMobXCaption(ev.text);
      if (!caption) {
        await editMobTemp(timedEnv, ev.chat, "کپشن خالی است؛ متن سادهٔ کپشن را دوباره بفرست.", kb([[["لغو ویرایش", "p_mob_edit_cancel"]]]));
        return;
      }
      if (caption.length > captionMax) {
        await editMobTemp(timedEnv, ev.chat, "کپشن بیش از سقف مجاز " + captionMax + " نویسه است؛ کوتاه‌ترش کن.", kb([[["لغو ویرایش", "p_mob_edit_cancel"]]]));
        return;
      }
      mobTextMode = "mob_input_processing";
      await saveMobCaptionAndRefresh(
        ui, jobId, row, result, caption,
        { clearEditMode: true, successText: "✅ کپشن ویرایش شد و پیش‌نمایش جدید ارسال شد." },
      );
      return;
    }
    if (activeMode === "waiting_tiktok_review") {
      const preview = await apiPost(ui, "/api/brain/owner-console/tiktok-review/preview", { lines: ev.text });
      if (preview && preview.ok === true && typeof preview.challenge_id === "string") {
        await setStates(timedEnv, [
          ["tiktok_review_challenge", preview.challenge_id],
          ["mode", "waiting_tiktok_review_confirm"],
        ]);
        await updateMobMain(ui, "🔎 پیش‌نمایش تصمیم‌ها\n\n" + esc(preview.summary_fa || "پیش‌نمایش آماده شد.") +
          "\n\nبرای اعمال دقیق همین تصمیم‌ها، «تأیید» را بفرست. درخواست ۱۰ دقیقه اعتبار دارد.", HOME_KB);
      } else {
        await setState(timedEnv, "mode", "idle");
        await updateMobMain(ui, "⚠️ پیش‌نمایش انجام نشد: " + esc(preview && preview.error || "no_valid_items") +
          "\n" + esc(preview && preview.summary_fa || ""), HOME_KB);
      }
      return;
    }
    if (activeMode === "waiting_tiktok_review_confirm") {
      const challengeId = await getState(timedEnv, "tiktok_review_challenge", "");
      if (!["تأیید", "تایید"].includes(ev.text.trim()) || !challengeId) {
        await setStates(timedEnv, [["mode", "idle"], ["tiktok_review_challenge", ""]]);
        await updateMobMain(ui, "تصمیم‌های بازبینی لغو شد؛ هیچ موردی تغییر نکرد.", HOME_KB);
        return;
      }
      const confirmed = await apiPost(ui, "/api/brain/owner-console/tiktok-review/confirm", {
        challenge_id: challengeId, confirm: true,
      });
      await setStates(timedEnv, [["mode", "idle"], ["tiktok_review_challenge", ""]]);
      if (confirmed && confirmed.ok === true) {
        await updateMobMain(ui, "✅ تصمیم‌ها ثبت شدند.\nقبول: " + n(confirmed.approved_count) +
          " | رد: " + n(confirmed.rejected_count) +
          "\nموارد تأییدشده از مسیر عادی صف عبور می‌کنند؛ انتشار فوری انجام نشد.", HOME_KB);
      } else {
        await updateMobMain(ui, "⚠️ تأیید منقضی یا نامعتبر بود؛ هیچ ارسال جدیدی انجام نشد. خطا: " +
          esc(confirmed && confirmed.error || "review_confirmation_failed"), HOME_KB);
      }
      return;
    }
    if (activeMode === "waiting_link") {
      const link = parseXLink(ev.text);
      if (!link) {
        await editMobTemp(timedEnv, ev.chat, "فقط لینک پست حساب رسمی mobgamesstudios در x.com یا twitter.com پذیرفته می‌شود. لینک را دوباره بفرست.", CANCEL_KB);
        return;
      }
      mobTextMode = "mob_input_processing";
      await deleteMobTemp(timedEnv, ev.chat);
      const externalId = link.split("/").pop();
      await timedEnv.DB.prepare("INSERT OR IGNORE INTO panel_links (url, external_id, status) VALUES (?, ?, 'pending')").bind(link, externalId).run();
      const saved = await timedEnv.DB.prepare("SELECT id, url, external_id, status FROM panel_links WHERE external_id = ?").bind(externalId).first();
      if (!saved) throw new PanelError("mob_x_link_save_failed");
      if (saved.status === "rejected") {
        // A cancel on the confirmation prompt rejects the link before any job exists.
        // Reopen only that case; links rejected after a real review (a job exists) stay blocked.
        const hasJob = await timedEnv.DB.prepare(
          "SELECT id FROM console_jobs WHERE panel_link_id = ? AND type = 'mob_x_link'"
        ).bind(saved.id).first();
        if (!hasJob) {
          const reopened = await timedEnv.DB.prepare(
            "UPDATE panel_links SET status = 'pending', updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') " +
            "WHERE id = ? AND status = 'rejected'"
          ).bind(saved.id).run();
          if (reopened?.meta?.changes) saved.status = "pending";
        }
      }
      if (saved.status !== "pending") {
        await setState(timedEnv, "mode", "idle");
        await updateMobMain(ui, "این پست قبلاً ثبت شده است؛ وضعیت فعلی: " + esc(saved.status) + ".", HOME_KB);
        return;
      }
      await setStates(timedEnv, [["panel_mob_pending_link_id", String(saved.id)], ["mode", "idle"]]);
      const confirmKb = kb([[["✅ تأیید", "p_mob_confirm"], ["✖️ لغو", "p_mob_cancel"]]]);
      await sendMobTemp(timedEnv, ev.chat, "این لینک است؟\n" + saved.url, confirmKb);
      await updateMobMain(ui, "لینک دریافت شد؛ تأیید را در پیام جداگانه انجام بده.", HOME_KB);
      return;
    }
    await send(timedEnv, ev.chat, "برای شروع «منو» را بفرست.", HOME_KB);
  } catch (e) {
    if (e instanceof StalePanelEvent) return;
    const mobClick = ev.kind === "click" && (
      /^p_mob_(?:publish|edit|nocap|reject|manualconfirm)_job_[A-Fa-f0-9]{8,80}$/.test(String(ev.button || "")) ||
      ["p_mob_confirm", "p_mob_cancel", "p_mob_edit_cancel"].includes(String(ev.button || ""))
    );
    if (ev.kind === "text" && mobTextMode === "waiting_link") {
      try { await editMobTemp(timedEnv, ev.chat, "ثبت لینک انجام نشد؛ لینک رسمی پست را دوباره بفرست.", CANCEL_KB); } catch {}
      return;
    }
    if (ev.kind === "text" && mobTextMode === "waiting_mob_caption_edit") {
      try { await editMobTemp(timedEnv, ev.chat, "ثبت کپشن انجام نشد؛ متن سادهٔ کپشن را دوباره بفرست.", kb([[["لغو ویرایش", "p_mob_edit_cancel"]]])); } catch {}
      return;
    }
    if (ev.kind === "text" && mobTextMode === "mob_input_processing") {
      try { await updateMobMain({ env: timedEnv, chat: ev.chat }, "❌ پردازش ورودی Mob X ناموفق شد؛ وضعیت را تازه‌سازی کن. خطا: " + esc(e && e.code || "unknown"), HOME_KB); } catch {}
      return;
    }
    if (ev.kind === "click" && (isMainPanelScreenClick(ev.button) || mobClick)) {
      const currentMid = await getState(timedEnv, "panel_mid", "");
      // Settings writes must not be repeated blindly after a partial failure; refresh the settings screen instead.
      const retryId = isSettingsId(ev.button) ? "p_set" : ev.button;
      const errorText = "❌ نمایش صفحه انجام نشد. خطا: " + esc(e && e.code || "unknown") + "\nمی‌توانی دوباره تلاش کنی.";
      const errorKb = kb([[["🔄 تلاش مجدد", retryId], ["🏠 منوی اصلی", "p_home"]]]);
      if (currentMid) {
        try {
          await edit(timedEnv, ev.chat, currentMid, errorText, errorKb);
          return;
        } catch { /* use the ordinary response only if both edit and its delivery fallback failed */ }
      }
    }
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
