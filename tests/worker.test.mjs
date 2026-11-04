import assert from "node:assert/strict";
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
  .replace('from "./mob_x_caption.js";', 'from "' + mobXCaptionModuleUrl + '";');
const consoleApiModuleUrl = dataModuleUrl(consoleApiSource);
const originalSource = fs.readFileSync(path.join(root, "src/index.js"), "utf8");
const workerImport = 'import { handleOwnerPanel } from "./owner_panel.js";';
const consoleApiImport = 'import { handleConsoleApi, corsPreflight, withCors } from "./console_api.js";';
assert.ok(originalSource.includes(workerImport), "Worker must import owner panel handler");
assert.ok(originalSource.includes(consoleApiImport), "Worker must import console API handler");
const source = originalSource
  .replace(workerImport, `const { handleOwnerPanel } = await import("${panelModuleUrl}");`)
  .replace(consoleApiImport, `const { handleConsoleApi, corsPreflight, withCors } = await import("${consoleApiModuleUrl}");`);
const moduleUrl = "data:text/javascript;base64," + Buffer.from(source).toString("base64");
const imported = await import(moduleUrl);
const worker = imported.default;
const panel = importedPanel;

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
    if (this.sql.includes("FROM failed_media WHERE status IN")) {
      const limit = Number(this.values[0] || 0);
      const rows = [...this.db.failedMedia.values()]
        .filter(row => ["stored", "delete_failed", "delete_requested"].includes(row.status))
        .sort((a, b) => String(a.updated_at).localeCompare(String(b.updated_at)) || String(a.id).localeCompare(String(b.id)))
        .slice(0, limit);
      return { results: rows.map(({ id, storage_key, status, updated_at }) => ({ id, storage_key, status, updated_at })) };
    }
    if (this.sql.includes("FROM failed_media WHERE id IN")) {
      const ids = new Set(this.values);
      return { results: [...this.db.failedMedia.values()].filter(row => ids.has(row.id))
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
    if (this.sql.includes("SELECT id, platform, external_id, media_sha256, status, sent_at, review_state FROM items")) {
      this.db.seenQuery = { sql: this.sql, values: this.values };
      const all = [...this.db.items.values()].map(item => ({
        id: item.id, platform: item.platform, external_id: item.external_id,
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
        if (!row || row.status !== status || String(row.updated_at ?? "") !== updatedAt || row.storage_key !== storageKey) {
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
    if (sql.startsWith("INSERT INTO items")) {
      const item = {
        id: v[0], platform: v[1], source_key: v[2], external_id: v[3],
        canonical_url: v[4], title: v[5], caption: v[6], published_at: v[7],
        status: v[8], media_sha256: v[9], media_bytes: v[10], media_mime: v[11],
        media_type: v[12], source_tier: v[13], storage_key: v[14],
        validation_json: v[15], moderation_json: v[16], review_state: v[17],
        original_caption: v[18], persian_translation: v[19],
      };
      const previous = this.db.items.get(item.id);
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