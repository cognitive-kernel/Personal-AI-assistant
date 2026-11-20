(() => {
 "use strict";
 const dashboard=window.dashboardCore;
 const fallback=document.getElementById("paths-output")||document.getElementById("due-reviews-output");
 if(!dashboard||dashboard.version!==1){
  if(fallback)fallback.textContent="هستهٔ یادگیری به dashboardCore v1 نیاز دارد.";
  return;
 }
 const learningEl=dashboard.el;
 const learningEl = id => document.getElementById(id);
 
 async function learningApi(path, options = {}) {
  const response = await fetch(path, {headers: {"Content-Type":"application/json"}, ...options});
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
   const error = new Error(data.detail || ("HTTP " + response.status));
   error.status = response.status;
   throw error;
  }
  return data;
 }
 
 const learningDelay = ms => new Promise(resolve => setTimeout(resolve, ms));
 
 function formatTaskAuditEventChange(event) {
  switch (event.event_type) {
   case "status_changed":
    return (event.old_status || "—") + " ← " + (event.new_status || "—");
   case "priority_changed":
    return "اولویت: " + (event.old_priority ?? "—") + " ← " + (event.new_priority ?? "—");
   case "scheduled":
    return "زمان‌بندی: " + (event.old_scheduled_at || "—") + " ← " + (event.new_scheduled_at || "—");
   case "created":
    return "ایجاد وظیفه: " + (event.new_status || "—") + "، اولویت: " + (event.new_priority ?? "—");
   default:
    return event.new_status || event.event_type || "رویداد";
  }
 }
 
 
 async function waitForLearningJob(jobId, output) {
  for (let attempt = 0; attempt < 600; attempt += 1) {
   const job = await learningApi("/api/learning/jobs/" + jobId);
   if (["completed", "rejected", "failed", "cancelled"].includes(job.status)) return job;
   output.textContent = "درس در حال تولید است… وضعیت: " + job.status + " (شناسه کار: " + jobId + ")";
   await learningDelay(1500);
  }
  throw new Error("پس از ۱۵ دقیقه انتظار، هنوز نتیجه نهایی دریافت نشد؛ کار ممکن است در پس‌زمینه ادامه داشته باشد. شناسه کار: " + jobId);
 }
 
 const learningStatusLabels = {
  queued: "در صف", running: "در حال اجرا", completed: "درس تولید شد",
  rejected: "ردشده در کنترل کیفیت", failed: "ناموفق", cancelled: "لغوشده"
 };
 const taskStatusLabels = {
  queued: "در صف", running: "در حال اجرا", pause_requested: "درخواست توقف موقت",
  paused: "متوقف موقت", cancel_requested: "درخواست لغو", succeeded: "اجرای وظیفه تمام شد",
  failed: "اجرای وظیفه ناموفق", cancelled: "وظیفه لغو شد"
 };
 
 function appendHistoryText(container, label, value) {
  if (value === null || value === undefined || value === "") return;
  const line = document.createElement("p");
  line.textContent = label + ": " + (typeof value === "string" ? value : JSON.stringify(value, null, 2));
  container.append(line);
 }
 
 
 window.learningCore=Object.freeze({
  version:1,el:learningEl,api:learningApi,delay:learningDelay,
  formatTaskAuditEventChange,learningStatusLabels:Object.freeze(learningStatusLabels),
  taskStatusLabels:Object.freeze(taskStatusLabels),appendHistoryText,waitForLearningJob
 });
})();