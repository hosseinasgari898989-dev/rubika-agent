/* Dedicated TikTok owner-console pages. Loaded after window.RC is initialized. */
(()=>{"use strict";
const R=window.RC;
if(!R||!R.V||!R.A)return;
const {V,A,api,card,tbl,E,N,fmt,toast,NAV,val}=R;
const inFlight=new Set();
let unsentRows=[];

const sourceStatus=row=>Number(row.enabled)===1
  ? '<span class="bd ok">فعال</span>'
  : '<span class="bd mut">غیرفعال</span>';
const addedAt=row=>row.created_at
  ? fmt(row.created_at)
  : 'از فهرست اولیه';

V.tkaccounts=async()=>{
  const r=await api('/api/console/sources?platform=tiktok');
  const rows=r.items||[];
  const active=rows.filter(x=>Number(x.enabled)===1).length;
  const body='<div class="row" style="align-items:end">'+
    '<div style="flex:1;min-width:220px"><label for="tk_account">نام کاربری یا لینک پروفایل TikTok</label>'+
    '<input id="tk_account" dir="ltr" autocomplete="off" placeholder="@name یا https://www.tiktok.com/@name"></div>'+
    '<button class="btn" data-a="tkAdd">افزودن حساب</button></div>'+
    '<div class="s" style="margin-top:8px">برای حساب تازه، کشف فقط ویدئوهای منتشرشده از زمان افزودن حساب به بعد را می‌پذیرد. حذف حساب فقط کشف‌های بعدی را متوقف می‌کند؛ صف و تاریخچهٔ قبلی باقی می‌ماند.</div>'+
    '<div class="chips" style="margin-top:10px"><span class="chip">حساب فعال <b>'+active+'</b></span><span class="chip">کل ثبت‌شده <b>'+rows.length+'</b></span></div>';
  return card('افزودن حساب TikTok',body)+card('حساب‌های ثبت‌شده',tbl([
    ['حساب',x=>'<code dir="ltr">@'+E(x.source_key)+'</code>'],
    ['وضعیت',x=>sourceStatus(x)],
    ['شروع کشف',x=>E(addedAt(x))],
    ['آخرین بررسی',x=>E(fmt(x.last_checked_at))],
    ['',x=>Number(x.enabled)===1
      ? '<button class="btn r sm" data-a="tkRemove" data-id="'+E(x.id)+'" data-handle="'+E(x.source_key)+'">حذف حساب</button>'
      : '<span class="s">غیرفعال</span>']
  ],rows,'حسابی ثبت نشده است'));
};

A.tkAdd=async()=>{
  const value=val('tk_account').trim();
  if(!value)return toast('نام کاربری یا لینک پروفایل را وارد کن','bad');
  try{
    await api('/api/console/sources',{method:'POST',body:{platform:'tiktok',source_key:value}});
    toast('حساب اضافه شد؛ کشف از همین زمان شروع می‌شود','ok');
    R.go('tkaccounts');
  }catch(e){
    const messages={
      invalid_tiktok_account:'نام کاربری یا لینک پروفایل TikTok معتبر نیست',
      source_already_exists:'این حساب قبلاً ثبت شده است'
    };
    toast(messages[e.message]||('افزودن حساب ناموفق بود: '+e.message),'bad');
  }
};

A.tkRemove=async el=>{
  const id=String(el.dataset.id||''),handle=String(el.dataset.handle||'');
  if(!id.startsWith('tiktok:'))return toast('شناسهٔ حساب معتبر نیست','bad');
  if(!confirm('حساب @'+handle+' از کشف‌های بعدی حذف شود؟ آیتم‌ها، صف، ledger و تاریخچهٔ قبلی پاک نمی‌شوند.'))return;
  try{
    await api('/api/console/sources/'+encodeURIComponent(id),{method:'DELETE'});
    toast('حساب غیرفعال شد؛ تاریخچه و آیتم‌های قبلی باقی ماندند','ok');
    R.go('tkaccounts');
  }catch(e){toast('حذف حساب ناموفق بود: '+e.message,'bad')}
};

const reasonFa=value=>{
  const key=String(value||'').toLowerCase();
  const map={
    rubika_upload_failed:'ارسال رسانه به روبیکا ناموفق بود',
    rubika_rejected_upload_failed:'روبیکا فایل را نپذیرفت',
    rubika_rejected_file_too_large:'حجم فایل از حد پذیرفته‌شده بیشتر بود',
    rubika_upload_server_error:'سرور بارگذاری روبیکا خطا داد',
    rubika_rate_limited:'روبیکا موقتاً محدودیت درخواست اعمال کرد',
    delivery_uncertain:'نتیجهٔ ارسال نامشخص ماند؛ بررسی رسید لازم است',
    delivery_receipt_missing:'رسید قطعی ارسال دریافت نشد',
    ambiguous_response:'پاسخ روبیکا برای تأیید ارسال کافی نبود',
    cloud_sync_failed:'ثبت وضعیت در دیتابیس ناموفق بود',
    failed_media_record_failed:'ثبت رسانهٔ ناموفق تکمیل نشد',
    failed_media_upload_failed:'ذخیرهٔ نسخهٔ اصلی در فضای ابری ناموفق بود',
    failed_media_internal_error:'هنگام نگه‌داری نسخهٔ اصلی خطا رخ داد'
  };
  if(map[key])return map[key];
  if(key.startsWith('transport_'))return 'ارتباط با سرویس ارسال قطع شد';
  if(key.startsWith('rubika_rejected_'))return 'روبیکا درخواست ارسال را رد کرد';
  return 'خطا در آماده‌سازی یا ارسال رسانه';
};
const sendState=row=>{
  if(Number(row.already_sent)===1)return '<span class="bd warn">ارسال‌شده / درحال بررسی</span>';
  if(row.queue_status==='queued')return '<span class="bd info">در صف</span>';
  if(row.can_resend===1||row.can_resend===true)return '<button class="btn sm" data-a="tkResend" data-id="'+E(row.id)+'">ارسال دوباره</button>';
  return '<span class="s">قابل ارسال مجدد نیست</span>';
};

V.tiktokUnsent=async()=>{
  const r=await api('/api/console/tiktok-unsent');
  unsentRows=r.items||[];
  return card('تیک‌تاک - ارسال‌نشده', '<div class="s">فقط موارد TikTok که ارسالشان ناموفق مانده یا به مقصد نرسیده‌اند. ارسال دوباره هر ردیف فقط همان آیتم را به صف موجود برمی‌گرداند؛ مقصد از تنظیم فعلی پردازشگر گرفته می‌شود. نتیجهٔ نامشخص یا ارسال‌شده دوباره صف نمی‌شود.</div>')+
    card('',tbl([
      ['حساب',x=>'<code dir="ltr">@'+E(String(x.account||'').replace(/^@/,''))+'</code>'],
      ['زمان',x=>E(fmt(x.created_at))],
      ['دلیل خطا',x=>E(reasonFa(x.reason))],
      ['وضعیت',x=>sendState(x)]
    ],unsentRows,'مورد ارسال‌نشده‌ای وجود ندارد.'));
};

A.tkResend=async el=>{
  const id=String(el.dataset.id||'');
  const row=unsentRows.find(x=>String(x.id)===id);
  if(!row||Number(row.already_sent)===1||!(row.can_resend===1||row.can_resend===true))return;
  if(inFlight.has(id))return;
  inFlight.add(id);
  el.disabled=true;
  el.textContent='در حال ثبت…';
  try{
    await api('/api/console/tiktok-unsent/'+encodeURIComponent(id)+'/resend',{method:'POST',body:{}});
    row.can_resend=0;
    row.queue_status='queued';
    el.outerHTML='<span class="bd info">در صف</span>';
    toast('همین آیتم به صف موجود برگشت؛ پردازش در نوبت خودکار انجام می‌شود','ok');
  }catch(e){
    if(e.message==='tiktok_item_already_sent_or_not_retryable'){
      row.can_resend=0;
      el.outerHTML='<span class="s">ارسال‌شده یا غیرقابل‌ارسال مجدد</span>';
      toast('این آیتم قبلاً ارسال شده یا در وضعیت امن برای تکرار نیست؛ دوباره ثبت نشد','bad');
    }else{
      el.disabled=false;
      el.textContent='ارسال دوباره';
      toast('ثبت تلاش مجدد ناموفق بود: '+e.message,'bad');
    }
  }finally{inFlight.delete(id)}
};

if(!NAV.some(x=>x[0]==='tkaccounts'))NAV.splice(Math.max(0,NAV.findIndex(x=>x[0]==='src')+1),0,['tkaccounts','حساب‌های TikTok','♧']);
if(!NAV.some(x=>x[0]==='tiktok-unsent'))NAV.splice(Math.max(0,NAV.findIndex(x=>x[0]==='unsent')+1),0,['tiktok-unsent','تیک‌تاک - ارسال‌نشده','↻']);
document.getElementById('nav').innerHTML=NAV.map(n=>'<button class="nv" data-id="'+E(n[0])+'" data-a="go"><span>'+n[2]+'</span>'+n[1]+'</button>').join('');
R.go(R.cur);
})();