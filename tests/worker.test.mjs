import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const screensSource = fs.readFileSync(path.join(root, "src/panel_screens.js"), "utf8");
const screensModuleUrl = "data:text/javascript;base64," + Buffer.from(screensSource).toString("base64");
const panelSource = fs.readFileSync(path.join(root, "src/owner_panel.js"), "utf8")
  .replace('from "./panel_screens.js";', 'from "' + screensModuleUrl + '";');
const panelModuleUrl = "data:text/javascript;base64," + Buffer.from(panelSource).toString("base64");
const importedPanel = await import(panelModuleUrl);
const screens = await import(screensModuleUrl);
const originalSource = fs.readFileSync(path.join(root, "src/index.js"), "utf8");
const workerImport = 'import { handleOwnerPanel } from "./owner_panel.js";';
assert.ok(originalSource.includes(workerImport), "Worker must import owner panel handler");
const source = originalSource.replace(workerImport, `const { handleOwnerPanel } = await import("${panelModuleUrl}");`);
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
    if (this.sql.includes("FROM automation_queue WHERE id = ?")) {
      return this.db.automationQueue.get(this.values[0]) || null;
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
    return { results: [] };
  }
  async run() {
    const sql = this.sql;
    const v = this.values;
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
  }
  prepare(sql) {
    return new FakeStatement(this, sql);
  }
  async batch(statements) {
    return Promise.all(statements.map(statement => statement.run()));
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
  state.DB.automationQueue.set("youtube:item-2", {
    id: "youtube:item-2", status: "processing", sent_at: null,
  });
  state.DB.automationQueue.set("instagram:item-3", {
    id: "instagram:item-3", status: "sent", sent_at: new Date().toISOString(),
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
  state.DB.items.set("youtube:vkVFgmgLo1s", {
    id: "youtube:vkVFgmgLo1s",
    platform: "youtube",
    external_id: "vkVFgmgLo1s",
    media_sha256: "c".repeat(64),
    status: "sent",
    review_state: "approved",
  });
  const response = await read(await worker.fetch(request(
    "/api/brain/items/seen?limit=999&offset=-7&platform=youtube"
  ), state));
  assert.equal(response.status, 200);
  assert.equal(response.body.items.length, 1);
  assert.equal(response.body.items[0].external_id, "vkVFgmgLo1s");
  assert.deepEqual(state.DB.seenQuery.values, ["youtube", 500, 0]);
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
  assert.equal(response.body.error, "delivery_status_requires_attempt_api");
}
{
  const state = env();
  const badApproval = {
    platform: "youtube", external_id: "vkVFgmgLo1s",
    canonical_url: "https://www.youtube.com/watch?v=vkVFgmgLo1s",
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
    body: { audio: sampleAudioDataUrl(), platform: "youtube", title: "Poppy Playtime edit", caption: "#edit" },
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
    body: { audio: "data:text/html;base64," + Buffer.from("x".repeat(1400)).toString("base64"), platform: "youtube" },
  }), state));
  assert.equal(response.status, 400);
  assert.equal(response.body.error, "moderation_audio_format_invalid");
  assert.equal(state.DB.aiUsageDaily.size, 0);
}
{
  const state = env();
  state.AI = { run: async () => { throw new Error("secret provider body"); } };
  const response = await read(await worker.fetch(request("/api/brain/moderate-music", {
    method: "POST", body: { audio: sampleAudioDataUrl(), platform: "youtube" },
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
      method: "POST", body: { audio: sampleAudioDataUrl(), platform: "youtube" },
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
    if (this.sql.includes("SELECT chat_id FROM panel_owner WHERE id=1")) {
      return this.db.ownerChat ? { chat_id: this.db.ownerChat } : null;
    }
    if (this.sql.includes("SELECT value FROM panel_state WHERE key=?")) {
      const value = this.db.state.get(this.values[0]);
      return value === undefined ? null : { value };
    }
    if (this.sql.includes("SELECT COUNT(*) AS c FROM panel_links")) {
      return { c: this.db.links.length };
    }
    return null;
  }
  async all() {
    if (this.sql.includes("SELECT url, status, created_at FROM panel_links")) {
      return { results: this.db.links.slice().reverse().slice(0, 8).map(row => ({ ...row })) };
    }
    return { results: [] };
  }
  async run() {
    if (this.sql.startsWith("DELETE FROM panel_links")) {
      const count = this.db.links.length;
      this.db.links = [];
      this.db.linkDeleteCalls += 1;
      return { meta: { changes: count } };
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
    return { meta: { changes: 1 } };
  }
}
class PanelFakeDB {
  constructor() {
    this.ownerChat = null;
    this.state = new Map();
    this.events = new Set();
    this.links = [];
    this.linkDeleteCalls = 0;
  }
  prepare(sql) { return new PanelFakeStatement(this, sql); }
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

// Link parsing canonicalises X/Twitter post URLs and rejects unrelated text.
{
  assert.equal(panel.parseXLink("New post https://twitter.com/Test_User/status/2107878755079213476?s=20"),
    "https://x.com/Test_User/status/2107878755079213476");
  assert.equal(panel.parseXLink("https://example.com/not-a-post"), null);
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

// A click with no existing panel creates one tracked message; every report stage edits that same message.
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
    assert.equal(state.DB.state.get("panel_edit_ok"), "4");
    assert.equal(state.DB.state.get("panel_edit_fail") || "0", "0");
    assert.equal(calls.some(call => call.payload.message_id === "clicked-event-original-9876"), false);
    assert.equal(calls.filter(call => call.method === "editMessageText").length >= 4, true);
    assert.equal(calls.every(call => !("message_id" in call.payload) || call.payload.message_id === "mock-reply-1"), true);
    for (const call of calls.filter(call => call.method === "editMessageKeypad")) {
      assert.equal(Array.isArray(call.payload.inline_keypad.rows), true);
      assert.equal(call.payload.inline_keypad.rows.some(row => row.buttons && row.buttons.length > 0), true);
    }
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
        message_id: "status-click-4321", chat_id: "b0actualowner", text: "", aux_data: { button_id: "p_status" },
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
        message_id: "status-click-3322", chat_id: "b0actualowner", text: "", aux_data: { button_id: "p_status" },
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


// Stale button clicks must edit the saved panel message, never the message that carried the click.
{
  await withMockRubikaFetch(async calls => {
    const state = panelEnv();
    state.DB.ownerChat = "b0actualowner";
    state.DB.state.set("panel_mid", "latest-live-panel-9876");
    const secret = await panel.hookSecret(state);
    await postPanelUpdate(state, {
      update: { type: "NewMessage", chat_id: "b0actualowner", new_message: {
        message_id: "stale-click-message-1234", chat_id: "b0actualowner",
        text: "", aux_data: { button_id: "p_home" },
      } },
    }, secret);
    const editCalls = calls.filter(call => call.method === "editMessageText" || call.method === "editMessageKeypad");
    assert.equal(calls.filter(call => call.method === "sendMessage").length, 0);
    assert.equal(editCalls.length, 2);
    assert.equal(editCalls.every(call => call.payload.message_id === "latest-live-panel-9876"), true);
    assert.equal(editCalls.some(call => call.payload.message_id === "stale-click-message-1234"), false);
    assert.equal(state.DB.state.get("panel_mid"), "latest-live-panel-9876");
    assert.equal(state.DB.state.get("panel_edit_ok"), "1");
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
        message_id: "status-click-single-1", chat_id: "b0actualowner",
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
    if (this.sql.includes("SELECT url, status, created_at FROM panel_links")) {
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
    ["p_links_clear", "همهٔ 0 لینک"],
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
    aiCalls: [{ provider: "groq", role: "code_repair", status: "success", error_code: null, started_at: "2026-10-24T04:00:00Z" }],
    links: [{ url: "https://x.com/Test/status/2107878755079213476", status: "queued", created_at: "2026-10-24T05:00:00Z" }],
  };
  const seeded = screenEnv(seed);
  for (const [id, phrase] of [
    ["p_digest", "Digest title"], ["p_errors", "Digest title"], ["p_runs", "success"],
    ["p_qs", "در انتظار: 1"], ["p_ailog", "groq/code_repair"], ["p_links", "@Test"],
    ["p_links_clear", "همهٔ 1 لینک"],
  ]) {
    const result = await screens.buildScreen(seeded, id);
    assert.equal(result.text.includes(phrase), true, id + " should show seeded data");
    assert.equal(result.text.includes("d1_error"), false, id + " should not fail with seeded data");
  }
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
  const ids = ["p_digest", "p_errors", "p_runs", "p_qs", "p_ailog", "p_links", "p_links_clear", "p_links_clear_yes"]
    .concat(screenStatuses.map(status => "p_ql_" + status + "_0"));
  for (const id of ids) {
    const result = await screens.buildScreen(screenEnv({ failReads: true }), id);
    assert.equal(result.text.includes("d1_error"), true, id + " should turn D1 failures into d1_error");
  }
  const statusText = panel.fmtStatus({ ok: true, agent: { enabled: 1 }, capabilities: { publishing: false }, item_counts: [], recent_runs: [] });
  assert.equal(statusText.includes("پایگاه داده"), false);
}

// New textual commands render the imported screens directly on the single tracked panel (without loading frames).
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
    for (let i = 0; i < commands.length; i += 1) {
      await postPanelUpdate(state, {
        update: { type: "NewMessage", chat_id: "b0actualowner", new_message: {
          message_id: "stage-two-command-" + i, chat_id: "b0actualowner", text: commands[i][0],
        } },
      }, secret);
      const editCalls = calls.filter(call => call.method === "editMessageText");
      assert.equal(editCalls.length, i + 1, commands[i][0] + " should edit exactly one page");
      assert.equal(editCalls.at(-1).payload.message_id, "one-live-screen-panel");
      assert.equal(editCalls.at(-1).payload.text.includes(commands[i][1]), true, commands[i][0] + " should render its screen");
      assert.equal(calls.some(call => call.method === "sendMessage" && String(call.payload.text).includes("در حال دریافت")), false);
      assert.equal(state.DB.state.get("mode"), "idle");
    }
    assert.equal(calls.filter(call => call.method === "sendMessage").length, 0);
  });
}

// Link deletion requires the explicit confirmation screen; a direct confirmation ID is harmless.
{
  await withMockRubikaFetch(async calls => {
    const state = panelEnv();
    state.DB.ownerChat = "b0actualowner";
    state.DB.links = [{ url: "https://x.com/Test/status/2107878755079213476", status: "queued", created_at: "2026-10-24T05:00:00Z" }];
    state.DB.state.set("panel_mid", "link-confirmation-panel");
    const secret = await panel.hookSecret(state);
    const click = async (button, messageId) => postPanelUpdate(state, {
      update: { type: "NewMessage", chat_id: "b0actualowner", new_message: {
        message_id: messageId, chat_id: "b0actualowner", text: "", aux_data: { button_id: button },
      } },
    }, secret);
    await click("p_links_clear_yes", "direct-yes-1");
    assert.equal(state.DB.links.length, 1, "direct confirmation ID must not delete links");
    assert.equal(state.DB.linkDeleteCalls, 0);
    await click("p_links_clear", "open-confirm-2");
    assert.equal(state.DB.links.length, 1, "opening confirmation must not delete links");
    assert.equal(calls.filter(call => call.method === "editMessageText").at(-1).payload.text.includes("پاک شود؟"), true);
    await click("p_links_clear_yes", "confirmed-yes-3");
    assert.equal(state.DB.links.length, 0, "confirmed action should clear links");
    assert.equal(state.DB.linkDeleteCalls, 1);
    assert.equal(calls.filter(call => call.method === "editMessageText").at(-1).payload.text.includes("پاک شدند"), true);
  });
}

console.log("Worker tests passed: live panel tracking, stage-two D1 screens, safe confirmation, bounded timings, and owner isolation.");

