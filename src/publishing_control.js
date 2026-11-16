const PAUSE_KEY = "publishing_paused";
const CHALLENGE_PREFIX = "publishing_control_challenge_";

const randomHex = (size = 16) => {
  const bytes = crypto.getRandomValues(new Uint8Array(size));
  return Array.from(bytes, value => value.toString(16).padStart(2, "0")).join("");
};

export async function getPublishingControl(db) {
  try {
    const row = await db.prepare("SELECT value FROM panel_state WHERE key = ?")
      .bind(PAUSE_KEY).first();
    if (!row) return { ok: true, paused: false };
    if (row.value === "true") return { ok: true, paused: true };
    if (row.value === "false") return { ok: true, paused: false };
    return { ok: false, paused: true, error: "publishing_control_state_invalid" };
  } catch {
    // A missing/invalid control store must never permit a publish.
    return { ok: false, paused: true, error: "publishing_control_unavailable" };
  }
}

export function publishingControlReasonFa(control) {
  if (control?.ok !== true) return "وضعیت توقف انتشار قابل‌بررسی نیست؛ برای ایمنی هیچ ارسالی انجام نمی‌شود.";
  return control.paused === true
    ? "انتشار با فرمان مالک موقتاً متوقف شده است."
    : "توقف اضطراری فعال نیست.";
}

export async function startPublishingControlChallenge(db, action, actor) {
  if (!["pause", "resume"].includes(action) ||
      typeof actor !== "string" || !/^(?:web-owner|rubika:[0-9]{1,32})$/.test(actor)) {
    return { ok: false, error: "publishing_control_request_invalid" };
  }
  const challengeId = randomHex(16);
  const expiresAt = new Date(Date.now() + 2 * 60 * 1000).toISOString();
  const key = CHALLENGE_PREFIX + challengeId;
  const value = JSON.stringify({ action, actor, expires_at: expiresAt });
  try {
    await db.prepare(
      "DELETE FROM panel_state WHERE key LIKE ? AND updated_at < datetime('now','-1 day')"
    ).bind(CHALLENGE_PREFIX + "%").run();
    await db.prepare(
      "INSERT INTO panel_state(key,value,updated_at) VALUES(?,?,datetime('now'))"
    ).bind(key, value).run();
    return { ok: true, challenge_id: challengeId, action, expires_at: expiresAt };
  } catch {
    return { ok: false, error: "publishing_control_challenge_unavailable" };
  }
}

export async function confirmPublishingControl(db, challengeId, actor) {
  if (typeof challengeId !== "string" || !/^[a-f0-9]{32}$/.test(challengeId) ||
      typeof actor !== "string" || !/^(?:web-owner|rubika:[0-9]{1,32})$/.test(actor)) {
    return { ok: false, error: "publishing_control_confirmation_invalid" };
  }
  const key = CHALLENGE_PREFIX + challengeId;
  try {
    const row = await db.prepare("SELECT value FROM panel_state WHERE key = ?").bind(key).first();
    if (!row || typeof row.value !== "string") {
      return { ok: false, error: "publishing_control_challenge_stale" };
    }
    let challenge;
    try { challenge = JSON.parse(row.value); } catch {}
    if (!challenge || !["pause", "resume"].includes(challenge.action) ||
        challenge.actor !== actor ||
        typeof challenge.expires_at !== "string" ||
        challenge.expires_at <= new Date().toISOString()) {
      return { ok: false, error: "publishing_control_challenge_stale" };
    }

    const pausedValue = challenge.action === "pause" ? "true" : "false";
    const now = new Date().toISOString();
    const result = await db.batch([
      db.prepare(
        "DELETE FROM panel_state WHERE key = ? AND value = ? AND julianday(json_extract(value, '$.expires_at')) > julianday(?)"
      ).bind(key, row.value, now),
      db.prepare(
        "INSERT INTO panel_state(key,value,updated_at) " +
        "SELECT ?, ?, datetime('now') WHERE changes() = 1 " +
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at"
      ).bind(PAUSE_KEY, pausedValue),
      db.prepare(
        "INSERT INTO brain_events(item_id,level,code,details_json) " +
        "SELECT NULL,'info','publishing_control_changed',? WHERE changes() > 0"
      ).bind(JSON.stringify({ action: challenge.action, paused: pausedValue === "true", actor_kind: actor.startsWith("rubika:") ? "rubika" : "web" })),
    ]);
    if (!result?.[0]?.meta?.changes || !result?.[1]?.meta?.changes) {
      return { ok: false, error: "publishing_control_challenge_stale" };
    }
    const control = await getPublishingControl(db);
    if (!control.ok) return { ok: false, error: "publishing_control_unavailable" };
    return { ok: true, action: challenge.action, paused: control.paused };
  } catch {
    return { ok: false, error: "publishing_control_update_failed" };
  }
}
