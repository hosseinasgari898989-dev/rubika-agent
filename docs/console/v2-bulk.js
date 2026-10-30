/* Web console v2 bulk extension (2026-10-30). Needs window.RC from v2.html and runs AFTER v2-ext.js.
   Adds multi-select for saved X links and failed Mob X jobs, plus selective/bulk retry.
   Uses POST /api/console/links/bulk and POST /api/console/jobs/retry (src/console_retry.js). */
(()=>{"use strict";
const R=window.RC;if(!R||!R.V||!R.V.inst||!R.V.jobs)return;
const {V,A,api,safe,card,tbl,opts,badge,E,N,val,fmt,cut,toast,modal}=R;
const SEL={l:new Set(),j:new Set()};
const cnt=k=>{const e=document.getElementById('sel_'+k);if(e)e.textContent=String(SEL[k].size)};
const box=(k,id,on)=>`<input type="checkbox" data-a="selTog" data-k="${k}" data-id="${E(String(id))}" ${SEL[k].has(String(id))?'checked':''}>`;
A.selTog=el=>{const k=el.dataset.k,id=el.dataset.id;if(el.checked)SEL[k].add(id);else SEL[k].delete(id);cnt(k)};
A.selAll=el=>{const k=el.dataset.k,bs=[...document.querySelectorAll('input[data-a="selTog"][data-k="'+k+'"]')];
const on=bs.some(b=>!b.checked);bs.forEach(b=>{b.checked=on;if(on)SEL[k].add(b.dataset.id);else SEL[k].delete(b.dataset.id)});cnt(k)};
const bar=(k,btns)=>`<div class="row" style="margin:8px 0"><button class="btn g sm" data-a="selAll" data-k="${k}">انتخاب همه / هیچ</button><span class="s">انتخاب‌شده: <b id="sel_${k}">${SEL[k].size}</b></span>${btns}</div>`;

/* ---------- حالت فوری: انتخاب چندتایی لینک‌ها ---------- */
const INS={link:null};
V.inst=async()=>{
const [l,j]=await Promise.all([safe('/api/console/links'),safe('/api/console/jobs?limit=100')]);
const jobBy={};for(const x of (j.items||[]))if(x.panel_link_id!=null)jobBy[x.panel_link_id]=x;
const act=x=>{if(x.status==='pending')return `<button class="btn sm" data-a="insOk" data-id="${x.id}">تأیید</button> <button class="btn g sm" data-a="insNo" data-id="${x.id}">لغو</button>`;const jb=jobBy[x.id];return jb?`<button class="btn g sm" data-a="jView" data-id="${E(jb.id)}">جزئیات کار</button>`:'—'};
const step2=INS.link?card('مرحلهٔ ۲ · تأیید',`<div class="s">این لینک است؟</div><div style="margin:8px 0"><code>${E(INS.link.url)}</code></div><div class="row"><button class="btn" data-a="insOk" data-id="${N(INS.link.id)}">✅ تأیید و شروع</button><button class="btn g" data-a="insNo" data-id="${N(INS.link.id)}">✖ لغو</button></div>`):'';
const lb=bar('l',`<button class="btn sm" data-a="lkBulk" data-act="confirm">تأیید انتخاب‌شده‌ها</button><button class="btn g sm" data-a="lkBulk" data-act="cancel">لغو انتخاب‌شده‌ها</button><button class="btn g sm" data-a="lkBulk" data-act="delete">🗑 حذف غیرفعال‌های انتخابی</button>`);
return card('حالت فوری · فقط کانال تست',`<div class="s">فقط پست رسمی <code>x.com/mobgamesstudios/status/…</code>. ذخیره → تأیید → پردازش → پیش‌نمایش در PV → تأیید شما در PV → ارسال به کانال تست (اخبار Mob مشمول ساعت سکوت و سقف روزانه نیست). انتشار در کانال اصلی وجود ندارد.</div><div class="row" style="margin-top:10px"><div><label>مرحلهٔ ۱ · لینک پست</label><input id="ins_u" dir="ltr" placeholder="https://x.com/mobgamesstudios/status/…"></div><button class="btn" data-a="insAdd">ذخیره و بررسی</button></div>`)
+step2
+card('لینک‌های اخیر',l.ok===false?`<div class="empty">${E(l.error)}</div>`:lb+tbl([['',x=>box('l',x.id)],['لینک',x=>`<code>${E(x.url)}</code>`],['وضعیت لینک',x=>badge(x.status)],['وضعیت کار',x=>jobBy[x.id]?badge(jobBy[x.id].status):'—'],['ذخیره',x=>fmt(x.created_at)],['',act]],l.items||[],'لینکی نیست'))};
A.lkBulk=async el=>{const a=el.dataset.act,ids=[...SEL.l].map(Number);
if(!ids.length)return toast('چیزی انتخاب نشده','bad');
if(a==='delete'&&!confirm('فقط لینک‌های غیرفعالِ انتخاب‌شده حذف می‌شوند؛ ادامه؟'))return;
try{const r=await api('/api/console/links/bulk',{method:'POST',body:a==='delete'?{action:a,ids,confirm:true}:{action:a,ids}});
SEL.l.clear();toast('انجام‌شده: '+N(r.done)+' · ردشده: '+N(r.skipped),r.done?'ok':'bad');R.go('inst')}
catch(e){toast('خطا: '+e.message,'bad')}};

/* ---------- کارها: تلاش مجدد ناموفق‌ها ---------- */
const JT=[['queue_intake_dry_run','بررسی آزمایشی ورود به صف'],['ai_health_check','بررسی سلامت AI'],['log_zip','فشرده‌سازی لاگ‌ها'],['d1_backup','بکاپ دیتابیس'],['requeue_stuck','بازگرداندن گیرکرده‌ها']];
const JL=Object.fromEntries(JT.concat([['mob_x_link','لینک X خبر Mob']]));
V.jobs=async()=>{
const [r,l]=await Promise.all([api('/api/console/jobs?limit=100'),safe('/api/console/links')]);
const linkBy={};for(const x of (l.items||[]))linkBy[x.id]=x;
const retryable=x=>x.type==='mob_x_link'&&x.status==='failed'&&x.panel_link_id!=null&&linkBy[x.panel_link_id]&&linkBy[x.panel_link_id].status==='failed';
const nFail=(r.items||[]).filter(retryable).length;
const jb=bar('j',`<button class="btn sm" data-a="jbRetry">🔁 تلاش مجدد انتخاب‌شده‌ها</button><button class="btn g sm" data-a="jbRetry" data-all="1">🔁 همهٔ ناموفق‌ها (${nFail})</button>`);
return card('ثبت کار جدید',`<div class="row"><div><label>نوع کار</label><select id="j_t">${opts(JT)}</select></div><button class="btn" data-a="jAdd">ثبت در صف</button><button class="btn g" data-a="reload">↻ تازه‌سازی</button></div><div class="s">فقط رکورد ثبت می‌شود؛ موتور اجرای کارها هنوز وجود ندارد (وضعیت «در انتظار» یعنی اجرا نشده). کار لینک X از «حالت فوری» ساخته می‌شود. «تلاش مجدد» فقط کارهای ناموفقِ لینک X را به صف برمی‌گرداند و تا ۵ دقیقه دیگر پردازش می‌شود.</div>`)
+card('',jb+tbl([['',x=>retryable(x)?box('j',x.id):''],['شناسه',x=>`<code>${cut(x.id,16)}</code>`],['نوع',x=>E(JL[x.type]||x.type)],['وضعیت',x=>badge(x.status)],['پیشرفت',x=>`<div class="bar" style="min-width:90px"><i data-w="${N(x.progress)}"></i></div>`],['ثبت',x=>fmt(x.created_at)],['خطا',x=>E(x.error||'—')],['',x=>`<button class="btn g sm" data-a="jView" data-id="${E(x.id)}">جزئیات</button>`]],r.items||[]))};
A.jbRetry=async el=>{const all=el.dataset.all==='1';let body;
if(all){if(!confirm('همهٔ کارهای ناموفق لینک X دوباره در صف قرار بگیرند؟'))return;body={all_failed:true}}
else{const ids=[...SEL.j];if(!ids.length)return toast('کار ناموفقی انتخاب نشده','bad');body={job_ids:ids}}
try{const r=await api('/api/console/jobs/retry',{method:'POST',body});
SEL.j.clear();toast('به صف برگشت: '+N(r.retried)+' · ردشده: '+N(r.skipped),r.retried?'ok':'bad');R.go('jobs')}
catch(e){toast('خطا: '+e.message,'bad')}};
R.go(R.cur);
})();
