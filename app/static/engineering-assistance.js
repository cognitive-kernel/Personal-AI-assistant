/* Engineering assistance UI.
 * Owns programming-task submission/polling and the self-improvement proposal lifecycle.
 * Shared dependency contract v1: dashboardCore.{api,el}.
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
 if(!core||core.version!==1||typeof core.api!=="function"||typeof core.el!=="function"){
  for(const id of ["programming-assist-status","self-improvement-output"]){
   const node=document.getElementById(id);
   if(node) node.textContent="ماژول دستیار مهندسی بارگذاری نشد: قرارداد هستهٔ داشبورد در دسترس نیست.";
  }
  throw new Error("Engineering assistance module requires dashboardCore contract v1.");
 }
 const {api,el}=core;
async function refreshSelfImprovement() {
 const output=el("self-improvement-output");
 const list=el("self-improvement-list");
 try {
  const result=await api("/api/self-improvement/proposals");
  list.replaceChildren();
  for (const item of result.proposals || []) {
   const card=document.createElement("article");
   card.style.border="1px solid #8886";
   card.style.borderRadius="8px";
   card.style.padding="10px";
   card.style.margin="8px 0";
   const heading=document.createElement("h3");
   heading.textContent="#" + item.id + " — " + item.title + " (" + item.status + ")";
   const rationale=document.createElement("p");
   rationale.textContent=item.rationale;
   const hash=document.createElement("p");
   hash.className="muted";
   hash.textContent="SHA-256: " + item.patch_sha256 + " | پایه: " + (item.base_commit_sha || "نامشخص");
   card.append(heading,rationale,hash);
   const eventButton=document.createElement("button");
   eventButton.type="button";
   eventButton.textContent="تاریخچه";
   eventButton.addEventListener("click",async()=>{
    try { const events=await api("/api/self-improvement/proposals/"+item.id+"/events"); output.textContent=JSON.stringify(events.events,null,2); }
    catch(error){output.textContent=error.message;}
   });
   card.append(eventButton);
   const diffButton=document.createElement("button");
   diffButton.type="button";
   diffButton.textContent="مشاهدهٔ diff";
   diffButton.addEventListener("click",async()=>{
    try { const detail=await api("/api/self-improvement/proposals/"+item.id); output.textContent=detail.proposal.patch; }
    catch(error){output.textContent=error.message;}
   });
   card.append(diffButton);
   if(item.status==="proposed") {
    const validate=document.createElement("button");
    validate.type="button";
    validate.textContent="بررسی سازگاری diff";
    validate.addEventListener("click",async()=>{
     validate.disabled=true;
     try {
      await api("/api/self-improvement/proposals/"+item.id+"/validate",{method:"POST"});
      output.textContent="بررسی سازگاری انجام شد؛ این مرحله آزمون کد اجرا نمی‌کند.";
      await refreshSelfImprovement();
     } catch(error) {
      const message="اعتبارسنجی ناموفق بود: "+error.message;
      output.textContent=message;
      let errorNode=card.querySelector(".self-improvement-validation-error");
      if(!errorNode) {
       errorNode=document.createElement("p");
       errorNode.className="self-improvement-validation-error";
       errorNode.setAttribute("role","alert");
       card.append(errorNode);
      }
      errorNode.textContent=message;
     } finally {
      validate.disabled=false;
     }
    });
    card.append(validate);
   }
   if(item.status==="validated" && (!item.static_check || item.static_check.status!=="passed")) {
    const syntaxCheck=document.createElement("button");
    syntaxCheck.type="button";
    syntaxCheck.textContent="بررسی ایزولهٔ نحوی و رفتاری";
    syntaxCheck.addEventListener("click",async()=>{
     if(!window.confirm("این بررسی، patch را فقط در worktree موقت اعمال و فایل‌های Python را داخل Docker با شبکهٔ خاموش تجزیهٔ نحوی می‌کند؛ آزمون پروژه یا اجرای کد پیشنهادی انجام نمی‌شود. رضایت Sandbox باید فعال باشد. ادامه؟")) return;
     syntaxCheck.disabled=true;
     try {
      const result=await api("/api/self-improvement/proposals/"+item.id+"/static-check",{method:"POST",body:JSON.stringify({confirm:true})});
      const check=result.proposal.static_check||{};
      output.textContent="نتیجهٔ بررسی ایزوله: "+check.status+"؛ بررسی نحوی: "+(check.syntax_check||"نامشخص")+"؛ یکپارچگی آزمون‌ها: "+((check.test_integrity||{}).status||"نامشخص")+"؛ آزمون رفتاری: "+((check.behavioral_tests||{}).status||"اجرا نشد")+"؛ اجرای کد پیشنهادی داخل Docker: "+(check.candidate_code_executed?"بله":"خیر")+"؛ فایل‌های نحوی: "+(check.files_checked||0);
      await refreshSelfImprovement();
     } catch(error){output.textContent=error.message;}
     finally{syntaxCheck.disabled=false;}
    });
    card.append(syntaxCheck);
   }
   if(item.status==="validated" && item.static_check && item.static_check.status==="passed") {
    const approve=document.createElement("button");
    approve.type="button";
    approve.textContent="تأیید برای بررسی PR";
    approve.addEventListener("click",async()=>{
     if(!window.confirm("این تأیید کد را اعمال یا مستقر نمی‌کند. فقط اجازهٔ انتقال به بررسی انسانی PR را ثبت می‌کند. ادامه؟")) return;
     approve.disabled=true;
     try { await api("/api/self-improvement/proposals/"+item.id+"/approve",{method:"POST",body:JSON.stringify({confirm:true})}); output.textContent="تأیید ثبت شد؛ هیچ فایلی تغییر نکرد."; await refreshSelfImprovement(); }
     catch(error){output.textContent=error.message;}
     finally{approve.disabled=false;}
    });
    card.append(approve);
   }
   if(item.status==="approved_for_pr") {
    const openPr=document.createElement("button");
    openPr.type="button";
    openPr.textContent="ایجاد PR پیش‌نویس در GitHub";
    openPr.addEventListener("click",async()=>{
     if(!window.confirm("این اقدام patch تأییدشده را به‌صورت branch و PR پیش‌نویس در مخزن GitHub پیکربندی‌شده منتشر می‌کند. PR ادغام یا مستقر نمی‌شود و checkout محلی تغییر نمی‌کند. ادامه؟")) return;
     openPr.disabled=true;
     try {
      const result=await api("/api/self-improvement/proposals/"+item.id+"/open-pr",{method:"POST",body:JSON.stringify({confirm:true})});
      const pr=result.pull_request;
      output.textContent="PR پیش‌نویس ایجاد شد: "+pr.pull_request_url+"؛ branch: "+pr.branch+"؛ ادغام یا استقرار انجام نشده است.";
      await refreshSelfImprovement();
     } catch(error){output.textContent="ایجاد PR انجام نشد: "+error.message;}
     finally{openPr.disabled=false;}
    });
    card.append(openPr);
    const rollbackPr=document.createElement("button");
    rollbackPr.type="button";
    rollbackPr.textContent="ایجاد PR پیش‌نویس بازگشت";
    rollbackPr.addEventListener("click",async()=>{
     if(!window.confirm("فقط اگر PR اصلی ادغام شده باشد، یک PR پیش‌نویس بازگشت ایجاد می‌شود. اگر فایل‌های مربوطه پس از ادغام تغییر کرده باشند، عملیات متوقف می‌شود. هیچ ادغام یا استقراری انجام نمی‌شود. ادامه؟")) return;
     rollbackPr.disabled=true;
     try {
      const result=await api("/api/self-improvement/proposals/"+item.id+"/rollback-pr",{method:"POST",body:JSON.stringify({confirm:true})});
      const rollback=result.rollback_pull_request;
      output.textContent="PR پیش‌نویس بازگشت: "+rollback.pull_request_url+"؛ ادغام یا استقرار انجام نشده است.";
      await refreshSelfImprovement();
     } catch(error){output.textContent="ایجاد PR بازگشت انجام نشد: "+error.message;}
     finally{rollbackPr.disabled=false;}
    });
    card.append(rollbackPr);
   }
   if(["proposed","validated"].includes(item.status)) {
    const reject=document.createElement("button");
    reject.type="button";
    reject.textContent="رد پیشنهاد";
    reject.addEventListener("click",async()=>{
     if(!window.confirm("این پیشنهاد رد شود؟")) return;
     try { await api("/api/self-improvement/proposals/"+item.id+"/reject",{method:"POST",body:JSON.stringify({confirm:true,reason:"رد از رابط محلی"})}); await refreshSelfImprovement(); }
     catch(error){output.textContent=error.message;}
    });
    card.append(reject);
   }
   list.append(card);
  }
  if(!(result.proposals||[]).length) list.textContent="هنوز پیشنهادی ثبت نشده است.";
 } catch(error) {
  output.textContent="مدیریت پیشنهادها در دسترس نیست: "+error.message;
 }
}
el("programming-assist-form").addEventListener("submit",async event=>{
 event.preventDefault();
 const button=el("programming-assist-submit");
 const status=el("programming-assist-status");
 const output=el("programming-assist-output");
 button.disabled=true;
 output.textContent="";
 status.textContent="درخواست برنامه‌نویسی در حال صف‌بندی است…";
 try {
  const queued=await api("/api/programming/tasks",{method:"POST",body:JSON.stringify({
   instruction:el("programming-instruction").value,
   context:el("programming-context").value
  })});
  const taskId=queued.task.id;
  status.textContent="وظیفهٔ برنامه‌نویسی #"+taskId+" در صف مستقل برنامه‌نویسی قرار گرفت.";
  const poll=async()=>{
   try {
    const task=await api("/api/tasks/"+taskId);
    if(["queued","running","pause_requested","paused","cancel_requested"].includes(task.status)) {
     status.textContent="وضعیت وظیفهٔ برنامه‌نویسی #"+taskId+": "+task.status;
     setTimeout(poll,1000);
     return;
    }
    if(task.status==="succeeded") {
     output.textContent=(task.result&&task.result.reply)||"مدل پاسخ متنی برنگرداند.";
     status.textContent="پاسخ دریافت شد. کد اجرا نشده و هیچ آزمونی اجرا نشده است.";
    } else {
     status.textContent="وظیفهٔ برنامه‌نویسی تکمیل نشد: "+(task.error||task.status);
    }
    button.disabled=false;
   } catch(error) {
    status.textContent="وضعیت وظیفه قابل دریافت نیست: "+error.message;
    button.disabled=false;
   }
  };
  setTimeout(poll,500);
 } catch(error) {
  status.textContent="ارسال درخواست ناموفق بود: "+error.message;
  button.disabled=false;
 }
});
el("self-improvement-form").addEventListener("submit",async event=>{
 event.preventDefault();
 const output=el("self-improvement-output");
 output.textContent="در حال ثبت پیشنهاد…";
 try {
  const result=await api("/api/self-improvement/proposals",{method:"POST",body:JSON.stringify({
   title:el("self-improvement-title").value,
   rationale:el("self-improvement-rationale").value,
   patch:el("self-improvement-patch").value
  })});
  output.textContent="پیشنهاد #"+result.proposal.id+" ثبت شد. هنوز هیچ تغییری روی فایل‌های پروژه اعمال نشده است.";
  el("self-improvement-form").reset();
  await refreshSelfImprovement();
 } catch(error) { output.textContent=error.message; }
});
el("self-improvement-refresh").addEventListener("click",refreshSelfImprovement);
})();
