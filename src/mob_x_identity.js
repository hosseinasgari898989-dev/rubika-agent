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
    "SELECT j.id, j.status FROM console_jobs j " +
    "LEFT JOIN panel_links p ON p.id = j.panel_link_id " +
    "WHERE j.type = 'mob_x_link' " +
    "AND (p.external_id IN (?, ?) OR j.params_json LIKE ? OR j.result_json LIKE ?)";
  const jobArgs = [id, prefixed, "%" + id + "%", "%" + prefixed + "%"];
  if (excludeJobId) {
    jobSql += " AND j.id <> ?";
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
