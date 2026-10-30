// Thin wrapper so panel_screens.js stays import-free (its unit test loads it from a data: URL).
// owner_panel.js imports these three names instead of the originals from panel_screens.js.
import { isScreenId, buildScreen, HOME_ROWS } from "./panel_screens.js";
import { isRetryScreenId, buildRetryScreen, RETRY_HOME_ROW } from "./panel_retry_screens.js";

export const isScreenIdExt = (id) => isScreenId(id) || isRetryScreenId(id);
export const buildScreenExt = (env, id) => (isRetryScreenId(id) ? buildRetryScreen(env, id) : buildScreen(env, id));
// retry/select row goes just above the last two rows (help, main menu)
export const HOME_ROWS_EXT = [...HOME_ROWS.slice(0, -2), RETRY_HOME_ROW, ...HOME_ROWS.slice(-2)];
