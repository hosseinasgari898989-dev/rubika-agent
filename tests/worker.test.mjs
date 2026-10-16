import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const source = fs.readFileSync(path.join(root, "src/index.js"), "utf8");
const moduleUrl = "data:text/javascript;base64," + Buffer.from(source).toString("base64");
const imported = await import(moduleUrl);
const worker = imported.default;

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
    if (this.sql.startsWith("INSERT INTO gemini_translation_usage_daily")) {
      if (this.db.failGeminiTranslationReservation) throw new Error("synthetic D1 failure");
      const [day, maximum] = this.values;
      const current = this.db.geminiTranslationUsageDaily.get(day) || 0;
      if (current >= maximum) return null;
      this.db.geminiTranslationUsageDaily.set(day, current + 1);
      return { request_count: current + 1 };
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
    this.geminiTranslationUsageDaily = new Map();
    this.failGeminiTranslationReservation = false;
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
  const mock = mockGeminiFetch("به عمق بازی بروید, еще اسرارآمیزتر.");
  const state = env();
  state.GEMINI_API_KEY = "synthetic-test-key";
  state.AI = { run: async () => { throw new Error("Workers AI must not be used for translation"); } };
  try {
    const response = await read(await worker.fetch(request("/api/brain/translate-official", {
      method: "POST",
      body: { text: "Explore the depths of the game." },
    }), state));
    assert.equal(response.status, 502);
    assert.equal(response.body.error, "translation_model_output_invalid");
    assert.equal(state.DB.aiUsageDaily.size, 0);
    assert.equal(mock.getCaptured().url, "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent");
  } finally {
    mock.restore();
  }
}

console.log("Worker API tests passed: auth, D1 writes, run lifecycle, approval gate, canonical URLs, queue bounds, event redaction, Clef Omni music moderation and uncapped daily usage accounting.");

function mockGeminiFetch(responseText = "ماب انترتینمنت خبر رسمی تازه‌ای منتشر کرد.", status = 200) {
  const originalFetch = globalThis.fetch;
  let captured = null;
  let callCount = 0;
  globalThis.fetch = async (url, options = {}) => {
    callCount += 1;
    captured = { url: String(url), options };
    return new Response(JSON.stringify({
      candidates: [{ content: { parts: [{ text: responseText }] } }],
    }), {
      status,
      headers: { "content-type": "application/json; charset=utf-8" },
    });
  };
  return {
    getCaptured: () => captured,
    getCallCount: () => callCount,
    restore: () => { globalThis.fetch = originalFetch; },
  };
}

{
  const mock = mockGeminiFetch();
  const state = env();
  state.GEMINI_API_KEY = "synthetic-test-key";
  try {
    const response = await read(await worker.fetch(request("/api/brain/translate-official", {
      method: "POST", body: { text: "Mob published an update." },
    }), state));
    assert.equal(response.status, 503);
    assert.equal(response.body.error, "official_translation_disabled");
    assert.equal(mock.getCallCount(), 0);
    assert.equal(state.DB.geminiTranslationUsageDaily.size, 0);
  } finally {
    mock.restore();
  }
}
{
  const mock = mockGeminiFetch();
  const state = env();
  state.GEMINI_TRANSLATION_APPROVED = "true";
  state.GEMINI_API_KEY = "synthetic-test-key";
  state.AI = { run: async () => { throw new Error("Workers AI must not be used for translation"); } };
  try {
    const response = await read(await worker.fetch(request("/api/brain/translate-official", {
      method: "POST",
      body: { text: "Mob Entertainment published an official update at https://example.com/news." },
    }), state));
    assert.equal(response.status, 200);
    assert.equal(response.body.ok, true);
    assert.match(response.body.translation, /ماب انترتینمنت/);
    assert.equal(response.body.publishing, false);
    assert.equal(response.body.model, "gemini-3.8-flash");
    assert.equal(state.DB.aiUsageDaily.size, 0);
    assert.equal(state.DB.geminiTranslationUsageDaily.size, 1);
    const call = mock.getCaptured();
    assert.equal(call.url, "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent");
    assert.equal(call.options.headers["x-goog-api-key"], "synthetic-test-key");
    const payload = JSON.parse(call.options.body);
    assert.ok(!payload.contents[0].parts[0].text.includes("example.com"));
  } finally {
    mock.restore();
  }
}
{
  const mock = mockGeminiFetch("This is still English, not a Persian translation.");
  const state = env();
  state.GEMINI_TRANSLATION_APPROVED = "true";
  state.GEMINI_API_KEY = "synthetic-test-key";
  try {
    const response = await read(await worker.fetch(request("/api/brain/translate-official", {
      method: "POST", body: { text: "This is a valid source sentence." },
    }), state));
    assert.equal(response.status, 502);
    assert.equal(response.body.error, "translation_model_output_invalid");
    assert.equal(state.DB.aiUsageDaily.size, 0);
    assert.equal(state.DB.geminiTranslationUsageDaily.size, 1);
  } finally {
    mock.restore();
  }
}
{
  const mock = mockGeminiFetch();
  const state = env();
  state.GEMINI_TRANSLATION_APPROVED = "true";
  state.GEMINI_API_KEY = "synthetic-test-key";
  try {
    const response = await read(await worker.fetch(request("/api/brain/translate-official", {
      method: "POST", body: { text: "x".repeat(4501) },
    }), state));
    assert.equal(response.status, 413);
    assert.equal(response.body.error, "translation_text_too_long");
    assert.equal(mock.getCaptured(), null);
    assert.equal(state.DB.aiUsageDaily.size, 0);
    assert.equal(state.DB.geminiTranslationUsageDaily.size, 0);
  } finally {
    mock.restore();
  }
}
{
  const mock = mockGeminiFetch("ماب انترتینمنت خبر تازه‌ای منتشر کرد.", 429);
  const state = env();
  state.GEMINI_TRANSLATION_APPROVED = "true";
  state.GEMINI_API_KEY = "synthetic-test-key";
  try {
    const response = await read(await worker.fetch(request("/api/brain/translate-official", {
      method: "POST", body: { text: "Mob published an update." },
    }), state));
    assert.equal(response.status, 503);
    assert.equal(response.body.error, "official_translation_quota_exhausted");
    assert.equal(state.DB.aiUsageDaily.size, 0);
    assert.equal(state.DB.geminiTranslationUsageDaily.size, 1);
  } finally {
    mock.restore();
  }
}
{
  const mock = mockGeminiFetch();
  const state = env();
  state.GEMINI_TRANSLATION_APPROVED = "true";
  try {
    const response = await read(await worker.fetch(request("/api/brain/translate-official", {
      method: "POST", body: { text: "Mob published an update." },
    }), state));
    assert.equal(response.status, 503);
    assert.equal(response.body.error, "gemini_api_key_missing");
    assert.equal(mock.getCaptured(), null);
    assert.equal(state.DB.aiUsageDaily.size, 0);
    assert.equal(state.DB.geminiTranslationUsageDaily.size, 0);
  } finally {
    mock.restore();
  }
}
{
  const mock = mockGeminiFetch();
  const state = env();
  state.GEMINI_TRANSLATION_APPROVED = "true";
  state.GEMINI_API_KEY = "synthetic-test-key";
  try {
    for (let index = 0; index < 10; index++) {
      const response = await read(await worker.fetch(request("/api/brain/translate-official", {
        method: "POST", body: { text: "Mob published an update." },
      }), state));
      assert.equal(response.status, 200);
    }
    assert.equal(mock.getCallCount(), 10);
    assert.equal(state.DB.geminiTranslationUsageDaily.values().next().value, 10);
    const capped = await read(await worker.fetch(request("/api/brain/translate-official", {
      method: "POST", body: { text: "Another Mob update." },
    }), state));
    assert.equal(capped.status, 429);
    assert.equal(capped.body.error, "official_translation_daily_limit_reached");
    assert.equal(mock.getCallCount(), 10);
  } finally {
    mock.restore();
  }
}
{
  const mock = mockGeminiFetch();
  const state = env();
  state.GEMINI_TRANSLATION_APPROVED = "true";
  state.GEMINI_API_KEY = "synthetic-test-key";
  state.DB.failGeminiTranslationReservation = true;
  try {
    const response = await read(await worker.fetch(request("/api/brain/translate-official", {
      method: "POST", body: { text: "Mob published an update." },
    }), state));
    assert.equal(response.status, 503);
    assert.equal(response.body.error, "official_translation_quota_guard_unavailable");
    assert.equal(mock.getCallCount(), 0);
  } finally {
    mock.restore();
  }
}
{
  const state = env();
  const response = await read(await worker.fetch(request("/api/brain/translate-official", {
    method: "GET",
  }), state));
  assert.equal(response.status, 405);
  assert.equal(response.body.error, "method_not_allowed");
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

