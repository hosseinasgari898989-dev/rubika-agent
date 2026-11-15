export function normalizeMobXExternalId(value) {
  if (typeof value !== "string") return null;
  let normalized = value.trim();
  if (normalized.startsWith("mob_official:")) normalized = normalized.slice("mob_official:".length);
  if (normalized.startsWith("x-")) normalized = normalized.slice(2);
  return /^[0-9]{15,22}$/.test(normalized) ? normalized : null;
}

export function mobXIdentityAliases(value) {
  const id = normalizeMobXExternalId(value);
  if (!id) return [];
  return [id, "x-" + id, "mob_official:" + id, "mob_official:x-" + id];
}

export async function findMobXDuplicate(db, value, {
  excludeItemId = null,
  excludeJobId = null,
  excludePanelLinkId = null,
} = {}) {
  const id = normalizeMobXExternalId(value);
  if (!id) return { duplicate: false, invalid: true };
  const prefixed = "x-" + id;
  const itemIds = ["mob_official:" + id, "mob_official:" + prefixed];

  let itemSql =
    "SELECT id, status, external_id FROM items WHERE platform = 'mob_official' " +
    "AND (external_id IN (?, ?) OR id IN (?, ?))";
  const itemArgs = [id, prefixed, ...itemIds];
  if (excludeItemId) {
    itemSql += " AND id <> ?";
    itemArgs.push(excludeItemId);
  }
  itemSql += " LIMIT 1";
  const item = await db.prepare(itemSql).bind(...itemArgs).first();

  let jobSql =
    "SELECT id, status FROM console_jobs WHERE type = 'mob_x_link' AND (" +
    "(CASE WHEN json_valid(result_json) THEN json_extract(result_json, '$.external_id') END IN (?, ?)) OR " +
    "(CASE WHEN json_valid(result_json) THEN json_extract(result_json, '$.item_id') END IN (?, ?)) OR " +
    "(CASE WHEN json_valid(params_json) THEN json_extract(params_json, '$.url') END IN (?, ?)))";
  const canonicalUrl = "https://x.com/mobgamesstudios/status/" + id;
  const twitterUrl = "https://twitter.com/mobgamesstudios/status/" + id;
  const jobArgs = [id, prefixed, ...itemIds, canonicalUrl, twitterUrl];
  if (excludeJobId) {
    jobSql += " AND id <> ?";
    jobArgs.push(excludeJobId);
  }
  jobSql += " LIMIT 1";
  const job = await db.prepare(jobSql).bind(...jobArgs).first();

  let linkSql = "SELECT id, status FROM panel_links WHERE external_id IN (?, ?)";
  const linkArgs = [id, prefixed];
  if (excludePanelLinkId != null) {
    linkSql += " AND id <> ?";
    linkArgs.push(excludePanelLinkId);
  }
  linkSql += " LIMIT 1";
  const link = await db.prepare(linkSql).bind(...linkArgs).first();

  return {
    duplicate: Boolean(item || job || link),
    item: item || null,
    job: job || null,
    link: link || null,
  };
}
