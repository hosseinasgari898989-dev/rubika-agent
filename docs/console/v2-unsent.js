/* Web console v2: لیست «ارسال‌نشده‌ها» (failed_media). Needs window.RC and runs AFTER v2-bulk.js.
   Uses POST /api/console/failed-media/list and /failed-media/delete-request (src/console_failed_media.js).
   Deleting only REQUESTS deletion: the Worker marks rows and starts the delete workflow (mega-rm first, row after). */
(()=>{"use strict";
const R=window.RC;if(!R||!R.V||!R.V.jobs)return;
const {V,A,api,card,tbl,badge,E,N,fmt,toast,NAV}=R;
if(R.ST)Object.assign(R.ST,{stored:'ذخیره‌شده',delete_requested:'در انتظار حذف',delete_failed:'حذف ناموفق'});
if(R.BC)Object.assign(R.BC,{stored:'info',delete_requested:'warn',delete_failed:'bad'});
const SEL=new Set();
const deletable=x=>x.status==='stored'||x.status==='delete_failed';
const cnt=()=>{const e=document.getElementById('fm_cnt');if(e)e.textContent=String(SEL.size)};
A.fmTog=el=>{const id=el.dataset.id;if(el.checked)SEL.add(id);else SEL.delete(id);cnt()};
A.fmAll=()=>{const bs=[...document.querySelectorAll('input[data-a="fmTog"]')];
const on=bs.some(b=>!b.checked);bs.forEach(b=>{b.checked=on;if(on)SEL.add(b.dataset.id);else SEL.delete(b.dataset.id)});cnt()};
const mb=n=>n==null?'—':(Number(n)/1048576).toFixed(1)+' MB';
const link=x=>x.share_url&&/^https:\/\/mega\.(nz|io)\//.test(x.share_url)
?`<a href="${E(x.share_url)}" target="_blank" rel="noopener noreferrer">دانلود</a>`:'<span class="s">بدون لینک</span>';

V.unsent=async()=>{
let r;
try{r=await api('/api/console/failed-media/list',{method:'POST',body:{limit:100}})}
catch(e){return card('ارسال‌نشده‌ها',`<div class="empty">${E(e.message)}</div>`)}
const items=r.items||[];
for(const id of [...SEL])if(!items.some(x=>x.id===id&&deletable(x)))SEL.delete(id);
const more=N(r.total)>items.length?`<div class="s">فقط ${N(items.length)} مورد اخیر از ${N(r.total)} نمایش داده می‌شود.</div>`:'';
const bar=`<div class="row" style="margin:8px 0"><button class="btn g sm" data-a="fmAll">انتخاب همه / هیچ</button><span class="s">انتخاب‌شده: <b id="fm_cnt">${SEL.size}</b></span><button class="btn sm" data-a="fmDel">🗑 حذف انتخاب‌شده‌ها</button><button class="btn g sm" data-a="fmDelAll">🗑 حذف همه</button><button class="btn g sm" data-a="reload">↻ تازه‌سازی</button></div>`;
return card('ارسال‌نشده‌ها · نسخهٔ اصلی در Mega',`<div class="s">ویدیو/عکس‌هایی که ارسالشان به روبیکا خطا داد و نسخهٔ اصلی‌شان نگه‌داری شده. «دانلود» چیزی را حذف نمی‌کند. «حذف» برگشت‌ناپذیر است (فایل Mega و ردیف دیتابیس) و چند دقیقه طول می‌کشد؛ تا آن موقع وضعیت «در انتظار حذف» است.</div>`)
+card('',bar+more+tbl([
['',x=>deletable(x)?`<input type="checkbox" data-a="fmTog" data-id="${E(x.id)}" ${SEL.has(x.id)?'checked':''}>`:''],
['منبع',x=>E(x.source)],['نوع',x=>E(x.media_type)],
['کپشن',x=>E(String(x.caption||'').slice(0,160)||'—')],
['دلیل',x=>`<code>${E(x.reason)}</code>`],['حجم',x=>mb(x.size_bytes)],
['وضعیت',x=>badge(x.status)],['ثبت',x=>fmt(x.created_at)],['فایل',link]
],items,'موردی نیست'))};

const ask=async(body,label)=>{
try{const r=await api('/api/console/failed-media/delete-request',{method:'POST',body});
SEL.clear();
toast(label+N(r.requested)+(r.requested&&!r.dispatched?' · شروع خودکار حذف انجام نشد':''),r.requested&&r.dispatched?'ok':'bad');
R.go('unsent')}catch(e){toast('خطا: '+e.message,'bad')}};
A.fmDel=async()=>{const ids=[...SEL];
if(!ids.length)return toast('چیزی انتخاب نشده','bad');
if(!confirm('فایل اصلی و کپشنِ '+ids.length+' مورد برای همیشه حذف می‌شود. ادامه؟'))return;
await ask({ids},'درخواست حذف: ')};
A.fmDelAll=async()=>{
if(!confirm('همهٔ موردهای ارسال‌نشده برای همیشه حذف شوند؟'))return;
if(prompt('برای تأیید نهایی کلمهٔ DELETE را بنویس')!=='DELETE')return toast('لغو شد؛ چیزی حذف نشد','bad');
await ask({all:true,confirm:'DELETE_ALL'},'درخواست حذف همه: ')};

if(!NAV.some(n=>n[0]==='unsent'))NAV.splice(Math.min(4,NAV.length),0,['unsent','ارسال‌نشده‌ها','📦']);
document.getElementById('nav').innerHTML=NAV.map(n=>`<button class="nv" data-id="${n[0]}" data-a="go"><span>${n[2]}</span>${n[1]}</button>`).join('');
R.go(R.cur);
})();
