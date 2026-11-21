(() => {
 "use strict";
 const core=window.learningCore && window.learningCore.version===1 ? window.learningCore : (() => {
  const el=id=>document.getElementById(id);
  async function api(path,options={}) {
   const response=await fetch(path,{headers:{"Content-Type":"application/json",...(options.headers||{})},...options});
   const payload=await response.json().catch(()=>({}));
   if(!response.ok)throw new Error(payload.detail||("HTTP "+response.status));
   return payload;
  }
  function appendHistoryText(container,label,value) {
   if(value===null||value===undefined||value==="")return;
   const line=document.createElement("p");
   line.textContent=label+": "+(typeof value==="string"?value:JSON.stringify(value,null,2));
   container.append(line);
  }
  function formatTaskAuditEventChange(event) {
   switch(event.event_type) {
    case "status_changed": return (event.old_status||"—")+" ← "+(event.new_status||"—");
    case "priority_changed": return "اولویت: "+(event.old_priority??"—")+" ← "+(event.new_priority??"—");
    case "scheduled": return "زمان‌بندی: "+(event.old_scheduled_at||"—")+" ← "+(event.new_scheduled_at||"—");
    case "created": return "ایجاد وظیفه: "+(event.new_status||"—")+"، اولویت: "+(event.new_priority??"—");
    default: return event.new_status||event.event_type||"رویداد";
   }
  }
  return Object.freeze({
   version:1,el,api,formatTaskAuditEventChange,appendHistoryText,
   learningStatusLabels:{queued:"در صف",running:"در حال اجرا",completed:"درس تولید شد",rejected:"ردشده در کنترل کیفیت",failed:"ناموفق",cancelled:"لغوشده"},
   taskStatusLabels:{queued:"در صف",running:"در حال اجرا",pause_requested:"درخواست توقف موقت",paused:"متوقف موقت",cancel_requested:"درخواست لغو",succeeded:"اجرای وظیفه تمام شد",failed:"اجرای وظیفه ناموفق",cancelled:"وظیفه لغو شد"}
  });
 })();
 const lessons=window.learningLessons && window.learningLessons.version===1 ? window.learningLessons : null;
 const overview=window.learningOverview && window.learningOverview.version===1 ? window.learningOverview : null;
 const fallback=document.getElementById("paths-output");
 if(!lessons && fallback)fallback.textContent="ماژول تولید درس در دسترس نیست؛ سایر بخش‌های مسیر یادگیری همچنان فعال هستند.";
 const {
  el:learningEl,api:learningApi,formatTaskAuditEventChange,
  learningStatusLabels,taskStatusLabels,appendHistoryText
 }=core;
 const startLesson=lessons?.startLesson|| (async()=>{throw new Error("ماژول تولید درس در دسترس نیست.")});
 const stopLesson=lessons?.stopLesson|| (async()=>{throw new Error("ماژول تولید درس در دسترس نیست.")});
 const refreshDueLearningReviews=overview?.refreshDueReviews|| (async()=>{});
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
 
 
 let learningPathsLoadSequence = 0;
 async function loadLearningPaths() {
  const requestSequence = ++learningPathsLoadSequence;
  try {
   const data = await learningApi("/api/learning/paths");
   if (requestSequence !== learningPathsLoadSequence) return;
   const [jobData, taskData] = await Promise.all([
    learningApi("/api/learning/jobs?limit=200").catch(() => ({jobs: []})),
    learningApi("/api/tasks?limit=100").catch(() => ({tasks: []}))
   ]);
   if (requestSequence !== learningPathsLoadSequence) return;
   const jobsByTopicId = new Map();
   (jobData.jobs || []).forEach(job => {
    if (job.path_topic_id == null) return;
    const topicId = Number(job.path_topic_id);
    if (!jobsByTopicId.has(topicId)) jobsByTopicId.set(topicId, []);
    jobsByTopicId.get(topicId).push(job);
   });
   jobsByTopicId.forEach(items => items.sort((a, b) => Number(b.id) - Number(a.id)));
   const tasksByJobId = new Map();
   (taskData.tasks || []).forEach(task => {
    if (task.task_type === "learning.run_job" && task.payload && task.payload.job_id != null) {
     tasksByJobId.set(Number(task.payload.job_id), task);
    }
   });
   const container = learningEl("paths-container");
   container.replaceChildren();
   learningEl("paths-output").textContent = "تعداد مسیرهای ثبت‌شده: " + data.paths.length;
   data.paths.forEach(path => {
    const pathCard = document.createElement("div");
    pathCard.className = "path-card";
    const details = document.createElement("details");
    const summary = document.createElement("summary");
    const topicCount = Math.max(0, Number(path.topic_count) || 0);
    const completedCount = Math.min(topicCount, Math.max(0, Number(path.completed_count) || 0));
    const progressPercent = topicCount ? Math.round((completedCount / topicCount) * 100) : 0;
    const progressNumber = new Intl.NumberFormat("fa-IR");
    summary.textContent = path.title + " — " + progressPercent + "٪ (" + completedCount + "/" + topicCount + ")";
    details.append(summary);
 
    const progressGroup = document.createElement("div");
    progressGroup.className = "path-progress";
    progressGroup.setAttribute("role", "group");
    progressGroup.setAttribute("aria-label", "پیشرفت مسیر " + path.title);
    const progressMeta = document.createElement("div");
    progressMeta.className = "path-progress-meta";
    const progressLabel = document.createElement("span");
    progressLabel.textContent = "پیشرفت مسیر";
    const progressValue = document.createElement("strong");
    progressValue.textContent = progressNumber.format(progressPercent) + "٪";
    const progress = document.createElement("progress");
    progress.className = "path-progress-bar";
    progress.max = 100;
    progress.value = progressPercent;
    progress.setAttribute("aria-label", "پیشرفت مسیر " + path.title);
    progress.setAttribute("aria-valuetext", progressNumber.format(progressPercent) + " درصد تکمیل‌شده");
    const progressCaption = document.createElement("p");
    progressCaption.className = "path-progress-caption";
    progressCaption.textContent = progressNumber.format(completedCount) + " از " +
     progressNumber.format(topicCount) + " سرفصل تکمیل شده است.";
    progressMeta.append(progressLabel, progressValue);
    progressGroup.append(progressMeta, progress, progressCaption);
 
    if (Array.isArray(path.reference_urls) && path.reference_urls.length) {
     const references = document.createElement("div");
     references.className = "path-reference-links";
     const referenceHeading = document.createElement("p");
     referenceHeading.textContent = "منابع پیشنهادی (پیوندها مرجع‌اند؛ متن آن‌ها خودکار دریافت یا راستی‌آزمایی نمی‌شود):";
     references.append(referenceHeading);
     path.reference_urls.forEach(reference => {
      const link = document.createElement("a");
      link.href = reference.url;
      link.textContent = reference.title;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.style.display = "inline-block";
      link.style.marginInlineEnd = "12px";
      references.append(link);
      if (reference.description) {
       const description = document.createElement("p");
       description.textContent = reference.description;
       references.append(description);
      }
     });
     details.append(references);
    }
 
    if (!path.research) {
     const syncControls = document.createElement("div");
     syncControls.className = "path-sync-controls";
     const syncButton = document.createElement("button");
     syncButton.type = "button";
     syncButton.textContent = "همگام‌سازی سرفصل‌ها";
     const syncOutput = document.createElement("p");
     syncOutput.setAttribute("role", "status");
     syncButton.addEventListener("click", async () => {
      syncButton.disabled = true;
      syncOutput.textContent = "در حال بررسی مسیر و حفظ سوابق موجود…";
      try {
       const result = await learningApi("/api/learning/paths/" + path.id + "/sync", {method: "POST"});
       const summary = result.sync || {};
       await loadLearningPaths();
       learningEl("paths-output").textContent =
        "همگام‌سازی انجام شد. سرفصل جدید: " + (summary.added_topic_count || 0) +
        "؛ سرفصل قدیمیِ بدون سابقه حذف‌شده: " + (summary.removed_legacy_count || 0) +
        "؛ سرفصل قدیمی دارای سابقه و حفظ‌شده: " + (summary.retained_legacy_count || 0) +
        ". وضعیت‌ها، امتیازها و کارهای مرتبط حفظ شدند.";
      } catch (error) {
       syncOutput.textContent = "خطا در همگام‌سازی مسیر: " + error.message;
      } finally {
       syncButton.disabled = false;
      }
     });
     syncControls.append(syncButton, syncOutput);
     details.append(syncControls);
    }
 
    const proposalControls = document.createElement("section");
    proposalControls.className = "curriculum-proposal-controls";
    const generateProposalsButton = document.createElement("button");
    generateProposalsButton.type = "button";
    generateProposalsButton.textContent = "تولید پیشنهادهای تطبیق مسیر";
    const proposalOutput = document.createElement("div");
    proposalOutput.className = "curriculum-proposal-output";
    proposalOutput.setAttribute("aria-live", "polite");
 
    function renderCurriculumProposals(result) {
     proposalOutput.replaceChildren();
     const proposals = result.proposals || [];
     if (!proposals.length) {
      proposalOutput.textContent = "پیشنهاد تازه‌ای وجود ندارد؛ امتیازها و تغییر منابع را دوباره بررسی کنید.";
      return;
     }
     proposals.forEach(proposal => {
      const card = document.createElement("article");
      card.className = "curriculum-proposal";
      const heading = document.createElement("h4");
      heading.textContent = (proposal.proposal_type === "reinforce_topic" ? "تقویت سرفصل" : "بازبینی منبع درس") +
       " — " + proposal.proposed_topic + " (" + proposal.status + ")";
      const rationale = document.createElement("p");
      rationale.textContent = proposal.rationale;
      const evidence = document.createElement("pre");
      evidence.textContent = JSON.stringify(proposal.evidence || {}, null, 2);
      card.append(heading, rationale, evidence);
      if (proposal.status === "pending") {
       const note = document.createElement("textarea");
       note.rows = 2;
       note.maxLength = 1000;
       note.placeholder = "دلیل تصمیم (حداقل ۲۰ نویسه)";
       note.setAttribute("aria-label", "دلیل تصمیم برای " + proposal.proposed_topic);
       const actionOutput = document.createElement("p");
       actionOutput.setAttribute("role", "status");
       for (const [decision, label] of [["accept", "پذیرش پیشنهاد"], ["reject", "رد پیشنهاد"]]) {
        const action = document.createElement("button");
        action.type = "button";
        action.textContent = label;
        action.addEventListener("click", async () => {
         const reviewNote = note.value.trim();
         if (reviewNote.length < 20) {
          actionOutput.textContent = "برای ثبت تصمیم، توضیحی با حداقل ۲۰ نویسه وارد کنید.";
          return;
         }
         action.disabled = true;
         actionOutput.textContent = "در حال ثبت تصمیم…";
         try {
          const response = await learningApi("/api/learning/curriculum-proposals/" + proposal.id + "/review", {
           method: "POST",
           body: JSON.stringify({decision, note: reviewNote})
          });
          actionOutput.textContent = response.regeneration
           ? "تصمیم ثبت شد؛ درس جایگزین در صف قرار گرفت. شناسه کار: " + response.regeneration.job_id
           : "تصمیم ثبت شد؛ نسخه‌ها و سوابق قبلی حفظ شدند.";
          await refreshCurriculumProposals();
         } catch (error) {
          actionOutput.textContent = "ثبت تصمیم ناموفق بود: " + error.message;
         } finally {
          action.disabled = false;
         }
        });
        card.append(action);
       }
       card.append(note, actionOutput);
      }
      proposalOutput.append(card);
     });
    }
 
    async function refreshCurriculumProposals() {
     proposalOutput.textContent = "در حال دریافت پیشنهادهای ثبت‌شده…";
     try {
      const result = await learningApi("/api/learning/paths/" + path.id + "/curriculum-proposals");
      renderCurriculumProposals(result);
     } catch (error) {
      proposalOutput.textContent = "دریافت پیشنهادهای تطبیق مسیر ناموفق بود: " + error.message;
     }
    }
 
    generateProposalsButton.addEventListener("click", async () => {
     generateProposalsButton.disabled = true;
     proposalOutput.textContent = "در حال تحلیل نمره‌های ذخیره‌شده و تغییرات منابع…";
     try {
      const result = await learningApi("/api/learning/paths/" + path.id + "/curriculum-proposals/generate", {
       method: "POST"
      });
      renderCurriculumProposals(result);
     } catch (error) {
      proposalOutput.textContent = "تولید پیشنهادهای تطبیق مسیر ناموفق بود: " + error.message;
     } finally {
      generateProposalsButton.disabled = false;
     }
    });
    proposalControls.append(generateProposalsButton, proposalOutput);
    details.append(proposalControls);
 
 
    const bulkControls = document.createElement("div");
    bulkControls.className = "path-bulk-controls";
    const startAll = document.createElement("button");
    startAll.type = "button";
    startAll.textContent = "شروع یادگیری همه درس‌ها";
    const stopAll = document.createElement("button");
    stopAll.type = "button";
    stopAll.textContent = "توقف یادگیری همه درس‌ها";
    const bulkOutput = document.createElement("p");
    bulkOutput.setAttribute("role", "status");
 
    startAll.addEventListener("click", async () => {
     startAll.disabled = true;
     stopAll.disabled = true;
     bulkOutput.textContent = "در حال افزودن درس‌های باقی‌مانده به صف…";
     try {
      const result = await learningApi("/api/learning/paths/" + path.id + "/start", {method: "POST"});
      bulkOutput.textContent = result.message + " تعداد افزوده‌شده: " + result.queued_count +
       "؛ از قبل فعال: " + result.skipped_active_count +
       "؛ درس‌های قبلاً تولیدشده و حفظ‌شده: " + result.skipped_existing_lesson_count +
       "؛ مسیرهای تکمیل‌شده: " + result.skipped_completed_count + ".";
      path.topics.forEach(topic => {
       if (topic.status !== "completed") topic.statusSelect.value = "in_progress";
      });
     } catch (error) {
      bulkOutput.textContent = "خطا در شروع گروهی: " + error.message;
     } finally {
      startAll.disabled = false;
      stopAll.disabled = false;
     }
    });
 
    stopAll.addEventListener("click", async () => {
     startAll.disabled = true;
     stopAll.disabled = true;
     bulkOutput.textContent = "در حال توقف صف یادگیری…";
     try {
      const result = await learningApi("/api/learning/paths/" + path.id + "/stop", {method: "POST"});
      bulkOutput.textContent = result.message + " تعداد متوقف‌شده: " + result.cancelled_count +
       " (در صف: " + result.cancelled_queued_count + "، در حال اجرا: " + result.cancelled_running_count + ").";
     } catch (error) {
      bulkOutput.textContent = "خطا در توقف گروهی: " + error.message;
     } finally {
      startAll.disabled = false;
      stopAll.disabled = false;
     }
    });
 
    bulkControls.append(startAll, stopAll, bulkOutput);
    details.append(bulkControls);
 
    path.topics.forEach(topic => {
     const row = document.createElement("div");
     row.className = "path-topic";
     const label = document.createElement("label");
     label.textContent = topic.position + ". " + topic.topic;
     const status = document.createElement("select");
     [["pending","شروع‌نشده"],["in_progress","در حال یادگیری"],["completed","تکمیل‌شده"]].forEach(([value,title]) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = title;
      status.append(option);
     });
     status.value = topic.status;
     topic.statusSelect = status;
     topic.row = row;
     const score = document.createElement("span");
     score.className = "path-topic-score";
     score.textContent = topic.latest_score == null
      ? "امتیاز کیفیت درس: پس از تولید خودکار محاسبه می‌شود"
      : "امتیاز کیفیت درس: " + topic.latest_score + " از 100 (خودکار)";
     const save = document.createElement("button");
     save.type = "button";
     save.textContent = "ذخیره وضعیت";
     save.addEventListener("click", async () => {
      save.disabled = true;
      try {
       await learningApi("/api/learning/paths/" + path.id + "/topics/" + topic.id, {
        method: "PATCH", body: JSON.stringify({status: status.value})
       });
       await loadLearningPaths();
      } catch (error) {
       learningEl("paths-output").textContent = error.message;
       save.disabled = false;
      }
     });
     const start = document.createElement("button");
     start.type = "button";
     start.textContent = "شروع / ادامه درس";
     const stop = document.createElement("button");
     stop.type = "button";
     stop.textContent = "توقف درس";
     stop.disabled = true;
     const historyOutput = document.createElement("div");
     historyOutput.className = "path-topic-job-history-container";
     const savedJobs = (jobsByTopicId.get(Number(topic.id)) || []).slice(0, 5);
     renderTopicJobHistory(historyOutput, topic, savedJobs, tasksByJobId);
     const output = document.createElement("div");
     output.className = "path-topic-lesson";
     const reviewLabel = document.createElement("label");
     reviewLabel.textContent = "میزان یادآوری (۰ تا ۵)";
     const reviewRating = document.createElement("select");
     [[0,"۰ — کاملاً فراموش کردم"],[1,"۱ — بسیار دشوار"],[2,"۲ — دشوار"],[3,"۳ — با تلاش یادآوردم"],[4,"۴ — خوب"],[5,"۵ — آسان"]].forEach(([value,title]) => {
      const option = document.createElement("option");
      option.value = String(value);
      option.textContent = title;
      reviewRating.append(option);
     });
     reviewLabel.append(reviewRating);
     const reviewButton = document.createElement("button");
     reviewButton.type = "button";
     reviewButton.textContent = "ثبت مرور و زمان‌بندی بعدی";
     const reviewOutput = document.createElement("p");
     reviewOutput.setAttribute("role", "status");
     reviewButton.addEventListener("click", async () => {
      reviewButton.disabled = true;
      reviewOutput.textContent = "در حال ثبت مرور…";
      try {
       const result = await learningApi("/api/learning/paths/" + path.id + "/topics/" + topic.id + "/reviews", {
        method: "POST", body: JSON.stringify({rating: Number(reviewRating.value)})
       });
       reviewOutput.textContent = "مرور ثبت شد. مرور بعدی: " + result.due_at +
        " (فاصله: " + result.interval_days + " روز). " + result.note;
       await refreshDueLearningReviews();
      } catch (error) {
       reviewOutput.textContent = "ثبت مرور ناموفق بود: " + error.message;
      } finally {
       reviewButton.disabled = false;
      }
     });
     start.addEventListener("click", () => startLesson(path, topic, start, stop, output));
     stop.addEventListener("click", () => stopLesson(path, topic, stop, output));
     row.append(label, status, score, save, start, stop, reviewLabel, reviewButton, reviewOutput, historyOutput, output);
     details.append(row);
    });
    pathCard.append(progressGroup, details);
    container.append(pathCard);
   });
  } catch (error) {
   if (requestSequence === learningPathsLoadSequence) learningEl("paths-output").textContent = error.message;
  }
 }
 
 
 document.addEventListener("DOMContentLoaded",()=>{
  const refresh=learningEl("refresh-paths");
  if(refresh)refresh.addEventListener("click",loadLearningPaths);
  loadLearningPaths();
 });
})();