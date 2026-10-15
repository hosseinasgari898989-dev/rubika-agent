# نصب Rubika-Agent در Termux

این راهنما، جمع‌آوری‌کنندهٔ سبکِ گوشی را نصب می‌کند. این بخش فقط فرادادهٔ عمومی و لینک‌ها را جمع می‌کند؛ خودش ویدئو دانلود نمی‌کند و در روبیکا پست نمی‌گذارد. اجرای کاملِ دانلود تا انتشار هنوز نیازمند تأییدهای جداگانه است.

## اگر مخزن را قبلاً در گوشی داری

وارد پوشهٔ مخزن خودت شو؛ اگر اسم پوشه همان `rubika-agent-github` است:

```bash
cd ~/rubika-agent-github
git fetch origin agent-brain-mega-v1
git switch agent-brain-mega-v1 2>/dev/null || git switch --track origin/agent-brain-mega-v1
git pull --ff-only origin agent-brain-mega-v1
bash termux/bootstrap.sh
```

اگر مسیر مخزنت متفاوت است، فقط خط `cd` را متناسب با مسیر خودت تغییر بده.

## اگر مخزن هنوز نصب نشده

```bash
pkg update -y
pkg install -y git python gh ffmpeg
git clone --branch agent-brain-mega-v1 https://github.com/hosseinasgari898989-dev/rubika-agent.git ~/rubika-agent-github
cd ~/rubika-agent-github
bash termux/bootstrap.sh
```

## ورود یک‌باره به GitHub

اگر اسکریپت گفت GitHub CLI هنوز وارد حساب نشده، این دو فرمان را اجرا کن و دستورهای روی صفحه را کامل کن:

```bash
gh auth login
gh auth setup-git
```

توکن یا رمز عبورت را داخل چت نفرست.

## اجرای جمع‌آوری اطلاعات

هر بار که خواستی گزارش تازه بسازی و فقط گزارش را به شاخهٔ آزمایشی بفرستی:

```bash
cd ~/rubika-agent-github
bash termux/daily.sh
```

این فرمان باید روی شاخهٔ `agent-brain-mega-v1` اجرا شود. اگر گزارش یا GitHub به مشکل بخورد، خروجی خطا را بررسی کن؛ ادیتور ویدئو یا انتشار روبیکا با این فرمان اجرا نمی‌شوند.

## چه چیزهایی هنوز فعال نشده‌اند؟

- اجرای این فرمان، سرویس دائمی پس‌زمینه در گوشی نصب نمی‌کند؛ آن را هنگام نیاز اجرا کن.
- زمان‌بندی کشف ابری از GitHub Actions جداست و به secrets/تنظیمات موجود مخزن نیاز دارد.
- دریافت/بررسی رسانه، اتصال سرتاسری MEGA و D1، و انتشار واقعی هنوز نباید نهایی فرض شوند.
- وضعیت عامل در `config/agent_lifecycle.json` روی `test` بماند؛ به `production` تغییرش نده تا آزمایش‌های سرتاسری و مجوز انتشار جداگانه تأیید شوند.
- فایل `docs/END_TO_END_SMOKE.md` را فعلاً اجرا نکن؛ workflow آن در صورت باز بودن دروازهٔ انتشار می‌تواند یک رسانه را به کانال آزمایشی روبیکا بفرستد.

برای اجرای روزمره، تنها فرمان لازم پس از نصب و ورود یک‌باره، `bash termux/daily.sh` است.
