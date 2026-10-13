# Rubika-Agent — Cloud Edition

هدف این مخزن اجرای ایجنت روبیکا بدون Termux و با زیرساخت رایگان تا حد امکان است.

## معماری
- **Cloudflare Workers Builds:** اتصال مستقیم Worker به GitHub و استقرار خودکار بعد از push به شاخهٔ `main`.
- **Cloudflare Worker + D1:** API کنترل و ذخیرهٔ فراداده‌ها.
- **GitHub Actions:** اجرای تست‌های مخزن؛ مسئول استقرار Worker نیست.
- **فایل ویدئوها:** در مرحلهٔ دانلود و اعتبارسنجی روی runner موقت پردازش می‌شوند؛ Cloudflare R2 استفاده نمی‌شود.
- **ارسال به روبیکا:** فعلاً عمداً غیرفعال است.

## سرویس Cloudflare (اکانت Hossein 3)
- Worker: `rubika-agent`
- D1: `rubika-agent-db`
- Worker URL: `https://rubika-agent.h98609294.workers.dev`
- Health: `/health`
- Status: `/api/status`

## اتصال مستقیم Worker به GitHub
ریپوی `hosseinasgari898989-dev/rubika-agent` از طریق Cloudflare Workers Builds به شاخهٔ `main` متصل است.

تنظیمات مورد انتظار:
- Production branch: `main`
- Build command: خالی
- Deploy command: `npx --yes wrangler@4 deploy --config wrangler.jsonc`
- Root directory: ریشهٔ مخزن
- Disable builds: خاموش

فایل `wrangler.jsonc` از نام Worker (`rubika-agent`)، شناسهٔ اکانت Hossein 3 و شناسهٔ D1 همین اکانت استفاده می‌کند. API سلامت و وضعیت در آدرس بالا بررسی شده و اتصال D1 تأیید شده است.

Workers Builds می‌تواند API token مخصوص build را در Cloudflare ایجاد و مدیریت کند؛ برای استقرار مستقیم Cloudflare نیازی به قرار دادن توکن در GitHub Secrets نیست.

### Migrationهای D1
ساختار اولیهٔ دیتابیس `rubika-agent-db` در اکانت Hossein 3 ایجاد شده است. Deploy معمولی Worker، Migrationهای جدید D1 را خودکار اجرا نمی‌کند؛ migrationهای آینده باید جداگانه اجرا شوند یا روش اجرای خودکار آن آگاهانه پیکربندی شود.

## بررسی اتصال روبیکا
در صورت نیاز به آزمایش بدون انتشار، در GitHub Actions، Workflow **Cloud bootstrap checks** را اجرا کن و حالت `rubika-auth` را انتخاب کن. Secretهای `RUBIKA_TOKEN` و `RUBIKA_CHANNEL_GUID` را فقط در تنظیمات Secrets مخزن نگه دار. این آزمایش نباید هیچ پیامی منتشر کند.

## ایمنی فعلی
- ایجنت به‌صورت پیش‌فرض خاموش است و API فعلی فقط خواندنی است.
- کشف خودکار محتوا، دانلود خودکار و انتشار هنوز فعال نشده‌اند.
- هیچ توکنی را در کد، README، تاریخچهٔ Git یا لاگ Workflow قرار نده.
