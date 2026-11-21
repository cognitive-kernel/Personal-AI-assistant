(() => {
 "use strict";
 const core = window.learningCore;
 const fallback = document.getElementById("paths-output");
 if (!core || core.version !== 1) {
  if (fallback) fallback.textContent = "ماژول سوابق یادگیری به قرارداد learningCore v1 نیاز دارد.";
  return;
 }
 const {
  api: learningApi, formatTaskAuditEventChange,
  learningStatusLabels, taskStatusLabels, appendHistoryText
 } = core;

function renderTopicJobHistory(output, topic, jobs, tasksByJobId) {
  const history = document.createElement("details");
  history.className = "path-topic-job-history";
  const summary = document.createElement("summary");
  summary.textContent = "سوابق یادگیری و گزارش‌های ذخیره‌شده (" + jobs.length + ")";
  history.append(summary);
  if (!jobs.length) {
   const empty = document.createElement("p");
   empty.textContent = "هنوز وظیفه‌ای برای این درس ثبت نشده است.";
   history.append(empty);
   output.append(history);
   return;
  }
  jobs.forEach(job => {
   const record = document.createElement("details");
   record.className = "path-topic-job-record";
   const heading = document.createElement("summary");
   const domainStatus = learningStatusLabels[job.status] || job.status || "نامشخص";
   const task = tasksByJobId.get(Number(job.id));
   const queueStatus = task ? (taskStatusLabels[task.status] || task.status) : "وظیفه صف یافت نشد";
   heading.textContent = "درس #" + job.id + " — " + domainStatus + "؛ صف: " + queueStatus;
   record.append(heading);
   appendHistoryText(record, "موضوع", job.topic);
   appendHistoryText(record, "ایجاد", job.created_at);
   appendHistoryText(record, "آخرین تغییر", job.updated_at);
   appendHistoryText(record, "نوع منبع", job.source_kind);
   appendHistoryText(record, "خطای درس", job.error);
   if (job.quality) appendHistoryText(record, "امتیاز کیفیت", job.quality.score + "/100");
   if (job.lesson) appendHistoryText(record, "نتیجه درس", job.lesson);
   if (job.assessment) appendHistoryText(record, "آزمون", job.assessment.questions || job.assessment.learning_outcomes || []);
   if (job.evidence && job.evidence.length) {
    const evidence = document.createElement("ul");
    job.evidence.forEach(item => {
     const entry = document.createElement("li");
     entry.textContent = [item.evidence_type, item.source_ref, item.note].filter(Boolean).join(" — ");
     evidence.append(entry);
    });
    record.append(evidence);
   }
   if (task) {
    appendHistoryText(record, "شناسه وظیفه", task.id);
    appendHistoryText(record, "تلاش‌ها", (task.attempts || 0) + "/" + (task.max_attempts || "?"));
    appendHistoryText(record, "آخرین خطای صف", task.error);
    if (task.result) appendHistoryText(record, "نتیجه اجرای صف", task.result);
    const eventsButton = document.createElement("button");
    eventsButton.type = "button";
    eventsButton.textContent = "بارگذاری تاریخچه رویدادها";
    const eventsOutput = document.createElement("div");
    const eventsStatus = document.createElement("p");
    eventsStatus.setAttribute("role", "status");
    eventsStatus.setAttribute("aria-live", "polite");
    const eventsList = document.createElement("ol");
    eventsList.hidden = true;
    const olderEventsButton = document.createElement("button");
    olderEventsButton.type = "button";
    olderEventsButton.textContent = "بارگذاری رویدادهای قدیمی‌تر";
    olderEventsButton.hidden = true;
    let nextEventsBeforeId = null;
 
    function renderTaskAuditEvents(events, prepend) {
     const items = events.slice().reverse().map(event => {
      const item = document.createElement("li");
      const change = formatTaskAuditEventChange(event);
      item.textContent = (event.occurred_at || "") + " — " + event.event_type + " — " + change;
      return item;
     });
     if (prepend) eventsList.prepend(...items);
     else eventsList.append(...items);
     if (items.length) eventsList.hidden = false;
    }
 
    async function loadTaskAuditEvents(beforeId = null) {
     eventsButton.disabled = true;
     olderEventsButton.disabled = true;
     eventsStatus.textContent = "در حال بارگذاری رویدادهای ذخیره‌شده…";
     try {
      const cursor = beforeId === null ? "" : "&before_id=" + encodeURIComponent(beforeId);
      const response = await learningApi("/api/tasks/" + task.id + "/events?limit=100" + cursor);
      const events = response.events || [];
      if (beforeId === null) {
       eventsList.replaceChildren();
       if (!events.length) eventsStatus.textContent = "برای این وظیفه رویدادی ثبت نشده است.";
       else eventsStatus.textContent = "";
      } else if (!events.length) {
       eventsStatus.textContent = "رویداد قدیمی‌تری برای این وظیفه وجود ندارد.";
      } else {
       eventsStatus.textContent = "";
      }
      renderTaskAuditEvents(events, beforeId !== null);
      nextEventsBeforeId = response.has_more ? response.next_before_id : null;
      olderEventsButton.hidden = !nextEventsBeforeId;
     } catch (error) {
      eventsStatus.textContent = "بارگذاری تاریخچه ناموفق بود: " + error.message;
     } finally {
      eventsButton.disabled = false;
      olderEventsButton.disabled = false;
     }
    }
 
    eventsButton.addEventListener("click", () => loadTaskAuditEvents());
    olderEventsButton.addEventListener("click", () => {
     if (nextEventsBeforeId !== null) loadTaskAuditEvents(nextEventsBeforeId);
    });
    eventsOutput.append(eventsStatus, eventsList, olderEventsButton);
    record.append(eventsButton, eventsOutput);
   } else {
    appendHistoryText(record, "تاریخچه صف", "رکورد صف برای این درس در ۱۰۰ وظیفه اخیر پیدا نشد.");
   }
   outputForHistory(record, job);
   history.append(record);
  });
  output.append(history);
 }
 
 function outputForHistory(record, job) {
  if (job.status === "rejected" && !job.error) {
   appendHistoryText(record, "توضیح وضعیت", "وظیفه صف ممکن است موفق شده باشد، اما خود درس در کنترل کیفیت رد شده است. این دو وضعیت متفاوت‌اند.");
  }
 }

 window.learningHistory = Object.freeze({version: 1, renderTopicJobHistory});
})();
