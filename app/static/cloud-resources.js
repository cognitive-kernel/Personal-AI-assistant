/* Cloud resource discovery and review UI.
 * Owns its request sequencing, rendering, and event handlers.
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
  const output=document.getElementById("cloud-output");
  if(output) output.textContent="ماژول منابع ابری بارگذاری نشد: قرارداد هستهٔ داشبورد در دسترس نیست.";
  throw new Error("Cloud resources module requires the dashboardCore contract.");
 }
 const {api,el,appendText}=core;
let cloudResourcesRequestSequence=0;
async function refreshCloudResources() {
 const requestSequence=++cloudResourcesRequestSequence;
 const output=el("cloud-output");
 output.textContent="در حال بارگذاری نامزدهای منابع…";
 try {
  const limitControl=el("cloud-candidate-limit");
  const requestedLimit=Number(limitControl?.value||10);
  const candidateLimit=[10,25,50,100].includes(requestedLimit)?requestedLimit:10;
  const result=await api("/api/cloud-resources?limit="+candidateLimit);
  if(requestSequence!==cloudResourcesRequestSequence)return;
  const run=result.latest_run;
  const runSummary=run
   ? "آخرین اجرا: "+run.status+" | زمان: "+(run.finished_at||run.started_at)+" | منابع دیده‌شده: "+run.sources_seen+" | نامزدهای ذخیره‌شده: "+run.candidates_upserted
   : "هنوز اجرای جست‌وجو ثبت نشده است.";
  const providerLines=[];
  (run?.query_results||[]).forEach(queryResult=>{
   providerLines.push("پرس‌وجو: "+queryResult.query+" | صفحات قابل‌دریافت: "+queryResult.result_count
    +(queryResult.error?" | خطا: "+queryResult.error:""));
   const filter=queryResult.candidate_filter;
   if(filter) providerLines.push("  فیلتر محتوا: بررسی="+filter.documents_checked
    +", مرتبط="+filter.relevant_documents
    +", نامرتبط="+filter.rejected_as_irrelevant
    +", تکراری نامزدها="+filter.duplicate_candidates_suppressed
    +(filter.not_checked_due_to_candidate_cap?", به‌دلیل سقف بررسی‌نشده="+filter.not_checked_due_to_candidate_cap:""));
   (queryResult.provider_diagnostics||[]).forEach(provider=>{
    const attempts=(provider.attempts||[]).map(attempt=>{
     const outcome=attempt.error?"خطا="+attempt.error:"بدون خطا";
     return attempt.variant+(attempt.endpoint_variant?"/"+attempt.endpoint_variant:"")+": parse="+attempt.parsed_result_count
      +", پذیرفته="+attempt.accepted_result_count
      +", یکتا="+attempt.unique_result_count
      +", تکراری="+attempt.duplicate_result_count
      +" ("+outcome+")";
    }).join("؛ ");
    providerLines.push("  "+provider.provider+": مجموع parse="+provider.parsed_result_count
     +", پذیرفته="+provider.accepted_result_count
     +", تکراری حذف‌شده="+provider.duplicate_result_count
     +(attempts?" | تلاش‌ها: "+attempts:""));
   });
  });
  output.textContent=runSummary+(providerLines.length
   ? "\n\nجزئیات موتورهای جست‌وجو:\n"+providerLines.join("\n")
   : "");
  renderCloudCandidates(result.candidates||[]);
 } catch(error) {
  if(requestSequence===cloudResourcesRequestSequence) {
   output.textContent="خطا در بارگذاری منابع: "+error.message;
   el("cloud-candidates").replaceChildren();
  }
 }
}
function renderCloudCandidates(candidates) {
 const container=el("cloud-candidates");
 container.replaceChildren();
 if(!candidates.length) {
  appendText(container,"p","هنوز نامزدی ثبت نشده است. اجرای جست‌وجوی جدید را انتخاب کنید.");
  return;
 }
 candidates.forEach(candidate=>{
  const card=document.createElement("article");
  card.className="cloud-candidate";
  appendText(card,"h3",candidate.title||candidate.provider_domain);
  const linkLine=document.createElement("p");
  const link=document.createElement("a");
  try {
   const source=new URL(candidate.source_url);
   if(source.protocol!=="https:") throw new Error("HTTPS required");
   link.href=source.href; link.target="_blank"; link.rel="noopener noreferrer";
   link.textContent=candidate.source_url;
   linkLine.append(link);
  } catch(_) {
   linkLine.textContent="نشانی منبع نامعتبر است و پیوند باز نمی‌شود.";
  }
  card.append(linkLine);
  appendText(card,"p","دامنه: "+candidate.provider_domain+" | بازبینی: "+candidate.review_status+" | ظرفیت: "+candidate.availability_status+" | تازگی: "+candidate.freshness_status,"cloud-status");
  const authorityLabel=candidate.source_authority==="recognized_official_domain"
   ? "دامنهٔ رسمی شناخته‌شده"
   : "دامنهٔ رسمی در فهرست شناخته‌شده نیست";
  appendText(card,"p","شناخت دامنه: "+authorityLabel,"cloud-status");
  const sourceClassificationLabels={
   recognized_provider_domain:"صفحه روی دامنهٔ رسمی شناخته‌شده (تشخیص دامنه‌ای؛ نه تأیید محتوا)",
   likely_intermediary_article:"مقاله/مقایسهٔ واسطهٔ احتمالی — ادعاها را در سایت ارائه‌دهنده بررسی کنید",
   unclassified_unverified_domain:"نوع منبع نامشخص؛ دامنهٔ رسمی تأیید نشده"
  };
  appendText(card,"p","نوع منبع: "+(sourceClassificationLabels[candidate.source_classification]||sourceClassificationLabels.unclassified_unverified_domain),"cloud-status");
  const assessment=candidate.eligibility_assessment||{status:"needs_review",execution_authorized:false,blockers:["پیش‌ارزیابی در دسترس نیست."],review_needed:[]};
  const assessmentLabels={blocked:"مسدود",needs_review:"نیازمند بازبینی",ready_for_provider_adapter:"آماده برای adapter ارائه‌دهنده"};
  appendText(card,"p","پیش‌ارزیابی: "+(assessmentLabels[assessment.status]||assessment.status)+" | اجرای راه دور مجاز: "+(assessment.execution_authorized?"بله":"خیر"),"cloud-status");
  const details=document.createElement("details");
  const summary=document.createElement("summary");
  summary.textContent="جزئیات شواهد و ادعاها ("+(candidate.observed_claims||[]).length+")";
  details.append(summary);
  (assessment.blockers||[]).forEach(reason=>appendText(details,"p","مانع: "+reason,"muted"));
  (assessment.review_needed||[]).forEach(reason=>appendText(details,"p","نیاز بعدی: "+reason,"muted"));
  appendText(details,"p","سیگنال‌های متنی: "+((candidate.resource_signals||[]).join("، ")||"موردی شناسایی نشد"));
  appendText(details,"p","آخرین مشاهده: "+candidate.last_seen_at);
  appendText(details,"p",candidate.evidence_excerpt||"خلاصه شواهد موجود نیست.","cloud-evidence");
  appendText(details,"h4","ادعاهای استخراج‌شده از متن (هنوز تأییدنشده)");
  const claims=candidate.observed_claims||[];
  if(!claims.length) appendText(details,"p","ادعای ساختاریافته‌ای با الگوهای فعلی پیدا نشد.","muted");
  else {
   const claimList=document.createElement("ul");
   claims.forEach(claim=>{
    const item=document.createElement("li");
    appendText(item,"span",claim.claim_type+": "+JSON.stringify(claim.value)+(claim.unit?" "+claim.unit:"")+" — «"+claim.evidence_quote+"» ["+claim.review_status+"] ");
    const controls=document.createElement("span"); controls.className="cloud-actions";
    const accept=document.createElement("button"); accept.type="button"; accept.textContent="تأیید ادعا";
    accept.disabled=candidate.freshness_status==="stale"||candidate.review_status!=="approved";
    const reject=document.createElement("button"); reject.type="button"; reject.textContent="رد ادعا";
    reject.disabled=candidate.freshness_status==="stale";
    const claimFeedback=document.createElement("span"); claimFeedback.className="muted";
    const sendClaimReview=async decision=>{
     claimFeedback.textContent="در حال ذخیره…"; accept.disabled=true; reject.disabled=true;
     try {
      await api("/api/cloud-resources/"+candidate.id+"/claims/"+claim.id+"/review",{
       method:"PATCH",
       body:JSON.stringify({
        expected_source_content_sha256:claim.source_content_sha256,
        review_status:decision
       })
      });
      claimFeedback.textContent="ذخیره شد.";
      await refreshCloudResources();
     } catch(error) {
      claimFeedback.textContent="ذخیره نشد: "+error.message;
      accept.disabled=candidate.freshness_status==="stale"||candidate.review_status!=="approved";
      reject.disabled=candidate.freshness_status==="stale";
     }
    };
    accept.addEventListener("click",()=>sendClaimReview("accepted"));
    reject.addEventListener("click",()=>sendClaimReview("rejected"));
    controls.append(accept,reject,claimFeedback); item.append(controls); claimList.append(item);
   });
   details.append(claimList);
  }
  if(candidate.freshness_status==="stale") {
   appendText(details,"p","شواهد قدیمی است؛ پیش از بازبینی باید منبع دوباره کشف شود.","muted");
  }
  const form=document.createElement("form");
  const grid=document.createElement("div"); grid.className="cloud-form-grid";
  const reviewLabel=document.createElement("label"); reviewLabel.textContent="تصمیم بازبینی";
  const review=document.createElement("select"); review.required=true;
  [["","انتخاب تصمیم"],["approved","تأیید شواهد"],["rejected","رد منبع"]].forEach(([value,label])=>{
   const option=document.createElement("option"); option.value=value; option.textContent=label; review.append(option);
  });
  if(["approved","rejected"].includes(candidate.review_status)) review.value=candidate.review_status;
  review.addEventListener("change",()=>{if(review.value==="rejected"&&availability.value==="available")availability.value="unknown";});
  reviewLabel.append(review); grid.append(reviewLabel);
  const availabilityLabel=document.createElement("label"); availabilityLabel.textContent="وضعیت ظرفیت";
  const availability=document.createElement("select");
  [["unknown","نامشخص"],["available","در دسترس (بررسی‌شده)"],["unavailable","در دسترس نیست"],["requires_auth","نیازمند احراز هویت"]].forEach(([value,label])=>{
   const option=document.createElement("option"); option.value=value; option.textContent=label; availability.append(option);
  });
  availability.value=candidate.availability_status||"unknown";
  availabilityLabel.append(availability); grid.append(availabilityLabel);
  const termsLabel=document.createElement("label"); termsLabel.textContent="نشانی HTTPS شرایط استفاده (برای تأیید الزامی)";
  const terms=document.createElement("input"); terms.type="url"; terms.placeholder="https://…"; terms.setAttribute("aria-label", "نشانی HTTPS شرایط استفاده"); terms.value=candidate.terms_url||"";
  termsLabel.append(terms); grid.append(termsLabel);
  const quotaLabel=document.createElement("label"); quotaLabel.textContent="سهمیه‌های تأییدشده به‌شکل JSON";
  const quota=document.createElement("textarea"); quota.rows=3; quota.value=JSON.stringify(candidate.quota||{},null,2);
  quotaLabel.append(quota); grid.append(quotaLabel);
  form.append(grid);
  const actions=document.createElement("div"); actions.className="cloud-actions";
  const submit=document.createElement("button"); submit.type="submit"; submit.textContent="ذخیره بازبینی";
  submit.disabled=candidate.freshness_status==="stale";
  actions.append(submit); form.append(actions);
  const feedback=document.createElement("p"); feedback.className="muted"; form.append(feedback);
  form.addEventListener("submit",async event=>{
   event.preventDefault(); feedback.textContent="در حال ذخیره…"; submit.disabled=true;
   try {
    let quotaValue;
    try { quotaValue=JSON.parse(quota.value||"{}"); } catch(_) { throw new Error("JSON سهمیه معتبر نیست."); }
    if(!quotaValue||Array.isArray(quotaValue)||typeof quotaValue!=="object") throw new Error("سهمیه باید یک شیء JSON باشد.");
    const result=await api("/api/cloud-resources/"+candidate.id+"/review",{
     method:"PATCH",
     body:JSON.stringify({
      expected_content_sha256:candidate.content_sha256,
      review_status:review.value,
      availability_status:availability.value,
      quota:quotaValue,
      terms_url:terms.value.trim()||null
     })
    });
    feedback.textContent="بازبینی ذخیره شد: "+result.candidate.review_status;
    await refreshCloudResources();
   } catch(error) {
    feedback.textContent="ذخیره نشد: "+error.message;
    submit.disabled=candidate.freshness_status==="stale";
   }
  });
  details.append(form);
  const deleteButton=document.createElement("button");
  deleteButton.type="button";
  deleteButton.className="danger";
  deleteButton.textContent="حذف منبع";
  deleteButton.setAttribute("aria-label","حذف منبع "+(candidate.title||candidate.id));
  deleteButton.addEventListener("click",async()=>{
   if(!window.confirm("منبع «"+(candidate.title||candidate.source_url)+"» حذف شود؟ اگر در تاریخچهٔ برنامه‌های انتخاب ثبت شده باشد، حذف توسط سرور رد می‌شود.")) return;
   deleteButton.disabled=true;
   try {
    await api("/api/cloud-resources/"+encodeURIComponent(candidate.id),{method:"DELETE"});
    await refreshCloudResources();
   } catch(error) {
    const feedback=appendText(card,"p","حذف منبع انجام نشد: "+error.message,"muted");
    feedback.setAttribute("role","status");
    deleteButton.disabled=false;
   }
  });
  const deleteActions=document.createElement("div"); deleteActions.className="cloud-actions";
  deleteActions.append(deleteButton);
  card.append(details,deleteActions); container.append(card);
 });
}
el("cloud-manual-form").addEventListener("submit",async event=>{
 event.preventDefault();
 const button=el("cloud-manual-submit");
 const feedback=el("cloud-manual-feedback");
 button.disabled=true;
 feedback.textContent="در حال ثبت منبع…";
 try {
  const result=await api("/api/cloud-resources/manual",{
   method:"POST",
   body:JSON.stringify({
    source_url:el("cloud-manual-url").value.trim(),
    title:el("cloud-manual-title").value.trim(),
    description:el("cloud-manual-description").value.trim()
   })
  });
  feedback.textContent="منبع #"+result.candidate.id+" ثبت شد و تا بازبینی شما تأییدنشده باقی می‌ماند.";
  el("cloud-manual-form").reset();
  await refreshCloudResources();
 } catch(error) {
  feedback.textContent="ثبت منبع ناموفق بود: "+error.message;
 } finally {
  button.disabled=false;
 }
});
el("cloud-refresh").addEventListener("click",refreshCloudResources);
el("cloud-candidate-limit").addEventListener("change",refreshCloudResources);
el("cloud-discover").addEventListener("click",async()=>{
 const output=el("cloud-output");
 const button=el("cloud-discover");
 button.disabled=true;
 output.textContent="در حال صف‌بندی جست‌وجوی جدید…";
 try {
  const response=await api("/api/cloud-resources/discover",{method:"POST"});
  const task=response.task;
  let pollFailures=0;
  output.textContent="وظیفهٔ جست‌وجو در صف قرار گرفت: #"+task.id+" ("+task.status+").";
  const poll=async()=>{
   try {
    const current=await api("/api/tasks/"+task.id);
    pollFailures=0;
    const status=current.status||"unknown";
    const checkpoint=current.checkpoint&&typeof current.checkpoint==="object"
     ? current.checkpoint : {};
    let progress="";
    if(checkpoint.phase==="cloud_discovery_query"&&Number.isInteger(checkpoint.query_index)){
     const queryNumber=Math.max(1,Math.min(5,checkpoint.query_index+1));
     progress=" — پرس‌وجوی "+queryNumber+" از 5";
    } else if(status==="running") {
     progress=" — در حال آماده‌سازی یا پردازش نتیجه‌ها";
    }
    const updated=current.updated_at?"\nآخرین تغییر ثبت‌شده: "+current.updated_at:"";
    const savedCandidatesNote=["queued","running","pause_requested","cancel_requested"].includes(status)
     ?"\nنامزدهای نمایش‌داده‌شده تا پایان کار، آخرین داده‌های ذخیره‌شده هستند.":"";
    output.textContent="وظیفهٔ جست‌وجو: #"+current.id+" — "+status+progress+updated+savedCandidatesNote;
    if(["queued","running","pause_requested","cancel_requested"].includes(status)){
     setTimeout(poll,2000);
    } else {
     button.disabled=false;
     await refreshCloudResources();
     const resultSummary=current.result&&typeof current.result==="object"
      ? "\nنتیجهٔ وظیفه: "+(current.result.status||status)
        +(Number.isInteger(current.result.sources_seen)?" | منابع دیده‌شده: "+current.result.sources_seen:"")
        +(Number.isInteger(current.result.candidates_upserted)?" | نامزدهای ذخیره‌شده: "+current.result.candidates_upserted:"")
      : "";
     const errorSummary=current.error?"\nخطا: "+current.error:"";
     output.textContent+="\n\nپایان وظیفهٔ #"+current.id+" — "+status+resultSummary+errorSummary;
    }
   } catch(error) {
    pollFailures+=1;
    output.textContent="وضعیت وظیفهٔ #"+task.id+" فعلاً دریافت نشد: "+error.message
     +(pollFailures<5?"\nتلاش مجدد خودکار تا زمان بازیابی ارتباط انجام می‌شود.":"\nتلاش خودکار متوقف شد؛ برای بررسی دوباره، «بارگذاری منابع» را بزنید.");
    if(pollFailures<5) setTimeout(poll,5000);
    else button.disabled=false;
   }
  };
  setTimeout(poll,1000);
 } catch(error) {
  button.disabled=false;
  output.textContent="جست‌وجو صف‌بندی نشد: "+error.message;
 }
});

el("cloud-plan-run").addEventListener("click",async()=>{
 const output=el("cloud-plan-output");
 const cpuRaw=el("plan-min-cpu").value;
 const memoryRaw=el("plan-min-memory").value;
 const cpu=Number(cpuRaw);
 const memory=Number(memoryRaw);
 if(!cpuRaw.trim()||!Number.isInteger(cpu)||cpu<0||cpu>256) {
  output.textContent="حداقل CPU باید عدد صحیحی بین ۰ و ۲۵۶ باشد.";
  return;
 }
 if(!memoryRaw.trim()||!Number.isFinite(memory)||memory<0||memory>1048576) {
  output.textContent="حداقل حافظه باید عددی معتبر بین ۰ و ۱۰۴۸۵۷۶ GiB باشد.";
  return;
 }
 const splitList=value=>value.split(",").map(item=>item.trim()).filter(Boolean);
 const request={
  min_cpu_count:cpu,
  min_memory_gib:memory,
  require_gpu:el("plan-require-gpu").checked,
  gpu_models:splitList(el("plan-gpu-models").value),
  allowed_provider_domains:splitList(el("plan-provider-domains").value)
 };
 const button=el("cloud-plan-run");
 button.disabled=true;
 output.textContent="در حال ارزیابی شواهد فعلی…";
 try {
  const plan=await api("/api/cloud-resources/plan",{
   method:"POST",
   body:JSON.stringify(request)
  });
  output.textContent=JSON.stringify(plan,null,2);
 } catch(error) {
  output.textContent="برنامه ساخته نشد: "+error.message;
 } finally {
  button.disabled=false;
 }
});


 refreshCloudResources();
})();
