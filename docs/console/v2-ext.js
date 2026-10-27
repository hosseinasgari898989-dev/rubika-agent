/* Web console v2 extension (2026-10-27). Needs window.RC exposed by v2.html.
   Source of truth for the inline <script id="rc-ext"> block in v2.html. Uses only existing /api/console/* endpoints. */
(()=>{"use strict";
const R=window.RC;if(!R)return;
const {V,A,api,safe,card,tbl,opts,badge,E,N,val,fmt,cut,toast,modal,closeModal,NAV,ST,BC}=R;
Object.assign(ST,{confirmed:'تأییدشده توسط مالک',ready_for_review:'آماده برای بازبینی'});
Object.assign(BC,{confirmed:'info',ready_for_review:'warn'});
const toTeh=t=>{const m=/^(\d\d):(\d\d)$/.exec(t||'');if(!m)return '—';const x=(+m[1]*60+ +m[2]+210)%1440;return String(Math.floor(x/60)).padStart(2,'0')+':'+String(x%60).padStart(2,'0')};

/* ---------- قوانین / تنظیمات (هم‌قرارداد با PV) ---------- */
const RF=[['daily_success_cap','سقف موفق روزانه',0,50],['daily_edit_count','تعداد ویرایش روزانه',0,50],['mob_posts_per_run','پست Mob در هر اجرا',0,10],['freshness_hours','تازگی (ساعت)',1,168],['ai_daily_limit_groq','سهمیهٔ Groq',0,5000],['ai_daily_limit_openrouter','سهمیهٔ OpenRouter',0,5000],['caption_max_chars','حداکثر نویسهٔ کپشن',50,4000]];
let RU={version:0,rules:{}};
V.rules=async()=>{
const [r,ch]=await Promise.all([api('/api/console/rules'),safe('/api/console/channels')]);
RU={version:r.version,rules:r.rules||{}};const x=RU.rules,q=x.quiet_hours||{},qs=q.start||'00:30',qe=q.end||'07:00',dg=x.digest_window_utc||'07:05';
const chs=ch.items||[],test=chs.filter(c=>c.role==='test'&&N(c.enabled)),prod=chs.filter(c=>c.role==='production');
const num=f=>`<div><label>${f[1]}</label><input type="number" id="r_${f[0]}" min="${f[2]}" max="${f[3]}" value="${N(x[f[0]])}"></div>`;
const quick=(f,l)=>`<div class="row" style="margin-top:8px">${l.map(v=>`<button class="btn g sm" data-a="pSet" data-f="${f}" data-v="${v}">${v}</button>`).join('')}</div>`;
return card(`تنظیمات اصلی · نسخهٔ ${N(r.version)}`,`<div class="grid g2"><div>${num(RF[0])}${quick(RF[0][0],[4,7,10])}</div><div>${num(RF[1])}${quick(RF[1][0],[4,6,10])}</div></div><div class="s" style="margin-top:10px">مقدار فقط با دکمهٔ «ذخیره» اعمال می‌شود. شمارش روزانه به UTC است.</div>`)
+card('ساعت سکوت (فقط نمایش؛ تغییر با مجوز مالک)',`<div class="chips"><div class="chip">UTC <b>${E(qs)}</b> تا <b>${E(qe)}</b></div><div class="chip">تهران <b>${toTeh(qs)}</b> تا <b>${toTeh(qe)}</b></div>${q.enabled===false?'<span class="bd warn">غیرفعال</span>':'<span class="bd ok">فعال</span>'}</div><div class="s">تهران = UTC+۳:۳۰ ثابت. پنجرهٔ خلاصه: ${E(dg)} UTC (${toTeh(dg)} تهران).</div>`)
+card('مقصد انتشار',`<div class="chips"><div class="chip"><b>TEST</b> ${test.length?E(test.map(c=>c.label).join('، ')):'<span class="mut">کانال فعالی نیست</span>'}<span class="bd ok">فعال</span></div><div class="chip"><b>MAIN</b><span class="bd bad lock">قفل</span></div></div><div class="s">کانال اصلی قفل است و فقط با تأیید روشن مالک باز می‌شود؛ از این صفحه قابل تغییر نیست (${prod.length?prod.length+' کانال اصلی ثبت‌شده، غیرفعال':'کانال اصلی ثبت نشده'}).</div>`)
+card('محدودیت‌ها و ترجمه',`<div class="grid g4">${RF.slice(2).map(num).join('')}</div><div class="grid g4"><div><label>ترتیب AI</label><select id="r_ord">${opts([['groq,openrouter','Groq سپس OpenRouter'],['openrouter,groq','OpenRouter سپس Groq'],['groq','فقط Groq'],['openrouter','فقط OpenRouter']],(x.ai_provider_order||[]).join(','))}</select></div></div><label>شروع اخبار Mob (ISO، خالی = بدون محدودیت)</label><input id="r_mob" dir="ltr" value="${E(x.mob_since_cutoff||'')}"><label><input type="checkbox" id="r_tr" ${x.translate_enabled?'checked':''}> ترجمهٔ خودکار فعال باشد</label><div class="row" style="margin-top:14px"><span class="bd warn lock">انتشار: غیرفعال (قفل)</span><span class="bd info lock">فقط کانال تست (قفل)</span></div><div class="row" style="margin-top:16px"><button class="btn" data-a="rSave">ذخیره</button></div>`)};
A.pSet=el=>{document.getElementById('r_'+el.dataset.f).value=el.dataset.v};
A.rSave=async()=>{const rules={...RU.rules};
for(const f of RF){const v=Number(val('r_'+f[0]));if(!Number.isInteger(v)||v<f[2]||v>f[3])return toast('«'+f[1]+'» باید عددی بین '+f[2]+' و '+f[3]+' باشد','bad');rules[f[0]]=v}
rules.ai_provider_order=val('r_ord').split(',');rules.mob_since_cutoff=val('r_mob').trim();rules.translate_enabled=document.getElementById('r_tr').checked;
try{await api('/api/console/rules',{method:'PUT',body:{base_version:RU.version,rules}});toast('ذخیره شد','ok')}catch(e){toast(e.message==='version_conflict'?'قوانین هم‌زمان تغییر کرده؛ دوباره بارگذاری شد':'خطا: '+e.message,'bad')}
R.go(R.cur)};

/* ---------- حالت فوری (فقط لینک رسمی Mob X؛ فقط کانال تست) ---------- */
const INS={link:null};
V.inst=async()=>{
const [l,j]=await Promise.all([safe('/api/console/links'),safe('/api/console/jobs?limit=100')]);
const jobBy={};for(const x of (j.items||[]))if(x.panel_link_id!=null)jobBy[x.panel_link_id]=x;
const step2=INS.link?card('مرحلهٔ ۲ · تأیید',`<div class="s">این لینک است؟</div><div style="margin:8px 0"><code>${E(INS.link.url)}</code></div><div class="row"><button class="btn" data-a="insOk" data-id="${N(INS.link.id)}">✅ تأیید و شروع</button><button class="btn g" data-a="insNo" data-id="${N(INS.link.id)}">✖ لغو</button></div>`):'';
const act=x=>{if(x.status==='pending')return `<button class="btn sm" data-a="insOk" data-id="${x.id}">تأیید</button> <button class="btn g sm" data-a="insNo" data-id="${x.id}">لغو</button>`;const jb=jobBy[x.id];return jb?`<button class="btn g sm" data-a="jView" data-id="${E(jb.id)}">جزئیات کار</button>`:'—'};
return card('حالت فوری · فقط کانال تست',`<div class="s">فقط پست رسمی <code>x.com/mobgamesstudios/status/…</code>. ذخیره → تأیید → پردازش → پیش‌نمایش در PV → تأیید شما در PV → ارسال به کانال تست (با رعایت ساعت سکوت و سقف روزانه). انتشار در کانال اصلی وجود ندارد.</div><div class="row" style="margin-top:10px"><div><label>مرحلهٔ ۱ · لینک پست</label><input id="ins_u" dir="ltr" placeholder="https://x.com/mobgamesstudios/status/…"></div><button class="btn" data-a="insAdd">ذخیره و بررسی</button></div>`)
+step2
+card('لینک‌های اخیر',l.ok===false?`<div class="empty">${E(l.error)}</div>`:tbl([['لینک',x=>`<code>${E(x.url)}</code>`],['وضعیت لینک',x=>badge(x.status)],['وضعیت کار',x=>jobBy[x.id]?badge(jobBy[x.id].status):'—'],['ذخیره',x=>fmt(x.created_at)],['',act]],l.items||[],'لینکی نیست'))};
A.insAdd=async()=>{const u=val('ins_u').trim();if(!u)return toast('لینک را بنویس','bad');const r=await api('/api/console/links',{method:'POST',body:{url:u}});INS.link=r.link;toast('ذخیره شد؛ تأیید کن','ok');R.go('inst')};
A.insOk=async el=>{await api('/api/console/links/'+N(el.dataset.id)+'/confirm',{method:'POST',body:{}});INS.link=null;toast('تأیید شد؛ کار ثبت شد','ok');R.go('inst')};
A.insNo=async el=>{await api('/api/console/links/'+N(el.dataset.id)+'/cancel',{method:'POST',body:{}});INS.link=null;toast('لغو شد','ok');R.go('inst')};

/* ---------- کارها ---------- */
const JT=[['queue_intake_dry_run','بررسی آزمایشی ورود به صف'],['ai_health_check','بررسی سلامت AI'],['log_zip','فشرده‌سازی لاگ‌ها'],['d1_backup','بکاپ دیتابیس'],['requeue_stuck','بازگرداندن گیرکرده‌ها']];
const JL=Object.fromEntries(JT.concat([['mob_x_link','لینک X خبر Mob']]));
V.jobs=async()=>{const r=await api('/api/console/jobs?limit=30');
return card('ثبت کار جدید',`<div class="row"><div><label>نوع کار</label><select id="j_t">${opts(JT)}</select></div><button class="btn" data-a="jAdd">ثبت در صف</button><button class="btn g" data-a="reload">↻ تازه‌سازی</button></div><div class="s">فقط رکورد ثبت می‌شود؛ موتور اجرای کارها هنوز وجود ندارد (وضعیت «در انتظار» یعنی اجرا نشده). کار لینک X از «حالت فوری» ساخته می‌شود.</div>`)
+card('',tbl([['شناسه',x=>`<code>${cut(x.id,16)}</code>`],['نوع',x=>E(JL[x.type]||x.type)],['وضعیت',x=>badge(x.status)],['پیشرفت',x=>`<div class="bar" style="min-width:90px"><i data-w="${N(x.progress)}"></i></div>`],['ثبت',x=>fmt(x.created_at)],['خطا',x=>E(x.error||'—')],['',x=>`<button class="btn g sm" data-a="jView" data-id="${E(x.id)}">جزئیات</button>`]],r.items||[]))};
A.jView=async el=>{const r=await api('/api/console/jobs/'+encodeURIComponent(el.dataset.id)),j=r.job,x=j.result||{};
const row=(k,v)=>`<div class="br" style="grid-template-columns:150px 1fr"><span class="s">${k}</span><b style="text-align:right">${v}</b></div>`;
modal(`<h3 style="margin-bottom:8px">جزئیات کار</h3>${row('نوع',E(JL[j.type]||j.type))}${row('وضعیت',badge(j.status))}${row('پیشرفت',N(j.progress)+'٪')}${row('شناسهٔ پست (۶ رقم آخر)',E(String(x.external_id||'').slice(-6)||'—'))}${row('رسانه',E(x.media_type||'—'))}${row('پیش‌نمایش در PV',x.preview_sent?'<span class="bd ok">ارسال شد</span>':'—')}${row('وضعیت ارسال',E(x.delivery_status||'—'))}${row('خطا',E(j.error||x.delivery_error||'—'))}<div class="s" style="margin-top:10px">تأیید نهایی و ارسال فقط از PV انجام می‌شود (فقط کانال تست). پیش‌نمایش ویدیو در وب وجود ندارد.</div><div class="row" style="margin-top:14px"><button class="btn g" data-a="close">بستن</button></div>`)};

/* ---------- دیتابیس (رفع باگ data-n) ---------- */
V.d1=async()=>{const t=await api('/api/console/d1/tables');
return card('جدول‌ها',`<div class="chips">${(t.items||[]).map(x=>`<button class="chip" style="cursor:pointer" data-a="dT" data-tbl="${E(x.name)}" title="${E(x.columns.map(c=>c.name).join(', '))}">${E(x.name)}<b>${N(x.row_count).toLocaleString('fa-IR')}</b></button>`).join('')}</div>`)
+card('پرس‌وجو (فقط SELECT)',`<textarea id="d_q" rows="3" dir="ltr" spellcheck="false">SELECT * FROM items ORDER BY discovered_at DESC LIMIT 20</textarea><div class="row" style="margin-top:10px"><button class="btn" data-a="dRun">اجرا</button></div>`)+'<div id="d_r"></div>'};
A.dT=el=>{document.getElementById('d_q').value='SELECT * FROM "'+el.dataset.tbl+'" LIMIT 20'};

/* ---------- ناوبری ---------- */
if(!NAV.some(n=>n[0]==='inst'))NAV.splice(3,0,['inst','حالت فوری','➤']);
document.getElementById('nav').innerHTML=NAV.map(n=>`<button class="nv" data-id="${n[0]}" data-a="go"><span>${n[2]}</span>${n[1]}</button>`).join('');
R.go(R.cur);
})();
