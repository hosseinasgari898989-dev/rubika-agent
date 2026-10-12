# Rubika-Agent — Cloud Edition

هدف این مخزن اجرای ایجنت روبیکا بدون Termux و با زیرساخت رایگان تا حد امکان است.

## معماری
- **Cloudflare Workers Builds:** اتصال مستقیم Worker به GitHub و استقرار خودکار بعد از push به شاخهٔ `main`.
- **Cloudflare Worker + D1:** API کنترل و ذخیرهٔ فراداده‌ها.
- **GitHub Actions:** اجرای تست‌های مخزن؛ مسئول استقرار Worker نیست.
- **فایل ویدئوها:** در مرحلهٔ دانلود و اعتبارسنجی روی runner موقت پردازش می‌شوند؛ Cloudflare R2 استفاده نمی‌شود.
- **ارسال به روبیکا:** فعلاً عمداً غیرفعال است.

## سرویس Cloudflare
- Worker موجود: `rubika-agent-control`
- D1: `rubika-agent-db`
- Worker URL: `https://rubika-agent-control.amber-harbor-f617.workers.dev`
- Health: `/health`
- Status: `/api/status`

## اتصال مستقیم Worker به GitHub
از داشبورد Cloudflare، **همان Worker موجود** با نام `rubika-agent-control` را باز کن و از **Settings → Builds → Connect** ریپوی `hosseinasgari898989-dev/rubika-agent` را متصل کن.

تنظیمات مورد انتظار:
- Git account: `hosseinasgari898989-dev`
- Repository: `rubika-agent`
- Production branch: `main`
- Build command: خالی بماند؛ در این مخزن مرحلهٔ build جداگانه لازم نیست.
- Deploy command: `npx --yes wrangler@4 deploy --config wrangler.jsonc`
- Root directory: خالی بماند تا تنظیمات و کد از ریشهٔ مخزن خوانده شوند.
- Disable builds: خاموش بماند.

فایل `wrangler.jsonc` از قبل نام Worker، شناسهٔ حساب Hossein 2 و شناسهٔ D1 موردنظر را تعیین کرده است. نام Worker در داشبورد باید با فیلد `name` در این فایل برابر باشد.

Workers Builds می‌تواند API token مخصوص build را در Cloudflare ایجاد و مدیریت کند؛ برای **استقرار مستقیم Cloudflare** نیازی نیست مقدار توکن را در GitHub Secrets با نام `CLOUDFLARE_API_TOKEN` ذخیره کنی. این روش از Workflow مستقل استقرار در GitHub Actions استفاده نمی‌کند.

### Migrationهای D1
ساختار فعلی D1 از قبل ایجاد شده است. Deploy معمولی Worker، Migrationهای جدید D1 را خودکار اجرا نمی‌کند. وقتی در آینده فایل migration جدید اضافه شد، آن migration باید جداگانه اجرا شود یا دسترسی و روش اجرای خودکار آن به‌صورت آگاهانه پیکربندی شود.

## بررسی اتصال روبیکا
در صورت نیاز به آزمایش بدون انتشار، در GitHub Actions، Workflow **Cloud bootstrap checks** را اجرا کن و حالت `rubika-auth` را انتخاب کن. برای این آزمون، Secretهای `RUBIKA_TOKEN` و `RUBIKA_CHANNEL_GUID` را جداگانه و فقط در تنظیمات Secrets مخزن نگه دار. این آزمایش نباید هیچ پیامی منتشر کند.

## ایمنی فعلی
- ایجنت به‌صورت پیش‌فرض خاموش است و API فعلی فقط خواندنی است.
- کشف خودکار محتوا، دانلود خودکار و انتشار هنوز فعال نشده‌اند.
- هیچ توکنی را در کد، README، تاریخچهٔ Git یا لاگ Workflow قرار نده.
