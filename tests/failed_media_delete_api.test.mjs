import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const src = readFileSync(new URL("../src/console_failed_media.js", import.meta.url), "utf8");
const { handleFailedMedia } = await import("data:text/javascript;base64," + Buffer.from(src).toString("base64"));

const OWNER_TOKEN = "test-owner-web-token-with-at-least-thirty-two-characters";
const json = (obj, status = 200) => new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json" } });
const readJson = async (req) => {
  try { return { ok: true, value: await req.json() }; } catch { return { ok: false, error: "invalid_json", status: 400 }; }
};
const h = { json, readJson };
const ID = "fm_" + "c".repeat(24);
const KEY = "/Rubika-Agent/failed/a.mp4";
let env;
const post = (sub, body, token = OWNER_TOKEN) => handleFailedMedia(
  new Request("https://x.test/api/console" + sub, {
    method: "POST",
    headers: { "content-type": "application/json", ...(token ? { authorization: "Bearer " + token } : {}) },
    body: JSON.stringify(body),
  }),
  env, h, sub, "POST",
);

function makeEnv({ changes = 1, token = "", fail = false } = {}) {
  const calls = [];
  const rows = new Map([[ID, {
    id: ID, storage_key: KEY, status: "stored", updated_at: "2026-11-03T00:00:00Z",
  }]]);
  return {
    calls,
    rows,
    AGENT_API_TOKEN: OWNER_TOKEN,
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
                  if (!Number.isInteger(count)) throw new Error("snapshot_bind_count_invalid");
                  const snapshot = args.slice(0, count * 4);
                  const duplicate = args.slice(count * 4, count * 8);
                  if (JSON.stringify(snapshot) !== JSON.stringify(duplicate)) throw new Error("snapshot_bind_mismatch");
                  const selected = [];
                  for (let i = 0; i < count; i += 1) {
                    const [id, status, updatedAt, storageKey] = snapshot.slice(i * 4, i * 4 + 4);
                    const row = rows.get(id);
                    if (!row || row.status !== status || row.updated_at !== updatedAt || row.storage_key !== storageKey) {
                      return { meta: { changes: 0 } };
                    }
                    selected.push(row);
                  }
                  if (changes === 0) return { meta: { changes: 0 } };
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
                return { meta: { changes } };
              },
              async all() {
                record();
                if (sql.includes("FROM failed_media WHERE id IN")) {
                  return { results: args.map(id => rows.get(id)).filter(Boolean)
                    .map(({ id, storage_key, status, updated_at }) => ({ id, storage_key, status, updated_at })) };
                }
                if (sql.includes("FROM failed_media WHERE status IN")) {
                  const selected = [...rows.values()].filter(row =>
                    ["stored", "delete_failed", "delete_requested"].includes(row.status));
                  return { results: selected.map(({ id, storage_key, status, updated_at }) => ({ id, storage_key, status, updated_at })) };
                }
                if (sql.includes("FROM failed_media WHERE status = 'delete_requested'")) {
                  return { results: [...rows.values()].filter(row => row.status === "delete_requested")
                    .slice(0, 50).map(({ id, storage_key }) => ({ id, storage_key })) };
                }
                return { results: [] };
              },
              async first() {
                record();
                if (sql.includes("SELECT id, storage_key, status FROM failed_media WHERE id = ?")) {
                  const row = rows.get(args[0]);
                  return row ? { id: row.id, storage_key: row.storage_key, status: row.status } : null;
                }
                return null;
              },
            };
          },
        };
      },
    },
  };
}

test("pending-delete lists only delete_requested rows and exposes no other statuses", async () => {
  env = makeEnv();
  const res = await post("/failed-media/pending-delete", {});
  const body = await res.json();
  assert.equal(res.status, 200);
  assert.deepEqual(body.items, []);
  assert.match(env.calls[0].sql, /status = 'delete_requested'/);
  env.rows.get(ID).status = "delete_requested";
  const again = await post("/failed-media/pending-delete", {});
  assert.deepEqual((await again.json()).items, [{ id: ID, storage_key: KEY }]);
});

test("delete-result success removes the row only after it is delete_requested and key-scoped", async () => {
  env = makeEnv();
  env.rows.get(ID).status = "delete_requested";
  const res = await post("/failed-media/delete-result", { id: ID, ok: true });
  assert.equal(res.status, 200);
  assert.equal((await res.json()).changed, 1);
  assert.match(env.calls[0].sql, /SELECT id, storage_key, status FROM failed_media WHERE id = \?/);
  assert.match(env.calls[1].sql, /^DELETE FROM failed_media WHERE id = \? AND status = 'delete_requested' AND storage_key = \?$/);
  assert.deepEqual(env.calls[1].args, [ID, KEY]);
  assert.equal(env.rows.has(ID), false);
});

test("delete-result failure keeps the row as delete_failed and never deletes", async () => {
  env = makeEnv();
  env.rows.get(ID).status = "delete_requested";
  const res = await post("/failed-media/delete-result", { id: ID, ok: false });
  assert.equal(res.status, 200);
  assert.equal((await res.json()).changed, 1);
  assert.match(env.calls[0].sql, /SELECT id, storage_key, status FROM failed_media/);
  assert.match(env.calls[1].sql, /^UPDATE failed_media SET status = 'delete_failed'/);
  assert.match(env.calls[1].sql, /AND storage_key = \?/);
  assert.doesNotMatch(env.calls[1].sql, /DELETE FROM/);
  assert.equal(env.rows.get(ID).status, "delete_failed");
});

test("delete-result validates id, ok, and current storage scope before mutation", async () => {
  for (const body of [{}, { id: "x", ok: true }, { id: ID }, { id: ID, ok: "yes" }]) {
    env = makeEnv();
    assert.equal((await post("/failed-media/delete-result", body)).status, 400, JSON.stringify(body));
    assert.equal(env.calls.length, 0);
  }
  env = makeEnv();
  env.rows.get(ID).status = "delete_requested";
  env.rows.get(ID).storage_key = "/Rubika-Agent/official/not-failed.mp4";
  const invalid = await post("/failed-media/delete-result", { id: ID, ok: true });
  assert.equal(invalid.status, 409);
  assert.equal((await invalid.json()).error, "invalid_storage_key");
  assert.equal(env.rows.has(ID), true);
});

test("delete-request requires a valid owner bearer before creating a challenge", async () => {
  env = makeEnv();
  const viewer = await post("/failed-media/delete-request", { ids: [ID] }, "viewer-web-token-is-not-the-owner-token");
  assert.equal(viewer.status, 403);
  const missing = await post("/failed-media/delete-request", { ids: [ID] }, null);
  assert.equal(missing.status, 403);
  env.AGENT_API_TOKEN = "short";
  const shortSecret = await post("/failed-media/delete-request", { ids: [ID] }, "short");
  assert.equal(shortSecret.status, 403, "the internal guard rejects short configured owner tokens");
  assert.equal(env.calls.length, 0);
  assert.equal(env.rows.get(ID).status, "stored");
});

test("delete-request dispatches only after signed server confirmation", async () => {
  const realFetch = globalThis.fetch;
  const sent = [];
  globalThis.fetch = async (url, init) => {
    sent.push({ url, init });
    return new Response(null, { status: 204 });
  };
  try {
    env = makeEnv({ changes: 1, token: "tok" });
    const stageOneResponse = await post("/failed-media/delete-request", { ids: [ID] });
    const stageOne = await stageOneResponse.json();
    assert.equal(stageOneResponse.status, 200);
    assert.equal(stageOne.confirmation_required, true);
    assert.equal(stageOne.requested, 1);
    assert.equal(env.rows.get(ID).status, "stored");
    assert.equal(sent.length, 0, "stage one cannot dispatch the Mega runner");

    const stageTwoResponse = await post("/failed-media/delete-request", { confirmation: stageOne.confirmation, phrase: "DELETE" });
    const stageTwo = await stageTwoResponse.json();
    assert.equal(stageTwoResponse.status, 200);
    assert.deepEqual([stageTwo.requested, stageTwo.dispatched], [1, true]);
    assert.equal(env.rows.get(ID).status, "delete_requested");
    assert.equal(sent.length, 1);
    assert.match(sent[0].url, /\/actions\/workflows\/failed-media-delete\.yml\/dispatches$/);
    assert.equal(sent[0].init.headers.Authorization, "Bearer tok");
    assert.deepEqual(JSON.parse(sent[0].init.body), { ref: "main" });
  } finally {
    globalThis.fetch = realFetch;
  }
});

test("stale confirmation and empty dispatch sets are rejected without redispatch", async () => {
  env = makeEnv({ changes: 0, token: "tok" });
  const challenge = await (await post("/failed-media/delete-request", { ids: [ID] })).json();
  const missingPhrase = await post("/failed-media/delete-request", { confirmation: challenge.confirmation });
  assert.equal(missingPhrase.status, 400);
  assert.equal((await missingPhrase.json()).error, "confirmation_phrase_required");
  const wrongPhrase = await post("/failed-media/delete-request", { confirmation: challenge.confirmation, phrase: "NO" });
  assert.equal(wrongPhrase.status, 400);
  assert.equal((await wrongPhrase.json()).error, "confirmation_phrase_required");
  assert.equal(env.rows.get(ID).status, "stored");
  const stale = await post("/failed-media/delete-request", { confirmation: challenge.confirmation, phrase: "DELETE" });
  assert.equal(stale.status, 409);
  assert.equal((await stale.json()).error, "confirmation_stale");
  assert.equal(env.rows.get(ID).status, "stored");
});

test("missing dispatch token or network error leaves request visible without changing order", async () => {
  env = makeEnv({ changes: 1, token: "" });
  const challenge = await (await post("/failed-media/delete-request", { ids: [ID] })).json();
  const noToken = await post("/failed-media/delete-request", { confirmation: challenge.confirmation, phrase: "DELETE" });
  assert.equal(noToken.status, 200);
  assert.equal((await noToken.json()).dispatched, false);

  const realFetch = globalThis.fetch;
  globalThis.fetch = async () => { throw new Error("network"); };
  try {
    env = makeEnv({ changes: 1, token: "tok" });
    const secondChallenge = await (await post("/failed-media/delete-request", { ids: [ID] })).json();
    const failed = await post("/failed-media/delete-request", { confirmation: secondChallenge.confirmation, phrase: "DELETE" });
    assert.equal(failed.status, 200);
    assert.equal((await failed.json()).dispatched, false);
  } finally {
    globalThis.fetch = realFetch;
  }
});
