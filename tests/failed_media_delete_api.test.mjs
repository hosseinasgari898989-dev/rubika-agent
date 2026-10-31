import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const src = readFileSync(new URL("../src/console_failed_media.js", import.meta.url), "utf8");
const { handleFailedMedia } = await import("data:text/javascript;base64," + Buffer.from(src).toString("base64"));

const json = (obj, status = 200) => new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json" } });
const readJson = async (req) => {
  try { return { ok: true, value: await req.json() }; } catch { return { ok: false, error: "invalid_json", status: 400 }; }
};
const h = { json, readJson };
const ID = "fm_" + "c".repeat(24);
let env;
const post = (sub, body) => handleFailedMedia(new Request("https://x.test/x", { method: "POST", body: JSON.stringify(body) }), env, h, sub, "POST");

function makeEnv({ changes = 1, token } = {}) {
  const calls = [];
  return {
    calls, GITHUB_DISPATCH_TOKEN: token,
    DB: { prepare(sql) { return { bind(...args) { return {
      async run() { calls.push({ sql, args }); return { meta: { changes } }; },
      async all() { calls.push({ sql, args }); return { results: [{ id: ID, storage_key: "/Rubika-Agent/failed/a.mp4" }] }; },
    }; } }; } },
  };
}

test("pending-delete lists only delete_requested rows", async () => {
  env = makeEnv();
  const res = await post("/failed-media/pending-delete", {});
  const body = await res.json();
  assert.equal(res.status, 200);
  assert.equal(body.items.length, 1);
  assert.match(env.calls[0].sql, /status = 'delete_requested'/);
});

test("delete-result ok removes the row only while delete_requested", async () => {
  env = makeEnv({ changes: 1 });
  const res = await post("/failed-media/delete-result", { id: ID, ok: true });
  assert.equal((await res.json()).changed, 1);
  assert.match(env.calls[0].sql, /^DELETE FROM failed_media WHERE id = \? AND status = 'delete_requested'$/);
  assert.deepEqual(env.calls[0].args, [ID]);
});

test("delete-result failure keeps the row as delete_failed and never deletes", async () => {
  env = makeEnv();
  await post("/failed-media/delete-result", { id: ID, ok: false });
  assert.match(env.calls[0].sql, /^UPDATE failed_media SET status = 'delete_failed'/);
  assert.doesNotMatch(env.calls[0].sql, /DELETE FROM/);
});

test("delete-result validates id and ok", async () => {
  for (const b of [{}, { id: "x", ok: true }, { id: ID }, { id: ID, ok: "yes" }]) {
    env = makeEnv();
    assert.equal((await post("/failed-media/delete-result", b)).status, 400, JSON.stringify(b));
    assert.equal(env.calls.length, 0);
  }
});

test("delete-request dispatches the workflow once via GITHUB_DISPATCH_TOKEN", async () => {
  const realFetch = globalThis.fetch;
  const sent = [];
  globalThis.fetch = async (url, init) => { sent.push({ url, init }); return new Response(null, { status: 204 }); };
  try {
    env = makeEnv({ changes: 2, token: "tok" });
    const body = await (await post("/failed-media/delete-request", { ids: [ID] })).json();
    assert.deepEqual([body.requested, body.dispatched], [2, true]);
    assert.equal(sent.length, 1);
    assert.match(sent[0].url, /\/actions\/workflows\/failed-media-delete\.yml\/dispatches$/);
    assert.equal(sent[0].init.headers.Authorization, "Bearer tok");
    assert.deepEqual(JSON.parse(sent[0].init.body), { ref: "main" });

    env = makeEnv({ changes: 0, token: "tok" });
    assert.equal((await (await post("/failed-media/delete-request", { ids: [ID] })).json()).dispatched, false);
    assert.equal(sent.length, 1, "no dispatch when nothing was requested");

    env = makeEnv({ changes: 1 });
    assert.equal((await (await post("/failed-media/delete-request", { ids: [ID] })).json()).dispatched, false);
    assert.equal(sent.length, 1, "no token means no dispatch");

    globalThis.fetch = async () => { throw new Error("network"); };
    env = makeEnv({ changes: 1, token: "tok" });
    const failed = await post("/failed-media/delete-request", { ids: [ID] });
    assert.equal(failed.status, 200);
    assert.equal((await failed.json()).dispatched, false);
  } finally {
    globalThis.fetch = realFetch;
  }
});
