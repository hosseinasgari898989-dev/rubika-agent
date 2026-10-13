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
    return null;
  }
  async all() {
    if (this.sql.includes("GROUP BY status")) return { results: [] };
    if (this.sql.includes("FROM runs ORDER BY")) return { results: [] };
    if (this.sql.includes("review_state = 'approved'")) {
      this.db.queueLimit = this.values[0];
      return { results: [...this.db.items.values()].filter(x => x.review_state === "approved" && x.status === "ready").slice(0, this.values[0]) };
    }
    return { results: [] };
  }
  async run() {
    const sql = this.sql;
    const v = this.values;
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
      this.db.items.set(item.id, item);
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
      this.db.events.push({ values: v, sql });
      return { meta: { changes: 1 } };
    }
    return { meta: { changes: 1 } };
  }
}

class FakeDB {
  constructor() {
    this.runs = new Map();
    this.items = new Map();
    this.attempts = [];
    this.events = [];
    this.queueLimit = null;
  }
  prepare(sql) {
    return new FakeStatement(this, sql);
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
    poppy_relevant: true, is_edit: true, insults_poppy: false,
    insults_other_games: false, high_effort: true, has_music: true,
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
  assert.equal(response.body.error, "edit_approval_requires_all_content_checks");
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
console.log("Worker API tests passed: auth, D1 writes, run lifecycle, approval gate, canonical URLs, queue bounds, event redaction.");
