// Owner web console API (phase 1). Same Worker, same D1. Nothing here publishes, downloads or runs jobs.
// Auth (Bearer AGENT_API_TOKEN) is enforced by the caller in index.js BEFORE handleConsoleApi() is reached.
// Endpoints not implemented here fall through to 404 {ok:false,error:"not_found"}; the console shows them as "not active yet".

const CORS = {
  "access-control-allow-origin": "*",
  "access-control-allow-methods": "GET, POST, PATCH, DELETE, OPTIONS",
  "access-control-allow-headers": "authorization, content-type",
  "access-control-max-age": "86400",
};

export function corsPreflight() {
  return new Response(null, { status: 204, headers: CORS });
}

export function withCors(response) {
  const headers = new Headers(response.headers);
  for (const [k, v] of Object.entries(CORS)) headers.set(k, v);
  headers.set("cross-origin-resource-policy", "cross-origin");
  headers.set("access-control-expose-headers", "content-type");
  return new Response(response.body, { status: response.status, statusText: response.statusText, headers });
}

const CATEGORIES = new Set(["info", "success", "error", "ai_success", "ai_error"]);
const LEVELS = new Set(["debug", "info", "warning", "error"]);
const PLATFORMS = new Set(["tiktok", "youtube", "instagram", "mob_official"]);
const ITEM_STATUSES = new Set(["discovered", "filtered", "queued", "downloading", "ready", "sending", "sent", "failed", "uncertain"]);
const AI_PROVIDERS = new Set(["groq", "openrouter"]);
const AI_ROLES = new Set(["diagnose", "propose_patch", "review_patch", "translate_official"]);
const EDITABLE_REVIEW = new Set(["pending", "rejected", "needs_review"]); // "approved" is never set from the console
const LOCKED_ITEM_STATUSES = new Set(["sending", "sent"]);
const SOURCE_PLATFORMS = new Set(["tiktok", "youtube", "instagram", "x", "mob_official"]);
const X_LINK = /^https:\/\/(?:www\.)?x\.com\/mobgamesstudios\/status\/([0-9]{15,22})\/?$/;

export async function handleConsoleApi(request, env, h) {
  const { json, readJson, text, validIdentifier } = h;
  const url = new URL(request.url);
  const path = url.pathname.replace(/\/+$/, "");
  const m = request.method;
  const sub = path.slice("/api/console".length); // "" | "/events" | ...

  const page = (defLimit = 50, maxLimit = 200) => {
    const l = Number(url.searchParams.get("limit") || defLimit);
    const o = Number(url.searchParams.get("offset") || 0);
    return {
      limit: Number.isInteger(l) ? Math.min(maxLimit, Math.max(1, l)) : defLimit,
      offset: Number.isInteger(o) ? Math.min(1000000, Math.max(0, o)) : 0,
    };
  };
  const all = async (sql, ...b) => (await env.DB.prepare(sql).bind(...b).all()).results || [];
  const first = async (sql, ...b) => (await env.DB.prepare(sql).bind(...b).first()) || null;
  const fail = (e, status = 500) => json({ ok: false, error: e }, status);
  const audit = async (code, itemId, details) => {
    try {
      await env.DB.prepare("INSERT INTO brain_events (item_id, level, code, details_json) VALUES (?, 'info', ?, ?)")
        .bind(itemId || null, code, JSON.stringify(details || {}).slice(0, 900)).run();
    } catch { /* audit must never break the action */ }
  };
  const auditMutation = async (statements, code, details) => {
    const safeDetails = JSON.stringify(details || {}).slice(0, 900);
    const auditStatement = env.DB.prepare(
      "INSERT INTO brain_events (item_id, level, code, details_json) SELECT NULL, 'info', ?, ? WHERE changes() > 0"
    ).bind(code, safeDetails);
    return env.DB.batch([...statements, auditStatement]);
  };
  const isRecord = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
  const bounded = (value, min, max) => Number.isInteger(value) && value >= min && value <= max;
  const randomHex = (size = 16) => {
    const bytes = crypto.getRandomValues(new Uint8Array(size));
    return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  };
  const sha256Hex = async (value) => {
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
    return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
  };
  const like = (q) => "%" + q.replace(/[\\%_]/g, (c) => "\\" + c) + "%";

  try {
    // ---------- read-only history ----------
    if (m === "GET" && sub === "/events") {
      const { limit, offset } = page();
      const cat = text(url.searchParams.get("category"), 20);
      if (cat && !CATEGORIES.has(cat)) return fail("invalid_category", 400);
      const where = cat ? "WHERE category = ?" : "";
      const args = cat ? [cat] : [];
      const items = await all(`SELECT id, occurred_at, category, source, title, detail FROM owner_digest_events ${where} ORDER BY id DESC LIMIT ? OFFSET ?`, ...args, limit, offset);
      const total = Number((await first(`SELECT COUNT(*) AS c FROM owner_digest_events ${where}`, ...args))?.c || 0);
      return json({ ok: true, items, total, page: { limit, offset, returned: items.length } });
    }
    if (m === "GET" && sub === "/runs") {
      const { limit, offset } = page(30, 100);
      const items = await all("SELECT id, trigger_type, status, started_at, finished_at, found_count, eligible_count, sent_count, skipped_count, failed_count, summary FROM runs ORDER BY started_at DESC LIMIT ? OFFSET ?", limit, offset);
      return json({ ok: true, items, page: { limit, offset, returned: items.length } });
    }
    if (m === "GET" && sub === "/delivery-attempts") {
      const { limit, offset } = page(50, 200);
      const itemId = text(url.searchParams.get("item_id"), 160);
      if (itemId && !validIdentifier(itemId)) return fail("invalid_item_id", 400);
      const sql = "SELECT id, item_id, attempt_number, status, started_at, finished_at, rubika_message_id, error_code FROM delivery_attempts" + (itemId ? " WHERE item_id = ?" : "") + " ORDER BY id DESC LIMIT ? OFFSET ?";
      const items = await all(sql, ...(itemId ? [itemId] : []), limit, offset);
      return json({ ok: true, items });
    }
    if (m === "GET" && sub === "/brain-events") {
      const { limit, offset } = page();
      const level = text(url.searchParams.get("level"), 20);
      if (level && !LEVELS.has(level)) return fail("invalid_level", 400);
      const items = await all("SELECT id, run_id, item_id, level, code, details_json, created_at FROM brain_events" + (level ? " WHERE level = ?" : "") + " ORDER BY id DESC LIMIT ? OFFSET ?", ...(level ? [level] : []), limit, offset);
      return json({ ok: true, items });
    }
    if (m === "GET" && sub === "/audit") {
      const { limit } = page(100, 200);
      const items = await all("SELECT id, item_id, level, code, details_json, created_at FROM brain_events WHERE code LIKE 'console\\_%' ESCAPE '\\' ORDER BY id DESC LIMIT ?", limit);
      return json({ ok: true, items });
    }
    if (m === "GET" && sub === "/ai/calls") {
      const { limit, offset } = page(50, 200);
      const provider = text(url.searchParams.get("provider"), 20);
      const role = text(url.searchParams.get("role"), 30);
      if (provider && !AI_PROVIDERS.has(provider)) return fail("invalid_provider", 400);
      if (role && !AI_ROLES.has(role)) return fail("invalid_role", 400);
      const conds = [], args = [];
      if (provider) { conds.push("provider = ?"); args.push(provider); }
      if (role) { conds.push("role = ?"); args.push(role); }
      const items = await all("SELECT call_id, usage_day, provider, role, status, error_code, prompt_tokens, output_tokens, total_tokens, cooldown_until, started_at, finished_at FROM ai_provider_calls" + (conds.length ? " WHERE " + conds.join(" AND ") : "") + " ORDER BY started_at DESC LIMIT ? OFFSET ?", ...args, limit, offset);
      return json({ ok: true, items });
    }

    // ---------- items ----------
    if (m === "GET" && sub === "/items") {
      const { limit, offset } = page(50, 100);
      const status = text(url.searchParams.get("status"), 20);
      const platform = text(url.searchParams.get("platform"), 24);
      const q = text(url.searchParams.get("q"), 80);
      if (status && !ITEM_STATUSES.has(status)) return fail("invalid_status", 400);
      if (platform && !PLATFORMS.has(platform)) return fail("invalid_platform", 400);
      const conds = [], args = [];
      if (status) { conds.push("status = ?"); args.push(status); }
      if (platform) { conds.push("platform = ?"); args.push(platform); }
      if (q) { conds.push("(title LIKE ? ESCAPE '\\' OR canonical_url LIKE ? ESCAPE '\\' OR external_id LIKE ? ESCAPE '\\')"); args.push(like(q), like(q), like(q)); }
      const where = conds.length ? " WHERE " + conds.join(" AND ") : "";
      const items = await all("SELECT id, platform, source_key, external_id, canonical_url, title, caption, published_at, discovered_at, status, media_sha256, media_bytes, media_mime, rubika_message_id, attempt_count, last_error_code, last_error_at, sent_at, media_type, source_tier, storage_key, validation_json, moderation_json, review_state, original_caption, persian_translation FROM items" + where + " ORDER BY discovered_at DESC LIMIT ? OFFSET ?", ...args, limit, offset);
      return json({ ok: true, items, page: { limit, offset, returned: items.length } });
    }
    const itemMatch = /^\/items\/([^/]+)$/.exec(sub);
    if (m === "PATCH" && itemMatch) {
      const id = decodeURIComponent(itemMatch[1]);
      if (!validIdentifier(id)) return fail("invalid_item_id", 400);
      const parsed = await readJson(request);
      if (!parsed.ok) return fail(parsed.error, parsed.status);
      const b = parsed.value;
      const allowed = new Set(["caption", "title", "persian_translation", "review_state"]);
      if (Object.keys(b).some((k) => !allowed.has(k))) return fail("fields_not_editable", 400);
      const sets = [], args = [], changed = [];
      if ("caption" in b) { if (typeof b.caption !== "string") return fail("invalid_caption", 400); sets.push("caption = ?"); args.push(text(b.caption, 5000) || null); changed.push("caption"); }
      if ("title" in b) { if (typeof b.title !== "string") return fail("invalid_title", 400); sets.push("title = ?"); args.push(text(b.title, 500) || null); changed.push("title"); }
      if ("persian_translation" in b) { if (typeof b.persian_translation !== "string") return fail("invalid_translation", 400); sets.push("persian_translation = ?"); args.push(text(b.persian_translation, 5000) || null); changed.push("persian_translation"); }
      if ("review_state" in b) { if (!EDITABLE_REVIEW.has(b.review_state)) return fail("review_state_not_allowed_from_console", 400); sets.push("review_state = ?"); args.push(b.review_state); changed.push("review_state"); }
      if (!sets.length) return fail("nothing_to_update", 400);
      const item = await first("SELECT id, status FROM items WHERE id = ?", id);
      if (!item) return fail("item_not_found", 404);
      if (LOCKED_ITEM_STATUSES.has(item.status)) return fail("item_locked", 409);
      await env.DB.prepare(`UPDATE items SET ${sets.join(", ")} WHERE id = ? AND status NOT IN ('sending','sent')`).bind(...args, id).run();
      await audit("console_item_edit", id, { fields: changed });
      const row = await first("SELECT id, title, caption, persian_translation, review_state, status FROM items WHERE id = ?", id);
      return json({ ok: true, item: row });
    }

    // ---------- queue actions (DB state only; nothing is sent) ----------
    const qa = /^\/queue\/([^/]+)\/action$/.exec(sub);
    if (m === "POST" && qa) {
      const id = decodeURIComponent(qa[1]);
      if (!validIdentifier(id)) return fail("invalid_queue_id", 400);
      const parsed = await readJson(request);
      if (!parsed.ok) return fail(parsed.error, parsed.status);
      const action = text(parsed.value.action, 20);
      const row = await first("SELECT id, status FROM automation_queue WHERE id = ?", id);
      if (!row) return fail("queue_item_not_found", 404);
      let res;
      if (action === "requeue") {
        if (row.status === "uncertain" && parsed.value.confirm_uncertain !== true) return fail("uncertain_requires_confirm_uncertain_true", 409);
        res = await env.DB.prepare("UPDATE automation_queue SET status='queued', last_error_code=NULL, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=? AND status IN ('failed','uncertain','skipped')").bind(id).run();
      } else if (action === "skip") {
        res = await env.DB.prepare("UPDATE automation_queue SET status='skipped', updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=? AND status IN ('queued','failed','uncertain')").bind(id).run();
      } else if (action === "delete") {
        res = await env.DB.prepare("DELETE FROM automation_queue WHERE id=? AND status IN ('queued','failed','skipped')").bind(id).run();
      } else if (action === "retry_now") {
        return fail("retry_now_not_supported_use_requeue", 501);
      } else {
        return fail("invalid_action", 400);
      }
      if (!res?.meta?.changes) return fail("invalid_state_for_action", 409);
      await audit("console_queue_" + action, null, { id, from: row.status });
      return json({ ok: true, id, action });
    }

    // ---------- D1 explorer ----------
    if (m === "GET" && sub === "/d1/tables") {
      const names = (await all("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\' AND name NOT LIKE '\\_cf\\_%' ESCAPE '\\' AND name NOT LIKE 'd1\\_%' ESCAPE '\\' ORDER BY name")).map((r) => String(r.name)).filter((n) => /^[A-Za-z0-9_]{1,64}$/.test(n));
      const stmts = names.flatMap((n) => [env.DB.prepare(`PRAGMA table_info("${n}")`), env.DB.prepare(`SELECT COUNT(*) AS c FROM "${n}"`)]);
      const res = await env.DB.batch(stmts);
      const items = names.map((n, i) => ({
        name: n,
        row_count: Number(res[i * 2 + 1]?.results?.[0]?.c || 0),
        columns: (res[i * 2]?.results || []).map((c) => ({ name: c.name, type: c.type, nullable: Number(c.notnull) === 0 })),
      }));
      return json({ ok: true, items });
    }
    if (m === "POST" && sub === "/d1/query") {
      const parsed = await readJson(request);
      if (!parsed.ok) return fail(parsed.error, parsed.status);
      let sql = typeof parsed.value.sql === "string" ? parsed.value.sql.trim().replace(/;+\s*$/, "") : "";
      if (!sql || sql.length > 2000) return fail("sql_invalid_length", 400);
      const stripped = sql.replace(/'(?:[^']|'')*'/g, "''").replace(/"(?:[^"]|"")*"/g, '""');
      if (!/^select\b/i.test(stripped) || /;|--|\/\*/.test(stripped) ||
          /\b(insert|update|delete|drop|alter|create|attach|detach|pragma|vacuum|reindex|begin|commit|rollback|savepoint|load_extension)\b/i.test(stripped)) {
        return fail("only_single_select_allowed", 400);
      }
      const started = Date.now();
      const raw = await env.DB.prepare(`SELECT * FROM (${sql}) LIMIT 200`).raw({ columnNames: true });
      const columns = raw[0] || [];
      const rows = raw.slice(1).map((r) => r.map((v) => (typeof v === "string" && v.length > 1000 ? v.slice(0, 1000) + "…" : v)));
      return json({ ok: true, columns, rows, duration_ms: Date.now() - started });
    }

    // ---------- owners and one-time invitations ----------
    if (sub === "/owners" && m === "GET") {
      const items = await all("SELECT id, chat_id, label, role, enabled, claimed_at, last_seen_at, created_at, updated_at FROM console_owners ORDER BY id");
      return json({ ok: true, items });
    }
    if (sub === "/owners" && m === "POST") {
      const parsed = await readJson(request);
      if (!parsed.ok) return fail(parsed.error, parsed.status);
      const b = parsed.value;
      if (b.id !== undefined) {
        const id = Number(b.id);
        if (!Number.isInteger(id) || id < 1) return fail("invalid_owner_id", 400);
        if (Object.keys(b).some((k) => !["id", "enabled", "role", "label"].includes(k))) return fail("fields_not_editable", 400);
        const current = await first("SELECT id, chat_id, label, role, enabled FROM console_owners WHERE id = ?", id);
        if (!current) return fail("owner_not_found", 404);
        const sets = [], args = [], changed = [];
        if ("enabled" in b) {
          if (![0, 1, true, false].includes(b.enabled)) return fail("invalid_enabled", 400);
          const next = b.enabled === true || b.enabled === 1 ? 1 : 0;
          if (current.role === "owner" && Number(current.enabled) === 1 && next === 0) {
            const count = Number((await first("SELECT COUNT(*) AS total FROM console_owners WHERE role = 'owner' AND enabled = 1"))?.total || 0);
            if (count <= 1) return fail("last_enabled_owner", 409);
          }
          sets.push("enabled = ?");
          args.push(next);
          changed.push("enabled");
        }
        if ("role" in b) {
          if (!["owner", "viewer"].includes(b.role)) return fail("invalid_owner_role", 400);
          if (current.role === "owner" && current.enabled && b.role !== "owner") {
            const count = Number((await first("SELECT COUNT(*) AS total FROM console_owners WHERE role = 'owner' AND enabled = 1"))?.total || 0);
            if (count <= 1) return fail("last_enabled_owner", 409);
          }
          sets.push("role = ?");
          args.push(b.role);
          changed.push("role");
        }
        if ("label" in b) {
          const label = text(b.label, 120);
          if (!label) return fail("invalid_owner_label", 400);
          sets.push("label = ?");
          args.push(label);
          changed.push("label");
        }
        if (!sets.length) return fail("nothing_to_update", 400);
        sets.push("updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')");
        await auditMutation([
          env.DB.prepare("UPDATE console_owners SET " + sets.join(", ") + " WHERE id = ?").bind(...args, id),
        ], "console_owner_update", { id, fields: changed });
        const item = await first("SELECT id, chat_id, label, role, enabled, claimed_at, last_seen_at, created_at, updated_at FROM console_owners WHERE id = ?", id);
        return json({ ok: true, item });
      }

      if (Object.keys(b).some((k) => !["chat_id", "label", "role", "enabled"].includes(k))) return fail("fields_not_editable", 400);
      const chatId = text(b.chat_id, 128);
      if (!chatId || !/^[A-Za-z0-9._:@-]{1,128}$/.test(chatId)) return fail("invalid_chat_id", 400);
      const label = text(b.label, 120) || ("مالک " + chatId.slice(-4));
      const role = b.role === undefined ? "viewer" : b.role;
      if (!["owner", "viewer"].includes(role)) return fail("invalid_owner_role", 400);
      let enabled = b.enabled === undefined ? 1 : b.enabled;
      if (![0, 1, true, false].includes(enabled)) return fail("invalid_enabled", 400);
      enabled = enabled === true || enabled === 1 ? 1 : 0;
      if (await first("SELECT id FROM console_owners WHERE chat_id = ?", chatId)) return fail("owner_already_exists", 409);
      await auditMutation([
        env.DB.prepare("INSERT INTO console_owners (chat_id, label, role, enabled, claimed_at, last_seen_at) VALUES (?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), NULL)")
          .bind(chatId, label, role, enabled),
      ], "console_owner_create", { role, enabled, label });
      const item = await first("SELECT id, chat_id, label, role, enabled, claimed_at, last_seen_at, created_at, updated_at FROM console_owners WHERE chat_id = ?", chatId);
      return json({ ok: true, item }, 201);
    }
    const ownerMatch = /^\/owners\/(\d+)$/.exec(sub);
    if (ownerMatch && m === "DELETE") {
      const id = Number(ownerMatch[1]);
      const current = await first("SELECT id, role, enabled FROM console_owners WHERE id = ?", id);
      if (!current) return fail("owner_not_found", 404);
      if (current.role === "owner" && Number(current.enabled) === 1) {
        const count = Number((await first("SELECT COUNT(*) AS total FROM console_owners WHERE role = 'owner' AND enabled = 1"))?.total || 0);
        if (count <= 1) return fail("last_enabled_owner", 409);
      }
      await auditMutation([
        env.DB.prepare("DELETE FROM console_owners WHERE id = ?").bind(id),
      ], "console_owner_delete", { id, role: current.role });
      return json({ ok: true, id });
    }
    if (sub === "/owners/invite" && m === "POST") {
      const parsed = await readJson(request);
      if (!parsed.ok) return fail(parsed.error, parsed.status);
      const inviteCode = "INV-" + randomHex(16).toUpperCase();
      const codeHash = await sha256Hex(inviteCode);
      const expiresAt = new Date(Date.now() + 60 * 60 * 1000).toISOString();
      await auditMutation([
        env.DB.prepare("INSERT INTO console_owner_invites (code_hash, expires_at) VALUES (?, ?)").bind(codeHash, expiresAt),
      ], "console_owner_invite", { expires_at: expiresAt, lifetime_minutes: 60 });
      return json({ ok: true, invite_code: inviteCode, expires_at: expiresAt }, 201);
    }

    // ---------- versioned, optimistic-lock rules ----------
    if (sub === "/rules" && m === "GET") {
      const row = await first("SELECT version, rules_json, updated_at FROM console_rules WHERE id = 1");
      if (!row) return fail("rules_not_initialized", 503);
      let rules;
      try { rules = JSON.parse(row.rules_json); } catch { return fail("rules_invalid_json", 503); }
      rules.publishing_enabled = false;
      rules.test_channel_only = true;
      return json({ ok: true, version: Number(row.version), rules, updated_at: row.updated_at });
    }
    if (sub === "/rules" && m === "PUT") {
      const parsed = await readJson(request);
      if (!parsed.ok) return fail(parsed.error, parsed.status);
      const b = parsed.value;
      if (!bounded(b.base_version, 1, 1000000000)) return fail("invalid_base_version", 400);
      if (!isRecord(b.rules)) return fail("rules_object_required", 400);
      const current = await first("SELECT version, rules_json, updated_at FROM console_rules WHERE id = 1");
      if (!current) return fail("rules_not_initialized", 503);
      let currentRules;
      try { currentRules = JSON.parse(current.rules_json); } catch { return fail("rules_invalid_json", 503); }
      const server = { version: Number(current.version), rules: currentRules, updated_at: current.updated_at };
      if (Number(current.version) !== b.base_version) return json({ ok: false, error: "version_conflict", server }, 409);

      const rules = { ...b.rules };
      rules.publishing_enabled = false;
      rules.test_channel_only = true;
      const integerRules = {
        daily_success_cap: [0, 50], daily_edit_count: [0, 50], mob_posts_per_run: [0, 10],
        freshness_hours: [1, 168], ai_daily_limit_groq: [0, 5000],
        ai_daily_limit_openrouter: [0, 5000], caption_max_chars: [50, 4000],
      };
      for (const [key, range] of Object.entries(integerRules)) {
        if (key in rules && !bounded(rules[key], range[0], range[1])) return fail("invalid_rule_" + key, 400);
      }
      for (const key of ["translate_enabled"]) {
        if (key in rules && typeof rules[key] !== "boolean") return fail("invalid_rule_" + key, 400);
      }
      if ("ai_provider_order" in rules &&
          (!Array.isArray(rules.ai_provider_order) || rules.ai_provider_order.length < 1 || rules.ai_provider_order.length > 2 ||
           rules.ai_provider_order.some((provider) => !AI_PROVIDERS.has(provider)) ||
           new Set(rules.ai_provider_order).size !== rules.ai_provider_order.length)) {
        return fail("invalid_rule_ai_provider_order", 400);
      }
      if ("quiet_hours" in rules &&
          (!isRecord(rules.quiet_hours) ||
           !/^([01]\d|2[0-3]):[0-5]\d$/.test(String(rules.quiet_hours.start || "")) ||
           !/^([01]\d|2[0-3]):[0-5]\d$/.test(String(rules.quiet_hours.end || "")))) {
        return fail("invalid_rule_quiet_hours", 400);
      }
      if ("digest_window_utc" in rules && !/^([01]\d|2[0-3]):[0-5]\d$/.test(String(rules.digest_window_utc))) {
        return fail("invalid_rule_digest_window_utc", 400);
      }
      if ("mob_since_cutoff" in rules &&
          (typeof rules.mob_since_cutoff !== "string" || rules.mob_since_cutoff.length > 80 ||
           (rules.mob_since_cutoff !== "" && Number.isNaN(Date.parse(rules.mob_since_cutoff))))) {
        return fail("invalid_rule_mob_since_cutoff", 400);
      }
      if ("ai_provider_order" in rules) rules.ai_provider_order = rules.ai_provider_order.slice();
      let rulesJson;
      try {
        rulesJson = JSON.stringify(rules);
        if (new TextEncoder().encode(rulesJson).byteLength > 12000) return fail("rules_too_large", 400);
      } catch { return fail("rules_not_serializable", 400); }
      const now = new Date().toISOString();
      const result = await auditMutation([
        env.DB.prepare("UPDATE console_rules SET version = version + 1, rules_json = ?, updated_at = ? WHERE id = 1 AND version = ?")
          .bind(rulesJson, now, b.base_version),
      ], "console_rules_update", { from_version: b.base_version, fields: Object.keys(rules).slice(0, 40) });
      if (!result[0]?.meta?.changes) {
        const latest = await first("SELECT version, rules_json, updated_at FROM console_rules WHERE id = 1");
        let latestRules = {};
        try { latestRules = JSON.parse(latest?.rules_json || "{}"); } catch {}
        return json({ ok: false, error: "version_conflict", server: { version: Number(latest?.version || 0), rules: latestRules, updated_at: latest?.updated_at || null } }, 409);
      }
      return json({ ok: true, version: b.base_version + 1, rules, updated_at: now });
    }

    // ---------- destination channels (configuration only; this Worker never publishes) ----------
    if (sub === "/channels" && m === "GET") {
      const items = await all("SELECT id, label, rubika_channel_id, role, enabled, created_at, updated_at FROM console_channels ORDER BY id");
      return json({ ok: true, items });
    }
    if (sub === "/channels" && m === "POST") {
      const parsed = await readJson(request);
      if (!parsed.ok) return fail(parsed.error, parsed.status);
      const b = parsed.value;
      const rubikaChannelId = text(b.rubika_channel_id, 40);
      if (!/^@?[A-Za-z0-9_]{3,40}$/.test(rubikaChannelId)) return fail("invalid_channel_id", 400);
      const role = b.role === undefined ? "test" : b.role;
      if (!["test", "production"].includes(role)) return fail("invalid_channel_role", 400);
      const label = text(b.label, 120) || rubikaChannelId;
      if (await first("SELECT id FROM console_channels WHERE rubika_channel_id = ?", rubikaChannelId)) return fail("channel_already_exists", 409);
      let enabled = b.enabled === undefined ? (role === "test" ? 1 : 0) : b.enabled;
      if (![0, 1, true, false].includes(enabled)) return fail("invalid_enabled", 400);
      enabled = enabled === true || enabled === 1 ? 1 : 0;
      if (role === "production") enabled = 0; // production destinations are metadata only and never enabled here
      await auditMutation([
        env.DB.prepare("INSERT INTO console_channels (label, rubika_channel_id, role, enabled) VALUES (?, ?, ?, ?)")
          .bind(label, rubikaChannelId, role, enabled),
      ], "console_channel_create", { role, enabled, label });
      const item = await first("SELECT id, label, rubika_channel_id, role, enabled, created_at, updated_at FROM console_channels WHERE rubika_channel_id = ?", rubikaChannelId);
      return json({ ok: true, item }, 201);
    }
    const channelMatch = /^\/channels\/(\d+)$/.exec(sub);
    if (channelMatch && m === "DELETE") {
      const id = Number(channelMatch[1]);
      const current = await first("SELECT id, label, role FROM console_channels WHERE id = ?", id);
      if (!current) return fail("channel_not_found", 404);
      await auditMutation([
        env.DB.prepare("DELETE FROM console_channels WHERE id = ?").bind(id),
      ], "console_channel_delete", { id, role: current.role, label: current.label });
      return json({ ok: true, id });
    }

    // ---------- persisted job records only; this route never starts a workflow or publishes ----------
    const jobTypes = new Set(["queue_intake_dry_run", "ai_health_check", "log_zip", "d1_backup", "mob_x_link", "requeue_stuck"]);
    if (sub === "/jobs" && m === "GET") {
      const rawLimit = Number(url.searchParams.get("limit") || "20");
      const limit = Number.isInteger(rawLimit) ? Math.min(100, Math.max(1, rawLimit)) : 20;
      const items = await all("SELECT id, type, status, progress, result_json, error, created_at, updated_at FROM console_jobs ORDER BY created_at DESC LIMIT ?", limit);
      return json({ ok: true, items: items.map((row) => ({
        id: row.id, type: row.type, status: row.status, progress: Number(row.progress || 0),
        result: row.result_json ? (() => { try { return JSON.parse(row.result_json); } catch { return null; } })() : null,
        error: row.error || null, created_at: row.created_at, updated_at: row.updated_at,
      })) });
    }
    if (sub === "/jobs" && m === "POST") {
      const parsed = await readJson(request);
      if (!parsed.ok) return fail(parsed.error, parsed.status);
      const b = parsed.value;
      if (!jobTypes.has(b.type)) return fail("invalid_job_type", 400);
      const params = b.params === undefined ? {} : b.params;
      if (!isRecord(params)) return fail("job_params_object_required", 400);
      let paramsJson;
      try {
        paramsJson = JSON.stringify(params);
        if (new TextEncoder().encode(paramsJson).byteLength > 12000) return fail("job_params_too_large", 400);
      } catch { return fail("job_params_not_serializable", 400); }
      const id = "job_" + randomHex(12);
      await auditMutation([
        env.DB.prepare("INSERT INTO console_jobs (id, type, status, progress, params_json, result_json, error) VALUES (?, ?, 'queued', 0, ?, NULL, NULL)")
          .bind(id, b.type, paramsJson),
      ], "console_job_create", { id, type: b.type });
      return json({ ok: true, job_id: id }, 201);
    }
    const jobMatch = /^\/jobs\/([A-Za-z0-9_-]{1,100})$/.exec(sub);
    if (jobMatch && m === "GET") {
      const row = await first("SELECT id, type, status, progress, result_json, error, created_at, updated_at FROM console_jobs WHERE id = ?", jobMatch[1]);
      if (!row) return fail("job_not_found", 404);
      let result = null;
      try { result = row.result_json ? JSON.parse(row.result_json) : null; } catch {}
      return json({ ok: true, job: {
        id: row.id, type: row.type, status: row.status, progress: Number(row.progress || 0),
        result, error: row.error || null, created_at: row.created_at, updated_at: row.updated_at,
      } });
    }

    // ---------- X links saved by the owner (panel_links) ----------
    if (sub === "/links" && m === "GET") {
      const items = await all("SELECT id, url, status, created_at FROM panel_links ORDER BY id DESC LIMIT 100");
      return json({ ok: true, items });
    }
    if (sub === "/links" && m === "POST") {
      const parsed = await readJson(request);
      if (!parsed.ok) return fail(parsed.error, parsed.status);
      const mm = X_LINK.exec(text(parsed.value.url, 300));
      if (!mm) return fail("only_official_mob_x_status_links_allowed", 400);
      const canonical = "https://x.com/mobgamesstudios/status/" + mm[1];
      if (await first("SELECT id FROM panel_links WHERE url = ?", canonical)) return fail("link_already_saved", 409);
      await env.DB.prepare("INSERT INTO panel_links (url, status) VALUES (?, 'pending')").bind(canonical).run();
      await audit("console_link_add", null, { tail: mm[1].slice(-6) });
      return json({ ok: true, url: canonical }, 201);
    }
    if (sub === "/links/clear" && m === "POST") {
      const parsed = await readJson(request);
      if (!parsed.ok) return fail(parsed.error, parsed.status);
      if (parsed.value.confirm !== true) return fail("confirm_true_required", 400);
      const r = await env.DB.prepare("DELETE FROM panel_links").run();
      await audit("console_links_clear", null, { removed: Number(r?.meta?.changes || 0) });
      return json({ ok: true, removed: Number(r?.meta?.changes || 0) });
    }

    // ---------- sources (X / TikTok / YouTube ...) ----------
    if (sub === "/sources" && m === "GET") {
      const items = await all("SELECT id, platform, source_key, display_name, enabled, created_at, last_checked_at, last_seen_published_at FROM sources ORDER BY platform, source_key LIMIT 500");
      return json({ ok: true, items });
    }
    if (sub === "/sources" && m === "POST") {
      const parsed = await readJson(request);
      if (!parsed.ok) return fail(parsed.error, parsed.status);
      const b = parsed.value;
      const platform = text(b.platform, 24);
      const key = text(b.source_key, 200);
      if (!SOURCE_PLATFORMS.has(platform)) return fail("invalid_platform", 400);
      if (!/^[A-Za-z0-9._@:/-]{1,200}$/.test(key)) return fail("invalid_source_key", 400);
      const id = platform + ":" + key.replace(/[^A-Za-z0-9._-]/g, "_").slice(0, 120);
      const exists = await first("SELECT id FROM sources WHERE platform = ? AND source_key = ?", platform, key);
      if (exists) return fail("source_already_exists", 409);
      await env.DB.prepare("INSERT INTO sources (id, platform, source_key, display_name, enabled) VALUES (?, ?, ?, ?, 1)").bind(id, platform, key, text(b.display_name, 120) || null).run();
      await audit("console_source_add", null, { id });
      return json({ ok: true, id }, 201);
    }
    const src = /^\/sources\/([^/]+)$/.exec(sub);
    if (src && (m === "PATCH" || m === "DELETE")) {
      const id = decodeURIComponent(src[1]);
      if (!/^[A-Za-z0-9._:@-]{1,200}$/.test(id)) return fail("invalid_source_id", 400);
      if (m === "DELETE") {
        const r = await env.DB.prepare("DELETE FROM sources WHERE id = ?").bind(id).run();
        if (!r?.meta?.changes) return fail("source_not_found", 404);
        await audit("console_source_delete", null, { id });
        return json({ ok: true, id });
      }
      const parsed = await readJson(request);
      if (!parsed.ok) return fail(parsed.error, parsed.status);
      const b = parsed.value;
      if (Object.keys(b).some((k) => !["enabled", "display_name"].includes(k))) return fail("fields_not_editable", 400);
      const sets = [], args = [];
      if ("enabled" in b) { if (typeof b.enabled !== "boolean") return fail("invalid_enabled", 400); sets.push("enabled = ?"); args.push(b.enabled ? 1 : 0); }
      if ("display_name" in b) { sets.push("display_name = ?"); args.push(text(b.display_name, 120) || null); }
      if (!sets.length) return fail("nothing_to_update", 400);
      const r = await env.DB.prepare(`UPDATE sources SET ${sets.join(", ")} WHERE id = ?`).bind(...args, id).run();
      if (!r?.meta?.changes) return fail("source_not_found", 404);
      await audit("console_source_edit", null, { id, fields: Object.keys(b) });
      return json({ ok: true, id });
    }
  } catch {
    return fail("console_db_error", 503);
  }
  return fail("not_found", 404);
}
