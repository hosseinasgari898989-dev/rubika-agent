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
    if (this.sql.startsWith("INSERT INTO brain_events") && this.sql.includes("ai_supervisor_reserved")) {
      const [detailsJson, start, end, limit] = this.values;
      const reserved = this.db.events.filter(event => {
        const code = event.code || (event.values && event.sql && event.sql.includes("ai_supervisor_reserved") ? "ai_supervisor_reserved" : "");
        const createdAt = event.created_at || new Date().toISOString();
        return code === "ai_supervisor_reserved" && createdAt >= start && createdAt < end;
      }).length;
      if (reserved >= limit) return null;
      const id = this.db.events.length + 1;
      this.db.events.push({
        id, code: "ai_supervisor_reserved", created_at: new Date().toISOString(),
        details_json: detailsJson, sql: this.sql, values: this.values,
      });
      return { id };
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
    if (sql.startsWith("UPDATE automation_queue SET status = 'uncertain'")) {
      return { meta: { changes: 0 } };
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
  state.AI = makeTranslationAI("به عمق بازی بروید, еще اسرارآمیزتر.");
  const response = await read(await worker.fetch(request("/api/brain/translate-official", {
    method: "POST",
    body: { text: "Explore the depths of the game." },
  }), state));
  assert.equal(response.status, 502);
  assert.equal(response.body.error, "translation_model_output_invalid");
}

console.log("Worker API tests passed: auth, D1 writes, run lifecycle, approval gate, canonical URLs, queue bounds, event redaction, Clef Omni music moderation and uncapped daily usage accounting.");

function makeTranslationAI(response = "ماب انترتینمنت خبر رسمی تازه‌ای منتشر کرد.") {
  return {
    run: async (model, input) => {
      assert.equal(model, "@cf/meta/llama-3.3-70b-instruct-fp8-fast");
      assert.equal(input.temperature, 0.1);
      assert.equal(input.max_tokens, 2048);
      assert.ok(Array.isArray(input.messages));
      assert.ok(!input.messages[1].content.includes("example.com"));
      return { response };
    },
  };
}
{
  const state = env();
  state.AI = makeTranslationAI();
  const response = await read(await worker.fetch(request("/api/brain/translate-official", {
    method: "POST",
    body: { text: "Mob Entertainment published an official update at https://example.com/news." },
  }), state));
  assert.equal(response.status, 200);
  assert.equal(response.body.ok, true);
  assert.match(response.body.translation, /ماب انترتینمنت/);
  assert.equal(response.body.publishing, false);
  assert.equal(state.DB.aiUsageDaily.size, 1);
}
{
  const state = env();
  state.AI = makeTranslationAI();
  const response = await read(await worker.fetch(request("/api/brain/translate-official", {
    method: "POST", body: { text: "x".repeat(4501) },
  }), state));
  assert.equal(response.status, 413);
  assert.equal(response.body.error, "translation_text_too_long");
  assert.equal(state.DB.aiUsageDaily.size, 0);
}
{
  const state = env();
  state.AI = makeTranslationAI("This is still English, not a Persian translation.");
  const response = await read(await worker.fetch(request("/api/brain/translate-official", {
    method: "POST", body: { text: "This is a valid source sentence." },
  }), state));
  assert.equal(response.status, 502);
  assert.equal(response.body.error, "translation_model_output_invalid");
}
{
  const state = env();
  state.AI = makeTranslationAI();
  const response = await read(await worker.fetch(request("/api/brain/translate-official", {
    method: "GET",
  }), state));
  assert.equal(response.status, 405);
  assert.equal(response.body.error, "method_not_allowed");
}



// The diagnostic helper is advisory only: it never gets tools, credentials, URLs,
// media bytes, or authority to publish/delete/change configuration.
function makeSupervisorAI(response) {
  return {
    run: async (model, input) => {
      assert.equal(model, "@cf/meta/llama-3.1-8b-instruct-fast");
      assert.equal(input.temperature, 0);
      assert.equal(input.max_tokens, 220);
      assert.equal(Array.isArray(input.messages), true);
      const prompt = input.messages.map(message => message.content).join("\\n");
      assert.equal(prompt.includes("LEAKED_TEST_SECRET"), false);
      assert.equal(prompt.includes("https://"), false);
      assert.equal(prompt.includes("raw_log"), false);
      return { response };
    },
  };
}
function supervisorRequest(body, options = {}) {
  return request("/api/brain/supervise-error", { method: "POST", body, ...options });
}
function supervisorInput(extra = {}) {
  return {
    event_code: "queue_intake_failed",
    stage: "queue_intake",
    error_code: "control_api_unreachable",
    platform: "tiktok",
    attempt_count: 1,
    counts: { queued: 12, processing: 0, sent: 3, failed: 1, uncertain: 0, skipped: 2, sent_today: 1 },
    ...extra,
  };
}
{
  const state = env();
  state.AI = makeSupervisorAI(JSON.stringify({
    action: "retry_next_scheduled_run",
    reason_code: "temporary_network",
    next_step_code: "wait_for_next_schedule",
    human_review: false,
  }));
  const response = await read(await worker.fetch(supervisorRequest(supervisorInput()), state));
  assert.equal(response.status, 200);
  assert.equal(response.body.ok, true);
  assert.equal(response.body.source, "workers_ai");
  assert.equal(response.body.recommendation.action, "retry_next_scheduled_run");
  assert.equal(response.body.recommendation.next_step_code, "wait_for_next_schedule");
  assert.equal(response.body.applied, false);
  assert.equal(JSON.stringify(response.body).includes("LEAKED_TEST_SECRET"), false);
  assert.equal(state.DB.events.filter(event => event.code === "ai_supervisor_reserved").length, 1);
  assert.equal(state.DB.events.some(event => event.code === "ai_supervisor_decision"), true);
}
{
  const state = env();
  state.AI = { run: async () => { throw new Error("LEAKED_TEST_SECRET"); } };
  const today = new Date().toISOString().slice(0, 10);
  for (let i = 0; i < 4; i++) {
    state.DB.events.push({
      code: "ai_supervisor_reserved",
      created_at: today + "T12:00:00.000Z",
    });
  }
  const response = await read(await worker.fetch(supervisorRequest(supervisorInput()), state));
  assert.equal(response.status, 200);
  assert.equal(response.body.source, "deterministic_fallback");
  assert.equal(response.body.recommendation.action, "retry_next_scheduled_run");
  assert.equal(response.body.recommendation.next_step_code, "wait_for_next_schedule");
  assert.equal(state.DB.events.filter(event => event.code === "ai_supervisor_reserved").length, 4);
  assert.equal(JSON.stringify(response.body).includes("LEAKED_TEST_SECRET"), false);
}
{
  const state = env();
  state.AI = makeSupervisorAI("Here is the JSON result: ```json " + JSON.stringify({
    action: "skip_source_and_continue",
    reason_code: "source_blocked",
    next_step_code: "continue_with_other_sources",
    human_review: false,
  }) + " ```");
  const response = await read(await worker.fetch(supervisorRequest(supervisorInput()), state));
  assert.equal(response.status, 200);
  assert.equal(response.body.source, "workers_ai");
  assert.equal(response.body.recommendation.action, "skip_source_and_continue");
  assert.equal(response.body.recommendation.next_step_code, "continue_with_other_sources");
  assert.equal(response.body.applied, false);
}
{
  const state = env();
  state.AI = makeSupervisorAI(JSON.stringify({
    action: "publish_now",
    shell_command: "mega-rm --force",
    reason_code: "unknown",
    next_step_code: "delete_everything",
    human_review: false,
  }));
  const response = await read(await worker.fetch(supervisorRequest(supervisorInput()), state));
  assert.equal(response.status, 200);
  assert.equal(response.body.source, "deterministic_fallback");
  assert.equal(response.body.recommendation.action, "retry_next_scheduled_run");
  assert.equal(JSON.stringify(response.body).includes("shell_command"), false);
  assert.equal(JSON.stringify(response.body).includes("delete_everything"), false);
}
{
  const state = env();
  state.AI = { run: async () => { throw new Error("mock-provider-internal-message"); } };
  const response = await read(await worker.fetch(supervisorRequest(supervisorInput()), state));
  assert.equal(response.status, 200);
  assert.equal(response.body.source, "deterministic_fallback");
  assert.equal(response.body.recommendation.reason_code, "unknown_error");
  assert.equal(response.body.applied, false);
  assert.equal(JSON.stringify(response.body).includes("mock-provider-internal-message"), false);
  const decision = state.DB.events.find(event => event.code === "ai_supervisor_decision");
  assert.equal(JSON.parse(decision.details_json).reason_code, "unknown_error");
}
{
  const state = env();
  state.AI = makeSupervisorAI("{}");
  const response = await read(await worker.fetch(supervisorRequest(supervisorInput({
    event_code: "arbitrary",
    token: "LEAKED_TEST_SECRET",
  })), state));
  assert.equal(response.status, 400);
  assert.equal(state.DB.events.length, 0);
}
{
  const response = await read(await worker.fetch(supervisorRequest(supervisorInput(), { token: null }), env()));
  assert.equal(response.status, 401);
}
