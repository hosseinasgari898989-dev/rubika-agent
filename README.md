# Rubika-Agent — Cloud Edition

هدف این مخزن اجرای ایجنت روبیکا بدون Termux و با زیرساخت رایگان تا حد امکان است.

## معماری
- **Cloudflare Worker + D1:** API کنترل و ذخیرهٔ فراداده‌ها.
- **GitHub Actions:** تست و استقرار Worker از روی همین مخزن؛ همچنین اجرای کارهای سنگین دانلود/اعتبارسنجی روی runner موقت.
- **فایل ویدئوها:** فقط موقت روی runner پردازش می‌شوند؛ Cloudflare R2 استفاده نمی‌شود.
- **ارسال به روبیکا:** فعلاً عمداً غیرفعال است. پیش از هر انتشار، باید اتصال و دسترسی بات جداگانه بررسی شود.

## سرویس Cloudflare
- Worker: `rubika-agent-control`
- D1: `rubika-agent-db`
- Health: `/health`
- Status: `/api/status`
- API endpoint: `https://rubika-agent-control.amber-harbor-f617.workers.dev`

## اتصال GitHub به Cloudflare
Workflow استقرار در `.github/workflows/deploy-worker.yml` قرار دارد. پس از اضافه‌شدن Secret لازم و ادغام تغییرات در `main`، هر تغییری در کد Worker، تنظیمات Wrangler یا migrationهای D1 به‌طور خودکار استقرار داده می‌شود. استقرار، migrationها را اجرا و سپس `/health` و `/api/status` را بررسی می‌کند.

برای فعال‌کردن استقرار:
1. در حساب Cloudflare **Hossein 2** یک API Token با دسترسی ویرایش Workers Scripts و D1 برای همان حساب بساز.
2. در GitHub مخزن `hosseinasgari898989-dev/rubika-agent`، به **Settings → Secrets and variables → Actions → New repository secret** برو.
3. نام Secret را دقیقاً `CLOUDFLARE_API_TOKEN` بگذار و مقدار Token را فقط همان‌جا وارد کن؛ هرگز آن را در چت یا فایل مخزن ننویس.
4. بعد از آماده‌شدن Secret، تغییرات شاخهٔ اتصال را در `main` ادغام کن. Workflow استقرار را می‌توان از تب **Actions** هم به‌صورت دستی اجرا کرد.

## بررسی اتصال روبیکا
برای آزمون بدون انتشار، در GitHub Actions، Workflow **Cloud bootstrap checks** را اجرا کن و حالت `rubika-auth` را انتخاب کن. برای این آزمون، Secretهای `RUBIKA_TOKEN` و `RUBIKA_CHANNEL_GUID` را جداگانه و فقط در تنظیمات Secrets مخزن نگه دار. این آزمایش نباید هیچ پیامی منتشر کند.

## ایمنی فعلی
- ایجنت به‌صورت پیش‌فرض خاموش است؛ منابع ورودی نیز تا انتخاب و تأیید کاربر خاموش می‌مانند.
- این مرحله شامل کشف خودکار محتوا، فیلتر هوش مصنوعی یا انتشار نیست.
- هیچ توکنی نباید در کد، README، تاریخچهٔ Git یا لاگ Workflow قرار بگیرد.
