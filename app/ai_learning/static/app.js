"use strict";
const API="/api/ai-learning", $=id=>document.getElementById(id);let courseId=null,topics=[],allCourseLessonsVisible=false,selectedTopicIds=new Set();
async function api(path,options={}){const r=await fetch(API+path,{cache:"no-store",headers:{"Content-Type":"application/json",...(options.headers||{})},...options});const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.detail||("HTTP "+r.status));return d}
function say(s,bad=false){$("status").textContent=s;$("status").className=bad?"error":"success"}
function el(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n}
function data(form){return Object.fromEntries(new FormData(form).entries())}
function progressMeter(percent,label){const value=Math.max(0,Math.min(100,Number(percent)||0));const wrap=el("div",undefined,"progress-wrap");const meta=el("div",undefined,"progress-meta");meta.append(el("span",label||"پیشرفت دریافت محتوا"),el("strong",value+"٪"));const track=el("div",undefined,"progress-track"),bar=el("span",undefined,"progress-bar");bar.style.width=value+"%";track.append(bar);wrap.append(meta,track);return wrap}
async function act(fn){try{await fn()}catch(e){say(e.message,true)}}
async function loadCourses(keep){const d=await api("/courses");const s=$("course-select");s.replaceChildren();for(const c of d.items){const o=el("option",c.title+" (شناسه "+c.id+")");o.value=c.id;s.append(o)}if(keep&&d.items.some(c=>String(c.id)===String(keep)))s.value=String(keep);courseId=s.value?Number(s.value):null;if(courseId)localStorage.setItem("aiLearningCourseId",String(courseId));await loadCourse()}
async function loadCourse(){const nextCourseId=$("course-select").value?Number($("course-select").value):null;if(nextCourseId!==courseId)selectedTopicIds.clear();courseId=nextCourseId;$("topics").replaceChildren();$("lessons").replaceChildren();if($("lesson-list-details"))$("lesson-list-details").open=false;if(!courseId){$("topics").append(el("p","هنوز دوره‌ای ثبت نشده است."));await loadRuns();return}topics=(await api("/courses/"+courseId)).topics||[];renderTopics();$("lessons").append(el("p","برای نمایش متن، روی «مشاهده درس‌ها و محتوا» در کارت سرفصل بزنید یا «نمایش همهٔ درس‌های دوره» را انتخاب کنید."));await loadRuns()}
async function loadRuns(){const root=$("runs");if(!root)return;root.replaceChildren();const selectAll=$("select-all-runs");if(selectAll)selectAll.checked=false;if(!courseId){root.append(el("p","ابتدا یک دوره انتخاب کنید.","muted"));return}const d=await api("/courses/"+courseId+"/runs?limit=50");if(!d.items.length){root.append(el("p","برای این دوره هنوز اجرای خودکاری ثبت نشده است.","muted"));return}for(const run of d.items){const card=el("article",undefined,"run"),type=run.run_type==="outline"?"دریافت سرفصل":"تولید درس",states={queued:"در صف",running:"در حال اجرا",succeeded:"با موفقیت ثبت شد",needs_user_action:"نیازمند بررسی",failed:"ناموفق",cancelled:"لغو شده"};card.dataset.runId=String(run.id);card.dataset.runStatus=run.status;const head=el("div",undefined,"topic-head"),check=el("input");check.type="checkbox";check.className="run-check";check.value=String(run.id);check.setAttribute("aria-label","انتخاب اجرای شماره "+run.id);check.disabled=["queued","running"].includes(run.status);head.append(check,el("h4","اجرای #"+run.id+" — "+type+" — "+(states[run.status]||run.status)));card.append(head);card.append(el("p","ایجاد: "+run.created_at,"muted"));if(["queued","running"].includes(run.status)){const cancel=el("button","توقف اجرا","secondary");cancel.type="button";cancel.className="run-cancel";cancel.onclick=()=>act(async()=>{if(!confirm("اجرای شماره "+run.id+" متوقف شود؟"))return;const result=await api("/runs/"+run.id+"/cancel",{method:"POST"});await loadRuns();say(result.cancelled_by_request===false?(result.message||"این اجرا پیش از درخواست توقف به پایان رسیده بود؛ وضعیت آن به‌روزرسانی شد."):"اجرای شماره "+run.id+" متوقف شد و اکنون قابل حذف است.")});card.append(cancel)}if(run.error_message)card.append(el("p",run.error_message,"error"));if(run.status==="needs_user_action"){const reveal=el("button","مشاهده و اصلاح پاسخ","secondary");reveal.type="button";reveal.onclick=()=>act(async()=>{const full=await api("/runs/"+run.id);const area=el("textarea");area.rows=10;area.maxLength=500000;area.value=full.response_text||"";area.placeholder="پاسخ ذخیره‌شده ChatGPT را بررسی یا اصلاح کن…";const save=el("button","پردازش دوباره و ذخیره سرفصل/درس");save.type="button";save.onclick=()=>act(async()=>{if(!area.value.trim())throw Error("پاسخ خالی است.");const result=await api("/runs/"+run.id+"/complete",{method:"POST",body:JSON.stringify({response_text:area.value})});await loadCourse();say(result.status==="succeeded"?"پاسخ اصلاح‌شده پردازش و ذخیره شد.":(result.error_message||"پاسخ هنوز نیازمند اصلاح است."),result.status!=="succeeded")});card.append(area,save);reveal.disabled=true});card.append(reveal)}root.append(card)}if(selectAll)selectAll.onchange=()=>{document.querySelectorAll(".run-check:not(:disabled)").forEach(check=>{check.checked=selectAll.checked})}}
$("stop-active-runs").onclick=()=>act(async()=>{const ids=Array.from(document.querySelectorAll(".run-cancel")).map(b=>Number(b.closest(".run").dataset.runId));if(!ids.length)throw Error("اجرای فعالی برای توقف وجود ندارد.");if(!confirm("تعداد "+ids.length+" اجرای فعال متوقف شود؟"))return;let stopped=0,alreadyFinished=0;for(const id of ids){try{const result=await api("/runs/"+id+"/cancel",{method:"POST"});if(result.cancelled_by_request===false)alreadyFinished++;else stopped++}catch(e){}}await loadRuns();say(stopped+" اجرا متوقف شد؛ "+alreadyFinished+" اجرا پیش از درخواست توقف به پایان رسیده بود. سوابق نهایی را می‌توانید انتخاب و حذف کنید.",alreadyFinished>0||stopped+alreadyFinished!==ids.length)});
$("delete-selected-runs").onclick=()=>act(async()=>{if(!courseId)throw Error("ابتدا یک دوره انتخاب کنید.");const ids=Array.from(document.querySelectorAll(".run-check:checked")).map(x=>Number(x.value));if(!ids.length)throw Error("حداقل یک سابقه قابل حذف انتخاب کنید.");if(!confirm("تعداد "+ids.length+" سابقه انتخاب‌شده حذف شود؟ پاسخ‌های ذخیره‌شده این اجراها نیز حذف می‌شوند؛ درس‌ها و سرفصل‌های ثبت‌شده باقی می‌مانند."))return;const result=await api("/courses/"+courseId+"/runs",{method:"DELETE",body:JSON.stringify({run_ids:ids})});await loadRuns();say(result.deleted+" سابقه اجرا حذف شد.")});
function childTopics(topicId) {
 return topics.filter(item=>Number(item.parent_topic_id||0)===Number(topicId));
}
function topicDescendantIds(topicId) {
 const found=new Set();
 const visit=id=>{
  for(const child of childTopics(id)){
   const childId=Number(child.id);
   if(found.has(childId))continue;
   found.add(childId);visit(childId);
  }
 };
 visit(Number(topicId));
 return found;
}
function topicProgress(topic,trail=new Set()) {
 const id=Number(topic.id);
 const own=Math.max(0,Math.min(100,Number(topic.progress_percent)||0));
 if(trail.has(id))return own;
 const next=new Set(trail);next.add(id);
 const children=childTopics(id);
 if(!children.length)return own;
 const childValues=children.map(child=>topicProgress(child,next));
 if(Number(topic.lesson_count||0)>0)childValues.push(own);
 return Math.round(childValues.reduce((sum,value)=>sum+value,0)/childValues.length);
}
function refreshTopicParentOptions() {
 const select=$("topic-parent-select");
 if(!select)return;
 const selected=select.value;
 const rootOption=el("option","بدون مبحث والد (مبحث اصلی)");rootOption.value="";select.replaceChildren(rootOption);
 const byId=new Map(topics.map(item=>[Number(item.id),item]));
 const depthOf=topic=>{
  let depth=0,parent=byId.get(Number(topic.parent_topic_id||0)),guard=new Set([Number(topic.id)]);
  while(parent&&!guard.has(Number(parent.id))){guard.add(Number(parent.id));depth++;parent=byId.get(Number(parent.parent_topic_id||0))}
  return Math.min(depth,6);
 };
 for(const topic of topics){
  const option=el("option","　".repeat(depthOf(topic))+topic.position+". "+topic.title);
  option.value=String(topic.id);select.append(option);
 }
 if(Array.from(select.options).some(option=>option.value===selected))select.value=selected;
}
function renderTopics(){
 const root=$("topics");
 const expandedTopicIds=new Set(Array.from(root.querySelectorAll("details.topic[open]")).map(item=>Number(item.dataset.id)));
 root.replaceChildren();
 const summary=$("course-progress");
 const childrenByParent=new Map();
 const topicById=new Map(topics.map(topic=>[Number(topic.id),topic]));
 for(const topic of topics){
  const parent=Number(topic.parent_topic_id||0);
  if(!childrenByParent.has(parent))childrenByParent.set(parent,[]);
  childrenByParent.get(parent).push(topic);
 }
 const childrenOf=id=>childrenByParent.get(Number(id))||[];
 const isRoot=topic=>!topic.parent_topic_id||!topicById.has(Number(topic.parent_topic_id))||Number(topic.parent_topic_id)===Number(topic.id);
 const progressTopics=topics.filter(topic=>!childrenOf(topic.id).length||Number(topic.lesson_count||0)>0);
 const completed=progressTopics.filter(topic=>topicProgress(topic)===100).length;
 const paused=progressTopics.filter(topic=>topic.processing_status==="paused").length;
 const avg=progressTopics.length?Math.round(progressTopics.reduce((sum,topic)=>sum+topicProgress(topic),0)/progressTopics.length):0;
 if(summary){
  summary.replaceChildren();
  summary.append(el("h3","پیشرفت کلی دوره"));
  summary.append(progressMeter(avg,"پیشرفت دریافت و آماده‌سازی محتوا"));
  const stats=el("div",undefined,"progress-stats");
  stats.append(el("span","کل موضوعات و سرفصل‌ها: "+topics.length),el("span","تکمیل‌شده: "+completed),el("span","باقی‌مانده: "+(progressTopics.length-completed)),el("span","متوقف‌شده: "+paused));
  summary.append(stats);
 }
 if(!topics.length){root.append(el("p","سرفصلی وجود ندارد."));refreshTopicParentOptions();return}
 const renderOne=(topic,depth=0,trail=new Set(),numberPath=String(topic.position))=>{
  const card=el("details",undefined,"topic");
  card.dataset.id=String(topic.id);
  card.dataset.parentTopicId=String(topic.parent_topic_id||"");
  card.style.setProperty("--topic-depth",String(depth));
  card.open=expandedTopicIds.has(Number(topic.id));
  const head=el("summary",undefined,"topic-head"),check=el("input");
  check.type="checkbox";check.className="topic-check";check.checked=selectedTopicIds.has(Number(topic.id));
  check.setAttribute("aria-label","انتخاب "+topic.title);
  check.onclick=event=>event.stopPropagation();
  check.onchange=()=>{if(check.checked)selectedTopicIds.add(Number(topic.id));else selectedTopicIds.delete(Number(topic.id))};
  const percent=topicProgress(topic);
  const status=topic.approval_status==="approved"?"تأییدشده":topic.approval_status==="rejected"?"ردشده":"در انتظار";
  head.append(check,el("strong",numberPath+". "+topic.title),el("span",status+" · "+percent+"٪","small topic-state"));
  card.append(head);
  if(topic.description)card.append(el("p",topic.description,"muted"));
  card.append(progressMeter(percent,childrenOf(topic.id).length?"پیشرفت این مبحث و زیرمجموعه‌ها":topic.progress_label));
  const actions=el("div",undefined,"actions");
  const button=(label,fn,secondary=false)=>{
   const b=el("button",label,secondary?"secondary":"");b.type="button";b.onclick=()=>act(fn);actions.append(b);
  };
  button("ویرایش",async()=>{
   const title=prompt("عنوان سرفصل",topic.title);if(title===null)return;
   const description=prompt("توضیحات",topic.description||"");if(description===null)return;
   await api("/topics/"+topic.id,{method:"PATCH",body:JSON.stringify({title,description})});
   await loadCourse();say("سرفصل ویرایش شد.");
  },true);
  button("تغییر مبحث والد",async()=>{
   const existing=card.querySelector(".parent-editor");
   if(existing){existing.remove();return;}
   const editor=el("div",undefined,"actions parent-editor");
   const label=el("label","مبحث والد");label.className="parent-editor-label";
   const selector=el("select");selector.setAttribute("aria-label","انتخاب مبحث والد برای "+topic.title);
   const rootOption=el("option","بدون مبحث والد (مبحث اصلی)");rootOption.value="";selector.append(rootOption);
   const excluded=topicDescendantIds(topic.id);excluded.add(Number(topic.id));
   for(const candidate of topics){
    if(excluded.has(Number(candidate.id)))continue;
    const option=el("option",candidate.position+". "+candidate.title);
    option.value=String(candidate.id);selector.append(option);
   }
   selector.value=topic.parent_topic_id?String(topic.parent_topic_id):"";
   label.append(selector);
   const save=el("button","ذخیره مبحث والد");save.type="button";
   save.onclick=()=>act(async()=>{
    const parentId=selector.value?Number(selector.value):null;
    await api("/topics/"+topic.id,{method:"PATCH",body:JSON.stringify({parent_topic_id:parentId})});
    await loadCourse();returnToTopic(topic.id);
    say(parentId?"سرفصل زیرمجموعه مبحث انتخاب‌شده شد.":"سرفصل به مبحث اصلی منتقل شد.");
   });
   const cancel=el("button","انصراف","secondary");cancel.type="button";cancel.onclick=()=>editor.remove();
   label.append(save,cancel);editor.append(label);actions.after(editor);
  },true);
  button("تأیید",()=>decide([topic.id],"approved"));
  button("رد",()=>decide([topic.id],"rejected"),true);
  let inlineLessons=null;
  button("مشاهده درس‌ها و محتوا",async()=>{
   if(!inlineLessons){inlineLessons=el("div",undefined,"topic-inline-lessons");inlineLessons.hidden=true;inlineLessons.dataset.topicId=String(topic.id);head.after(inlineLessons)}
   if(!inlineLessons.hidden){inlineLessons.hidden=true;return}
   const previousScrollX=window.scrollX,previousScrollY=window.scrollY;
   inlineLessons.hidden=false;
   try{
    await loadLessons(topic.id,inlineLessons,true);
    inlineLessons.dataset.loaded="true";
   }finally{
    window.scrollTo(previousScrollX,previousScrollY);
   }
  },true);
  button("تلاش مجدد دریافت/اصلاح درس",async()=>{
   if(topic.approval_status!=="approved")throw Error("ابتدا سرفصل را تأیید کنید.");
   if(!confirm("برای این سرفصل یک تلاش تازه انجام شود؟ نسخه قبلی حفظ می‌شود و محتوای جدید برای بازبینی ثبت خواهد شد."))return;
   const result=await api("/browser/lessons",{method:"POST",body:JSON.stringify({course_id:courseId,topic_ids:[topic.id],request_key:"playwright-retry-topic-"+topic.id+"-"+Date.now(),force_retry:true})});
   await loadCourse();await loadRuns();say("تلاش مجدد آغاز شد؛ اجرای #"+result.run_id+" در سوابق دیده می‌شود.");
  },true);
  button(topic.processing_status==="paused"?"ادامه سرفصل":"مکث سرفصل",async()=>{
   const paused=topic.processing_status!=="paused";
   await api("/topics/"+topic.id+"/pause",{method:"POST",body:JSON.stringify({paused})});
   await loadCourse();say(paused?"این سرفصل متوقف شد؛ محتوای ذخیره‌شده حفظ شد.":"ادامه این سرفصل ثبت شد.");
  },true);
  button("حذف",async()=>{
   if(!confirm("سرفصل از فهرست فعال حذف شود؟"))return;
   await api("/topics/"+topic.id,{method:"DELETE"});await loadCourse();say("سرفصل حذف نرم شد.");
  },true);
  const siblings=childrenOf(Number(topic.parent_topic_id||0));
  const siblingIndex=siblings.findIndex(item=>Number(item.id)===Number(topic.id));
  button("بالا",()=>moveInHierarchy(topic,-1),true);
  button("پایین",()=>moveInHierarchy(topic,1),true);
  card.append(actions);
  const children=childrenOf(topic.id);
  if(children.length){
   const nested=el("div",undefined,"topic-children");
   const nextTrail=new Set(trail);nextTrail.add(Number(topic.id));
   children.forEach((child,index)=>{
    if(nextTrail.has(Number(child.id)))return;
    nested.append(renderOne(child,depth+1,nextTrail,numberPath+"."+(index+1)));
   });
   card.append(nested);
  }
  return card;
 };
 topics.filter(isRoot).forEach((topic,index)=>root.append(renderOne(topic,0,new Set(),String(index+1))));
 const renderedIds=new Set(Array.from(root.querySelectorAll("details.topic")).map(card=>Number(card.dataset.id)));
 for(const topic of topics)if(!renderedIds.has(Number(topic.id)))root.append(renderOne(topic,0,new Set(),String(topics.filter(isRoot).length+1)));
 refreshTopicParentOptions();
 if($("select-all-topics"))$("select-all-topics").checked=topics.length>0&&topics.every(topic=>selectedTopicIds.has(Number(topic.id)));
}
async function moveInHierarchy(topic,delta){
 const parentId=Number(topic.parent_topic_id||0);
 const siblings=childTopics(parentId).slice();
 const index=siblings.findIndex(item=>Number(item.id)===Number(topic.id));
 const target=index+delta;if(target<0||target>=siblings.length)return;
 [siblings[index],siblings[target]]=[siblings[target],siblings[index]];
 const byParent=new Map();
 for(const item of topics){
  const key=Number(item.parent_topic_id||0);
  if(!byParent.has(key))byParent.set(key,[]);
  byParent.get(key).push(item);
 }
 byParent.set(parentId,siblings);
 const ordered=[];
 const visit=parent=>{
  for(const child of byParent.get(Number(parent))||[]){
   ordered.push(child);
   visit(Number(child.id));
  }
 };
 visit(0);
 const seen=new Set(ordered.map(item=>Number(item.id)));
 for(const item of topics)if(!seen.has(Number(item.id))){ordered.push(item);seen.add(Number(item.id))}
 await api("/courses/"+courseId+"/topic-order",{method:"PUT",body:JSON.stringify({topic_ids:ordered.map(item=>item.id)})});
 await loadCourse();say("ترتیب سرفصل در همین مبحث ذخیره شد.");
}
async function move(i,d){const j=i+d;if(j<0||j>=topics.length)return;[topics[i],topics[j]]=[topics[j],topics[i]];topics.forEach((t,k)=>t.position=k+1);renderTopics();say("ترتیب محلی تغییر کرد؛ برای ذخیره، «ذخیره ترتیب» را بزنید.")}
async function decide(ids,decision){if(!ids.length)throw Error("حداقل یک سرفصل انتخاب کنید.");await api("/topics/decision",{method:"POST",body:JSON.stringify({topic_ids:ids,decision})});await loadCourse();say("وضعیت سرفصل‌ها به‌روزرسانی شد.")}
$("course-form").onsubmit=e=>{e.preventDefault();const form=e.currentTarget;const payload=data(form);act(async()=>{await api("/courses",{method:"POST",body:JSON.stringify(payload)});form.reset();await loadCourses();say("دوره ایجاد شد.")})};
$("reload").onclick=()=>act(async()=>{const b=$("reload"),label=b.textContent;if(!courseId)throw Error("ابتدا دورهٔ موردنظر را انتخاب کنید.");b.disabled=true;b.textContent="در حال اجرای Chrome…";try{say("در حال راه‌اندازی Chrome و ارسال درخواست به ChatGPT؛ این صفحه را نبندید.");const started=await api("/browser/outline",{method:"POST",body:JSON.stringify({course_id:Number(courseId),request_key:"playwright-outline-"+courseId+"-"+Date.now()})});const runId=Number(started.run_id);if(!runId)throw Error("سرویس مرورگر شناسه اجرای معتبر برنگرداند.");say("درخواست ارسال شد؛ منتظر پاسخ کامل ChatGPT هستیم. ممکن است پنجره Chrome باز شود.");const deadline=Date.now()+12*60*1000;let run=null;while(Date.now()<deadline){await new Promise(resolve=>setTimeout(resolve,2500));run=await api("/runs/"+runId);if(["succeeded","failed","cancelled","needs_user_action"].includes(run.status))break;say("در حال دریافت پاسخ از ChatGPT… اجرای #"+runId)}if(!run||!["succeeded","failed","cancelled","needs_user_action"].includes(run.status))throw Error("زمان انتظار داشبورد تمام شد؛ وضعیت اجرا در بخش «اجراهای اخیر» قابل بررسی است.");await loadCourse();if(run.status==="succeeded")say("سرفصل‌ها با موفقیت ثبت شدند؛ موارد تکراری نادیده گرفته شدند. برای کنترل کیفیت، ابتدا سرفصل‌ها را تأیید کنید و سپس دریافت درس را اجرا کنید یا «ادامه/بازیابی» را بزنید.");else say(run.error_message||("اجرای ChatGPT با وضعیت "+run.status+" پایان یافت."),true)}finally{b.disabled=false;b.textContent=label}});
$("delete-course").onclick=()=>act(async()=>{
 if(!courseId)throw Error("ابتدا یک دوره انتخاب کنید.");
 const selected=$("course-select").selectedOptions[0]?.textContent||("شناسه "+courseId);
 if(!confirm("دوره «"+selected+"» از فهرست فعال حذف شود؟ محتوای آن پاک نمی‌شود و برای حفظ سابقه بایگانی خواهد شد."))return;
 await api("/courses/"+courseId,{method:"DELETE"});
 courseId=null;
 await loadCourses();
 say("دوره بایگانی شد و از فهرست فعال حذف شد.");
});
$("course-select").onchange=()=>{if($("course-select").value)localStorage.setItem("aiLearningCourseId",$("course-select").value);act(loadCourse)};
$("outline-form").onsubmit=e=>{e.preventDefault();act(async()=>{if(!courseId)throw Error("ابتدا دوره ایجاد کنید.");const raw=$("outline-text").value;if(!raw.trim())throw Error("پاسخ سرفصل‌ها را وارد کنید.");const r=await api("/courses/"+courseId+"/outline/import",{method:"POST",body:JSON.stringify({raw_response:raw,provider:"manual"})});$("outline-text").value="";await loadCourse();say(r.imported+" سرفصل در انتظار تأیید اضافه شد.")})};
$("topic-form").onsubmit=e=>{e.preventDefault();const form=e.currentTarget;const payload=data(form);payload.parent_topic_id=payload.parent_topic_id?Number(payload.parent_topic_id):null;act(async()=>{if(!courseId)throw Error("ابتدا دوره ایجاد کنید.");await api("/courses/"+courseId+"/topics",{method:"POST",body:JSON.stringify(payload)});form.reset();await loadCourse();say(payload.parent_topic_id?"سرفصل زیرمجموعه مبحث انتخاب‌شده اضافه شد.":"مبحث اصلی اضافه شد.")})};
$("select-all-topics").onchange=()=>{selectedTopicIds=$("select-all-topics").checked?new Set(topics.map(t=>Number(t.id))):new Set();document.querySelectorAll(".topic-check").forEach(x=>x.checked=$("select-all-topics").checked)};
$("approve-selected").onclick=()=>act(()=>decide(Array.from(document.querySelectorAll(".topic-check:checked")).map(x=>Number(x.closest(".topic").dataset.id)),"approved"));
$("approve-all-topics").onclick=()=>act(async()=>{if(!topics.length)throw Error("سرفصلی برای تأیید وجود ندارد.");if(!confirm("همهٔ "+topics.length+" سرفصل این دوره تأیید شوند؟"))return;await decide(topics.map(t=>t.id),"approved");$("select-all-topics").checked=false;say("همهٔ سرفصل‌های دوره تأیید شدند. برای دریافت درس‌های باقی‌مانده، دکمهٔ ادامه/بازیابی را بزنید.")});
$("learn-selected-topics").onclick=()=>act(async()=>{
 const ids=Array.from(document.querySelectorAll(".topic-check:checked")).map(x=>Number(x.closest(".topic").dataset.id));
 if(!ids.length)throw Error("ابتدا سرفصل‌هایی را که باید برایشان درس دریافت شود انتخاب کنید.");
 const chosen=topics.filter(t=>ids.includes(Number(t.id)));
 const unapproved=chosen.filter(t=>t.approval_status!=="approved");
 if(unapproved.length)throw Error("ابتدا سرفصل‌های انتخاب‌شده را تأیید کنید؛ فقط سرفصل‌های تأییدشده برای دریافت درس ارسال می‌شوند.");
 if(!confirm("برای سرفصل‌های انتخاب‌شده‌ای که هنوز محتوای درس ندارند، دریافت درس از ChatGPT آغاز شود؟"))return;
 const result=await api("/browser/lessons",{method:"POST",body:JSON.stringify({course_id:courseId,topic_ids:ids,request_key:"playwright-selected-lessons-"+courseId+"-"+Date.now()})});
 await loadCourse();
 await loadRuns();
 say("دریافت درس آغاز شد؛ اجرای #"+result.run_id+" در سوابق نمایش داده می‌شود. محتوای دریافت‌شده همچنان نیازمند بازبینی خواهد بود.");
});
$("approve-all-lessons").onclick=()=>act(async()=>{if(!courseId)throw Error("ابتدا یک دوره انتخاب کنید.");if(!confirm("فقط نسخه‌های معتبرِ نیازمند بازبینی در این دوره تأیید شوند؟ هیچ درسی حذف نخواهد شد."))return;let reviewed=0,skipped=0;for(const t of topics){const d=await api("/topics/"+t.id+"/lessons");for(const l of d.items){const quality=await inspectLessonForBulk(l.id);if(!quality.valid){skipped++;continue}if(quality.version.review_status==="needs_review"){await api("/versions/"+quality.version.id+"/review",{method:"POST",body:JSON.stringify({decision:"reviewed"})});reviewed++}}}await loadCourse();say(reviewed+" نسخهٔ درس تأیید بازبینی شد؛ "+skipped+" درس به‌دلیل منبع یا متن نامعتبر از تأیید رد شد و حذف نشد.")});
$("reject-selected").onclick=()=>act(()=>decide(Array.from(document.querySelectorAll(".topic-check:checked")).map(x=>Number(x.closest(".topic").dataset.id)),"rejected"));
$("save-order").onclick=()=>act(async()=>{if(!courseId||!topics.length)throw Error("سرفصلی برای مرتب‌سازی وجود ندارد.");await api("/courses/"+courseId+"/topic-order",{method:"PUT",body:JSON.stringify({topic_ids:topics.map(t=>t.id)})});await loadCourse();say("ترتیب ذخیره شد.")});
function returnToTopic(topicId){
 const root=$("topics");if(!root)return;
 const card=Array.from(root.querySelectorAll("details.topic")).find(item=>Number(item.dataset.id)===Number(topicId));
 if(!card)return;
 const catalog=$("catalog-menu");if(catalog)catalog.open=true;
 const lessonDetails=$("lesson-list-details");if(lessonDetails)lessonDetails.open=false;
 let ancestor=card.parentElement.closest("details.topic");
 while(ancestor){ancestor.open=true;ancestor=ancestor.parentElement.closest("details.topic")}
 card.open=true;
 window.requestAnimationFrame(()=>{card.scrollIntoView({behavior:"smooth",block:"start"});const summary=card.querySelector(":scope > summary");if(summary){summary.setAttribute("tabindex","-1");try{summary.focus({preventScroll:true})}catch(_){summary.focus()}}});
}
async function inspectLessonForBulk(lessonId){
 const versions=await api("/lessons/"+lessonId+"/versions");
 if(!versions.items.length)return {valid:false,reason:"no-version",version:null};
 const version=versions.items[0];
 const [content,sources]=await Promise.all([
  api("/versions/"+version.id),
  api("/versions/"+version.id+"/sources")
 ]);
 const body=(content.markdown||"").replace(/^#{1,6}\s+.*$/gm,"").trim();
 return {valid:sources.items.length>0&&body.length>=10,version,content,sources};
}
async function deleteLessonForBulk(lessonId){
 await api("/lessons/"+lessonId,{method:"DELETE"});
}
async function loadLessons(topicId,targetRoot,inline=false){const catalog=$("catalog-menu");if(!inline&&catalog)catalog.open=true;const lessonDetails=$("lesson-list-details");if(!inline&&lessonDetails)lessonDetails.open=true;if(!inline)allCourseLessonsVisible=!topicId;const topicIds=topicId?[topicId]:topics.map(x=>x.id);const t=topics.find(x=>Number(x.id)===Number(topicId)),root=targetRoot||$("lessons");const refreshLessons=()=>loadLessons(topicId,root,inline);root.replaceChildren(el("h3",topicId?(t?t.title:"درس‌ها"):"همهٔ درس‌های دوره"));const bulk=el("div",undefined,"toolbar"),all=el("input");all.type="checkbox";all.id="select-all-lessons";const allLabel=el("label",topicId?"انتخاب همهٔ درس‌های این سرفصل":"انتخاب همهٔ درس‌های دوره","inline-check");allLabel.prepend(all);bulk.append(allLabel);const approveBulk=el("button","تأیید درس‌های انتخاب‌شده");approveBulk.type="button";approveBulk.onclick=()=>act(async()=>{const ids=Array.from(root.querySelectorAll(".lesson-check:checked")).map(x=>Number(x.closest(".lesson").dataset.lessonId));if(!ids.length)throw Error("ابتدا درس‌ها را انتخاب کنید.");let count=0,skipped=0;for(const id of ids){const quality=await inspectLessonForBulk(id);if(!quality.valid){skipped++;continue}if(quality.version.review_status==="needs_review"){await api("/versions/"+quality.version.id+"/review",{method:"POST",body:JSON.stringify({decision:"reviewed"})});count++}}await refreshLessons();say(count+" نسخهٔ درس تأیید بازبینی شد؛ "+skipped+" درس نامعتبر از تأیید رد شد و حذف نشد.")});bulk.append(approveBulk);const publishBulk=el("button","انتشار گروهی درس‌های انتخاب‌شده","secondary");publishBulk.type="button";publishBulk.onclick=()=>act(async()=>{const ids=Array.from(root.querySelectorAll(".lesson-check:checked")).map(x=>Number(x.closest(".lesson").dataset.lessonId));if(!ids.length)throw Error("ابتدا درس‌ها را انتخاب کنید.");if(!confirm("فقط نسخه‌های معتبرِ تأییدشده منتشر شوند؟ هیچ درسی حذف نخواهد شد."))return;let published=0,skipped=0;for(const id of ids){const quality=await inspectLessonForBulk(id);if(!quality.valid){skipped++;continue}if(quality.version.review_status==="reviewed"){await api("/versions/"+quality.version.id+"/publish",{method:"POST"});published++}else skipped++}await refreshLessons();say(published+" درس منتشر شد؛ "+skipped+" درس به دلیل منبع/متن نامعتبر یا نداشتن وضعیت بازبینی‌شده رد شد و حذف نشد.")});bulk.append(publishBulk);const deleteInvalidBulk=el("button","حذف گروهی درس‌های بدون منبع/متن معتبر","secondary");deleteInvalidBulk.type="button";deleteInvalidBulk.onclick=()=>act(async()=>{const ids=Array.from(root.querySelectorAll(".lesson-check:checked")).map(x=>Number(x.closest(".lesson").dataset.lessonId));if(!ids.length)throw Error("ابتدا درس‌ها را انتخاب کنید.");if(!confirm("فقط درس‌های انتخاب‌شده‌ای که نسخهٔ فعلی‌شان منبع یا متن معتبر ندارد حذف شوند؟"))return;let deleted=0;for(const id of ids){const quality=await inspectLessonForBulk(id);if(!quality.valid){await deleteLessonForBulk(id);deleted++}}await refreshLessons();say(deleted+" درس بدون منبع یا متن معتبر حذف شد.")});bulk.append(deleteInvalidBulk);root.append(bulk);all.onchange=root.append(bulk);all.onchange=()=>root.querySelectorAll(".lesson-check").forEach(x=>x.checked=all.checked);const form=el("form",undefined,"grid");form.innerHTML='<label>عنوان درس<input name="title" required maxlength="300"></label><button type="submit">ایجاد درس</button>';form.onsubmit=e=>{e.preventDefault();act(async()=>{await api("/topics/"+topicId+"/lessons",{method:"POST",body:JSON.stringify(data(e.currentTarget))});await refreshLessons();say("درس ایجاد شد.")})};root.append(form);const allLessons=[];for(const tid of topicIds){const d=await api("/topics/"+tid+"/lessons");const parent=topics.find(x=>x.id===tid);for(const item of d.items)allLessons.push({...item,_topicId:tid,_topicTitle:parent?parent.title:"سرفصل"});}if(!allLessons.length)root.append(el("p","برای این انتخاب هنوز درسی ثبت نشده است.","muted"));for(const l of allLessons){const card=el("article",undefined,"lesson");card.dataset.lessonId=String(l.id);card.dataset.topicId=String(l._topicId||topicId);if(!topicId)card.append(el("p","سرفصل: "+l._topicTitle,"muted"));const lessonHead=el("div",undefined,"topic-head"),lessonCheck=el("input");lessonCheck.type="checkbox";lessonCheck.className="lesson-check";lessonCheck.setAttribute("aria-label","انتخاب درس "+l.title);lessonHead.append(lessonCheck,el("h4",l.position+". "+l.title));card.append(lessonHead);card.append(progressMeter(l.progress_percent,l.progress_label));const actions=el("div",undefined,"actions"),pause=el("button",l.status==="paused"?"ادامه درس":"مکث درس","secondary");pause.type="button";pause.onclick=()=>act(async()=>{await api("/lessons/"+l.id+"/pause",{method:"POST",body:JSON.stringify({paused:l.status!=="paused"})});await refreshLessons();say(l.status==="paused"?"درس برای ادامه آماده شد؛ نسخه‌های قبلی حفظ شدند.":"این درس متوقف شد؛ نسخه‌های ذخیره‌شده حفظ شدند.")});actions.append(pause);const retryLesson=el("button","تلاش مجدد با منابع به‌روز","secondary");retryLesson.type="button";retryLesson.onclick=()=>act(async()=>{const parent=topics.find(x=>Number(x.id)===Number(l._topicId||topicId));if(!parent||parent.approval_status!=="approved")throw Error("ابتدا سرفصل مربوط را تأیید کنید.");if(!confirm("نسخه تازه‌ای از این درس با بررسی و به‌روزرسانی پیوندهای منابع دریافت شود؟ نسخه‌های قبلی حفظ می‌شوند."))return;const result=await api("/browser/lessons",{method:"POST",body:JSON.stringify({course_id:courseId,topic_ids:[Number(l._topicId||topicId)],request_key:"playwright-retry-lesson-"+l.id+"-"+Date.now(),force_retry:true})});await loadRuns();say("تلاش مجدد آغاز شد؛ اجرای #"+result.run_id+" در سوابق نمایش داده می‌شود.")});actions.append(retryLesson);const edit=el("button","ویرایش عنوان","secondary");edit.type="button";edit.onclick=()=>act(async()=>{const title=prompt("عنوان درس",l.title);if(title===null)return;await api("/lessons/"+l.id,{method:"PATCH",body:JSON.stringify({title})});await refreshLessons();say("عنوان درس ویرایش شد.")});actions.append(edit);const del=el("button","حذف درس","secondary");del.type="button";del.onclick=()=>act(async()=>{if(!confirm("حذف نرم درس؟"))return;await api("/lessons/"+l.id,{method:"DELETE"});await refreshLessons();say("درس حذف شد.")});actions.append(del);card.append(actions);const f=el("form");f.innerHTML='<label>متن درس (Markdown)<textarea name="markdown" rows="5" maxlength="200000" required></textarea></label><label>منبع<input name="provider" value="manual" maxlength="80"></label><button type="submit">ثبت نسخه جدید</button>';f.onsubmit=e=>{e.preventDefault();act(async()=>{const x=data(e.currentTarget);await api("/lessons/"+l.id+"/versions",{method:"POST",body:JSON.stringify({...x,source_kind:"manual"})});await refreshLessons();say("نسخه جدید در انتظار بازبینی است.")})};card.append(f);const vs=await api("/lessons/"+l.id+"/versions");for(const v of vs.items){const box=el("div",undefined,"version");box.append(el("p","نسخه "+v.version_no+" — "+v.review_status+" — "+v.content_sha256,"small"));const full=await api("/versions/"+v.id);const sourceResult=await api("/versions/"+v.id+"/sources");if(sourceResult.items.length){const sourceBox=el("div",undefined,"source-list");sourceBox.append(el("strong","منابع ذخیره‌شده (هنوز تأییدنشده)"));for(const source of sourceResult.items){const row=el("p",undefined,"small");const link=el("a",source.title||source.url);link.href=source.url;link.target="_blank";link.rel="noopener noreferrer";row.append(link,document.createTextNode(" — "+(source.publisher||"ناشر نامشخص")+" · "+(source.validation_status==="reviewed"?"پیوند در دسترس":source.validation_status==="rejected"?"پیوند نامعتبر/نیازمند جایگزین":"بررسی‌نشده")));if(source.excerpt)row.append(document.createTextNode(" · "+source.excerpt));const checkLink=el("button","بررسی پیوند","secondary");checkLink.type="button";checkLink.onclick=()=>act(async()=>{checkLink.disabled=true;try{const checked=await api("/sources/"+source.id+"/check",{method:"POST"});await refreshLessons();say(checked.validation_status==="reviewed"?"پیوند پاسخ داد؛ صحت محتوای منبع جداگانه باید بررسی شود.":"پیوند در دسترس نیست یا تغییر مسیر دارد؛ برای دریافت نسخه با منابع تازه، «تلاش مجدد دریافت/اصلاح درس» را بزنید.",checked.validation_status!=="reviewed")}finally{checkLink.disabled=false}});row.append(checkLink);sourceBox.append(row)}box.append(sourceBox)}else box.append(el("p","منبع دارای پیوندی در پاسخ ثبت نشده است.","small"));if(full.markdown&&full.markdown.trim())box.append(el("pre",full.markdown,"content"));else {box.append(el("p","هشدار: متن این نسخه خالی است؛ وضعیت انتشار به‌تنهایی به معنی وجود محتوای قابل استفاده نیست. دریافت مجدد یا ثبت نسخهٔ اصلاح‌شده لازم است.","error"));if(full.raw_response&&full.raw_response.trim()){const rawToggle=el("button","نمایش پاسخ خام ذخیره‌شده از ChatGPT","secondary");rawToggle.type="button";rawToggle.onclick=()=>{const existing=box.querySelector(".raw-response");if(existing)existing.remove();else box.append(el("pre",full.raw_response,"content raw-response"))};box.append(rawToggle);const recover=el("button","استفاده از پاسخ خام برای نسخهٔ اصلاحی","secondary");recover.type="button";recover.onclick=()=>{const textarea=card.querySelector('textarea[name="markdown"]');if(!textarea){say("کادر ثبت نسخه پیدا نشد.",true);return}textarea.value=full.raw_response;textarea.focus();textarea.scrollIntoView({behavior:"smooth",block:"center"});say("پاسخ خام کپی شد؛ آن را بررسی کن و با «ثبت نسخه جدید» ذخیره کن.")};box.append(recover)}}const a=el("div",undefined,"actions"),view=el("button","نمایش/پنهان‌کردن متن","secondary");view.type="button";view.onclick=()=>{const existing=box.querySelector(".content");if(existing)existing.remove();else if(full.markdown&&full.markdown.trim())box.append(el("pre",full.markdown,"content"));else say("متن این نسخه خالی است؛ امکان نمایش محتوا وجود ندارد.",true)};a.append(view);const editContent=el("button","ویرایش محتوا (ایجاد نسخهٔ اصلاحی)","secondary");editContent.type="button";editContent.onclick=()=>{const textarea=card.querySelector('textarea[name="markdown"]');if(!textarea){say("کادر ثبت نسخه پیدا نشد.",true);return}textarea.value=full.markdown||"";textarea.focus();textarea.scrollIntoView({behavior:"smooth",block:"center"});say("متن نسخه در کادر ثبت نسخهٔ جدید کپی شد؛ با ثبت آن، نسخهٔ قبلی حفظ می‌شود.")};a.append(editContent);if(v.review_status==="needs_review"){for(const [label,decision] of [["تأیید بازبینی","reviewed"],["رد نسخه","rejected"]]){const b=el("button",label,decision==="rejected"?"secondary":"");b.type="button";b.onclick=()=>act(async()=>{await api("/versions/"+v.id+"/review",{method:"POST",body:JSON.stringify({decision})});await refreshLessons();say("وضعیت بازبینی ثبت شد.")});a.append(b)}}if(v.review_status==="reviewed"||v.review_status==="rejected"){const reset=el("button","بازگردانی به نیازمند بازبینی","secondary");reset.type="button";reset.onclick=()=>act(async()=>{await api("/versions/"+v.id+"/review",{method:"POST",body:JSON.stringify({decision:"needs_review"})});await refreshLessons();say("نسخه به وضعیت نیازمند بازبینی برگشت.")});a.append(reset)}if(v.review_status==="published"){const unpublish=el("button","لغو انتشار / بازگشت به بازبینی","secondary");unpublish.type="button";unpublish.onclick=()=>act(async()=>{if(!confirm("این نسخه از دانش قابل بازیابی خارج شود؟ متن حفظ می‌شود و می‌توان آن را دوباره بازبینی و منتشر کرد."))return;await api("/versions/"+v.id+"/unpublish",{method:"POST"});await refreshLessons();say("انتشار لغو شد؛ متن حفظ شده و نسخه به وضعیت بازبینی برگشت.")});a.append(unpublish)}if(v.review_status==="reviewed"){const b=el("button","انتشار برای بازیابی");b.type="button";b.onclick=()=>act(async()=>{const parentTopicId=Number(l._topicId||topicId);if(!parentTopicId)throw Error("سرفصل والد این درس شناسایی نشد.");await api("/versions/"+v.id+"/publish",{method:"POST"});await loadLessons(parentTopicId,root,inline);if(!inline)returnToTopic(parentTopicId);else{root.hidden=true;root.replaceChildren();root.dataset.loaded="false"}say(inline?"نسخه منتشر شد؛ پنل درس‌ها بسته شد.":"نسخه منتشر شد؛ به سرفصل مربوط برگشتید.")});a.append(b)}box.append(a);card.append(box)}root.append(card)}}
$("show-all-lessons").onclick=()=>act(()=>loadLessons(null));
$("select-all-course-lessons").onchange=()=>document.querySelectorAll("#lessons .lesson-check").forEach(x=>x.checked=$("select-all-course-lessons").checked);
$("approve-selected-course-lessons").onclick=()=>act(async()=>{
 const ids=Array.from(document.querySelectorAll("#lessons .lesson-check:checked")).map(x=>Number(x.closest(".lesson").dataset.lessonId));
 if(!ids.length)throw Error("ابتدا یک یا چند درس را انتخاب کنید.");
 if(!confirm("درس‌های انتخاب‌شده بدون منبع یا متن معتبر حذف شوند و نسخه‌های معتبرِ نیازمند بازبینی تأیید شوند؟"))return;
 let reviewed=0,deleted=0;
 for(const id of ids){const quality=await inspectLessonForBulk(id);if(!quality.valid){await deleteLessonForBulk(id);deleted++;continue}if(quality.version.review_status==="needs_review"){await api("/versions/"+quality.version.id+"/review",{method:"POST",body:JSON.stringify({decision:"reviewed"})});reviewed++}}
 await loadLessons(allCourseLessonsVisible?null:(document.querySelector("#lessons .lesson")?.dataset.topicId?Number(document.querySelector("#lessons .lesson").dataset.topicId):null));
 $("select-all-course-lessons").checked=false;
 say(reviewed+" نسخهٔ درس تأیید بازبینی شد؛ "+deleted+" درس بدون منبع یا متن معتبر حذف شد.");
});
$("search-form").onsubmit=e=>{e.preventDefault();act(async()=>{
 const r=await api("/search?query="+encodeURIComponent($("search-query").value));
 const root=$("search-results");root.replaceChildren();
 if(!r.items.length)root.append(el("p","نتیجه‌ای یافت نشد."));
 for(const x of r.items){const card=el("article",undefined,"result");card.append(el("h3",x.course_title+" ← "+x.topic_title+" ← "+x.lesson_title),el("p","نسخه "+x.version_no+"؛ بخش "+x.chunk_id,"muted"),el("pre",x.body,"content"));root.append(card)}
 const feedback=el("section",undefined,"feedback-controls");
 feedback.append(el("p","آیا این نتایج برای پرسش شما مفید بودند؟","muted"));
 const helpful=el("button","مفید بود"),notHelpful=el("button","مفید نبود"),status=el("span","","muted");
 helpful.type="button";notHelpful.type="button";
 const submitFeedback=async rating=>{helpful.disabled=true;notHelpful.disabled=true;try{await api("/feedback",{method:"POST",body:JSON.stringify({query_trace_id:r.trace_id,rating,comment:""})});status.textContent="بازخورد ثبت شد.";say("بازخورد جست‌وجو ثبت شد.")}catch(error){helpful.disabled=false;notHelpful.disabled=false;status.textContent=error.message;say(error.message,true)}};
 helpful.addEventListener("click",()=>submitFeedback(1));notHelpful.addEventListener("click",()=>submitFeedback(-1));
 feedback.append(helpful,notHelpful,status);root.append(feedback);
 say(r.count+" نتیجه بازیابی شد.")
})};
async function refreshCourseProgress(){if(!courseId)return;const d=await api("/courses/"+courseId);topics=d.topics||[];const inlineReviewOpen=!!document.querySelector(".topic-inline-lessons:not([hidden])");if(!inlineReviewOpen){renderTopics();$("select-all-topics").checked=topics.length>0&&topics.every(t=>selectedTopicIds.has(Number(t.id)))}}
async function refreshLearningControl(){
 const state=await api("/control");
 $("learning-control-status").textContent=state.paused
  ?"وضعیت: مکث سراسری فعال است؛ این تنظیم پس از خاموشی نیز حفظ می‌شود."
  :"وضعیت: مکث سراسری فعال نیست. پس از راه‌اندازی مجدد نیز می‌توانید بازیابی را بزنید.";
 $("pause-learning").disabled=!!state.paused;
 $("resume-learning").disabled=false;
}
$("pause-learning").onclick=()=>act(async()=>{
 await api("/pause",{method:"POST"});
 await refreshLearningControl();
 say("مکث سراسری ثبت شد؛ کار جاری در اولین نقطه امن متوقف می‌شود.");
});
$("resume-learning").onclick=()=>act(async()=>{
 const result=await api("/resume",{method:"POST",body:JSON.stringify({course_id:courseId})});
 await refreshLearningControl();
 await loadRuns();
 say(result.message||"فرایند یادگیری ادامه یافت.");
});
$("reset-learning-data").onclick=()=>act(async()=>{
 if(!confirm("همهٔ سرفصل‌ها، درس‌ها، نسخه‌های محتوا، سوابق اجرا و ردپاهای جست‌وجوی این ماژول پاک می‌شوند. تعریف دوره‌ها باقی می‌ماند. ادامه می‌دهید؟"))return;
 const phrase=prompt("برای تأیید پاک‌سازی کامل، عبارت RESET را وارد کنید:");
 if(phrase!=="RESET") { say("پاک‌سازی لغو شد."); return; }
 const result=await api("/reset-learning-data",{method:"POST"});
 await loadCourses(courseId||undefined);
 $("select-all-topics").checked=false;
 $("select-all-course-lessons").checked=false;
 $("select-all-runs").checked=false;
 say("پاک‌سازی انجام شد. سرفصل‌ها: "+result.deleted.topics+"، درس‌ها: "+result.deleted.lessons+"، نسخه‌ها: "+result.deleted.versions+"، سوابق اجرا: "+result.deleted.runs+" حذف شدند؛ دوره‌ها باقی ماندند و آماده شروع مجدد هستند.");
});
loadCourses(Number(localStorage.getItem("aiLearningCourseId"))||undefined)
 .then(async()=>{await refreshLearningControl();say("ماژول آماده است. مکث و بازیابی برای دریافت سرفصل و تولید دسته‌های درس فعال است؛ محتوای درس‌ها پیش از انتشار نیازمند بازبینی است.");window.setInterval(()=>{refreshLearningControl().catch(()=>{});refreshCourseProgress().catch(()=>{})},5000)})
 .catch(e=>say(e.message,true));
