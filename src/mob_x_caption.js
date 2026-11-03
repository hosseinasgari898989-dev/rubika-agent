export const MOB_X_NO_CAPTION_TEXT = "ماب انترتینمنت در پیج خود پستی منتشر کرد.";

export function buildMobXNoCaptionCaption() {
  return "**" + MOB_X_NO_CAPTION_TEXT + "**";
}

// Keep web and PV caption sanitation/limits identical.
export function normalizeMobXCaption(value) {
  return String(value || "")
    .replace(/[*_~]/g, "")
    .replaceAll(String.fromCharCode(96), "")
    .replace(/\u0000/g, "")
    .trim();
}

export function maxMobXCaptionChars(value) {
  const rawLimit = Number(value ?? 900);
  return Number.isFinite(rawLimit) ? Math.max(1, Math.min(4000, Math.trunc(rawLimit))) : 900;
}
