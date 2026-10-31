import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

// Loaded from a data: URL like the other worker tests (no package.json "type" dependency).
const src = readFileSync(new URL("../src/console_failed_media.js", import.meta.url), "utf8");
const { handleFailedMedia } = await import("data:text/javascript;base64," + Buffer.from(src).toString("base64"));

const json = (obj, status = 200) => new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json" } });
const readJson = async (req) => {
  try { return { ok: true, value: await req.json() }; } catch { return { ok: false, error: "invalid_json", status: 400 }; }
};
const h = { json, readJson };
const req = (body, method = "POST") => new Request("https://x.test/api/console/failed-media/record", { method, body: body === undefined ? undefined : JSON.stringify(body) });

function fakeEnv({ fail = false } = {}) {
  const calls = [];
  return {
    calls,
    DB: {
      prepare(sql) {
        return {
          bind(...args) {
            return {
              async run() { if (fail && /failed_media/.test(sql)) throw new Error("db"); calls.push({ sql, args }); return { meta: { changes: 1 } }; },
              async first() { calls.push({ sql, args }); return { id: "fm_existing", share_url: "https://mega.nz/file/A#B" }; },
            };
          },
        };
      },
    },
  };
}

const good = {
  source: "mob_x", media_type: "video", storage_key: "/Rubika-Agent/failed/mob_x_job_abcdef12.mp4",
  share_url: "https://mega.nz/file/A#B", caption: "cap", reason: "mob_x_preview_upload_failed",
  job_id: "job_abcdef12", item_id: "mob_official:1", size_bytes: 4096,
};

test("returns null for other routes and methods", async () => {
  assert.equal(await handleFailedMedia(req(good), fakeEnv(), h, "/jobs/retry", "POST"), null);
  assert.equal(await handleFailedMedia(req(undefined, "GET"), fakeEnv(), h, "/failed-media/record", "GET"), null);
});

test("valid record upserts on storage_key and audits", async () => {
  const env = fakeEnv();
  const res = await handleFailedMedia(req(good), env, h, "/failed-media/record", "POST");
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.deepEqual([body.ok, body.id, body.storage_key], [true, "fm_existing", good.storage_key]);
  const insert = env.calls[0];
  assert.match(insert.sql, /ON CONFLICT\(storage_key\) DO UPDATE/);
  assert.match(insert.sql, /COALESCE\(excluded\.share_url/);
  assert.match(insert.args[0], /^fm_[0-9a-f]{24}$/);
  assert.ok(env.calls.some((c) => c.args.includes("console_failed_media_record")));
});

test("rejects invalid input with 400", async () => {
  const bad = [
    { ...good, source: "youtube" }, { ...good, media_type: "audio" },
    { ...good, storage_key: "/Rubika-Agent/staging/a.mp4" }, { ...good, storage_key: "/Rubika-Agent/failed/.." },
    { ...good, storage_key: "/Rubika-Agent/failed/a/b.mp4" }, { ...good, share_url: "https://evil.example/x" },
    { ...good, reason: "Bad Reason!" }, { ...good, caption: "x".repeat(5001) }, { ...good, size_bytes: -1 },
    { ...good, job_id: "bad id" },
  ];
  for (const b of bad) {
    const env = fakeEnv();
    const res = await handleFailedMedia(req(b), env, h, "/failed-media/record", "POST");
    assert.equal(res.status, 400, JSON.stringify(b));
    assert.equal(env.calls.length, 0);
  }
  const arr = await handleFailedMedia(req([1]), fakeEnv(), h, "/failed-media/record", "POST");
  assert.equal(arr.status, 400);
});

test("share_url may be missing; DB error gives console_db_error 503", async () => {
  const { share_url, ...noUrl } = good;
  assert.equal((await handleFailedMedia(req(noUrl), fakeEnv(), h, "/failed-media/record", "POST")).status, 200);
  const res = await handleFailedMedia(req(good), fakeEnv({ fail: true }), h, "/failed-media/record", "POST");
  assert.equal(res.status, 503);
  assert.equal((await res.json()).error, "console_db_error");
});
