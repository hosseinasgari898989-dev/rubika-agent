const RESPONSE_HEADERS = {
  "content-type": "application/json; charset=utf-8",
  "access-control-allow-origin": "*",
  "access-control-allow-methods": "GET, OPTIONS",
  "access-control-allow-headers": "Content-Type, Authorization",
  "cache-control": "no-store",
  "x-content-type-options": "nosniff",
  "referrer-policy": "no-referrer"
};

const json = (data, status = 200) =>
  new Response(JSON.stringify(data, null, 2), {
    status,
    headers: RESPONSE_HEADERS
  });

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method === "OPTIONS") {
      return new Response(null, {
        status: 204,
        headers: RESPONSE_HEADERS
      });
    }

    // This bootstrap API is intentionally read-only. All mutation and
    // publication features stay disabled until separately implemented/tested.
    if (request.method !== "GET") {
      return json({ ok: false, error: "method_not_allowed" }, 405);
    }

    if (
      url.pathname === "/" ||
      url.pathname === "/health" ||
      url.pathname === "/api/health"
    ) {
      try {
        await env.DB.prepare("SELECT 1 AS ok").first();
        return json({
          ok: true,
          service: "rubika-agent",
          database: "connected",
          mode: "bootstrap",
          publishing: false,
          timestamp: new Date().toISOString()
        });
      } catch {
        return json({
          ok: false,
          service: "rubika-agent",
          database: "unavailable"
        }, 503);
      }
    }

    if (url.pathname === "/api/status") {
      try {
        const [agent, sources, items, runs] = await Promise.all([
          env.DB.prepare(
            "SELECT enabled, started_at, updated_at FROM agent_config WHERE id = 1"
          ).first(),
          env.DB.prepare("SELECT COUNT(*) AS count FROM sources").first(),
          env.DB.prepare("SELECT COUNT(*) AS count FROM items").first(),
          env.DB.prepare("SELECT COUNT(*) AS count FROM runs").first()
        ]);

        return json({
          ok: true,
          service: "rubika-agent",
          agent: agent || { enabled: 0, started_at: null, updated_at: null },
          counts: {
            sources: sources?.count ?? 0,
            items: items?.count ?? 0,
            runs: runs?.count ?? 0
          },
          capabilities: {
            database: true,
            health: true,
            status: true,
            discovery: false,
            downloading: false,
            publishing: false,
            admin_auth: false
          },
          publishing: false
        });
      } catch {
        return json({
          ok: false,
          error: "database_unavailable"
        }, 503);
      }
    }

    if (url.pathname === "/api/sources") {
      try {
        const result = await env.DB.prepare(
          "SELECT id, platform, source_key, display_name, enabled, created_at, last_checked_at, last_seen_published_at FROM sources ORDER BY platform, source_key"
        ).all();

        return json({
          ok: true,
          sources: result.results || [],
          publishing: false
        });
      } catch {
        return json({
          ok: false,
          error: "database_unavailable"
        }, 503);
      }
    }

    return json({ ok: false, error: "not_found" }, 404);
  }
};
