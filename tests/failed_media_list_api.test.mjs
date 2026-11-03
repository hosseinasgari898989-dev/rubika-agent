import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const src = readFileSync(new URL("../src/console_failed_media.js", import.meta.url), "utf8");
const { handleFailedMedia } = await import("data:text/javascript;base64," + Buffer.from(src).toString("base64"));

const OWNER_TOKEN = "test-owner-web-token-with-at-least-thirty-two-characters";
const AGENT_TOKEN = "test-agent-api-token-with-at-least-thirty-two-characters";
const json = (obj, status = 200) => new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json" } });
const readJson = async (req) => {
  try { return { ok: true, value: await req.json() }; } catch { return { ok: false, error: "invalid_json", status: 400 }; }
};
const h = { json, readJson };
const ID1 = "fm_" + "a".repeat(24);
const ID2 = "fm_" + "b".repeat(24);
const keyFor = id => "/Rubika-Agent/failed/" + id.slice(-8) + ".mp4";

let env;
function makeEnv({ fail = false, changes = null, token = "", allCount = 7 } = {}) {
  const calls = [];
  const rows = new Map();
  const seed = (id, status = "stored") => rows.set(id, {
    id, storage_key: keyFor(id), status, updated_at: "2026-11-03T00:00:00Z",
    source: "mob_x", media_type: "video", caption: "caption",
  });
  seed(ID1);
  seed(ID2);
  for (let i = 0; i < Math.max(0, allCount - 2); i += 1) {
    const id = "fm_" + i.toString(16).padStart(24, "0");
    if (!rows.has(id)) seed(id);
  }
  return {
    calls,
    AGENT_API_TOKEN: AGENT_TOKEN,
    OWNER_WEB_TOKEN: OWNER_TOKEN,
    GITHUB_DISPATCH_TOKEN: token,
    DB: {
      prepare(sql) {
        return {
          bind(...args) {
            const record = () => {
              if (fail && /failed_media/.test(sql)) throw new Error("mock_db_failure");
              calls.push({ sql, args });
            };
            return {
              async run() {
                record();
                if (sql.startsWith("UPDATE failed_media SET status = 'delete_requested'")) {
                  const count = (args.length - 1) / 8;
                  if (!Number.isInteger(count)) throw new Error("mock_snapshot_bind_count_invalid");
                  const snapshot = args.slice(0, count * 4);
                  const duplicate = args.slice(count * 4, count * 8);
                  if (JSON.stringify(snapshot) !== JSON.stringify(duplicate)) throw new Error("mock_snapshot_bind_mismatch");
                  const selected = [];
                  for (let i = 0; i < count; i += 1) {
                    const [id, status, updatedAt, storageKey] = snapshot.slice(i * 4, i * 4 + 4);
                    const row = rows.get(id);
                    if (!row || row.status !== status || row.updated_at !== updatedAt || row.storage_key !== storageKey) {
                      return { meta: { changes: 0 } };
                    }
                    selected.push(row);
                  }
                  for (const row of selected) Object.assign(row, {
                    status: "delete_requested", updated_at: "2026-11-03T00:01:00.000Z",
                  });
                  return { meta: { changes: selected.length } };
                }
                if (sql.startsWith("DELETE FROM failed_media")) {
                  const row = rows.get(args[0]);
                  if (!row || row.status !== "delete_requested" || row.storage_key !== args[1]) return { meta: { changes: 0 } };
                  rows.delete(args[0]);
                  return { meta: { changes: 1 } };
                }
                if (sql.startsWith("UPDATE failed_media SET status = 'delete_failed'")) {
                  const row = rows.get(args[0]);
                  if (!row || row.status !== "delete_requested" || row.storage_key !== args[1]) return { meta: { changes: 0 } };
                  Object.assign(row, { status: "delete_failed", updated_at: "2026-11-03T00:02:00.000Z" });
                  return { meta: { changes: 1 } };
                }
                return { meta: { changes: changes ?? 1 } };
              },
              async all() {
                record();
                if (sql.includes("FROM failed_media WHERE id IN")) {
                  const selected = args.map(id => rows.get(id)).filter(Boolean);
                  return { results: selected.map(({ id, storage_key, status, updated_at }) => ({ id, storage_key, status, updated_at })) };
                }
                if (sql.includes("FROM failed_media WHERE status IN")) {
                  const limit = Number(args[0]);
                  const selected = [...rows.values()]
                    .filter(row => ["stored", "delete_failed", "delete_requested"].includes(row.status))
                    .sort((a, b) => a.updated_at.localeCompare(b.updated_at) || a.id.localeCompare(b.id))
                    .slice(0, limit);
                  return { results: selected.map(({ id, storage_key, status, updated_at }) => ({ id, storage_key, status, updated_at })) };
                }
                if (sql.includes("FROM failed_media WHERE status = 'delete_requested'")) {
                  const selected = [...rows.values()].filter(row => row.status === "delete_requested").slice(0, 50);
                  return { results: selected.map(({ id, storage_key }) => ({ id, storage_key })) };
                }
                if (sql.includes("FROM failed_media ORDER BY created_at DESC")) {
                  return { results: [{ id: ID1, status: "stored", caption: "caption" }] };
                }
                return { results: [] };
              },
              async first() {
                record();
                if (sql.includes("SELECT COUNT(*) AS n FROM failed_media")) return { n: 7 };
                if (sql.includes("SELECT id, storage_key, status FROM failed_media WHERE id = ?")) {
                  const row = rows.get(args[0]);
                  return row ? { id: row.id, storage_key: row.storage_key, status: row.status } : null;
                }
                return { n: 7 };
              },
            };
          },
        };
      },
    },
    rows,
  };
}
const post = (sub, body, token = OWNER_TOKEN) => handleFailedMedia(
  new Request("https://x.test/api/console" + sub, {
    method: "POST",
    headers: { "content-type": "application/json", ...(token ? { authorization: "Bearer " + token } : {}) },
    body: JSON.stringify(body),
  }),
  env, h, sub, "POST",
);

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

test("delete-request only issues an owner-bound snapshot challenge in phase one", async () => {
  env = makeEnv();
  const res = await post("/failed-media/delete-request", { ids: [ID1, ID2] });
  const body = await res.json();
  assert.equal(res.status, 200);
  assert.equal(body.ok, true);
  assert.equal(body.confirmation_required, true);
  assert.equal(body.requested, 2);
  assert.equal(typeof body.confirmation, "string");
  assert.equal(env.rows.get(ID1).status, "stored");
  assert.equal(env.rows.get(ID2).status, "stored");
  assert.match(env.calls[0].sql, /SELECT id, storage_key, status, updated_at FROM failed_media/);
  assert.deepEqual(env.calls[0].args, [ID1, ID2]);
  assert.equal(env.calls.some(call => call.sql.startsWith("UPDATE failed_media")), false);
  assert.equal(env.calls.some(call => call.sql.startsWith("DELETE FROM")), false);
});

test("wrong bearer cannot request or confirm deletion", async () => {
  env = makeEnv();
  const denied = await post("/failed-media/delete-request", { ids: [ID1] }, "viewer-web-token-is-not-the-owner-token");
  assert.equal(denied.status, 403);
  assert.equal(env.calls.length, 0);
  const noBearer = await post("/failed-media/delete-request", { ids: [ID1] }, null);
  assert.equal(noBearer.status, 403);
  assert.equal(env.calls.length, 0);
});

test("a valid owner confirmation changes exact snapshots and dispatches once", async () => {
  const realFetch = globalThis.fetch;
  const dispatched = [];
  globalThis.fetch = async (url, init) => {
    dispatched.push({ url, init });
    return new Response(null, { status: 204 });
  };
  try {
    env = makeEnv({ token: "test-dispatch-token" });
    const stageOne = await (await post("/failed-media/delete-request", { ids: [ID1] })).json();
    const stageTwoResponse = await post("/failed-media/delete-request", { confirmation: stageOne.confirmation, phrase: "DELETE" });
    const stageTwo = await stageTwoResponse.json();
    assert.equal(stageTwoResponse.status, 200);
    assert.deepEqual([stageTwo.requested, stageTwo.dispatched], [1, true]);
    const update = env.calls.find(call => call.sql.startsWith("UPDATE failed_media SET status = 'delete_requested'"));
    assert.ok(update);
    assert.match(update.sql, /COALESCE\(updated_at, ''\)/);
    assert.equal(update.args.length, 9);
    assert.equal(env.rows.get(ID1).status, "delete_requested");
    assert.equal(env.rows.get(ID2).status, "stored");
    assert.equal(dispatched.length, 1);
    assert.match(dispatched[0].url, /failed-media-delete\.yml\/dispatches$/);
  } finally {
    globalThis.fetch = realFetch;
  }
});

test("replayed or tampered signed confirmation is rejected", async () => {
  env = makeEnv();
  const challenge = await (await post("/failed-media/delete-request", { ids: [ID1] })).json();
  const missingPhrase = await post("/failed-media/delete-request", { confirmation: challenge.confirmation });
  assert.equal(missingPhrase.status, 400);
  assert.equal((await missingPhrase.json()).error, "confirmation_phrase_required");
  const wrongPhrase = await post("/failed-media/delete-request", { confirmation: challenge.confirmation, phrase: "NO" });
  assert.equal(wrongPhrase.status, 400);
  assert.equal((await wrongPhrase.json()).error, "confirmation_phrase_required");
  assert.equal(env.rows.get(ID1).status, "stored");
  const tampered = challenge.confirmation.slice(0, -1) + (challenge.confirmation.endsWith("A") ? "B" : "A");
  const invalid = await post("/failed-media/delete-request", { confirmation: tampered, phrase: "DELETE" });
  assert.equal(invalid.status, 409);
  assert.equal((await invalid.json()).error, "confirmation_invalid_or_expired");
  const [payloadPart, signaturePart] = challenge.confirmation.split(".");
  const changedSignature = (signaturePart[0] === "A" ? "B" : "A") + signaturePart.slice(1);
  const badSignature = await post("/failed-media/delete-request", { confirmation: payloadPart + "." + changedSignature, phrase: "DELETE" });
  assert.equal(badSignature.status, 409, "changed signature bytes must fail HMAC verification");
  assert.equal((await badSignature.json()).error, "confirmation_invalid_or_expired");
  const first = await post("/failed-media/delete-request", { confirmation: challenge.confirmation, phrase: "DELETE" });
  assert.equal(first.status, 200);
  const replay = await post("/failed-media/delete-request", { confirmation: challenge.confirmation, phrase: "DELETE" });
  assert.equal(replay.status, 409);
  assert.equal((await replay.json()).error, "confirmation_stale");
});

test("selected requests reject empty, invalid, duplicate, and over-limit ids", async () => {
  const bad = [{}, { ids: [] }, { ids: ["x"] }, { ids: [ID1, 5] }, { ids: [ID1, ID1] },
    { ids: Array.from({ length: 51 }, (_, i) => "fm_" + i.toString(16).padStart(24, "0")) }];
  for (const body of bad) {
    env = makeEnv();
    assert.equal((await post("/failed-media/delete-request", body)).status, 400, JSON.stringify(body));
    assert.equal(env.calls.length, 0);
  }
});

test("delete-all only stages up to 50 rows and requires signed second step", async () => {
  env = makeEnv({ allCount: 55 });
  const firstResponse = await post("/failed-media/delete-request", { all: true });
  const first = await firstResponse.json();
  assert.equal(firstResponse.status, 200);
  assert.equal(first.requested, 50);
  assert.equal(first.limited, true);
  assert.equal([...env.rows.values()].filter(row => row.status === "stored").length, 55);
  const secondResponse = await post("/failed-media/delete-request", { confirmation: first.confirmation, phrase: "DELETE" });
  const second = await secondResponse.json();
  assert.equal(secondResponse.status, 200);
  assert.equal(second.requested, 50);
  assert.equal([...env.rows.values()].filter(row => row.status === "delete_requested").length, 50);
  assert.equal([...env.rows.values()].filter(row => row.status === "stored").length, 5);
});

test("delete-all rejects the old client-only confirmation fields", async () => {
  env = makeEnv();
  for (const body of [{ all: true, confirm: "DELETE_ALL" }, { all: "true" }, { ids: [] }]) {
    assert.equal((await post("/failed-media/delete-request", body)).status, 400);
  }
  assert.equal(env.calls.length, 0);
});

test("DB errors give console_db_error 503; unknown routes and GET give null", async () => {
  env = makeEnv({ fail: true });
  for (const [sub, body] of [["/failed-media/list", {}], ["/failed-media/delete-request", { ids: [ID1] }]]) {
    const res = await post(sub, body);
    assert.equal(res.status, 503);
    assert.equal((await res.json()).error, "console_db_error");
  }
  assert.equal(await post("/failed-media/other", {}), null);
  assert.equal(await post("/failed-media/constructor", {}), null);
  assert.equal(await handleFailedMedia(new Request("https://x.test/"), env, h, "/failed-media/list", "GET"), null);
});

test("pending-delete and delete-result enforce the /failed/ key and keep Mega before row deletion", async () => {
  env = makeEnv();
  const row = env.rows.get(ID1);
  row.status = "delete_requested";
  const pending = await post("/failed-media/pending-delete", {});
  assert.equal(pending.status, 200);
  assert.deepEqual((await pending.json()).items.map(item => item.id), [ID1]);

  const bad = await env.DB.prepare("UPDATE failed_media SET storage_key = ? WHERE id = ?").bind("/Rubika-Agent/official/no.mp4", ID1).run();
  assert.equal(bad.meta.changes, 1);
  env.rows.get(ID1).storage_key = "/Rubika-Agent/official/no.mp4";
  const invalidPending = await post("/failed-media/pending-delete", {});
  assert.equal(invalidPending.status, 409);

  env.rows.get(ID1).storage_key = keyFor(ID1);
  const success = await post("/failed-media/delete-result", { id: ID1, ok: true });
  assert.equal(success.status, 200);
  assert.equal(env.rows.has(ID1), false);
});

if (globalThis.fetch && typeof globalThis.fetch === "function") {
  test("route-level owner bearer is checked before mutation", async () => {
    env = makeEnv();
    const wrong = await post("/failed-media/delete-request", { ids: [ID1] }, "viewer-token");
    assert.equal(wrong.status, 403);
  });
}
