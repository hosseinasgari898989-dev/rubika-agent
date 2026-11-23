const REPOSITORY = "hosseinasgari898989-dev/rubika-agent";
const API_ROOT = "https://api.github.com/repos/" + REPOSITORY + "/actions/workflows/";
const OFF_KEY = "bot_off";
const PAUSE_KEY = "publishing_paused";
const UPSERT_SQL =
  "INSERT INTO panel_state(key,value,updated_at) VALUES(?,?,datetime('now')) " +
  "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at";

export const BOT_POWER_WORKFLOWS = Object.freeze([
  "termux-docker-discovery.yml",
  "mob-x-link-processor.yml",
  "daily-queue-processor.yml",
  "mob-x-news-production.yml",
  "owner-daily-digest.yml",
  "owner-digest-refresh.yml",
]);

export async function isBotOff(db) {
  try {
    const row = await db.prepare("SELECT value FROM panel_state WHERE key = ?").bind(OFF_KEY).first();
    return Boolean(row && row.value === "true");
  } catch {
    return false;
  }
}

async function toggleWorkflow(token, workflow, enable, fetcher) {
  try {
    const response = await fetcher(API_ROOT + workflow + (enable ? "/enable" : "/disable"), {
      method: "PUT",
      headers: {
        "User-Agent": "rubika-agent-worker",
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        Authorization: "Bearer " + token,
      },
      signal: AbortSignal.timeout(4000),
    });
    return { workflow, ok: Boolean(response && response.ok), status: Number(response && response.status) || null };
  } catch {
    return { workflow, ok: false, status: null };
  }
}

export async function setBotPower(env, on, fetcher = globalThis.fetch) {
  const db = env && env.DB;
  if (!db) return { ok: false, error: "db_missing", results: [] };
  const token = typeof env.GITHUB_DISPATCH_TOKEN === "string" ? env.GITHUB_DISPATCH_TOKEN.trim() : "";
  const off = on !== true;
  const value = off ? "true" : "false";
  try {
    await db.batch([
      db.prepare(UPSERT_SQL).bind(OFF_KEY, value),
      db.prepare(UPSERT_SQL).bind(PAUSE_KEY, value),
      db.prepare("INSERT INTO brain_events(item_id,level,code,details_json) VALUES(NULL,'info','bot_power_changed',?)")
        .bind(JSON.stringify({ bot_off: off })),
    ]);
  } catch {
    return { ok: false, error: "bot_power_state_failed", results: [] };
  }
  const results = token && typeof fetcher === "function"
    ? await Promise.all(BOT_POWER_WORKFLOWS.map((workflow) => toggleWorkflow(token, workflow, !off, fetcher)))
    : [];
  return { ok: true, bot_off: off, workflows_checked: Boolean(token), results };
}
