# Checkpoint — Rubika-Agent, 2026-10-14

## هدف فعلی
- صف معمول فقط برای ادیت‌های TikTok است.
- خبرهای رسمی Mob Entertainment باید در مسیر مستقل از حساب رسمی X یعنی @mobgamesstudios پردازش شوند.
- YouTube فعلاً متوقف و Instagram از مسیر فعال حذف شده است.
- انتشار عمومی همچنان خاموش است؛ اجرای Mob در حال حاضر فقط Preview است.

## تغییرهای ثبت‌شده روی agent-brain-mega-v1
- `agent/discovery/termux_mob_x.py`: کشف فرادادهٔ پست‌های X با Bing RSS، تلاش روی نمایهٔ عمومی mirror به‌عنوان منبع شناسه‌ها، سپس تأیید نام حساب/کپشن/تاریخ/نوع رسانه از X syndication. این مسیر رایگان و best-effort است، نه API رسمی تضمینی.
- `termux/daily_discovery.py`: گزارش Termux فعلاً فقط نامزدهای TikTok و Mob X را ترکیب می‌کند؛ بدون دانلود یا انتشار از سمت گوشی.
- `agent/brain/queue_intake.py`: فقط TikTok را وارد صف معمول می‌کند. خبر Mob وارد این صف نمی‌شود.
- `agent/brain/queue_worker.py`: فقط TikTok می‌تواند از صف معمول به مرحلهٔ پردازش برسد؛ YouTube/Instagram متوقف‌اند.
- `agent/brain/mob_official_trial.py`: مسیر مستقل Preview خبر Mob؛ اگر `--candidate-report` داده شود، فقط پست‌های X تأییدشده در گزارش Termux را مصرف می‌کند. رسانهٔ عکس باید از `pbs.twimg.com` و ویدیو از `video.twimg.com` باشد.
- `.github/workflows/mob-official-trial.yml`: نام جدید `Mob Entertainment X news preview`; در این workflow توکن انتشار روبیکا و شناسهٔ کانال اصلاً در دسترس نیستند.
- `.github/workflows/mob-x-free-live-check.yml`: تشخیص زندهٔ رایگان X از GitHub Actions؛ بدون دانلود رسانه، خرج API یا انتشار روبیکا.
- `agent/lifecycle.py` و `config/agent_lifecycle.json`: حالت فعلی `test` است. هنگام راه‌اندازی نهایی، `mode=production` و `born_at` به زمان دقیق UTC فعال‌سازی تغییر کند؛ در تولید فقط پست‌های دارای تاریخ قابل‌تأیید و منتشرشده در یا بعد از تولد ایجنت پذیرفته می‌شوند. این cutoff در کشف، ورود صف و پیش از پردازش نهایی بررسی می‌شود.
- `tests/test_termux_mob_x.py`, `tests/test_lifecycle.py`, `tests/test_mob_official_trial.py`: آزمون‌های جدید اضافه شده‌اند.
- `docs/TERMUX_DAILY_DISCOVERY.md`: دستور روزانه و معماری جدید توضیح داده شده است.

## نتیجهٔ آزمون‌ها
- آخرین `Agent brain checks` روی commit `c54f86bdf7d4e3181b4c26e6c339bc17525cb1a6` موفق بود؛ آزمون‌ها شامل parser X، تأیید نام کاربری رسمی، media URL و cutoff تولد هستند.
- اجرای live check در GitHub Actions موفق شد به‌عنوان آزمایش تشخیصی، اما هیچ نامزد زنده‌ای پیدا نشد: Bing HTTP 200 با صفر لینک، TwStalker HTTP 403، DuckDuckGo HTTP 202. آزمایش yt-dlp برای URL نمایهٔ X هم با چالش 403 متوقف شد. بنابراین دریافت مستقیم زنده از X هنوز از سمت runner تأیید نشده است.
- این نتیجه به معنی نبود پست در حساب Mob نیست؛ جست‌وجوی عمومی برنامه‌ای از شبکهٔ GitHub در این لحظه مسدود/کم‌نتیجه بوده. وقتی اینترنت Termux برگشت، باید `bash termux/daily.sh` اجرا شود تا رفتار از شبکهٔ واقعی گوشی بررسی شود.

## ایمنی و مواردی که هنوز نباید انجام شوند
- قفل انتشار خودکار `RUBIKA_AGENT_PUBLISH_GATE_20261014` در GitHub Actions نباید `approved` شود تا تست سرتاسری با موفقیت تکمیل و راه‌اندازی تولیدی تأیید شود.
- workflow قدیمی `mob-news-auto.yml` از main حذف شده تا مسیر زمان‌بندی‌شدهٔ قدیمی به کانال پست نکند.
- Preview موفق، خودش به معنی مجوز بازنشر رسانه‌های دارای حق نشر نیست؛ پیش از انتشار عمومی باید حق استفاده از رسانه‌های Mob تأیید باشد.
- هیچ دستور جدیدی لازم نیست تا وقتی اینترنت گوشی برگردد. پس از آن، pull شاخهٔ آزمایشی و اجرای daily script تست بعدی است.

## دستور بعد از بازگشت اینترنت
    cd ~/rubika-agent-github && git pull --ff-only origin agent-brain-mega-v1 && bash termux/daily.sh
