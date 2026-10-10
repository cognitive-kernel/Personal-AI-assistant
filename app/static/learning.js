const learningEl = id => document.getElementById(id);

async function learningApi(path, options = {}) {
 const response = await fetch(path, {headers: {"Content-Type":"application/json"}, ...options});
 const data = await response.json().catch(() => ({}));
 if (!response.ok) throw new Error(data.detail || ("HTTP " + response.status));
 return data;
}

const learningDelay = ms => new Promise(resolve => setTimeout(resolve, ms));

async function waitForLearningJob(jobId, output) {
 for (let attempt = 0; attempt < 600; attempt += 1) {
  const job = await learningApi("/api/learning/jobs/" + jobId);
  if (["completed", "rejected", "failed", "cancelled"].includes(job.status)) return job;
  output.textContent = "درس در حال تولید است… وضعیت: " + job.status + " (شناسه کار: " + jobId + ")";
  await learningDelay(1500);
 }
 throw new Error("پس از ۱۵ دقیقه انتظار، هنوز نتیجه نهایی دریافت نشد؛ کار ممکن است در پس‌زمینه ادامه داشته باشد. شناسه کار: " + jobId);
}

async function regenerateLesson(path, topic, button, output) {
 button.disabled = true;
 output.textContent = "در حال تولید نسخه تازه درس…";
 try {
  const job = await learningApi("/api/learning/paths/" + path.id + "/topics/" + topic.id + "/regenerate", {
   method: "POST"
  });
  topic.lessonActiveJobId = job.id;
  const finished = ["completed", "rejected", "failed", "cancelled"].includes(job.status)
   ? job
   : await waitForLearningJob(job.id, output);
  renderLessonResult(output, finished, path, topic);
 } catch (error) {
  output.textContent = "خطا در تولید مجدد درس: " + error.message;
 } finally {
  button.disabled = false;
 }
}

function addRegenerateButton(output, path, topic) {
 const button = document.createElement("button");
 button.type = "button";
 button.textContent = "تولید مجدد درس";
 button.addEventListener("click", () => regenerateLesson(path, topic, button, output));
 output.append(button);
}

function renderLessonResult(output, job, path, topic) {
 output.replaceChildren();
 if (job.quality && topic.row) {
  const scoreField = topic.row.querySelector(".path-topic-score");
  if (scoreField) scoreField.textContent = "امتیاز کیفیت درس: " + job.quality.score + " از 100 (خودکار)";
 }
 if (job.status !== "completed") {
  if (job.quality) {
   const qualityNote = document.createElement("p");
   qualityNote.textContent = "امتیاز کیفیت خودکار: " + job.quality.score + " از 100.";
   output.append(qualityNote);
  }
  const errorNote = document.createElement("p");
  errorNote.textContent = job.error || (job.status === "rejected"
   ? "درس به دلیل کنترل کیفیت ذخیره نشد. می‌توانید نسخه تازه‌ای تولید کنید."
   : "تولید درس موفق نبود. وضعیت کار: " + job.status);
  output.append(errorNote);
  addRegenerateButton(output, path, topic);
  return;
 }

 const qualityNote = document.createElement("p");
 qualityNote.textContent = "امتیاز کیفیت خودکار درس: " + (job.quality ? job.quality.score : "نامشخص") + " از 100. این امتیاز کیفیت محتوا را می‌سنجد، نه میزان یادگیری شما.";
 output.append(qualityNote);
 addRegenerateButton(output, path, topic);
 const lesson = document.createElement("p");
 lesson.textContent = job.lesson || "متن درس در پاسخ موجود نیست.";
 output.append(lesson);

 const assessment = job.assessment;
 if (assessment) {
  const reviewForm = document.createElement("form");
  reviewForm.className = "lesson-review-form";
  const reviewHeading = document.createElement("h4");
  reviewHeading.textContent = "بررسی ارزش آموزشی و میزان یادگیری";
  reviewForm.append(reviewHeading);
  const intro = document.createElement("p");
  intro.textContent = "برای هر هدف مشخص کن چقدر بر آن مسلطی. این ارزیابی از نمره خودکار کیفیت درس جداست.";
  reviewForm.append(intro);
  const outcomes = Array.isArray(assessment.learning_outcomes) && assessment.learning_outcomes.length
   ? assessment.learning_outcomes : (assessment.questions || []);
  const previous = job.latest_review || null;
  const outcomeFields = [];
  const statusLabels = [
   ["mastered", "مسلط هستم"],
   ["partial", "تا حدی یاد گرفته‌ام"],
   ["review", "نیاز به آموزش دوباره دارم"]
  ];
  outcomes.forEach((outcome, index) => {
   const label = document.createElement("label");
   label.textContent = (index + 1) + ". " + outcome;
   label.style.display = "block";
   label.style.margin = "10px 0 4px";
   const select = document.createElement("select");
   select.required = true;
   select.style.display = "block";
   select.style.maxWidth = "100%";
   statusLabels.forEach(([value, title]) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = title;
    select.append(option);
   });
   if (previous && previous.outcomes && previous.outcomes[index]) select.value = previous.outcomes[index];
   label.append(select);
   reviewForm.append(label);
   outcomeFields.push(select);
  });
  const ratingLabel = document.createElement("label");
  ratingLabel.textContent = "این درس چقدر ارزشمند و کاربردی بود؟ (۱ = کم، ۵ = بسیار زیاد)";
  ratingLabel.style.display = "block";
  const rating = document.createElement("select");
  rating.required = true;
  for (let score = 1; score <= 5; score += 1) {
   const option = document.createElement("option");
   option.value = String(score);
   option.textContent = String(score);
   rating.append(option);
  }
  if (previous && previous.value_rating) rating.value = String(previous.value_rating);
  ratingLabel.append(rating);
  reviewForm.append(ratingLabel);

  const takeawayLabel = document.createElement("label");
  takeawayLabel.textContent = "مهم‌ترین چیزی که از این درس یاد گرفتم";
  takeawayLabel.style.display = "block";
  const takeaway = document.createElement("textarea");
  takeaway.rows = 3;
  takeaway.maxLength = 3000;
  takeaway.style.display = "block";
  takeaway.style.width = "100%";
  takeaway.value = previous ? (previous.takeaway || "") : "";
  takeaway.placeholder = "با زبان خودت بنویس چه چیزی یاد گرفتی یا حالا چه کاری می‌توانی انجام بدهی.";
  takeawayLabel.append(takeaway);
  reviewForm.append(takeawayLabel);

  const issuesLabel = document.createElement("label");
  issuesLabel.textContent = "اشکال‌ها، بخش‌های مبهم یا چیزهایی که باید بهتر آموزش داده شوند";
  issuesLabel.style.display = "block";
  const issues = document.createElement("textarea");
  issues.rows = 3;
  issues.maxLength = 3000;
  issues.style.display = "block";
  issues.style.width = "100%";
  issues.value = previous ? (previous.issues || "") : "";
  issues.placeholder = "مثلاً تعریف نادرست، مثال ناکافی، عبارت نامفهوم یا موضوع جاافتاده.";
  issuesLabel.append(issues);
  reviewForm.append(issuesLabel);

  const saveReview = document.createElement("button");
  saveReview.type = "submit";
  saveReview.textContent = previous ? "ذخیره بررسی جدید" : "ذخیره بررسی درس";
  reviewForm.append(saveReview);
  const reviewResult = document.createElement("p");
  reviewResult.setAttribute("role", "status");
  if (previous) reviewResult.textContent = "آخرین بررسی ذخیره‌شده: ارزش درس " + previous.value_rating + " از 5.";
  reviewForm.append(reviewResult);
  reviewForm.addEventListener("submit", async event => {
   event.preventDefault();
   saveReview.disabled = true;
   reviewResult.textContent = "در حال ذخیره بررسی…";
   try {
    const saved = await learningApi("/api/learning/jobs/" + job.id + "/review", {
     method: "POST",
     body: JSON.stringify({
      outcomes: outcomeFields.map(field => field.value),
      value_rating: Number(rating.value),
      takeaway: takeaway.value,
      issues: issues.value
     })
    });
    reviewResult.textContent = "بررسی ذخیره شد. ارزش درس: " + saved.value_rating + " از 5. " + saved.note;
    saveReview.textContent = "ذخیره بررسی جدید";
   } catch (error) {
    reviewResult.textContent = "خطا در ذخیره بررسی: " + error.message;
   } finally {
    saveReview.disabled = false;
   }
  });
  const caveat = document.createElement("p");
  caveat.textContent = "این فرم خودارزیابی و بازخورد شما را ثبت می‌کند؛ به‌تنهایی صحت علمی درس را اثبات نمی‌کند.";
  reviewForm.append(caveat);
  output.append(reviewForm);
 }
 if (!assessment || !Array.isArray(assessment.questions) || assessment.questions.length === 0) {
  const note = document.createElement("p");
  note.textContent = "برای این درس آزمون قابل ارائه‌ای وجود ندارد.";
  output.append(note);
  return;
 }

 const form = document.createElement("form");
 const heading = document.createElement("h4");
 heading.textContent = "آزمون درس";
 form.append(heading);
 const fields = [];
 assessment.questions.forEach((question, index) => {
  const label = document.createElement("label");
  label.textContent = (index + 1) + ". " + question;
  const answer = document.createElement("textarea");
  answer.required = true;
  answer.maxLength = 2000;
  answer.rows = 2;
  answer.style.display = "block";
  answer.style.width = "100%";
  label.append(answer);
  form.append(label);
  fields.push(answer);
 });
 const submit = document.createElement("button");
 submit.type = "submit";
 submit.textContent = "ثبت پاسخ‌ها";
 form.append(submit);
 const result = document.createElement("p");
 form.append(result);
 form.addEventListener("submit", async event => {
  event.preventDefault();
  submit.disabled = true;
  result.textContent = "در حال ارزیابی پاسخ‌ها…";
  try {
   const outcome = await learningApi("/api/learning/jobs/" + job.id + "/assessment", {
    method: "POST",
    body: JSON.stringify({answers: fields.map(field => field.value)})
   });
   result.textContent = "نمره: " + outcome.score + " از 100 (" +
    outcome.correct_count + " پاسخ منطبق از " + outcome.question_count + "). " +
    outcome.note + " وضعیت تکمیل مسیر را جداگانه ذخیره کنید.";
   result.textContent += " این نمره مربوط به پاسخ‌های آزمون شماست؛ امتیاز کیفیت درس به‌صورت خودکار و جداگانه ثبت می‌شود.";
  } catch (error) {
   result.textContent = "خطا در ثبت آزمون: " + error.message;
   submit.disabled = false;
  }
 });
 output.append(form);
 const caveat = document.createElement("p");
 caveat.textContent = "این آزمون بر اساس تطبیق متنی پاسخ‌ها نمره می‌دهد و تأیید علمی مستقل یا فهم معنایی را اثبات نمی‌کند.";
 output.append(caveat);
}

async function startLesson(path, topic, button, stopButton, output) {
 button.disabled = true;
 stopButton.disabled = false;
 output.textContent = "در حال ثبت یا ادامه کار یادگیری…";
 try {
  if (topic.statusSelect.value !== "completed") {
   await learningApi("/api/learning/paths/" + path.id + "/topics/" + topic.id, {
    method: "PATCH",
    body: JSON.stringify({status: "in_progress"})
   });
   topic.statusSelect.value = "in_progress";
  }
  const job = await learningApi("/api/learning/paths/" + path.id + "/topics/" + topic.id + "/start", {
   method: "POST"
  });
  topic.lessonActiveJobId = job.id;
  if (job.status === "completed") {
   renderLessonResult(output, job, path, topic);
   stopButton.disabled = true;
   return;
  }
  output.textContent = "کار یادگیری ثبت شد؛ شناسه: " + job.id + "؛ برای توقف از دکمه «توقف درس» استفاده کنید.";
  const finished = await waitForLearningJob(job.id, output);
  renderLessonResult(output, finished, path, topic);
  stopButton.disabled = true;
 } catch (error) {
  output.textContent = "خطا در شروع یا ادامه درس: " + error.message;
 } finally {
  button.disabled = false;
 }
}

async function stopLesson(path, topic, stopButton, output) {
 stopButton.disabled = true;
 try {
  const result = await learningApi("/api/learning/paths/" + path.id + "/topics/" + topic.id + "/stop", {
   method: "POST"
  });
  output.textContent = result.message;
 } catch (error) {
  output.textContent = "خطا در توقف درس: " + error.message;
  stopButton.disabled = false;
 }
}

async function loadLearningCurricula() {
 try {
  const data = await learningApi("/api/learning/curricula");
  const select = learningEl("curriculum-select");
  select.replaceChildren();
  data.curricula.forEach(course => {
   const option = document.createElement("option");
   option.value = course.slug;
   option.textContent = course.title + " (" + course.topic_count + " سرفصل)";
   select.append(option);
  });
 } catch (error) { learningEl("paths-output").textContent = error.message; }
}

async function loadLearningPaths() {
 try {
  const data = await learningApi("/api/learning/paths");
  const container = learningEl("paths-container");
  container.replaceChildren();
  learningEl("paths-output").textContent = "تعداد مسیرهای ثبت‌شده: " + data.paths.length;
  data.paths.forEach(path => {
   const details = document.createElement("details");
   const summary = document.createElement("summary");
   summary.textContent = path.title + " — " + path.progress_percent + "٪ (" + path.completed_count + "/" + path.topic_count + ")";
   details.append(summary);

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
    const output = document.createElement("div");
    output.className = "path-topic-lesson";
    start.addEventListener("click", () => startLesson(path, topic, start, stop, output));
    stop.addEventListener("click", () => stopLesson(path, topic, stop, output));
    row.append(label, status, score, save, start, stop, output);
    details.append(row);
   });
   container.append(details);
  });
 } catch (error) { learningEl("paths-output").textContent = error.message; }
}

document.addEventListener("DOMContentLoaded", () => {
 const enroll = learningEl("enroll-curriculum");
 if (!enroll) return;
 enroll.addEventListener("click", async () => {
  const slug = learningEl("curriculum-select").value;
  if (!slug) return;
  try {
   const path = await learningApi("/api/learning/paths", {method: "POST", body: JSON.stringify({slug})});
   learningEl("paths-output").textContent = "مسیر ثبت شد: " + path.title;
   await loadLearningPaths();
  } catch (error) { learningEl("paths-output").textContent = error.message; }
 });
 learningEl("refresh-paths").addEventListener("click", loadLearningPaths);
 loadLearningCurricula();
 loadLearningPaths();
});
