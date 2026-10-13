# جمع‌آوری روزانهٔ اطلاعات از Termux

## این مرحله چه می‌کند؟
گوشی از صفحات عمومی TikTok و Instagram و جست‌وجوی metadata-only در YouTube اطلاعات قابل‌دسترسی و URL استاندارد را جمع می‌کند. سپس فقط گزارش JSON را به شاخهٔ آزمایشی GitHub می‌فرستد. در این مرحله **هیچ ویدیویی دانلود نمی‌شود و چیزی در روبیکا منتشر نمی‌شود**.

هیچ Apify، سرویس پولی، کلید API پولی، کوکی ورود، پروکسی یا روش دورزدن challenge در مسیر روزانه استفاده نمی‌شود.

- TikTok: حداکثر ۴ صفحهٔ عمومی هشتگ؛ شناسه و URL به‌علاوهٔ کپشن، تاریخ و آمار اگر در HTML در دسترس باشند.
- Instagram: حداکثر ۳ صفحهٔ عمومی هشتگ و حداکثر ۲ Reel. اگر صفحه دیوار ورود/challenge یا HTML بدون داده برگرداند، صفر نتیجه ثبت می‌شود؛ نتیجهٔ ساختگی تولید نمی‌شود.
- YouTube: چهار جست‌وجوی yt-dlp در حالت metadata-only؛ شناسه، URL، عنوان، کانال، مدت، تاریخ و آمار در صورت دسترس‌بودن.

این محدودیت‌ها تعداد درخواست‌های خود برنامه را کاهش می‌دهند اما سهمیهٔ نامحدود ایجاد نمی‌کنند؛ پلتفرم‌ها همچنان می‌توانند درخواست‌ها را محدود کنند. اجرای روی گوشی با runner ابری فرق دارد، ولی تضمین نمی‌کند که TikTok یا Instagram حتماً نتیجه بدهند. روش جدید Instagram باید در اولین اجرای زنده آزمایش شود؛ اگر آن هم به challenge خورد، همان راه را پشت‌سرهم تکرار نمی‌کنیم.

## راه‌اندازی یک‌باره

### اگر مخزن در گوشی وجود دارد
در Termux:
    pkg update
    pkg install -y git python gh ffmpeg
    cd ~/rubika-agent
    git fetch origin
    git switch agent-brain-mega-v1 || git switch --track origin/agent-brain-mega-v1
    git pull --ff-only origin agent-brain-mega-v1
    bash termux/bootstrap.sh

### اگر مخزن هنوز کلون نشده
در Termux:
    pkg update
    pkg install -y git python gh ffmpeg
    git clone --branch agent-brain-mega-v1 https://github.com/hosseinasgari898989-dev/rubika-agent.git ~/rubika-agent
    cd ~/rubika-agent
    bash termux/bootstrap.sh

اسکریپت نصب فقط یک محیط مجازی .venv و دو وابستگی لازم برای کشف metadata را آماده می‌کند؛ به نصب همهٔ وابستگی‌های backend نیاز نیست. این مسیر به توکن Apify یا رمز/کوکی Instagram نیاز ندارد.

### ورود یک‌باره به GitHub
گزارش باید به مخزن خودت push شود. یک‌بار این دو فرمان را اجرا کن و مراحل ورود را در صفحهٔ رسمی GitHub کامل کن:
    gh auth login
    gh auth setup-git

توکن یا رمز GitHub را در چت، گزارش JSON یا فایل کد قرار نده.

## فرمان هر روز
از ریشهٔ ~/rubika-agent فقط این فرمان را اجرا کن:
    bash termux/daily.sh

این اسکریپت شاخهٔ آزمایشی را به‌صورت fast-forward-only به‌روز می‌کند، سپس collector را اجرا می‌کند و فقط artifacts/termux_discovery.json را به agent-brain-mega-v1 می‌فرستد. اگر branch یا remote اشتباه باشد، یا Git امن به‌روز نشود، متوقف می‌شود. اگر فایل دیگری از قبل stage شده باشد، push گزارش هم متوقف می‌شود تا تغییرات نامربوط وارد commit نشود.

## خروجی و معنی آن
فایل گزارش:
    artifacts/termux_discovery.json

گزارش شامل summary، candidates و attempts است. نتیجه‌هایی مثل no_public_video_links، login_wall_or_challenge، http_403، http_429 یا metadata_search_failed شکست همان منبع را ثبت می‌کنند، نه موفقیت جعلی.

پس از push، GitHub Actions تعداد نامزدهای معتبر را بررسی می‌کند. اگر نتایج TikTok/YouTube به هدف نرسند، workflow باید قرمز شود تا شکست پنهان نماند. سبز بودن workflow نیز دانلود سالم، مجوز بازنشر یا آمادگی انتشار را ثابت نمی‌کند.

## حریم خصوصی و محدودیت‌های فعلی
مخزن GitHub فعلاً عمومی است؛ بنابراین گزارش JSON و اطلاعات عمومی و URLها در مخزن قابل‌مشاهده می‌شوند. هیچ توکن، کوکی یا فایل ویدیویی در گزارش ذخیره نمی‌شود. برای حفظ حریم خصوصی گزارش را به مخزن push نکن؛ روش انتقال خصوصی باید جداگانه طراحی شود.

این مرحله فقط جمع‌آوری metadata را آزمایش می‌کند. دانلود، بررسی واقعی رسانه، تکراری‌یابی محتوا، ثبت D1/MEGA و انتشار روبیکا مراحل جداگانه‌اند و تا زمانی که تست سرتاسری موفق نشود، انتشار تولیدی خاموش می‌ماند.
