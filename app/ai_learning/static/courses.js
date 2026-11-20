export const COURSES_CONTRACT_VERSION=1;
export function createCoursesModule(core){
 if(!core||core.CORE_CONTRACT_VERSION!==1)throw new Error("AI-learning courses requires core contract v1");
 const {api,say,$,el,data,progressMeter,act}=core;
let courseId=null,topics=[],selectedTopicIds=new Set(),renderedTopicsSnapshot="";
let courseListRequestSequence=0,courseDetailsRequestSequence=0,courseProgressRequestSequence=0;
let runHistoryLoader=async()=>{},lessonLoader=async()=>{};
function setRunHistoryLoader(loader){if(typeof loader!=="function")throw new TypeError("run history loader must be a function");runHistoryLoader=loader}
function setLessonLoader(loader){if(typeof loader!=="function")throw new TypeError("lesson loader must be a function");lessonLoader=loader}
function getCourseId(){return courseId}
function getTopics(){return topics}
async function loadCourses(keep){const requestSequence=++courseListRequestSequence;let d;try{d=await api("/courses")}catch(error){if(requestSequence!==courseListRequestSequence)return;throw error}if(requestSequence!==courseListRequestSequence)return;const s=$("course-select");s.replaceChildren();for(const c of d.items){const o=el("option",c.title+" (شناسه "+c.id+")");o.value=c.id;s.append(o)}if(keep&&d.items.some(c=>String(c.id)===String(keep)))s.value=String(keep);courseId=s.value?Number(s.value):null;if(courseId)localStorage.setItem("aiLearningCourseId",String(courseId));await loadCourse()}
function isCurrentCourse(requestedCourseId){const selectedValue=$("course-select").value;const selectedId=selectedValue?Number(selectedValue):null;return courseId===requestedCourseId&&selectedId===requestedCourseId}
async function loadCourse(){const nextCourseId=$("course-select").value?Number($("course-select").value):null;if(nextCourseId!==courseId)selectedTopicIds.clear();courseId=nextCourseId;const requestedCourseId=courseId;const requestSequence=++courseDetailsRequestSequence;courseProgressRequestSequence++;const lessonsRoot=$("lessons");if(lessonsRoot)lessonsRoot.dataset.loadSequence=String((Number(lessonsRoot.dataset.loadSequence)||0)+1);$("topics").replaceChildren();$("lessons").replaceChildren();if($("lesson-list-details"))$("lesson-list-details").open=false;if(!requestedCourseId){$("topics").append(el("p","هنوز دوره‌ای ثبت نشده است."));await runHistoryLoader(null);return}let course;try{course=await api("/courses/"+requestedCourseId)}catch(error){if(requestSequence!==courseDetailsRequestSequence||!isCurrentCourse(requestedCourseId))return;throw error}if(requestSequence!==courseDetailsRequestSequence||!isCurrentCourse(requestedCourseId))return;topics=course.topics||[];renderTopics();$("lessons").append(el("p","برای نمایش متن، روی «مشاهده درس‌ها و محتوا» در کارت سرفصل بزنید یا «نمایش همهٔ درس‌های دوره» را انتخاب کنید."));await runHistoryLoader(requestedCourseId)}
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
 renderedTopicsSnapshot=JSON.stringify(topics);
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
  head.append(el("strong",numberPath+". "+topic.title),el("span",status+" · "+percent+"٪","small topic-state"));
  card.append(head);
  const selection=el("div",undefined,"topic-select");selection.append(check);card.append(selection);
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
    await lessonLoader(topic.id,inlineLessons,true);
    inlineLessons.dataset.loaded="true";
   }finally{
    window.scrollTo(previousScrollX,previousScrollY);
   }
  },true);
  button("تلاش مجدد دریافت/اصلاح درس",async()=>{
   if(topic.approval_status!=="approved")throw Error("ابتدا سرفصل را تأیید کنید.");
   if(!confirm("برای این سرفصل یک تلاش تازه انجام شود؟ نسخه قبلی حفظ می‌شود و محتوای جدید برای بازبینی ثبت خواهد شد."))return;
   const result=await api("/browser/lessons",{method:"POST",body:JSON.stringify({course_id:courseId,topic_ids:[topic.id],request_key:"playwright-retry-topic-"+topic.id+"-"+Date.now(),force_retry:true})});
   await loadCourse();say("تلاش مجدد آغاز شد؛ اجرای #"+result.run_id+" در سوابق دیده می‌شود.");
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
$("course-form").onsubmit=e=>{e.preventDefault();const form=e.currentTarget;const payload=data(form);act(async()=>{const created=await api("/courses",{method:"POST",body:JSON.stringify(payload)});form.reset();await loadCourses(created.id);say("دوره ایجاد شد.")})};
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
 
 say("دریافت درس آغاز شد؛ اجرای #"+result.run_id+" در سوابق نمایش داده می‌شود. محتوای دریافت‌شده همچنان نیازمند بازبینی خواهد بود.");
});
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
async function refreshCourseProgress(){if(!courseId)return;const requestSequence=++courseProgressRequestSequence;const requestedCourseId=courseId;let d;try{d=await api("/courses/"+requestedCourseId)}catch(error){if(requestSequence!==courseProgressRequestSequence||!isCurrentCourse(requestedCourseId))return;throw error}if(requestSequence!==courseProgressRequestSequence||!isCurrentCourse(requestedCourseId))return;const nextTopics=d.topics||[];if(JSON.stringify(nextTopics)===renderedTopicsSnapshot)return;const inlineReviewOpen=!!document.querySelector(".topic-inline-lessons:not([hidden])");if(!inlineReviewOpen){topics=nextTopics;renderTopics();$("select-all-topics").checked=topics.length>0&&topics.every(t=>selectedTopicIds.has(Number(t.id)))}}

 return Object.freeze({setRunHistoryLoader,setLessonLoader,getCourseId,getTopics,isCurrentCourse,loadCourses,loadCourse,refreshCourseProgress,returnToTopic});
}
