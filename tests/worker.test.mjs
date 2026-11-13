import assert from "node:assert/strict";
import "./tiktok_console.test.mjs";
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const dataModuleUrl = source => "data:text/javascript;base64," + Buffer.from(source).toString("base64");
const screensSource = fs.readFileSync(path.join(root, "src/panel_screens.js"), "utf8");
const screensModuleUrl = dataModuleUrl(screensSource);
const settingsSource = fs.readFileSync(path.join(root, "src/panel_settings.js"), "utf8");
const settingsModuleUrl = dataModuleUrl(settingsSource);
const mobXCaptionSource = fs.readFileSync(path.join(root, "src/mob_x_caption.js"), "utf8");
const mobXCaptionModuleUrl = dataModuleUrl(mobXCaptionSource);
const mobXCaption = await import(mobXCaptionModuleUrl);
const consoleRetrySource = fs.readFileSync(path.join(root, "src/console_retry.js"), "utf8");
const consoleRetryModuleUrl = dataModuleUrl(consoleRetrySource);
const consoleFailedMediaSource = fs.readFileSync(path.join(root, "src/console_failed_media.js"), "utf8");
const consoleFailedMediaModuleUrl = dataModuleUrl(consoleFailedMediaSource);
const failedMedia = await import(consoleFailedMediaModuleUrl);
const consoleTikTokSource = fs.readFileSync(path.join(root, "src/console_tiktok.js"), "utf8");
const consoleTikTokModuleUrl = dataModuleUrl(consoleTikTokSource);
const retryScreensSource = fs.readFileSync(path.join(root, "src/panel_retry_screens.js"), "utf8")
  .replace('from "./console_retry.js";', 'from "' + consoleRetryModuleUrl + '";');
const retryScreensModuleUrl = dataModuleUrl(retryScreensSource);
const screensExtSource = fs.readFileSync(path.join(root, "src/panel_screens_ext.js"), "utf8")
  .replace('from "./panel_screens.js";', 'from "' + screensModuleUrl + '";')
  .replace('from "./panel_retry_screens.js";', 'from "' + retryScreensModuleUrl + '";');
const screensExtModuleUrl = dataModuleUrl(screensExtSource);
const panelSource = fs.readFileSync(path.join(root, "src/owner_panel.js"), "utf8")
  .replace('from "./panel_screens.js";', 'from "' + screensModuleUrl + '";')
  .replace('from "./panel_screens_ext.js";', 'from "' + screensExtModuleUrl + '";')
  .replace('from "./panel_settings.js";', 'from "' + settingsModuleUrl + '";')
  .replace('from "./mob_x_caption.js";', 'from "' + mobXCaptionModuleUrl + '";');
const panelModuleUrl = dataModuleUrl(panelSource);
const importedPanel = await import(panelModuleUrl);
const screens = await import(screensModuleUrl);
const consoleApiSource = fs.readFileSync(path.join(root, "src/console_api.js"), "utf8")
  .replace('from "./console_retry.js";', 'from "' + consoleRetryModuleUrl + '";')
  .replace('from "./console_failed_media.js";', 'from "' + consoleFailedMediaModuleUrl + '";')
  .replace('from "./console_tiktok.js";', 'from "' + consoleTikTokModuleUrl + '";')
  .replace('from "./mob_x_caption.js";', 'from "' + mobXCaptionModuleUrl + '";');
const consoleApiModuleUrl = dataModuleUrl(consoleApiSource);
const originalSource = fs.readFileSync(path.join(root, "src/index.js"), "utf8");
const workerImport = 'import { dispatchMobXWorkflow, handleOwnerPanel } from "./owner_panel.js";';
const cronImport = 'import { getLastWorkflowCronResult, runWorkflowCron } from "./workflow_cron.js";';
const consoleApiImport = 'import { handleConsoleApi, corsPreflight, withCors } from "./console_api.js";';
assert.ok(originalSource.includes(workerImport), "Worker must import owner panel handler and shared dispatcher");
assert.ok(originalSource.includes(cronImport), "Worker must import cron dispatcher");
assert.ok(originalSource.includes(consoleApiImport), "Worker must import console API handler");
const cronSourceForWorker = fs.readFileSync(path.join(root, "src/workflow_cron.js"), "utf8");
const cronModuleUrlForWorker = dataModuleUrl(cronSourceForWorker);
const source = originalSource
  .replace(workerImport, `const { dispatchMobXWorkflow, handleOwnerPanel } = await import("${panelModuleUrl}");`)
  .replace(cronImport, `const { getLastWorkflowCronResult, runWorkflowCron } = await import("${cronModuleUrlForWorker}");`)
  .replace(consoleApiImport, `const { handleConsoleApi, corsPreflight, withCors } = await import("${consoleApiModuleUrl}");`);

// Test-only baseline Worker restores direct getState reads while keeping all other code identical.
const getStateStart = panelSource.indexOf("const getState = async (env, k, d) => {");
const getStateEnd = panelSource.indexOf("\nasync function nextPanelSeq", getStateStart);
assert.ok(getStateStart >= 0 && getStateEnd > getStateStart, "memoized getState section must be identifiable");
const uncachedPanelSource = panelSource.slice(0, getStateStart) +
  'const getState = async (env, k, d) => (await env.DB.prepare("SELECT value FROM panel_state WHERE key=?").bind(k).first())?.value ?? d;' +
  panelSource.slice(getStateEnd + 1);
const uncachedPanelModuleUrl = dataModuleUrl(uncachedPanelSource);
const uncachedSource = source.replace(panelModuleUrl, uncachedPanelModuleUrl);
assert.notEqual(uncachedSource, source, "reference Worker must use uncached panel source");
const uncachedModuleUrl = "data:text/javascript;base64," + Buffer.from(uncachedSource).toString("base64");
const uncachedWorker = (await import(uncachedModuleUrl)).default;

const moduleUrl = "data:text/javascript;base64," + Buffer.from(source).toString("base64");
const imported = await import(moduleUrl);
const worker = imported.default;
const panel = importedPanel;
const panelSettings = await import(settingsModuleUrl);
{
  const queried = [];
  const env = {
    DB: {
      prepare(sql) {
        queried.push(sql);
        return {
          async first() {
            return {
              version: 9,
              rules_json: JSON.stringify({
                daily_success_cap: 4,
                quiet_hours: { start: "02:15", end: "06:45", enabled: true },
              }),
            };
          },
        };
      },
    },
  };
  const screen = await panelSettings.settingsScreen(env);
  assert.ok(screen.text.includes("تیک‌تاک: بدون سقف"));
  assert.ok(screen.text.includes("Mob X: معاف از سقف و ساعت سکوت"));
  assert.ok(screen.text.includes("02:15") && screen.text.includes("06:45"));
  assert.ok(!screen.text.includes("سقف روزانه از 4") && !screen.text.includes("سقف روزانه از ۴"));
  assert.ok(!screen.text.includes("سقف ارسال موفق امروز"));
  assert.ok(!JSON.stringify(screen.rows).includes("p_set_cap_4"));
  assert.deepEqual(queried, ["SELECT version, rules_json FROM console_rules WHERE id = 1"]);
}

class FakeStatement {
  constructor(db, sql) {
    this.db = db;
    this.sql = sql;
    this.values = [];
  }
  bind(...values) {
    this.values = values;
    return this;
  }
  async first() {
    if (this.sql.includes("COUNT(*) AS n FROM failed_media WHERE source <> 'tiktok'")) {
      return { n: [...this.db.failedMedia.values()].filter(row => row.source !== "tiktok").length };
    }
    if (this.sql.includes("FROM failed_media WHERE id = ?")) {
      const row = this.db.failedMedia.get(this.values[0]);
      return row ? { ...row } : null;
    }
    if (this.sql.includes("INSERT INTO ai_provider_calls") && this.sql.includes("'cloudflare', 'translate_official'")) {
      const [callId,usageDay,runId,estimate,startedAt,unknownDay,totalDay,totalEstimate,totalLimit,bucketDay,kind,bucketEstimate,bucketLimit]=this.values;
      const rows=[...this.db.aiProviderCalls.values()].filter(x=>x.usage_day===totalDay&&x.role==="translate_official");
      const used=rows.reduce((n,x)=>n+Number(x.total_tokens||0),0);
      const bucket=rows.filter(x=>(String(x.run_id||"").startsWith("mobsub_")?"subtitle":"caption")===kind).reduce((n,x)=>n+Number(x.total_tokens||0),0);
      if(rows.some(x=>x.usage_day===unknownDay&&x.total_tokens==null)||used+totalEstimate>totalLimit||bucket+bucketEstimate>bucketLimit||this.db.aiProviderCalls.has(callId))return null;
      this.db.aiProviderCalls.set(callId,{call_id:callId,usage_day:usageDay,provider:"cloudflare",role:"translate_official",run_id:runId,status:"reserved",total_tokens:estimate,started_at:startedAt,error_code:null,finished_at:null});
      return {call_id:callId};
    }
    if (this.sql.includes("FROM owner_console_state WHERE id = 1")) {
      return this.db.ownerConsoleState ? { ...this.db.ownerConsoleState } : null;
    }
    if (this.sql.startsWith("INSERT INTO owner_console_updates")) {
      const [updateId, chatHash, command, createdAt, updatedAt, dayStart, dailyLimit] = this.values;
      if (this.db.ownerConsoleUpdates.has(updateId)) return null;
      const used = [...this.db.ownerConsoleUpdates.values()]
        .filter(row => row.created_at >= dayStart).length;
      if (used >= dailyLimit) return null;
      const row = {
        update_id: updateId, chat_hash: chatHash, command, status: "claimed",
        result_code: null, created_at: createdAt, updated_at: updatedAt,
      };
      this.db.ownerConsoleUpdates.set(updateId, row);
      return { update_id: updateId };
    }
    if (this.sql.includes("FROM owner_console_updates WHERE update_id = ?")) {
      const row = this.db.ownerConsoleUpdates.get(this.values[0]);
      return row ? { ...row } : null;
    }
    if (this.sql.includes("SELECT COUNT(*) AS total FROM owner_console_updates WHERE created_at >= ?")) {
      const dayStart = this.values[0];
      return {
        total: [...this.db.ownerConsoleUpdates.values()]
          .filter(row => row.created_at >= dayStart).length,
      };
    }
    if (this.sql.includes("SELECT COUNT(*) AS total FROM automation_queue WHERE status = 'sent' AND sent_at >= ?")) {
      const dayStart = this.values[0];
      return {
        total: [...this.db.automationQueue.values()]
          .filter(row => row.status === "sent" && row.sent_at && row.sent_at >= dayStart).length,
      };
    }
    if (this.sql.includes("SELECT COUNT(*) AS count FROM items WHERE status = 'sent' AND sent_at >= ?")) {
      const dayStart = this.values[0];
      return { count: [...this.db.items.values()].filter(row =>
        row.status === "sent" && row.sent_at && row.sent_at >= dayStart).length };
    }
    if (this.sql.includes("FROM console_jobs j") && this.sql.includes("j.status = 'approved'")) {
      const requestedJobId = this.sql.includes("AND j.id = ?") ? this.values[0] : null;
      const pair = [...this.db.consoleJobs.values()]
        .filter(job => job.type === "mob_x_link" && job.status === "approved" &&
          (requestedJobId === null || job.id === requestedJobId))
        .map(job => ({ job, link: this.db.panelLinks.get(Number(job.panel_link_id)) }))
        .find(value => value.link && value.link.status === "approved");
      return pair ? {
        id: pair.job.id, panel_link_id: pair.job.panel_link_id, result_json: pair.job.result_json,
      } : null;
    }
    if (this.sql.includes("FROM owner_digest_state WHERE id = 1")) {
      return this.db.ownerDigestState ? { ...this.db.ownerDigestState } : null;
    }
    if (this.sql.startsWith("UPDATE owner_digest_state SET edit_lock_token")) {
      const [lockToken, lockUntil, updatedAt, windowId, now] = this.values;
      const state = this.db.ownerDigestState;
      if (!state || state.window_id !== windowId ||
          (state.edit_lock_token && state.edit_lock_until_epoch > now)) return null;
      state.edit_lock_token = lockToken;
      state.edit_lock_until_epoch = lockUntil;
      state.updated_at = updatedAt;
      return { ...state };
    }
    if (this.sql.startsWith("INSERT INTO ai_usage_daily")) {
      const [day] = this.values;
      const current = this.db.aiUsageDaily.get(day) || 0;
      this.db.aiUsageDaily.set(day, current + 1);
      return { request_count: current + 1 };
    }
    if (this.sql.includes("SELECT 1 AS ok")) return { ok: 1 };
    if (this.sql.includes("FROM agent_config")) {
      return { enabled: 0, started_at: null, updated_at: null };
    }
    if (this.sql.includes("FROM runs WHERE id = ?")) {
      return this.db.runs.get(this.values[0]) || null;
    }
    if (this.sql.includes("FROM items WHERE id = ?")) {
      return this.db.items.get(this.values[0]) || null;
    }
    if (this.sql.includes("FROM sources WHERE platform = ? AND source_key = ?")) {
      const [platform, sourceKey] = this.values;
      const row = [...this.db.sources.values()].find(item => item.platform === platform && item.source_key === sourceKey);
      return row ? { id: row.id } : null;
    }
    if (this.sql.includes("FROM automation_queue WHERE id = ?")) {
      return this.db.automationQueue.get(this.values[0]) || null;
    }
    if (this.sql.includes("FROM console_owners WHERE chat_id = ?")) {
      const row = [...this.db.consoleOwners.values()].find(item => item.chat_id === this.values[0]);
      if (!row || (this.sql.includes("AND enabled = 1") && Number(row.enabled) !== 1)) return null;
      return { ...row };
    }
    if (this.sql.includes("FROM console_owners WHERE id = ?")) {
      const row = this.db.consoleOwners.get(Number(this.values[0]));
      return row ? { ...row } : null;
    }
    if (this.sql.includes("SELECT COUNT(*) AS total FROM console_owners WHERE role = 'owner' AND enabled = 1")) {
      return { total: [...this.db.consoleOwners.values()].filter(row => row.role === "owner" && Number(row.enabled) === 1).length };
    }
    if (this.sql.includes("FROM console_rules WHERE id = 1")) {
      return this.db.consoleRules ? { ...this.db.consoleRules } : null;
    }
    if (this.sql.includes("FROM console_channels WHERE rubika_channel_id = ?")) {
      const row = [...this.db.consoleChannels.values()].find(item => item.rubika_channel_id === this.values[0]);
      return row ? { ...row } : null;
    }
    if (this.sql.includes("FROM console_channels WHERE id = ?")) {
      const row = this.db.consoleChannels.get(Number(this.values[0]));
      return row ? { ...row } : null;
    }
    if (this.sql.includes("FROM console_owner_invites WHERE code_hash = ?")) {
      const row = [...this.db.consoleInvites.values()].find(item => item.code_hash === this.values[0] && item.used_at == null && item.expires_at > (this.values[1] || ""));
      return row ? { ...row } : null;
    }
    if (this.sql.includes("JOIN panel_links p ON p.id = j.panel_link_id")) {
      const requestedLinkId = this.sql.includes("AND j.panel_link_id = ?") ? Number(this.values[0]) : null;
      const pair = [...this.db.consoleJobs.values()]
        .filter(job => job.type === "mob_x_link" && ["confirmed", "queued"].includes(job.status) &&
          (requestedLinkId === null || Number(job.panel_link_id) === requestedLinkId))
        .sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)))
        .map(job => ({ job, link: this.db.panelLinks.get(Number(job.panel_link_id)) }))
        .find(value => value.link && ["confirmed", "queued"].includes(value.link.status));
      return pair ? { id: pair.job.id, panel_link_id: pair.job.panel_link_id, url: pair.link.url, external_id: pair.link.external_id } : null;
    }
    if (this.sql.includes("SELECT COUNT(*) AS c FROM panel_links WHERE status NOT IN")) {
      const active = new Set(["confirmed", "queued", "processing", "ready_for_review", "approved", "sending"]);
      return { c: [...this.db.panelLinks.values()].filter(row => !active.has(row.status)).length };
    }
    if (this.sql.includes("SELECT COUNT(*) AS c FROM panel_links")) return { c: this.db.panelLinks.size };
    if (this.sql.includes("FROM panel_links WHERE external_id = ?")) {
      const row = [...this.db.panelLinks.values()].find(item => item.external_id === String(this.values[0]));
      return row ? { ...row } : null;
    }
    if (this.sql.includes("FROM panel_links WHERE id = ?")) {
      const row = this.db.panelLinks.get(Number(this.values[0]));
      return row ? { ...row } : null;
    }
    if (this.sql.includes("FROM console_jobs WHERE panel_link_id = ?")) {
      const row = [...this.db.consoleJobs.values()].find(item => Number(item.panel_link_id) === Number(this.values[0]));
      return row ? { ...row } : null;
    }
    if (this.sql.includes("FROM console_jobs WHERE id = ?")) {
      const row = this.db.consoleJobs.get(this.values[0]);
      return row ? { ...row } : null;
    }
    return null;
  }
  async all() {
    if (this.sql.includes("FROM failed_media WHERE source <> 'tiktok'") && this.sql.includes("ORDER BY created_at DESC")) {
      const [limit, offset] = this.values;
      const rows = [...this.db.failedMedia.values()]
        .filter(row => row.source !== "tiktok")
        .sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")))
        .slice(Number(offset || 0), Number(offset || 0) + Number(limit || 50));
      return { results: rows.map(row => ({ ...row })) };
    }
    if (this.sql.includes("FROM failed_media WHERE source <> 'tiktok' AND status IN")) {
      const limit = Number(this.values[0] || 0);
      const rows = [...this.db.failedMedia.values()]
        .filter(row => row.source !== "tiktok" && ["stored", "delete_failed", "delete_requested"].includes(row.status))
        .sort((a, b) => String(a.updated_at).localeCompare(String(b.updated_at)) || String(a.id).localeCompare(String(b.id)))
        .slice(0, limit);
      return { results: rows.map(({ id, storage_key, status, updated_at }) => ({ id, storage_key, status, updated_at })) };
    }
    if (this.sql.includes("FROM failed_media WHERE status IN")) {
      const limit = Number(this.values[0] || 0);
      const rows = [...this.db.failedMedia.values()]
        .filter(row => row.source !== "tiktok" && ["stored", "delete_failed", "delete_requested"].includes(row.status))
        .sort((a, b) => String(a.updated_at).localeCompare(String(b.updated_at)) || String(a.id).localeCompare(String(b.id)))
        .slice(0, limit);
      return { results: rows.map(({ id, storage_key, status, updated_at }) => ({ id, storage_key, status, updated_at })) };
    }
    if (this.sql.includes("FROM failed_media WHERE id IN")) {
      const ids = new Set(this.values);
      return { results: [...this.db.failedMedia.values()].filter(row => ids.has(row.id) && row.source !== "tiktok")
        .sort((a, b) => String(a.id).localeCompare(String(b.id)))
        .map(({ id, storage_key, status, updated_at }) => ({ id, storage_key, status, updated_at })) };
    }
    if (this.sql.includes("FROM failed_media WHERE status = 'delete_requested'")) {
      const rows = [...this.db.failedMedia.values()].filter(row => row.status === "delete_requested")
        .sort((a, b) => String(a.updated_at).localeCompare(String(b.updated_at)) || String(a.id).localeCompare(String(b.id)))
        .slice(0, 50).map(({ id, storage_key }) => ({ id, storage_key }));
      return { results: rows };
    }
    if (this.sql.includes("FROM owner_digest_events WHERE window_id = ? GROUP BY category")) {
      const windowId = this.values[0];
      const totals = new Map();
      for (const row of this.db.ownerDigestEvents) {
        if (row.window_id === windowId) totals.set(row.category, (totals.get(row.category) || 0) + 1);
      }
      return { meta: { size_after: this.db.sizeAfter }, results: [...totals.entries()].map(([category, total]) => ({ category, total })) };
    }
    if (this.sql.includes("FROM owner_digest_events WHERE window_id = ? ORDER BY id DESC LIMIT 100")) {
      const windowId = this.values[0];
      return { results: this.db.ownerDigestEvents
        .filter(row => row.window_id === windowId)
        .sort((a, b) => b.id - a.id)
        .slice(0, 32)
        .map(row => ({ ...row })) };
    }
    if (this.sql.includes("FROM automation_queue GROUP BY status")) {
      const totals = new Map();
      for (const row of this.db.automationQueue.values()) {
        totals.set(row.status, (totals.get(row.status) || 0) + 1);
      }
      return { results: [...totals.entries()].map(([status, total]) => ({ status, total })) };
    }
    if (this.sql.includes("FROM automation_queue WHERE status = ?")) {
      const status = this.values[0];
      const limit = this.values[1];
      const offset = this.values[2];
      return { results: [...this.db.automationQueue.values()].filter(row => row.status === status).slice(offset, offset + limit) };
    }
    if (this.sql.includes("GROUP BY status")) return { results: [] };
    if (this.sql.includes("FROM runs ORDER BY")) return { results: [] };
    if (this.sql.includes("review_state = 'approved'")) {
      this.db.queueLimit = this.values[0];
      return { results: [...this.db.items.values()].filter(x => x.review_state === "approved" && x.status === "ready").slice(0, this.values[0]) };
    }
    if (
      (this.sql.includes("FROM items WHERE platform = ?") ||
       this.sql.includes("FROM items WHERE platform IN")) &&
      (this.sql.includes("SELECT id, platform, external_id, media_sha256") ||
       this.sql.includes("SELECT id, platform, external_id, canonical_url"))
    ) {
      this.db.seenQuery = { sql: this.sql, values: this.values };
      const all = [...this.db.items.values()].map(item => ({
        id: item.id, platform: item.platform, external_id: item.external_id,
        canonical_url: item.canonical_url, source_key: item.source_key,
        title: item.title, caption: item.caption, published_at: item.published_at,
        media_type: item.media_type, validation_json: item.validation_json,
        original_caption: item.original_caption, persian_translation: item.persian_translation,
        media_sha256: item.media_sha256, status: item.status,
        sent_at: item.sent_at || null, review_state: item.review_state,
      }));
      const filtered = this.sql.includes("WHERE platform = ?")
        ? all.filter(item => item.platform === this.values[0])
        : all;
      const limit = this.sql.includes("WHERE platform = ?") ? this.values[1] : this.values[0];
      const offset = this.sql.includes("WHERE platform = ?") ? this.values[2] : this.values[1];
      return { results: filtered.slice(offset, offset + limit) };
    }
    if (this.sql.includes("FROM console_owners ORDER BY id")) {
      return { results: [...this.db.consoleOwners.values()].sort((a, b) => a.id - b.id).map(row => ({ ...row })) };
    }
    if (this.sql.includes("FROM console_channels ORDER BY id")) {
      return { results: [...this.db.consoleChannels.values()].sort((a, b) => a.id - b.id).map(row => ({ ...row })) };
    }
    if (this.sql.includes("FROM panel_links ORDER BY id DESC")) {
      return { results: [...this.db.panelLinks.values()].sort((a, b) => b.id - a.id).slice(0, this.values.at(-1) || 100).map(row => ({ ...row })) };
    }
    if (this.sql.includes("FROM console_jobs") && this.sql.includes("ORDER BY created_at DESC LIMIT ?")) {
      let rows = [...this.db.consoleJobs.values()];
      let valueIndex = 0;
      if (this.sql.includes("type = ?")) {
        const type = this.values[valueIndex++];
        rows = rows.filter(row => row.type === type);
      }
      if (this.sql.includes("status = ?")) {
        const status = this.values[valueIndex++];
        rows = rows.filter(row => row.status === status);
      }
      const limit = this.values[valueIndex];
      return { results: rows.sort((a, b) => String(b.created_at).localeCompare(String(a.created_at))).slice(0, limit).map(row => ({ ...row })) };
    }
    return { results: [] };
  }
  async run() {
    const sql = this.sql;
    const v = this.values;
    if (sql.startsWith("UPDATE failed_media SET status = 'delete_requested'")) {
      if ((v.length - 1) % 8 !== 0) throw new Error("fake_failed_media_snapshot_bind_mismatch");
      const count = (v.length - 1) / 8;
      const first = v.slice(0, count * 4);
      const second = v.slice(count * 4, count * 8);
      if (JSON.stringify(first) !== JSON.stringify(second)) throw new Error("fake_failed_media_snapshot_copy_mismatch");
      const rows = [];
      for (let i = 0; i < count; i += 1) {
        const [id, status, updatedAt, storageKey] = first.slice(i * 4, i * 4 + 4);
        const row = this.db.failedMedia.get(id);
        if (!row || row.source === "tiktok" || row.status !== status || String(row.updated_at ?? "") !== updatedAt || row.storage_key !== storageKey) {
          return { meta: { changes: 0 } };
        }
        rows.push(row);
      }
      const changedAt = new Date(Date.now() + 1000).toISOString();
      for (const row of rows) Object.assign(row, { status: "delete_requested", updated_at: changedAt });
      return { meta: { changes: rows.length } };
    }
    if (sql.startsWith("DELETE FROM failed_media WHERE id = ? AND status = 'delete_requested'")) {
      const row = this.db.failedMedia.get(v[0]);
      if (!row || row.status !== "delete_requested" || row.storage_key !== v[1]) return { meta: { changes: 0 } };
      this.db.failedMedia.delete(v[0]);
      return { meta: { changes: 1 } };
    }
    if (sql.startsWith("UPDATE failed_media SET status = 'delete_failed'")) {
      const row = this.db.failedMedia.get(v[0]);
      if (!row || row.status !== "delete_requested" || row.storage_key !== v[1]) return { meta: { changes: 0 } };
      Object.assign(row, { status: "delete_failed", updated_at: new Date().toISOString() });
      return { meta: { changes: 1 } };
    }
    if (sql.startsWith("UPDATE console_jobs SET params_json = ?")) {
      const [paramsJson, updatedAt, jobId, expectedParams] = v;
      const job = this.db.consoleJobs.get(jobId);
      if (!job) return { meta: { changes: 0 } };
      if (expectedParams !== undefined && String(job.params_json ?? "{}") !== String(expectedParams)) {
        return { meta: { changes: 0 } };
      }
      const statusMatch = /status IN \(([^)]+)\)/.exec(sql);
      if (statusMatch) {
        const allowed = statusMatch[1].split(",").map(value => value.trim().replace(/^'|'$/g, ""));
        if (!allowed.includes(job.status)) return { meta: { changes: 0 } };
      }
      const markerMatch = /json_extract\(params_json, '\$\.([a-z_]+)'\)/.exec(sql);
      if (markerMatch) {
        let existingParams = {};
        try { existingParams = JSON.parse(job.params_json || "{}"); } catch {}
        if (["dispatching", "dispatched"].includes(existingParams[markerMatch[1]])) {
          return { meta: { changes: 0 } };
        }
      }
      Object.assign(job, { params_json: paramsJson, updated_at: updatedAt });
      return { meta: { changes: 1 } };
    }
    if(sql.startsWith("UPDATE ai_provider_calls SET status=?")&&sql.includes("provider='cloudflare'")){
      const [status,errorCode,finishedAt,callId]=v;const row=this.db.aiProviderCalls.get(callId);
      if(!row||row.status!=="reserved")return {meta:{changes:0}};Object.assign(row,{status,error_code:errorCode,finished_at:finishedAt});return {meta:{changes:1}};
    }
    if (sql.startsWith("INSERT INTO sources (id, platform, source_key, display_name, enabled) VALUES")) {
      const [id, platform, source_key, display_name, enabled] = v;
      if ([...this.db.sources.values()].some(row => row.platform === platform && row.source_key === source_key)) {
        return { meta: { changes: 0 } };
      }
      this.db.sources.set(id, {
        id, platform, source_key, display_name, enabled: Number(enabled),
        created_at: new Date().toISOString(), last_checked_at: null, last_seen_published_at: null,
      });
      return { meta: { changes: 1 } };
    }
    if (sql.startsWith("INSERT INTO console_owners (chat_id, label, role, enabled, claimed_at, last_seen_at) VALUES")) {
      if ([...this.db.consoleOwners.values()].some(row => row.chat_id === v[0])) return { meta: { changes: 0 } };
      const id = this.db.nextConsoleOwnerId++;
      const row = { id, chat_id: v[0], label: v[1], role: v[2], enabled: Number(v[3]), claimed_at: v[4] || new Date().toISOString(), last_seen_at: v[5] || null, created_at: new Date().toISOString(), updated_at: new Date().toISOString() };
      this.db.consoleOwners.set(id, row);
      return { meta: { changes: 1, last_row_id: id } };
    }
    if (sql.startsWith("INSERT OR IGNORE INTO console_owners")) {
      if ([...this.db.consoleOwners.values()].some(row => row.chat_id === v[0])) return { meta: { changes: 0 } };
      const id = this.db.nextConsoleOwnerId++;
      this.db.consoleOwners.set(id, { id, chat_id: v[0], label: "مالک اصلی", role: "owner", enabled: 1, claimed_at: new Date().toISOString(), last_seen_at: new Date().toISOString(), created_at: new Date().toISOString(), updated_at: new Date().toISOString() });
      return { meta: { changes: 1, last_row_id: id } };
    }
    if (sql.startsWith("UPDATE console_owners SET")) {
      const id = Number(v[v.length - 1]);
      const row = this.db.consoleOwners.get(id);
      if (!row) return { meta: { changes: 0 } };
      const setPart = sql.slice(sql.indexOf(" SET ") + 5, sql.indexOf(" WHERE id = ?"));
      let vi = 0;
      for (const assignment of setPart.split(",")) {
        const key = assignment.split("=")[0].trim();
        if (assignment.includes("?")) row[key] = v[vi++];
        else if (key === "updated_at") row.updated_at = new Date().toISOString();
      }
      return { meta: { changes: 1 } };
    }
    if (sql.startsWith("DELETE FROM console_owners WHERE id = ?")) {
      return { meta: { changes: this.db.consoleOwners.delete(Number(v[0])) ? 1 : 0 } };
    }
    if (sql.startsWith("INSERT INTO console_owner_invites")) {
      const id = this.db.nextConsoleInviteId++;
      const row = { id, code_hash: v[0], expires_at: v[1], created_at: new Date().toISOString(), used_at: null, used_by: null };
      this.db.consoleInvites.set(id, row);
      return { meta: { changes: 1, last_row_id: id } };
    }
    if (sql.startsWith("UPDATE console_owner_invites SET used_at")) {
      const row = this.db.consoleInvites.get(Number(v[2]));
      if (!row || row.used_at || row.expires_at <= v[3]) return { meta: { changes: 0 } };
      Object.assign(row, { used_at: v[0], used_by: v[1] });
      return { meta: { changes: 1 } };
    }
    if (sql.startsWith("UPDATE console_rules SET version = version + 1")) {
      const [rulesJson, updatedAt, baseVersion] = v;
      if (!this.db.consoleRules || Number(this.db.consoleRules.version) !== Number(baseVersion)) return { meta: { changes: 0 } };
      Object.assign(this.db.consoleRules, { version: Number(baseVersion) + 1, rules_json: rulesJson, updated_at: updatedAt });
      return { meta: { changes: 1 } };
    }
    if (sql.startsWith("INSERT INTO console_channels")) {
      if ([...this.db.consoleChannels.values()].some(row => row.rubika_channel_id === v[1])) return { meta: { changes: 0 } };
      const id = this.db.nextConsoleChannelId++;
      const row = { id, label: v[0], rubika_channel_id: v[1], role: v[2], enabled: Number(v[3]), created_at: new Date().toISOString(), updated_at: new Date().toISOString() };
      this.db.consoleChannels.set(id, row);
      return { meta: { changes: 1, last_row_id: id } };
    }
    if (sql.startsWith("DELETE FROM console_channels WHERE id = ?")) {
      return { meta: { changes: this.db.consoleChannels.delete(Number(v[0])) ? 1 : 0 } };
    }
    if (sql.startsWith("DELETE FROM panel_links WHERE status NOT IN")) {
      const active = new Set(["confirmed", "queued", "processing", "ready_for_review", "approved", "sending"]);
      const before = this.db.panelLinks.size;
      for (const [id, row] of this.db.panelLinks) if (!active.has(row.status)) this.db.panelLinks.delete(id);
      const changes = before - this.db.panelLinks.size;
      this.db.lastChanges = changes;
      return { meta: { changes } };
    }
    if (sql.startsWith("INSERT INTO panel_links (url, external_id, status)")) {
      const [url, externalId] = v;
      if ([...this.db.panelLinks.values()].some(row => row.external_id === String(externalId) || row.url === url)) return { meta: { changes: 0 } };
      const id = this.db.nextPanelLinkId++;
      this.db.panelLinks.set(id, { id, url, external_id: String(externalId), status: "pending", created_at: new Date().toISOString(), confirmed_at: null, updated_at: new Date().toISOString() });
      return { meta: { changes: 1, last_row_id: id } };
    }
    if (sql.startsWith("UPDATE panel_links SET status = 'rejected'")) {
      if (sql.includes("status = 'ready_for_review'")) {
        const [updatedAt, linkId] = v;
        const row = this.db.panelLinks.get(Number(linkId));
        if (Number(this.db.lastChanges || 0) <= 0 || !row || row.status !== "ready_for_review") return { meta: { changes: 0 } };
        Object.assign(row, { status: "rejected", updated_at: updatedAt });
        return { meta: { changes: 1 } };
      }
      const row = this.db.panelLinks.get(Number(v[0]));
      if (!row || row.status !== "pending") return { meta: { changes: 0 } };
      Object.assign(row, { status: "rejected", updated_at: new Date().toISOString() });
      return { meta: { changes: 1 } };
    }
    if (sql.startsWith("UPDATE console_jobs SET result_json = ?")) {
      const [resultJson, updatedAt, jobId, expectedResultJson, panelLinkId, expectedLinkId, itemId] = v;
      const job = this.db.consoleJobs.get(jobId);
      const link = this.db.panelLinks.get(Number(panelLinkId));
      const item = this.db.items.get(itemId);
      if (!job || job.type !== "mob_x_link" || job.status !== "ready_for_review" ||
          job.result_json !== expectedResultJson || Number(job.panel_link_id) !== Number(panelLinkId) ||
          Number(expectedLinkId) !== Number(panelLinkId) || !link || link.status !== "ready_for_review" ||
          !item || item.platform !== "mob_official" || item.status !== "ready" || item.review_state !== "pending") return { meta: { changes: 0 } };
      Object.assign(job, { result_json: resultJson, updated_at: updatedAt });
      return { meta: { changes: 1 } };
    }
    if (sql.startsWith("UPDATE console_jobs SET status = 'rejected'")) {
      const [updatedAt, jobId, panelLinkId, expectedLinkId, itemId] = v;
      const job = this.db.consoleJobs.get(jobId);
      const link = this.db.panelLinks.get(Number(panelLinkId));
      const item = this.db.items.get(itemId);
      if (!job || job.type !== "mob_x_link" || job.status !== "ready_for_review" ||
          Number(job.panel_link_id) !== Number(panelLinkId) || Number(expectedLinkId) !== Number(panelLinkId) ||
          !link || link.status !== "ready_for_review" || !item || item.platform !== "mob_official" ||
          item.status !== "ready" || item.review_state !== "pending") return { meta: { changes: 0 } };
      Object.assign(job, { status: "rejected", progress: 100, error: null, updated_at: updatedAt });
      return { meta: { changes: 1 } };
    }
    if (sql.startsWith("UPDATE items SET caption = ?")) {
      const [caption, itemId] = v;
      const item = this.db.items.get(itemId);
      if (sql.includes("changes() > 0") && Number(this.db.lastChanges || 0) <= 0) return { meta: { changes: 0 } };
      if (!item || item.platform !== "mob_official" || item.status !== "ready" || item.review_state !== "pending") return { meta: { changes: 0 } };
      item.caption = caption;
      if (sql.includes("original_caption = ''")) { item.original_caption = ""; item.persian_translation = ""; }
      return { meta: { changes: 1 } };
    }
    if (sql.startsWith("UPDATE items SET review_state = 'rejected'")) {
      const [itemId] = v;
      const item = this.db.items.get(itemId);
      if (Number(this.db.lastChanges || 0) <= 0 || !item || item.platform !== "mob_official" ||
          item.status !== "ready" || item.review_state !== "pending") return { meta: { changes: 0 } };
      item.review_state = "rejected";
      return { meta: { changes: 1 } };
    }
    if (sql.startsWith("INSERT INTO brain_events (item_id, level, code, details_json) SELECT ?")) {
      if (Number(this.db.lastChanges || 0) <= 0) return { meta: { changes: 0 } };
      const [itemId, code, details] = v;
      this.db.events.push({ item_id: itemId, level: "info", code, details_json: details, created_at: new Date().toISOString(), sql });
      return { meta: { changes: 1 } };
    }

    if (sql.startsWith("UPDATE console_jobs SET status = 'processing'")) {
      const [updatedAt, jobId, panelLinkId, existsLinkId] = v;
      const job = this.db.consoleJobs.get(jobId);
      const link = this.db.panelLinks.get(Number(panelLinkId));
      if (!job || job.type !== "mob_x_link" || Number(job.panel_link_id) !== Number(panelLinkId) ||
          !["confirmed", "queued"].includes(job.status) || Number(existsLinkId) !== Number(panelLinkId) ||
          !link || !["confirmed", "queued"].includes(link.status)) return { meta: { changes: 0 } };
      Object.assign(job, { status: "processing", progress: 10, error: null, updated_at: updatedAt });
      return { meta: { changes: 1 } };
    }
    if (sql.startsWith("UPDATE panel_links SET status = 'processing'")) {
      const [updatedAt, linkId] = v;
      const link = this.db.panelLinks.get(Number(linkId));
      if (Number(this.db.lastChanges || 0) <= 0 || !link || !["confirmed", "queued"].includes(link.status)) return { meta: { changes: 0 } };
      Object.assign(link, { status: "processing", updated_at: updatedAt });
      return { meta: { changes: 1 } };
    }
    if (sql.startsWith("UPDATE console_jobs SET status = ?, progress = ?, result_json = COALESCE")) {
      const [status, progress, resultJson, error, updatedAt, jobId] = v;
      const job = this.db.consoleJobs.get(jobId);
      const transitionAllowed = status === "failed"
        ? ["processing", "ready_for_review"].includes(job?.status)
        : job?.status === "processing";
      if (!job || job.type !== "mob_x_link" || !transitionAllowed) return { meta: { changes: 0 } };
      Object.assign(job, { status, progress, result_json: resultJson == null ? job.result_json : resultJson, error, updated_at: updatedAt });
      return { meta: { changes: 1 } };
    }
    if (sql.startsWith("UPDATE panel_links SET status = ?, updated_at = ? WHERE id = (SELECT panel_link_id")) {
      const [status, updatedAt, jobId, requiredStatus] = v;
      if (Number(this.db.lastChanges || 0) <= 0) return { meta: { changes: 0 } };
      const job = this.db.consoleJobs.get(jobId);
      if (!job || job.status !== requiredStatus || job.panel_link_id == null) return { meta: { changes: 0 } };
      const link = this.db.panelLinks.get(Number(job.panel_link_id));
      if (!link) return { meta: { changes: 0 } };
      Object.assign(link, { status, updated_at: updatedAt });
      return { meta: { changes: 1 } };
    }
    if (sql.startsWith("UPDATE panel_links SET status = 'confirmed'")) {
      const row = this.db.panelLinks.get(Number(v[0]));
      if (!row || !["pending", "confirmed"].includes(row.status)) return { meta: { changes: 0 } };
      Object.assign(row, { status: "confirmed", confirmed_at: row.confirmed_at || new Date().toISOString(), updated_at: new Date().toISOString() });
      return { meta: { changes: 1 } };
    }
    if (sql.startsWith("INSERT INTO console_jobs") && sql.includes("SELECT ?, 'mob_x_link'")) {
      const [id, paramsJson, panelLinkId] = v;
      const existing = [...this.db.consoleJobs.values()].find(row => Number(row.panel_link_id) === Number(panelLinkId));
      const link = this.db.panelLinks.get(Number(panelLinkId));
      if (existing || !link || link.status !== "confirmed") return { meta: { changes: 0 } };
      this.db.consoleJobs.set(id, { id, type: "mob_x_link", status: "confirmed", progress: 0, params_json: paramsJson, result_json: null, error: null, panel_link_id: Number(panelLinkId), created_at: new Date().toISOString(), updated_at: new Date().toISOString() });
      return { meta: { changes: 1 } };
    }
    if (sql.startsWith("INSERT INTO console_jobs")) {
      const row = { id: v[0], type: v[1], status: "queued", progress: 0, params_json: v[2], result_json: null, error: null, created_at: new Date().toISOString(), updated_at: new Date().toISOString() };
      this.db.consoleJobs.set(row.id, row);
      return { meta: { changes: 1 } };
    }
    if (sql.startsWith("INSERT INTO brain_events") && sql.includes("SELECT NULL")) {
      if (Number(this.db.lastChanges || 0) <= 0) return { meta: { changes: 0 } };
      let code = null, details = null;
      if (sql.includes("'console_owner_claim'")) { code = "console_owner_claim"; details = v[0]; }
      else { code = v[0]; details = v[1]; }
      this.db.events.push({ item_id: null, level: "info", code, details_json: details, created_at: new Date().toISOString(), sql });
      return { meta: { changes: 1 } };
    }
    if (sql.startsWith("UPDATE owner_console_state SET offset_id")) {
      if (!this.db.ownerConsoleState) return { meta: { changes: 0 } };
      Object.assign(this.db.ownerConsoleState, { offset_id: v[0], updated_at: v[1] });
      return { meta: { changes: 1 } };
    }
    if (sql.startsWith("UPDATE owner_console_updates SET status = ?")) {
      const row = this.db.ownerConsoleUpdates.get(v[3]);
      if (!row || row.status !== "claimed") return { meta: { changes: 0 } };
      Object.assign(row, { status: v[0], result_code: v[1], updated_at: v[2] });
      return { meta: { changes: 1 } };
    }
    if (sql.startsWith("UPDATE automation_queue SET status = 'uncertain'")) {
      this.db.queueStateRepairCalls += 1;
      return { meta: { changes: 0 } };
    }
    if (sql.startsWith("INSERT OR IGNORE INTO owner_digest_state")) {
      if (!this.db.ownerDigestState) {
        this.db.ownerDigestState = {
          id: 1, window_id: v[0], window_started_at: v[1], window_ends_at: v[2],
          window_started_epoch: v[3], window_ends_epoch: v[4],
          message_id: null, message_body: null, message_creation_uncertain: 0,
          edit_lock_token: null, edit_lock_until_epoch: null, updated_at: v[1],
        };
        return { meta: { changes: 1 } };
      }
      return { meta: { changes: 0 } };
    }
    if (sql.startsWith("UPDATE owner_digest_state SET window_id")) {
      const state = this.db.ownerDigestState;
      const [windowId, start, end, startEpoch, endEpoch, updated, now] = v;
      if (!state || state.window_ends_epoch > now) return { meta: { changes: 0 } };
      Object.assign(state, {
        window_id: windowId, window_started_at: start, window_ends_at: end,
        window_started_epoch: startEpoch, window_ends_epoch: endEpoch,
        message_id: null, message_body: null, message_creation_uncertain: 0,
        edit_lock_token: null, edit_lock_until_epoch: null, updated_at: updated,
      });
      return { meta: { changes: 1 } };
    }
    if (sql.startsWith("INSERT INTO owner_digest_events")) {
      const [windowId, category, source, title, detail] = v;
      this.db.ownerDigestEvents.push({
        id: this.db.ownerDigestEvents.length + 1,
        window_id: windowId, category, source, title, detail,
        occurred_at: new Date().toISOString(),
      });
      return { meta: { changes: 1, last_row_id: this.db.ownerDigestEvents.length } };
    }
    if (sql.startsWith("UPDATE owner_digest_state SET message_id")) {
      const state = this.db.ownerDigestState;
      const [messageId, messageBody, creationUncertain, updated, windowId, lockToken] = v;
      if (!state || state.window_id !== windowId || state.edit_lock_token !== lockToken) {
        return { meta: { changes: 0 } };
      }
      Object.assign(state, {
        message_id: messageId, message_body: messageBody,
        message_creation_uncertain: creationUncertain,
        edit_lock_token: null, edit_lock_until_epoch: null, updated_at: updated,
      });
      return { meta: { changes: 1 } };
    }
    if (sql.startsWith("INSERT INTO automation_queue")) {
      const row = {
        id: v[0], platform: v[1], external_id: v[2], canonical_url: v[3],
        source_key: v[4], title: v[5], caption: v[6], published_at: v[7],
        media_type: v[8], freshness_verified: v[9], method: v[10],
        metadata_json: v[11], status: "queued", attempt_count: 0,
        last_error_code: null, created_at: "2026-10-14T00:00:00Z",
        updated_at: "2026-10-14T00:00:00Z", sent_at: null,
      };
      const existing = [...this.db.automationQueue.values()].some(
        item => item.platform === row.platform && item.external_id === row.external_id
      );
      if (!existing) this.db.automationQueue.set(row.id, row);
      return { meta: { changes: existing ? 0 : 1 } };
    }
    if (sql.startsWith("UPDATE automation_queue SET status = ?")) {
      const row = this.db.automationQueue.get(v[4]);
      if (!row) return { meta: { changes: 0 } };
      if (v[1] === "processing" && row.status === "queued") row.attempt_count += 1;
      row.status = v[0];
      row.last_error_code = v[2];
      if (v[3] === "sent") row.sent_at = "2026-10-14T00:00:00Z";
      row.updated_at = "2026-10-14T00:00:00Z";
      return { meta: { changes: 1 } };
    }
    if (sql.startsWith("INSERT INTO runs")) {
      this.db.runs.set(v[0], { id: v[0], trigger_type: v[1], status: "running" });
      return { meta: { changes: 1 } };
    }
    if (sql.startsWith("UPDATE runs SET")) {
      const run = this.db.runs.get(v[7]);
      if (!run || run.status !== "running") return { meta: { changes: 0 } };
      run.status = v[0];
      return { meta: { changes: 1 } };
    }
    if (sql.startsWith("INSERT INTO items") || sql.startsWith("INSERT OR IGNORE INTO items")) {
      const item = {
        id: v[0], platform: v[1], source_key: v[2], external_id: v[3],
        canonical_url: v[4], title: v[5], caption: v[6], published_at: v[7],
        status: v[8], media_sha256: v[9], media_bytes: v[10], media_mime: v[11],
        media_type: v[12], source_tier: v[13], storage_key: v[14],
        validation_json: v[15], moderation_json: v[16], review_state: v[17],
        original_caption: v[18], persian_translation: v[19],
      };
      const previous = this.db.items.get(item.id);
      if (sql.startsWith("INSERT OR IGNORE INTO items") && previous) {
        return { meta: { changes: 0 } };
      }
      if (previous && ["sent", "uncertain", "sending"].includes(previous.status) &&
          !["sent", "uncertain", "sending"].includes(item.status)) {
        item.status = previous.status;
      }
      this.db.items.set(item.id, { ...previous, ...item });
      return { meta: { changes: 1 } };
    }
    if (sql.startsWith("INSERT INTO delivery_attempts")) {
      this.db.attempts.push({ item_id: v[0], attempt_number: v[1], status: v[2] });
      return { meta: { changes: 1 } };
    }
    if (sql.startsWith("UPDATE items SET storage_key")) {
      const item = this.db.items.get(v[1]);
      if (!item) return { meta: { changes: 0 } };
      item.storage_key = v[0];
      return { meta: { changes: 1 } };
    }
    if (sql.startsWith("UPDATE items SET attempt_count")) {
      const item = this.db.items.get(v[7]);
      if (!item) return { meta: { changes: 0 } };
      item.status = v[6];
      item.attempt_count = v[0];
      return { meta: { changes: 1 } };
    }
    if (sql.startsWith("INSERT INTO brain_events")) {
      this.db.events.push({
        values: v, sql, level: v[0], code: v[1],
        details_json: v[2], created_at: new Date().toISOString(),
      });
      return { meta: { changes: 1 } };
    }
    return { meta: { changes: 1 } };
  }
}

class FakeDB {
  constructor() {
    this.runs = new Map();
    this.items = new Map();
    this.sources = new Map();
    this.automationQueue = new Map();
    this.attempts = [];
    this.events = [];
    this.queueLimit = null;
    this.seenQuery = null;
    this.aiUsageDaily = new Map();
    this.aiProviderCalls = new Map();
    this.ownerDigestState = null;
    this.ownerDigestEvents = [];
    this.sizeAfter = 12 * 1024 * 1024;
    this.ownerConsoleState = { id: 1, offset_id: null, updated_at: null };
    this.ownerConsoleUpdates = new Map();
    this.queueStateRepairCalls = 0;
    this.consoleOwners = new Map();
    this.nextConsoleOwnerId = 1;
    this.consoleInvites = new Map();
    this.nextConsoleInviteId = 1;
    this.consoleRules = {
      id: 1, version: 1,
      rules_json: JSON.stringify({ publishing_enabled: false, test_channel_only: true, daily_success_cap: 4, daily_edit_count: 6, mob_posts_per_run: 1, mob_since_cutoff: "", freshness_hours: 24, ai_provider_order: ["groq", "openrouter"], ai_daily_limit_groq: 200, ai_daily_limit_openrouter: 50, translate_enabled: true, quiet_hours: { start: "00:30", end: "07:00" }, caption_max_chars: 900, digest_window_utc: "07:05" }),
      updated_at: new Date().toISOString(),
    };
    this.consoleChannels = new Map();
    this.nextConsoleChannelId = 1;
    this.consoleJobs = new Map();
    this.failedMedia = new Map();
    this.panelLinks = new Map();
    this.nextPanelLinkId = 1;
    this.lastChanges = 0;
  }
  prepare(sql) {
    return new FakeStatement(this, sql);
  }
  async batch(statements) {
    const results = [];
    for (const statement of statements) {
      const result = await statement.run();
      this.lastChanges = Number(result?.meta?.changes || 0);
      results.push(result);
    }
    return results;
  }
}

const TOKEN = "test-agent-api-token-with-at-least-thirty-two-characters";
const OWNER_TOKEN = "test-owner-web-token-with-at-least-thirty-two-characters";
const env = () => ({ DB: new FakeDB(), AGENT_API_TOKEN: TOKEN, OWNER_WEB_TOKEN: OWNER_TOKEN });
const OWNER_EXEMPT_POSTS = new Set([
  "/mob-x/claim", "/mob-x/publish-claim",
  "/failed-media/record", "/failed-media/list", "/failed-media/pending-delete", "/failed-media/delete-result", "/d1/query",
]);
const needsOwnerToken = (url, method) => {
  if (!url.startsWith("/api/console/") || ["GET", "HEAD", "OPTIONS"].includes(method)) return false;
  const sub = String(url).split("?")[0].replace(/\/+$/, "").slice("/api/console".length);
  if (OWNER_EXEMPT_POSTS.has(sub)) return false;
  if (/^\/mob-x\/jobs\/[A-Za-z0-9_-]{1,100}\/(?:transition|preview|delivery)$/.test(sub)) return false;
  return true;
};
const request = (url, { method = "GET", body, token, headers = {} } = {}) => {
  const bearer = token === undefined ? (needsOwnerToken(url, method) ? OWNER_TOKEN : TOKEN) : token;
  return new Request("https://worker.test" + url, {
    method,
    headers: {
      ...(body === undefined ? {} : { "content-type": "application/json" }),
      ...(bearer === null ? {} : { authorization: "Bearer " + bearer }),
      ...headers,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
};
const read = async response => ({ status: response.status, body: await response.json() });

{
  const response = await read(await worker.fetch(request("/health"), env()));
  assert.equal(response.status, 200);
  assert.equal(response.body.service, "rubika-agent");
  assert.equal(response.body.publishing, false);
}

// Console API is auth-gated, CORS-enabled, and leaves existing status/brain routes intact.
{
  const state = env();
  let response = await read(await worker.fetch(
    request("/api/console/events", { token: null }), state
  ));
  assert.equal(response.status, 401);
  assert.equal(response.body.error, "unauthorized");

  const preflight = await worker.fetch(request("/api/console/events", { method: "OPTIONS", token: null }), state);
  assert.equal(preflight.status, 204);
  assert.equal(preflight.headers.get("access-control-allow-origin"), "*");

  response = await read(await worker.fetch(request("/api/status"), state));
  assert.equal(response.status, 200);
  assert.equal(response.body.publishing, false);
  assert.equal(response.body.capabilities.publishing, false, "publishing capability must default to false");

  state.PUBLISHING_ENABLED = "true";
  response = await read(await worker.fetch(request("/api/status"), state));
  assert.equal(response.status, 200);
  assert.equal(response.body.capabilities.publishing, true, "only the exact string true enables the capability");
  assert.equal(response.body.publishing, false, "other publishing guard remains unchanged");
  delete state.PUBLISHING_ENABLED;

  response = await read(await worker.fetch(request("/api/brain/owner-console/state"), state));
  assert.equal(response.status, 200);
  assert.equal(response.body.ok, true);

  response = await read(await worker.fetch(request("/api/console/not-a-route"), state));
  assert.equal(response.status, 404);
  assert.equal(response.body.error, "not_found");
}

// The web report is GET-only, AGENT-token readable, and exposes only sanitized 24h digest fields.
{
  const state = env();
  delete state.OWNER_WEB_TOKEN;
  state.DB.ownerDigestState = {
    id: 1, window_id: "window-private-id", window_started_at: "2026-11-02T07:05:00.000Z",
    window_ends_at: "2026-11-03T07:05:00.000Z", updated_at: "2026-11-03T07:05:02.000Z",
  };
  state.DB.ownerDigestEvents = [
    { id: 1, window_id: "window-private-id", occurred_at: "2026-11-03T06:00:00.000Z", category: "success", source: "x", title: "پست ثبت شد **موفق**", detail: "private details must not leak" },
    { id: 2, window_id: "window-private-id", occurred_at: "2026-11-03T06:10:00.000Z", category: "ai_error", source: "openrouter", title: "translation failed", detail: "secret api key must not leak" },
    { id: 3, window_id: "window-private-id", occurred_at: "2026-11-03T06:20:00.000Z", category: "unknown_private_category", source: "internal-secret", title: "private event", detail: "must be omitted" },
    { id: 4, window_id: "other-window", occurred_at: "2026-11-02T01:00:00.000Z", category: "error", source: "worker", title: "outside active window", detail: "omitted" },
  ];
  const digestSnapshot = JSON.stringify(state.DB.ownerDigestState);
  const eventsSnapshot = JSON.stringify(state.DB.ownerDigestEvents);
  const writesBefore = state.DB.events.length;

  const response = await read(await worker.fetch(request("/api/console/digest"), state));
  assert.equal(response.status, 200);
  assert.deepEqual(response.body.window, {
    started_at: "2026-11-02T07:05:00.000Z",
    ends_at: "2026-11-03T07:05:00.000Z",
  });
  assert.deepEqual(response.body.counts, [
    { category: "success", count: 1 },
    { category: "ai_error", count: 1 },
  ]);
  assert.equal(response.body.events.length, 2);
  assert.deepEqual(Object.keys(response.body.events[0]).sort(), ["category", "occurred_at", "source", "title"]);
  assert.equal(response.body.events[0].title, "پست ثبت شد موفق");
  assert.equal(JSON.stringify(response.body).includes("private details"), false);
  assert.equal(JSON.stringify(response.body).includes("secret api key"), false);
  assert.equal(JSON.stringify(response.body).includes("window-private-id"), false);
  assert.equal(JSON.stringify(response.body).includes("unknown_private_category"), false);
  assert.equal(JSON.stringify(state.DB.ownerDigestState), digestSnapshot);
  assert.equal(JSON.stringify(state.DB.ownerDigestEvents), eventsSnapshot);
  assert.equal(state.DB.events.length, writesBefore, "GET digest must not write/audit D1");
  
  const denied = await read(await worker.fetch(request("/api/console/digest", { token: OWNER_TOKEN }), state));
  assert.equal(denied.status, 401, "OWNER_WEB_TOKEN is not a viewer/read token");
  const anonymous = await read(await worker.fetch(request("/api/console/digest", { token: null }), state));
  assert.equal(anonymous.status, 401);

  const methodDenied = await read(await worker.fetch(request("/api/console/digest", {
    method: "POST", body: {}, token: TOKEN,
  }), state));
  assert.equal(methodDenied.status, 403, "non-GET report requests remain owner-gated");
  assert.equal(JSON.stringify(state.DB.ownerDigestState), digestSnapshot);
  assert.equal(JSON.stringify(state.DB.ownerDigestEvents), eventsSnapshot);
}
{
  const state = env();
  const response = await read(await worker.fetch(request("/api/console/digest"), state));
  assert.equal(response.status, 200);
  assert.deepEqual(response.body, { ok: true, window: null, counts: [], events: [] });
}

// Owner-only web mutations require OWNER_WEB_TOKEN; the AGENT token remains viewer/runner-only.
{
  const state = env();
  const path = "/api/console/links/clear";
  const body = { confirm: true };
  for (const [label, token] of [
    ["missing bearer", null],
    ["viewer bearer", TOKEN],
    ["wrong owner bearer", "wrong-owner-web-token-with-at-least-thirty-two-characters"],
  ]) {
    const response = await read(await worker.fetch(request(path, { method: "POST", body, token }), state));
    assert.equal(response.status, 403, label);
    assert.equal(response.body.error, "owner_required", label);
  }

  const missingSecret = env();
  delete missingSecret.OWNER_WEB_TOKEN;
  const closed = await read(await worker.fetch(
    request(path, { method: "POST", body, token: OWNER_TOKEN }), missingSecret
  ));
  assert.equal(closed.status, 403, "missing OWNER_WEB_TOKEN must fail closed");
  assert.equal(closed.body.error, "owner_required");
  assert.equal(missingSecret.DB.panelLinks.size, 0, "denied owner requests must not mutate D1 state");

  const sharedSecret = env();
  sharedSecret.OWNER_WEB_TOKEN = TOKEN;
  const shared = await read(await worker.fetch(
    request(path, { method: "POST", body, token: TOKEN }), sharedSecret
  ));
  assert.equal(shared.status, 403, "OWNER_WEB_TOKEN must not reuse the viewer/Actions secret");

  const viewerRead = await read(await worker.fetch(
    request("/api/console/events", { token: TOKEN }), state
  ));
  assert.equal(viewerRead.status, 200, "AGENT_API_TOKEN keeps read-only viewer access");

  const actionClaim = await read(await worker.fetch(
    request("/api/console/mob-x/claim", { method: "POST", body: {}, token: TOKEN }), state
  ));
  assert.equal(actionClaim.status, 200, "GitHub Actions runner contract remains on AGENT_API_TOKEN");
  assert.equal(actionClaim.body.claimed, false);
}

// POST /sources accepts X but stores it under the schema-supported mob_official platform.
{
  const state = env();
  const body = { platform: "x", source_key: "mob-entertainment", display_name: "Mob Entertainment" };
  let response = await read(await worker.fetch(request("/api/console/sources", {
    method: "POST", body,
  }), state));
  assert.equal(response.status, 201);
  assert.equal(response.body.id, "mob_official:mob-entertainment");
  assert.equal(state.DB.sources.get("mob_official:mob-entertainment").platform, "mob_official");

  response = await read(await worker.fetch(request("/api/console/sources", {
    method: "POST", body,
  }), state));
  assert.equal(response.status, 409);
  assert.equal(response.body.error, "source_already_exists");

  for (const platform of ["youtube", "instagram", "mob_official"]) {
    response = await read(await worker.fetch(request("/api/console/sources", {
      method: "POST",
      body: { platform, source_key: "legacy-source", display_name: "Retired source" },
    }), state));
    assert.equal(response.status, 400, platform);
    assert.equal(response.body.error, "invalid_platform", platform);
  }
  assert.equal(state.DB.sources.size, 1);
}

// Console API rejects destructive SQL, unauthorized review approval, uncertain requeue without confirmation,
// and link clearing without explicit confirmation using the fake D1 database.
{
  const state = env();
  let response = await read(await worker.fetch(request("/api/console/d1/query", {
    method: "POST", body: { sql: "DELETE FROM panel_links" },
  }), state));
  assert.equal(response.status, 400);

  response = await read(await worker.fetch(request("/api/console/d1/query", {
    method: "POST", body: { sql: "SELECT 1; SELECT 2" },
  }), state));
  assert.equal(response.status, 400);

  response = await read(await worker.fetch(request("/api/console/items/tiktok:fake-item", {
    method: "PATCH", body: { review_state: "approved" },
  }), state));
  assert.equal(response.status, 400);
  assert.equal(response.body.error, "review_state_not_allowed_from_console");

  const uncertainId = "mob_official:12345678901234567";
  state.DB.automationQueue.set(uncertainId, { id: uncertainId, status: "uncertain" });
  response = await read(await worker.fetch(request("/api/console/queue/" + uncertainId + "/action", {
    method: "POST", body: { action: "requeue" },
  }), state));
  assert.equal(response.status, 409);
  assert.equal(response.body.error, "uncertain_requires_confirm_uncertain_true");
  assert.equal(state.DB.automationQueue.get(uncertainId).status, "uncertain");

  const prepared = [];
  const originalPrepare = state.DB.prepare.bind(state.DB);
  state.DB.prepare = sql => { prepared.push(sql); return originalPrepare(sql); };
  response = await read(await worker.fetch(request("/api/console/links/clear", {
    method: "POST", body: {},
  }), state));
  assert.equal(response.status, 400);
  assert.equal(response.body.error, "confirm_true_required");
  assert.equal(prepared.some(sql => sql.startsWith("DELETE FROM panel_links")), false);

  const activeStatusesForClear = ["confirmed", "queued", "processing", "ready_for_review", "approved", "sending"];
  const seededStatuses = [...activeStatusesForClear, "pending", "rejected", "sent"];
  state.DB.panelLinks = new Map(seededStatuses.map((status, index) => [index + 1, {
    id: index + 1, status, url: `https://x.com/mobgamesstudios/status/${2107878755079213476 + index}`,
    external_id: String(2107878755079213476 + index),
  }]));
  response = await read(await worker.fetch(request("/api/console/links/clear", {
    method: "POST", body: { confirm: true },
  }), state));
  assert.equal(response.status, 200);
  assert.deepEqual({ removed: response.body.removed, kept: response.body.kept }, { removed: 3, kept: 6 });
  assert.deepEqual([...state.DB.panelLinks.values()].map(row => row.status).sort(), activeStatusesForClear.slice().sort(),
    "clear must preserve all active statuses and delete inactive statuses");
}

{
  const state = env();
  let response = await read(await worker.fetch(
    request("/api/brain/owner-console/state", { token: null }), state
  ));
  assert.equal(response.status, 401);
  assert.equal(response.body.error, "unauthorized");

  response = await read(await worker.fetch(
    request("/api/brain/owner-console/state"), state
  ));
  assert.equal(response.status, 200);
  assert.equal(response.body.offset_id, null);

  response = await read(await worker.fetch(
    request("/api/brain/owner-console/offset", {
      method: "POST", body: { offset_id: "cursor-123" },
    }), state
  ));
  assert.equal(response.status, 200);
  response = await read(await worker.fetch(
    request("/api/brain/owner-console/state"), state
  ));
  assert.equal(response.body.offset_id, "cursor-123");
  assert.equal(response.body.ok, true);
}
{
  const state = env();
  const updateId = "a".repeat(64);
  const chatHash = "b".repeat(64);
  const payload = { update_id: updateId, chat_hash: chatHash, command: "status" };
  let response = await read(await worker.fetch(
    request("/api/brain/owner-console/claim", { method: "POST", body: payload }), state
  ));
  assert.equal(response.status, 201);
  assert.equal(response.body.claimed, true);

  response = await read(await worker.fetch(
    request("/api/brain/owner-console/claim", { method: "POST", body: payload }), state
  ));
  assert.equal(response.status, 200);
  assert.equal(response.body.claimed, false);
  assert.equal(response.body.duplicate, true);
  assert.equal(response.body.status, "claimed");

  response = await read(await worker.fetch(
    request("/api/brain/owner-console/claim", {
      method: "POST", body: { ...payload, command: "delete_everything" },
    }), state
  ));
  assert.equal(response.status, 400);
  assert.equal(response.body.error, "owner_console_command_invalid");

  response = await read(await worker.fetch(
    request("/api/brain/owner-console/finish", {
      method: "POST",
      body: { update_id: updateId, status: "answered", result_code: "status_ok" },
    }), state
  ));
  assert.equal(response.status, 200);
  assert.equal(response.body.status, "answered");

  response = await read(await worker.fetch(
    request("/api/brain/owner-console/finish", {
      method: "POST",
      body: { update_id: updateId, status: "answered", result_code: "status_ok" },
    }), state
  ));
  assert.equal(response.status, 409);
}
{
  const state = env();
  const created = new Date().toISOString();
  for (let i = 0; i < 80; i += 1) {
    const id = String(i).padStart(64, "0");
    state.DB.ownerConsoleUpdates.set(id, {
      update_id: id, chat_hash: "b".repeat(64), command: "status",
      status: "answered", result_code: "reply_sent", created_at: created, updated_at: created,
    });
  }
  const response = await read(await worker.fetch(
    request("/api/brain/owner-console/claim", {
      method: "POST",
      body: { update_id: "f".repeat(64), chat_hash: "b".repeat(64), command: "status" },
    }), state
  ));
  assert.equal(response.status, 200);
  assert.equal(response.body.claimed, false);
  assert.equal(response.body.rate_limited, true);
  assert.equal(state.DB.ownerConsoleUpdates.size, 80);
}
{
  const state = env();
  state.DB.automationQueue.set("tiktok:item-1", {
    id: "tiktok:item-1", status: "queued", sent_at: null,
  });
  state.DB.automationQueue.set("tiktok:item-2", {
    id: "tiktok:item-2", status: "processing", sent_at: null,
  });
  state.DB.automationQueue.set("tiktok:item-3", {
    id: "tiktok:item-3", status: "sent", sent_at: new Date().toISOString(),
  });
  const before = [...state.DB.automationQueue.values()].map(row => row.status);
  let response = await read(await worker.fetch(
    request("/api/brain/owner-console/queue-summary"), state
  ));
  assert.equal(response.status, 200);
  assert.equal(response.body.queue_counts.queued, 1);
  assert.equal(response.body.queue_counts.processing, 1);
  assert.equal(response.body.queue_counts.sent, 1);
  assert.equal(response.body.sent_today, 1);
  assert.equal(response.body.publishing, false);
  assert.deepEqual([...state.DB.automationQueue.values()].map(row => row.status), before);
  assert.equal(state.DB.queueStateRepairCalls, 0);

  response = await read(await worker.fetch(
    request("/api/brain/owner-console/queue-summary", { method: "POST", body: {} }), state
  ));
  assert.equal(response.status, 405);
}
{
  const response = await read(await worker.fetch(request("/api/brain/runs", { method: "POST", body: { id: "run-test-0001" }, token: null }), env()));
  assert.equal(response.status, 401);
  assert.equal(response.body.error, "unauthorized");
}
{
  const state = env();
  let response = await read(await worker.fetch(request("/api/brain/runs", {
    method: "POST", body: { id: "run-test-0001", trigger_type: "manual" },
  }), state));
  assert.equal(response.status, 201);
  assert.equal(response.body.status, "running");

  response = await read(await worker.fetch(request("/api/brain/runs", {
    method: "POST", body: { id: "run-test-0001", trigger_type: "manual" },
  }), state));
  assert.equal(response.status, 409);

  response = await read(await worker.fetch(request("/api/brain/runs/run-test-0001/finish", {
    method: "POST",
    body: { status: "ok", found_count: 10, eligible_count: 8, sent_count: 0, skipped_count: 2, failed_count: 0, summary: "Discovery complete." },
  }), state));
  assert.equal(response.status, 200);
}
{
  const state = env();
  const goodModeration = {
    is_poppy_playtime: null,
    is_edit: null,
    high_quality_edit: null,
    has_music: true,
  };
  const base = {
    platform: "tiktok", external_id: "7616247520657771796",
    canonical_url: "https://www.tiktok.com/@creator/video/7616247520657771796",
    source_key: "@creator", title: "Poppy Playtime edit", caption: "#PoppyPlaytime #edit", status: "ready",
    review_state: "approved", media_type: "video", source_tier: "trusted",
    media_sha256: "a".repeat(64), media_bytes: 1000000,
    validation: { ok: true, has_audio: true, width: 1080, height: 1080, mime_type: "video/mp4" },
    moderation: { ...goodModeration, music_probability: 0.97 },
  };
  let response = await read(await worker.fetch(request("/api/brain/items/upsert", {
    method: "POST", body: base,
  }), state));
  assert.equal(response.status, 200);
  assert.equal(response.body.item_id, "tiktok:7616247520657771796");

  for (const hasMusic of [false, null]) {
    const rejectedState = env();
    response = await read(await worker.fetch(request("/api/brain/items/upsert", {
      method: "POST",
      body: {
        ...base,
        external_id: "761624752065777179" + (hasMusic === false ? "7" : "8"),
        canonical_url: "https://www.tiktok.com/@creator/video/761624752065777179" + (hasMusic === false ? "7" : "8"),
        moderation: { is_poppy_playtime: null, is_edit: null, has_music: hasMusic },
      },
    }), rejectedState));
    assert.equal(response.status, 400);
    assert.equal(response.body.error, "edit_approval_requires_visual_content_and_music_checks");
  }

  const queue = await read(await worker.fetch(request("/api/brain/queue?limit=999"), state));
  assert.equal(queue.status, 200);
  assert.equal(queue.body.publishing, false);
  assert.equal(state.DB.queueLimit, 100);
}
{
  const response = await read(await worker.fetch(request("/api/brain/items/upsert", {
    method: "POST",
    body: {
      platform: "youtube", external_id: "vkVFgmgLo1s",
      canonical_url: "https://www.youtube.com/watch?v=vkVFgmgLo1s",
      source_key: "channel", status: "ready", review_state: "approved",
      media_type: "video", source_tier: "trusted",
      media_sha256: "d".repeat(64), media_bytes: 500000,
      validation: { ok: true },
      moderation: { has_music: true, is_poppy_playtime: true, is_edit: true },
    },
  }), env()));
  assert.equal(response.status, 400);
  assert.equal(response.body.error, "invalid_platform_or_external_id");
}
{
  const state = env();
  const approvedOfficial = {
    platform: "mob_official",
    external_id: "x-2107878755079213476",
    canonical_url: "https://x.com/mobgamesstudios/status/2107878755079213476",
    source_key: "x.com/mobgamesstudios",
    title: "Official Mob approval contract",
    status: "ready", review_state: "approved", media_type: "image", source_tier: "official",
    media_sha256: "e".repeat(64), media_bytes: 500000,
    validation: { ok: true },
  };
  const response = await read(await worker.fetch(request("/api/brain/items/upsert", {
    method: "POST", body: approvedOfficial,
  }), state));
  assert.equal(response.status, 200);
  assert.equal(response.body.item_id, "mob_official:x-2107878755079213476");
}
{
  const state = env();
  state.DB.items.set("tiktok:vkVFgmgLo1s", {
    id: "tiktok:vkVFgmgLo1s",
    platform: "tiktok",
    external_id: "vkVFgmgLo1s",
    media_sha256: "c".repeat(64),
    status: "sent",
    review_state: "approved",
  });
  const response = await read(await worker.fetch(request(
    "/api/brain/items/seen?limit=999&offset=-7&platform=tiktok"
  ), state));
  assert.equal(response.status, 200);
  assert.equal(response.body.items.length, 1);
  assert.equal(response.body.items[0].external_id, "vkVFgmgLo1s");
  assert.deepEqual(state.DB.seenQuery.values, ["tiktok", 500, 0]);
}

{
  const state = env();
  const discoveryMetadata = {
    mob_x_discovery: {
      version: 1, source_platform: "x", official_source: true,
      author: "@mobgamesstudios", pinned: false,
      pin_state: "known_not_pinned", pin_state_source: "test_fixture.syndication.is_pinned",
      published_at: "2026-11-05T01:00:00Z", media_type: "image",
      media_url: "https://pbs.twimg.com/media/example.jpg",
      original_caption: "Official Mob caption", discovery_source: "x-cli",
    },
  };
  const mobItem = {
    id: "mob_official:x-2111111111111111111",
    platform: "mob_official",
    external_id: "x-2111111111111111111",
    canonical_url: "https://x.com/mobgamesstudios/status/2111111111111111111",
    source_key: "x.com/mobgamesstudios",
    title: "Mob announcement",
    caption: "Official Mob caption",
    original_caption: "Official Mob caption",
    published_at: "2026-11-05T01:00:00Z",
    media_type: "image",
    validation_json: JSON.stringify(discoveryMetadata),
    status: "discovered",
    review_state: "pending",
  };
  state.DB.items.set(mobItem.id, mobItem);
  const response = await read(await worker.fetch(request(
    "/api/brain/items/seen?limit=50&offset=0&platform=mob_official"
  ), state));
  assert.equal(response.status, 200);
  assert.equal(response.body.items.length, 1);
  assert.equal(response.body.items[0].validation_json, JSON.stringify(discoveryMetadata));
  assert.equal(response.body.items[0].canonical_url, mobItem.canonical_url);
  assert.equal(response.body.items[0].original_caption, mobItem.original_caption);
  const result = await read(await worker.fetch(request("/api/brain/items/upsert", {
    method: "POST",
    body: {
      platform: "mob_official",
      external_id: mobItem.external_id,
      canonical_url: mobItem.canonical_url,
      source_key: mobItem.source_key,
      status: "discovered",
      review_state: "pending",
      media_type: "image",
      source_tier: "official",
      validation: discoveryMetadata,
    },
  }), state));
  assert.equal(result.status, 200);
  assert.equal(state.DB.items.get(mobItem.id).status, "discovered");
  assert.equal(state.DB.items.get(mobItem.id).caption, "Official Mob caption");
}
{
  const state = env();
  const previous = {
    id: "mob_official:x-2111111111111111112",
    platform: "mob_official",
    external_id: "x-2111111111111111112",
    status: "failed",
    review_state: "pending",
    caption: "Original failed caption",
    validation_json: '{"previous":true}',
  };
  state.DB.items.set(previous.id, previous);
  const response = await read(await worker.fetch(request("/api/brain/items/upsert", {
    method: "POST",
    body: {
      platform: "mob_official",
      external_id: previous.external_id,
      canonical_url: "https://x.com/mobgamesstudios/status/2111111111111111112",
      source_key: "x.com/mobgamesstudios",
      status: "discovered",
      review_state: "pending",
      media_type: "image",
      source_tier: "official",
    },
  }), state));
  assert.equal(response.status, 200);
  assert.equal(state.DB.items.get(previous.id).status, "failed");
  assert.equal(state.DB.items.get(previous.id).caption, "Original failed caption");
  assert.equal(state.DB.items.get(previous.id).validation_json, '{"previous":true}');
}

{
  const state = env();
  const invalid = {
    platform: "tiktok", external_id: "7616247520657771796",
    canonical_url: "https://www.tiktok.com/@creator/video/7616247520657771796?sig=secret",
    source_key: "@creator", status: "discovered",
  };
  const response = await read(await worker.fetch(request("/api/brain/items/upsert", {
    method: "POST", body: invalid,
  }), state));
  assert.equal(response.status, 400);
  assert.equal(response.body.error, "invalid_canonical_url");
}
for (const [label, canonicalUrl] of [
  ["YouTube", "https://www.youtube.com/watch?v=abcdefghijk"],
  ["Instagram", "https://www.instagram.com/p/ABC123/"],
]) {
  const state = env();
  const officialNonXPost = {
    platform: "mob_official",
    external_id: "legacy-video-id",
    canonical_url: canonicalUrl,
    source_key: "legacy-non-x",
    status: "discovered",
    review_state: "pending",
    media_type: "video",
    source_tier: "official",
    original_caption: "Official Mob caption",
    persian_translation: "کپشن رسمی",
  };
  const response = await read(await worker.fetch(request("/api/brain/items/upsert", {
    method: "POST", body: officialNonXPost,
  }), state));
  assert.equal(response.status, 400, label);
  assert.equal(response.body.error, "invalid_canonical_url", label);
  assert.equal(state.DB.items.size, 0, label);
}
{
  const state = env();
  const original = {
    id: "tiktok:7616247520657771711",
    platform: "tiktok",
    external_id: "7616247520657771711",
    status: "sending",
    review_state: "pending",
  };
  state.DB.items.set(original.id, original);
  const response = await read(await worker.fetch(request("/api/brain/items/upsert", {
    method: "POST",
    body: {
      platform: "tiktok",
      external_id: original.external_id,
      canonical_url: "https://www.tiktok.com/@creator/video/" + original.external_id,
      source_key: "@creator",
      title: "Retry must not overwrite an in-flight send",
      status: "ready",
      review_state: "pending",
      media_type: "video",
      source_tier: "trusted",
    },
  }), state));
  assert.equal(response.status, 200);
  assert.equal(state.DB.items.get(original.id).status, "sending");
}
{
  const state = env();
  const officialXPost = {
    platform: "mob_official",
    external_id: "x-2107878755079213476",
    canonical_url: "https://x.com/mobgamesstudios/status/2107878755079213476",
    source_key: "x.com/mobgamesstudios",
    title: "Official Mob X announcement",
    status: "discovered",
    review_state: "pending",
    media_type: "image",
    source_tier: "official",
    original_caption: "Official Mob caption",
    persian_translation: "کپشن رسمی",
  };
  let response = await read(await worker.fetch(request("/api/brain/items/upsert", {
    method: "POST", body: officialXPost,
  }), state));
  assert.equal(response.status, 200);
  assert.equal(response.body.item_id, "mob_official:x-2107878755079213476");
  assert.equal(state.DB.items.get("mob_official:x-2107878755079213476").canonical_url,
    "https://x.com/mobgamesstudios/status/2107878755079213476");

  const badUrls = [
    "https://x.com/fanaccount/status/2107878755079213476",
    "https://evil.x.com/mobgamesstudios/status/2107878755079213476",
    "https://x.com/mobgamesstudios/status/2107878755079213476?s=20",
    "https://x.com/mobgamesstudios/status/not-a-post-id",
  ];
  for (const [index, badUrl] of badUrls.entries()) {
    response = await read(await worker.fetch(request("/api/brain/items/upsert", {
      method: "POST",
      body: { ...officialXPost, external_id: "x-invalid-" + index, canonical_url: badUrl },
    }), state));
    assert.equal(response.status, 400, badUrl);
    assert.equal(response.body.error, "invalid_canonical_url", badUrl);
  }
}
{
  const state = env();
  const directSent = {
    platform: "youtube", external_id: "vkVFgmgLo1s",
    canonical_url: "https://www.youtube.com/watch?v=vkVFgmgLo1s",
    source_key: "channel", status: "sent", review_state: "pending",
  };
  const response = await read(await worker.fetch(request("/api/brain/items/upsert", {
    method: "POST", body: directSent,
  }), state));
  assert.equal(response.status, 400);
  assert.equal(response.body.error, "invalid_platform_or_external_id");
}
{
  const state = env();
  const unsupportedInstagram = {
    platform: "instagram", external_id: "AbCdEf12345",
    canonical_url: "https://www.instagram.com/reel/AbCdEf12345/",
    source_key: "@poppy", status: "discovered",
  };
  const response = await read(await worker.fetch(request("/api/brain/items/upsert", {
    method: "POST", body: unsupportedInstagram,
  }), state));
  assert.equal(response.status, 400);
  assert.equal(response.body.error, "invalid_platform_or_external_id");
}
{
  const state = env();
  const badApproval = {
    platform: "tiktok", external_id: "vkVFgmgLo1s",
    canonical_url: "https://www.tiktok.com/@creator/video/vkVFgmgLo1s",
    source_key: "channel", status: "ready", review_state: "approved",
    media_type: "video", source_tier: "unknown", media_sha256: "b".repeat(64),
    media_bytes: 500000, validation: { ok: true },
  };
  const response = await read(await worker.fetch(request("/api/brain/items/upsert", {
    method: "POST", body: badApproval,
  }), state));
  assert.equal(response.status, 400);
  assert.equal(response.body.error, "edit_approval_requires_visual_content_and_music_checks");
}
{
  const response = await read(await worker.fetch(request("/api/brain/events", {
    method: "POST", body: {
      code: "storage-test", details: {
        platform: "tiktok", result: "ok", password: "must-not-be-stored",
        "signed_url": "https://private.invalid/?token=example",
      },
    },
  }), env()));
  assert.equal(response.status, 200);
}
{
  const state = env();
  const valid = {
    platform: "tiktok", external_id: "7616247520657771796",
    canonical_url: "https://www.tiktok.com/@creator/video/7616247520657771796",
    source_key: "@creator", title: "Poppy Playtime edit",
    caption: "Public metadata only", media_type: "video",
    freshness_verified: true, method: "termux_phone",
  };
  const invalid = {
    platform: "youtube", external_id: "abcdefghijk",
    canonical_url: "https://www.youtube.com/watch?v=abcdefghijk&feature=share",
    source_key: "channel", title: "bad canonical url",
  };
  let response = await read(await worker.fetch(request("/api/brain/automation-queue", {
    method: "POST", body: { items: [valid, invalid] },
  }), state));
  assert.equal(response.status, 200);
  assert.equal(response.body.inserted_count, 1);
  assert.equal(response.body.invalid_count, 1);
  assert.equal(state.DB.automationQueue.size, 1);

  response = await read(await worker.fetch(request("/api/brain/automation-queue", {
    method: "POST", body: { items: [valid] },
  }), state));
  assert.equal(response.status, 200);
  assert.equal(response.body.inserted_count, 0);
  assert.equal(response.body.duplicate_count, 1);

  response = await read(await worker.fetch(request("/api/brain/automation-queue?status=queued&limit=100"), state));
  assert.equal(response.status, 200);
  assert.equal(response.body.items.length, 1);
  assert.equal(response.body.items[0].id, "tiktok:7616247520657771796");

  const regularRules = JSON.parse(state.DB.consoleRules.rules_json);
  regularRules.quiet_hours = { start: "00:00", end: "00:00", enabled: false };
  state.DB.consoleRules.rules_json = JSON.stringify(regularRules);
  response = await read(await worker.fetch(request("/api/brain/automation-queue/tiktok%3A7616247520657771796/state", {
    method: "POST", body: { status: "processing" },
  }), state));
  assert.equal(response.status, 200);
  assert.equal(state.DB.automationQueue.get("tiktok:7616247520657771796").attempt_count, 1);

  response = await read(await worker.fetch(request("/api/brain/automation-queue/tiktok%3A7616247520657771796/state", {
    method: "POST", body: { status: "sent" },
  }), state));
  assert.equal(response.status, 200);
  assert.equal(state.DB.automationQueue.get("tiktok:7616247520657771796").status, "sent");

  response = await read(await worker.fetch(request("/api/brain/automation-queue?summary=1"), state));
  assert.equal(response.status, 200);
  assert.equal(response.body.counts.sent, 1);
  assert.equal(response.body.counts.queued || 0, 0);
}
{
  const state = env();
  state.DB.consoleRules.rules_json = JSON.stringify({
    quiet_hours: { start: "00:00", end: "00:00" },
    daily_success_cap: 4,
  });
  const item = {
    platform: "tiktok", external_id: "7616247520657771999",
    canonical_url: "https://www.tiktok.com/@creator/video/7616247520657771999",
    source_key: "@creator", title: "Quiet-hours TikTok edit", caption: "Test caption",
    media_type: "video", freshness_verified: true, method: "termux_phone",
  };
  let response = await read(await worker.fetch(request("/api/brain/automation-queue", {
    method: "POST", body: { items: [item] },
  }), state));
  assert.equal(response.status, 200);
  const queueId = "tiktok:7616247520657771999";
  response = await read(await worker.fetch(request("/api/brain/automation-queue/" + encodeURIComponent(queueId) + "/state", {
    method: "POST", body: { status: "processing" },
  }), state));
  assert.equal(response.status, 200);
  assert.equal(state.DB.automationQueue.get(queueId).status, "processing");
  assert.equal(state.DB.automationQueue.get(queueId).attempt_count, 1);
}

// Mob X news is exempt from daily_success_cap and quiet_hours (owner decision 2026-10-30).
{
  const state = env();
  state.DB.consoleRules.rules_json = JSON.stringify({
    quiet_hours: { start: "00:00", end: "00:00" },
    daily_success_cap: 4,
  });
  const linkId = 7;
  const jobId = "job_abcdef1234567890";
  const itemId = "mob_official:2107878755079213476";
  state.DB.panelLinks.set(linkId, {
    id: linkId, url: "https://x.com/mobgamesstudios/status/2107878755079213476",
    external_id: "2107878755079213476", status: "approved",
  });
  state.DB.consoleJobs.set(jobId, {
    id: jobId, type: "mob_x_link", status: "approved", panel_link_id: linkId,
    result_json: JSON.stringify({ item_id: itemId, rubika_file_id: "mock-file-id", caption: "approved caption" }),
  });
  state.DB.items.set(itemId, {
    id: itemId, platform: "mob_official", status: "ready", review_state: "approved",
  });
  const sentAtToday = new Date().toISOString();
  for (let i = 0; i < 4; i += 1) {
    const sentItemId = `mob_official:already-sent-today-${i}`;
    state.DB.items.set(sentItemId, { id: sentItemId, platform: "mob_official", status: "sent", sent_at: sentAtToday });
  }
  const response = await read(await worker.fetch(request("/api/console/mob-x/publish-claim", {
    method: "POST", body: { job_id: jobId },
  }), state));
  assert.equal(response.status, 200);
  assert.equal(response.body.claimed, true);
  assert.equal(response.body.job.id, jobId);
  assert.equal(response.body.channel_policy, "test_channel_only");
}

{
  const response = await read(await worker.fetch(request("/api/brain/automation-queue", {
    method: "POST", body: { items: [] }, token: null,
  }), env()));
  assert.equal(response.status, 401);
}

function sampleAudioDataUrl() {
  const bytes = Buffer.concat([
    Buffer.from("ID3"),
    Buffer.alloc(1400, 7),
  ]);
  return "data:audio/mpeg;base64," + bytes.toString("base64");
}

function makeOmniAI(probability = 0.97) {
  return {
    run: async (model, input) => {
      assert.equal(model, "@cf/cloudflare/clef-omni");
      assert.equal(input.model, "clef-omni");
      assert.equal(input.audio.length, 1);
      assert.equal(input.audio[0].startsWith("data:audio/mpeg;base64,"), true);
      assert.equal("videos" in input, false);
      assert.deepEqual(Object.keys(input.questions), ["has_music"]);
      return {
        answers: { has_music: { noul: probability } },
        usage: { input_tokens: 9000 },
      };
    },
  };
}
{
  const state = env();
  state.AI = makeOmniAI();
  const response = await read(await worker.fetch(request("/api/brain/moderate-music", {
    method: "POST",
    body: { audio: sampleAudioDataUrl(), platform: "tiktok", title: "Poppy Playtime edit", caption: "#edit" },
  }), state));
  assert.equal(response.status, 200);
  assert.equal(response.body.model, "@cf/cloudflare/clef-omni");
  assert.equal(response.body.checks.has_music, true);
  assert.equal(response.body.probabilities.has_music, 0.97);
  assert.equal(response.body.diagnostic.has_music, "music_detected_above_threshold");
  assert.deepEqual(Object.keys(response.body.checks), ["has_music"]);
  assert.equal(response.body.human_review_required, true);
  assert.equal(response.body.auto_approved, false);
  assert.equal(response.body.publishing, false);
  assert.equal(state.DB.aiUsageDaily.size, 1);
}
{
  const state = env();
  state.AI = makeOmniAI(0.55);
  const response = await read(await worker.fetch(request("/api/brain/moderate-music", {
    method: "POST",
    body: { audio: sampleAudioDataUrl(), platform: "tiktok" },
  }), state));
  assert.equal(response.status, 200);
  assert.equal(response.body.checks.has_music, null);
  assert.equal(response.body.probabilities.has_music, 0.55);
  assert.equal(response.body.diagnostic.has_music, "music_probability_ambiguous");
}
{
  const state = env();
  state.AI = makeOmniAI();
  const response = await read(await worker.fetch(request("/api/brain/moderate-music", {
    method: "POST",
    body: { audio: "data:text/html;base64," + Buffer.from("x".repeat(1400)).toString("base64"), platform: "tiktok" },
  }), state));
  assert.equal(response.status, 400);
  assert.equal(response.body.error, "moderation_audio_format_invalid");
  assert.equal(state.DB.aiUsageDaily.size, 0);
}
{
  const state = env();
  state.AI = { run: async () => { throw new Error("secret provider body"); } };
  const response = await read(await worker.fetch(request("/api/brain/moderate-music", {
    method: "POST", body: { audio: sampleAudioDataUrl(), platform: "tiktok" },
  }), state));
  assert.equal(response.status, 502);
  assert.equal(response.body.error, "music_inference_failed");
  assert.equal(JSON.stringify(response.body).includes("secret provider body"), false);
  assert.equal(state.DB.aiUsageDaily.size, 1);
}
{
  const state = env();
  state.AI = makeOmniAI();
  // More than the former 10-request custom cap should now be allowed.
  for (let index = 0; index < 12; index++) {
    const response = await read(await worker.fetch(request("/api/brain/moderate-music", {
      method: "POST", body: { audio: sampleAudioDataUrl(), platform: "tiktok" },
    }), state));
    assert.equal(response.status, 200);
  }
  assert.equal(state.DB.aiUsageDaily.values().next().value, 12);
}
{
  const response = await read(await worker.fetch(request("/api/brain/moderate-music", {
    method: "POST", body: { audio: sampleAudioDataUrl(), platform: "youtube" }, token: null,
  }), env()));
  assert.equal(response.status, 401);
}




{
  const state=env();let args=null;state.AI={run:async(model,options)=>{args={model,options};return {response:"ترجمه فارسی معتبر"};}};
  const r=await read(await worker.fetch(request("/api/brain/translate-official",{method:"POST",body:{text:"A short official update.",kind:"caption",run_id:"mobcap_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}}),state));
  assert.equal(r.status,200);assert.equal(r.body.ok,true);assert.equal(args.options.max_tokens,700);assert.equal([...state.DB.aiProviderCalls.values()][0].status,"succeeded");
}
{
  const state=env();let called=false;state.AI={run:async()=>{called=true;return {response:"ترجمه فارسی"};}};
  const r=await read(await worker.fetch(request("/api/brain/translate-official",{method:"POST",body:{text:"x".repeat(2101),kind:"caption",run_id:"mobcap_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}}),state));
  assert.equal(r.status,413);assert.equal(r.body.error,"translation_text_too_long");assert.equal(called,false);assert.equal(state.DB.aiProviderCalls.size,0);
}
{
  const state=env();let args=null;state.AI={run:async(model,options)=>{args={model,options};return {response:"0|در باز است.\\n1|وارد شوید."};}};
  const r=await read(await worker.fetch(request("/api/brain/translate-official",{method:"POST",body:{text:"0|The door is open.\\n1|Go inside.",kind:"subtitle",run_id:"mobsub_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}}),state));
  assert.equal(r.status,200);assert.equal(args.options.max_tokens,8192);assert.equal(r.body.usage.kind,"subtitle");
}
{
  const state=env();let called=false;state.AI={run:async()=>{called=true;return {response:"ترجمه فارسی"};}};
  const r=await read(await worker.fetch(request("/api/brain/translate-official",{method:"POST",body:{text:"A short update",kind:"caption",run_id:"wrong_aaaaaaaaaaaaaaaaaaaaaaaa"}}),state));
  assert.equal(r.status,400);assert.equal(called,false);
}
{
  const state = env();
  let response = await read(await worker.fetch(request("/api/brain/owner-digest/events", {
    method: "POST",
    body: { category: "error", source: "queue_worker", title: "Pipeline failed", detail: "error_code=staging_failed" },
  }), state));
  assert.equal(response.status, 201, JSON.stringify(response.body));
  assert.equal(response.body.ok, true);
  const firstWindow = response.body.window_id;

  response = await read(await worker.fetch(request("/api/brain/owner-digest/claim", {
    method: "POST", body: {},
  }), state));
  assert.equal(response.status, 200);
  assert.equal(response.body.claimed, true);
  const lock = response.body.lock_token;
  assert.equal(response.body.state.events.length, 1);
  assert.equal(response.body.state.events[0].category, "error");
  assert.equal(response.body.state.health.d1_size_bytes, state.DB.sizeAfter);
  assert.equal(response.body.state.health.daily_d1_reads, null);
  assert.equal(response.body.state.health.daily_d1_writes, null);

  const concurrent = await read(await worker.fetch(request("/api/brain/owner-digest/claim", {
    method: "POST", body: {},
  }), state));
  assert.equal(concurrent.status, 200);
  assert.equal(concurrent.body.claimed, false);

  response = await read(await worker.fetch(request("/api/brain/owner-digest/commit", {
    method: "POST",
    body: {
      window_id: firstWindow, lock_token: lock, message_id: "rubika-message-1",
      message_body: "Daily digest", message_creation_uncertain: false,
    },
  }), state));
  assert.equal(response.status, 200);
  assert.equal(state.DB.ownerDigestState.message_id, "rubika-message-1");

  response = await read(await worker.fetch(request("/api/brain/owner-digest/events", {
    method: "POST",
    body: { category: "ai_success", source: "code_ai_github_models", title: "AI call succeeded", detail: "role=diagnose" },
  }), state));
  assert.equal(response.status, 201);

  response = await read(await worker.fetch(request("/api/brain/owner-digest/claim", {
    method: "POST", body: {},
  }), state));
  assert.equal(response.body.claimed, true);
  assert.equal(response.body.state.message_id, "rubika-message-1");
  assert.equal(response.body.state.counts.error, 1);
  assert.equal(response.body.state.counts.ai_success, 1);
  assert.equal(response.body.state.events.length, 2);

  // The next rolling 24-hour window starts a new message state, without touching old events.
  state.DB.ownerDigestState.edit_lock_token = null;
  state.DB.ownerDigestState.edit_lock_until_epoch = null;
  state.DB.ownerDigestState.window_ends_epoch = Date.now() - 1;
  response = await read(await worker.fetch(request("/api/brain/owner-digest/events", {
    method: "POST",
    body: { category: "info", source: "daily_digest", title: "New 24-hour window", detail: "" },
  }), state));
  assert.equal(response.status, 201);
  assert.notEqual(response.body.window_id, firstWindow);
  assert.equal(state.DB.ownerDigestState.message_id, null);
  assert.equal(state.DB.ownerDigestEvents.length, 3);
}
{
  const response = await read(await worker.fetch(request("/api/brain/owner-digest/events", {
    method: "POST",
    body: { category: "fatal", source: "bad", title: "Invalid category", detail: "" },
  }), env()));
  assert.equal(response.status, 400);
  assert.equal(response.body.error, "owner_digest_category_invalid");
}

// Error guidance now runs locally in Python. The Worker has no AI supervisor endpoint.
{
  const response = await read(await worker.fetch(request("/api/brain/supervise-error", {
    method: "POST",
    body: { event_code: "queue_intake_failed", stage: "queue_intake", error_code: "network_error" },
  }), env()));
  assert.equal(response.status, 404);
  assert.equal(response.body.error, "not_found");
}
// Owner panel unit tests: run in the existing Node Worker test suite with fake D1 and mocked Rubika Bot API.
class PanelFakeStatement {
  constructor(db, sql) {
    this.db = db;
    this.sql = sql;
    this.values = [];
  }
  bind(...values) { this.values = values; return this; }
  async first() {
    this.db.recordQuery(this.sql);
    if (this.sql.startsWith("INSERT INTO panel_state") && this.sql.includes("RETURNING value")) {
      const next = Number(this.db.state.get("panel_seq") || 0) + 1;
      this.db.state.set("panel_seq", String(next));
      return { value: String(next) };
    }
    if (this.sql.includes("SELECT chat_id FROM panel_owner WHERE id=1")) {
      return this.db.ownerChat ? { chat_id: this.db.ownerChat } : null;
    }
    if (this.sql.includes("AS agent_json")) {
      return {
        agent_json: JSON.stringify({ enabled: 0, started_at: null, updated_at: null }),
        item_counts_json: JSON.stringify([{ status: "ready", total: 2 }]),
        recent_runs_json: JSON.stringify([]),
        queue_counts_json: JSON.stringify({ queued: 2, sent: 1 }),
        sent_today: 1,
      };
    }
    if (this.sql.includes("SELECT value FROM panel_state WHERE key=?")) {
      const value = this.db.state.get(this.values[0]);
      return value === undefined ? null : { value };
    }
    if (this.sql.includes("SELECT version, rules_json FROM console_rules WHERE id = 1")) {
      return this.db.consoleRules ? { ...this.db.consoleRules } : null;
    }
    if (this.sql.includes("SELECT COUNT(*) AS c FROM items WHERE status = 'sent' AND sent_at >= ?")) {
      return { c: this.db.itemsSentToday };
    }
    if (this.sql.includes("SELECT COUNT(*) AS c FROM panel_links WHERE status NOT IN")) {
      const active = new Set(["confirmed", "queued", "processing", "ready_for_review", "approved", "sending"]);
      return { c: this.db.links.filter(row => !active.has(row.status)).length };
    }
    if (this.sql.includes("SELECT COUNT(*) AS c FROM panel_links")) {
      return { c: this.db.links.length };
    }
    if (this.sql.includes("FROM panel_links WHERE external_id = ?")) {
      const row = this.db.links.find(item => item.external_id === String(this.values[0]));
      return row ? { ...row } : null;
    }
    if (this.sql.includes("FROM panel_links WHERE id = ?")) {
      const row = this.db.links.find(item => Number(item.id) === Number(this.values[0]));
      return row ? { ...row } : null;
    }
    if (this.sql.includes("FROM console_jobs WHERE panel_link_id = ?")) {
      const row = [...this.db.consoleJobs.values()].find(item => Number(item.panel_link_id) === Number(this.values[0]));
      return row ? { ...row } : null;
    }
    if (this.sql.includes("FROM console_jobs WHERE id = ?")) {
      const row = this.db.consoleJobs.get(this.values[0]);
      return row ? { ...row } : null;
    }
    if (this.sql.includes("FROM console_owners WHERE chat_id = ?")) {
      const row = [...this.db.consoleOwners.values()].find(item => item.chat_id === this.values[0]);
      if (!row || (this.sql.includes("AND enabled = 1") && Number(row.enabled) !== 1)) return null;
      return { ...row };
    }
    if (this.sql.includes("FROM console_owner_invites WHERE code_hash = ?")) {
      const row = [...this.db.consoleInvites.values()].find(item =>
        item.code_hash === this.values[0] && item.used_at == null && item.expires_at > (this.values[1] || "")
      );
      return row ? { ...row } : null;
    }
    return null;
  }
  async all() {
    this.db.recordQuery(this.sql);
    if (this.db.failPanelReads) throw new Error("mock_panel_read_failed");
    if (this.sql.includes("FROM (SELECT 'groq' AS provider UNION ALL SELECT 'openrouter')")) {
      return { results: ["groq", "openrouter"].map((provider) => ({
        provider, calls_used: 2, total_calls_used: 3, cooldown_until: null,
        state_error_code: null, latest_error_code: null,
      })) };
    }
    if (this.sql.includes("FROM panel_links WHERE status NOT IN")) {
      const active = new Set(["confirmed", "queued", "processing", "ready_for_review", "approved", "sending"]);
      const limit = Number(this.values[0]) || 50;
      return { results: this.db.links.filter(row => !active.has(row.status))
        .sort((a, b) => Number(b.id) - Number(a.id)).slice(0, limit).map(row => ({ ...row })) };
    }
    if (this.sql.includes("FROM panel_links ORDER BY id DESC")) {
      return { results: this.db.links.slice().reverse().slice(0, 8).map(row => ({ ...row })) };
    }
    return { results: [] };
  }
  async run() {
    this.db.recordQuery(this.sql);
    if (this.sql.startsWith("UPDATE console_jobs SET params_json = ?")) {
      const [paramsJson, updatedAt, jobId] = this.values;
      const row = this.db.consoleJobs.get(jobId);
      if (!row) return { meta: { changes: 0 } };
      const statusMatch = /status IN \(([^)]+)\)/.exec(this.sql);
      if (statusMatch) {
        const allowed = statusMatch[1].split(",").map(value => value.trim().replace(/^'|'$/g, ""));
        if (!allowed.includes(row.status)) return { meta: { changes: 0 } };
      }
      const markerMatch = /json_extract\(params_json, '\$\.([a-z_]+)'\)/.exec(this.sql);
      if (markerMatch) {
        let params = {};
        try { params = JSON.parse(row.params_json || "{}"); } catch {}
        if (["dispatching", "dispatched"].includes(params[markerMatch[1]])) {
          return { meta: { changes: 0 } };
        }
      }
      Object.assign(row, { params_json: paramsJson, updated_at: updatedAt });
      return { meta: { changes: 1 } };
    }
    if (this.sql.startsWith("UPDATE console_jobs SET result_json = ?")) {
      const [resultJson, updatedAt, jobId, expectedResultJson] = this.values;
      const row = this.db.consoleJobs.get(jobId);
      this.db.consoleJobResultUpdates.push({
        sql: this.sql, job_id: jobId,
        expected_matches: Boolean(row && row.result_json === expectedResultJson),
        status: row?.status || null,
        bound_caption: (() => { try { return JSON.parse(String(resultJson)).caption; } catch { return null; } })(),
      });
      if (!row || row.type !== "mob_x_link" || row.status !== "ready_for_review" ||
          row.result_json !== expectedResultJson) return { meta: { changes: 0 } };
      row.result_json = String(resultJson);
      row.updated_at = updatedAt;
      this.db.consoleJobResultUpdates.at(-1).persisted_caption = JSON.parse(row.result_json).caption;
      return { meta: { changes: 1 } };
    }
    if (this.sql.startsWith("UPDATE items SET caption = ?, original_caption = '', persian_translation = ''")) {
      const [caption, itemId] = this.values;
      const row = this.db.items.get(itemId);
      if (!row || row.platform !== "mob_official" || row.status !== "ready") return { meta: { changes: 0 } };
      Object.assign(row, { caption, original_caption: "", persian_translation: "" });
      return { meta: { changes: 1 } };
    }
    if (this.sql.startsWith("UPDATE items SET caption = ? WHERE id = ? AND platform = 'mob_official' AND status = 'ready'")) {
      const [caption, itemId] = this.values;
      const row = this.db.items.get(itemId);
      if (!row || row.platform !== "mob_official" || row.status !== "ready") return { meta: { changes: 0 } };
      row.caption = caption;
      return { meta: { changes: 1 } };
    }
    if (this.sql.startsWith("UPDATE console_rules SET version = version + 1")) {
      if (this.db.conflictConsoleRulesUpdate) {
        this.db.conflictConsoleRulesUpdate = false;
        this.db.consoleRules.version += 1;
        return { meta: { changes: 0 } };
      }
      const [rulesJson, updatedAt, expectedVersion] = this.values;
      if (!this.db.consoleRules || Number(this.db.consoleRules.version) !== Number(expectedVersion)) {
        return { meta: { changes: 0 } };
      }
      this.db.consoleRules.version += 1;
      this.db.consoleRules.rules_json = String(rulesJson);
      this.db.consoleRules.updated_at = updatedAt;
      return { meta: { changes: 1 } };
    }
    if (this.sql.startsWith("DELETE FROM panel_links WHERE status NOT IN")) {
      const active = new Set(["confirmed", "queued", "processing", "ready_for_review", "approved", "sending"]);
      const before = this.db.links.length;
      this.db.links = this.db.links.filter(row => active.has(row.status));
      const count = before - this.db.links.length;
      this.db.linkDeleteCalls += 1;
      return { meta: { changes: count } };
    }
    if (this.sql.startsWith("INSERT OR IGNORE INTO panel_links (url, external_id, status)")) {
      const [url, externalId] = this.values;
      if (this.db.links.some(row => row.external_id === String(externalId) || row.url === url)) return { meta: { changes: 0 } };
      const id = this.db.nextPanelLinkId++;
      this.db.links.push({ id, url, external_id: String(externalId), status: "pending", created_at: new Date().toISOString(), confirmed_at: null, updated_at: new Date().toISOString() });
      return { meta: { changes: 1, last_row_id: id } };
    }
    if (this.sql.startsWith("UPDATE panel_links SET status = 'pending'")) {
      const row = this.db.links.find(item => Number(item.id) === Number(this.values[0]));
      if (!row || row.status !== "rejected") return { meta: { changes: 0 } };
      Object.assign(row, { status: "pending", updated_at: new Date().toISOString() });
      return { meta: { changes: 1 } };
    }
    if (this.sql.startsWith("UPDATE panel_links SET status = 'rejected'")) {
      const row = this.db.links.find(item => Number(item.id) === Number(this.values[0]));
      if (!row || row.status !== "pending") return { meta: { changes: 0 } };
      Object.assign(row, { status: "rejected", updated_at: new Date().toISOString() });
      return { meta: { changes: 1 } };
    }
    if (this.sql.startsWith("UPDATE panel_links SET status = 'confirmed'")) {
      const row = this.db.links.find(item => Number(item.id) === Number(this.values[0]));
      if (!row || !["pending", "confirmed"].includes(row.status)) return { meta: { changes: 0 } };
      Object.assign(row, { status: "confirmed", confirmed_at: row.confirmed_at || new Date().toISOString(), updated_at: new Date().toISOString() });
      return { meta: { changes: 1 } };
    }
    if (this.sql.startsWith("INSERT INTO console_jobs") && this.sql.includes("SELECT ?, 'mob_x_link'")) {
      const [id, paramsJson, panelLinkId] = this.values;
      const existing = [...this.db.consoleJobs.values()].find(row => Number(row.panel_link_id) === Number(panelLinkId));
      const link = this.db.links.find(item => Number(item.id) === Number(panelLinkId));
      if (existing || !link || link.status !== "confirmed") return { meta: { changes: 0 } };
      this.db.consoleJobs.set(id, { id, type: "mob_x_link", status: "confirmed", progress: 0, params_json: paramsJson, result_json: null, error: null, panel_link_id: Number(panelLinkId), created_at: new Date().toISOString(), updated_at: new Date().toISOString() });
      return { meta: { changes: 1 } };
    }
    if (this.sql.startsWith("INSERT INTO panel_state")) {
      this.db.state.set(this.values[0], String(this.values[1]));
      return { meta: { changes: 1 } };
    }
    if (this.sql.startsWith("INSERT OR IGNORE INTO panel_owner")) {
      if (this.db.ownerChat) return { meta: { changes: 0 } };
      this.db.ownerChat = this.values[0];
      return { meta: { changes: 1 } };
    }
    if (this.sql.startsWith("INSERT OR IGNORE INTO panel_events")) {
      const key = this.values[0];
      const has = this.db.events.has(key);
      this.db.events.add(key);
      return { meta: { changes: has ? 0 : 1 } };
    }
    if (this.sql.startsWith("DELETE FROM panel_events")) {
      this.db.events.clear();
      return { meta: { changes: 1 } };
    }
    if (this.sql.startsWith("INSERT OR IGNORE INTO console_owners")) {
      if ([...this.db.consoleOwners.values()].some(row => row.chat_id === this.values[0])) return { meta: { changes: 0 } };
      const id = this.db.nextConsoleOwnerId++;
      this.db.consoleOwners.set(id, { id, chat_id: this.values[0], label: "مالک اصلی", role: "owner", enabled: 1, claimed_at: new Date().toISOString(), last_seen_at: new Date().toISOString() });
      return { meta: { changes: 1, last_row_id: id } };
    }
    if (this.sql.startsWith("UPDATE console_owner_invites SET used_at")) {
      const row = this.db.consoleInvites.get(Number(this.values[2]));
      if (!row || row.used_at || row.expires_at <= this.values[3]) return { meta: { changes: 0 } };
      Object.assign(row, { used_at: this.values[0], used_by: this.values[1] });
      return { meta: { changes: 1 } };
    }
    if (this.sql.startsWith("INSERT INTO console_owners") && this.sql.includes("SELECT ?")) {
      if (Number(this.db.lastChanges || 0) <= 0 || [...this.db.consoleOwners.values()].some(row => row.chat_id === this.values[0])) {
        return { meta: { changes: 0 } };
      }
      const id = this.db.nextConsoleOwnerId++;
      const row = { id, chat_id: this.values[0], label: this.values[1], role: "viewer", enabled: 1, claimed_at: this.values[2], last_seen_at: this.values[3] };
      this.db.consoleOwners.set(id, row);
      return { meta: { changes: 1, last_row_id: id } };
    }
    if (this.sql.startsWith("INSERT INTO brain_events") && this.sql.includes("SELECT NULL")) {
      if (Number(this.db.lastChanges || 0) <= 0) return { meta: { changes: 0 } };
      const code = this.sql.includes("'console_owner_claim_bootstrap'") ? "console_owner_claim_bootstrap"
        : this.sql.includes("'console_rules_update'") ? "console_rules_update"
        : "console_owner_claim";
      this.db.brainEvents.push({ code, level: "info", details_json: this.values[0] });
      return { meta: { changes: 1 } };
    }
    return { meta: { changes: 1 } };
  }
}
class PanelFakeDB {
  constructor() {
    this.d1QueryCount = 0;
    this.d1QueryLog = [];
    this.ownerChat = null;
    this.consoleRules = {
      version: 1,
      rules_json: JSON.stringify({
        daily_success_cap: 4,
        publishing_enabled: true,
        test_channel_only: false,
        quiet_hours: { start: "20:00", end: "07:00" },
      }),
      updated_at: null,
    };
    this.itemsSentToday = 3;
    this.conflictConsoleRulesUpdate = false;
    // Some tests seed the retired atomic value to verify that it cannot replace the tracked primary message.
    this.state = new Map([["panel_sync_mode", "inplace"]]);
    this.events = new Set();
    this.links = [];
    this.linkDeleteCalls = 0;
    this.nextPanelLinkId = 1;
    this.consoleJobs = new Map();
    this.consoleJobResultUpdates = [];
    this.items = new Map();
    this.consoleOwners = new Map();
    this.nextConsoleOwnerId = 1;
    this.consoleInvites = new Map();
    this.brainEvents = [];
    this.lastChanges = 0;
    this.failReadOnlyBatch = false;
    this.failPanelReads = false;
  }
  recordQuery(sql) { this.d1QueryCount += 1; this.d1QueryLog.push(String(sql)); }
  resetQueryCount() { this.d1QueryCount = 0; this.d1QueryLog = []; }
  prepare(sql) { return new PanelFakeStatement(this, sql); }
  async batch(statements) {
    if (statements.length && statements.every((statement) => /^SELECT\b/i.test(statement.sql.trim()))) {
      for (const statement of statements) this.recordQuery(statement.sql);
      if (this.failReadOnlyBatch) throw new Error("mock_d1_read_failure");
      if (statements.length === 2 && statements[0].sql.includes("AS primary_owner") &&
          statements[1].sql.includes("SELECT key, value FROM panel_state WHERE key IN")) {
        const owner = [...this.consoleOwners.values()].find(row => row.chat_id === statements[0].values[0]);
        return [
          { results: [{ primary_owner: this.ownerChat, console_owner_id: owner?.id ?? null,
            console_owner_role: owner?.role ?? null, console_owner_enabled: owner?.enabled ?? null }] },
          { results: [...this.state.entries()].map(([key, value]) => ({ key, value }))
            .filter(row => statements[1].values.includes(row.key)) },
        ];
      }
      if (statements.length === 2 && statements[0].sql.includes("AS agent_json") &&
          statements[1].sql.includes("FROM (SELECT 'groq' AS provider UNION ALL SELECT 'openrouter')")) {
        return [
          { results: [{
            agent_json: JSON.stringify({ enabled: 0, started_at: null, updated_at: null }),
            item_counts_json: JSON.stringify([{ status: "ready", total: 2 }]),
            recent_runs_json: JSON.stringify([]),
            queue_counts_json: JSON.stringify({ queued: 2, sent: 1 }), sent_today: 1,
          }] },
          { results: ["groq", "openrouter"].map(provider => ({
            provider, calls_used: 2, total_calls_used: 3, cooldown_until: null,
            state_error_code: null, latest_error_code: null,
          })) },
        ];
      }
      return [
        { results: [{ queued: 2, failed: 1, sent_today: 3 }] },
        { results: [{ status: "success" }] },
        { results: [{ errors_24h: 4 }] },
      ];
    }
    const results = [];
    for (const statement of statements) {
      const result = await statement.run();
      this.lastChanges = Number(result?.meta?.changes || 0);
      results.push(result);
    }
    return results;
  }
}
const panelEnv = () => ({
  DB: new PanelFakeDB(),
  RUBIKA_TOKEN: "rubika-panel-test-token-with-sufficient-length",
  OWNER_SETUP_CODE: "correct-setup-code",
  AGENT_API_TOKEN: TOKEN,
});
async function withMockRubikaFetch(fn, responder = null) {
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, init = {}) => {
    const method = String(url).split("/").at(-1);
    let payload = {};
    try { payload = JSON.parse(init.body || "{}"); } catch {}
    calls.push({ url: String(url), method, payload });
    const custom = responder ? await responder({ url, init, method, payload, calls }) : null;
    if (custom instanceof Response) return custom;
    const messageCount = calls.filter(call => call.method === "sendMessage").length;
    return new Response(JSON.stringify({ status: "OK", data: { message_id: "mock-reply-" + messageCount } }), {
      status: 200, headers: { "content-type": "application/json" },
    });
  };
  try { await fn(calls); } finally { globalThis.fetch = originalFetch; }
}
async function postPanelUpdateWithWorker(workerInstance, state, update, secret) {
  const pending = [];
  const ctx = { waitUntil(promise) { pending.push(promise); } };
  const response = await workerInstance.fetch(new Request(
    "https://worker.test/panel/hook/" + secret + "/ReceiveUpdate",
    { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(update) }
  ), state, ctx);
  await Promise.all(pending);
  return response;
}
async function postPanelUpdate(state, update, secret) {
  return postPanelUpdateWithWorker(worker, state, update, secret);
}


// Phase-two regression: improve the final #184 counts and cap each slow route at 15 D1 statements.
{
  await withMockRubikaFetch(async () => {
    const phaseOne = { p_report: 43, p_ai: 37, p_status: 32 };
    const measure = async button => {
      const state = panelEnv();
      state.DB.ownerChat = "b0actualowner";
      const panelMid = "query-count-panel-" + button;
      state.DB.state.set("panel_mid", panelMid);
      const secret = await panel.hookSecret(state);
      state.DB.resetQueryCount();
      const response = await postPanelUpdate(state, {
        update: { type: "NewMessage", chat_id: "b0actualowner", new_message: {
          message_id: panelMid, chat_id: "b0actualowner", text: "",
          aux_data: { button_id: button },
        } },
      }, secret);
      assert.equal(response.status, 200, button + " click hook should be accepted");
      assert.equal(state.DB.d1QueryLog.filter(sql =>
        sql.includes("SELECT key, value FROM panel_state WHERE key IN")).length, 1,
        button + " must prefetch panel_state once per event");
      stateQueryLogByButton[button] = state.DB.d1QueryLog.slice();
      return state.DB.d1QueryCount;
    };
    const after = {};
    const stateQueryLogByButton = {};
    for (const button of ["p_report", "p_ai", "p_status"]) {
      after[button] = await measure(button);
      if (after[button] > 15) {
        console.error("OWNER_PANEL_PHASE2_QUERY_DIAGNOSTIC=" + JSON.stringify({
          button, count: after[button], queries: stateQueryLogByButton[button],
        }));
      }
      assert.ok(after[button] <= 15, button + " must use at most 15 D1 statements: " + after[button]);
      assert.ok(after[button] < phaseOne[button], button + " must improve on phase one: " +
        phaseOne[button] + " -> " + after[button]);
    }
    console.log("OWNER_PANEL_CLICK_D1_QUERY_COUNTS=" + JSON.stringify({ before: phaseOne, after }));
  });
}

async function withDispatchFetch(responder, fn) {
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, init = {}) => {
    calls.push({ url: String(url), init });
    return responder({ url: String(url), init, calls });
  };
  try { return await fn(calls); } finally { globalThis.fetch = originalFetch; }
}

// Worker dispatch is pinned to the approved workflow/main ref and never exposes token-bearing errors.
{
  const fakeToken = "mock-github-dispatch-token-never-log";
  await withDispatchFetch(async () => new Response(null, { status: 204 }), async calls => {
    const result = await panel.dispatchMobXWorkflow(
      { GITHUB_DISPATCH_TOKEN: fakeToken },
      "mob-x-link-processor.yml", { panel_link_id: 23 },
    );
    assert.deepEqual(result, { ok: true });
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, "https://api.github.com/repos/hosseinasgari898989-dev/rubika-agent/actions/workflows/mob-x-link-processor.yml/dispatches");
    assert.equal(calls[0].init.method, "POST");
    assert.equal(calls[0].init.headers["User-Agent"], "rubika-agent-worker");
    assert.equal(calls[0].init.headers.Accept, "application/vnd.github+json");
    assert.equal(calls[0].init.headers["X-GitHub-Api-Version"], "2022-11-28");
    assert.equal(calls[0].init.headers.Authorization, "Bearer " + fakeToken);
    assert.deepEqual(JSON.parse(calls[0].init.body), { ref: "main" });
    assert.ok(calls[0].init.signal);
  });
}

// Permanent GitHub HTTP errors are sanitized and never retried.
{
  for (const status of [401, 403, 404, 422]) {
    await withDispatchFetch(async () => new Response(null, { status }), async calls => {
      const result = await panel.dispatchMobXWorkflow(
        { GITHUB_DISPATCH_TOKEN: "mock-github-dispatch-token-never-log" },
        "mob-x-link-processor.yml", { panel_link_id: 23 },
      );
      assert.equal(result.ok, false);
      assert.equal(result.error, "github_dispatch_http_" + status);
      assert.equal(result.error_detail, "status=" + status + " body=");
      assert.equal(calls.length, 1);
      assert.equal(JSON.stringify(result).includes("mock-github-dispatch-token"), false);
    });
  }
}

// GitHub's 403 message is surfaced as a bounded, token-redacted error_detail.
{
  const fakeToken = "mock-github-dispatch-token-never-log";
  const responseBody = JSON.stringify({
    message: "Resource not accessible by integration",
    detail: "This mock body is intentionally longer than two hundred characters to prove truncation works. ".repeat(4),
    echoed: fakeToken,
  });
  await withDispatchFetch(async () => new Response(responseBody, { status: 403 }), async calls => {
    const result = await panel.dispatchMobXWorkflow(
      { GITHUB_DISPATCH_TOKEN: fakeToken },
      "mob-x-link-processor.yml", { panel_link_id: 23 },
    );
    assert.equal(result.ok, false);
    assert.equal(result.error, "github_dispatch_http_403");
    assert.equal(result.error_detail, "status=403 body=" + responseBody.replaceAll(fakeToken, "[REDACTED]").slice(0, 200));
    assert.equal(result.error_detail.length, ("status=403 body=").length + 200);
    assert.equal(calls.length, 1);
    assert.equal(calls[0].init.headers["User-Agent"], "rubika-agent-worker");
    assert.equal(calls[0].init.headers.Accept, "application/vnd.github+json");
    assert.equal(calls[0].init.headers["X-GitHub-Api-Version"], "2022-11-28");
    assert.equal(calls[0].init.headers.Authorization, "Bearer " + fakeToken);
    assert.deepEqual(JSON.parse(calls[0].init.body), { ref: "main" });
    assert.equal(JSON.stringify(result).includes(fakeToken), false);
  });
}

// Rate limits, server failures and timeouts receive bounded retries.
{
  for (const status of [429, 503]) {
    await withDispatchFetch(async ({ calls }) =>
      calls.length === 1 ? new Response(null, { status }) : new Response(null, { status: 204 }),
    async calls => {
      const result = await panel.dispatchMobXWorkflow(
        { GITHUB_DISPATCH_TOKEN: "mock-github-dispatch-token-never-log" },
        "mob-x-link-processor.yml", { panel_link_id: 24 },
      );
      assert.deepEqual(result, { ok: true });
      assert.equal(calls.length, 2);
    });
  }
  await withDispatchFetch(async ({ calls }) => {
    if (calls.length === 1) {
      const error = new Error("private token/header detail must not be returned");
      error.name = "TimeoutError";
      throw error;
    }
    return new Response(null, { status: 204 });
  }, async calls => {
    const result = await panel.dispatchMobXWorkflow(
      { GITHUB_DISPATCH_TOKEN: "mock-github-dispatch-token-never-log" },
      "mob-x-news-production.yml", { panel_link_job_id: "job_abcdef1234567890" },
    );
    assert.deepEqual(result, { ok: true });
    assert.equal(calls.length, 2);
    assert.equal(JSON.stringify(result).includes("private token"), false);
  });
  await withDispatchFetch(async () => new Response(null, { status: 503 }), async calls => {
    const result = await panel.dispatchMobXWorkflow(
      { GITHUB_DISPATCH_TOKEN: "mock-github-dispatch-token-never-log" },
      "mob-x-link-processor.yml", { panel_link_id: 25 },
    );
    assert.equal(result.ok, false);
    assert.equal(result.error, "github_dispatch_http_503");
    assert.equal(result.error_detail, "status=503 body=");
    assert.equal(calls.length, 3);
  });
}

// A duplicate event for a previously dispatched link cannot dispatch a second workflow.
{
  const state = env();
  state.GITHUB_DISPATCH_TOKEN = "mock-github-dispatch-token-never-log";
  const panelLinkId = 3;
  const jobId = "job_abcdef1234567890";
  state.DB.panelLinks.set(panelLinkId, {
    id: panelLinkId, url: "https://x.com/mobgamesstudios/status/2107878755079213476",
    external_id: "2107878755079213476", status: "confirmed", created_at: new Date().toISOString(),
  });
  state.DB.consoleJobs.set(jobId, {
    id: jobId, type: "mob_x_link", status: "confirmed", panel_link_id: panelLinkId,
    params_json: JSON.stringify({ url: "https://x.com/mobgamesstudios/status/2107878755079213476", panel_link_id: panelLinkId }),
    created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
  });
  await withDispatchFetch(async () => new Response(null, { status: 204 }), async calls => {
    const first = await panel.dispatchMobXJobOnce(state, jobId, "link_dispatch_state",
      "mob-x-link-processor.yml", { panel_link_id: panelLinkId }, ["confirmed", "queued"]);
    const second = await panel.dispatchMobXJobOnce(state, jobId, "link_dispatch_state",
      "mob-x-link-processor.yml", { panel_link_id: panelLinkId }, ["confirmed", "queued"]);
    assert.equal(first.ok, true);
    assert.equal(first.skipped, false);
    assert.equal(second.ok, true);
    assert.equal(second.skipped, true);
    assert.equal(calls.length, 1);
    assert.equal(JSON.parse(state.DB.consoleJobs.get(jobId).params_json).link_dispatch_state, "dispatched");
  });
}
// md() reports Rubika metadata offsets in JavaScript UTF-16 code units, including astral emoji.
{
  const formatted = panel.md("😀**سلام🌟**");
  assert.equal(formatted.text, "😀سلام🌟");
  assert.deepEqual(formatted.metadata.meta_data_parts, [
    { type: "Bold", from_index: 2, length: 6 },
  ]);
}

// normalize() covers new-message button aux_data, inline-message event, and ordinary text.
{
  const click = panel.normalize({
    update: {
      type: "NewMessage", chat_id: "b0paneltest",
      new_message: { message_id: "m-aux", chat_id: "b0paneltest", text: "", aux_data: { button_id: "p_status" } },
    },
  });
  assert.equal(click.kind, "click");
  assert.equal(click.button, "p_status");
  assert.equal(click.chat, "b0paneltest");

  const inline = panel.normalize({
    inline_message: { message_id: "m-inline", chat_id: "b0paneltest", text: "  inline text  " },
  });
  assert.equal(inline.kind, "click");
  assert.equal(inline.text, "inline text");

  const textEvent = panel.normalize({
    update: {
      type: "NewMessage", chat_id: "b0paneltest",
      new_message: { message_id: "m-text", chat_id: "b0paneltest", text: "  سلام دنیا  ", time: 1234567890 },
    },
  });
  assert.equal(textEvent.kind, "text");
  assert.equal(textEvent.text, "سلام دنیا");
  assert.equal(textEvent.time, 1234567890);
  assert.equal(panel.normalize({ update: { type: "NewMessage", chat_id: "b0paneltest",
    new_message: { message_id: "m-no-time", text: "hello", time: "1234567890" } } }).time, null);
}

// Private-chat recognition accepts b0 IDs, rejects groups/channels, and rejects bot-authored input.
{
  const event = overrides => ({
    chat: "b0paneltest", types: [], isGroupish: false, botAuthored: false, ...overrides,
  });
  assert.equal(panel.isPrivateChat(event({})), true);
  assert.equal(panel.isPrivateChat(event({ chat: "g0grouptest" })), false);
  assert.equal(panel.isPrivateChat(event({ chat: "c0channeltest" })), false);
  assert.equal(panel.isPrivateChat(event({ botAuthored: true })), false);
  assert.equal(panel.isPrivateChat(event({ types: ["group"] })), false);
}

// Link parsing accepts only a canonical post from the exact official Mob handle.
{
  assert.equal(panel.parseXLink("https://twitter.com/mobgamesstudios/status/2107878755079213476"),
    "https://x.com/mobgamesstudios/status/2107878755079213476");
  assert.equal(panel.parseXLink("https://www.x.com/mobgamesstudios/status/2107878755079213476/"),
    "https://x.com/mobgamesstudios/status/2107878755079213476");
  assert.equal(panel.parseXLink("New post https://twitter.com/mobgamesstudios/status/2107878755079213476?s=20"), null);
  assert.equal(panel.parseXLink("https://x.com/Test_User/status/2107878755079213476"), null);
  assert.equal(panel.parseXLink("http://x.com/mobgamesstudios/status/2107878755079213476"), null);
  assert.equal(panel.parseXLink("https://example.com/not-a-post"), null);
}

// PV link submission remains pending until the owner confirms, then creates exactly one Mob X job.
{
  await withMockRubikaFetch(async calls => {
    const state = panelEnv();
    state.GITHUB_DISPATCH_TOKEN = "mock-github-dispatch-token-never-log";
    state.DB.ownerChat = "b0actualowner";
    state.DB.state.set("panel_mid", "mob-link-confirm-panel");
    const secret = await panel.hookSecret(state);
    const postText = async (text, messageId) => postPanelUpdate(state, {
      update: { type: "NewMessage", chat_id: "b0actualowner", new_message: {
        message_id: messageId, chat_id: "b0actualowner", text,
      } },
    }, secret);
    const click = async (button, messageId) => postPanelUpdate(state, {
      update: { type: "NewMessage", chat_id: "b0actualowner", new_message: {
        message_id: messageId, chat_id: "b0actualowner", text: "", aux_data: { button_id: button },
      } },
    }, secret);

    await click("p_mob", "mob-link-confirm-panel");
    await postText("https://x.com/mobgamesstudios/status/2107878755079213476", "mob-link-text-2");
    assert.equal(state.DB.links.length, 1);
    assert.equal(state.DB.links[0].status, "pending");
    assert.equal(state.DB.consoleJobs.size, 0, "submitting a link must not start processing before owner confirmation");
    const confirmMessage = calls.find(call => call.method === "sendMessage" && call.payload.text.includes("این لینک است؟"));
    assert.ok(confirmMessage, "the Mob X confirmation prompt must be sent separately");
    const confirmButtons = confirmMessage.payload.inline_keypad.rows.flatMap(row => row.buttons).map(button => button.id);
    assert.deepEqual(confirmButtons, ["p_mob_confirm", "p_mob_cancel"], "the link-choice prompt must include both glass buttons");
    const confirmMessageId = state.DB.state.get("panel_mob_temp_mid");
    assert.ok(confirmMessageId && confirmMessageId !== state.DB.state.get("panel_mid"));
    assert.equal(state.DB.state.get("panel_mid"), "mob-link-confirm-panel", "the confirmation prompt must not replace the main panel");
    await click("p_mob_confirm", confirmMessageId);
    assert.equal(calls.some(call => call.method === "deleteMessage" && call.payload.message_id === confirmMessageId), true,
      "the temporary link confirmation prompt must be deleted after its action");
    assert.equal(state.DB.state.get("panel_mob_temp_mid"), "");
    assert.equal(state.DB.state.get("panel_mid"), "mob-link-confirm-panel");
    assert.equal(state.DB.links[0].status, "confirmed");
    assert.equal(state.DB.consoleJobs.size, 1);
    const job = [...state.DB.consoleJobs.values()][0];
    assert.equal(job.type, "mob_x_link");
    assert.equal(job.status, "confirmed");
    const confirmedParams = JSON.parse(job.params_json);
    assert.deepEqual({
      url: confirmedParams.url,
      panel_link_id: confirmedParams.panel_link_id,
    }, {
      url: "https://x.com/mobgamesstudios/status/2107878755079213476",
      panel_link_id: state.DB.links[0].id,
    });
    assert.equal(calls.filter(call => call.method === "editMessageText").length > 0, true);
    const dispatchCalls = calls.filter(call => call.url && call.url.includes("api.github.com"));
    assert.equal(dispatchCalls.length, 1);
    assert.equal(dispatchCalls[0].method, "dispatches");
    assert.equal(dispatchCalls[0].payload.ref, "main");
    assert.deepEqual(dispatchCalls[0].payload, { ref: "main" });
  }, async ({ url }) =>
    String(url).startsWith("https://api.github.com/") ? new Response(null, { status: 204 }) : null);
}

// The PV no-caption action is job-scoped, bold, idempotent and never approves or dispatches publication.
{
  await withMockRubikaFetch(async calls => {
    const state = panelEnv();
    state.DB.ownerChat = "b0actualowner";
    state.DB.state.set("panel_mid", "mob-x-main-panel-001");
    state.DB.consoleOwners.set(1, {
      id: 1, chat_id: "b0actualowner", role: "owner", enabled: 1,
    });
    const firstJobId = "job_abcdef1234567890";
    const secondJobId = "job_abcdef1234567891";
    const firstItemId = "mob_official:2107878755079213476";
    const secondItemId = "mob_official:2107878755079213477";
    const firstResult = {
      item_id: firstItemId, external_id: "2107878755079213476",
      caption: "**ماب انترتینمنت در پیج خود پستی منتشر کرد و گفت:**\\n\\n**Original post text**\\n\\n$**ترجمه متن:**\\n**ترجمهٔ خصوصی**$",
      original_caption: "Original post text", persian_translation: "ترجمهٔ خصوصی",
      rubika_file_id: "file-first", preview_sent: true, preview_message_id: "preview-old-first",
    };
    const secondResult = {
      item_id: secondItemId, external_id: "2107878755079213477",
      caption: "Second job caption", original_caption: "Second original",
      persian_translation: "Second translation", rubika_file_id: "file-second",
      preview_sent: true, preview_message_id: "preview-old-second",
    };
    state.DB.consoleJobs.set(firstJobId, {
      id: firstJobId, type: "mob_x_link", status: "ready_for_review",
      panel_link_id: 1, params_json: "{}", result_json: JSON.stringify(firstResult),
    });
    state.DB.consoleJobs.set(secondJobId, {
      id: secondJobId, type: "mob_x_link", status: "ready_for_review",
      panel_link_id: 2, params_json: "{}", result_json: JSON.stringify(secondResult),
    });
    state.DB.items.set(firstItemId, {
      id: firstItemId, platform: "mob_official", status: "ready", review_state: "pending",
      caption: firstResult.caption, original_caption: firstResult.original_caption,
      persian_translation: firstResult.persian_translation,
    });
    state.DB.items.set(secondItemId, {
      id: secondItemId, platform: "mob_official", status: "ready", review_state: "pending",
      caption: secondResult.caption, original_caption: secondResult.original_caption,
      persian_translation: secondResult.persian_translation,
    });
    const secret = await panel.hookSecret(state);
    const click = async (button, messageId = "preview-old-first") => postPanelUpdate(state, {
      update: { type: "NewMessage", chat_id: "b0actualowner", new_message: {
        message_id: messageId, chat_id: "b0actualowner", text: "", aux_data: { button_id: button },
      } },
    }, secret);

    await click("p_mob_nocap_" + firstJobId);
    assert.equal(state.DB.state.get("panel_mid"), "mob-x-main-panel-001", "no-caption callback must not move panel_mid");
    assert.equal(calls.filter(call => call.method === "sendMessage").length, 0, "the callback result belongs on the main message");
    const mainTextEdits = calls.filter(call => call.method === "editMessageText" && call.payload.message_id === "mob-x-main-panel-001");
    assert.ok(mainTextEdits.length >= 2, "slow action must edit main panel for loading and final result");
    assert.match(mainTextEdits[0].payload.text, /در حال انجام/);
    assert.equal(mainTextEdits.at(-1).payload.text.includes("کپشن حذف"), true);
    assert.equal(calls.filter(call => call.method === "editMessageText").every(call => call.payload.message_id === "mob-x-main-panel-001"), true);
    const observedEvent = JSON.parse(state.DB.state.get("last_event") || "{}");
    assert.equal(observedEvent.button, "p_mob_nocap_" + firstJobId, "the PV hook must receive the no-caption callback");
    assert.ok(Number(state.DB.state.get("panel_seq") || 0) > 0, "the callback must pass owner authorization and event de-duplication");
    assert.equal(calls.some(call => call.method === "sendMessage" && String(call.payload.text).includes("فقط نقش مالک")), false);
    assert.ok(calls.some(call => call.method === "sendFile"), "no-caption callback must refresh this job's preview; last_error=" + String(state.DB.state.get("last_error") || "none"));
    assert.ok(state.DB.consoleJobResultUpdates.length >= 2, "both job-scoped CAS updates should execute; updates=" + JSON.stringify(state.DB.consoleJobResultUpdates));
    assert.ok(state.DB.consoleJobResultUpdates.every(update => update.job_id === firstJobId && update.expected_matches && update.status === "ready_for_review"), "each compare-and-swap must match the same job and its current state; updates=" + JSON.stringify(state.DB.consoleJobResultUpdates));
    const changed = JSON.parse(state.DB.consoleJobs.get(firstJobId).result_json);
    assert.equal(changed.caption, "**ماب انترتینمنت در پیج خود پستی منتشر کرد.**", "updated result and final stored job result differ; trace=" + JSON.stringify(state.DB.consoleJobResultUpdates));
    assert.equal(changed.caption_mode, "no_caption");
    assert.equal(changed.original_caption, "");
    assert.equal(changed.persian_translation, "");
    assert.equal(changed.preview_sent, true);
    assert.equal(state.DB.items.get(firstItemId).caption, changed.caption);
    assert.equal(state.DB.items.get(firstItemId).original_caption, "");
    assert.equal(state.DB.items.get(firstItemId).persian_translation, "");
    assert.deepEqual(state.DB.consoleJobs.get(secondJobId), {
      id: secondJobId, type: "mob_x_link", status: "ready_for_review",
      panel_link_id: 2, params_json: "{}", result_json: JSON.stringify(secondResult),
    });
    assert.equal(state.DB.items.get(secondItemId).caption, secondResult.caption);
    assert.equal(state.DB.items.get(secondItemId).original_caption, secondResult.original_caption);
    assert.equal(state.DB.items.get(secondItemId).persian_translation, secondResult.persian_translation);

    const previews = calls.filter(call => call.method === "sendFile");
    assert.equal(previews.length, 1, "no-caption must refresh the preview once");
    assert.equal(previews[0].payload.file_id, "file-first");
    assert.equal(previews[0].payload.text, "ماب انترتینمنت در پیج خود پستی منتشر کرد.");
    assert.deepEqual(previews[0].payload.metadata.meta_data_parts.map(part => part.type), ["Bold"]);
    assert.equal(previews[0].payload.inline_keypad.rows.flatMap(row => row.buttons).some(button => button.id === "p_mob_publish_" + firstJobId), true);
    assert.equal(calls.some(call => call.url && call.url.includes("api.github.com")), false, "the button must not dispatch publication");

    await click("p_mob_nocap_" + firstJobId);
    assert.equal(calls.filter(call => call.method === "sendFile").length, 1, "a second click must be idempotent");
  });
}

// Editing a Mob X caption keeps its prompt separate and deletes that temporary message after valid input.
{
  await withMockRubikaFetch(async calls => {
    const state = panelEnv();
    state.DB.ownerChat = "b0actualowner";
    state.DB.state.set("panel_mid", "mob-x-main-panel-edit");
    const jobId = "job_abcdef1234567890";
    const itemId = "mob_official:2107878755079213476";
    const result = {
      item_id: itemId, caption: "Before", original_caption: "Before",
      persian_translation: "", rubika_file_id: "mob-preview-file",
      preview_sent: true, preview_message_id: "mob-old-preview",
    };
    state.DB.consoleJobs.set(jobId, {
      id: jobId, type: "mob_x_link", status: "ready_for_review",
      panel_link_id: 1, params_json: "{}", result_json: JSON.stringify(result),
    });
    state.DB.items.set(itemId, {
      id: itemId, platform: "mob_official", status: "ready", review_state: "pending",
      caption: "Before", original_caption: "Before", persian_translation: "",
    });
    const secret = await panel.hookSecret(state);
    const click = button => postPanelUpdate(state, {
      update: { type: "NewMessage", chat_id: "b0actualowner", new_message: {
        message_id: "mob-old-preview", chat_id: "b0actualowner", text: "",
        aux_data: { button_id: button },
      } },
    }, secret);
    await click("p_mob_edit_" + jobId);
    const promptMid = state.DB.state.get("panel_mob_temp_mid");
    assert.ok(promptMid && promptMid !== "mob-x-main-panel-edit");
    assert.equal(state.DB.state.get("panel_mid"), "mob-x-main-panel-edit");
    const prompt = calls.find(call => call.method === "sendMessage");
    assert.ok(prompt, "caption prompt must be a separate temporary message");
    assert.deepEqual(prompt.payload.inline_keypad.rows.flatMap(row => row.buttons).map(button => button.id), ["p_mob_edit_cancel"]);
    assert.equal(calls.filter(call => call.method === "editMessageText").every(call => call.payload.message_id === "mob-x-main-panel-edit"), true);
    await postPanelUpdate(state, {
      update: { type: "NewMessage", chat_id: "b0actualowner", new_message: {
        message_id: "mob-caption-input-001", chat_id: "b0actualowner", text: "Edited caption",
      } },
    }, secret);
    assert.equal(state.DB.state.get("panel_mid"), "mob-x-main-panel-edit");
    assert.equal(state.DB.state.get("panel_mob_temp_mid"), "");
    assert.equal(calls.some(call => call.method === "deleteMessage" && call.payload.message_id === promptMid), true,
      "caption prompt must be deleted after input");
    assert.equal(calls.filter(call => call.method === "sendMessage").length, 1, "caption edit must not create a main panel message");
    assert.equal(calls.filter(call => call.method === "sendFile").length, 1, "video preview remains a separate sendFile message");
    assert.equal(calls.filter(call => call.method === "editMessageText").every(call => call.payload.message_id === "mob-x-main-panel-edit"), true);
  });
}

// Reject, publish and manual-confirm callbacks edit the same main panel and never send a replacement.
{
  for (const [action, status, extra] of [
    ["reject", "ready_for_review", {}],
    ["publish", "approved", {}],
    ["manualconfirm", "uncertain", { delivery_status: "uncertain" }],
  ]) {
    await withMockRubikaFetch(async calls => {
      const state = panelEnv();
      state.DB.ownerChat = "b0actualowner";
      state.DB.state.set("panel_mid", "mob-x-main-" + action);
      const jobId = "job_abcdef1234567890";
      state.DB.consoleJobs.set(jobId, {
        id: jobId, type: "mob_x_link", status, panel_link_id: 1, params_json: "{}",
        result_json: JSON.stringify({ item_id: "mob_official:2107878755079213476", rubika_file_id: "file", preview_sent: true, ...extra }),
      });
      const secret = await panel.hookSecret(state);
      await postPanelUpdate(state, {
        update: { type: "NewMessage", chat_id: "b0actualowner", new_message: {
          message_id: "separate-preview-" + action, chat_id: "b0actualowner", text: "",
          aux_data: { button_id: "p_mob_" + action + "_" + jobId },
        } },
      }, secret);
      assert.equal(state.DB.state.get("panel_mid"), "mob-x-main-" + action, action + " must preserve panel_mid");
      assert.equal(calls.filter(call => call.method === "sendMessage").length, 0, action + " result must not be a new main message");
      const edits = calls.filter(call => call.method === "editMessageText");
      assert.ok(edits.length >= 2, action + " must show loading then result");
      assert.equal(edits.every(call => call.payload.message_id === "mob-x-main-" + action), true);
      assert.match(edits[0].payload.text, /در حال انجام/);
      assert.equal(calls.some(call => call.url && call.url.includes("api.github.com")), false,
        "the mocked publish callback must not dispatch a workflow without a configured token");
    });
  }
}

// A callback from an older temporary link prompt is stale and cannot confirm its pending link.
{
  await withMockRubikaFetch(async calls => {
    const state = panelEnv();
    state.DB.ownerChat = "b0actualowner";
    state.DB.state.set("panel_mid", "mob-main-stale");
    state.DB.state.set("panel_mob_temp_mid", "fresh-link-prompt");
    state.DB.state.set("panel_mob_pending_link_id", "1");
    state.DB.links.push({
      id: 1, url: "https://x.com/mobgamesstudios/status/2107878755079213476",
      external_id: "2107878755079213476", status: "pending",
    });
    const secret = await panel.hookSecret(state);
    await postPanelUpdate(state, {
      update: { type: "NewMessage", chat_id: "b0actualowner", new_message: {
        message_id: "stale-link-prompt", chat_id: "b0actualowner", text: "",
        aux_data: { button_id: "p_mob_confirm" },
      } },
    }, secret);
    assert.equal(state.DB.links[0].status, "pending", "stale confirm must not mutate the saved link");
    assert.equal(state.DB.consoleJobs.size, 0, "stale confirm must not create a job");
    assert.equal(state.DB.state.get("panel_mid"), "mob-main-stale");
    assert.equal(state.DB.state.get("panel_mob_temp_mid"), "fresh-link-prompt");
    assert.equal(calls.some(call => call.method === "deleteMessage" && call.payload.message_id === "stale-link-prompt"), true);
  });
}

// The same action is rejected when the Mob X job has already left review.
{
  await withMockRubikaFetch(async calls => {
    const state = panelEnv();
    state.DB.ownerChat = "b0actualowner";
    const jobId = "job_abcdef1234567890";
    state.DB.consoleJobs.set(jobId, {
      id: jobId, type: "mob_x_link", status: "approved", panel_link_id: 1,
      params_json: "{}", result_json: JSON.stringify({
        item_id: "mob_official:2107878755079213476",
        caption: "original", rubika_file_id: "file", preview_sent: true,
      }),
    });
    const secret = await panel.hookSecret(state);
    await postPanelUpdate(state, {
      update: { type: "NewMessage", chat_id: "b0actualowner", new_message: {
        message_id: "old-preview", chat_id: "b0actualowner", text: "",
        aux_data: { button_id: "p_mob_nocap_" + jobId },
      } },
    }, secret);
    assert.equal(JSON.parse(state.DB.consoleJobs.get(jobId).result_json).caption, "original");
    assert.equal(calls.some(call => call.method === "sendFile"), false);
    assert.equal(calls.some(call => call.url && call.url.includes("api.github.com")), false);
  });
}

// safeEqual compares values without treating differing lengths or Unicode values as equal.
{
  assert.equal(panel.safeEqual("same-value", "same-value"), true);
  assert.equal(panel.safeEqual("same-value", "same-value-extra"), false);
  assert.equal(panel.safeEqual("سلام😀", "سلام😀"), true);
  assert.equal(panel.safeEqual("سلام", "سلامی"), false);
}

// A hook with an incorrect secret is deliberately indistinguishable from a missing route (404).
{
  const state = panelEnv();
  const expected = await panel.hookSecret(state);
  const incorrect = (expected[0] === "0" ? "1" : "0") + expected.slice(1);
  const response = await postPanelUpdate(state, { update: { type: "NewMessage" } }, incorrect);
  assert.equal(response.status, 404);
}

// A wrong claim code never registers an owner.
{
  await withMockRubikaFetch(async calls => {
    const state = panelEnv();
    const secret = await panel.hookSecret(state);
    const response = await postPanelUpdate(state, {
      update: { type: "NewMessage", chat_id: "b0wrongclaim", new_message: {
        message_id: "wrong-1", chat_id: "b0wrongclaim", text: "/claim incorrect-code",
      } },
    }, secret);
    assert.equal(response.status, 200);
    assert.equal(state.DB.ownerChat, null);
    assert.equal(state.DB.state.get("claim_fails"), "1");
    assert.equal(calls.length, 1);
    assert.equal(calls[0].method, "sendMessage");
  });
}

// The correct claim code binds the first private chat and sends the owner keypad.
{
  await withMockRubikaFetch(async calls => {
    const state = panelEnv();
    const secret = await panel.hookSecret(state);
    const response = await postPanelUpdate(state, {
      update: { type: "NewMessage", chat_id: "b0ownerclaim", new_message: {
        message_id: "claim-1", chat_id: "b0ownerclaim", text: "/claim correct-setup-code",
      } },
    }, secret);
    assert.equal(response.status, 200);
    assert.equal(state.DB.ownerChat, "b0ownerclaim");
    assert.equal(calls.some(call => call.method === "sendMessage" && call.payload.inline_keypad), true);
  });
}

// A console invitation is hashed in D1, grants one viewer claim, and cannot be reused.
{
  await withMockRubikaFetch(async calls => {
    const state = panelEnv();
    state.DB.ownerChat = "b0existingowner";
    state.DB.consoleOwners.set(1, {
      id: 1, chat_id: "b0existingowner", label: "مالک اصلی", role: "owner", enabled: 1,
      claimed_at: new Date().toISOString(), last_seen_at: null,
    });
    state.DB.nextConsoleOwnerId = 2;
    const inviteCode = "INV-" + "A".repeat(32);
    const codeHash = createHash("sha256").update(inviteCode).digest("hex");
    state.DB.consoleInvites.set(1, {
      id: 1, code_hash: codeHash, created_at: new Date().toISOString(),
      expires_at: new Date(Date.now() + 3600_000).toISOString(), used_at: null, used_by: null,
    });
    const secret = await panel.hookSecret(state);
    await postPanelUpdate(state, {
      update: { type: "NewMessage", chat_id: "b0invitedviewer", new_message: {
        message_id: "invite-claim-1", chat_id: "b0invitedviewer", text: "/claim " + inviteCode,
      } },
    }, secret);
    const invited = [...state.DB.consoleOwners.values()].find(row => row.chat_id === "b0invitedviewer");
    assert.ok(invited, "invite claim should register a new console owner");
    assert.equal(invited.role, "viewer");
    assert.equal(Number(invited.enabled), 1);
    assert.ok(state.DB.consoleInvites.get(1).used_at);
    assert.equal(state.DB.consoleInvites.get(1).used_by, "b0invitedviewer");
    assert.equal(state.DB.brainEvents.some(event => event.code === "console_owner_claim"), true);
    assert.equal(calls.some(call => call.method === "sendMessage" && String(call.payload.text).includes("دعوت پذیرفته شد")), true);

    await postPanelUpdate(state, {
      update: { type: "NewMessage", chat_id: "b0secondviewer", new_message: {
        message_id: "invite-claim-2", chat_id: "b0secondviewer", text: "/claim " + inviteCode,
      } },
    }, secret);
    assert.equal([...state.DB.consoleOwners.values()].filter(row => row.chat_id.startsWith("b0")).length, 2,
      "one-time invite must not register a second user");
    assert.equal(state.DB.consoleInvites.get(1).used_by, "b0invitedviewer");
  });
}

// Five bad claims permanently lock the claim path for this owner-panel setup.
{
  await withMockRubikaFetch(async calls => {
    const state = panelEnv();
    const secret = await panel.hookSecret(state);
    for (let i = 1; i <= 5; i += 1) {
      await postPanelUpdate(state, {
        update: { type: "NewMessage", chat_id: "b0claimlocked", new_message: {
          message_id: "wrong-" + i, chat_id: "b0claimlocked", text: "/claim incorrect-code",
        } },
      }, secret);
    }
    assert.equal(state.DB.state.get("claim_fails"), "5");
    await postPanelUpdate(state, {
      update: { type: "NewMessage", chat_id: "b0claimlocked", new_message: {
        message_id: "correct-after-lock", chat_id: "b0claimlocked", text: "/claim correct-setup-code",
      } },
    }, secret);
    assert.equal(state.DB.ownerChat, null);
    assert.equal(calls.filter(call => call.method === "sendMessage").length, 5);
  });
}

// A non-owner private chat receives no reply.
{
  await withMockRubikaFetch(async calls => {
    const state = panelEnv();
    state.DB.ownerChat = "b0actualowner";
    const secret = await panel.hookSecret(state);
    await postPanelUpdate(state, {
      update: { type: "NewMessage", chat_id: "b0notowner", new_message: {
        message_id: "non-owner-1", chat_id: "b0notowner", text: "/menu",
      } },
    }, secret);
    assert.equal(calls.length, 0);
  });
}

// A click with no existing panel creates one tracked report; status, queue, and AI are gathered in parallel.
{
  await withMockRubikaFetch(async calls => {
    const state = panelEnv();
    state.DB.ownerChat = "b0actualowner";
    const secret = await panel.hookSecret(state);
    await postPanelUpdate(state, {
      update: { type: "NewMessage", chat_id: "b0actualowner", new_message: {
        message_id: "clicked-event-original-9876", chat_id: "b0actualowner",
        text: "", aux_data: { button_id: "p_report" },
      } },
    }, secret);
    assert.equal(calls.filter(call => call.method === "sendMessage").length, 1);
    assert.equal(state.DB.state.get("panel_mid"), "mock-reply-1");
    assert.equal(state.DB.state.get("panel_edit_ok") || "0", "0");
    assert.equal(state.DB.state.get("panel_edit_fail") || "0", "0");
    assert.equal(calls.some(call => call.method !== "deleteMessage" && call.payload.message_id === "clicked-event-original-9876"), false);
    assert.equal(calls.filter(call => call.method === "editMessageText").length, 0);
    assert.equal(calls.filter(call => call.method === "editMessageKeypad").length, 0);
    assert.equal(calls.some(call => call.method === "deleteMessage" && call.payload.message_id === "clicked-event-original-9876"), true);
    assert.equal(calls.every(call => call.method === "deleteMessage" || !("message_id" in call.payload) || call.payload.message_id === "mock-reply-1"), true);
    const stored = state.DB.state.get("last_event");
    assert.equal(stored.includes("b0actualowner"), false);
    assert.equal(stored.includes("clicked-event-original-9876"), false);
    assert.equal(stored.includes("9876"), true);
  });
}

// Failed edits save only a safe error code and fall back to sending the exact same text and keypad.
{
  await withMockRubikaFetch(async calls => {
    const state = panelEnv();
    state.DB.ownerChat = "b0actualowner";
    state.DB.state.set("panel_mid", "old-live-panel-4321");
    const secret = await panel.hookSecret(state);
    await postPanelUpdate(state, {
      update: { type: "NewMessage", chat_id: "b0actualowner", new_message: {
        message_id: "old-live-panel-4321", chat_id: "b0actualowner", text: "", aux_data: { button_id: "p_status" },
      } },
    }, secret);
    assert.equal(state.DB.state.get("last_error"), "rubika_editmessagetext_edit_failed");
    assert.equal(state.DB.state.get("panel_edit_fail"), "1");
    assert.equal(state.DB.state.get("panel_mid"), "mock-reply-1");
    assert.equal(calls.some(call => call.method === "deleteMessage" && call.payload.message_id === "old-live-panel-4321"), true);
    const failedEdit = calls.find(call => call.method === "editMessageText");
    const fallbackSend = calls.find(call => call.method === "sendMessage");
    assert.equal(failedEdit.payload.text, "⏳ در حال انجام…");
    assert.equal(fallbackSend.payload.text, failedEdit.payload.text);
    assert.equal(Array.isArray(fallbackSend.payload.inline_keypad.rows), true);
    assert.equal(fallbackSend.payload.inline_keypad.rows.some(row => row.buttons && row.buttons.length > 0), true);
    assert.equal(calls.filter(call => call.method === "sendMessage").length, 1);
    assert.equal(calls.filter(call => call.method === "editMessageText").at(-1).payload.text.includes("وضعیت"), true);
    assert.equal(calls.some(call => call.method === "editMessageKeypad" &&
      (!call.payload.inline_keypad.rows.length ||
       !call.payload.inline_keypad.rows.some(row => row.buttons && row.buttons.length > 0))), false);
  }, (() => {
    let first = true;
    return ({ method }) => {
      if (method === "editMessageText" && first) {
        first = false;
        return new Response(JSON.stringify({ status: "EDIT_FAILED" }), { status: 200 });
      }
      return null;
    };
  })());
}

// HTTP failures from the edit API include both the method and HTTP status in last_error.
{
  await withMockRubikaFetch(async calls => {
    const state = panelEnv();
    state.DB.ownerChat = "b0actualowner";
    state.DB.state.set("panel_mid", "old-http-panel-3322");
    const secret = await panel.hookSecret(state);
    await postPanelUpdate(state, {
      update: { type: "NewMessage", chat_id: "b0actualowner", new_message: {
        message_id: "old-http-panel-3322", chat_id: "b0actualowner", text: "", aux_data: { button_id: "p_status" },
      } },
    }, secret);
    assert.equal(state.DB.state.get("last_error"), "rubika_editmessagetext_http_503");
    assert.equal(state.DB.state.get("panel_edit_fail"), "1");
    assert.equal(state.DB.state.get("panel_mid"), "mock-reply-1");
    assert.equal(calls.filter(call => call.method === "sendMessage").length, 1);
    assert.equal(calls.filter(call => call.method === "editMessageText").at(0).payload.text, "⏳ در حال انجام…");
    assert.equal(calls.filter(call => call.method === "editMessageText").at(-1).payload.text.includes("وضعیت"), true);
  }, (() => {
    let first = true;
    return ({ method }) => {
      if (method === "editMessageText" && first) {
        first = false;
        return new Response(JSON.stringify({ status: "UNAVAILABLE" }), { status: 503 });
      }
      return null;
    };
  })());
}

// HTTP rate limiting on an edit falls back for that click to a replacement panel and deletes the old message.
{
  await withMockRubikaFetch(async calls => {
    const state = panelEnv();
    state.DB.ownerChat = "b0actualowner";
    state.DB.state.set("panel_mid", "old-rate-limited-panel-7788");
    const secret = await panel.hookSecret(state);
    await postPanelUpdate(state, {
      update: { type: "NewMessage", chat_id: "b0actualowner", new_message: {
        message_id: "old-rate-limited-panel-7788", chat_id: "b0actualowner",
        text: "", aux_data: { button_id: "p_status" },
      } },
    }, secret);
    const failedEdit = calls.find(call => call.method === "editMessageText");
    const fallbackSend = calls.find(call => call.method === "sendMessage");
    assert.equal(state.DB.state.get("last_error"), "rubika_editmessagetext_http_429");
    assert.equal(calls.filter(call => call.method === "sendMessage").length, 1);
    assert.equal(calls.some(call => call.method === "deleteMessage" && call.payload.message_id === "old-rate-limited-panel-7788"), true);
    assert.equal(state.DB.state.get("panel_mid"), "mock-reply-1");
    assert.equal(fallbackSend.payload.text, failedEdit.payload.text);
    assert.equal(Array.isArray(fallbackSend.payload.inline_keypad.rows), true);
    assert.equal(fallbackSend.payload.inline_keypad.rows.some(row => row.buttons?.length), true);
  }, (() => {
    let first = true;
    return ({ method }) => {
      if (method === "editMessageText" && first) {
        first = false;
        return new Response(JSON.stringify({ status: "RATE_LIMITED" }), { status: 429 });
      }
      return null;
    };
  })());
}

// /debug is owner-only and prints only error codes and last-four-character identifier tails.
{
  await withMockRubikaFetch(async calls => {
    const state = panelEnv();
    state.DB.ownerChat = "b0actualowner";
    state.DB.state.set("last_error", "rubika_editmessagetext_http_503");
    state.DB.state.set("panel_edit_ok", "9");
    state.DB.state.set("panel_edit_fail", "2");
    state.DB.state.set("last_timings", JSON.stringify([
      {
        event_kind: "click", msg_age_s: 1.25, total_ms: 10,
        d1_queries: 7, d1_ms: 3, rubika_calls: 2, rubika_ms: 6,
        worker_api_calls: 1, worker_api_ms: 4, external_calls: 1, external_ms: 2,
      },
    ]));
    const secret = await panel.hookSecret(state);
    await postPanelUpdate(state, {
      update: { type: "NewMessage", chat_id: "b0actualowner", new_message: {
        message_id: "private-debug-message-1234", chat_id: "b0actualowner", text: "/debug",
      } },
    }, secret);
    const stored = state.DB.state.get("last_event");
    assert.equal(stored.includes("b0actualowner"), false);
    assert.equal(stored.includes("private-debug-message-1234"), false);
    assert.equal(stored.includes("/debug"), false);
    assert.equal(stored.includes("wner"), true);
    assert.equal(stored.includes("1234"), true);
    const debugSend = calls.find(call => call.method === "sendMessage");
    assert.equal(debugSend.payload.text.includes("rubika_editmessagetext_http_503"), true);
    assert.equal(debugSend.payload.text.includes("panel_edit_ok: 9"), true);
    assert.equal(debugSend.payload.text.includes("panel_edit_fail: 2"), true);
    assert.equal(debugSend.payload.text.includes('"msg_age_s":1.25'), true);
    assert.equal(debugSend.payload.text.includes('"event_kind":"click"'), true);
    assert.equal(debugSend.payload.text.includes('"d1_queries":7'), true);
    assert.equal(debugSend.payload.text.includes('"rubika_calls":2'), true);
    assert.equal(debugSend.payload.text.includes('"worker_api_calls":1'), true);
    assert.equal(debugSend.payload.text.includes('"external_calls":1'), true);
    assert.equal(debugSend.payload.text.includes("chat_tail: wner"), true);
    assert.equal(debugSend.payload.text.includes("message_id_tail: 1234"), true);
    assert.equal(debugSend.payload.text.includes("b0actualowner"), false);
    assert.equal(debugSend.payload.text.includes("private-debug-message-1234"), false);
  });
}


// Every normal screen click first shows loading, then edits text and keypad on the same message.
{
  await withMockRubikaFetch(async calls => {
    const state = panelEnv();
    state.DB.ownerChat = "b0actualowner";
    state.DB.state.delete("panel_sync_mode");
    state.DB.state.set("panel_mid", "primary-panel-default-001");
    const secret = await panel.hookSecret(state);
    await postPanelUpdate(state, {
      update: { type: "NewMessage", chat_id: "b0actualowner", new_message: {
        message_id: "primary-panel-default-001", chat_id: "b0actualowner",
        text: "", aux_data: { button_id: "p_status" },
      } },
    }, secret);
    assert.equal(calls.filter(call => call.method === "sendMessage").length, 0);
    const textEdits = calls.filter(call => call.method === "editMessageText");
    const keypadEdits = calls.filter(call => call.method === "editMessageKeypad");
    assert.equal(textEdits.length, 2);
    assert.equal(keypadEdits.length, 2);
    assert.equal(textEdits[0].payload.text, "⏳ در حال انجام…");
    assert.notEqual(textEdits[1].payload.text, "⏳ در حال انجام…");
    assert.equal(textEdits.every(call => call.payload.message_id === "primary-panel-default-001"), true);
    assert.equal(keypadEdits.every(call => call.payload.message_id === "primary-panel-default-001"), true);
    assert.equal(keypadEdits[0].payload.inline_keypad.rows.flatMap(row => row.buttons || []).map(button => button.id).join(","), "p_home");
    assert.equal(calls.some(call => call.method === "deleteMessage" && call.payload.message_id === "primary-panel-default-001"), false);
    assert.equal(state.DB.state.get("panel_mid"), "primary-panel-default-001");
    assert.equal(state.DB.state.has("panel_sync_mode"), false);
    // Only the final rendered screen increments panel_edit_ok; the loading edit is excluded.
    assert.equal(state.DB.state.get("panel_edit_ok"), "1");
    assert.equal(state.DB.state.get("atomic_ok") || "0", "0");
    assert.equal(state.DB.state.get("atomic_fail") || "0", "0");
  });
}

// The inactive-link selection page has real glass-button rows, and each click replaces both the checkmark text and keypad.
{
  await withMockRubikaFetch(async calls => {
    const state = panelEnv();
    state.DB.ownerChat = "b0actualowner";
    state.DB.state.set("panel_mid", "inactive-link-select-main");
    state.DB.links = [
      { id: 21, url: "https://x.com/Test/status/2107878755079213476", status: "pending", created_at: "2026-11-03T08:00:00Z" },
      { id: 22, url: "https://x.com/Test/status/2107878755079213477", status: "rejected", created_at: "2026-11-03T08:01:00Z" },
    ];
    const secret = await panel.hookSecret(state);
    const click = button => postPanelUpdate(state, {
      update: { type: "NewMessage", chat_id: "b0actualowner", new_message: {
        message_id: "inactive-link-select-main", chat_id: "b0actualowner", text: "",
        aux_data: { button_id: button },
      } },
    }, secret);

    await click("p_lsel");
    const firstText = calls.filter(call => call.method === "editMessageText");
    const firstKeypad = calls.filter(call => call.method === "editMessageKeypad");
    assert.equal(firstText[0].payload.text, "⏳ در حال انجام…");
    assert.equal(firstText.at(-1).payload.text.includes("انتخاب لینک‌های غیرفعال"), true);
    const firstButtons = firstKeypad.at(-1).payload.inline_keypad.rows.flatMap(row => row.buttons || []).map(button => button.id);
    assert.ok(firstButtons.some(id => id === "p_lsel_1_21"), "first inactive link needs a selectable glass button; actual=" + JSON.stringify({ firstButtons, finalText: firstText.at(-1)?.payload.text, links: state.DB.links }));
    assert.ok(firstButtons.some(id => id === "p_lsel_2_21"), "second inactive link needs a selectable glass button");
    assert.equal(state.DB.state.get("panel_mid"), "inactive-link-select-main");
    assert.equal(calls.filter(call => call.method === "sendMessage").length, 0);

    await click("p_lsel_1_21");
    const finalText = calls.filter(call => call.method === "editMessageText").at(-1);
    const finalButtons = calls.filter(call => call.method === "editMessageKeypad").at(-1).payload.inline_keypad.rows.flatMap(row => row.buttons || []).map(button => button.id);
    assert.equal(finalText.payload.message_id, "inactive-link-select-main");
    assert.equal(finalText.payload.text.includes("☑️ 1)"), true);
    assert.ok(finalButtons.includes("p_lseldel_1_21"), "selecting one row must reveal the matching delete action");
    assert.equal(state.DB.state.get("panel_mid"), "inactive-link-select-main");
    assert.equal(calls.filter(call => call.method === "sendMessage").length, 0);
    assert.equal(calls.some(call => call.method === "sendFile"), false, "selection screens do not create temporary media messages");
    assert.equal(calls.some(call => call.method === "deleteMessage"), false);
  });
}

// A screen-read error is rendered on the same primary message with a safe retry button, not a side message.
{
  await withMockRubikaFetch(async calls => {
    const state = panelEnv();
    state.DB.ownerChat = "b0actualowner";
    state.DB.state.set("panel_mid", "panel-read-error-main");
    state.DB.failPanelReads = true;
    const secret = await panel.hookSecret(state);
    await postPanelUpdate(state, {
      update: { type: "NewMessage", chat_id: "b0actualowner", new_message: {
        message_id: "panel-read-error-main", chat_id: "b0actualowner", text: "",
        aux_data: { button_id: "p_lsel" },
      } },
    }, secret);
    const texts = calls.filter(call => call.method === "editMessageText");
    const keypads = calls.filter(call => call.method === "editMessageKeypad");
    assert.equal(texts.length, 2);
    assert.equal(keypads.length, 2);
    assert.equal(texts[0].payload.text, "⏳ در حال انجام…");
    assert.equal(texts[1].payload.text.includes("❌ نمایش صفحه انجام نشد"), true);
    assert.equal(texts[1].payload.message_id, "panel-read-error-main");
    const retryIds = keypads.at(-1).payload.inline_keypad.rows.flatMap(row => row.buttons || []).map(button => button.id);
    assert.ok(retryIds.includes("p_lsel"));
    assert.ok(retryIds.includes("p_home"));
    assert.equal(calls.filter(call => call.method === "sendMessage").length, 0);
    assert.equal(calls.filter(call => call.method === "deleteMessage").length, 0);
    assert.equal(state.DB.state.get("panel_mid"), "panel-read-error-main");
  });
}

// If the loading edit is rate-limited, one complete replacement is sent and the final page edits that replacement.
{
  await withMockRubikaFetch(async calls => {
    const state = panelEnv();
    state.DB.ownerChat = "b0actualowner";
    state.DB.state.set("panel_mid", "rate-limited-loading-main");
    state.DB.links = [
      { id: 21, url: "https://x.com/Test/status/2107878755079213476", status: "pending", created_at: "2026-11-03T08:00:00Z" },
    ];
    const secret = await panel.hookSecret(state);
    await postPanelUpdate(state, {
      update: { type: "NewMessage", chat_id: "b0actualowner", new_message: {
        message_id: "rate-limited-loading-main", chat_id: "b0actualowner", text: "",
        aux_data: { button_id: "p_lsel" },
      } },
    }, secret);
    const sends = calls.filter(call => call.method === "sendMessage");
    const finalText = calls.filter(call => call.method === "editMessageText").at(-1);
    assert.equal(sends.length, 1);
    assert.equal(sends[0].payload.text, "⏳ در حال انجام…");
    assert.ok(sends[0].payload.inline_keypad.rows.flatMap(row => row.buttons || []).some(button => button.id === "p_home"));
    assert.equal(calls.some(call => call.method === "deleteMessage" && call.payload.message_id === "rate-limited-loading-main"), true);
    assert.equal(state.DB.state.get("panel_mid"), "mock-reply-1");
    assert.equal(finalText.payload.message_id, "mock-reply-1");
    assert.equal(finalText.payload.text.includes("انتخاب لینک‌های غیرفعال"), true);
  }, (() => {
    let first = true;
    return ({ method }) => {
      if (method === "editMessageText" && first) {
        first = false;
        return new Response(JSON.stringify({ status: "RATE_LIMITED" }), { status: 429 });
      }
      return null;
    };
  })());
}

// A newer click supersedes an in-flight loading edit without ever replacing the primary message.
{
  let signalSlowStarted;
  const slowStarted = new Promise(resolve => { signalSlowStarted = resolve; });
  let releaseSlow;
  const slowGate = new Promise(resolve => { releaseSlow = resolve; });
  let editTextCount = 0;
  const responder = async ({ method }) => {
    if (method !== "editMessageText") return null;
    editTextCount += 1;
    if (editTextCount === 1) {
      signalSlowStarted();
      await slowGate;
    }
    return null;
  };
  await withMockRubikaFetch(async calls => {
    const state = panelEnv();
    state.DB.ownerChat = "b0actualowner";
    state.DB.state.set("panel_sync_mode", "atomic"); // Retired value must not switch back to replacement mode.
    state.DB.state.set("panel_mid", "race-base-panel-0101");
    const secret = await panel.hookSecret(state);
    const click = (button, messageId) => ({
      update: { type: "NewMessage", chat_id: "b0actualowner", new_message: {
        message_id: messageId, chat_id: "b0actualowner", text: "", aux_data: { button_id: button },
      } },
    });
    const first = postPanelUpdate(state, click("p_status", "race-base-panel-0101"), secret);
    await slowStarted;
    await postPanelUpdate(state, click("p_qs", "race-base-panel-0101"), secret);
    releaseSlow();
    await first;
    assert.equal(state.DB.state.get("panel_mid"), "race-base-panel-0101");
    assert.equal(calls.filter(call => call.method === "sendMessage").length, 0);
    assert.equal(calls.filter(call => call.method === "deleteMessage").length, 0);
    assert.equal(calls.filter(call => call.method === "editMessageText").every(call =>
      call.payload.message_id === "race-base-panel-0101"), true);
    assert.equal(calls.filter(call => call.method === "editMessageKeypad").every(call =>
      call.payload.message_id === "race-base-panel-0101"), true);
    assert.equal(calls.filter(call => call.method === "editMessageText").at(-1).payload.text.includes("صف انتشار"), true);
    assert.equal(state.DB.state.get("atomic_ok") || "0", "0");
    assert.equal(state.DB.state.get("atomic_fail") || "0", "0");
  }, responder);
}

// A duplicate tap is ignored after one loading/final cycle and preserves the primary message ID.
{
  await withMockRubikaFetch(async calls => {
    const state = panelEnv();
    state.DB.ownerChat = "b0actualowner";
    state.DB.state.set("panel_sync_mode", "atomic");
    state.DB.state.set("panel_mid", "double-tap-panel-0202");
    const secret = await panel.hookSecret(state);
    const update = {
      update: { type: "NewMessage", chat_id: "b0actualowner", new_message: {
        message_id: "double-tap-panel-0202", chat_id: "b0actualowner",
        text: "", aux_data: { button_id: "p_status" },
      } },
    };
    const originalNow = Date.now;
    const originalRandom = Math.random;
    Date.now = () => 1800000000000;
    Math.random = () => 0.99;
    try {
      await postPanelUpdate(state, update, secret);
      await postPanelUpdate(state, update, secret);
    } finally {
      Date.now = originalNow;
      Math.random = originalRandom;
    }
    assert.equal(calls.filter(call => call.method === "sendMessage").length, 0);
    assert.equal(calls.filter(call => call.method === "editMessageText").length, 2);
    assert.equal(calls.filter(call => call.method === "editMessageKeypad").length, 2);
    assert.equal(calls.filter(call => call.method === "editMessageText")[0].payload.text, "⏳ در حال انجام…");
    assert.equal(state.DB.state.get("panel_mid"), "double-tap-panel-0202");
    assert.equal(calls.every(call => call.method === "deleteMessage" ||
      call.payload.message_id === "double-tap-panel-0202"), true);
  });
}

// If both the in-place edit and its fallback delivery fail, the old coherent panel remains tracked.
{
  await withMockRubikaFetch(async calls => {
    const state = panelEnv();
    state.DB.ownerChat = "b0actualowner";
    state.DB.state.set("panel_sync_mode", "atomic");
    state.DB.state.set("panel_mid", "atomic-send-failure-old");
    const secret = await panel.hookSecret(state);
    await postPanelUpdate(state, {
      update: { type: "NewMessage", chat_id: "b0actualowner", new_message: {
        message_id: "atomic-send-failure-old", chat_id: "b0actualowner",
        text: "", aux_data: { button_id: "p_status" },
      } },
    }, secret);
    assert.equal(state.DB.state.get("panel_mid"), "atomic-send-failure-old");
    assert.equal(state.DB.state.get("last_error"), "rubika_editmessagetext_http_503");
    assert.equal(state.DB.state.get("panel_edit_fail"), "1");
    assert.ok(calls.some(call => call.method === "editMessageText"));
    assert.equal(calls.filter(call => call.method === "sendMessage").length, 1,
      "only the failed final-screen fallback send is attempted");
    assert.equal(calls.filter(call => call.method === "editMessageText").at(-1).payload.text.includes("❌ نمایش صفحه انجام نشد"), true);
    const retryIds = calls.filter(call => call.method === "editMessageKeypad").at(-1).payload.inline_keypad.rows
      .flatMap(row => row.buttons || []).map(button => button.id);
    assert.ok(retryIds.includes("p_status"), "the error screen must contain a same-screen retry button");
    assert.equal(calls.some(call => call.method === "deleteMessage" && call.payload.message_id === "atomic-send-failure-old"), false);
  }, (() => {
    let textEdits = 0;
    return ({ method }) => {
      if (method === "editMessageText") {
        textEdits += 1;
        if (textEdits === 2) return new Response(JSON.stringify({ status: "UNAVAILABLE" }), { status: 503 });
      }
      if (method === "sendMessage") return new Response(JSON.stringify({ status: "SEND_FAILED" }), { status: 200 });
      return null;
    };
  })());
}

// The retired atomic setting is ignored; successful navigation edits the main text and keypad in place.
{
  await withMockRubikaFetch(async calls => {
    const state = panelEnv();
    state.DB.ownerChat = "b0actualowner";
    state.DB.state.set("panel_sync_mode", "atomic");
    state.DB.state.set("panel_mid", "atomic-delete-failure-old");
    const secret = await panel.hookSecret(state);
    await postPanelUpdate(state, {
      update: { type: "NewMessage", chat_id: "b0actualowner", new_message: {
        message_id: "atomic-delete-failure-old", chat_id: "b0actualowner",
        text: "", aux_data: { button_id: "p_status" },
      } },
    }, secret);
    assert.equal(state.DB.state.get("panel_mid"), "atomic-delete-failure-old");
    assert.equal(calls.filter(call => call.method === "sendMessage").length, 0);
    assert.equal(calls.filter(call => call.method === "deleteMessage").length, 0);
    assert.equal(calls.filter(call => call.method === "editMessageText").length, 2);
    assert.equal(calls.filter(call => call.method === "editMessageKeypad").length, 2);
    assert.equal(calls.filter(call => call.method === "editMessageText")[0].payload.text, "⏳ در حال انجام…");
    assert.equal(state.DB.state.get("atomic_ok") || "0", "0");
    assert.equal(state.DB.state.get("atomic_fail") || "0", "0");
  }, ({ method }) => method === "deleteMessage"
    ? new Response(JSON.stringify({ status: "DELETE_FAILED" }), { status: 200 })
    : null);
}

// /panelmode reports the enforced in-place policy; only the primary owner may inspect it.
{
  await withMockRubikaFetch(async calls => {
    const state = panelEnv();
    state.DB.ownerChat = "b0actualowner";
    state.DB.state.set("panel_mid", "panelmode-old-panel");
    const secret = await panel.hookSecret(state);
    await postPanelUpdate(state, {
      update: { type: "NewMessage", chat_id: "b0actualowner", new_message: {
        message_id: "panelmode-command-1", chat_id: "b0actualowner", text: "/panelmode atomic",
      } },
    }, secret);
    assert.equal(state.DB.state.get("panel_sync_mode"), "inplace");
    assert.equal(state.DB.state.get("panel_mid"), "panelmode-old-panel");
    assert.equal(calls.filter(call => call.method === "sendMessage").length, 0);
    assert.equal(calls.filter(call => call.method === "editMessageText").length, 1);
    assert.equal(calls.filter(call => call.method === "editMessageKeypad").length, 1);
    assert.equal(calls.some(call => call.method === "editMessageText" &&
      call.payload.text.includes("atomic غیرفعال است")), true);
    assert.equal(calls.filter(call => call.method === "deleteMessage").length, 0);
  });
  await withMockRubikaFetch(async calls => {
    const state = panelEnv();
    state.DB.ownerChat = "b0actualowner";
    state.DB.consoleOwners.set(2, { id: 2, chat_id: "b0viewer", role: "viewer", enabled: 1 });
    state.DB.state.set("panel_sync_mode", "atomic");
    state.DB.state.set("panel_mid", "primary-owner-panel-stays");
    const secret = await panel.hookSecret(state);
    await postPanelUpdate(state, {
      update: { type: "NewMessage", chat_id: "b0viewer", new_message: {
        message_id: "viewer-panelmode-1", chat_id: "b0viewer", text: "/panelmode inplace",
      } },
    }, secret);
    assert.equal(state.DB.state.get("panel_sync_mode"), "atomic");
    assert.equal(state.DB.state.get("panel_mid"), "primary-owner-panel-stays");
    assert.equal(calls.filter(call => call.method === "sendMessage").length, 1);
    assert.equal(calls[0].payload.text.includes("نقش مالک"), true);
  });
}

// A stale callback is rejected and refreshes the currently tracked primary message in place.
{
  await withMockRubikaFetch(async calls => {
    const state = panelEnv();
    state.DB.ownerChat = "b0actualowner";
    state.DB.state.set("panel_sync_mode", "atomic");
    state.DB.state.set("panel_mid", "latest-live-panel-9876");
    state.DB.state.set("panel_mob_pending_link_id", "1");
    state.DB.links.push({
      id: 1, url: "https://x.com/mobgamesstudios/status/2107878755079213476",
      external_id: "2107878755079213476", status: "pending", created_at: new Date().toISOString(),
      confirmed_at: null, updated_at: new Date().toISOString(),
    });
    state.DB.nextPanelLinkId = 2;
    const secret = await panel.hookSecret(state);
    await postPanelUpdate(state, {
      update: { type: "NewMessage", chat_id: "b0actualowner", new_message: {
        message_id: "stale-click-message-1234", chat_id: "b0actualowner",
        text: "", aux_data: { button_id: "p_mob_confirm" },
      } },
    }, secret);
    assert.equal(state.DB.links[0].status, "pending", "stale confirm must not mutate the link");
    assert.equal(state.DB.consoleJobs.size, 0, "stale confirm must not create a job");
    assert.equal(state.DB.state.get("panel_mob_pending_link_id"), "1");
    assert.equal(state.DB.state.get("panel_mid"), "latest-live-panel-9876");
    assert.equal(calls.filter(call => call.method === "sendMessage").length, 0);
    assert.equal(calls.some(call => call.method === "deleteMessage" && call.payload.message_id === "stale-click-message-1234"), true);
    assert.equal(calls.some(call => call.method === "deleteMessage" && call.payload.message_id === "latest-live-panel-9876"), false);
    assert.equal(calls.filter(call => call.method === "editMessageText")[0].payload.text, "⏳ در حال انجام…");
    assert.equal(calls.filter(call => call.method === "editMessageText").every(call =>
      call.payload.message_id === "latest-live-panel-9876"), true);
  });
}

// editMessageText is completed before editMessageKeypad starts.
{
  await withMockRubikaFetch(async calls => {
    const state = panelEnv();
    state.DB.ownerChat = "b0actualowner";
    state.DB.state.set("panel_mid", "ordered-live-panel-2468");
    const secret = await panel.hookSecret(state);
    await postPanelUpdate(state, {
      update: { type: "NewMessage", chat_id: "b0actualowner", new_message: {
        message_id: "ordered-live-panel-2468", chat_id: "b0actualowner",
        text: "", aux_data: { button_id: "p_links" },
      } },
    }, secret);
    const textIndex = calls.findIndex(call => call.method === "editMessageText");
    const keypadIndex = calls.findIndex(call => call.method === "editMessageKeypad");
    assert.equal(textIndex >= 0, true);
    assert.equal(keypadIndex > textIndex, true);
    assert.equal(state.DB.state.get("panel_edit_ok"), "2");
    assert.equal(calls.filter(call => call.method === "editMessageText")[0].payload.text, "⏳ در حال انجام…");
  });
}

// A keypad edit failure is retried once; a second failure falls back to a new message with buttons.
{
  await withMockRubikaFetch(async calls => {
    const state = panelEnv();
    state.DB.ownerChat = "b0actualowner";
    state.DB.state.set("panel_mid", "old-keypad-panel-1357");
    const secret = await panel.hookSecret(state);
    await postPanelUpdate(state, {
      update: { type: "NewMessage", chat_id: "b0actualowner", new_message: {
        message_id: "old-keypad-panel-1357", chat_id: "b0actualowner",
        text: "", aux_data: { button_id: "p_links" },
      } },
    }, secret);
    const textIndexes = calls.map((call, index) => call.method === "editMessageText" ? index : -1).filter(index => index >= 0);
    const keypadIndexes = calls.map((call, index) => call.method === "editMessageKeypad" ? index : -1).filter(index => index >= 0);
    const sendIndex = calls.findIndex(call => call.method === "sendMessage");
    assert.equal(keypadIndexes.length, 3, "loading keypad failure retries once, then the final keyboard is installed");
    assert.equal(textIndexes.length, 2);
    assert.equal(keypadIndexes[0] > textIndexes[0], true);
    assert.equal(keypadIndexes[1] > keypadIndexes[0], true);
    assert.equal(sendIndex > keypadIndexes[1], true);
    assert.equal(state.DB.state.get("last_error"), "rubika_editmessagekeypad_edit_failed");
    assert.equal(state.DB.state.get("panel_edit_fail"), "1");
    assert.equal(state.DB.state.get("panel_mid"), "mock-reply-1");
    const fallback = calls[sendIndex];
    assert.equal(fallback.payload.text, "⏳ در حال انجام…");
    assert.equal(Array.isArray(fallback.payload.inline_keypad.rows), true);
    assert.equal(fallback.payload.inline_keypad.rows.some(row => row.buttons && row.buttons.length > 0), true);
    assert.equal(textIndexes.every(index => calls[index].payload.message_id === "mock-reply-1"), false,
      "the loading edit targets the old panel; the final text edits the replacement");
    assert.equal(calls.some(call => call.method === "deleteMessage" && call.payload.message_id === "old-keypad-panel-1357"), true);
  }, (() => {
    let fails = 2;
    return ({ method }) => {
      if (method === "editMessageKeypad" && fails > 0) {
        fails -= 1;
        return new Response(JSON.stringify({ status: "EDIT_FAILED" }), { status: 200 });
      }
      return null;
    };
  })());
}

// p_home still renders its fallback text and main-menu buttons when the read-only D1 batch fails.
{
  await withMockRubikaFetch(async calls => {
    const state = panelEnv();
    state.DB.ownerChat = "b0actualowner";
    state.DB.failReadOnlyBatch = true;
    const secret = await panel.hookSecret(state);
    await postPanelUpdate(state, {
      update: { type: "NewMessage", chat_id: "b0actualowner", new_message: {
        message_id: "home-d1-failure-1234", chat_id: "b0actualowner",
        text: "", aux_data: { button_id: "p_home" },
      } },
    }, secret);
    const displayed = calls.find(call => call.method === "sendMessage");
    assert.equal(displayed.payload.text.includes("پنل مدیریت Rubika-Agent"), true);
    assert.equal(displayed.payload.text.includes("صف:"), false);
    assert.equal(displayed.payload.inline_keypad.rows.some(row =>
      row.buttons && row.buttons.some(button => button.id === "p_home" && button.button_text === "🏠 منوی اصلی")), true);
  });
}

// A typed page command creates a replacement, updates panel_mid, then best-effort deletes the previous panel.
{
  await withMockRubikaFetch(async calls => {
    const state = panelEnv();
    state.DB.ownerChat = "b0actualowner";
    state.DB.state.set("panel_mid", "old-panel-to-delete-5678");
    const secret = await panel.hookSecret(state);
    await postPanelUpdate(state, {
      update: { type: "NewMessage", chat_id: "b0actualowner", new_message: {
        message_id: "menu-command-1", chat_id: "b0actualowner", text: "/menu",
      } },
    }, secret);
    assert.equal(calls.filter(call => call.method === "sendMessage").length, 1);
    assert.equal(calls.some(call => call.method === "deleteMessage" && call.payload.message_id === "old-panel-to-delete-5678"), true);
    assert.equal(state.DB.state.get("panel_mid"), "mock-reply-1");
  }, ({ method }) => method === "deleteMessage"
    ? new Response(JSON.stringify({ status: "DELETE_FAILED" }), { status: 200 })
    : null);
}


// Non-report screen callbacks first show loading, then replace both text and keypad on the same main message.
{
  await withMockRubikaFetch(async calls => {
    const state = panelEnv();
    state.DB.ownerChat = "b0actualowner";
    state.DB.state.set("panel_mid", "existing-live-status-4321");
    const secret = await panel.hookSecret(state);
    await postPanelUpdate(state, {
      update: { type: "NewMessage", chat_id: "b0actualowner", new_message: {
        message_id: "existing-live-status-4321", chat_id: "b0actualowner",
        text: "", aux_data: { button_id: "p_status" },
      } },
    }, secret);
    const textEdits = calls.filter(call => call.method === "editMessageText");
    const keypadEdits = calls.filter(call => call.method === "editMessageKeypad");
    assert.equal(calls.filter(call => call.method === "sendMessage").length, 0);
    assert.equal(textEdits.length, 2);
    assert.equal(keypadEdits.length, 2);
    assert.equal(textEdits[0].payload.text, "⏳ در حال انجام…");
    assert.equal(textEdits[1].payload.text.includes("وضعیت"), true);
    assert.equal(textEdits.every(call => call.payload.message_id === "existing-live-status-4321"), true);
    assert.equal(keypadEdits.every(call => call.payload.message_id === "existing-live-status-4321"), true);
    assert.equal(state.DB.state.get("panel_mid"), "existing-live-status-4321");
    assert.equal(state.DB.state.get("panel_edit_ok"), "2");
  });
}

// The last five owner events retain numeric-only timing records with a numeric message timestamp.
{
  await withMockRubikaFetch(async calls => {
    const state = panelEnv();
    state.DB.ownerChat = "b0actualowner";
    state.DB.state.set("panel_mid", "older-live-panel");
    const secret = await panel.hookSecret(state);
    for (let i = 1; i <= 6; i += 1) {
      await postPanelUpdate(state, {
        update: { type: "NewMessage", chat_id: "b0actualowner", new_message: {
          message_id: "timing-event-" + i, chat_id: "b0actualowner", text: "/menu",
          time: Math.floor(Date.now() / 1000) - 2,
        } },
      }, secret);
    }
    const history = JSON.parse(state.DB.state.get("last_timings"));
    assert.equal(history.length, 5);
    for (const row of history) {
      assert.equal(typeof row.msg_age_s, "number");
      assert.equal(Number.isFinite(row.msg_age_s), true);
      assert.equal(typeof row.total_ms, "number");
      assert.equal(typeof row.rubika_calls, "number");
      assert.equal(typeof row.event_kind, "string");
      assert.equal(typeof row.d1_queries, "number");
      assert.equal(typeof row.rubika_ms, "number");
      assert.equal(typeof row.d1_ms, "number");
      assert.equal(typeof row.worker_api_calls, "number");
      assert.equal(typeof row.worker_api_ms, "number");
      assert.equal(typeof row.external_calls, "number");
      assert.equal(typeof row.external_ms, "number");
      assert.equal(row.event_kind, "text");
      assert.equal(row.d1_queries >= 1, true);
      assert.equal(row.rubika_calls, 2);
      assert.equal(row.total_ms >= 0, true);
      assert.equal(row.rubika_ms >= 0, true);
      assert.equal(row.d1_ms >= 0, true);
      assert.deepEqual(Object.keys(row).sort(), [
        "d1_ms", "d1_queries", "event_kind", "external_calls", "external_ms",
        "msg_age_s", "rubika_calls", "rubika_ms", "total_ms",
        "worker_api_calls", "worker_api_ms",
      ]);
    }
    assert.equal(calls.filter(call => call.method === "deleteMessage").length, 6);
  });
}

// Missing or nonnumeric message time is saved as null, without exposing timing details outside the owner panel.
{
  await withMockRubikaFetch(async calls => {
    const state = panelEnv();
    state.DB.ownerChat = "b0actualowner";
    const secret = await panel.hookSecret(state);
    await postPanelUpdate(state, {
      update: { type: "NewMessage", chat_id: "b0actualowner", new_message: {
        message_id: "timing-no-time-1", chat_id: "b0actualowner", text: "/menu", time: "not-numeric",
      } },
    }, secret);
    const history = JSON.parse(state.DB.state.get("last_timings"));
    assert.equal(history.at(-1).msg_age_s, null);
  });
}


// Stage-two panel screen D1 mocks exercise the real screen module without a live database.
class ScreenFakeStatement {
  constructor(db, sql) { this.db = db; this.sql = sql; this.values = []; }
  bind(...values) { this.values = values; return this; }
  check() { this.db.queryCount += 1; if (this.db.failReads) throw new Error("mock D1 unavailable"); }
  async first() {
    this.check();
    if (this.sql.includes("FROM owner_digest_state")) return this.db.digestState ? { ...this.db.digestState } : null;
    if (this.sql.includes("SELECT COUNT(*) AS c FROM automation_queue WHERE status='sent'")) {
      const day = new Date().toISOString().slice(0, 10);
      return { c: this.db.queue.filter(row => row.status === "sent" && String(row.sent_at || "").slice(0, 10) === day).length };
    }
    if (this.sql.includes("SELECT COUNT(*) AS c FROM automation_queue WHERE status=?")) {
      return { c: this.db.queue.filter(row => row.status === this.values[0]).length };
    }
    if (this.sql.includes("SELECT COUNT(*) AS c FROM panel_links WHERE status NOT IN")) {
      const active = new Set(["confirmed", "queued", "processing", "ready_for_review", "approved", "sending"]);
      return { c: this.db.links.filter(row => !active.has(row.status)).length };
    }
    if (this.sql.includes("SELECT COUNT(*) AS c FROM panel_links")) return { c: this.db.links.length };
    return null;
  }
  async all() {
    this.check();
    if (this.sql.includes("FROM owner_digest_events WHERE window_id=? GROUP BY category")) {
      const rows = this.db.digestEvents.filter(row => row.window_id === this.values[0]);
      const totals = new Map();
      for (const row of rows) totals.set(row.category, (totals.get(row.category) || 0) + 1);
      return { results: [...totals.entries()].map(([category, c]) => ({ category, c })) };
    }
    if (this.sql.includes("FROM owner_digest_events WHERE window_id=? ORDER BY id DESC LIMIT 8")) {
      return { results: this.db.digestEvents.filter(row => row.window_id === this.values[0])
        .sort((a, b) => b.id - a.id).slice(0, 8).map(row => ({ ...row })) };
    }
    if (this.sql.includes("FROM owner_digest_events WHERE category IN")) {
      return { results: this.db.digestEvents.filter(row => ["error", "ai_error"].includes(row.category))
        .sort((a, b) => b.id - a.id).slice(0, 8).map(row => ({ ...row })) };
    }
    if (this.sql.includes("FROM brain_events")) {
      return { results: this.db.brainEvents.filter(row => ["error", "critical"].includes(String(row.level).toLowerCase()))
        .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at))).slice(0, 5).map(row => ({ ...row })) };
    }
    if (this.sql.includes("FROM runs ORDER BY started_at DESC")) {
      return { results: this.db.runs.slice().sort((a, b) => String(b.started_at).localeCompare(String(a.started_at)))
        .slice(0, 6).map(row => ({ ...row })) };
    }
    if (this.sql.includes("FROM automation_queue GROUP BY status")) {
      const totals = new Map();
      for (const row of this.db.queue) totals.set(row.status, (totals.get(row.status) || 0) + 1);
      return { results: [...totals.entries()].map(([status, c]) => ({ status, c })) };
    }
    if (this.sql.includes("FROM automation_queue WHERE status=? ORDER BY updated_at DESC")) {
      const status = this.values[0], limit = this.values[1], offset = this.values[2];
      return { results: this.db.queue.filter(row => row.status === status)
        .sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at)))
        .slice(offset, offset + limit).map(row => ({ ...row })) };
    }
    if (this.sql.includes("FROM ai_provider_calls ORDER BY started_at DESC")) {
      return { results: this.db.aiCalls.slice().sort((a, b) => String(b.started_at).localeCompare(String(a.started_at)))
        .slice(0, 8).map(row => ({ ...row })) };
    }
    if (this.sql.includes("FROM panel_links ORDER BY id DESC")) {
      return { results: this.db.links.slice().reverse().slice(0, 8).map(row => ({ ...row })) };
    }
    return { results: [] };
  }
  async run() {
    this.check();
    if (this.sql.startsWith("DELETE FROM panel_links WHERE status NOT IN")) {
      const active = new Set(["confirmed", "queued", "processing", "ready_for_review", "approved", "sending"]);
      const before = this.db.links.length;
      this.db.links = this.db.links.filter(row => active.has(row.status));
      return { meta: { changes: before - this.db.links.length } };
    }
    return { meta: { changes: 1 } };
  }
}
class ScreenFakeDB {
  constructor(seed = {}) {
    this.digestState = seed.digestState || null;
    this.digestEvents = seed.digestEvents || [];
    this.brainEvents = seed.brainEvents || [];
    this.runs = seed.runs || [];
    this.queue = seed.queue || [];
    this.aiCalls = seed.aiCalls || [];
    this.links = seed.links || [];
    this.failReads = seed.failReads === true;
    this.queryCount = 0;
  }
  prepare(sql) { return new ScreenFakeStatement(this, sql); }
}
const screenEnv = (seed = {}) => ({ DB: new ScreenFakeDB(seed) });
const screenStatuses = ["queued", "processing", "sent", "failed", "uncertain", "skipped"];
{
  assert.equal(screens.clean("**Bold** __italic__ ||spoiler||", 100), "Bold italic spoiler");
  assert.equal(screens.when("2026-10-24T00:00:00Z"), "10/24 03:30");
  assert.equal(screens.isScreenId("p_ql_queued_0"), true);
  assert.equal(screens.isScreenId("p_ql_sent_12"), true);
}
{
  const emptyPages = [
    ["p_digest", "خلاصهٔ ۲۴ ساعت"], ["p_errors", "خطاها"], ["p_runs", "آخرین اجراها"],
    ["p_qs", "صف انتشار"], ["p_ailog", "تاریخچهٔ تماس‌های AI"], ["p_links", "لینک‌های X"],
    ["p_jobs", "کارهای کنسول"], ["p_links_clear", "لینک غیرفعالی برای پاک‌کردن نیست."],
  ];
  for (const [id, phrase] of emptyPages) {
    const result = await screens.buildScreen(screenEnv(), id);
    assert.equal(result.text.includes(phrase), true, id + " should render when tables are empty");
    assert.equal(result.text.includes("d1_error"), false, id + " should not report an error on empty tables");
  }
}
{
  const today = new Date().toISOString().slice(0, 10);
  const seed = {
    digestState: { window_id: "window-1", window_started_at: "2026-10-24T00:00:00Z", window_ends_at: "2026-10-25T00:00:00Z" },
    digestEvents: [
      { id: 1, window_id: "window-1", category: "error", occurred_at: "2026-10-24T00:00:00Z", source: "worker", title: "**Digest title**", detail: "||safe detail||" },
      { id: 2, window_id: "window-1", category: "ai_success", occurred_at: "2026-10-24T01:00:00Z", source: "ai", title: "AI completed", detail: null },
      { id: 3, window_id: "old-window", category: "error", occurred_at: "2026-10-23T00:00:00Z", source: "old", title: "Old event", detail: null },
    ],
    brainEvents: [{ level: "error", code: "worker_failed", created_at: "2026-10-24T02:00:00Z" }],
    runs: [{ trigger_type: "daily", status: "success", started_at: "2026-10-24T03:00:00Z", found_count: 4, eligible_count: 3, sent_count: 2, skipped_count: 1, failed_count: 0 }],
    queue: screenStatuses.map((status, i) => ({ status, platform: "rubika", title: "Queue_" + status, last_error_code: status === "failed" ? "network_error" : null, updated_at: "2026-10-24T0" + i + ":00:00Z", sent_at: status === "sent" ? today + "T09:00:00Z" : null })),
    aiCalls: [{ provider: "groq", role: "code_repair", status: "failed", error_code: "http_5xx", started_at: "2026-10-24T04:00:00Z" }],
    links: [
      { id: 1, url: "https://x.com/mobgamesstudios/status/2107878755079213476", external_id: "2107878755079213476", status: "confirmed", created_at: "2026-10-24T05:00:00Z" },
      { id: 2, url: "https://x.com/mobgamesstudios/status/2107878755079213477", external_id: "2107878755079213477", status: "pending", created_at: "2026-10-24T06:00:00Z" },
    ],
  };
  const seeded = screenEnv(seed);
  for (const [id, phrase] of [
    ["p_digest", "Digest title"], ["p_errors", "Digest title"], ["p_runs", "success"],
    ["p_qs", "در انتظار: 1"], ["p_ailog", "groq/code_repair"], ["p_links", "mobgamesstudios"],
    ["p_links_clear", "🗑 1 لینک غیرفعال پاک شود؟"],
  ]) {
    const result = await screens.buildScreen(seeded, id);
    assert.equal(result.text.includes(phrase), true, id + " should show seeded data");
    assert.equal(result.text.includes("d1_error"), false, id + " should not fail with seeded data");
  }
  const linksScreen = await screens.buildScreen(seeded, "p_links");
  assert.equal(linksScreen.rows.flat().some(button => button[1] === "p_links_clear" && button[0] === "🗑 پاک‌کردن غیرفعال‌ها (1)"), true,
    "clear button must count inactive links only");
  const activeOnly = screenEnv({ links: [{ id: 10, status: "queued" }, { id: 11, status: "ready_for_review" }] });
  const activeOnlyScreen = await screens.buildScreen(activeOnly, "p_links");
  assert.equal(activeOnlyScreen.rows.flat().some(button => button[1] === "p_links_clear"), false,
    "clear button is hidden when every link is active");
  const clearScreenEnv = screenEnv({ links: [{ id: 20, status: "queued" }, { id: 21, status: "pending" }, { id: 22, status: "approved" }] });
  const clearedScreen = await screens.buildScreen(clearScreenEnv, "p_links_clear_yes");
  assert.equal(clearedScreen.text, "✅ 1 لینک غیرفعال پاک شد؛ 2 لینک فعال حفظ شد.");
  assert.deepEqual(clearScreenEnv.DB.links.map(row => row.status).sort(), ["approved", "queued"]);
  const aiLogWithCode = await screens.buildScreen(seeded, "p_ailog");
  assert.equal(aiLogWithCode.text.includes("http_5xx"), true, "AI history should show the precise error code");

  for (const status of screenStatuses) {
    const id = "p_ql_" + status + "_0";
    assert.equal(screens.isScreenId(id), true, id + " should be recognized");
    const result = await screens.buildScreen(screenEnv(seed), id);
    assert.equal(result.text.includes("Queue_" + status), true, id + " should show items for its allowed status");
  }
  const invalidDb = new ScreenFakeDB({ queue: seed.queue });
  const invalidPage = await screens.buildScreen({ DB: invalidDb }, "p_ql_unknown_0");
  assert.equal(invalidPage.text.includes("گزینهٔ نامعتبر"), true);
  assert.equal(invalidDb.queryCount, 0, "invalid queue status must not query D1");
}
{
  const ids = ["p_digest", "p_errors", "p_runs", "p_qs", "p_ailog", "p_links", "p_jobs", "p_links_clear", "p_links_clear_yes"]
    .concat(screenStatuses.map(status => "p_ql_" + status + "_0"));
  for (const id of ids) {
    const result = await screens.buildScreen(screenEnv({ failReads: true }), id);
    assert.equal(result.text.includes("d1_error"), true, id + " should turn D1 failures into d1_error");
  }
  const statusText = panel.fmtStatus({ ok: true, agent: { enabled: 1 }, capabilities: { publishing: false }, item_counts: [], recent_runs: [] });
  assert.equal(statusText.includes("پایگاه داده"), false);
}

// Slash commands are text input, not glass-button callbacks; the PV button path itself keeps a stable message.
{
  await withMockRubikaFetch(async calls => {
    const state = panelEnv();
    state.DB.ownerChat = "b0actualowner";
    state.DB.state.set("panel_mid", "one-live-screen-panel");
    state.DB.state.set("mode", "waiting_link");
    const secret = await panel.hookSecret(state);
    const commands = [
      ["/digest", "خلاصهٔ ۲۴ ساعت"], ["/errors", "خطاها"], ["/runs", "آخرین اجراها"],
      ["/links", "لینک‌های X"], ["/ailog", "تاریخچهٔ تماس‌های AI"], ["/queue", "صف انتشار"],
    ];
    let expectedSends = 0;
    for (let i = 0; i < commands.length; i += 1) {
      await postPanelUpdate(state, {
        update: { type: "NewMessage", chat_id: "b0actualowner", new_message: {
          message_id: "stage-two-command-" + i, chat_id: "b0actualowner", text: commands[i][0],
        } },
      }, secret);
      const sendCalls = calls.filter(call => call.method === "sendMessage");
      if (["/digest", "/errors", "/runs", "/links"].includes(commands[i][0])) {
        expectedSends += 1;
        assert.equal(sendCalls.length, expectedSends, "these commands intentionally send a fresh report page");
        assert.equal(sendCalls.at(-1).payload.text.includes(commands[i][1]), true);
      } else {
        assert.equal(sendCalls.length, expectedSends, "these commands edit the existing main panel");
        assert.equal(calls.filter(call => call.method === "editMessageText").at(-1).payload.text.includes(commands[i][1]), true);
      }
    }
    assert.equal(state.DB.state.get("mode"), "idle");
  });
}

// Link deletion confirmation edits the same main panel; a stale/direct confirmation never deletes.
{
  await withMockRubikaFetch(async calls => {
    const state = panelEnv();
    state.DB.ownerChat = "b0actualowner";
    state.DB.links = [
      { id: 1, url: "https://x.com/Test/status/2107878755079213476", status: "queued", created_at: "2026-10-24T05:00:00Z" },
      { id: 2, url: "https://x.com/Test/status/2107878755079213477", status: "pending", created_at: "2026-10-24T05:01:00Z" },
      { id: 3, url: "https://x.com/Test/status/2107878755079213478", status: "sent", created_at: "2026-10-24T05:02:00Z" },
      { id: 4, url: "https://x.com/Test/status/2107878755079213479", status: "ready_for_review", created_at: "2026-10-24T05:03:00Z" },
    ];
    state.DB.state.set("panel_sync_mode", "atomic");
    state.DB.state.set("panel_mid", "link-confirmation-panel");
    const secret = await panel.hookSecret(state);
    const click = button => postPanelUpdate(state, {
      update: { type: "NewMessage", chat_id: "b0actualowner", new_message: {
        message_id: "link-confirmation-panel", chat_id: "b0actualowner", text: "", aux_data: { button_id: button },
      } },
    }, secret);
    await click("p_links_clear_yes");
    assert.equal(state.DB.links.length, 4, "direct confirmation without a pending intent must not delete links");
    assert.equal(state.DB.linkDeleteCalls, 0);
    assert.equal(state.DB.state.get("panel_mid"), "link-confirmation-panel");
    await click("p_links_clear");
    assert.equal(state.DB.links.length, 4, "opening confirmation must not delete links");
    assert.equal(calls.filter(call => call.method === "editMessageText").at(-1).payload.text.includes("🗑 2 لینک غیرفعال پاک شود؟"), true);
    assert.equal(state.DB.state.get("panel_mid"), "link-confirmation-panel");
    assert.equal(calls.filter(call => call.method === "sendMessage").length, 0);
    assert.equal(calls.filter(call => call.method === "editMessageText").length, 4, "two clicks each render loading then final text");
    assert.equal(calls.filter(call => call.method === "editMessageKeypad").length, 4);
    assert.equal(calls.filter(call => call.method === "deleteMessage").length, 0);
  });
}

// PV settings keep the same tracked panel and show loading before D1 writes/reads.
{
  assert.equal(screens.HOME_ROWS.flat().some(([label, id]) => label === "⚙️ تنظیمات" && id === "p_set"), true,
    "owner home menu must include the settings entry");

  await withMockRubikaFetch(async calls => {
    const state = panelEnv();
    state.DB.ownerChat = "b0actualowner";
    state.DB.state.set("panel_sync_mode", "atomic"); // Old stored mode must not re-enable replacement behavior.
    state.DB.state.set("panel_mid", "settings-panel-initial");
    const secret = await panel.hookSecret(state);
    const click = async button => {
      const mid = state.DB.state.get("panel_mid");
      await postPanelUpdate(state, {
        update: { type: "NewMessage", chat_id: "b0actualowner", new_message: {
          message_id: mid, chat_id: "b0actualowner", text: "", aux_data: { button_id: button },
        } },
      }, secret);
    };
    const rules = () => JSON.parse(state.DB.consoleRules.rules_json);

    await click("p_set_cap_inc");
    assert.equal(rules().daily_success_cap, 5);
    assert.equal(state.DB.consoleRules.version, 2);
    assert.equal(rules().publishing_enabled, false);
    assert.equal(rules().test_channel_only, true);

    await click("p_set_cap_dec");
    assert.equal(rules().daily_success_cap, 4);
    assert.equal(state.DB.consoleRules.version, 3);

    await click("p_set_cap_7");
    assert.equal(rules().daily_success_cap, 7);
    assert.equal(state.DB.consoleRules.version, 4);
    assert.equal(rules().publishing_enabled, false);
    assert.equal(rules().test_channel_only, true);
    assert.deepEqual(state.DB.brainEvents.map(event => event.code), [
      "console_rules_update", "console_rules_update", "console_rules_update",
    ]);
    const details = JSON.parse(state.DB.brainEvents[0].details_json);
    assert.deepEqual(details.fields, ["daily_success_cap"]);
    assert.equal(details.source, "pv");
    const textEdits = calls.filter(call => call.method === "editMessageText");
    const keypadEdits = calls.filter(call => call.method === "editMessageKeypad");
    assert.equal(calls.filter(call => call.method === "sendMessage").length, 0);
    assert.equal(textEdits.length, 6, "each settings button has loading and final text");
    assert.equal(keypadEdits.length, 6, "each settings button replaces loading and final keyboard");
    assert.equal(textEdits[0].payload.text, "⏳ در حال انجام…");
    assert.equal(textEdits.at(-1).payload.text.includes("تیک‌تاک: بدون سقف"), true);
    assert.equal(textEdits.at(-1).payload.text.includes("Mob X: معاف از سقف و ساعت سکوت"), true);
    assert.equal(textEdits.at(-1).payload.text.includes("سقف ارسال موفق امروز"), false);
    assert.equal(textEdits.every(call => call.payload.message_id === "settings-panel-initial"), true);
    assert.equal(keypadEdits.every(call => call.payload.message_id === "settings-panel-initial"), true);
    assert.equal(state.DB.state.get("panel_mid"), "settings-panel-initial");
  });
}

// Settings boundary errors update the same panel after their loading state; they do not send side messages.
{
  await withMockRubikaFetch(async calls => {
    const state = panelEnv();
    state.DB.ownerChat = "b0actualowner";
    state.DB.state.set("panel_sync_mode", "atomic");
    state.DB.state.set("panel_mid", "settings-boundary-panel");
    state.DB.consoleRules.version = 7;
    state.DB.consoleRules.rules_json = JSON.stringify({ daily_success_cap: 0, publishing_enabled: false, test_channel_only: true });
    const secret = await panel.hookSecret(state);
    const click = async button => postPanelUpdate(state, {
      update: { type: "NewMessage", chat_id: "b0actualowner", new_message: {
        message_id: state.DB.state.get("panel_mid"), chat_id: "b0actualowner", text: "", aux_data: { button_id: button },
      } },
    }, secret);
    await click("p_set_cap_dec");
    assert.equal(JSON.parse(state.DB.consoleRules.rules_json).daily_success_cap, 0);
    assert.equal(state.DB.consoleRules.version, 7);
    assert.equal(state.DB.brainEvents.filter(event => event.code === "console_rules_update").length, 0);
    assert.equal(calls.filter(call => call.method === "editMessageText").at(-1).payload.text.includes("عدد خارج از محدوده"), true);

    state.DB.consoleRules.rules_json = JSON.stringify({ daily_success_cap: 50, publishing_enabled: false, test_channel_only: true });
    await click("p_set_cap_inc");
    assert.equal(JSON.parse(state.DB.consoleRules.rules_json).daily_success_cap, 50);
    assert.equal(state.DB.consoleRules.version, 7);
    assert.equal(state.DB.brainEvents.filter(event => event.code === "console_rules_update").length, 0);
    assert.equal(calls.filter(call => call.method === "editMessageText").at(-1).payload.text.includes("عدد خارج از محدوده"), true);
    assert.equal(calls.filter(call => call.method === "sendMessage").length, 0);
    assert.equal(calls.filter(call => call.method === "editMessageText").length, 4);
    assert.equal(calls.filter(call => call.method === "editMessageKeypad").length, 4);
    assert.equal(state.DB.state.get("panel_mid"), "settings-boundary-panel");
  });
}

// Optimistic-lock conflicts are rendered on the existing panel after a loading state.
{
  await withMockRubikaFetch(async calls => {
    const state = panelEnv();
    state.DB.ownerChat = "b0actualowner";
    state.DB.state.set("panel_sync_mode", "atomic");
    state.DB.state.set("panel_mid", "settings-version-conflict");
    state.DB.conflictConsoleRulesUpdate = true;
    const secret = await panel.hookSecret(state);
    await postPanelUpdate(state, {
      update: { type: "NewMessage", chat_id: "b0actualowner", new_message: {
        message_id: "settings-version-conflict", chat_id: "b0actualowner", text: "",
        aux_data: { button_id: "p_set_cap_7" },
      } },
    }, secret);
    assert.equal(state.DB.consoleRules.version, 2);
    assert.equal(JSON.parse(state.DB.consoleRules.rules_json).daily_success_cap, 4);
    assert.equal(state.DB.brainEvents.filter(event => event.code === "console_rules_update").length, 0);
    assert.equal(calls.filter(call => call.method === "editMessageText")[0].payload.text, "⏳ در حال انجام…");
    assert.equal(calls.filter(call => call.method === "editMessageText").at(-1).payload.text.includes("هم‌زمان تغییر کرد"), true);
    assert.equal(calls.filter(call => call.method === "sendMessage").length, 0);
    assert.equal(state.DB.state.get("panel_mid"), "settings-version-conflict");
  });
}

// Only the primary owner may read or modify PV settings, even when another viewer is allow-listed for ordinary screens.
{
  await withMockRubikaFetch(async calls => {
    const state = panelEnv();
    state.DB.ownerChat = "b0actualowner";
    state.DB.consoleOwners.set(2, { id: 2, chat_id: "b0viewer", role: "viewer", enabled: 1 });
    state.DB.state.set("panel_sync_mode", "atomic");
    state.DB.state.set("panel_mid", "primary-owner-panel");
    const beforeRules = state.DB.consoleRules.rules_json;
    const beforeVersion = state.DB.consoleRules.version;
    const secret = await panel.hookSecret(state);
    await postPanelUpdate(state, {
      update: { type: "NewMessage", chat_id: "b0viewer", new_message: {
        message_id: "viewer-settings-command", chat_id: "b0viewer", text: "/settings",
      } },
    }, secret);
    assert.equal(state.DB.consoleRules.rules_json, beforeRules);
    assert.equal(state.DB.consoleRules.version, beforeVersion);
    assert.equal(state.DB.brainEvents.filter(event => event.code === "console_rules_update").length, 0);
    assert.equal(state.DB.state.get("panel_mid"), "primary-owner-panel", "denying viewer must not replace the owner's tracked panel");
    assert.equal(calls.filter(call => call.method === "sendMessage").length, 1);
    assert.equal(calls[0].payload.text.includes("نقش مالک"), true);
  });
}

// Settings command is mapped to p_set and the settings module is used without modifying its source.
{
  const panelSourceOnDisk = fs.readFileSync(path.join(root, "src/owner_panel.js"), "utf8");
  const settingsSourceOnDisk = fs.readFileSync(path.join(root, "src/panel_settings.js"), "utf8");
  assert.match(panelSourceOnDisk, /"\/settings":\s*"p_set"/);
  assert.match(panelSourceOnDisk, /import \{ isSettingsId, applySettingsAction, settingsScreen \} from "\.\/panel_settings\.js";/);
  assert.equal(settingsSourceOnDisk, settingsSource, "Claude settings module must remain unchanged");
}

// Lightweight CI checks for the legacy owner console HTML. The HTML itself must remain byte-for-byte unchanged.
{
  const htmlPath = path.join(root, "docs/console/legacy.html");
  const html = fs.readFileSync(htmlPath, "utf8");
  assert.equal((html.match(/<\/script>/g) || []).length, 1, "legacy owner console HTML must contain exactly one </script> closing tag");

  const opening = html.match(/<script\s*>/i);
  assert.ok(opening, "legacy owner console HTML must contain its inline script");
  const start = opening.index + opening[0].length;
  const end = html.indexOf("</script>", start);
  assert.ok(end >= start, "owner console inline script must have a closing tag");
  const inlineJs = html.slice(start, end);
  const syntax = spawnSync(process.execPath, ["--check", "--input-type=module"], {
    input: inlineJs,
    encoding: "utf8",
  });
  assert.equal(syntax.status, 0, "legacy owner console inline JavaScript must pass node --check: " + (syntax.stderr || syntax.stdout || ""));

  // Only the Worker API and GitHub API are actual network-request origins.
  // The third syntactic fetch occurrence is text inside a mock downloadable example, not an executed request.
  const fetchArgs = [...inlineJs.matchAll(/\bfetch\s*\(\s*([^,)\s]+)/g)].map(match => match[1]);
  assert.deepEqual(fetchArgs, ["url", "this.base+path", "req"], "unexpected fetch site: only the two approved API requests and the inert example may appear");
  assert.equal((inlineJs.match(/\bawait\s+fetch\s*\(/g) || []).length, 2, "only two runtime fetch requests are allowed");
  assert.match(inlineJs, /apiBase:\s*"https:\/\/rubika-agent\.h98609294\.workers\.dev"/, "Worker API base must be the approved Worker");
  assert.match(inlineJs, /base:"https:\/\/api\.github\.com"/, "GitHub API base must be api.github.com");
  assert.match(inlineJs, /const base = \(S\.apiBase\|\|""\)\.replace/, "Worker requests must use the configured Worker API base");
  assert.match(inlineJs, /url = base \+ path/, "Worker requests must be built from the approved base");
  assert.equal((inlineJs.match(/\b(?:XMLHttpRequest|WebSocket|EventSource)\b|\bsendBeacon\s*\(|\bimportScripts\s*\(/g) || []).length, 0,
    "no alternate network request primitive is allowed");
}

// The directory index is a small, hash-preserving redirect to v2; the old app lives separately.
{
  const entryPath = path.join(root, "docs/console/index.html");
  const entryHtml = fs.readFileSync(entryPath, "utf8");
  assert.ok(entryHtml.includes('<meta http-equiv="refresh" content="0;url=v2.html">'), "console entrypoint must provide the no-JS v2 redirect");
  assert.ok(entryHtml.includes("location.replace('v2.html'+location.hash)"), "console entrypoint must preserve the location hash");
  assert.ok(entryHtml.includes('href="v2.html">ورود به کنسول</a>'), "console entrypoint must show a Persian fallback link");
  assert.ok(entryHtml.includes('<html lang="fa" dir="rtl">'), "console fallback must remain Persian RTL");
  assert.ok(entryHtml.includes('background:#05060f'), "console fallback must use the dark theme background");
  assert.ok(!entryHtml.includes('id="rc-v5"'), "the legacy app must not remain in the directory index");
}

// GitHub Pages publishes the repository root, so /console/ is a root-level alias to the v2 app.
{
  const rootEntryPath = path.join(root, "console/index.html");
  const rootEntryHtml = fs.readFileSync(rootEntryPath, "utf8");
  assert.ok(rootEntryHtml.includes('<meta http-equiv="refresh" content="0;url=../docs/console/v2.html">'), "root console alias must provide the no-JS v2 redirect");
  assert.ok(rootEntryHtml.includes("const target='../docs/console/v2.html'+location.hash;"), "root console alias must build the target with the current hash");
  assert.ok(rootEntryHtml.includes("location.replace(target);"), "root console alias must replace the current location");
  assert.ok(rootEntryHtml.includes('href="../docs/console/v2.html">ورود به کنسول</a>'), "root console alias must show a Persian fallback link");
  assert.ok(!rootEntryHtml.includes('id="rc-v5"'), "the old app must remain only at legacy.html");
}

// Phase-two console endpoints use the fake D1 database and audit each successful mutation to brain_events.
{
  const state = env();
  let response = await read(await worker.fetch(request("/api/console/owners"), state));
  assert.equal(response.status, 200);
  assert.deepEqual(response.body.items, []);

  response = await read(await worker.fetch(request("/api/console/owners", {
    method: "POST", body: { chat_id: "owner-chat-001", label: "مالک اصلی", role: "owner", enabled: 1 },
  }), state));
  assert.equal(response.status, 201);
  assert.equal(response.body.item.role, "owner");
  const ownerId = response.body.item.id;

  response = await read(await worker.fetch(request("/api/console/owners", {
    method: "POST", body: { chat_id: "viewer-chat-002", label: "ناظر", role: "viewer", enabled: 1 },
  }), state));
  assert.equal(response.status, 201);
  const viewerId = response.body.item.id;

  response = await read(await worker.fetch(request("/api/console/owners", {
    method: "POST", body: { id: viewerId, enabled: 0 },
  }), state));
  assert.equal(response.status, 200);
  assert.equal(Number(response.body.item.enabled), 0);

  response = await read(await worker.fetch(request("/api/console/owners/" + viewerId, { method: "DELETE" }), state));
  assert.equal(response.status, 200);
  response = await read(await worker.fetch(request("/api/console/owners/" + ownerId, { method: "DELETE" }), state));
  assert.equal(response.status, 409, "the last enabled owner must not be removable");

  response = await read(await worker.fetch(request("/api/console/owners/invite", {
    method: "POST", body: {},
  }), state));
  assert.equal(response.status, 201);
  assert.match(response.body.invite_code, /^INV-[A-F0-9]{32}$/);
  assert.ok(Date.parse(response.body.expires_at) > Date.now());
  assert.equal([...state.DB.consoleInvites.values()].some(row => row.code_hash === response.body.invite_code), false,
    "plaintext invitation code must not be stored in D1");
  assert.equal(state.DB.events.some(event => event.code === "console_owner_create"), true);
  assert.equal(state.DB.events.some(event => event.code === "console_owner_update"), true);
  assert.equal(state.DB.events.some(event => event.code === "console_owner_delete"), true);
  assert.equal(state.DB.events.some(event => event.code === "console_owner_invite"), true);
  assert.equal(state.DB.events.some(event => JSON.stringify(event).includes(response.body.invite_code)), false,
    "audit logs must never contain the plaintext invite code");
}

// Rules follow the HTML's {ok, version, rules, updated_at} contract and optimistic base_version.
{
  const state = env();
  let response = await read(await worker.fetch(request("/api/console/rules"), state));
  assert.equal(response.status, 200);
  assert.equal(response.body.version, 1);
  assert.equal(response.body.rules.publishing_enabled, false);
  assert.equal(response.body.rules.test_channel_only, true);

  response = await read(await worker.fetch(request("/api/console/rules", {
    method: "PUT",
    body: { base_version: 1, rules: { publishing_enabled: true, test_channel_only: false, daily_success_cap: 4, daily_edit_count: 7 } },
  }), state));
  assert.equal(response.status, 200);
  assert.equal(response.body.version, 2);
  assert.equal(response.body.rules.publishing_enabled, false, "publishing must remain disabled");
  assert.equal(response.body.rules.test_channel_only, true);
  assert.equal(JSON.parse(state.DB.consoleRules.rules_json).publishing_enabled, false);

  response = await read(await worker.fetch(request("/api/console/rules", {
    method: "PUT", body: { base_version: 1, rules: { daily_edit_count: 8 } },
  }), state));
  assert.equal(response.status, 409);
  assert.equal(response.body.error, "version_conflict");
  assert.equal(response.body.server.version, 2);
  assert.equal(state.DB.events.filter(event => event.code === "console_rules_update").length, 1);
}

// Channels are configuration only; production role cannot be enabled and no publish path is called.
{
  const state = env();
  let response = await read(await worker.fetch(request("/api/console/channels"), state));
  assert.equal(response.status, 200);
  assert.deepEqual(response.body.items, []);

  response = await read(await worker.fetch(request("/api/console/channels", {
    method: "POST", body: { label: "کانال تست", rubika_channel_id: "@test_channel", role: "test" },
  }), state));
  assert.equal(response.status, 201);
  assert.equal(response.body.item.role, "test");
  assert.equal(Number(response.body.item.enabled), 1);
  const testChannelId = response.body.item.id;

  response = await read(await worker.fetch(request("/api/console/channels", {
    method: "POST", body: { label: "کانال اصلی", rubika_channel_id: "@main_channel", role: "production", enabled: 1 },
  }), state));
  assert.equal(response.status, 201);
  assert.equal(response.body.item.role, "production");
  assert.equal(Number(response.body.item.enabled), 0);

  response = await read(await worker.fetch(request("/api/console/channels", {
    method: "POST", body: { label: "تکراری", rubika_channel_id: "@test_channel", role: "test" },
  }), state));
  assert.equal(response.status, 409);

  response = await read(await worker.fetch(request("/api/console/channels/" + testChannelId, { method: "DELETE" }), state));
  assert.equal(response.status, 200);
  assert.equal(state.DB.events.some(event => event.code === "console_channel_create"), true);
  assert.equal(state.DB.events.some(event => event.code === "console_channel_delete"), true);
  assert.equal(state.DB.events.some(event => event.code === "console_channel_create" && String(event.details_json).includes("production") && String(event.details_json).includes("enabled")) === true, true);
}

// Mob X links require confirmation; external_id prevents duplicates and confirmation creates one idempotent job.
{
  const state = env();
  let response = await read(await worker.fetch(request("/api/console/links", {
    method: "POST", body: { url: "https://twitter.com/mobgamesstudios/status/2107878755079213476" },
  }), state));
  assert.equal(response.status, 201);
  assert.equal(response.body.link.external_id, "2107878755079213476");
  assert.equal(response.body.link.status, "pending");
  assert.equal(response.body.confirmation_required, true);
  const linkId = response.body.link.id;

  response = await read(await worker.fetch(request("/api/console/links", {
    method: "POST", body: { url: "https://x.com/Test/status/2107878755079213476" },
  }), state));
  assert.equal(response.status, 400);
  assert.equal(response.body.error, "only_official_mob_x_status_links_allowed");

  response = await read(await worker.fetch(request("/api/console/links", {
    method: "POST", body: { url: "https://x.com/mobgamesstudios/status/2107878755079213476/" },
  }), state));
  assert.equal(response.status, 409, "same external_id must be rejected even when the URL variant differs");

  response = await read(await worker.fetch(request("/api/console/jobs", {
    method: "POST", body: { type: "mob_x_link", params: { url: response.body?.url || "https://x.com/mobgamesstudios/status/2107878755079213476" } },
  }), state));
  assert.equal(response.status, 400, "mob_x_link jobs must only be created through explicit link confirmation");
  assert.equal(response.body.error, "mob_x_link_requires_link_confirmation");

  response = await read(await worker.fetch(request("/api/console/links/" + linkId + "/confirm", {
    method: "POST", body: {},
  }), state));
  assert.equal(response.status, 200);
  assert.equal(response.body.status, "confirmed");
  const firstJobId = response.body.job_id;
  const job = state.DB.consoleJobs.get(firstJobId);
  assert.equal(job.type, "mob_x_link");
  assert.equal(job.status, "confirmed");
  assert.equal(Number(job.panel_link_id), Number(linkId));
  assert.deepEqual(JSON.parse(job.params_json), {
    url: "https://x.com/mobgamesstudios/status/2107878755079213476",
    panel_link_id: linkId,
  });
  assert.equal(state.DB.panelLinks.get(linkId).status, "confirmed");

  response = await read(await worker.fetch(request("/api/console/links/" + linkId + "/confirm", {
    method: "POST", body: {},
  }), state));
  assert.equal(response.status, 200);
  assert.equal(response.body.job_id, firstJobId, "repeated confirmation must return the same job");
  assert.equal(state.DB.consoleJobs.size, 1, "repeated confirmation must not create another job");

  response = await read(await worker.fetch(request("/api/console/links"), state));
  assert.equal(response.status, 200);
  assert.equal(response.body.items[0].status, "confirmed");

  response = await read(await worker.fetch(request("/api/console/links", {
    method: "POST", body: { url: "https://x.com/mobgamesstudios/status/2107878755079213477" },
  }), state));
  const cancelId = response.body.link.id;
  response = await read(await worker.fetch(request("/api/console/links/" + cancelId + "/cancel", {
    method: "POST", body: {},
  }), state));
  assert.equal(response.status, 200);
  assert.equal(response.body.status, "rejected");
  assert.equal(state.DB.panelLinks.get(cancelId).status, "rejected");
  assert.equal(state.DB.consoleJobs.size, 1, "cancellation must not enqueue work");
}

// Owner-web Mob X caption actions are per-job, guarded, audited and have no publish/preview path.
{
  const state = env();
  const addReadyJob = (jobId, panelLinkId, itemId, overrides = {}) => {
    const result = {
      item_id: itemId, external_id: String(panelLinkId).padStart(16, "0"),
      rubika_file_id: "rubika-file-" + panelLinkId, media_type: "image",
      caption: "Initial caption " + panelLinkId, original_caption: "Original " + panelLinkId,
      persian_translation: "ترجمهٔ اولیه " + panelLinkId, caption_mode: "generated",
      preview_sent: true, preview_message_id: "preview-" + panelLinkId, ...overrides.result,
    };
    state.DB.consoleJobs.set(jobId, {
      id: jobId, type: "mob_x_link", status: overrides.status || "ready_for_review",
      progress: 100, panel_link_id: panelLinkId, result_json: JSON.stringify(result),
      error: null, created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
    });
    state.DB.panelLinks.set(panelLinkId, {
      id: panelLinkId, status: overrides.linkStatus || "ready_for_review",
      url: "https://x.com/mobgamesstudios/status/" + result.external_id,
    });
    state.DB.items.set(itemId, {
      id: itemId, platform: "mob_official", status: "ready", review_state: "pending",
      caption: result.caption, original_caption: result.original_caption,
      persian_translation: result.persian_translation,
    });
    return { result, item: state.DB.items.get(itemId) };
  };
  const firstJobId = "job_abcdef1234567890";
  const secondJobId = "job_bcdef1234567890";
  const noCaptionJobId = "job_cdefab1234567890";
  const englishJobId = "job_defabc1234567890";
  const rejectJobId = "job_efabcd1234567890";
  const invalidStateJobId = "job_fabcde1234567890";
  const firstItemId = "mob_official:review-caption-1";
  const secondItemId = "mob_official:review-caption-2";
  const noCaptionItemId = "mob_official:review-caption-3";
  const englishItemId = "mob_official:review-caption-4";
  const rejectItemId = "mob_official:review-caption-5";
  const invalidItemId = "mob_official:review-caption-6";

  addReadyJob(firstJobId, 101, firstItemId);
  addReadyJob(secondJobId, 102, secondItemId);
  addReadyJob(noCaptionJobId, 103, noCaptionItemId);
  addReadyJob(englishJobId, 104, englishItemId);
  addReadyJob(rejectJobId, 105, rejectItemId);
  addReadyJob(invalidStateJobId, 106, invalidItemId, { status: "queued" });

  const secondJobBefore = JSON.stringify(state.DB.consoleJobs.get(secondJobId));
  const secondItemBefore = JSON.stringify(state.DB.items.get(secondItemId));
  const tiktokBefore = {
    id: "tiktok:review-caption-control",
    platform: "tiktok", status: "ready", review_state: "pending",
    caption: "TikTok caption must stay unchanged",
  };
  state.DB.items.set(tiktokBefore.id, { ...tiktokBefore });
  const rulesBefore = JSON.stringify(state.DB.consoleRules);
  const writesBefore = state.DB.events.length;

  let response = await read(await worker.fetch(request("/api/console/jobs/" + firstJobId + "/mob-x-review", {
    method: "POST", body: { action: "edit_caption", caption: "   " },
  }), state));
  assert.equal(response.status, 400);
  assert.equal(response.body.error, "caption_empty");

  response = await read(await worker.fetch(request("/api/console/jobs/" + firstJobId + "/mob-x-review", {
    method: "POST", body: { action: "edit_caption", caption: "x".repeat(901) },
  }), state));
  assert.equal(response.status, 400);
  assert.equal(response.body.error, "caption_too_long");

  response = await read(await worker.fetch(request("/api/console/jobs/" + firstJobId + "/mob-x-review", {
    method: "POST", body: { action: "edit_caption", caption: "سلام دنیا؛ خبر تازه" },
  }), state));
  assert.equal(response.status, 200);
  assert.equal(response.body.caption, "سلام دنیا؛ خبر تازه");
  assert.equal(state.DB.items.get(firstItemId).caption, "سلام دنیا؛ خبر تازه");
  let editedResult = JSON.parse(state.DB.consoleJobs.get(firstJobId).result_json);
  assert.equal(editedResult.caption, "سلام دنیا؛ خبر تازه");
  assert.equal(editedResult.caption_mode, "edited");
  assert.equal(editedResult.original_caption, "Original 101");
  assert.equal(editedResult.persian_translation, "ترجمهٔ اولیه 101");
  assert.equal(editedResult.preview_sent, false, "web caption edits must invalidate the old PV preview");
  assert.equal(editedResult.caption_refresh_state, "not_refreshed");
  assert.equal(editedResult.preview_message_id, "preview-101", "PV retains the old message id to replace it on refresh");
  assert.equal(JSON.stringify(state.DB.consoleJobs.get(secondJobId)), secondJobBefore,
    "editing one Mob X job must not touch a second job");
  assert.equal(JSON.stringify(state.DB.items.get(secondItemId)), secondItemBefore,
    "editing one Mob X job must not touch a second job's item/caption");
  assert.deepEqual(state.DB.items.get(tiktokBefore.id), tiktokBefore,
    "Mob X web caption actions must not modify TikTok items");

  const auditsAfterPersian = state.DB.events.length;
  response = await read(await worker.fetch(request("/api/console/jobs/" + firstJobId + "/mob-x-review", {
    method: "POST", body: { action: "edit_caption", caption: "سلام دنیا؛ خبر تازه" },
  }), state));
  assert.equal(response.status, 200);
  assert.equal(response.body.unchanged, true);
  assert.equal(state.DB.events.length, auditsAfterPersian, "repeat edit must be idempotent and must not duplicate audit");

  response = await read(await worker.fetch(request("/api/console/jobs/" + englishJobId + "/mob-x-review", {
    method: "POST", body: { action: "edit_caption", caption: "**English update**" },
  }), state));
  assert.equal(response.status, 200);
  assert.equal(state.DB.items.get(englishItemId).caption, "English update");
  assert.equal(JSON.parse(state.DB.consoleJobs.get(englishJobId).result_json).caption, "English update");

  const noCaptionFixed = mobXCaption.buildMobXNoCaptionCaption();
  assert.equal(noCaptionFixed, "**ماب انترتینمنت در پیج خود پستی منتشر کرد.**");
  response = await read(await worker.fetch(request("/api/console/jobs/" + noCaptionJobId + "/mob-x-review", {
    method: "POST", body: { action: "no_caption" },
  }), state));
  assert.equal(response.status, 200);
  assert.equal(response.body.caption, noCaptionFixed);
  const noCaptionResult = JSON.parse(state.DB.consoleJobs.get(noCaptionJobId).result_json);
  const noCaptionItem = state.DB.items.get(noCaptionItemId);
  assert.equal(noCaptionResult.caption_mode, "no_caption");
  assert.equal(noCaptionResult.caption, noCaptionFixed);
  assert.equal(noCaptionResult.original_caption, "");
  assert.equal(noCaptionResult.persian_translation, "");
  assert.equal(noCaptionResult.preview_sent, false, "no-caption from web must invalidate the old PV preview");
  assert.equal(noCaptionResult.caption_refresh_state, "not_refreshed");
  assert.equal(noCaptionItem.caption, noCaptionFixed);
  assert.equal(noCaptionItem.original_caption, "");
  assert.equal(noCaptionItem.persian_translation, "");
  const noCaptionAuditCount = state.DB.events.filter(event => event.code === "console_mob_x_no_caption").length;
  response = await read(await worker.fetch(request("/api/console/jobs/" + noCaptionJobId + "/mob-x-review", {
    method: "POST", body: { action: "no_caption" },
  }), state));
  assert.equal(response.status, 200);
  assert.equal(response.body.unchanged, true);
  assert.equal(state.DB.events.filter(event => event.code === "console_mob_x_no_caption").length, noCaptionAuditCount);

  response = await read(await worker.fetch(request("/api/console/jobs/" + rejectJobId + "/mob-x-review", {
    method: "POST", body: { action: "reject" },
  }), state));
  assert.equal(response.status, 200);
  assert.equal(response.body.status, "rejected");
  assert.equal(state.DB.consoleJobs.get(rejectJobId).status, "rejected");
  assert.equal(state.DB.panelLinks.get(105).status, "rejected");
  assert.equal(state.DB.items.get(rejectItemId).review_state, "rejected");
  const rejectAuditCount = state.DB.events.filter(event => event.code === "console_mob_x_reject").length;
  response = await read(await worker.fetch(request("/api/console/jobs/" + rejectJobId + "/mob-x-review", {
    method: "POST", body: { action: "reject" },
  }), state));
  assert.equal(response.status, 200);
  assert.equal(response.body.unchanged, true);
  assert.equal(state.DB.events.filter(event => event.code === "console_mob_x_reject").length, rejectAuditCount);

  response = await read(await worker.fetch(request("/api/console/jobs/" + invalidStateJobId + "/mob-x-review", {
    method: "POST", body: { action: "reject" },
  }), state));
  assert.equal(response.status, 409);
  assert.equal(state.DB.consoleJobs.get(invalidStateJobId).status, "queued");

  response = await read(await worker.fetch(request("/api/console/jobs/" + firstJobId + "/mob-x-review", {
    method: "POST", body: { action: "edit_caption", caption: "Viewer must be rejected" }, token: TOKEN,
  }), state));
  assert.equal(response.status, 403);
  assert.equal(response.body.error, "owner_required");

  const missingSecret = env();
  delete missingSecret.OWNER_WEB_TOKEN;
  missingSecret.DB.consoleJobs.set(firstJobId, { ...state.DB.consoleJobs.get(firstJobId), status: "ready_for_review" });
  missingSecret.DB.panelLinks.set(101, { id: 101, status: "ready_for_review" });
  missingSecret.DB.items.set(firstItemId, { ...state.DB.items.get(firstItemId), status: "ready", review_state: "pending" });
  response = await read(await worker.fetch(request("/api/console/jobs/" + firstJobId + "/mob-x-review", {
    method: "POST", body: { action: "edit_caption", caption: "must fail closed" },
  }), missingSecret));
  assert.equal(response.status, 403);

  const shortSecret = env();
  shortSecret.OWNER_WEB_TOKEN = "short";
  response = await read(await worker.fetch(request("/api/console/jobs/" + firstJobId + "/mob-x-review", {
    method: "POST", body: { action: "edit_caption", caption: "must fail closed" }, token: "short",
  }), shortSecret));
  assert.equal(response.status, 403);

  const runner = await read(await worker.fetch(request("/api/console/mob-x/claim", {
    method: "POST", body: {}, token: TOKEN,
  }), state));
  assert.equal(runner.status, 200, "Actions/runner token path must remain unchanged");
  assert.equal(runner.body.claimed, false);
  const viewerGet = await read(await worker.fetch(request("/api/console/jobs/" + firstJobId, { token: TOKEN }), state));
  assert.equal(viewerGet.status, 200, "read-only job detail remains viewer-readable");

  assert.equal(JSON.stringify(state.DB.consoleRules), rulesBefore, "no global caption/rules setting may change");
  assert.equal(state.DB.events.length > writesBefore, true);
  assert.equal(state.DB.events.filter(event => event.code === "console_mob_x_caption_edit").length, 2);
  assert.equal(state.DB.events.filter(event => event.code === "console_mob_x_no_caption").length, 1);
  assert.equal(state.DB.events.filter(event => event.code === "console_mob_x_reject").length, 1);
  assert.equal([...state.DB.consoleJobs.values()].some(job => job.status === "approved"), false,
    "web caption actions must never approve or publish");
}

// The five-minute processor claims a single Mob X job and transitions it to pending review.
{
  const state = env();
  const externalId = "2107878755079213476";
  const linkId = 1;
  state.DB.panelLinks.set(linkId, {
    id: linkId, url: "https://x.com/mobgamesstudios/status/" + externalId,
    external_id: externalId, status: "confirmed", created_at: new Date().toISOString(),
  });
  state.DB.consoleJobs.set("job_abcdef1234567890", {
    id: "job_abcdef1234567890", type: "mob_x_link", status: "confirmed",
    progress: 0, params_json: JSON.stringify({ url: "https://x.com/mobgamesstudios/status/" + externalId, panel_link_id: linkId }),
    result_json: null, error: null, panel_link_id: linkId, created_at: new Date().toISOString(),
  });

  let response = await read(await worker.fetch(request("/api/console/mob-x/claim", { method: "POST", body: {} }), state));
  assert.equal(response.status, 200);
  assert.equal(response.body.claimed, true);
  assert.equal(response.body.job.status, "processing");
  assert.equal(response.body.job.external_id, externalId);
  assert.equal(response.body.job.params.panel_link_id, linkId);
  assert.equal(state.DB.panelLinks.get(linkId).status, "processing");
  assert.equal(state.DB.consoleJobs.get("job_abcdef1234567890").status, "processing");

  response = await read(await worker.fetch(request("/api/console/mob-x/claim", { method: "POST", body: {} }), state));
  assert.equal(response.status, 200);
  assert.equal(response.body.claimed, false, "a processing job must never be claimed twice");

  const ready = {
    item_id: "mob_official:" + externalId, external_id: externalId, title: "Official Mob test post",
    media_type: "image", caption: "کپشن ساده", original_caption: "Original caption",
    persian_translation: "کپشن ساده", storage_key: "/Rubika-Agent/official/test.jpg",
    source_tier: "official", review_state: "pending", publishing_attempted: false,
  };
  response = await read(await worker.fetch(request("/api/console/mob-x/jobs/job_abcdef1234567890/transition", {
    method: "POST", body: { status: "ready_for_review", result: ready },
  }), state));
  assert.equal(response.status, 200);
  assert.equal(response.body.status, "ready_for_review");
  assert.equal(state.DB.consoleJobs.get("job_abcdef1234567890").status, "ready_for_review");
  assert.equal(state.DB.panelLinks.get(linkId).status, "ready_for_review");
  assert.equal(JSON.parse(state.DB.consoleJobs.get("job_abcdef1234567890").result_json).publishing_attempted, false);

  response = await read(await worker.fetch(request("/api/console/mob-x/jobs/job_abcdef1234567890/transition", {
    method: "POST", body: {
      status: "failed", error_code: "worker_request_failed",
      error_detail: "path=/api/console/rules attempts=3 status=503 elapsed_ms=7021",
    },
  }), state));
  assert.equal(response.status, 200);
  assert.equal(response.body.status, "failed");
  assert.equal(response.body.error_detail, "path=/api/console/rules attempts=3 status=503 elapsed_ms=7021");
  const failedJob = state.DB.consoleJobs.get("job_abcdef1234567890");
  assert.equal(failedJob.status, "failed");
  assert.equal(failedJob.error, "worker_request_failed");
  assert.equal(JSON.parse(failedJob.result_json).publishing_attempted, false);
  assert.equal(JSON.parse(failedJob.result_json).error_detail, "path=/api/console/rules attempts=3 status=503 elapsed_ms=7021");
  assert.equal(state.DB.panelLinks.get(linkId).status, "failed");

  response = await read(await worker.fetch(request("/api/console/mob-x/jobs/job_abcdef1234567890/transition", {
    method: "POST", body: { status: "sent" },
  }), state));
  assert.equal(response.status, 400, "stage two must never transition a job directly to sent");
}

// The scheduled processor claims one Mob X job and can only transition it to pending review or failure.
{
  const state = env();
  const externalId = "2107878755079213476";
  const linkId = 1;
  state.DB.panelLinks.set(linkId, {
    id: linkId, url: "https://x.com/mobgamesstudios/status/" + externalId,
    external_id: externalId, status: "confirmed", created_at: new Date().toISOString(),
  });
  state.DB.consoleJobs.set("job_abcdef1234567890", {
    id: "job_abcdef1234567890", type: "mob_x_link", status: "confirmed",
    progress: 0, params_json: JSON.stringify({ url: "https://x.com/mobgamesstudios/status/" + externalId, panel_link_id: linkId }),
    result_json: null, error: null, panel_link_id: linkId, created_at: new Date().toISOString(),
  });

  let response = await read(await worker.fetch(request("/api/console/mob-x/claim", { method: "POST", body: {} }), state));
  assert.equal(response.status, 200);
  assert.equal(response.body.claimed, true);
  assert.equal(response.body.job.status, "processing");
  assert.equal(response.body.job.external_id, externalId);
  assert.equal(response.body.job.params.panel_link_id, linkId);
  assert.equal(state.DB.panelLinks.get(linkId).status, "processing");
  assert.equal(state.DB.consoleJobs.get("job_abcdef1234567890").status, "processing");

  response = await read(await worker.fetch(request("/api/console/mob-x/claim", { method: "POST", body: {} }), state));
  assert.equal(response.status, 200);
  assert.equal(response.body.claimed, false, "processing jobs cannot be claimed twice");

  const ready = {
    item_id: "mob_official:" + externalId, external_id: externalId, title: "Official Mob test post",
    media_type: "image", caption: "کپشن ساده", original_caption: "Original caption",
    persian_translation: "کپشن ساده", storage_key: "/Rubika-Agent/official/test.jpg",
    source_tier: "official", review_state: "pending", publishing_attempted: false,
  };
  response = await read(await worker.fetch(request("/api/console/mob-x/jobs/job_abcdef1234567890/transition", {
    method: "POST", body: { status: "ready_for_review", result: ready },
  }), state));
  assert.equal(response.status, 200);
  assert.equal(response.body.status, "ready_for_review");
  assert.equal(state.DB.consoleJobs.get("job_abcdef1234567890").status, "ready_for_review");
  assert.equal(state.DB.panelLinks.get(linkId).status, "ready_for_review");
  assert.equal(JSON.parse(state.DB.consoleJobs.get("job_abcdef1234567890").result_json).publishing_attempted, false);

  response = await read(await worker.fetch(request("/api/console/mob-x/jobs/job_abcdef1234567890/transition", {
    method: "POST", body: { status: "sent" },
  }), state));
  assert.equal(response.status, 400, "processor cannot mark a post sent");
}

// Jobs are durable queue records only: creating one does not run a worker, workflow, or publisher.
{
  const state = env();
  let response = await read(await worker.fetch(request("/api/console/jobs", {
    method: "POST", body: { type: "ai_health_check", params: { source: "console-test" } },
  }), state));
  assert.equal(response.status, 201);
  assert.match(response.body.job_id, /^job_[a-f0-9]{24}$/);
  const jobId = response.body.job_id;

  response = await read(await worker.fetch(request("/api/console/jobs/" + encodeURIComponent(jobId)), state));
  assert.equal(response.status, 200);
  assert.equal(response.body.job.id, jobId);
  assert.equal(response.body.job.type, "ai_health_check");
  assert.equal(response.body.job.status, "queued");
  assert.equal(response.body.job.progress, 0);
  assert.equal(response.body.job.result, null);

  response = await read(await worker.fetch(request("/api/console/jobs?limit=5"), state));
  assert.equal(response.status, 200);
  assert.equal(response.body.items.length, 1);

  // Worker-side type/status filters constrain rows before applying the limit.
  for (let i = 0; i < 9; i += 1) {
    state.DB.consoleJobs.set("job_irrelevant_" + i, {
      id: "job_irrelevant_" + i, type: "ai_health_check", status: "queued",
      progress: 0, created_at: new Date(Date.now() + i * 1000).toISOString(),
    });
  }
  state.DB.consoleJobs.set("job_mob_confirmed_1", {
    id: "job_mob_confirmed_1", type: "mob_x_link", status: "confirmed",
    progress: 0, panel_link_id: 23, created_at: "2026-01-01T00:00:00.000Z",
  });
  state.DB.consoleJobs.set("job_mob_queued_2", {
    id: "job_mob_queued_2", type: "mob_x_link", status: "queued",
    progress: 0, panel_link_id: 24, created_at: "2026-01-02T00:00:00.000Z",
  });
  response = await read(await worker.fetch(
    request("/api/console/jobs?type=mob_x_link&status=confirmed&limit=5"), state
  ));
  assert.equal(response.status, 200);
  assert.deepEqual(response.body.items.map(item => item.id), ["job_mob_confirmed_1"]);
  assert.equal(response.body.items.every(item => item.type === "mob_x_link" && item.status === "confirmed"), true);
  response = await read(await worker.fetch(request("/api/console/jobs?type=not_allowed&limit=5"), state));
  assert.equal(response.status, 400);
  assert.equal(response.body.error, "invalid_job_type");

  response = await read(await worker.fetch(request("/api/console/jobs", {
    method: "POST", body: { type: "run_arbitrary_workflow", params: {} },
  }), state));
  assert.equal(response.status, 400);
  assert.equal(response.body.error, "invalid_job_type");
  assert.equal(state.DB.events.some(event => event.code === "console_job_create"), true);
  assert.equal(state.DB.consoleJobs.get(jobId).status, "queued");
}

// Enabled PV viewers can read screens but every sensitive callback and follow-up text event rechecks the owner role.
{
  await withMockRubikaFetch(async calls => {
    const state = panelEnv();
    state.DB.ownerChat = "b0actualowner";
    state.DB.consoleOwners.set(2, {
      id: 2, chat_id: "b0viewer", role: "viewer", enabled: 1,
    });
    state.DB.state.set("panel_sync_mode", "atomic");
    state.DB.state.set("panel_mid", "viewer-sensitive-panel-001");
    state.DB.state.set("panel_mob_pending_link_id", "1");
    state.DB.state.set("mode", "waiting_mob_caption_edit");
    state.DB.state.set("panel_mob_edit_job_id", "job_abcdef1234567890");
    state.DB.consoleJobs.set("job_abcdef1234567890", {
      id: "job_abcdef1234567890", type: "mob_x_link", status: "ready_for_review",
      panel_link_id: 1,
      result_json: JSON.stringify({
        item_id: "mob_official:2107878755079213476",
        rubika_file_id: "preview-file-id", preview_sent: true, caption: "before",
      }),
      params_json: "{}",
    });
    state.DB.links.push({
      id: 1, url: "https://x.com/mobgamesstudios/status/2107878755079213476",
      status: "pending", created_at: new Date().toISOString(),
    });
    const originalResult = state.DB.consoleJobs.get("job_abcdef1234567890").result_json;
    const secret = await panel.hookSecret(state);
    const click = async button => postPanelUpdate(state, {
      update: { type: "NewMessage", chat_id: "b0viewer", new_message: {
        message_id: state.DB.state.get("panel_mid"),
        chat_id: "b0viewer", text: "", aux_data: { button_id: button },
      } },
    }, secret);

    for (const button of [
      "p_mob_confirm",
      "p_mob_cancel",
      "p_mob_publish_job_abcdef1234567890",
      "p_mob_edit_job_abcdef1234567890",
      "p_mob_nocap_job_abcdef1234567890",
      "p_mob_reject_job_abcdef1234567890",
      "p_mob_manualconfirm_job_abcdef1234567890",
      "p_links_clear_yes",
      "p_failall_yes",
      "p_failgo_1_abcde",
      "p_lseldel_1_abcde",
      "p_set_cap_7",
    ]) {
      await click(button);
    }

    await postPanelUpdate(state, {
      update: { type: "NewMessage", chat_id: "b0viewer", new_message: {
        message_id: "viewer-caption-followup-001", chat_id: "b0viewer",
        text: "caption must not be saved by viewer",
      } },
    }, secret);
    assert.equal(state.DB.consoleJobs.get("job_abcdef1234567890").result_json, originalResult,
      "a viewer demoted after opening the edit flow must not save a caption");
    assert.equal(calls.some(call => call.method === "sendFile"), false,
      "viewer callback must not resend a Mob X preview");
    assert.equal(state.DB.consoleJobs.get("job_abcdef1234567890").status, "ready_for_review");
    assert.equal(state.DB.links.length, 1, "viewer must not clear or mutate saved links");
    assert.equal(state.DB.linkDeleteCalls, 0);
    assert.equal(state.DB.consoleRules.version, 1, "viewer must not change protected settings");
    assert.equal(state.DB.state.get("panel_sync_mode"), "atomic");
    assert.equal(state.DB.state.get("panel_seq") || "0", "0",
      "unauthorized callbacks must not advance the global panel event sequence");

    state.DB.state.set("mode", "waiting_link");
    await postPanelUpdate(state, {
      update: { type: "NewMessage", chat_id: "b0viewer", new_message: {
        message_id: "viewer-link-submission-001", chat_id: "b0viewer",
        text: "https://x.com/mobgamesstudios/status/2107878755079213477",
      } },
    }, secret);
    assert.equal(state.DB.links.length, 1, "viewer cannot submit a new Mob X link from the waiting-link state");
    assert.equal(calls.some(call => call.method === "updateBotEndpoints"), false,
      "viewer must not register or change the webhook");
  });
}


// Failed-media bulk deletion is owner-bearer gated, bounded, path-safe, and requires a signed second step.
{
  const state = env();
  const id = "fm_" + "a".repeat(24);
  const key = "/Rubika-Agent/failed/owner-test.mp4";
  const row = { id, storage_key: key, status: "stored", updated_at: "2026-11-03T00:00:00Z", created_at: "2026-11-03T00:00:00Z" };
  state.DB.failedMedia.set(id, { ...row });

  const viewer = await read(await worker.fetch(request("/api/console/failed-media/delete-request", {
    method: "POST", token: "viewer-token-is-not-the-owner-api-token",
    body: { ids: [id] },
  }), state));
  assert.equal(viewer.status, 403, "the Worker owner gate must reject viewer credentials");
  assert.equal(state.DB.failedMedia.get(id).status, "stored");

  const directViewer = await failedMedia.handleFailedMedia(
    request("/api/console/failed-media/delete-request", {
      method: "POST", token: "viewer-token-is-not-the-owner-api-token", body: { ids: [id] },
    }),
    state,
    {
      json: (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }),
      readJson: async req => ({ ok: true, value: await req.json() }),
    },
    "/failed-media/delete-request", "POST",
  );
  assert.equal(directViewer.status, 403, "route-level guard must reject a viewer bearer even when called internally");
  assert.equal(state.DB.failedMedia.get(id).status, "stored");

  const empty = await read(await worker.fetch(request("/api/console/failed-media/delete-request", {
    method: "POST", body: { ids: [] },
  }), state));
  assert.equal(empty.status, 400);
  assert.equal(empty.body.error, "invalid_ids");

  const tooMany = await read(await worker.fetch(request("/api/console/failed-media/delete-request", {
    method: "POST", body: { ids: Array.from({ length: 51 }, (_, i) => "fm_" + String(i).padStart(24, "0")) },
  }), state));
  assert.equal(tooMany.status, 400);
  assert.equal(tooMany.body.error, "too_many_ids");

  const challenge = await read(await worker.fetch(request("/api/console/failed-media/delete-request", {
    method: "POST", body: { ids: [id] },
  }), state));
  assert.equal(challenge.status, 200);
  assert.equal(challenge.body.confirmation_required, true);
  assert.equal(challenge.body.requested, 1);
  assert.equal(state.DB.failedMedia.get(id).status, "stored", "phase one must not mutate the row");

  const tampered = challenge.body.confirmation.slice(0, -1) + (challenge.body.confirmation.endsWith("A") ? "B" : "A");
  const invalidConfirm = await read(await worker.fetch(request("/api/console/failed-media/delete-request", {
    method: "POST", body: { confirmation: tampered, phrase: "DELETE" },
  }), state));
  assert.equal(invalidConfirm.status, 409);
  assert.equal(invalidConfirm.body.error, "confirmation_invalid_or_expired");
  assert.equal(state.DB.failedMedia.get(id).status, "stored");

  const confirmed = await read(await worker.fetch(request("/api/console/failed-media/delete-request", {
    method: "POST", body: { confirmation: challenge.body.confirmation, phrase: "DELETE" },
  }), state));
  assert.equal(confirmed.status, 200);
  assert.equal(confirmed.body.requested, 1);
  assert.equal(state.DB.failedMedia.get(id).status, "delete_requested");

  const replay = await read(await worker.fetch(request("/api/console/failed-media/delete-request", {
    method: "POST", body: { confirmation: challenge.body.confirmation, phrase: "DELETE" },
  }), state));
  assert.equal(replay.status, 409, "a used snapshot must not be replayable");
  assert.equal(replay.body.error, "confirmation_stale");
  assert.equal(state.DB.failedMedia.get(id).status, "delete_requested");
}

// Invalid or out-of-scope storage paths are rejected before a challenge or runner handoff.
{
  const state = env();
  const id = "fm_" + "b".repeat(24);
  state.DB.failedMedia.set(id, {
    id, storage_key: "/Rubika-Agent/official/not-failed.mp4", status: "stored",
    updated_at: "2026-11-03T00:00:00Z", created_at: "2026-11-03T00:00:00Z",
  });
  const response = await read(await worker.fetch(request("/api/console/failed-media/delete-request", {
    method: "POST", body: { ids: [id] },
  }), state));
  assert.equal(response.status, 409);
  assert.equal(response.body.error, "invalid_storage_key");
  assert.equal(state.DB.failedMedia.get(id).status, "stored");

  state.DB.failedMedia.get(id).status = "delete_requested";
  const pending = await read(await worker.fetch(request("/api/console/failed-media/pending-delete", {
    method: "POST", body: {},
  }), state));
  assert.equal(pending.status, 409);
  assert.equal(pending.body.error, "invalid_storage_key");
  assert.equal(pending.body.items, undefined, "an invalid path must never reach the Mega runner");
}

// "Delete all" is limited to the runner's 50-item page and leaves the remainder untouched.
{
  const state = env();
  for (let i = 0; i < 55; i += 1) {
    const id = "fm_" + i.toString(16).padStart(24, "0");
    state.DB.failedMedia.set(id, {
      id, storage_key: "/Rubika-Agent/failed/bulk-" + i + ".mp4",
      status: "stored", updated_at: "2026-11-03T00:00:" + String(i).padStart(2, "0") + "Z",
      created_at: "2026-11-03T00:00:" + String(i).padStart(2, "0") + "Z",
    });
  }
  const stageOne = await read(await worker.fetch(request("/api/console/failed-media/delete-request", {
    method: "POST", body: { all: true },
  }), state));
  assert.equal(stageOne.status, 200);
  assert.equal(stageOne.body.requested, 50);
  assert.equal(stageOne.body.limited, true);
  assert.equal([...state.DB.failedMedia.values()].filter(row => row.status === "stored").length, 55);

  const stageTwo = await read(await worker.fetch(request("/api/console/failed-media/delete-request", {
    method: "POST", body: { confirmation: stageOne.body.confirmation, phrase: "DELETE" },
  }), state));
  assert.equal(stageTwo.status, 200);
  assert.equal(stageTwo.body.requested, 50);
  assert.equal([...state.DB.failedMedia.values()].filter(row => row.status === "delete_requested").length, 50);
  assert.equal([...state.DB.failedMedia.values()].filter(row => row.status === "stored").length, 5);
}

// The worker reports success only after the Mega runner confirms an in-scope key; row deletion remains last.
{
  const state = env();
  const id = "fm_" + "c".repeat(24);
  state.DB.failedMedia.set(id, {
    id, storage_key: "/Rubika-Agent/failed/remove-last.mp4", status: "delete_requested",
  });
  const removed = await read(await worker.fetch(request("/api/console/failed-media/delete-result", {
    method: "POST", body: { id, ok: true },
  }), state));
  assert.equal(removed.status, 200);
  assert.equal(removed.body.changed, 1);
  assert.equal(state.DB.failedMedia.has(id), false);
}





// PV publish approval updates the tracked main panel rather than sending a new main message.
{
  await withMockRubikaFetch(async calls => {
    const state = panelEnv();
    state.DB.ownerChat = "b0actualowner";
    const mainMid = "pv-publish-main-001";
    state.DB.state.set("panel_mid", mainMid);
    const jobId = "job_abcdef1234567890";
    const itemId = "mob_official:2107878755079213476";
    state.DB.consoleJobs.set(jobId, {
      id: jobId, type: "mob_x_link", status: "ready_for_review", panel_link_id: 1,
      params_json: "{}", result_json: JSON.stringify({
        item_id: itemId, rubika_file_id: "mock-preview-file", preview_sent: true,
      }),
    });
    state.DB.items.set(itemId, {
      id: itemId, platform: "mob_official", status: "ready", review_state: "pending",
    });
    const secret = await panel.hookSecret(state);
    await postPanelUpdate(state, {
      update: { type: "NewMessage", chat_id: "b0actualowner", new_message: {
        message_id: "pv-publish-preview-001", chat_id: "b0actualowner", text: "",
        aux_data: { button_id: "p_mob_publish_" + jobId },
      } },
    }, secret);

    assert.equal(state.DB.state.get("panel_mid"), mainMid, "publish approval must preserve panel_mid");
    assert.equal(calls.filter(call => call.method === "sendMessage").length, 0,
      "publish approval result must not create a new main message");
    const mainEdits = calls.filter(call => call.method === "editMessageText" && call.payload.message_id === mainMid);
    assert.ok(mainEdits.length >= 2, "the main message must receive Loading and the final result");
    assert.match(mainEdits[0].payload.text, /در حال انجام/);
    assert.ok(mainEdits.at(-1).payload.text.includes("تأیید مالک ثبت شد"));
    assert.equal(calls.filter(call => call.method === "editMessageText").every(call => call.payload.message_id === mainMid), true);
    assert.equal(calls.filter(call => call.method === "editMessageKeypad").every(call => call.payload.message_id === mainMid), true);
  });
}

// A link canceled before a job exists can be submitted again; reviewed/confirmed/sent links stay blocked.
{
  await withMockRubikaFetch(async calls => {
    const chat = "b0actualowner";
    const urlFor = id => "https://x.com/mobgamesstudios/status/" + id;
    const secretFor = async state => panel.hookSecret(state);
    const postText = (state, secret, text, messageId) => postPanelUpdate(state, {
      update: { type: "NewMessage", chat_id: chat, new_message: {
        message_id: messageId, chat_id: chat, text,
      } },
    }, secret);
    const click = (state, secret, button, messageId) => postPanelUpdate(state, {
      update: { type: "NewMessage", chat_id: chat, new_message: {
        message_id: messageId, chat_id: chat, text: "", aux_data: { button_id: button },
      } },
    }, secret);

    // Exercise the real cancel callback first: pending -> rejected with no job.
    const canceled = panelEnv();
    canceled.DB.ownerChat = chat;
    canceled.DB.state.set("panel_mid", "pv-reopen-main");
    canceled.DB.state.set("panel_mob_pending_link_id", "1");
    canceled.DB.state.set("panel_mob_temp_mid", "pv-reopen-prompt");
    canceled.DB.links.push({
      id: 1, url: urlFor("2107878755079213476"), external_id: "2107878755079213476",
      status: "pending", created_at: new Date().toISOString(),
    });
    canceled.DB.nextPanelLinkId = 2;
    const canceledSecret = await secretFor(canceled);
    await click(canceled, canceledSecret, "p_mob_cancel", "pv-reopen-prompt");
    assert.equal(canceled.DB.links[0].status, "rejected");
    assert.equal(canceled.DB.consoleJobs.size, 0);
    assert.equal(canceled.DB.state.get("panel_mob_temp_mid"), "");
    await click(canceled, canceledSecret, "p_mob", "pv-reopen-main");
    await postText(canceled, canceledSecret, urlFor("2107878755079213476"), "pv-reopen-text");
    assert.equal(canceled.DB.links[0].status, "pending",
      "a rejected link with no Mob X job must reopen after the owner submits it again");
    const reopenedPrompt = calls.filter(call => call.method === "sendMessage" && call.payload.text.includes("این لینک است؟"));
    assert.equal(reopenedPrompt.length, 1, "the reopened link must receive a fresh confirmation prompt");
    assert.equal(canceled.DB.state.get("panel_mid"), "pv-reopen-main",
      "reopening a link must not replace the main panel message");

    const blocked = async ({ status, id, withJob = false }) => {
      const state = panelEnv();
      state.DB.ownerChat = chat;
      const mainMid = "pv-blocked-main-" + status;
      state.DB.state.set("panel_mid", mainMid);
      state.DB.state.set("mode", "waiting_link");
      const externalId = id;
      state.DB.links.push({
        id: 1, url: urlFor(externalId), external_id: externalId, status,
        created_at: new Date().toISOString(),
      });
      if (withJob) {
        state.DB.consoleJobs.set("job_existing_" + status, {
          id: "job_existing_" + status, type: "mob_x_link", status: "failed",
          panel_link_id: 1, params_json: "{}", result_json: null,
        });
      }
      const secret = await secretFor(state);
      const beforePrompts = calls.filter(call => call.method === "sendMessage" && call.payload.text.includes("این لینک است؟")).length;
      await postText(state, secret, urlFor(externalId), "pv-resubmit-" + status);
      assert.equal(state.DB.links[0].status, status, status + " link status must not be reopened");
      assert.equal(state.DB.state.get("panel_mob_temp_mid") || "", "", status + " link must not get a new confirmation prompt");
      const afterPrompts = calls.filter(call => call.method === "sendMessage" && call.payload.text.includes("این لینک است؟")).length;
      assert.equal(afterPrompts, beforePrompts, status + " link must remain blocked");
      assert.ok(calls.some(call => call.method === "editMessageText" && call.payload.message_id === mainMid),
        status + " outcome must be rendered on the existing main panel");
    };

    await blocked({ status: "rejected", id: "2107878755079213477", withJob: true });
    await blocked({ status: "confirmed", id: "2107878755079213478" });
    await blocked({ status: "sent", id: "2107878755079213479" });
  });
}

// User-facing PV messages render real line breaks, never a literal backslash followed by n.
{
  const ownerPanelSource = fs.readFileSync(path.join(root, "src/owner_panel.js"), "utf8");
  assert.equal(ownerPanelSource.includes("\\\\n"), false,
    "all seven known double-backslash-n message escapes must be removed from owner_panel.js");
  assert.equal(ownerPanelSource.split('error_detail ? "\\n"').length - 1, 3,
    "all three error_detail suffixes must use a real newline escape");

  await withMockRubikaFetch(async calls => {
    const ownerState = panelEnv();
    const ownerSecret = await panel.hookSecret(ownerState);
    await postPanelUpdate(ownerState, {
      update: { type: "NewMessage", chat_id: "b0newowner", new_message: {
        message_id: "pv-claim-owner-001", chat_id: "b0newowner", text: "/claim correct-setup-code",
      } },
    }, ownerSecret);
    const ownerNotice = calls.find(call => call.method === "sendMessage" && String(call.payload.text).includes("مالک ثبت شد"));
    assert.ok(ownerNotice);
    assert.ok(ownerNotice.payload.text.includes("چت تأییدشده به پنل دسترسی دارد.\n\n🤖 پنل مدیریت"),
      "owner claim must use real line breaks immediately before the main panel text");
    assert.equal(ownerNotice.payload.text.includes("\\n"), false, "owner claim notice must not contain literal backslash-n");

    const inviteState = panelEnv();
    inviteState.DB.ownerChat = "b0existingowner";
    inviteState.DB.consoleOwners.set(1, {
      id: 1, chat_id: "b0existingowner", label: "مالک اصلی", role: "owner", enabled: 1,
      claimed_at: new Date().toISOString(), last_seen_at: null,
    });
    const inviteCode = "INV-" + "B".repeat(32);
    const codeHash = createHash("sha256").update(inviteCode).digest("hex");
    inviteState.DB.consoleInvites.set(1, {
      id: 1, code_hash: codeHash, created_at: new Date().toISOString(),
      expires_at: new Date(Date.now() + 3600_000).toISOString(), used_at: null, used_by: null,
    });
    const inviteSecret = await panel.hookSecret(inviteState);
    await postPanelUpdate(inviteState, {
      update: { type: "NewMessage", chat_id: "b0invitedviewer", new_message: {
        message_id: "pv-claim-invite-001", chat_id: "b0invitedviewer", text: "/claim " + inviteCode,
      } },
    }, inviteSecret);
    const inviteNotice = calls.find(call => call.method === "sendMessage" && String(call.payload.text).includes("دعوت پذیرفته شد"));
    assert.ok(inviteNotice);
    assert.ok(inviteNotice.payload.text.includes("دسترسی شما ثبت شد.\n\n🤖 پنل مدیریت"),
      "invite claim must use real line breaks immediately before the main panel text");
    assert.equal(inviteNotice.payload.text.includes("\\n"), false, "invite claim notice must not contain literal backslash-n");
  });
}



// Worker cron dispatcher: deterministic cadence, strict allowlist, fail-closed status checks.
{
  const cronSource = fs.readFileSync(path.join(root, "src/workflow_cron.js"), "utf8");
  const cron = await import(dataModuleUrl(cronSource));
  const expectedAllowlist = [
    "termux-docker-discovery.yml",
    "daily-queue-processor.yml",
    "mob-x-news-production.yml",
    "mob-x-link-processor.yml",
  ];
  assert.deepEqual(cron.WORKFLOW_CRON_ALLOWLIST, expectedAllowlist);
  assert.deepEqual(cron.workflowsDueForTick(1), [
    "termux-docker-discovery.yml", "mob-x-link-processor.yml",
  ]);
  assert.deepEqual(cron.workflowsDueForTick(6), [
    "termux-docker-discovery.yml", "daily-queue-processor.yml", "mob-x-link-processor.yml",
  ]);
  assert.deepEqual(cron.workflowsDueForTick(10), [
    "termux-docker-discovery.yml", "mob-x-news-production.yml", "mob-x-link-processor.yml",
  ]);
  assert.deepEqual(cron.workflowsDueForTick(30), expectedAllowlist);
  assert.deepEqual(cron.workflowsDueForTick(0), []);
  assert.deepEqual(cron.workflowsDueForTick(-1), []);
  assert.deepEqual(cron.workflowsDueForTick(Number.NaN), []);
}

// An in-progress workflow is skipped; only the idle workflow is dispatched once.
{
  const cronSource = fs.readFileSync(path.join(root, "src/workflow_cron.js"), "utf8");
  const cron = await import(dataModuleUrl(cronSource));
  const fetches = [];
  const dispatches = [];
  const logs = [];
  const result = await cron.runWorkflowCron(
    { scheduledTime: 0 },
    { GITHUB_DISPATCH_TOKEN: "cron-test-token-never-real", WORKFLOW_CRON_DISPATCH_ENABLED: "true" },
    async (_env, workflow, inputs, options) => {
      dispatches.push({ workflow, inputs, options });
      return { ok: true, status: 204 };
    },
    {
      fetcher: async url => {
        fetches.push(String(url));
        const active = String(url).includes("termux-docker-discovery.yml");
        return new Response(JSON.stringify({
          workflow_runs: active ? [{ status: "in_progress" }] : [],
        }), { status: 200 });
      },
      logger: entry => logs.push(entry),
    },
  );
  assert.equal(fetches.length, 2);
  assert.equal(dispatches.length, 1);
  assert.equal(dispatches[0].workflow, "mob-x-link-processor.yml");
  assert.equal(dispatches[0].inputs, null);
  assert.equal(dispatches[0].options.maxAttempts, 1);
  assert.equal(result.entries.find(x => x.workflow === "termux-docker-discovery.yml").result, "skipped");
  assert.equal(result.entries.find(x => x.workflow === "mob-x-link-processor.yml").result, "dispatched");
  assert.deepEqual(logs.map(x => Object.keys(x).sort()), [
    ["http_status", "result", "workflow"],
    ["http_status", "result", "workflow"],
  ]);
  assert.equal(JSON.stringify(logs).includes("cron-test-token-never-real"), false);
}

// The kill switch defaults ON but an explicit false value prevents all GitHub calls.
{
  const cronSource = fs.readFileSync(path.join(root, "src/workflow_cron.js"), "utf8");
  const cron = await import(dataModuleUrl(cronSource));
  let calls = 0;
  const result = await cron.runWorkflowCron(
    { scheduledTime: 0 },
    { GITHUB_DISPATCH_TOKEN: "cron-test-token-never-real", WORKFLOW_CRON_DISPATCH_ENABLED: "false" },
    async () => { calls += 1; return { ok: true, status: 204 }; },
    {
      fetcher: async () => { calls += 1; return new Response("{}", { status: 200 }); },
      logger: () => {},
    },
  );
  assert.equal(calls, 0);
  assert.ok(result.entries.every(x => x.result === "skipped" && x.http_status === null));
}

// The switch is ON when unset, and the real Worker entrypoint delegates to the cron runner.
{
  const cronSource = fs.readFileSync(path.join(root, "src/workflow_cron.js"), "utf8");
  const cron = await import(dataModuleUrl(cronSource));
  let dispatchCount = 0;
  const result = await cron.runWorkflowCron(
    { scheduledTime: 0 },
    { GITHUB_DISPATCH_TOKEN: "cron-test-token-never-real" },
    async () => { dispatchCount += 1; return { ok: true, status: 204 }; },
    { fetcher: async () => new Response(JSON.stringify({ workflow_runs: [] }), { status: 200 }), logger: () => {} },
  );
  assert.equal(result.enabled, true);
  assert.equal(dispatchCount, 2);
  const workerResult = await worker.scheduled(
    { scheduledTime: 0 },
    { WORKFLOW_CRON_DISPATCH_ENABLED: "false" },
  );
  assert.equal(workerResult.entries.length, 2);
  assert.ok(workerResult.entries.every(x => x.result === "skipped"));
}

// GitHub API errors are logged safely and never retried or dispatched.
{
  const cronSource = fs.readFileSync(path.join(root, "src/workflow_cron.js"), "utf8");
  const cron = await import(dataModuleUrl(cronSource));
  let fetchCount = 0;
  let dispatchCount = 0;
  const logs = [];
  const result = await cron.runWorkflowCron(
    { scheduledTime: 0 },
    { GITHUB_DISPATCH_TOKEN: "cron-test-token-never-real", WORKFLOW_CRON_DISPATCH_ENABLED: "true" },
    async () => { dispatchCount += 1; return { ok: true, status: 204 }; },
    {
      fetcher: async () => { fetchCount += 1; return new Response("private response body", { status: 403 }); },
      logger: entry => logs.push(entry),
    },
  );
  assert.equal(fetchCount, 2);
  assert.equal(dispatchCount, 0);
  assert.ok(result.entries.every(x => x.result === "error" && x.http_status === 403));
  assert.equal(JSON.stringify(logs).includes("private response body"), false);
  assert.equal(JSON.stringify(logs).includes("cron-test-token-never-real"), false);
}

// The shared dispatch helper accepts the exact scheduled inputs and performs one safe attempt.
{
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url: String(url), options });
    return new Response("response body must not be surfaced", { status: 503 });
  };
  try {
    const daily = await panel.dispatchMobXWorkflow(
      { GITHUB_DISPATCH_TOKEN: "cron-test-token-never-real" },
      "daily-queue-processor.yml",
      { publish_four_tiktok_test: true },
      { maxAttempts: 1, safeStatusOnly: true },
    );
    assert.equal(calls.length, 1);
    assert.equal(daily.ok, false);
    assert.equal(daily.status, 503);
    assert.equal(daily.error_detail, undefined);
    assert.deepEqual(JSON.parse(calls[0].options.body), {
      ref: "main", inputs: { publish_four_tiktok_test: "true" },
    });

    calls.length = 0;
    const termux = await panel.dispatchMobXWorkflow(
      { GITHUB_DISPATCH_TOKEN: "cron-test-token-never-real" },
      "termux-docker-discovery.yml",
      undefined,
      { maxAttempts: 1, safeStatusOnly: true },
    );
    assert.equal(calls.length, 1);
    assert.equal(termux.status, 503);
    assert.deepEqual(JSON.parse(calls[0].options.body), { ref: "main" });

    calls.length = 0;
    const news = await panel.dispatchMobXWorkflow(
      { GITHUB_DISPATCH_TOKEN: "cron-test-token-never-real" },
      "mob-x-news-production.yml",
      undefined,
      { maxAttempts: 1, safeStatusOnly: true },
    );
    assert.equal(calls.length, 1);
    assert.equal(news.status, 503);
    assert.deepEqual(JSON.parse(calls[0].options.body), { ref: "main" });

    calls.length = 0;
    const link = await panel.dispatchMobXWorkflow(
      { GITHUB_DISPATCH_TOKEN: "cron-test-token-never-real" },
      "mob-x-link-processor.yml",
      undefined,
      { maxAttempts: 1, safeStatusOnly: true },
    );
    assert.equal(calls.length, 1);
    assert.equal(link.status, 503);
    assert.deepEqual(JSON.parse(calls[0].options.body), { ref: "main" });

    calls.length = 0;
    const denied = await panel.dispatchMobXWorkflow(
      { GITHUB_DISPATCH_TOKEN: "cron-test-token-never-real" },
      "not-allowlisted.yml",
      {},
      { maxAttempts: 1, safeStatusOnly: true },
    );
    assert.equal(denied.ok, false);
    assert.equal(calls.length, 0);
  } finally {
    globalThis.fetch = originalFetch;
  }
}

console.log("Worker tests passed: live panel tracking, stage-two D1 screens, safe confirmation, bounded timings, and owner isolation.");
