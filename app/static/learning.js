(() => {
 "use strict";
 const core=window.learningCore;
 const lessons=window.learningLessons;
 const overview=window.learningOverview;
 const history=window.learningHistory;
 const curriculum=window.learningCurriculum;
 const fallback=document.getElementById("paths-output");
 if(!core||core.version!==1){
  if(fallback)fallback.textContent="ماژول مسیرهای یادگیری به قرارداد learningCore v1 نیاز دارد.";
  return;
 }
 const lessonsAvailable=Boolean(lessons&&lessons.version===1&&
  typeof lessons.startLesson==="function"&&typeof lessons.stopLesson==="function");
 const overviewAvailable=Boolean(overview&&overview.version===1&&
  typeof overview.refreshDueReviews==="function");
 const {el:learningEl,api:learningApi}=core;
 const startLesson=lessonsAvailable?lessons.startLesson:null;
 const stopLesson=lessonsAvailable?lessons.stopLesson:null;
 const refreshDueLearningReviews=overviewAvailable?overview.refreshDueReviews:async()=>{};
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
 
    if (curriculum && curriculum.version === 1) {
     details.append(curriculum.createControls(path));
    } else {
     const unavailable = document.createElement("p");
     unavailable.className = "muted";
     unavailable.textContent = "ماژول پیشنهادهای تطبیق مسیر در دسترس نیست؛ سایر کنترل‌های مسیر فعال هستند.";
     details.append(unavailable);
    }
 
 
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
     if (history && history.version === 1) {
      history.renderTopicJobHistory(historyOutput, topic, savedJobs, tasksByJobId);
     } else {
      historyOutput.textContent = "ماژول سوابق یادگیری در دسترس نیست؛ سایر کنترل‌های درس فعال هستند.";
     }
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
       if (overviewAvailable) await refreshDueLearningReviews();
      } catch (error) {
       reviewOutput.textContent = "ثبت مرور ناموفق بود: " + error.message;
      } finally {
       reviewButton.disabled = false;
      }
     });
     if (lessonsAvailable) {
      start.addEventListener("click", () => startLesson(path, topic, start, stop, output));
      stop.addEventListener("click", () => stopLesson(path, topic, stop, output));
     } else {
      start.disabled = true;
      stop.disabled = true;
      start.title = "ماژول تولید درس در دسترس نیست.";
      stop.title = "ماژول تولید درس در دسترس نیست.";
      output.textContent = "ماژول تولید و توقف درس در دسترس نیست؛ وضعیت مسیر و سایر کنترل‌ها فعال هستند.";
     }
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