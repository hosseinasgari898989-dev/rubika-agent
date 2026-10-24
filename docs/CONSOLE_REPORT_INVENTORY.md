# موجودی اعلان‌های مالک، گزارش‌ها، Worker API و D1

این سند از کد موجود در main با مبنای afc320616bdc03c718f081479a708d181d39b19c و فایل‌های migrations/ تهیه شده است. این سند فقط موجودی کد را توضیح می‌دهد؛ وجود یک جدول در migration به‌تنهایی ثابت نمی‌کند که همان migration روی D1 واقعی اعمال شده است.

## 1. پیام‌هایی که به PV مالک می‌رسند

### 1.1 مدل مشترک اعلان‌های owner_notify

تابع send_owner_notification معمولاً برای هر رخداد یک پیام مستقل نمی‌سازد: رخداد پاک‌سازی‌شده را با POST به Worker ثبت می‌کند، سپس refresh_daily_digest را صدا می‌زند. Worker رخداد را در owner_digest_events ذخیره می‌کند و وضعیت پنجره را از owner_digest_state می‌خواند. owner_notify یک snapshot از Worker می‌گیرد و پیام واحد پنجرهٔ ۲۴ ساعته را ایجاد یا ویرایش می‌کند. اگر پیام قبلی موجود باشد، همان پیام ویرایش می‌شود؛ هر ۲۴ ساعت پنجرهٔ جدیدی آغاز می‌شود.

پیام digest شامل زمان شروع/پایان پنجره، شمار موفقیت‌ها، خطاها و اطلاعیه‌ها، و فهرست محدودشدهٔ رخدادها با زمان، source، title و detail است. دسته‌های قابل ثبت عبارت‌اند از info، success، error، ai_success و ai_error. جزئیات حساس و URLها پیش از ذخیره/نمایش پاک‌سازی می‌شوند.

| رویداد/منبع | زمان اجرا و workflow/cron | متن قالب یا خلاصه | داده‌ای که می‌خواند یا ثبت می‌کند |
|---|---|---|---|
| daily_digest_start | owner-daily-digest.yml، هر روز ساعت 07:05 UTC؛ cron: 5 7 * * * | «📌 بازهٔ تازهٔ گزارش روزانه آغاز شد…»؛ یک رخداد info آغاز پنجره ثبت می‌کند. | ابتدا event جدید را به Worker می‌فرستد؛ سپس snapshot پنجره، شمار دسته‌ها و رخدادهای ذخیره‌شده را از Worker/D1 می‌خواند تا digest بسازد/ویرایش کند. |
| digest_refresh | owner-digest-refresh.yml، دقیقه‌های 07، 22، 37 و 52 هر ساعت UTC؛ cron: 7,22,37,52 * * * * | متن ثابت تازه‌ای ندارد؛ فقط اگر متن digest تغییر کرده باشد پیام موجود را ویرایش می‌کند. رخداد جدیدی اضافه نمی‌کند. | از POST /api/brain/owner-digest/claim وضعیت پنجره و رخدادهای owner_digest_events را می‌گیرد؛ در صورت نیاز با /commit شناسه و متن پیام را ثبت می‌کند. این گردش کار برای نمایش رخدادهای جدید Worker، از جمله رویدادهای AI، است. |
| workflow_failed | در شکست daily-queue-processor.yml؛ در شکست workflow دستی queue-intake.yml (از جمله شکست dispatch پردازش روزانه)؛ و در شکست mob-x-news-production.yml. این اعلان‌ها در stepهای failure هستند؛ برای cronهای مربوط به ردیف‌های دیگر زمان مستقل ندارند. | «🚨 هشدار عامل روبیکا»، نام workflow، کد خطا و جملهٔ «جزئیات محرمانه در این اعلان درج نشده است.» | workflow و reason به‌صورت متنی sanitize می‌شوند و به عنوان event دستهٔ error در Worker/D1 ثبت می‌شوند؛ متن digest بعداً از snapshot D1 دوباره ساخته می‌شود. اعلان خطا به‌خودی‌خود گزارش صف را از Worker نمی‌خواند. |
| daily-queue start message (custom --message) | daily-queue-processor.yml، cron: 0 7 * * *؛ همچنین workflow_dispatch فقط با تأیید صریح input و محدودیت کانال آزمایشی. بعد از بررسی اعتبارنامه‌ها اجرا می‌شود. | «🚀 شروع پردازش صف ادیت‌ها؛ سقف این اجرا ۴ انتشار موفق است.» | پیام از خود workflow می‌آید؛ دستور --message قالب پیش‌فرض CLI را جایگزین می‌کند. چون --event تعیین نشده، event پیش‌فرض CLI برابر test است و category ثبت‌شده info خواهد بود. متن پیام دادهٔ D1 نمی‌خواند؛ workflow نام را به عنوان source ثبت می‌کند. |
| queue intake success/failure (send_owner_notification مستقیم) | queue-intake.yml فقط با workflow_dispatch؛ مرحلهٔ intake بدون --no-notify پیام می‌فرستد. اجرای زمان‌بندی‌شدهٔ termux-docker-discovery.yml در cron: 7,37 * * * *، چرخهٔ جمع‌آوری را پس از گیت پنج‌ساعته انجام می‌دهد ولی queue_intake را با --no-notify اجرا می‌کند؛ بنابراین آن مسیر زمان‌بندی‌شده پیام intake نمی‌فرستد. | موفقیت: «📥 صف ویدئوهای روبیکا به‌روزرسانی شد»، تعداد موارد جدید، تکراری‌ها، نامعتبرها و inventory صف. شکست: «🚨 افزودن اطلاعات به صف ناموفق بود» و کد خطا. | از نتیجهٔ اجرای queue_intake و شمارش صف برمی‌آید؛ درج صف از Worker انجام می‌شود. متن inventory از پاسخ summary مربوط به automation_queue می‌آید. دسته از روی متن تعیین می‌شود: پیام موفقیت با 📥 معمولاً info و شکست با 🚨 خطا است. |
| queue worker per-item success (send_owner_notification مستقیم) | daily-queue-processor.yml هنگام هر انتشار موفقِ تأییدشده؛ حداکثر چهار موفقیت طبق تنظیم فعلی همان workflow. | «✅ انتشار موفق ویدئوی ادیت»، platform، عنوان، سهمیهٔ موفق امروز و inventory صف. | نتیجهٔ همان آیتم و سهمیهٔ تجمعی اجرای worker؛ queue summary از Worker/D1؛ inventory جانبی از MEGA. اگر جمع‌آوری summary/MEGA شکست بخورد، متن جایگزین موجودی در دسترس نبود را نشان می‌دهد. |
| queue worker final summary (send_owner_notification مستقیم) | پایان اجرای queue_worker در daily-queue-processor.yml، پس از پردازش صف؛ در cron روزانه یا dispatch مجاز. | «📊 گزارش پایان نوبت خودکار روبیکا»، تعداد موفق این اجرا و امروز، تلاش‌ها، خطاها، موارد ردشده، status، inventory صف و حداکثر چهار کد خطای منتخب. | نتیجهٔ نهایی در حافظه/فایل گزارش queue_worker، queue_counts و daily counts برگرفته از Worker/D1، و در صورت وجود آخرین خطاهای امن. این پیام گزارش تجمیعی است و با پیام‌های موفقیت هر آیتم فرق دارد. |
| Mob automation start (custom --message) | mob-x-news-production.yml، cron: 7 */5 * * *؛ همچنین workflow_dispatch. | «📰 شروع بررسی خبرهای رسمی Mob در پنجرهٔ پنج‌ساعته.» | متن مستقیم workflow است؛ --message قالب CLI را جایگزین می‌کند. event پیش‌فرض test است اما category ثبت‌شده info می‌شود و source نام workflow است. |
| Mob official publish success/failure (send_owner_notification مستقیم) | در mob_official_trial.py وقتی اجرای انتشار با --publish یا --publish-test-channel فعال باشد؛ از workflow رسمی Mob در هر اجرای زمان‌بندی‌شده/dispatch مجاز فراخوانی می‌شود. | موفقیت: «✅ خبر رسمی ماب انترتینمنت منتشر شد»، عنوان، نوع رسانه و inventory صف. خطا/نامشخص: «🚨 هشدار انتشار خبر رسمی ماب»، status، کد خطا و یادآوری عدم تکرار خودکار نتیجهٔ نامشخص. | selected result و status اجرای Mob؛ در موفقیت، queue summary از Worker/D1. اطلاعات عنوان/نوع رسانه از نتیجهٔ همان اجرای Mob است. |
| AI ledger event (تولید مستقیم در Worker، نه از CLI owner_notify) | هنگام رد reserve به دلیل cooldown یا اتمام سهمیه؛ و پس از finish هر تماس AI. پیام Rubika فوراً ارسال نمی‌شود؛ digest-refresh با فاصلهٔ حداکثر حدود ۱۵ دقیقه آن را نمایش می‌دهد. | category از نوع ai_error یا ai_success؛ عنوان‌هایی مانند «Code-repair AI call succeeded»، «Code-repair AI call did not succeed»، «provider was skipped» یا «quota exhausted». source از نوع code_ai_groq یا code_ai_openrouter است؛ detail شامل outcome/role/error code امن است. | Worker مستقیماً owner_digest_events را می‌نویسد. مصرف و وضعیت provider از ai_provider_calls و ai_provider_state محاسبه می‌شود؛ پیام digest توسط claim/snapshot از D1 جمع می‌شود. |
| queue_intake (گزینهٔ CLI owner_notify، نه پیام جزئی intake) | --event queue_intake قابل استفاده است، اما workflowهای فعلی که بررسی شدند این گزینه را مستقیماً صدا نمی‌زنند. مسیر واقعی intake از queue_intake.py پیام جزئی بالا را با send_owner_notification می‌سازد؛ intake زمان‌بندی‌شده نیز --no-notify دارد. | قالب عمومی: «📥 دریافت فهرست جدید انجام شد.» | یک event info را به digest اضافه می‌کند؛ خود قالب ثابت، آمار intake ندارد. |
| success (گزینهٔ CLI owner_notify) | گزینهٔ CLI موجود است اما در invocationهای فعلی workflowها به‌صورت --event success فراخوانی نمی‌شود. گزارش موفقیت واقعی از send_owner_notification در queue_worker.py و mob_official_trial.py می‌آید. | «✅ {workflow} با موفقیت انجام شد.» | منبع/نام workflow؛ پس از ثبت event، پنجرهٔ digest از Worker/D1 خوانده می‌شود. |
| test / custom message | event پیش‌فرض CLI است؛ در workflowهای فعلی دو پیام آغاز queue و Mob با --message از همین مسیر عبور می‌کنند. owner-notification-smoke.yml مسیر جداگانه‌ای است که فقط با workflow_dispatch یک پیام آزمایشی مستقیم می‌فرستد. | قالب پیش‌فرض owner_notify: «ℹ️ آزمایش مسیر اعلان روزانهٔ مالک.»؛ smoke test: «✅ تست خصوصی اعلان Rubika-Agent انجام شد… هیچ پستی منتشر نشده است.» | قالب test از D1/Worker دادهٔ وضعیت نمی‌خواند؛ اگر از owner_notify باشد event در digest ثبت می‌شود، اما smoke test مستقیماً Rubika sendMessage را صدا می‌زند و به D1 یا Worker احتیاج ندارد. |
| پاسخ‌های کنسول پایتونی | owner-console-poll.yml، هر ۵ دقیقه UTC؛ cron: */5 * * * *. | پاسخ به /help، /status، /queue، /ai-status [groq|openrouter] و /report؛ همچنین منوی تعاملی، دریافت لینک X و لغو. این‌ها پاسخ‌های command-based هستند، نه eventهای owner_notify. | /status از Worker status؛ /queue از queue-summary؛ /ai-status از AI provider ledger؛ /report هر سه منبع را ترکیب می‌کند و اجراهای اخیر GitHub Actions را از API عمومی GitHub می‌خواند. رخدادهای پذیرفته‌شده به‌صورت hash/update audit در جدول‌های owner_console ذخیره می‌شوند. |

نکته: owner_notify.py گزینه‌های workflow_failed، queue_intake، daily_digest_start، digest_refresh، success و test را می‌پذیرد. وقتی --message داده شود، متن static رویداد کنار گذاشته می‌شود؛ category همچنان با مقدار --event تعیین می‌شود (در صورت نگفتن آن، test/info). چند پیام مهم صف و Mob مستقیماً از send_owner_notification فراخوانی می‌شوند، پس نام event CLI ندارند و category آن‌ها از متن پیام حدس زده می‌شود.

## 2. Endpointهای Worker مورد استفادهٔ اعلان‌ها و کنسول

احراز عمومی در src/index.js این‌گونه است: GET /api/status پیش از بررسی bearer قرار دارد و برای خواندنش توکن لازم نیست. همهٔ مسیرهای زیر /api/brain/ پس از بررسی Authorization: Bearer AGENT_API_TOKEN قرار دارند. بنابراین هر GET/POST /api/brain/... در این جدول نیازمند Bearer معتبر است. در خطاها، ساختار معمول Worker برابر {ok: boolean, error: string} است؛ status HTTP ممکن است با نوع خطا تغییر کند.

### 2.1 Digest اعلان‌های مالک

| متد و مسیر | استفاده/احراز | پاسخ موفق: فیلدها و نوع |
|---|---|---|
| POST /api/brain/owner-digest/events | owner_notify.send_owner_notification؛ Bearer AGENT_API_TOKEN | {ok: boolean, window_id: string, event_id: number | null}. معمولاً HTTP 201. |
| POST /api/brain/owner-digest/claim | owner_notify.refresh_daily_digest و digest refresh workflow؛ Bearer AGENT_API_TOKEN | حالت قفل‌گرفته‌شده: {ok: boolean, claimed: boolean, lock_token: string, state: OwnerDigestSnapshot}. حالت مشغول: {ok: boolean, claimed: boolean, retry_after_ms: number}. |
| POST /api/brain/owner-digest/commit | owner_notify برای ذخیرهٔ وضعیت پیام پس از send/edit؛ Bearer AGENT_API_TOKEN | {ok: boolean, window_id: string, message_id: string | null}. |

ساختار OwnerDigestSnapshot: window_id:string؛ window_started_at:string؛ window_ends_at:string؛ message_id:string|null؛ message_body:string؛ message_creation_uncertain:boolean؛ counts: object با کلید category و مقدار number؛ total_event_count:number؛ events: آرایه‌ای از {id:number, occurred_at:string, category:string, source:string, title:string, detail:string|null}. این endpointها endpoint خواندن مستقل برای digest ندارند؛ claim با متد POST همزمان lease می‌گیرد و snapshot را برمی‌گرداند.

### 2.2 کنسول پایتونی و صفحات گزارش پنل Worker

| متد و مسیر | مصرف‌کننده/احراز | پاسخ موفق: فیلدها و نوع |
|---|---|---|
| GET /api/status | owner_console.py و src/owner_panel.js؛ در Worker احراز لازم نیست (پنل ممکن است bearer را هم بفرستد) | {ok:boolean, service:string, agent:{enabled:number, started_at:string|null, updated_at:string|null}, item_counts:Array<{status:string,total:number}>, recent_runs:Array<{id:string,status:string,started_at:string,finished_at:string|null,found_count:number,eligible_count:number,sent_count:number,skipped_count:number,failed_count:number}>, capabilities:{database:boolean,discovery:boolean,metadata_api:boolean,download:boolean,publishing:boolean}, publishing:boolean}. |
| GET /api/brain/owner-console/state | owner_console.py برای cursor/mode؛ Bearer AGENT_API_TOKEN | {ok:boolean, offset_id:string|null, updated_at:string|null}. |
| POST /api/brain/owner-console/offset | owner_console.py برای ثبت cursor/mode در offset_id؛ Bearer AGENT_API_TOKEN | {ok:boolean, offset_saved:boolean}. |
| POST /api/brain/owner-console/claim | owner_console.py برای dedupe/claim یک پیام؛ Bearer AGENT_API_TOKEN | پاسخ claim جدید: {ok:boolean, claimed:boolean, update_id:string}; duplicate: {ok:boolean, claimed:boolean, duplicate:boolean, status:string}; محدودیت روزانه: {ok:boolean, claimed:boolean, rate_limited:boolean}. |
| POST /api/brain/owner-console/finish | owner_console.py برای پایان audit یک update؛ Bearer AGENT_API_TOKEN | {ok:boolean, update_id:string, status:string}. status یکی از answered، failed، uncertain یا ignored است. |
| GET /api/brain/owner-console/queue-summary | owner_console.py برای /queue و /report؛ src/owner_panel.js برای p_queue و p_report؛ Bearer AGENT_API_TOKEN | {ok:boolean, queue_counts:object<string,number>, sent_today:number, item_counts:object<string,number>, usage_day:string, publishing:boolean}. این endpoint فقط خواندنی است و stale processing rows را repair نمی‌کند. |
| GET /api/brain/ai-provider/status?provider=groq یا openrouter | owner_console.py برای /ai-status و /report؛ src/owner_panel.js برای p_ai و p_report؛ Bearer AGENT_API_TOKEN | {ok:boolean, provider:string, usage_day:string, calls_used:number, daily_limit:number, remaining:number, total_calls_used:number, total_daily_limit:number, total_remaining:number, cooldown_until:string|null, cooldown_active:boolean, last_error_code:string|null, ready:boolean, status:string}. |

یادداشت ناسازگاری فعلی: format_status در کنسول پایتونی فیلد database را می‌خواند، اما پاسخ فعلی GET /api/status فیلد database ندارد (این فیلد در health endpoint هست). در نتیجه کنسول وضعیت پایگاه داده را «نامشخص» نمایش می‌دهد، حتی اگر Worker status پاسخ موفق بدهد. این موجودی فقط مستند می‌کند و آن را تغییر نمی‌دهد.

### 2.3 مسیرهای Worker برای گزارش‌های صفی که متن اعلان از آن‌ها استفاده می‌کند

این مسیرها توسط CloudState در intake/worker/Mob گزارش‌ها فراخوانی می‌شوند. آن‌ها APIهای عملیاتی هستند و بعضی از آن‌ها وضعیت صف را تغییر می‌دهند؛ با endpoint گزارش فقط‌خواندنی owner-console/queue-summary یکی فرض نشوند.

| متد و مسیر | استفاده/احراز | پاسخ موفق: فیلدها و نوع |
|---|---|---|
| POST /api/brain/automation-queue | queue_intake.py؛ Bearer AGENT_API_TOKEN | {ok:boolean, received_count:number, valid_count:number, inserted_count:number, duplicate_count:number, invalid_count:number, invalid:Array<{index:number,error:string}>}. |
| GET /api/brain/automation-queue?summary=1 | queue_intake.py/queue_worker.py/Mob برای inventory؛ Bearer AGENT_API_TOKEN | {ok:boolean, counts:object<string,number>, sent_today:number, usage_day:string}. توجه: handler فعلی قبل از برگرداندن summary آیتم‌های processing قدیمی‌تر از ۱۲ ساعت را به uncertain تبدیل می‌کند؛ پس این GET کاملاً read-only نیست. |
| GET /api/brain/automation-queue?status={queued|processing|sent|failed|uncertain|skipped}&limit={n}&offset={n} | queue_worker.py برای خواندن batch؛ Bearer AGENT_API_TOKEN | {ok:boolean, items:Array<AutomationQueueRow>, page:{limit:number,offset:number,returned:number}, counts:object<string,number>}. این حالت نیز قبل از خواندن، stale processing را repair می‌کند. |
| POST /api/brain/automation-queue/{id}/state | queue_worker.py برای ثبت وضعیت ارسال؛ Bearer AGENT_API_TOKEN | {ok:boolean, id:string, status:string, unchanged?:boolean}. status از queued، processing، sent، failed، uncertain یا skipped است. |

AutomationQueueRow شامل id، platform، external_id، canonical_url، source_key، title، caption، published_at، media_type، freshness_verified، method، metadata_json، status، attempt_count، last_error_code، created_at، updated_at و sent_at است. مقدار metadata_json یک رشتهٔ JSON است. پاسخ‌های خطا همچنان {ok:false,error:string} هستند.

### 2.4 منبع خارجی که در /report کنسول پایتونی استفاده می‌شود

/​report در owner_console.py علاوه بر Worker، درخواست GET به GitHub REST API عمومی برای /repos/hosseinasgari898989-dev/rubika-agent/actions/runs?per_page=10 می‌فرستد. این endpoint متعلق به Worker نیست و Bearer AGENT_API_TOKEN نمی‌خواهد؛ GITHUB_TOKEN در صورت تنظیم به صورت اختیاری استفاده می‌شود. کنسول فقط فیلدهای workflow_runs[].id:number، name:string، head_branch:string، status:string و conclusion:string را خلاصه می‌کند و لاگ خام workflow را نمی‌خواند.

### 2.5 رویدادهای AI که digest را تغذیه می‌کنند

reserve و finish دفتر AI نیز زیر احراز عمومی /api/brain/ هستند:
- POST /api/brain/ai-provider/reserve برای رزرو بودجه؛ پاسخ موفق معمولاً {ok:boolean, reserved:boolean, call_id:string, provider:string, usage_day:string, calls_used:number, daily_limit:number, remaining:number, total_calls_used:number, total_daily_limit:number, total_remaining:number, cooldown_until:string|null, cooldown_active:boolean, last_error_code:string|null, ready:boolean, status:string}. بعضی ردها علاوه بر ok:false/error، snapshot همین ledger را دارند.
- POST /api/brain/ai-provider/finish برای پایان دادن به رزرو؛ پاسخ موفق شامل ok، provider، call_id/status/outcome و snapshot ledger است. بعد از پایان، Worker تلاش می‌کند یک event از دستهٔ ai_success یا ai_error به owner_digest_events اضافه کند. این نوشتن جانبی نباید نتیجهٔ provider را تغییر دهد.

## 3. جداول D1 مرتبط؛ ستون‌ها مطابق migrations/

ستون‌های زیر از فایل‌های migrations/0001 تا 0009 تجمیع شده‌اند؛ ستون‌های nullable با علامت ? آمده‌اند. این فهرست schema تعریف‌شده در مخزن است، نه گواه اعمال‌شدن همهٔ migrationها روی D1 واقعی.

| جدول | ستون‌ها |
|---|---|
| agent_config | id INTEGER؛ enabled INTEGER؛ started_at TEXT?؛ updated_at TEXT |
| sources | id TEXT؛ platform TEXT؛ source_key TEXT؛ display_name TEXT?؛ enabled INTEGER؛ created_at TEXT؛ last_checked_at TEXT?؛ last_seen_published_at TEXT? |
| items (با افزوده‌های 0002) | id TEXT؛ platform TEXT؛ source_key TEXT؛ external_id TEXT؛ canonical_url TEXT؛ title TEXT?؛ caption TEXT?؛ published_at TEXT?؛ discovered_at TEXT؛ status TEXT؛ media_sha256 TEXT?؛ media_bytes INTEGER?؛ media_mime TEXT?؛ rubika_message_id TEXT?؛ attempt_count INTEGER؛ last_error_code TEXT?؛ last_error_at TEXT?؛ sent_at TEXT?؛ media_type TEXT؛ source_tier TEXT؛ storage_key TEXT?؛ validation_json TEXT?؛ moderation_json TEXT?؛ review_state TEXT؛ original_caption TEXT?؛ persian_translation TEXT? |
| runs | id TEXT؛ trigger_type TEXT؛ status TEXT؛ started_at TEXT؛ finished_at TEXT?؛ found_count INTEGER؛ eligible_count INTEGER؛ sent_count INTEGER؛ skipped_count INTEGER؛ failed_count INTEGER؛ summary TEXT? |
| delivery_attempts | id INTEGER؛ item_id TEXT؛ attempt_number INTEGER؛ status TEXT؛ started_at TEXT؛ finished_at TEXT?؛ rubika_message_id TEXT?؛ error_code TEXT? |
| brain_events | id INTEGER؛ run_id TEXT?؛ item_id TEXT?؛ level TEXT؛ code TEXT؛ details_json TEXT?؛ created_at TEXT |
| ai_usage_daily | usage_day TEXT؛ request_count INTEGER؛ updated_at TEXT |
| automation_queue | id TEXT؛ platform TEXT؛ external_id TEXT؛ canonical_url TEXT؛ source_key TEXT؛ title TEXT?؛ caption TEXT?؛ published_at TEXT?؛ media_type TEXT؛ freshness_verified INTEGER؛ method TEXT?؛ metadata_json TEXT؛ status TEXT؛ attempt_count INTEGER؛ last_error_code TEXT?؛ created_at TEXT؛ updated_at TEXT؛ sent_at TEXT? |
| ai_provider_calls (نقش translate_official در migration 0008 اضافه شده) | call_id TEXT؛ usage_day TEXT؛ provider TEXT؛ role TEXT؛ run_id TEXT?؛ status TEXT؛ error_code TEXT?؛ prompt_tokens INTEGER?؛ output_tokens INTEGER?؛ total_tokens INTEGER?؛ cooldown_until TEXT?؛ finish_marker TEXT?؛ started_at TEXT؛ finished_at TEXT? |
| ai_provider_state | provider TEXT؛ cooldown_until TEXT؛ last_error_code TEXT؛ updated_at TEXT |
| owner_digest_state | id INTEGER؛ window_id TEXT؛ window_started_at TEXT؛ window_ends_at TEXT؛ window_started_epoch INTEGER؛ window_ends_epoch INTEGER؛ message_id TEXT?؛ message_body TEXT?؛ message_creation_uncertain INTEGER؛ edit_lock_token TEXT?؛ edit_lock_until_epoch INTEGER?؛ updated_at TEXT |
| owner_digest_events | id INTEGER؛ window_id TEXT؛ category TEXT؛ source TEXT؛ title TEXT؛ detail TEXT?؛ occurred_at TEXT |
| owner_console_state | id INTEGER؛ offset_id TEXT?؛ updated_at TEXT |
| owner_console_updates | update_id TEXT؛ chat_hash TEXT؛ command TEXT؛ status TEXT؛ result_code TEXT?؛ created_at TEXT؛ updated_at TEXT |
| panel_owner | id INTEGER؛ chat_id TEXT؛ claimed_at TEXT |
| panel_events | event_key TEXT؛ created_at TEXT |
| panel_state | key TEXT؛ value TEXT؛ updated_at TEXT |
| panel_links | id INTEGER؛ url TEXT؛ status TEXT؛ created_at TEXT |

نکات تفسیری:
- صف پایدار انتشار در automation_queue است؛ اقلام متادیتا/رسانه و وضعیت بررسی در items است؛ نتایج اجرا در runs و delivery_attempts و brain_events ثبت می‌شوند.
- owner_digest_state محتوای پیام واحد ۲۴ ساعته و lease ویرایش را نگه می‌دارد؛ owner_digest_events رخدادهایی را که در آن پیام جمع می‌شوند ذخیره می‌کند.
- کنسول پایتونی cursor و audit محدود را در owner_console_state و owner_console_updates نگه می‌دارد؛ hash chat ذخیره می‌شود، نه متن پیام.
- پنل Worker از panel_state کلید-مقدار استفاده می‌کند؛ نمونهٔ کلیدها در کد فعلی شامل panel_mid، mode، last_error، panel_edit_ok، panel_edit_fail، last_event و last_timings است. last_timings JSON محدود به پنج رویداد است و schema ستون تازه نمی‌خواهد.
- migrations/0008 جدول ai_provider_calls را بازسازی می‌کند و role مجاز translate_official را به فهرست اضافه می‌کند؛ ستون جدید جداگانه‌ای ایجاد نمی‌کند.
- migration/0009 به‌صورت افزایشی panel_owner، panel_events، panel_state و panel_links را ایجاد می‌کند.

## 4. پیشنهاد جایگزینی هر گزارش در کنسول جدید

| گزارش/اعلان فعلی | بخش کنسول جدید که می‌تواند جایگزین یا مکمل باشد | شکاف باقی‌مانده |
|---|---|---|
| Worker / database / وضعیت کلی | پنل p_status؛ کنسول پایتونی /status | پاسخ Worker status در حال حاضر فیلد database ندارد؛ علاوه بر آن، خود پنل Cloud Worker جمع‌بندی CI را ارائه نمی‌کند. |
| شمارش صف و ارسال امروز | پنل p_queue و بخش صف در p_report؛ کنسول پایتونی /queue | این‌ها شمارش تجمیعی هستند؛ متن per-item success و inventory مربوط به MEGA از آن‌ها بازسازی نمی‌شود. |
| سهمیه، readiness و cooldown هوش مصنوعی | پنل p_ai؛ کنسول پایتونی /ai-status | برای دیدن رخدادهای تاریخی موفق/ناموفق AI همچنان باید owner digest یا صفحهٔ رخدادها استفاده شود؛ status فقط snapshot ledger است. |
| گزارش پایانی queue_worker | بخش صف در p_report، به‌اضافهٔ آمار /queue | p_report تعداد تلاش‌ها، کدهای خطای اجرای اخیر و جزییات workflow را فعلاً ندارد. برای جایگزینی کامل باید API/read model تاریخچهٔ run گزارش‌شده به کنسول اضافه شود. |
| خطای workflow_failed و intake failure | /report کنسول پایتونی برای وضعیت/CI؛ p_debug فقط برای خطاهای داخلی خود پنل | p_debug.last_error خطای ویرایش پیام پنل است، نه آخرین خطای pipeline. برای حذف اعلان push باید بخش «رخدادهای اخیر» به D1 owner_digest_events دسترسی خواندنی داشته باشد؛ اکنون endpoint خواندن مستقل برای event history وجود ندارد. |
| اعلان‌های ورود/افزودن صف | p_queue برای وضعیت فعلی صف | تعداد تازه/تکراری/نامعتبر مربوط به هر intake در snapshot صف نیست؛ برای جایگزینی پیام intake، رخدادهای intake باید با report ID و آمارش در history ثبت و نمایش داده شوند. |
| اعلان موفقیت هر ویدئو و گزارش پایان نوبت | p_queue برای aggregate و p_report برای وضعیت/صف/AI | پیام موفقیت در سطح آیتم، سهمیهٔ همان اجرا، inventory MEGA و کد خطاهای منتخب در کنسول فعلی موجود نیست؛ بخش activity/آخرین اجرا لازم است. |
| شروع و موفقیت/خطای Mob X | p_mob فقط برای ورود لینک X؛ p_report برای وضعیت کلی | p_mob فعلاً intake دستی لینک است، نه مانیتور نتیجهٔ workflow Mob. برای جایگزینی اعلان‌ها بخش Mob automation activity و نتیجهٔ آخرین اجرا لازم است. |
| daily_digest_start و digest_refresh | پیشنهاد: صفحهٔ «رخدادهای اخیر/گزارش ۲۴ ساعت» با شمار دسته‌ها و فهرست eventها | پنل فعلی endpoint خواندنی برای owner_digest_events ندارد؛ افزودن UI به‌تنهایی کافی نیست. تا زمان فراهم‌شدن read endpoint، digest push تنها نمای تاریخی یکپارچهٔ این رخدادهاست. |
| /report کنسول پایتونی، شامل CI | پنل p_report به‌علاوهٔ یک بخش CI | p_report پنل Worker فعلاً status، صف و AI را ترکیب می‌کند ولی GitHub Actions را نمی‌خواند؛ اگر هدف حذف کنسول پایتونی است، بخش CI باید جداگانه به پنل افزوده شود. |
| /help و پیام منوی PV | پنل p_home / p_help | قابلیت جایگزینی مستقیم دارد؛ محدودیت ایمنی فعلی حفظ شود و هیچ فرمان shell، انتشار یا تغییر صف از چت اضافه نشود. |

## جمع‌بندی اجرایی

1. گزارش‌های تعاملی وضعیت، صف و سهمیه اکنون در هر دو کنسول موجودند؛ گزارش ترکیبی کنسول پایتونی علاوه بر آن وضعیت CI اخیر را هم می‌خواند.
2. اعلان‌های push برای خطا، هر موفقیت رسانه، پایان اجرا و رویدادهای AI از نظر زمانی/جزئیات با snapshotهای /status و /queue معادل نیستند. برای جایگزینی‌شان، بخش رخدادهای اخیر همراه با read endpoint امن برای owner_digest_events و گزارش‌های run لازم است.
3. GET /api/brain/owner-console/queue-summary مسیر فقط‌خواندنی صف است. از GET /api/brain/automation-queue?summary=1 یا فهرست صف به‌عنوان endpoint خالص گزارشی استفاده نشود؛ handler فعلی آن‌ها processingهای قدیمی را به uncertain تغییر می‌دهد.
4. در این PR فقط مستند موجودی اضافه می‌شود؛ کد، workflow، endpoint، migration یا داده‌ای تغییر داده نشده است.
