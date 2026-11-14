const MOB_X_HOSTS = new Set([
  "x.com", "www.x.com", "mobile.x.com",
  "twitter.com", "www.twitter.com", "mobile.twitter.com",
]);

// Parse only the official account's status URL; tracking query/hash are discarded.
export function normalizeMobXLink(value) {
  if (typeof value !== "string") return null;
  const raw = value.trim();
  if (!raw || raw.length > 300 || /[\u0000-\u0020\u007f]/.test(raw)) return null;

  let parsed;
  try { parsed = new URL(raw); } catch { return null; }
  const authority = /^https:\/\/([^/?#]*)/i.exec(raw)?.[1] || "";
  const host = String(parsed.hostname || "").toLowerCase();
  const rawPath = raw.slice("https://".length + authority.length).split(/[?#]/, 1)[0] || "/";
  if (
    parsed.protocol !== "https:" ||
    !MOB_X_HOSTS.has(host) ||
    parsed.username || parsed.password || parsed.port ||
    authority.toLowerCase() !== host ||
    rawPath !== parsed.pathname
  ) return null;

  const match = /^\/mobgamesstudios\/status\/([0-9]{15,22})\/?$/i.exec(parsed.pathname);
  if (!match) return null;
  const externalId = match[1];
  return {
    externalId,
    canonicalUrl: "https://x.com/mobgamesstudios/status/" + externalId,
  };
}
