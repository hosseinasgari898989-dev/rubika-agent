# Rubika-Agent — Cloud Edition

هدف این مخزن اجرای ایجنت روبیکا بدون Termux و بدون سرویس پولی است.

## معماری
- **Cloudflare Worker + D1:** وضعیت سرویس و دیتابیس فراداده‌ها.
- **GitHub Actions:** اجرای تست‌ها و در مراحل بعد اجرای کارهای سنگین دانلود/اعتبارسنجی روی runner موقت.
- **فایل ویدئوها:** فقط موقت روی runner پردازش می‌شوند؛ Cloudflare R2 استفاده نمی‌شود.
- **ارسال به روبیکا:** هنوز عمداً غیرفعال است؛ ابتدا اتصال بات و دسترسی کانال آزمایش می‌شود.

## سرویس زنده
Worker name: `rubika-agent-control`
D1 database: `rubika-agent-db`
Health endpoint: `/health`
Status endpoint: `/api/status`

## بررسی اتصال روبیکا
۱. در تنظیمات مخزن GitHub به **Settings → Secrets and variables → Actions → New repository secret** برو.
۲. دو Secret بساز: `RUBIKA_TOKEN` و `RUBIKA_CHANNEL_GUID`.
۳. مقدار توکن فقط در فرم Secret وارد شود؛ آن را در چت، فایل، README یا لاگ‌ها ننویس.
۴. از **Actions → Cloud bootstrap checks → Run workflow** حالت `rubika-auth` را اجرا کن.

این آزمون فقط اعتبار توکن و دسترسی بات به کانال را بررسی می‌کند؛ هیچ پیامی منتشر نمی‌کند. منابع پیش‌فرض خاموش‌اند و پست‌های قدیمی نباید وارد صف شوند.

## اجرای تست‌ها
Workflow تست‌های خودکار را روی GitHub Actions اجرا می‌کند. اجرای زمان‌بندی‌شده یا انتشار خودکار هنوز فعال نشده است.
