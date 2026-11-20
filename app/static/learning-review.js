(() => {
 "use strict";
 const core=window.dashboardCore;
 const fallback=document.getElementById("learning-output");
 if(!core||core.version!==1){
  if(fallback)fallback.textContent="ماژول ارزیابی و بازبینی یادگیری بارگذاری نشد: قرارداد dashboardCore v1 در دسترس نیست.";
  return;
 }
 const {api,el}=core;
 const required=["learning-output","assessment-form-container","learning-form","learning-outline-output","learning-escalations","refresh-learning-escalations","factual-review-queue","refresh-factual-review-queue","factual-test-queue","refresh-factual-test-queue","factual-test-history","refresh-factual-test-history"];
 const missing=required.filter(id=>!el(id));
 if(missing.length){
  if(fallback)fallback.textContent="ماژول ارزیابی و بازبینی یادگیری به کنترل‌های لازم دسترسی ندارد: "+missing.join(", ");
  return;
 }
 let learningEscalationsRequestSequence=0;
let factualReviewQueueRequestSequence=0;
let factualTestQueueRequestSequence=0;
let factualTestHistoryRequestSequence=0;

function showLearningJob(result) {
  el("learning-output").textContent=JSON.stringify(result,null,2);
  const container=el("assessment-form-container");
  container.replaceChildren();
  const questions=result.assessment?.questions;
  if (!questions || result.status!=="completed") return;
  const form=document.createElement("form");
  const heading=document.createElement("h3"); heading.textContent="آزمون درس"; form.append(heading);
  questions.forEach((question,index)=>{
   const label=document.createElement("label"); label.textContent=(index+1)+". "+question;
   const answer=document.createElement("textarea"); answer.required=true; answer.maxLength=2000; answer.rows=2;
   answer.name="answer"; label.append(answer); form.append(label);
  });
  const submit=document.createElement("button"); submit.type="submit"; submit.textContent="ثبت پاسخ‌ها"; form.append(submit);
  form.addEventListener("submit",async event=>{
   event.preventDefault(); submit.disabled=true;
   try {
    const answers=Array.from(form.querySelectorAll("textarea")).map(input=>input.value);
    const attempt=await api("/api/learning/jobs/"+result.id+"/assessment",{method:"POST",body:JSON.stringify({answers})});
    el("learning-output").textContent=JSON.stringify({job:await api("/api/learning/jobs/"+result.id),latest_attempt:attempt},null,2);
    showLearningJob(await api("/api/learning/jobs/"+result.id));
   } catch(error) { el("learning-output").textContent=error.message; submit.disabled=false; }
  });
  container.append(form);
 }
 el("learning-form").addEventListener("submit", async event => {
  event.preventDefault();
  const output=el("learning-output");
  const outline=el("learning-outline-output");
  const submit=el("learning-form").querySelector('button[type="submit"]');
  output.textContent="درخواست پژوهش اینترنتی و ساخت سرفصل‌ها در صف ثبت می‌شود…";
  outline.replaceChildren();
  el("assessment-form-container").replaceChildren();
  submit.disabled=true;
  try {
   const task=await api("/api/learning/courses",{method:"POST",body:JSON.stringify({
    topic:el("topic").value,
    max_topics:Number(el("max-topics").value),
    source_text:el("source-text").value||null
   })});
   const taskId=task.task_id;
   output.textContent="وظیفهٔ پژوهش ثبت شد؛ شناسه: "+taskId+"؛ در حال جست‌وجوی منابع و ساخت سرفصل‌ها…";
   const poll=async()=>{
    try {
     const state=await api("/api/learning/courses/tasks/"+taskId);
     if(state.status==="succeeded"){
      const result=state.result||{};
      const activeLessonCount=result.queued_count||(result.active_job_id?1:0);
      output.textContent="پژوهش کامل شد. مدل: "+(result.model||"نامشخص")+
       "؛ منابع استفاده‌شده: "+(result.source_count||0)+
       "؛ سرفصل‌ها: "+((result.topics||[]).length)+
       "؛ همهٔ سرفصل‌ها در صف ترتیبی ثبت شدند و تولید درس‌ها یکی‌یکی انجام می‌شود؛ اکنون "+
       activeLessonCount+" درس فعال/در صف اجراست."+
       "\nمسیر آموزشی شمارهٔ "+result.path_id+" ایجاد شد.";
      const heading=document.createElement("h3");
      heading.textContent=result.title||"سرفصل‌های استخراج‌شده";
      outline.append(heading);
      if(result.notes){
       const note=document.createElement("p"); note.textContent=result.notes; outline.append(note);
      }
      const list=document.createElement("ol");
      (result.topics||[]).forEach(item=>{
       const li=document.createElement("li"); li.textContent=item; list.append(li);
      });
      outline.append(list);
      if(Array.isArray(result.sources)&&result.sources.length){
       const sourceHeading=document.createElement("h4"); sourceHeading.textContent="منابع پژوهش";
       const sourceList=document.createElement("ul");
       result.sources.forEach(source=>{
        const li=document.createElement("li");
        const link=document.createElement("a"); link.href=source.url; link.target="_blank";
        link.rel="noopener noreferrer"; link.textContent=source.title||source.url;
        li.append(link); sourceList.append(li);
       });
       outline.append(sourceHeading,sourceList);
      }
      const refresh=el("refresh-paths"); if(refresh)refresh.click();
      submit.disabled=false;
      return;
     }
     if(["failed","cancelled"].includes(state.status)){
      output.textContent="ساخت مسیر آموزشی ناموفق شد: "+(state.error||state.status);
      submit.disabled=false;
      return;
     }
     const phase=state.progress?.phase||state.status;
     const count=state.progress?.source_count||0;
     output.textContent="وضعیت پژوهش: "+phase+"؛ منابع دریافت‌شده: "+count+
      "؛ شناسهٔ وظیفه: "+taskId;
     setTimeout(poll,2000);
    } catch(error){
     output.textContent="دریافت وضعیت وظیفه با خطا مواجه شد: "+error.message+"؛ تلاش مجدد…";
     setTimeout(poll,5000);
    }
   };
   setTimeout(poll,1000);
  } catch(error) {
   output.textContent=error.message;
   submit.disabled=false;
  }
 });
 
 async function refreshLearningEscalations() {
  const requestSequence=++learningEscalationsRequestSequence;
  const container=el("learning-escalations");
  try {
   const result=await api("/api/learning/escalations?limit=50");
   if(requestSequence!==learningEscalationsRequestSequence)return;
   container.replaceChildren();
   const items=result.escalations||[];
   if(!items.length) { container.textContent="صف بازبینی انسانی خالی است."; return; }
   for(const item of items) {
    const card=document.createElement("article");
    const heading=document.createElement("h4");
    heading.textContent="#"+item.id+" — "+item.topic;
    const details=document.createElement("p");
    const reasonLabels={attempts_exhausted:"اتمام تلاش‌ها",permanent_failure:"خطای غیرقابل‌تکرار",quality_rejected:"رد کیفیت درس"};
    details.textContent=(reasonLabels[item.reason]||item.reason)+" | تلاش "+item.attempts+" از "+item.max_attempts+" | وضعیت درس: "+item.learning_status;
    const error=document.createElement("p");
    error.textContent=item.error_summary||"جزئیات خطا ثبت نشده است.";
    const note=document.createElement("textarea");
    note.maxLength=2000; note.rows=2; note.placeholder="یادداشت بازبینی (اختیاری)";
    const actions=document.createElement("div"); actions.className="cloud-actions";
    const detailsButton=document.createElement("button"); detailsButton.type="button"; detailsButton.textContent="نمایش جزئیات درس";
    detailsButton.addEventListener("click",async()=>{try{showLearningJob(await api("/api/learning/jobs/"+item.job_id));}catch(error){el("learning-output").textContent=error.message;}});
    const reviewedButton=document.createElement("button"); reviewedButton.type="button"; reviewedButton.textContent="بازبینی شد";
    reviewedButton.addEventListener("click",async()=>{try{await api("/api/learning/escalations/"+item.id,{method:"PATCH",body:JSON.stringify({status:"reviewed",review_note:note.value})});await refreshLearningEscalations();}catch(error){details.textContent=error.message;}});
    const dismissButton=document.createElement("button"); dismissButton.type="button"; dismissButton.className="danger"; dismissButton.textContent="بایگانی";
    dismissButton.addEventListener("click",async()=>{try{await api("/api/learning/escalations/"+item.id,{method:"PATCH",body:JSON.stringify({status:"dismissed",review_note:note.value})});await refreshLearningEscalations();}catch(error){details.textContent=error.message;}});
    actions.append(detailsButton,reviewedButton,dismissButton);
    card.append(heading,details,error,note,actions);
    container.append(card);
   }
  } catch(error) { if(requestSequence===learningEscalationsRequestSequence)container.textContent="بارگذاری صف بازبینی ناموفق بود: "+error.message; }
 }
 async function refreshFactualReviewQueue() {
  const requestSequence=++factualReviewQueueRequestSequence;
  const container=el("factual-review-queue");
  container.textContent="در حال بارگذاری صف منبع…";
  try {
   const result=await api("/api/factual-knowledge/review-queue?limit=100");
   if(requestSequence!==factualReviewQueueRequestSequence)return;
   container.replaceChildren();
   const items=result.items||[];
   if(!items.length) { container.textContent="ادعای خام دارای منبع برای بازبینی وجود ندارد."; return; }
   for(const item of items) {
    const card=document.createElement("article");
    const heading=document.createElement("h4");
    heading.textContent="#"+item.id+" — "+item.domain_name+" / "+item.environment_version;
    const claim=document.createElement("p");
    claim.textContent=item.claim;
    const meta=document.createElement("p");
    meta.textContent="سطح: "+item.evidence_level+" | وضعیت: "+item.lifecycle_status+" | ریسک: "+item.risk_level;
    const sources=document.createElement("ul");
    for(const evidence of (item.evidence||[])) {
     const li=document.createElement("li");
     const title=document.createElement("p");
     title.textContent=(evidence.source_type||"source")+" — "+(evidence.source_ref||"بدون نشانی");
     li.append(title);
     if(evidence.excerpt) { const excerpt=document.createElement("p"); excerpt.textContent=evidence.excerpt; li.append(excerpt); }
     if(evidence.source_hash) { const hash=document.createElement("small"); hash.textContent="SHA-256: "+evidence.source_hash; li.append(hash); }
     sources.append(li);
    }
    const note=document.createElement("textarea");
    note.rows=3; note.maxLength=1000; note.required=true;
    note.placeholder="یادداشت بازبینی: منبع چه چیزی را پشتیبانی می‌کند یا چرا کافی نیست؟ (حداقل ۲۰ نویسه)";
    const confirmLabel=document.createElement("label");
    const confirm=document.createElement("input");
    confirm.type="checkbox";
    const confirmText=document.createElement("span");
    confirmText.textContent="من منبع و ادعای دقیق را بررسی کردم و تصمیم ثبت‌شده بازتاب بررسی من است.";
    confirmLabel.append(confirm,confirmText);
    const submit=document.createElement("button");
    submit.type="button"; submit.textContent="تأیید پشتیبانی منبع (L1)";
    const reject=document.createElement("button"); reject.type="button"; reject.className="danger"; reject.textContent="رد منبع و انتقال به نیازمند بازبینی";
    const status=document.createElement("p");
    submit.addEventListener("click",async()=>{
     if(!confirm.checked) { status.textContent="برای ثبت، تأیید صریح لازم است."; return; }
     if(note.value.trim().length<20) { status.textContent="یادداشت بازبینی باید حداقل ۲۰ نویسه داشته باشد."; return; }
     submit.disabled=true; status.textContent="در حال ثبت بازبینی…";
     try {
      const response=await api("/api/factual-knowledge/"+item.id+"/approve-source",{method:"POST",body:JSON.stringify({confirm:true,source_supports_claim:true,review_note:note.value.trim()})});
      status.textContent=response.message||("ثبت شد؛ سطح فعلی: "+response.evidence_level+"، وضعیت: "+response.lifecycle_status);
      await refreshFactualReviewQueue();
     } catch(error) { status.textContent=error.message; submit.disabled=false; }
    });
    reject.addEventListener("click",async()=>{
     if(!confirm.checked) { status.textContent="برای ثبت، تأیید صریح لازم است."; return; }
     if(note.value.trim().length<20) { status.textContent="یادداشت بازبینی باید حداقل ۲۰ نویسه داشته باشد."; return; }
     reject.disabled=true; status.textContent="در حال ثبت رد منبع…";
     try {
      const response=await api("/api/factual-knowledge/"+item.id+"/reject-source",{method:"POST",body:JSON.stringify({confirm:true,source_supports_claim:false,review_note:note.value.trim()})});
      status.textContent=response.message||("ثبت شد؛ وضعیت: "+response.lifecycle_status);
      await refreshFactualReviewQueue();
     } catch(error) { status.textContent=error.message; reject.disabled=false; }
    });
    card.append(heading,claim,meta,sources,note,confirmLabel,submit,reject,status);
    container.append(card);
   }
  } catch(error) { if(requestSequence===factualReviewQueueRequestSequence)container.textContent="بارگذاری صف منبع ناموفق بود: "+error.message; }
 }
 async function refreshFactualTestQueue() {
  const requestSequence=++factualTestQueueRequestSequence;
  const container=el("factual-test-queue");
  container.textContent="در حال بارگذاری صف آزمون…";
  try {
   const result=await api("/api/factual-knowledge/test-queue?limit=50");
   if(requestSequence!==factualTestQueueRequestSequence)return;
   container.replaceChildren();
   const items=result.items||[];
   if(!items.length) { container.textContent="ادعای واجد شرایطی برای آزمون وجود ندارد."; return; }
   for(const item of items) {
    const card=document.createElement("article");
    const heading=document.createElement("h4");
    heading.textContent="#"+item.id+" — "+(item.domain_name==="sqlite" ? "SQLite" : "Python")+" "+item.environment_version;
    const claim=document.createElement("p");
    claim.textContent=item.claim;
    const sources=document.createElement("ul");
    for(const evidence of (item.evidence||[])) {
     const li=document.createElement("li");
     li.textContent=(evidence.source_type||"source")+" — "+(evidence.source_ref||"")+(evidence.source_hash ? " — SHA-256: "+evidence.source_hash : "");
     sources.append(li);
     if(evidence.excerpt) { const excerpt=document.createElement("p"); excerpt.textContent=evidence.excerpt; li.append(excerpt); }
    }
    const testName=document.createElement("input");
    testName.type="text"; testName.maxLength=300; testName.required=true;
    testName.placeholder="نام آزمون (مثلاً assert روی شرط نادرست)";
    const sourceCode=document.createElement("textarea");
    sourceCode.rows=6; sourceCode.maxLength=65536; sourceCode.required=true;
    sourceCode.placeholder=item.domain_name==="sqlite" ? "کد Python برای آزمون SQLite؛ از sqlite3 در حافظه استفاده کنید و دست‌کم یک assert داشته باشد" : "کد Python آزمون؛ باید دست‌کم یک assert داشته باشد";
    const oracleNote=document.createElement("textarea");
    oracleNote.rows=3; oracleNote.maxLength=1000; oracleNote.required=true;
    oracleNote.placeholder="توضیح اینکه آزمون با کدام oracle مستقل تطبیق داده شده (حداقل ۲۰ نویسه)";
    const timeout=document.createElement("input");
    timeout.type="number"; timeout.min="1"; timeout.max="120"; timeout.value="10";
    const confirmLabel=document.createElement("label");
    const confirm=document.createElement("input"); confirm.type="checkbox";
    const confirmText=document.createElement("span");
    confirmText.textContent="کد آزمون را بررسی کرده‌ام، با oracle مستقل تطبیق داده‌ام و اجرای ایزوله را صریحاً تأیید می‌کنم.";
    confirmLabel.append(confirm,confirmText);
    const submit=document.createElement("button");
    submit.type="button"; submit.textContent="ارسال آزمون به sandbox";
    const status=document.createElement("p");
    submit.addEventListener("click",async()=>{
     if(!confirm.checked) { status.textContent="تأیید صریح اجرای آزمون و بازبینی oracle لازم است."; return; }
     if(!testName.value.trim()||!sourceCode.value.trim()||oracleNote.value.trim().length<20) { status.textContent="نام، کد آزمون و یادداشت oracle را کامل کنید."; return; }
     const seconds=Number(timeout.value);
     if(!Number.isInteger(seconds)||seconds<1||seconds>120) { status.textContent="مهلت باید بین ۱ تا ۱۲۰ ثانیه باشد."; return; }
     submit.disabled=true; status.textContent="در حال ثبت وظیفهٔ آزمون…";
     try {
      const response=await api("/api/factual-knowledge/"+item.id+"/run-test",{method:"POST",body:JSON.stringify({confirm:true,oracle_reviewed:true,oracle_review_note:oracleNote.value.trim(),test_name:testName.value.trim(),source_code:sourceCode.value,timeout_seconds:seconds,max_output_bytes:32768})});
      status.textContent="وظیفه ثبت شد؛ شناسه: "+response.task.id+". وضعیت را در مدیریت وظایف پیگیری کنید. "+(response.message||"");
      card.dataset.taskQueued="true";
      await refreshFactualTestQueue();
     } catch(error) { status.textContent=error.message; submit.disabled=false; }
    });
    card.append(heading,claim,sources,testName,sourceCode,oracleNote,timeout,confirmLabel,submit,status);
    container.append(card);
   }
  } catch(error) { if(requestSequence===factualTestQueueRequestSequence)container.textContent="بارگذاری صف آزمون ناموفق بود: "+error.message; }
 }
 async function refreshFactualTestHistory() {
  const requestSequence=++factualTestHistoryRequestSequence;
  const container=el("factual-test-history");
  container.textContent="در حال بارگذاری تاریخچهٔ آزمون‌ها…";
  try {
   const result=await api("/api/factual-knowledge/test-history?limit=50");
   if(requestSequence!==factualTestHistoryRequestSequence)return;
   container.replaceChildren();
   const items=result.items||[];
   if(!items.length) { container.textContent="هنوز نتیجهٔ آزمونی ثبت نشده است."; return; }
   const outcomeLabels={passed:"موفق",failed:"ناموفق",inconclusive:"نامشخص",error:"خطای اجرا",timeout:"پایان مهلت"};
   for(const item of items) {
    const card=document.createElement("article");
    const heading=document.createElement("h4");
    heading.textContent="#"+item.test_id+" — ادعای #"+item.knowledge_id+" — "+(outcomeLabels[item.outcome]||item.outcome);
    const claim=document.createElement("p"); claim.textContent=item.claim;
    const details=document.createElement("p");
    details.textContent="آزمون: "+item.test_name+" | محیط اجرا: "+item.environment_name+" "+item.test_environment_version+" | نسخهٔ ادعا: "+item.current_environment_version+" | سطح فعلی: "+item.evidence_level+" | وضعیت: "+item.lifecycle_status;
    const summary=document.createElement("p"); summary.textContent=item.result_summary||"خلاصه‌ای ثبت نشده است.";
    const hashes=document.createElement("p");
    hashes.textContent="Hash کد: "+(item.command_hash||"ناموجود")+" | Hash گزارش: "+(item.log_hash||"ناموجود");
    const timestamp=document.createElement("small"); timestamp.textContent=item.created_at||"";
    card.append(heading,claim,details,summary,hashes,timestamp);
    container.append(card);
   }
  } catch(error) { if(requestSequence===factualTestHistoryRequestSequence)container.textContent="بارگذاری تاریخچهٔ آزمون ناموفق بود: "+error.message; }
 }
 el("refresh-factual-test-history").addEventListener("click",refreshFactualTestHistory);
 refreshFactualTestHistory();
 el("refresh-factual-test-queue").addEventListener("click",refreshFactualTestQueue);
 refreshFactualTestQueue();
 el("refresh-factual-review-queue").addEventListener("click",refreshFactualReviewQueue);
 refreshFactualReviewQueue();
 el("refresh-learning-escalations").addEventListener("click",refreshLearningEscalations);
 refreshLearningEscalations();
 
})();