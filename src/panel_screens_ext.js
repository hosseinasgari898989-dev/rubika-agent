// Thin wrapper so panel_screens.js stays import-free (its unit test loads it from a data: URL).
// owner_panel.js imports these three names instead of the originals from panel_screens.js.
import { isScreenId, buildScreen, HOME_ROWS } from "./panel_screens.js";
import { isRetryScreenId, buildRetryScreen, RETRY_HOME_ROW } from "./panel_retry_screens.js";

export const isScreenIdExt = (id) => id === "p_tiktok_review" || id === "p_pub_control" || isScreenId(id) || isRetryScreenId(id);
export const buildScreenExt = (env, id) => (isRetryScreenId(id) ? buildRetryScreen(env, id) : buildScreen(env, id));
// Review-only actions are rendered in owner_panel.js through the shared Worker API.
const TIKTOK_REVIEW_HOME_ROW = [["🎵 تیک‌تاک — در انتظار تأیید شما", "p_tiktok_review"]];
const SCAN_NOW_HOME_ROW = [["🔎 اسکن فوری — فقط گزارش", "p_scan"]];
const SCAN_PUBLISH_HOME_ROW = [["🚀 اسکن و انتشار", "p_scan_pub"]];
const PUBLISHING_CONTROL_HOME_ROW = [["🛑 توقف اضطراری انتشار", "p_pub_control"]];
const BOT_POWER_HOME_ROW = [["⏻ خاموش/روشن کردن ربات", "p_bot_power"]];
// Retry, review and read-only scan rows sit above help and main menu.
export const HOME_ROWS_EXT = [...HOME_ROWS.slice(0, -2), RETRY_HOME_ROW, TIKTOK_REVIEW_HOME_ROW, SCAN_NOW_HOME_ROW, SCAN_PUBLISH_HOME_ROW, PUBLISHING_CONTROL_HOME_ROW, BOT_POWER_HOME_ROW, ...HOME_ROWS.slice(-2)];
