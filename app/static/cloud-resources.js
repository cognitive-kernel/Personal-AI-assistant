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
 const cloudCardsFactory=window.cloudResourceCardsFactory;
 const cloudResourceCards=cloudCardsFactory&&cloudCardsFactory.version===1
  ? cloudCardsFactory.createCloudResourceCards({el,api,appendText,refreshCloudResources:()=>refreshCloudResources()})
  : null;
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
 if(cloudResourceCards) {
  cloudResourceCards.renderCloudCandidates(candidates);
  return;
 }
 el("cloud-candidates").replaceChildren();
 appendText(el("cloud-candidates"),"p","نمایش کارت‌های منابع در دسترس نیست؛ جست‌وجو و برنامه‌ریزی همچنان فعال هستند.");
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
