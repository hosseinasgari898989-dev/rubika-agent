import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const dataModuleUrl = source => "data:text/javascript;base64," + Buffer.from(source).toString("base64");
const tiktokModule = await import(dataModuleUrl(fs.readFileSync(path.join(root, "src/console_tiktok.js"), "utf8")));
const failedMediaModule = await import(dataModuleUrl(fs.readFileSync(path.join(root, "src/console_failed_media.js"), "utf8")));
const { handleTikTokConsoleApi, normalizeTikTokAccount } = tiktokModule;
const { handleFailedMedia } = failedMediaModule;

const json = (body, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { "content-type": "application/json" },
});
const helpers = {
  json,
  readJson: async request => {
    try { return { ok: true, value: await request.json() }; }
    catch { return { ok: false, status: 400, error: "invalid_json" }; }
  },
};

class FakeStatement {
  constructor(db, sql) { this.db = db; this.sql = sql; this.values = []; }
  bind(...values) { this.values = values; return this; }
  async first() {
    this.db.reads += 1;
    if (this.sql.includes("SELECT id, enabled FROM sources")) {
      const handle = String(this.values[0]).toLowerCase();
      const row = [...this.db.sources.values()].find(x =>
        x.platform === "tiktok" && String(x.source_key).replace(/@/g, "").toLowerCase() === handle);
      return row ? { id: row.id, enabled: row.enabled } : null;
    }
    if (this.sql.includes("COUNT(*) AS n FROM failed_media WHERE source <> 'tiktok'")) {
      return { n: [...this.db.failedMedia.values()].filter(x => x.source !== "tiktok").length };
    }
    return null;
  }
  async all() {
    this.db.reads += 1;
    if (this.sql.includes("FROM sources WHERE platform IN")) {
      return { results: [...this.db.sources.values()].map(x => ({ ...x })) };
    }
    if (this.sql.includes("FROM automation_queue q")) {
      assert.match(this.sql, /ORDER BY COALESCE\(fm\.created_at, q\.updated_at, q\.created_at\) DESC, q\.id DESC/);
      return { results: this.db.unsentRows.map(x => ({ ...x })) };
    }
    if (this.sql.includes("FROM failed_media WHERE source <> 'tiktok'")) {
      return {
        results: [...this.db.failedMedia.values()]
          .filter(x => x.source !== "tiktok")
          .sort((a,b) => String(b.created_at).localeCompare(String(a.created_at)))
          .slice(0, Number(this.values[0] || 50)),
      };
    }
    return { results: [] };
  }
  async run() {
    this.db.writes += 1;
    const v = this.values;
    if (this.sql.startsWith("INSERT INTO sources")) {
      const disabled = this.sql.includes("VALUES (?, 'tiktok', ?, 0)");
      const row = {
        id: v[0], platform: "tiktok", source_key: v[1],
        display_name: disabled ? null : v[2],
        enabled: disabled ? 0 : 1,
        created_at: "2026-11-05T20:00:00.000Z", last_checked_at: null,
        last_seen_published_at: null,
      };
      this.db.sources.set(row.id, row);
      return { meta: { changes: 1 } };
    }
    if (this.sql.startsWith("UPDATE sources SET source_key")) {
      const row = this.db.sources.get(v[2]);
      if (!row || Number(row.enabled) !== 0) return { meta: { changes: 0 } };
      Object.assign(row, { source_key: v[0], display_name: v[1], enabled: 1,
        created_at: "2026-11-05T20:00:00.000Z", last_checked_at: null,
        last_seen_published_at: null });
      return { meta: { changes: 1 } };
    }
    if (this.sql.startsWith("UPDATE sources SET enabled = 0")) {
      const row = this.db.sources.get(v[0]);
      if (!row) return { meta: { changes: 0 } };
      row.enabled = 0;
      return { meta: { changes: 1 } };
    }
    if (this.sql.startsWith("UPDATE automation_queue SET status = 'queued'")) {
      const queue = this.db.queue.get(v[0]);
      if (!queue || queue.platform !== "tiktok" || !["failed","skipped"].includes(queue.status)) {
        return { meta: { changes: 0 } };
      }
      const item = [...this.db.items.values()].find(x =>
        x.platform === "tiktok" && x.external_id === queue.external_id);
      const attempts = item ? this.db.attempts.filter(x => x.item_id === item.id) : [];
      const blocked = !item || ["sent","sending","uncertain"].includes(item.status) ||
        item?.sent_at || item?.rubika_message_id ||
        attempts.some(x => ["sent","uncertain","started"].includes(x.status));
      if (blocked) return { meta: { changes: 0 } };
      queue.status = "queued";
      queue.last_error_code = null;
      return { meta: { changes: 1 } };
    }
    return { meta: { changes: 0 } };
  }
}
class FakeDB {
  constructor() {
    this.sources = new Map();
    this.failedMedia = new Map();
    this.items = new Map();
    this.queue = new Map();
    this.attempts = [];
    this.ledger = new Map();
    this.unsentRows = [];
    this.reads = 0;
    this.writes = 0;
  }
  prepare(sql) { return new FakeStatement(this, sql); }
}
const request = (path, method = "GET", body = undefined) => new Request(
  "https://worker.test/api/console" + path,
  { method, headers: body === undefined ? {} : { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body) },
);
const call = (db, path, method = "GET", body = undefined) => {
  const req = request(path, method, body);
  const sub = new URL(req.url).pathname.slice("/api/console".length).replace(/\/+$/, "");
  return handleTikTokConsoleApi(req, { DB: db }, helpers, sub, method);
};

assert.equal(normalizeTikTokAccount("@New_Creator.1"), "new_creator.1");
assert.equal(normalizeTikTokAccount("https://www.tiktok.com/@New_Creator.1/"), "new_creator.1");
assert.equal(normalizeTikTokAccount("https://youtube.com/@new_creator"), null);
assert.equal(normalizeTikTokAccount("https://www.tiktok.com/@new_creator/video/1234567890123456"), null);
assert.equal(normalizeTikTokAccount("new..creator"), null);

// Add accepts @handle and profile URLs, normalizes case, and rejects duplicates/bad hosts.
{
  const db = new FakeDB();
  let response = await call(db, "/sources", "POST", { platform: "tiktok", source_key: "@NewCreator" });
  assert.equal(response.status, 201);
  assert.equal((await response.json()).source_key, "newcreator");
  response = await call(db, "/sources", "POST", {
    platform: "tiktok", source_key: "https://www.tiktok.com/@newcreator/",
  });
  assert.equal(response.status, 409);
  response = await call(db, "/sources", "POST", {
    platform: "tiktok", source_key: "https://youtube.com/@another",
  });
  assert.equal(response.status, 400);
  assert.equal(db.sources.size, 1);
}

// The source list exposes legacy config profiles plus D1-managed accounts on the account page.
{
  const db = new FakeDB();
  db.sources.set("tiktok:customcreator", {
    id: "tiktok:customcreator", platform: "tiktok", source_key: "customcreator",
    enabled: 1, created_at: "2026-11-05T20:00:00Z",
  });
  const response = await call(db, "/sources?platform=tiktok");
  const rows = (await response.json()).items;
  assert.ok(rows.some(x => x.source_key === "meloaep_"), "legacy config account remains visible");
  assert.ok(rows.some(x => x.source_key === "customcreator"), "D1-managed account is visible");
}

// A legacy config account can be removed without an existing D1 row, then re-added with a fresh cutoff.
{
  const db = new FakeDB();
  let response = await call(db, "/sources/tiktok%3Ameloaep_", "DELETE");
  assert.equal(response.status, 200);
  assert.equal(db.sources.get("tiktok:meloaep_").enabled, 0);
  response = await call(db, "/sources", "POST", {
    platform: "tiktok", source_key: "https://www.tiktok.com/@meloaep_/",
  });
  assert.equal(response.status, 201);
  assert.equal(db.sources.get("tiktok:meloaep_").enabled, 1);
  assert.equal(db.sources.get("tiktok:meloaep_").created_at, "2026-11-05T20:00:00.000Z");
}

// Removing an account soft-disables the source only; historical rows and queue/ledger evidence stay.
{
  const db = new FakeDB();
  db.sources.set("tiktok:oldcreator", {
    id: "tiktok:oldcreator", platform: "tiktok", source_key: "oldcreator",
    enabled: 1, created_at: "2026-10-01T00:00:00Z",
  });
  db.items.set("tiktok:1234567890123456", { id: "tiktok:1234567890123456", status: "failed" });
  db.queue.set("tiktok:1234567890123456", { id: "tiktok:1234567890123456", status: "failed" });
  db.attempts.push({ item_id: "tiktok:1234567890123456", status: "failed" });
  db.ledger.set("tiktok:1234567890123456", { status: "failed", history: ["attempt-1"] });
  const before = {
    items: JSON.stringify([...db.items]), queue: JSON.stringify([...db.queue]),
    attempts: JSON.stringify(db.attempts), ledger: JSON.stringify([...db.ledger]),
  };
  const response = await call(db, "/sources/tiktok%3Aoldcreator", "DELETE");
  assert.equal(response.status, 200);
  assert.equal(db.sources.get("tiktok:oldcreator").enabled, 0);
  assert.deepEqual({
    items: JSON.stringify([...db.items]), queue: JSON.stringify([...db.queue]),
    attempts: JSON.stringify(db.attempts), ledger: JSON.stringify([...db.ledger]),
  }, before);
  const list = await call(db, "/sources");
  const data = await list.json();
  assert.equal(data.items.find(x => x.source_key === "oldcreator").enabled, 0);
}

// The dedicated TikTok section makes exactly one D1 read for all rows and delivery guards.
{
  const db = new FakeDB();
  db.unsentRows = [
    { id: "tiktok:7616247520657771796", account: "newcreator", created_at: "2026-11-05T20:00:00Z", can_resend: 1, already_sent: 0 },
    { id: "tiktok:7616247520657771797", account: "oldcreator", created_at: "2026-11-04T20:00:00Z", can_resend: 0, already_sent: 1 },
  ];
  const response = await call(db, "/tiktok-unsent");
  assert.equal(response.status, 200);
  const data = await response.json();
  assert.equal(data.items.length, 2);
  assert.equal(data.items[0].account, "newcreator");
  assert.equal(db.reads, 1);
}

// The generic list query excludes TikTok at SQL level; no row is changed.
{
  const db = new FakeDB();
  db.failedMedia.set("fm_mob", { id: "fm_mob", source: "mob_x", created_at: "2026-11-05T20:00:00Z" });
  db.failedMedia.set("fm_tiktok", { id: "fm_tiktok", source: "tiktok", created_at: "2026-11-05T21:00:00Z" });
  const response = await handleFailedMedia(request("/failed-media/list", "POST", { limit: 50 }), { DB: db }, helpers, "/failed-media/list", "POST");
  const data = await response.json();
  assert.deepEqual(data.items.map(x => x.source), ["mob_x"]);
  assert.equal(db.failedMedia.size, 2);
}

// A resend click performs one conditional write, and a second click cannot queue it twice.
{
  const db = new FakeDB(), id = "tiktok:7616247520657771796", externalId = "7616247520657771796";
  db.items.set("tiktok:" + externalId, {
    id: "tiktok:" + externalId, external_id: externalId, status: "failed",
    sent_at: null, rubika_message_id: null,
  });
  db.queue.set("tiktok:" + externalId, {
    id: "tiktok:" + externalId, platform: "tiktok", external_id: externalId,
    status: "failed", last_error_code: "rubika_rejected_upload_failed",
  });
  db.attempts.push({ item_id: "tiktok:" + externalId, status: "failed" });
  let response = await call(db, "/tiktok-unsent/" + id + "/resend", "POST", {});
  assert.equal(response.status, 200);
  assert.equal(db.queue.get("tiktok:" + externalId).status, "queued");
  assert.equal(db.writes, 1);
  response = await call(db, "/tiktok-unsent/" + id + "/resend", "POST", {});
  assert.equal(response.status, 409);
  assert.equal(db.writes, 2);
  assert.equal(db.queue.get("tiktok:" + externalId).status, "queued");
}

// A sent receipt or a started/uncertain attempt blocks resend even if a failed-media row remains.
{
  const db = new FakeDB(), id = "tiktok:7616247520657771797", externalId = "7616247520657771797";
  db.items.set("tiktok:" + externalId, {
    id: "tiktok:" + externalId, external_id: externalId, status: "sent",
    sent_at: "2026-11-05T20:00:00Z", rubika_message_id: "message-1",
  });
  db.queue.set("tiktok:" + externalId, {
    id: "tiktok:" + externalId, platform: "tiktok", external_id: externalId, status: "failed",
  });
  db.attempts.push({ item_id: "tiktok:" + externalId, status: "sent" });
  const response = await call(db, "/tiktok-unsent/" + id + "/resend", "POST", {});
  assert.equal(response.status, 409);
  assert.equal(db.queue.get("tiktok:" + externalId).status, "failed");
}

// Cross-request double-click race: the conditional UPDATE allows exactly one winner.
{
  const db = new FakeDB(), id = "tiktok:7616247520657771798", externalId = "7616247520657771798";
  db.items.set("tiktok:" + externalId, {
    id: "tiktok:" + externalId, external_id: externalId, status: "failed",
    sent_at: null, rubika_message_id: null,
  });
  db.queue.set("tiktok:" + externalId, {
    id: "tiktok:" + externalId, platform: "tiktok", external_id: externalId, status: "failed",
  });
  const results = await Promise.all([
    call(db, "/tiktok-unsent/" + id + "/resend", "POST", {}),
    call(db, "/tiktok-unsent/" + id + "/resend", "POST", {}),
  ]);
  assert.deepEqual(results.map(x => x.status).sort(), [200, 409]);
  assert.equal(db.queue.get("tiktok:" + externalId).status, "queued");
}
console.log("TikTok console API tests passed");
