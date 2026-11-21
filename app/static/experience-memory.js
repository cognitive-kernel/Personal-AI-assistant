/* Experience memory UI.
 * Owns experience curation, source history, feedback, and controlled test workflows.
 * Shared dependency contract v1: dashboardCore.{api,el,appendText}.
 */
(() => {
 "use strict";
 function createLocalDashboardCore() {
 const el = id => document.getElementById(id);
 async function api(path, options = {}) {
  const response = await fetch(path, {
   headers: {"Content-Type": "application/json", ...(options.headers || {})},
   ...options
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || ("HTTP " + response.status));
  return payload;
 }
 function appendText(parent, tag, text, className) {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  parent.append(node);
  return node;
 }
 return Object.freeze({version: 1, el, api, appendText});
}

 const core=window.dashboardCore && window.dashboardCore.version===1 ? window.dashboardCore : createLocalDashboardCore();
 if(!core||core.version!==1||typeof core.api!=="function"||typeof core.el!=="function"||typeof core.appendText!=="function"){
  const output=document.getElementById("experience-output");
  if(output) output.textContent="ماژول حافظهٔ تجربه بارگذاری نشد: قرارداد هستهٔ داشبورد در دسترس نیست.";
  throw new Error("Experience memory module requires dashboardCore contract v1.");
 }
 const {api,el,appendText}=core;
// Experience memory: untrusted record/source text is always rendered as text.
const experienceStatuses = [
 ["identified","شناسایی‌شده"],["researching","در حال پژوهش"],
 ["ready_to_test","آمادهٔ آزمون"],["testing","در حال آزمون"],
 ["failed","ناموفق"],["needs_review","نیازمند بازبینی"]
];
async function refreshExperiences() {
 const output=el("experience-output");
 output.textContent="در حال بارگذاری تجربه‌ها…";
 try {
  const result=await api("/api/experiences?limit=100");
  output.textContent="تعداد تجربه‌ها: "+result.count;
  renderExperienceItems(result.items||[]);
 } catch(error) {
  output.textContent="بارگذاری تجربه‌ها ناموفق بود: "+error.message;
  el("experience-list").replaceChildren();
 }
}
function renderExperienceItems(items) {
 const container=el("experience-list");
 container.replaceChildren();
 if(!items.length) {
  appendText(container,"p","تجربه‌ای برای نمایش پیدا نشد.");
  return;
 }
 items.forEach(item=>{
  const card=document.createElement("article");
  card.className="cloud-candidate";
  appendText(card,"h3",(item._memory_group?item._memory_group+" — ":"")+(item.topic||"بدون موضوع"));
  appendText(card,"p","وضعیت: "+item.status+" | نسخه: "+item.version+" | شناسه: "+item.id,"cloud-status");
  appendText(card,"p","مشکل: "+(item.problem||"ثبت نشده"));
  if(item.hypothesis) appendText(card,"p","فرضیه: "+item.hypothesis);
  if(item.takeaway) appendText(card,"p","جمع‌بندی: "+item.takeaway);
  appendText(card,"h4","شواهد منبع");
  const evidenceList=document.createElement("ul");
  (item.source_evidence||[]).forEach(evidence=>{
   const li=document.createElement("li");
   appendText(li,"span",(evidence.claim||"ادعای بدون توضیح")+" — ");
   try {
    const source=new URL(evidence.url);
    if(source.protocol!=="https:") throw new Error("HTTPS required");
    const link=document.createElement("a");
    link.href=source.href; link.target="_blank"; link.rel="noopener noreferrer";
    link.textContent=evidence.url; li.append(link);
   } catch(_) { appendText(li,"span","نشانی HTTPS معتبر نیست."); }
   evidenceList.append(li);
  });
  if(!(item.source_evidence||[]).length) appendText(card,"p","هنوز شواهد منبع ثبت نشده است.","muted");
  else {
   card.append(evidenceList);
   const refreshSourcesButton=document.createElement("button");
   refreshSourcesButton.type="button"; refreshSourcesButton.textContent="بازبینی مجدد منابع ذخیره‌شده";
   const sourceHistoryButton=document.createElement("button");
   sourceHistoryButton.type="button"; sourceHistoryButton.textContent="نمایش تاریخچهٔ بازبینی منابع";
   const sourceCheckOutput=document.createElement("pre");
   refreshSourcesButton.addEventListener("click",async()=>{
    if(!window.confirm("فقط نشانی‌های HTTPS ذخیره‌شده بررسی می‌شوند. متن کامل صفحه ذخیره نمی‌شود و تجربه خودکار تأیید یا ویرایش نخواهد شد. ادامه می‌دهید؟")) return;
    refreshSourcesButton.disabled=true; sourceCheckOutput.textContent="در حال بررسی حداکثر سه منبع…";
    try {
     const result=await api("/api/experiences/"+item.id+"/refresh-sources",{
      method:"POST",body:JSON.stringify({confirm:true})
     });
     sourceCheckOutput.textContent=(result.checks||[]).map(check=>
      check.outcome+" — "+check.source_url+(check.previous_content_sha256?" — hash قبلی: "+check.previous_content_sha256:"")
     ).join("\n")+"\n\nتجربه تغییر نکرده و تأیید نشده است. تغییر منبع نیازمند بازبینی دستی است.";
    } catch(error) { sourceCheckOutput.textContent="بازبینی منابع ناموفق بود: "+error.message; }
    finally { refreshSourcesButton.disabled=false; }
   });
   sourceHistoryButton.addEventListener("click",async()=>{
    sourceCheckOutput.textContent="در حال دریافت تاریخچه…";
    try {
     const result=await api("/api/experiences/"+item.id+"/source-checks");
     sourceCheckOutput.textContent=(result.checks||[]).map(check=>
      check.checked_at+" — "+check.outcome+" — "+check.source_url
     ).join("\n")||"هنوز بازبینی منبعی ثبت نشده است.";
    } catch(error) { sourceCheckOutput.textContent="تاریخچه دریافت نشد: "+error.message; }
   });
   card.append(refreshSourcesButton,sourceHistoryButton,sourceCheckOutput);
  }
  const tests=item.test_results||[];
  appendText(card,"h4","نتایج آزمون ثبت‌شده");
  if(!tests.length) appendText(card,"p","نتیجهٔ آزمون ثبت نشده است.","muted");
  else {
   const testList=document.createElement("ul");
   tests.forEach(test=>appendText(testList,"li",(test.test||"آزمون نامشخص")+" — "+(test.passed===true?"موفق":test.passed===false?"ناموفق":"نامشخص")+(test.actual?"؛ نتیجه: "+test.actual:"")));
   card.append(testList);
  }
  const versionsButton=document.createElement("button");
  versionsButton.type="button"; versionsButton.textContent="نمایش تاریخچهٔ نسخه‌ها";
  const versionsOutput=document.createElement("pre");
  versionsButton.addEventListener("click",async()=>{
   versionsOutput.textContent="در حال دریافت تاریخچه…";
   try {
    const result=await api("/api/experiences/"+item.id+"/versions");
    versionsOutput.textContent=JSON.stringify(result.versions,null,2);
   } catch(error) { versionsOutput.textContent="تاریخچه دریافت نشد: "+error.message; }
  });
  card.append(versionsButton,versionsOutput);
  if(item.status==="verified") {
   appendText(card,"h4","بازخورد برای بهبود رتبه‌بندی بازیابی");
   appendText(card,"p","این رأی فقط روی اولویت بازیابی اثر می‌گذارد و وضعیت تأیید تجربه را تغییر نمی‌دهد.","muted");
   const feedbackStatus=document.createElement("p"); feedbackStatus.className="muted";
   feedbackStatus.textContent="در حال دریافت بازخورد ثبت‌شده…";
   const positiveButton=document.createElement("button");
   positiveButton.type="button"; positiveButton.textContent="مفید بود";
   const negativeButton=document.createElement("button");
   negativeButton.type="button"; negativeButton.textContent="مفید نبود";
   const clearFeedbackButton=document.createElement("button");
   clearFeedbackButton.type="button"; clearFeedbackButton.textContent="پاک‌کردن رأی";
   const sendFeedback=async rating=>{
    positiveButton.disabled=true; negativeButton.disabled=true; clearFeedbackButton.disabled=true;
    feedbackStatus.textContent="در حال ذخیرهٔ رأی…";
    try {
     if(rating===null) await api("/api/experiences/"+item.id+"/feedback",{method:"DELETE"});
     else await api("/api/experiences/"+item.id+"/feedback",{method:"POST",body:JSON.stringify({rating})});
     await loadFeedback();
    } catch(error) { feedbackStatus.textContent="ذخیرهٔ رأی ناموفق بود: "+error.message; }
    finally { positiveButton.disabled=false; negativeButton.disabled=false; clearFeedbackButton.disabled=false; }
   };
   const loadFeedback=async()=>{
    try {
     const result=await api("/api/experiences/"+item.id+"/feedback");
     feedbackStatus.textContent=!result.feedback?"هنوز رأیی ثبت نشده است.":result.feedback.rating===1?"رأی فعلی: مفید بود":"رأی فعلی: مفید نبود";
     positiveButton.disabled=result.feedback?.rating===1;
     negativeButton.disabled=result.feedback?.rating===-1;
     clearFeedbackButton.disabled=!result.feedback;
    } catch(error) { feedbackStatus.textContent="وضعیت رأی دریافت نشد: "+error.message; }
   };
   positiveButton.addEventListener("click",()=>sendFeedback(1));
   negativeButton.addEventListener("click",()=>sendFeedback(-1));
   clearFeedbackButton.addEventListener("click",()=>sendFeedback(null));
   card.append(feedbackStatus,positiveButton,negativeButton,clearFeedbackButton);
   loadFeedback();
  }
  if(item.status==="verified") {
   appendText(card,"p","این تجربه تأییدشده است و از رابط عمومی قابل ویرایش نیست.","muted");
   container.append(card);
   return;
  }
  const form=document.createElement("form");
  appendText(form,"h4","بازبینی تجربه");
  const statusLabel=document.createElement("label"); statusLabel.textContent="وضعیت";
  const statusSelect=document.createElement("select"); statusSelect.required=true;
  experienceStatuses.forEach(([value,label])=>{
   const option=document.createElement("option"); option.value=value; option.textContent=label;
   statusSelect.append(option);
  });
  statusSelect.value=experienceStatuses.some(([value])=>value===item.status)?item.status:"identified";
  statusLabel.append(statusSelect); form.append(statusLabel);
  const evidenceLabel=document.createElement("label"); evidenceLabel.textContent="شواهد منبع به‌شکل آرایهٔ JSON";
  const evidenceInput=document.createElement("textarea"); evidenceInput.rows=3;
  evidenceInput.value=JSON.stringify(item.source_evidence||[],null,2);
  evidenceLabel.append(evidenceInput); form.append(evidenceLabel);
  const planLabel=document.createElement("label"); planLabel.textContent="برنامهٔ آزمون به‌شکل آرایهٔ JSON";
  const planInput=document.createElement("textarea"); planInput.rows=3;
  planInput.value=JSON.stringify(item.test_plan||[],null,2);
  planLabel.append(planInput); form.append(planLabel);
  const takeawayLabel=document.createElement("label"); takeawayLabel.textContent="جمع‌بندی";
  const takeawayInput=document.createElement("textarea"); takeawayInput.rows=2; takeawayInput.maxLength=10000;
  takeawayInput.value=item.takeaway||"";
  takeawayLabel.append(takeawayInput); form.append(takeawayLabel);
  const submit=document.createElement("button"); submit.type="submit"; submit.textContent="ذخیرهٔ بازبینی";
  const feedback=document.createElement("p"); feedback.className="muted";
  form.append(submit,feedback);
  form.addEventListener("submit",async event=>{
   event.preventDefault(); submit.disabled=true; feedback.textContent="در حال ذخیره…";
   try {
    let sourceEvidence,testPlan;
    try { sourceEvidence=JSON.parse(evidenceInput.value||"[]"); testPlan=JSON.parse(planInput.value||"[]"); }
    catch(_) { throw new Error("شواهد و برنامهٔ آزمون باید JSON معتبر باشند."); }
    if(!Array.isArray(sourceEvidence)||!Array.isArray(testPlan)) throw new Error("شواهد و برنامهٔ آزمون باید آرایهٔ JSON باشند.");
    await api("/api/experiences/"+item.id,{
     method:"PATCH",
     body:JSON.stringify({status:statusSelect.value,source_evidence:sourceEvidence,test_plan:testPlan,takeaway:takeawayInput.value})
    });
    feedback.textContent="بازبینی ذخیره شد.";
    await refreshExperiences();
   } catch(error) {
    feedback.textContent="ذخیره نشد: "+error.message;
    submit.disabled=false;
   }
  });
  card.append(form);
  const runnableTests=(item.test_plan||[]).filter(entry=>entry&&typeof entry.test==="string"&&entry.test.trim());
  if(!(item.source_evidence||[]).length||!runnableTests.length) {
   appendText(card,"p","برای اجرای آزمون، ابتدا شواهد HTTPS و حداقل یک مورد برنامهٔ آزمون را ذخیره کنید.","muted");
  } else {
   const runnerForm=document.createElement("form");
   appendText(runnerForm,"h4","اجرای کنترل‌شدهٔ آزمون در Docker Sandbox");
   appendText(runnerForm,"p","کد آزمون باید حداقل یک assert داشته باشد. خروج موفق فقط نتیجهٔ همین آزمون را ثبت می‌کند؛ صحت خود آزمون را پیش از اجرا بررسی کنید. رضایت Sandbox نیز باید جداگانه فعال باشد.","muted");
   const testLabel=document.createElement("label"); testLabel.textContent="مورد برنامهٔ آزمون";
   const testSelect=document.createElement("select"); testSelect.required=true;
   runnableTests.forEach(entry=>{
    const option=document.createElement("option"); option.value=entry.test.trim(); option.textContent=entry.test.trim();
    testSelect.append(option);
   });
   testLabel.append(testSelect); runnerForm.append(testLabel);
   const sourceLabel=document.createElement("label"); sourceLabel.textContent="کد Python آزمون";
   const sourceInput=document.createElement("textarea"); sourceInput.rows=8; sourceInput.maxLength=65536; sourceInput.required=true;
   sourceInput.placeholder="assert actual_value == expected_value"; sourceInput.setAttribute("aria-label", "کد Python آزمون");
   sourceLabel.append(sourceInput); runnerForm.append(sourceLabel);
   const consentLabel=document.createElement("label");
   const confirmInput=document.createElement("input"); confirmInput.type="checkbox"; confirmInput.required=true; confirmInput.style.width="auto";
   consentLabel.append(confirmInput,document.createTextNode(" من کد را بررسی کرده‌ام و اجرای آن را در Sandbox محلی تأیید می‌کنم."));
   runnerForm.append(consentLabel);
   const runButton=document.createElement("button"); runButton.type="submit"; runButton.textContent="صف‌بندی آزمون";
   const runFeedback=document.createElement("pre"); runFeedback.className="muted";
   runnerForm.append(runButton,runFeedback);
   runnerForm.addEventListener("submit",async event=>{
    event.preventDefault(); runButton.disabled=true; runFeedback.textContent="در حال صف‌بندی آزمون…";
    try {
     const queued=await api("/api/experiences/"+item.id+"/run-test",{
      method:"POST",
      body:JSON.stringify({
       confirm:true,test_name:testSelect.value,source_code:sourceInput.value,
       timeout_seconds:10,max_output_bytes:32768
      })
     });
     const taskId=queued.task.id;
     runFeedback.textContent="آزمون در صف قرار گرفت: #"+taskId;
     const poll=async()=>{
      try {
       const current=await api("/api/tasks/"+taskId);
       if(["queued","running","pause_requested","paused","cancel_requested"].includes(current.status)) {
        runFeedback.textContent="وضعیت آزمون #"+taskId+": "+current.status;
        setTimeout(poll,1000);
        return;
       }
       if(current.status==="succeeded") {
        runFeedback.textContent=JSON.stringify(current.result||{status:current.status},null,2);
        await refreshExperiences();
       } else {
        runFeedback.textContent="آزمون تکمیل نشد: "+(current.error||current.status);
        runButton.disabled=false;
       }
      } catch(error) {
       runFeedback.textContent="وضعیت آزمون قابل دریافت نیست: "+error.message;
       runButton.disabled=false;
      }
     };
     setTimeout(poll,500);
    } catch(error) {
     runFeedback.textContent="آزمون صف‌بندی نشد: "+error.message;
     runButton.disabled=false;
    }
   });
   card.append(runnerForm);
  }
  container.append(card);
 });
}
let experienceConsentRefreshSequence=0;
async function refreshExperienceAutoCaptureConsent() {
 const requestSequence=++experienceConsentRefreshSequence;
 const output=el("experience-auto-capture-status");
 try {
  const result=await api("/api/experiences/auto-capture/consent");
  if(requestSequence!==experienceConsentRefreshSequence)return;
  const consent=result.consent||{};
  if(document.activeElement!==el("experience-auto-capture-confirm"))
   el("experience-auto-capture-confirm").checked=consent.enabled===true;
  output.textContent=(consent.enabled
   ? "ذخیرهٔ خودکار فعال است؛ فقط پرسش‌های فنیِ دارای منبع ذخیره می‌شوند."
   : "ذخیرهٔ خودکار غیرفعال است؛ هیچ پرسش یا پاسخی خودکار ذخیره نمی‌شود.")+
   "\n\nنسخهٔ سیاست: "+consent.policy_version+
   "\n\nتاریخچهٔ رضایت:\n"+JSON.stringify(consent.events||[],null,2);
 } catch(error) {
  if(requestSequence!==experienceConsentRefreshSequence)return;
  output.textContent="وضعیت رضایت قابل دریافت نیست: "+error.message;
 }
}
el("experience-auto-capture-enable").addEventListener("click",async()=>{
 experienceConsentRefreshSequence+=1;
 if(!el("experience-auto-capture-confirm").checked) {
  el("experience-auto-capture-status").textContent="برای فعال‌سازی، ابتدا رضایت صریح را علامت بزنید.";
  return;
 }
 el("experience-auto-capture-status").textContent="در حال ثبت رضایت…";
 try {
  await api("/api/experiences/auto-capture/consent",{method:"POST",body:JSON.stringify({confirm:true})});
  await refreshExperienceAutoCaptureConsent();
 } catch(error) {
  await refreshExperienceAutoCaptureConsent();
  el("experience-auto-capture-status").textContent="رضایت ثبت نشد: "+error.message;
 }
});
el("experience-auto-capture-revoke").addEventListener("click",async()=>{
 experienceConsentRefreshSequence+=1;
 el("experience-auto-capture-status").textContent="در حال لغو رضایت…";
 try {
  await api("/api/experiences/auto-capture/consent",{method:"DELETE"});
  el("experience-auto-capture-confirm").checked=false;
  await refreshExperienceAutoCaptureConsent();
 } catch(error) {
  await refreshExperienceAutoCaptureConsent();
  el("experience-auto-capture-status").textContent="لغو رضایت انجام نشد: "+error.message;
 }
});
el("experience-auto-capture-refresh").addEventListener("click",refreshExperienceAutoCaptureConsent);
refreshExperienceAutoCaptureConsent();

el("experience-refresh").addEventListener("click",refreshExperiences);
el("experience-create-form").addEventListener("submit",async event=>{
 event.preventDefault();
 const button=event.submitter;
 if(button) button.disabled=true;
 el("experience-output").textContent="در حال ثبت تجربه…";
 try {
  await api("/api/experiences",{method:"POST",body:JSON.stringify({
   topic:el("experience-topic").value,
   problem:el("experience-problem").value,
   hypothesis:el("experience-hypothesis").value
  })});
  el("experience-topic").value=""; el("experience-problem").value=""; el("experience-hypothesis").value="";
  el("experience-output").textContent="تجربه ثبت شد؛ وضعیت اولیه آن تأییدنشده است.";
  await refreshExperiences();
 } catch(error) { el("experience-output").textContent="ثبت تجربه ناموفق بود: "+error.message; }
 finally { if(button) button.disabled=false; }
});
el("experience-search-form").addEventListener("submit",async event=>{
 event.preventDefault();
 const query=el("experience-query").value.trim();
 const output=el("experience-output");
 output.textContent="در حال جست‌وجوی حافظه…";
 try {
  const result=await api("/api/experiences/search?query="+encodeURIComponent(query));
  const verified=(result.verified||[]).map(item=>({...item,_memory_group:"راهنمای تجربهٔ تأییدشده"}));
  const warnings=(result.warnings||[]).map(item=>({...item,_memory_group:"هشدار از تجربهٔ ناموفق/نیازمند بازبینی"}));
  output.textContent="تجربه‌های تأییدشده: "+verified.length+" | هشدارها: "+warnings.length;
  renderExperienceItems([...verified,...warnings]);
 } catch(error) {
  output.textContent="جست‌وجو ناموفق بود: "+error.message;
  el("experience-list").replaceChildren();
 }
});
})();
