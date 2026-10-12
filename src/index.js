const json = (data, status = 200) =>
  new Response(JSON.stringify(data, null, 2), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      "x-content-type-options": "nosniff",
      "referrer-policy": "no-referrer"
    }
  });

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method !== "GET") {
      return json({ ok: false, error: "method_not_allowed" }, 405);
    }

    if (url.pathname === "/" || url.pathname === "/health" || url.pathname === "/api/health") {
      try {
        await env.DB.prepare("SELECT 1 AS ok").first();
        return json({
          ok: true,
          service: "rubika-agent-control",
          database: "connected",
          mode: "bootstrap",
          publishing: false,
          timestamp: new Date().toISOString()
        });
      } catch {
        return json({
          ok: false,
          service: "rubika-agent-control",
          database: "unavailable"
        }, 503);
      }
    }

    if (url.pathname === "/api/status") {
      try {
        const config = await env.DB
          .prepare("SELECT enabled, started_at, updated_at FROM agent_config WHERE id = 1")
          .first();

        const counts = await env.DB.prepare(
          "SELECT status, COUNT(*) AS total FROM items GROUP BY status"
        ).all();

        const recentRuns = await env.DB.prepare(
          "SELECT id, status, started_at, finished_at, found_count, sent_count, failed_count FROM runs ORDER BY started_at DESC LIMIT 5"
        ).all();

        return json({
          ok: true,
          service: "rubika-agent-control",
          agent: config || { enabled: 0, started_at: null, updated_at: null },
          item_counts: counts.results || [],
          recent_runs: recentRuns.results || [],
          publishing: false
        });
      } catch {
        return json({ ok: false, error: "database_not_initialized" }, 503);
      }
    }

    return json({ ok: false, error: "not_found" }, 404);
  }
};
