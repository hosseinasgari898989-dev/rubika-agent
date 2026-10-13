# یک اجرای واقعی از گزارش گوشی تا روبیکا

این آزمایش دستی است و در هر اجرا حداکثر یک ویدئو را انتخاب می‌کند. مسیر اجرا:
گزارش `artifacts/termux_discovery.json` ← دانلود در GitHub Actions ← بررسی محتوای تصویری/صوتی با مغز Cloudflare AI ← تبدیل به MP4 عمودی 1080×1920 ← MEGA ← D1 ← ارسال به کانال روبیکا.

تاریخ انتشار برای انتخاب نامزد شرط نیست. ویدئو فقط در صورت موفقیت دانلود، اعتبارسنجی رسانه و عبور از همهٔ بررسی‌های مغز ارسال می‌شود. این workflow فقط با `workflow_dispatch` اجرا می‌شود؛ با هر push خودکار ویدئو منتشر نمی‌کند.

## ۱. ساخت توکن مشترک

در Termux:

```bash
cd ~/rubika-agent-github
TOKEN="$(python -c 'import secrets; print(secrets.token_hex(32))')"
gh secret set AGENT_API_TOKEN --body "$TOKEN"
umask 077
printf '%s' "$TOKEN" > "$HOME/agent_api_token.txt"
unset TOKEN
echo "GitHub Actions secret created. Now add the matching Cloudflare Worker secret."
```

مقدار توکن را در چت نفرست.

## ۲. ثبت همان توکن در Cloudflare

در داشبورد Cloudflare به **Workers & Pages → rubika-agent → Settings → Variables and Secrets** برو و یک Secret با نام `AGENT_API_TOKEN` بساز. مقدار آن باید دقیقاً همان متنی باشد که با این دستور در Termux نمایش داده می‌شود:

```bash
cat "$HOME/agent_api_token.txt"
```

پس از ذخیرهٔ Secret در Cloudflare، فایل محلی را پاک کن:

```bash
rm -f "$HOME/agent_api_token.txt"
```

## ۳. اجرای یک تست

در Termux اجرا کن:

```bash
cd ~/rubika-agent-github
git pull --ff-only origin agent-brain-mega-v1
gh workflow run brain-e2e-dispatch.yml --ref agent-brain-mega-v1 -f platform=tiktok
gh run list --workflow brain-e2e-dispatch.yml --branch agent-brain-mega-v1 --limit 1
```

این تست در صورت موفقیت مغز، یک ویدئو را در کانال روبیکا منتشر می‌کند. گزارش مرحلهٔ اجرا با نام `brain-e2e-report` به‌عنوان GitHub Actions Artifact ذخیره می‌شود.

## وضعیت آماده‌سازی زیرساخت

- D1 دیتابیس `rubika-agent-db` دارای migrationهای `0001`، `0002` و `0003` است.
- Bindingهای Worker شامل `DB` و `AI` هستند.
- Secretهای `RUBIKA_TOKEN`، `MEGA_EMAIL` و `MEGA_PASSWORD` باید در GitHub Actions موجود باشند؛ تست‌های قبلی پروژه نشان داده‌اند که ارسال به روبیکا و آپلود/دانلود آزمایشی MEGA کار می‌کنند.
- اگر خروجی محتوا را رد کند یا یکی از بررسی‌ها نامطمئن باشد، ویدئو ارسال نمی‌شود و گزارش دلیل توقف را ثبت می‌کند.
- برای YouTube هنوز Relay دانلود جداگانه لازم است؛ اولین تست را با TikTok اجرا کن.
