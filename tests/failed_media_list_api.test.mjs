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
const post = (sub, body) => handleFailedMedia(new Request("https://x.test/api/console" + sub, { method: "POST", body: JSON.stringify(body) }), env, h, sub, "POST");
const ID1 = "fm_" + "a".repeat(24);
const ID2 = "fm_" + "b".repeat(24);

let env;
function makeEnv({ fail = false, changes = 2 } = {}) {
  const calls = [];
  return {
    calls,
    DB: {
      prepare(sql) {
        return {
          bind(...args) {
            const rec = () => { if (fail && /failed_media/.test(sql)) throw new Error("db"); calls.push({ sql, args }); };
            return {
              async run() { rec(); return { meta: { changes } }; },
              async all() { rec(); return { results: [{ id: ID1, status: "stored", caption: "c" }] }; },
              async first() { rec(); return { n: 7 }; },
            };
          },
        };
      },
    },
  };
}

test("list returns items and total with defaults", async () => {
  env = makeEnv();
  const res = await post("/failed-media/list", {});
  const body = await res.json();
  assert.equal(res.status, 200);
  assert.deepEqual([body.ok, body.total, body.limit, body.offset, body.items.length], [true, 7, 50, 0, 1]);
  assert.match(env.calls[0].sql, /ORDER BY created_at DESC/);
  assert.deepEqual(env.calls[0].args, [50, 0]);
});

test("list validates limit and offset", async () => {
  for (const b of [{ limit: 0 }, { limit: 101 }, { limit: "5" }, { limit: 1.5 }]) {
    env = makeEnv();
    assert.equal((await post("/failed-media/list", b)).status, 400, JSON.stringify(b));
  }
  env = makeEnv();
  assert.equal((await post("/failed-media/list", { offset: -1 })).status, 400);
  assert.equal(env.calls.length, 0);
});

test("delete-request by ids only marks eligible rows and never deletes", async () => {
  env = makeEnv({ changes: 2 });
  const res = await post("/failed-media/delete-request", { ids: [ID1, ID2, ID1] });
  assert.equal(res.status, 200);
  assert.equal((await res.json()).requested, 2);
  const q = env.calls[0];
  assert.match(q.sql, /^UPDATE failed_media SET status = 'delete_requested'/);
  assert.match(q.sql, /status IN \('stored','delete_failed'\)/);
  assert.doesNotMatch(q.sql, /DELETE/i);
  assert.deepEqual(q.args, [ID1, ID2]);
  assert.ok(env.calls.some((c) => c.args.includes("console_failed_media_delete_request")));
});

test("delete-request rejects bad ids and unconfirmed delete-all", async () => {
  const bad = [{}, { ids: [] }, { ids: ["x"] }, { ids: [ID1, 5] }, { ids: Array(101).fill(ID1) }, { all: true }, { all: true, confirm: "yes" }, { all: "true", confirm: "DELETE_ALL" }];
  for (const b of bad) {
    env = makeEnv();
    assert.equal((await post("/failed-media/delete-request", b)).status, 400, JSON.stringify(b));
    assert.equal(env.calls.length, 0);
  }
});

test("delete-all needs exact confirmation phrase", async () => {
  env = makeEnv({ changes: 5 });
  const res = await post("/failed-media/delete-request", { all: true, confirm: "DELETE_ALL" });
  assert.equal((await res.json()).requested, 5);
  assert.deepEqual(env.calls[0].args, []);
  assert.doesNotMatch(env.calls[0].sql, /\bIN \(\?/);
});

test("DB errors give console_db_error 503; unknown routes and GET give null", async () => {
  env = makeEnv({ fail: true });
  for (const [sub, b] of [["/failed-media/list", {}], ["/failed-media/delete-request", { ids: [ID1] }]]) {
    const res = await post(sub, b);
    assert.equal(res.status, 503);
    assert.equal((await res.json()).error, "console_db_error");
  }
  assert.equal(await post("/failed-media/other", {}), null);
  assert.equal(await post("/failed-media/constructor", {}), null);
  assert.equal(await handleFailedMedia(new Request("https://x.test/"), env, h, "/failed-media/list", "GET"), null);
});
