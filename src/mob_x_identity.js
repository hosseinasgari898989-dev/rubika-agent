// Shared identity and duplicate guard for automatic and owner-submitted Mob X links.
const POST_ID_RE = /^(?:x-)?([0-9]{15,22})$/i;

export function mobXItemIdAliases(value) {
  const match = POST_ID_RE.exec(String(value ?? "").trim());
  if (!match) return null;
  const postId = match[1];
  return {
    postId,
    canonicalItemId: "mob_official:x-" + postId,
    legacyItemId: "mob_official:" + postId,
  };
}

// Keep these as single-source SQL constants so SQLite schema tests exercise
// the exact statements the Worker sends to D1.
export const MOB_X_DUPLICATE_ITEM_SQL =
  "SELECT id, status, review_state FROM items " +
  "WHERE platform = 'mob_official' AND id IN (?, ?) " +
  "AND status IN ('ready', 'sending', 'sent', 'uncertain') " +
  "AND (? IS NULL OR id <> ? OR status IN ('sending', 'sent', 'uncertain') OR review_state <> 'pending') " +
  "LIMIT 1";

export const MOB_X_DUPLICATE_JOB_SQL =
  "SELECT j.id, j.status, json_extract(j.result_json, '$.item_id') AS item_id " +
  "FROM console_jobs j LEFT JOIN panel_links p ON p.id = j.panel_link_id " +
  "WHERE j.type = 'mob_x_link' " +
  "AND j.status IN ('confirmed', 'queued', 'processing', 'ready_for_review', 'approved', 'sending', 'sent', 'uncertain') " +
  "AND (p.external_id = ? OR json_extract(j.result_json, '$.item_id') IN (?, ?)) " +
  "AND (? IS NULL OR j.id <> ?) " +
  "LIMIT 1";

export async function findMobXDeliveryDuplicate(env, externalId, {
  allowItemId = null,
  allowJobId = null,
} = {}) {
  const aliases = mobXItemIdAliases(externalId);
  if (!aliases || !env?.DB) return null;

  const item = await env.DB.prepare(MOB_X_DUPLICATE_ITEM_SQL).bind(
    aliases.canonicalItemId,
    aliases.legacyItemId,
    allowItemId,
    allowItemId,
  ).first();
  if (item) return { kind: "item", id: item.id, status: item.status };

  const job = await env.DB.prepare(MOB_X_DUPLICATE_JOB_SQL).bind(
    aliases.postId,
    aliases.canonicalItemId,
    aliases.legacyItemId,
    allowJobId,
    allowJobId,
  ).first();
  if (job) return { kind: "job", id: job.id, status: job.status };

  return null;
}
