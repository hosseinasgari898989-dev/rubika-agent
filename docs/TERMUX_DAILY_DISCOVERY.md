# اجرای روزانهٔ جمع‌آوری اطلاعات از Termux

## هدف

گوشی فقط اطلاعات عمومی و URL استاندارد را جمع می‌کند. این فرمان ویدیو دانلود نمی‌کند و چیزی در روبیکا منتشر نمی‌کند. فایل JSON حاصل به شاخهٔ آزمایشی agent-brain-mega-v1 ارسال می‌شود و GitHub Actions همان فایل را بررسی می‌کند.

هیچ Apify، Apiserpent، Tavily، کلید API پولی، کوکی ورود یا روش دورزدن چالش در مسیر پیش‌فرض این نسخه استفاده نمی‌شود.

## اطلاعاتی که جمع می‌شود

- TikTok: حداکثر چهار صفحهٔ عمومی هشتگ؛ URL، شناسه و هر کپشن/تاریخ/آمار عمومی که در HTML وجود داشته باشد.
- Instagram: حداکثر سه صفحهٔ عمومی هشتگ و حداکثر دو Reel؛ اگر صفحه فقط دیوار ورود، صفحهٔ چالش یا HTML بدون داده برگرداند، نتیجه به‌درستی صفر ثبت می‌شود.
- YouTube: چهار جست‌وجوی yt-dlp در حالت metadata-only؛ شناسه، URL، عنوان، کانال، مدت، تاریخ و آمار در صورت دسترس‌بودن.

این محدودیت‌ها فقط تعداد درخواست‌های خود برنامه را کم می‌کنند؛ خود TikTok، Instagram و YouTube همچنان می‌توانند درخواست‌ها را محدود کنند. اجرای درخواست‌ها از گوشی احتمالاً با runner سرور تفاوت دارد، اما تضمین نمی‌کند Instagram یا TikTok حتماً نتیجه برگردانند. هیچ تلاش برای ورود خودکار یا دورزدن چالش انجام نمی‌شود.

## آماده‌سازی یک‌باره

از Termux (نه داخل proot، مگر اینکه مخزن خودت آنجاست) استفاده کن.

اگر مخزن ~/rubika-agent از قبل وجود دارد:

    pkg update
    pkg install -y git python gh ffmpeg
    cd ~/rubika-agent
    git fetch origin
    git switch agent-brain-mega-v1
    git pull --ff-only origin agent-brain-mega-v1
    python -m venv .venv
    source .venv/bin/activate
    python -m pip install --upgrade pip
    python -m pip install requests yt-dlp==2026.08.19
    gh auth login
    gh auth setup-git

اگر هنوز مخزن را در Termux نداری، ابتدا این را اجرا کن و سپس مراحل نصب Python بالا را اجرا کن:

    pkg update
    pkg install -y git python gh ffmpeg
    git clone --branch agent-brain-mega-v1 https://github.com/hosseinasgari898989-dev/rubika-agent.git ~/rubika-agent
    cd ~/rubika-agent

در gh auth login مرورگر را باز کن و وارد حساب GitHub خودت شو. هیچ توکن یا رمز عبوری را در چت یا فایل کد قرار نده.

اگر پوشهٔ .venv از قبل وجود دارد، به‌جای ساخت دوباره فقط آن را فعال کن:

    cd ~/rubika-agent
    source .venv/bin/activate

## فرمان هر روز

    cd ~/rubika-agent && source .venv/bin/activate && git switch agent-brain-mega-v1 && git pull --ff-only origin agent-brain-mega-v1 && python -m termux.daily_discovery --push

فرمان بالا فقط artifacts/termux_discovery.json را commit و به شاخهٔ آزمایشی push می‌کند. مخزن باید روی شاخهٔ آزمایشی باشد، و هیچ فایل تغییریافتهٔ دیگری از قبل نباید stage شده باشد. اگر گزارش تغییری نکرده باشد، برنامه لازم نیست commit جدید بسازد.

پس از push، workflow تشخیصی اجرا می‌شود. سبز یا قرمز بودن آن بر اساس رسیدن به تعداد هدفِ نامزدهای معتبر است؛ سبز بودن به‌تنهایی به معنی دانلود موفق یا آمادگی انتشار نیست. فعلاً انتشار تولیدی خاموش می‌ماند.

## بررسی خروجی

فایل محلی خروجی:

    artifacts/termux_discovery.json

شکل گزارش شامل summary, candidates و attempts است. نتیجهٔ no_public_video_links، login_wall_or_challenge، http_403، http_429 یا metadata_search_failed را به‌عنوان شکست واقعی همان منبع در نظر بگیر؛ ربات لینک خیالی نمی‌سازد.

## حریم خصوصی و دسترسی

مخزن فعلاً عمومی است؛ بنابراین گزارش JSON و اطلاعات عمومی لینک‌ها در آن قابل مشاهده می‌شود. هیچ توکن، کوکی یا فایل ویدیویی در گزارش ثبت نمی‌شود. اگر نمی‌خواهی گزارش متادیتا به‌صورت عمومی commit شود، push را اجرا نکن تا روش انتقال خصوصی جداگانه‌ای اضافه شود.
