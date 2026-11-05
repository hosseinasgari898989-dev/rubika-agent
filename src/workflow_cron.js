const REPOSITORY = "hosseinasgari898989-dev/rubika-agent";
const API_ROOT = "https://api.github.com/repos/" + REPOSITORY;
const TICK_MS = 30 * 60 * 1000;

export const WORKFLOW_CRON_ALLOWLIST = Object.freeze([
  "termux-docker-discovery.yml",
  "daily-queue-processor.yml",
  "mob-x-news-production.yml",
  "mob-x-link-processor.yml",
]);

const DISPATCH_INPUTS = Object.freeze({
  "termux-docker-discovery.yml": null,
  "daily-queue-processor.yml": Object.freeze({ publish_four_tiktok_test: true }),
  "mob-x-news-production.yml": null,
  "mob-x-link-processor.yml": null,
});

let lastWorkflowCronResult = null;

export function workflowsDueForTick(value) {
  const tick = Number(value);
  if (!Number.isSafeInteger(tick) || tick < 1) return [];
  const due = [
    "termux-docker-discovery.yml",
  ];
  if (tick % 6 === 0) due.push("daily-queue-processor.yml");
  if (tick % 10 === 0) due.push("mob-x-news-production.yml");
  due.push("mob-x-link-processor.yml");
  return due;
}

export function getLastWorkflowCronResult() {
  return lastWorkflowCronResult == null
    ? null
    : JSON.parse(JSON.stringify(lastWorkflowCronResult));
}

function safeLog(logger, entry) {
  const safeEntry = {
    workflow: entry.workflow,
    result: entry.result,
    http_status: Number.isFinite(entry.http_status) ? entry.http_status : null,
  };
  try {
    logger(safeEntry);
  } catch {
    // Logging must not interrupt the scheduled workflow checks.
  }
}

export async function runWorkflowCron(controller, env, dispatchWorkflow, options = {}) {
  const scheduledTime = Number(controller && controller.scheduledTime);
  if (!Number.isFinite(scheduledTime) || scheduledTime < 0) {
    lastWorkflowCronResult = { tick: null, scheduled_at: null, enabled: false, entries: [] };
    return getLastWorkflowCronResult();
  }

  const tick = Math.floor(scheduledTime / TICK_MS) + 1;
  const scheduledAt = new Date(scheduledTime).toISOString();
  const due = workflowsDueForTick(tick);
  const enabled = env?.WORKFLOW_CRON_DISPATCH_ENABLED !== "false";
  const token = typeof env?.GITHUB_DISPATCH_TOKEN === "string"
    ? env.GITHUB_DISPATCH_TOKEN.trim()
    : "";
  const fetcher = typeof options.fetcher === "function" ? options.fetcher : globalThis.fetch;
  const logger = typeof options.logger === "function"
    ? options.logger
    : entry => console.log(JSON.stringify(entry));
  const entries = [];

  for (const workflow of due) {
    let result = "error";
    let httpStatus = null;

    if (!enabled) {
      result = "skipped";
    } else if (!token || typeof fetcher !== "function" || typeof dispatchWorkflow !== "function") {
      result = "error";
    } else {
      const runsUrl = API_ROOT + "/actions/workflows/" + workflow + "/runs?per_page=100";
      let response;
      try {
        response = await fetcher(runsUrl, {
          method: "GET",
          headers: {
            "User-Agent": "rubika-agent-worker-cron",
            Accept: "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            Authorization: "Bearer " + token,
          },
          signal: AbortSignal.timeout(5000),
        });
        httpStatus = Number(response && response.status) || null;
      } catch {
        response = null;
      }

      if (!response || !response.ok) {
        result = "error";
      } else {
        let payload = null;
        try {
          payload = await response.json();
        } catch {
          payload = null;
        }
        if (!payload || !Array.isArray(payload.workflow_runs)) {
          result = "error";
        } else if (payload.workflow_runs.some(run =>
          run && (run.status === "queued" || run.status === "in_progress")
        )) {
          result = "skipped";
        } else {
          try {
            const dispatched = await dispatchWorkflow(
              env,
              workflow,
              DISPATCH_INPUTS[workflow],
              { maxAttempts: 1, safeStatusOnly: true },
            );
            httpStatus = Number(dispatched && dispatched.status) || null;
            result = dispatched && dispatched.ok ? "dispatched" : "error";
          } catch {
            result = "error";
          }
        }
      }
    }

    const entry = { workflow, result, http_status: httpStatus };
    entries.push(entry);
    safeLog(logger, entry);
  }

  lastWorkflowCronResult = {
    tick,
    scheduled_at: scheduledAt,
    enabled,
    entries,
  };
  return getLastWorkflowCronResult();
}
