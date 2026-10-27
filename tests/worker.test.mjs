import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const screensSource = fs.readFileSync(path.join(root, "src/panel_screens.js"), "utf8");
const screensModuleUrl = "data:text/javascript;base64," + Buffer.from(screensSource).toString("base64");
const settingsSource = fs.readFileSync(path.join(root, "src/panel_settings.js"), "utf8");
const settingsModuleUrl = "data:text/javascript;base64," + Buffer.from(settingsSource).toString("base64");
const panelSource = fs.readFileSync(path.join(root, "src/owner_panel.js"), "utf8")
  .replace('from "./panel_screens.js";', 'from "' + screensModuleUrl + '";')
  .replace('from "./panel_settings.js";', 'from "' + settingsModuleUrl + '";');
const panelModuleUrl = "data:text/javascript;base64," + Buffer.from(panelSource).toString("base64");
const importedPanel = await import(panelModuleUrl);
const screens = await import(screensModuleUrl);
const consoleApiSource = fs.readFileSync(path.join(root, "src/console_api.js"), "utf8");
const consoleApiModuleUrl = "data:text/javascript;base64," + Buffer.from(consoleApiSource).toString("base64");
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
      const pair = [...this.db.consoleJobs.values()]
        .filter(job => job.type === "mob_x_link" && ["confirmed", "queued"].includes(job.status))
        .sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)))
        .map(job => ({ job, link: this.db.panelLinks.get(Number(job.panel_link_id)) }))
        .find(value => value.link && ["confirmed", "queued"].includes(value.link.status));
      return pair ? { id: pair.job.id, panel_link_id: pair.job.panel_link_id, url: pair.link.url, external_id: pair.link.external_id } : null;
    }
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
    if (this.sql.includes("FROM owner_digest_events WHERE window_id = ? GROUP BY category")) {
      const windowId = this.values[0];
      const totals = new Map();
      for (const row of this.db.ownerDigestEvents) {
        if (row.window_id === windowId) totals.set(row.category, (totals.get(row.category) || 0) + 1);
      }
      return { results: [...totals.entries()].map(([category, total]) => ({ category, total })) };
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
    if (this.sql.includes("FROM console_jobs ORDER BY created_at DESC LIMIT ?")) {
      return { results: [...this.db.consoleJobs.values()].sort((a, b) => String(b.created_at).localeCompare(String(a.created_at))).slice(0, this.values[0]).map(row => ({ ...row })) };
    }
    return { results: [] };
  }
  async run() {
    const sql = this.sql;
    const v = this.values;
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
    if (sql.startsWith("INSERT INTO panel_links (url, external_id, status)")) {
      const [url, externalId] = v;
      if ([...this.db.panelLinks.values()].some(row => row.external_id === String(externalId) || row.url === url)) return { meta: { changes: 0 } };
      const id = this.db.nextPanelLinkId++;
      this.db.panelLinks.set(id, { id, url, external_id: String(externalId), status: "pending", created_at: new Date().toISOString(), confirmed_at: null, updated_at: new Date().toISOString() });
      return { meta: { changes: 1, last_row_id: id } };
    }
    if (sql.startsWith("UPDATE panel_links SET status = 'rejected'")) {
      const row = this.db.panelLinks.get(Number(v[0]));
      if (!row || row.status !== "pending") return { meta: { changes: 0 } };
      Object.assign(row, { status: "rejected", updated_at: new Date().toISOString() });
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
      if (!job || job.type !== "mob_x_link" || job.status !== "processing") return { meta: { changes: 0 } };
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
    this.ownerDigestState = null;
    this.ownerDigestEvents = [];
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
const env = () => ({ DB: new FakeDB(), AGENT_API_TOKEN: TOKEN });
const request = (url, { method = "GET", body, token = TOKEN, headers = {} } = {}) =>
  new Request("https://worker.test" + url, {
    method,
    headers: {
      ...(body === undefined ? {} : { "content-type": "application/json" }),
      ...(token === null ? {} : { authorization: "Bearer " + token }),
      ...headers,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
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
    is_poppy_playtime: true,
    is_edit: true,
    high_quality_edit: true,
    has_music: true,
  };
  const base = {
    platform: "tiktok", external_id: "7616247520657771796",
    canonical_url: "https://www.tiktok.com/@creator/video/7616247520657771796",
    source_key: "@creator", title: "Poppy Playtime edit", status: "ready",
    review_state: "approved", media_type: "video", source_tier: "trusted",
    media_sha256: "a".repeat(64), media_bytes: 1000000,
    validation: { ok: true, width: 1080, height: 1080, mime_type: "video/mp4" },
    moderation: goodModeration,
  };
  const response = await read(await worker.fetch(request("/api/brain/items/upsert", {
    method: "POST", body: base,
  }), state));
  assert.equal(response.status, 200);
  assert.equal(response.body.item_id, "tiktok:7616247520657771796");

  const queue = await read(await worker.fetch(request("/api/brain/queue?limit=999"), state));
  assert.equal(queue.status, 200);
  assert.equal(queue.body.publishing, false);
  assert.equal(state.DB.queueLimit, 100);
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
{
  const state = env();
  const officialYoutubePost = {
    platform: "mob_official",
    external_id: "yt-abcdefghijk",
    canonical_url: "https://www.youtube.com/watch?v=abcdefghijk",
    source_key: "youtube.com",
    status: "discovered",
    review_state: "pending",
    media_type: "video",
    source_tier: "official",
    original_caption: "Official Mob caption",
    persian_translation: "کپشن رسمی",
  };
  const response = await read(await worker.fetch(request("/api/brain/items/upsert", {
    method: "POST", body: officialYoutubePost,
  }), state));
  assert.equal(response.status, 200);
  assert.equal(response.body.item_id, "mob_official:yt-abcdefghijk");
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
  const state = env();
  let aiCalled = false;
  state.AI = { run: async () => { aiCalled = true; throw new Error("translation inference must be disabled"); } };
  const response = await read(await worker.fetch(request("/api/brain/translate-official", {
    method: "POST",
    body: { text: "Mob Entertainment published an official update." },
  }), state));
  assert.equal(response.status, 410);
  assert.equal(response.body.error, "translation_route_retired");
  assert.equal(aiCalled, false);
  assert.equal(state.DB.aiUsageDaily.size, 0);
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
    if (this.sql.startsWith("INSERT INTO panel_state") && this.sql.includes("RETURNING value")) {
      const next = Number(this.db.state.get("panel_seq") || 0) + 1;
      this.db.state.set("panel_seq", String(next));
      return { value: String(next) };
    }
    if (this.sql.includes("SELECT chat_id FROM panel_owner WHERE id=1")) {
      return this.db.ownerChat ? { chat_id: this.db.ownerChat } : null;
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
    if (this.sql.includes("FROM panel_links ORDER BY id DESC")) {
      return { results: this.db.links.slice().reverse().slice(0, 8).map(row => ({ ...row })) };
    }
    return { results: [] };
  }
  async run() {
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
    if (this.sql.startsWith("DELETE FROM panel_links")) {
      const count = this.db.links.length;
      this.db.links = [];
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
    // Legacy panel tests opt into the explicit in-place mode; atomic behavior has dedicated tests below.
    this.state = new Map([["panel_sync_mode", "inplace"]]);
    this.events = new Set();
    this.links = [];
    this.linkDeleteCalls = 0;
    this.nextPanelLinkId = 1;
    this.consoleJobs = new Map();
    this.consoleOwners = new Map();
    this.nextConsoleOwnerId = 1;
    this.consoleInvites = new Map();
    this.brainEvents = [];
    this.lastChanges = 0;
    this.failReadOnlyBatch = false;
  }
  prepare(sql) { return new PanelFakeStatement(this, sql); }
  async batch(statements) {
    if (statements.length && statements.every((statement) => /^SELECT\b/i.test(statement.sql.trim()))) {
      if (this.failReadOnlyBatch) throw new Error("mock_d1_read_failure");
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
    calls.push({ method, payload });
    const custom = responder ? await responder({ url, init, method, payload, calls }) : null;
    if (custom instanceof Response) return custom;
    const messageCount = calls.filter(call => call.method === "sendMessage").length;
    return new Response(JSON.stringify({ status: "OK", data: { message_id: "mock-reply-" + messageCount } }), {
      status: 200, headers: { "content-type": "application/json" },
    });
  };
  try { await fn(calls); } finally { globalThis.fetch = originalFetch; }
}
async function postPanelUpdate(state, update, secret) {
  const pending = [];
  const ctx = { waitUntil(promise) { pending.push(promise); } };
  const response = await worker.fetch(new Request(
    "https://worker.test/panel/hook/" + secret + "/ReceiveUpdate",
    { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(update) }
  ), state, ctx);
  await Promise.all(pending);
  return response;
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

    await click("p_mob", "mob-link-open-1");
    await postText("https://x.com/mobgamesstudios/status/2107878755079213476", "mob-link-text-2");
    assert.equal(state.DB.links.length, 1);
    assert.equal(state.DB.links[0].status, "pending");
    assert.equal(state.DB.consoleJobs.size, 0, "submitting a link must not start processing before owner confirmation");
    assert.equal(calls.some(call => call.method === "sendMessage" && call.payload.text.includes("این لینک است؟")), true);

    const confirmMessageId = state.DB.state.get("panel_mid");
    await click("p_mob_confirm", confirmMessageId);
    assert.equal(state.DB.links[0].status, "confirmed");
    assert.equal(state.DB.consoleJobs.size, 1);
    const job = [...state.DB.consoleJobs.values()][0];
    assert.equal(job.type, "mob_x_link");
    assert.equal(job.status, "confirmed");
    assert.deepEqual(JSON.parse(job.params_json), {
      url: "https://x.com/mobgamesstudios/status/2107878755079213476",
      panel_link_id: state.DB.links[0].id,
    });
    assert.equal(calls.filter(call => call.method === "editMessageText").length > 0, true);
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
    const fallbackSend = calls.filter(call => call.method === "sendMessage").at(-1);
    assert.equal(fallbackSend.payload.text, failedEdit.payload.text);
    assert.equal(Array.isArray(fallbackSend.payload.inline_keypad.rows), true);
    assert.equal(fallbackSend.payload.inline_keypad.rows.some(row => row.buttons && row.buttons.length > 0), true);
    assert.equal(calls.some(call => call.method === "editMessageKeypad" &&
      (!call.payload.inline_keypad.rows.length ||
       !call.payload.inline_keypad.rows.some(row => row.buttons && row.buttons.length > 0))), false);
  }, ({ method }) => method === "editMessageText"
    ? new Response(JSON.stringify({ status: "EDIT_FAILED" }), { status: 200 })
    : null);
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
  }, ({ method }) => method === "editMessageText"
    ? new Response(JSON.stringify({ status: "UNAVAILABLE" }), { status: 503 })
    : null);
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
      { msg_age_s: 1.25, total_ms: 10, rubika_calls: 2, rubika_ms: 6, d1_ms: 3 },
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
    assert.equal(debugSend.payload.text.includes('"rubika_calls":2'), true);
    assert.equal(debugSend.payload.text.includes("chat_tail: wner"), true);
    assert.equal(debugSend.payload.text.includes("message_id_tail: 1234"), true);
    assert.equal(debugSend.payload.text.includes("b0actualowner"), false);
    assert.equal(debugSend.payload.text.includes("private-debug-message-1234"), false);
  });
}


// Atomic mode sends the whole text+keypad as one Rubika message; missing mode defaults to atomic.
{
  await withMockRubikaFetch(async calls => {
    const state = panelEnv();
    state.DB.ownerChat = "b0actualowner";
    state.DB.state.delete("panel_sync_mode");
    state.DB.state.set("panel_mid", "atomic-old-panel-001");
    const secret = await panel.hookSecret(state);
    await postPanelUpdate(state, {
      update: { type: "NewMessage", chat_id: "b0actualowner", new_message: {
        message_id: "atomic-old-panel-001", chat_id: "b0actualowner",
        text: "", aux_data: { button_id: "p_status" },
      } },
    }, secret);
    const sent = calls.filter(call => call.method === "sendMessage");
    assert.equal(sent.length, 1);
    assert.ok(sent[0].payload.inline_keypad?.rows?.some(row => row.buttons?.length));
    assert.equal(calls.some(call => call.method === "editMessageText" || call.method === "editMessageKeypad"), false);
    assert.equal(calls.some(call => call.method === "deleteMessage" && call.payload.message_id === "atomic-old-panel-001"), true);
    assert.equal(state.DB.state.get("panel_mid"), "mock-reply-1");
    assert.equal(state.DB.state.get("panel_sync_mode") || "atomic", "atomic");
    assert.equal(state.DB.state.get("panel_edit_ok"), "1");
    assert.equal(state.DB.state.get("atomic_ok"), "1");
    assert.equal(state.DB.state.get("atomic_fail") || "0", "0");
  });
}

// A late first Rubika response is deleted after the newer event wins; old text/buttons cannot become current again.
{
  let signalSlowStarted;
  const slowStarted = new Promise(resolve => { signalSlowStarted = resolve; });
  let releaseSlow;
  const slowGate = new Promise(resolve => { releaseSlow = resolve; });
  let sendCount = 0;
  const responder = async ({ method }) => {
    if (method !== "sendMessage") return null;
    sendCount += 1;
    if (sendCount === 1) {
      signalSlowStarted();
      await slowGate;
      return new Response(JSON.stringify({ status: "OK", data: { message_id: "slow-stale-panel-01" } }), {
        status: 200, headers: { "content-type": "application/json" },
      });
    }
    return new Response(JSON.stringify({ status: "OK", data: { message_id: "fast-current-panel-02" } }), {
      status: 200, headers: { "content-type": "application/json" },
    });
  };
  await withMockRubikaFetch(async calls => {
    const state = panelEnv();
    state.DB.ownerChat = "b0actualowner";
    state.DB.state.set("panel_sync_mode", "atomic");
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
    assert.equal(state.DB.state.get("panel_mid"), "fast-current-panel-02");
    assert.equal(calls.some(call => call.method === "deleteMessage" && call.payload.message_id === "slow-stale-panel-01"), true);
    assert.equal(calls.some(call => call.method === "editMessageText" || call.method === "editMessageKeypad"), false);
    assert.equal(state.DB.state.get("atomic_ok"), "1");
    assert.equal(state.DB.state.get("atomic_fail") || "0", "0");
    assert.equal(calls.filter(call => call.method === "sendMessage").length, 2);
  }, responder);
}

// A duplicate tap with the same event key is idempotently ignored.
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
    await postPanelUpdate(state, update, secret);
    await postPanelUpdate(state, update, secret);
    assert.equal(calls.filter(call => call.method === "sendMessage").length, 1);
    assert.equal(state.DB.state.get("panel_mid"), "mock-reply-1");
    assert.equal(calls.some(call => call.method === "editMessageKeypad" || call.method === "editMessageText"), false);
  });
}

// When replacement send fails in atomic mode, keep the previous coherent panel and never start an in-place edit.
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
    assert.equal(state.DB.state.get("last_error"), "rubika_sendmessage_send_failed");
    assert.equal(state.DB.state.get("atomic_fail"), "1");
    assert.equal(state.DB.state.get("atomic_ok") || "0", "0");
    assert.equal(state.DB.state.get("panel_edit_fail"), "1");
    assert.equal(calls.some(call => call.method === "editMessageText" || call.method === "editMessageKeypad"), false);
    assert.equal(calls.some(call => call.method === "deleteMessage" && call.payload.message_id === "atomic-send-failure-old"), false);
  }, ({ method }) => method === "sendMessage"
    ? new Response(JSON.stringify({ status: "SEND_FAILED" }), { status: 200 })
    : null);
}

// A best-effort old-panel delete failure must not corrupt the newly sent atomic panel.
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
    assert.equal(state.DB.state.get("panel_mid"), "mock-reply-1");
    assert.equal(calls.some(call => call.method === "deleteMessage" && call.payload.message_id === "atomic-delete-failure-old"), true);
    assert.equal(calls.some(call => call.method === "editMessageText" || call.method === "editMessageKeypad"), false);
    assert.equal(state.DB.state.get("atomic_ok"), "1");
    assert.equal(state.DB.state.get("atomic_fail") || "0", "0");
  }, ({ method }) => method === "deleteMessage"
    ? new Response(JSON.stringify({ status: "DELETE_FAILED" }), { status: 200 })
    : null);
}

// /panelmode can be changed only by the primary owner.
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
    assert.equal(state.DB.state.get("panel_sync_mode"), "atomic");
    assert.equal(state.DB.state.get("panel_mid"), "mock-reply-1");
    assert.equal(calls.some(call => call.method === "deleteMessage" && call.payload.message_id === "panelmode-old-panel"), true);
    assert.equal(calls.some(call => call.method === "editMessageText" || call.method === "editMessageKeypad"), false);
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
  });
}

// A stale confirm button is never executed; stale messages are deleted and the safe related page is refreshed.
{
  await withMockRubikaFetch(async calls => {
    const state = panelEnv();
    state.DB.ownerChat = "b0actualowner";
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
    assert.equal(state.DB.state.get("panel_mid"), "mock-reply-1");
    assert.equal(calls.filter(call => call.method === "sendMessage").length, 1);
    assert.equal(calls.some(call => call.method === "deleteMessage" && call.payload.message_id === "stale-click-message-1234"), true);
    assert.equal(calls.some(call => call.method === "deleteMessage" && call.payload.message_id === "latest-live-panel-9876"), true);
    assert.equal(calls.some(call => call.method === "editMessageText" || call.method === "editMessageKeypad"), false);
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
    assert.equal(state.DB.state.get("panel_edit_ok"), "1");
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
    const textIndex = calls.findIndex(call => call.method === "editMessageText");
    const keypadIndexes = calls.map((call, index) => call.method === "editMessageKeypad" ? index : -1).filter(index => index >= 0);
    const sendIndex = calls.findIndex(call => call.method === "sendMessage");
    assert.equal(keypadIndexes.length, 2);
    assert.equal(textIndex >= 0 && keypadIndexes[0] > textIndex, true);
    assert.equal(keypadIndexes[1] > keypadIndexes[0], true);
    assert.equal(sendIndex > keypadIndexes[1], true);
    assert.equal(state.DB.state.get("last_error"), "rubika_editmessagekeypad_edit_failed");
    assert.equal(state.DB.state.get("panel_edit_fail"), "1");
    assert.equal(state.DB.state.get("panel_mid"), "mock-reply-1");
    const fallback = calls[sendIndex];
    assert.equal(Array.isArray(fallback.payload.inline_keypad.rows), true);
    assert.equal(fallback.payload.inline_keypad.rows.some(row => row.buttons && row.buttons.length > 0), true);
    assert.equal(calls.some(call => call.method === "deleteMessage" && call.payload.message_id === "old-keypad-panel-1357"), true);
  }, ({ method }) => method === "editMessageKeypad"
    ? new Response(JSON.stringify({ status: "EDIT_FAILED" }), { status: 200 })
    : null);
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


// Non-report pages fetch data before rendering and only edit the existing live panel once.
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
    assert.equal(calls.filter(call => call.method === "sendMessage").length, 0);
    assert.equal(calls.filter(call => call.method === "editMessageText").length, 1);
    assert.equal(calls.filter(call => call.method === "editMessageKeypad").length, 1);
    assert.equal(calls.every(call => !("message_id" in call.payload) || call.payload.message_id === "existing-live-status-4321"), true);
    assert.equal(state.DB.state.get("panel_edit_ok"), "1");
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
      assert.equal(typeof row.rubika_ms, "number");
      assert.equal(typeof row.d1_ms, "number");
      assert.equal(row.rubika_calls, 2);
      assert.equal(row.total_ms >= 0, true);
      assert.equal(row.rubika_ms >= 0, true);
      assert.equal(row.d1_ms >= 0, true);
      assert.deepEqual(Object.keys(row).sort(), ["d1_ms", "msg_age_s", "rubika_calls", "rubika_ms", "total_ms"]);
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
    if (this.sql.startsWith("DELETE FROM panel_links")) {
      const count = this.db.links.length;
      this.db.links = [];
      return { meta: { changes: count } };
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
    ["p_jobs", "کارهای کنسول"], ["p_links_clear", "همهٔ 0 لینک"],
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
    links: [{ id: 1, url: "https://x.com/mobgamesstudios/status/2107878755079213476", external_id: "2107878755079213476", status: "confirmed", created_at: "2026-10-24T05:00:00Z" }],
  };
  const seeded = screenEnv(seed);
  for (const [id, phrase] of [
    ["p_digest", "Digest title"], ["p_errors", "Digest title"], ["p_runs", "success"],
    ["p_qs", "در انتظار: 1"], ["p_ailog", "groq/code_repair"], ["p_links", "mobgamesstudios"],
    ["p_links_clear", "همهٔ 1 لینک"],
  ]) {
    const result = await screens.buildScreen(seeded, id);
    assert.equal(result.text.includes(phrase), true, id + " should show seeded data");
    assert.equal(result.text.includes("d1_error"), false, id + " should not fail with seeded data");
  }
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

// Fresh commands replace the panel only for the four requested screens; other text commands keep edit-in-place behavior.
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
    const freshCommands = new Set(["/digest", "/errors", "/runs", "/links"]);
    let expectedSends = 0;
    let expectedEdits = 0;
    let currentPanel = "one-live-screen-panel";
    for (let i = 0; i < commands.length; i += 1) {
      await postPanelUpdate(state, {
        update: { type: "NewMessage", chat_id: "b0actualowner", new_message: {
          message_id: "stage-two-command-" + i, chat_id: "b0actualowner", text: commands[i][0],
        } },
      }, secret);
      const editCalls = calls.filter(call => call.method === "editMessageText");
      const sendCalls = calls.filter(call => call.method === "sendMessage");
      if (freshCommands.has(commands[i][0])) {
        expectedSends += 1;
        assert.equal(sendCalls.length, expectedSends, commands[i][0] + " should send one fresh page");
        assert.equal(sendCalls.at(-1).payload.text.includes(commands[i][1]), true, commands[i][0] + " should render its screen");
        assert.equal(state.DB.state.get("panel_mid"), "mock-reply-" + expectedSends);
        assert.equal(calls.some(call => call.method === "deleteMessage" && call.payload.message_id === currentPanel), true);
        currentPanel = state.DB.state.get("panel_mid");
      } else {
        expectedEdits += 1;
        assert.equal(editCalls.length, expectedEdits, commands[i][0] + " should edit exactly one page");
        assert.equal(editCalls.at(-1).payload.message_id, currentPanel);
        assert.equal(editCalls.at(-1).payload.text.includes(commands[i][1]), true, commands[i][0] + " should render its screen");
      }
      assert.equal(calls.some(call => call.method === "sendMessage" && String(call.payload.text).includes("در حال دریافت")), false);
      assert.equal(state.DB.state.get("mode"), "idle");
    }
    assert.equal(calls.filter(call => call.method === "sendMessage").length, 4);
    assert.equal(calls.filter(call => call.method === "editMessageText").length, 2);
  });
}

// Link deletion requires the explicit confirmation screen; a direct confirmation ID is harmless.
{
  await withMockRubikaFetch(async calls => {
    const state = panelEnv();
    state.DB.ownerChat = "b0actualowner";
    state.DB.links = [{ url: "https://x.com/Test/status/2107878755079213476", status: "queued", created_at: "2026-10-24T05:00:00Z" }];
    state.DB.state.set("panel_sync_mode", "atomic");
    state.DB.state.set("panel_mid", "link-confirmation-panel");
    const secret = await panel.hookSecret(state);
    const click = async (button, messageId) => postPanelUpdate(state, {
      update: { type: "NewMessage", chat_id: "b0actualowner", new_message: {
        message_id: messageId, chat_id: "b0actualowner", text: "", aux_data: { button_id: button },
      } },
    }, secret);
    await click("p_links_clear_yes", "link-confirmation-panel");
    assert.equal(state.DB.links.length, 1, "direct confirmation ID must not delete links");
    assert.equal(state.DB.linkDeleteCalls, 0);
    assert.equal(state.DB.state.get("panel_mid"), "mock-reply-1");
    await click("p_links_clear", "mock-reply-1");
    assert.equal(state.DB.links.length, 1, "opening confirmation must not delete links");
    assert.equal(calls.filter(call => call.method === "sendMessage").at(-1).payload.text.includes("پاک شود؟"), true);
    assert.equal(state.DB.state.get("panel_mid"), "mock-reply-2");
    await click("p_links_clear_yes", "mock-reply-2");
    assert.equal(state.DB.links.length, 0, "confirmed action should clear links");
    assert.equal(state.DB.linkDeleteCalls, 1);
    assert.equal(calls.filter(call => call.method === "sendMessage").at(-1).payload.text.includes("پاک شدند"), true);
    assert.equal(calls.some(call => call.method === "editMessageKeypad"), false);
  });
}

// PV settings are wired from the owner home menu and apply the existing optimistic-lock console_rules contract.
{
  assert.equal(screens.HOME_ROWS.flat().some(([label, id]) => label === "⚙️ تنظیمات" && id === "p_set"), true,
    "owner home menu must include the settings entry");

  await withMockRubikaFetch(async calls => {
    const state = panelEnv();
    state.DB.ownerChat = "b0actualowner";
    state.DB.state.set("panel_sync_mode", "atomic");
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
    assert.equal(rules().daily_success_cap, 5, "➕ raises the cap by one");
    assert.equal(state.DB.consoleRules.version, 2, "successful change increments the rules version once");
    assert.equal(rules().publishing_enabled, false);
    assert.equal(rules().test_channel_only, true);

    await click("p_set_cap_dec");
    assert.equal(rules().daily_success_cap, 4, "➖ lowers the cap by one");
    assert.equal(state.DB.consoleRules.version, 3, "second successful change increments version once");

    await click("p_set_cap_7");
    assert.equal(rules().daily_success_cap, 7, "preset button sets the selected cap");
    assert.equal(state.DB.consoleRules.version, 4, "preset increments version once");
    assert.equal(rules().publishing_enabled, false, "settings must never enable publishing");
    assert.equal(rules().test_channel_only, true, "settings must preserve test-channel-only mode");
    assert.deepEqual(state.DB.brainEvents.map(event => event.code), [
      "console_rules_update", "console_rules_update", "console_rules_update",
    ], "every successful setting change is audited");
    const details = JSON.parse(state.DB.brainEvents[0].details_json);
    assert.deepEqual(details.fields, ["daily_success_cap"]);
    assert.equal(details.source, "pv");
    assert.equal(calls.some(call => call.method === "editMessageKeypad"), false,
      "atomic settings navigation must not edit the keypad separately");
    assert.equal(calls.some(call => call.method === "sendMessage" && String(call.payload.text).includes("سقف ارسال موفق امروز")), true);
  });
}

// The settings cap rejects both boundaries (0 and 50) without changing the value/version or writing audit events.
{
  await withMockRubikaFetch(async calls => {
    const state = panelEnv();
    state.DB.ownerChat = "b0actualowner";
    state.DB.state.set("panel_sync_mode", "atomic");
    state.DB.state.set("panel_mid", "settings-boundary-panel");
    state.DB.consoleRules.version = 7;
    state.DB.consoleRules.rules_json = JSON.stringify({ daily_success_cap: 0, publishing_enabled: false, test_channel_only: true });
    const secret = await panel.hookSecret(state);
    const click = async button => {
      await postPanelUpdate(state, {
        update: { type: "NewMessage", chat_id: "b0actualowner", new_message: {
          message_id: state.DB.state.get("panel_mid"), chat_id: "b0actualowner", text: "", aux_data: { button_id: button },
        } },
      }, secret);
    };
    await click("p_set_cap_dec");
    assert.equal(JSON.parse(state.DB.consoleRules.rules_json).daily_success_cap, 0);
    assert.equal(state.DB.consoleRules.version, 7);
    assert.equal(state.DB.brainEvents.filter(event => event.code === "console_rules_update").length, 0);
    assert.equal(calls.filter(call => call.method === "sendMessage").at(-1).payload.text.includes("عدد خارج از محدوده"), true);

    state.DB.consoleRules.rules_json = JSON.stringify({ daily_success_cap: 50, publishing_enabled: false, test_channel_only: true });
    await click("p_set_cap_inc");
    assert.equal(JSON.parse(state.DB.consoleRules.rules_json).daily_success_cap, 50);
    assert.equal(state.DB.consoleRules.version, 7);
    assert.equal(state.DB.brainEvents.filter(event => event.code === "console_rules_update").length, 0);
    assert.equal(calls.filter(call => call.method === "sendMessage").at(-1).payload.text.includes("عدد خارج از محدوده"), true);
  });
}

// Optimistic-lock conflict shows the existing Persian conflict notice, leaves the new concurrent version untouched, and writes no audit.
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
    assert.equal(state.DB.consoleRules.version, 2, "mock concurrent write owns the newer version");
    assert.equal(JSON.parse(state.DB.consoleRules.rules_json).daily_success_cap, 4, "conflicting update must not overwrite the value");
    assert.equal(state.DB.brainEvents.filter(event => event.code === "console_rules_update").length, 0);
    assert.equal(calls.filter(call => call.method === "sendMessage").at(-1).payload.text.includes("هم‌زمان تغییر کرد"), true);
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
    assert.equal(calls[0].payload.text.includes("فقط مالک اصلی"), true);
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

// Lightweight CI checks for the owner console HTML. The HTML itself must remain byte-for-byte unchanged.
{
  const htmlPath = path.join(root, "docs/console/index.html");
  const html = fs.readFileSync(htmlPath, "utf8");
  assert.equal((html.match(/<\/script>/g) || []).length, 1, "owner console HTML must contain exactly one </script> closing tag");

  const opening = html.match(/<script\s*>/i);
  assert.ok(opening, "owner console HTML must contain its inline script");
  const start = opening.index + opening[0].length;
  const end = html.indexOf("</script>", start);
  assert.ok(end >= start, "owner console inline script must have a closing tag");
  const inlineJs = html.slice(start, end);
  const syntax = spawnSync(process.execPath, ["--check", "--input-type=module"], {
    input: inlineJs,
    encoding: "utf8",
  });
  assert.equal(syntax.status, 0, "owner console inline JavaScript must pass node --check: " + (syntax.stderr || syntax.stdout || ""));

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

  response = await read(await worker.fetch(request("/api/console/jobs", {
    method: "POST", body: { type: "run_arbitrary_workflow", params: {} },
  }), state));
  assert.equal(response.status, 400);
  assert.equal(response.body.error, "invalid_job_type");
  assert.equal(state.DB.events.some(event => event.code === "console_job_create"), true);
  assert.equal(state.DB.consoleJobs.get(jobId).status, "queued");
}

console.log("Worker tests passed: live panel tracking, stage-two D1 screens, safe confirmation, bounded timings, and owner isolation.");

