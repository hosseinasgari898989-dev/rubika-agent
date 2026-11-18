# چک‌لیست شواهد راه‌اندازی کانال اصلی روبیکا

**وضعیت این سند: فقط مستندات؛ کانال اصلی قفل است.** این فایل مجوز انتشار، تغییر pin، تغییر Secret/Variable یا حذف کانال تست نیست.

## ترتیب غیرقابل‌تغییر اجرا

1. **Preflight فقط‌خواندنی:** ابتدا ربات و دسترسی ادمین به مقصد MAIN با `RUBIKA_MAIN_CHANNEL_GUID` بررسی شود. نتیجه باید هویت ربات و دسترسی مقصد را تأیید کند؛ `main_matches_test_pin=false` باشد. هیچ پیام یا پستی ارسال نمی‌شود.
2. **بازکردن gateها، یکی‌یکی و با دو تأیید مالک:**
   - تأیید کتبی شمارهٔ ۱ فقط برای بازکردن gate صف TikTok یعنی `RUBIKA_AGENT_TIKTOK_EDIT_QUEUE_PRODUCTION_GATE`؛ پس از بازکردن، وضعیت همان gate و مقصد مؤثر بررسی و ثبت شود. gate مربوط به Mob در این مرحله بسته بماند.
   - تأیید کتبی شمارهٔ ۲ فقط برای بازکردن gate خبر Mob یعنی `RUBIKA_AGENT_MOB_X_NEWS_MAIN_CHANNEL_GATE`؛ پس از بازکردن، وضعیت همان gate و مقصد مؤثر بررسی و ثبت شود. تأیید کلی یا مربوط به کار دیگری معتبر نیست.
   - مقدار معتبر برای بازشدن هر gate فقط `open` است؛ مقدار خالی، نامعتبر یا غیرقابل‌خواندن یعنی `CLOSED`. بازبودن gate قدیمی Mob (`RUBIKA_AGENT_MOB_X_PUBLISH_GATE_20261014`) جای gate مستقل مقصد MAIN را نمی‌گیرد.
3. **تغییر pin با PR بازبینی‌شده:** پس از موفقیت preflight و ثبت دو تأیید، تغییر مقصد/هش pin تولیدی فقط در PR جداگانه، کوچک، backup‌شده و reviewشده انجام شود. همهٔ CIها باید روی head نهایی همان PR سبز باشند. Secret مشترک `RUBIKA_CHANNEL_GUID` که به TEST اشاره می‌کند، در این مرحله نیز به‌صورت ضمنی تغییر نکند.
4. **اولین پست محدود:** پس از ادغام PR pin و تأیید مجوز انتشار، فقط یک پست آزمایشیِ محدود به مقصد MAIN ارسال شود. X-only، مرز `born_at`، سقف یک پست، اعتبارسنجی رسانه، کنترل duplicate و `publishing_paused` باید برقرار باشند. رسید واقعی مقصد و شناسهٔ پیام به‌صورت امن ثبت شود؛ هیچ شناسه یا Secret خامی در log قرار نگیرد.
5. **حذف کانال TEST فقط پس از تأیید پست اول:** حذف کانال تست آخرین اقدام است، نه پیش‌شرط rollout. تا زمانی که پست اول در MAIN تأیید نشده و وضعیت `uncertain`/duplicate حل‌نشده وجود دارد، کانال تست حفظ شود.

## نگاشت ۹ مرحلهٔ GO-LIVE.md به شواهد قابل‌قبول

| مرحلهٔ GO-LIVE.md | شاهد لازم برای بستن مرحله | محل ثبت/بررسی |
|---|---|---|
| 1. تأیید صریح مالک | متن صریح تأیید شمارهٔ ۱ برای TikTok و تأیید شمارهٔ ۲ برای Mob؛ هرکدام محدود به همان gate | پیام مالک یا تأیید ثبت‌شده در PR/گزارش عملیات؛ زمان و دامنهٔ تأیید |
| 2. راستی‌آزمایی امن مقصد | خروجی preflight با `bot_identity_ok=true`، `main_destination_accessible=true`، `main_matches_test_pin=false` و `message_sent=false` | گزارش workflow preflight؛ فقط Boolean و پیشوند کوتاه SHA-256 |
| 3. جدایی مقصدها و guardها | PRهای مستقل R2 و R3، تست‌های gate بسته/باز/مقصد نامطابق، بررسی حفظ pin تست و عدم fallback؛ gateها در حالت پیش‌فرض بسته | PR diff، CI روی head نهایی، تنظیمات repo Variables |
| 4. تست کامل فقط در کانال تست | گزارش E2E از discovery تا رسید ارسال در TEST؛ شواهد duplicate، timeout، stale callback، uncertain delivery، retry و بازیابی | Actions artifacts، گزارش صف، گزارش رسانه و وضعیت D1 فقط‌خواندنی |
| 5. تأیید UX کنسول PV | تست mocked و Playwright برای editMessage، پیام preview مستقل و پاکسازی پیام موقت؛ بدون پیام اضافه برای کلیک عادی | CI و artifact تست مرورگر/گزارش‌های کنسول |
| 6. تأیید ظرفیت رایگان | شواهد پایش D1، فضای Mega، Worker requests/CPU/subrequests، Workers AI، سهمیه‌های Groq/OpenRouter، Actions و artifact storage؛ هشدار ۷۰٪ و مسیر کاهش بار آزموده شده باشد | گزارش‌های مصرف و هشدارها؛ تاریخ و بازهٔ اندازه‌گیری مشخص |
| 7. آمادگی عملیاتی و rollback | همهٔ CIهای لازم سبز، backup branch موجود، health check و اعلان خطا در دسترس، مورد uncertain/duplicate حل‌نشده صفر | PRها، فهرست backupها، `/api/health`، گزارش failure و شواهد rollback |
| 8. تأیید نهایی درست پیش از تغییر | تأیید شمارهٔ ۲ باید مستقل، صریح و نزدیک به زمان PR تغییر pin باشد؛ هر تغییر جدید پس از آن تأیید را باطل می‌کند | پیام مالک و PR تغییر pin با timestamp |
| 9. راه‌اندازی کنترل‌شده و پایش | گزارش پست اول با مقصد MAIN، تعداد دقیق یک پست، رسید موفق، عدم duplicate و مصرف منابع در محدوده؛ پس از آن حذف TEST | گزارش publish، receipt، status صف، health check و گزارش ظرفیت |

## Rollback یک‌مرحله‌ای به TEST

با یک تغییر عملیاتی، هر دو متغیر `RUBIKA_AGENT_TIKTOK_EDIT_QUEUE_PRODUCTION_GATE` و `RUBIKA_AGENT_MOB_X_NEWS_MAIN_CHANNEL_GATE` را هم‌زمان از `open` به `closed` برگردان. از آن پس هر اجرای جدید فقط مقصد pin‌شدهٔ TEST را می‌پذیرد و به‌علت بسته‌بودن gate اجازهٔ انتخاب MAIN ندارد. اجرای درحال‌انجامی که ممکن است قبلاً مقصد MAIN را resolve کرده باشد باید فوراً متوقف و وضعیت آن جداگانه بررسی شود. Secret مشترک TEST را برای rollback تغییر نده.

## شروط توقف

- کانال اصلی تا پایان ترتیب بالا قفل می‌ماند؛ این PR هیچ gate یا Secret/Variable زنده‌ای را باز نمی‌کند.
- pin تست در `agent/brain/test_channel.py` و Secret مشترک `RUBIKA_CHANNEL_GUID` در این کار تغییر نمی‌کنند.
- migration، D1 write، Mega delete، workflow dispatch، AI call و انتشار در این کار انجام نمی‌شود.
- هر ابهام در مقصد، gate، دسترسی، رسید ارسال یا وضعیت publishing باعث توقف fail-closed است.
