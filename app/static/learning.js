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

async function refreshDueLearningReviews() {
 const output = learningEl("due-reviews-output");
 if (!output) return;
 output.textContent = "در حال بارگذاری مرورهای سررسیدشده…";
 try {
  const result = await learningApi("/api/learning/reviews/due?limit=100");
  const items = result.items || [];
  output.textContent = items.length
   ? items.map(item => item.path_title + " — " + item.topic +
      "\nسررسید: " + item.due_at + "؛ فاصله فعلی: " + item.interval_days + " روز").join("\n\n")
   : "در حال حاضر مرور سررسیدشده‌ای وجود ندارد.";
 } catch (error) {
  output.textContent = "دریافت مرورهای سررسیدشده ناموفق بود: " + error.message;
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
   eventsOutput.setAttribute("aria-live", "polite");
   eventsButton.addEventListener("click", async () => {
    eventsButton.disabled = true;
    eventsOutput.textContent = "در حال بارگذاری رویدادهای ذخیره‌شده…";
    try {
     const response = await learningApi("/api/tasks/" + task.id + "/events?limit=100");
     const events = response.events || [];
     eventsOutput.replaceChildren();
     if (!events.length) {
      eventsOutput.textContent = "برای این وظیفه رویدادی ثبت نشده است.";
     } else {
      const list = document.createElement("ol");
      events.slice().reverse().forEach(event => {
       const item = document.createElement("li");
       const change = event.event_type === "status_changed"
        ? ((event.old_status || "—") + " ← " + (event.new_status || "—"))
        : (event.new_status || event.event_type || "رویداد");
       item.textContent = (event.occurred_at || "") + " — " + event.event_type + " — " + change;
       list.append(item);
      });
      eventsOutput.append(list);
     }
    } catch (error) {
     eventsOutput.textContent = "بارگذاری تاریخچه ناموفق بود: " + error.message;
    } finally {
     eventsButton.disabled = false;
    }
   });
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

async function refreshLearningRecommendations() {
 const output = learningEl("learning-recommendations-output");
 if (!output) return;
 output.textContent = "در حال محاسبهٔ گام بعدی یادگیری…";
 try {
  const result = await learningApi("/api/learning/recommendations?limit=20");
  const items = result.recommendations || [];
  const labels = {
   spaced_repetition: "مرور سررسیدشده",
   continue_learning: "ادامهٔ درس فعال",
   reinforcement: "تقویت موضوع ضعیف",
   next_topic: "موضوع بعدی"
  };
  output.textContent = items.length
   ? items.map((item, index) => (index + 1) + ". " + (labels[item.recommendation_type] || item.recommendation_type) +
      "\nمسیر: " + item.path_title + "\nموضوع: " + item.topic +
      "\nدلیل: " + item.reason + (item.due_at ? "\nسررسید: " + item.due_at : "")).join("\n\n") +
      "\n\nاین فهرست فقط توصیه است؛ هیچ درس یا وظیفه‌ای به‌صورت خودکار شروع نشده است."
   : "در حال حاضر گام بعدی مشخصی وجود ندارد.";
 } catch (error) {
  output.textContent = "توصیه‌های یادگیری در دسترس نیست: " + error.message;
 }
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
  takeaway.setAttribute("aria-label", "مهم‌ترین چیزی که از این درس یاد گرفتم");
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
  issues.setAttribute("aria-label", "اشکال‌ها، بخش‌های مبهم یا چیزهایی که باید بهتر آموزش داده شوند");
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
  answer.setAttribute("aria-label", "پاسخ سؤال " + (index + 1) + ": " + question);
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
 const semanticReviewButton = document.createElement("button");
 semanticReviewButton.type = "button";
 semanticReviewButton.textContent = "مشاهده/اجرای بازبینی معنایی کمکی";
 semanticReviewButton.hidden = true;
 const semanticReviewResult = document.createElement("p");
 semanticReviewResult.setAttribute("role", "status");
 form.append(semanticReviewButton, semanticReviewResult);
 let latestAttemptId = Array.isArray(job.assessment_attempts) && job.assessment_attempts.length
  ? job.assessment_attempts[0].id : null;
 semanticReviewButton.hidden = !latestAttemptId;
 semanticReviewResult.textContent = latestAttemptId
  ? "برای آخرین تلاش آزمون می‌توانید بازبینی ذخیره‌شده را ببینید یا بازبینی کمکی تازه‌ای ایجاد کنید."
  : "";
 const showSemanticReview = review => {
  const body = review && review.review ? review.review : {};
  const lines = (body.items || []).map(item =>
   item.index + ". " + item.judgment + " — " + item.rationale +
   ((item.missing_concepts || []).length ? "؛ مفاهیم جاافتاده: " + item.missing_concepts.join("، ") : "")
  );
  if (body.overall_note) lines.push(body.overall_note);
  semanticReviewResult.textContent = lines.join("\n") + (review.note ? "\n\n" + review.note : "");
 };
 semanticReviewButton.addEventListener("click", async () => {
  if (!latestAttemptId) return;
  semanticReviewButton.disabled = true;
  semanticReviewResult.textContent = "در حال بررسی سابقهٔ بازبینی…";
  const reviewPath = "/api/learning/jobs/" + job.id + "/assessment/" + latestAttemptId + "/semantic-review";
  try {
   let review;
   try {
    const saved = await learningApi(reviewPath);
    review = saved.semantic_review;
    semanticReviewResult.textContent = "بازبینی ذخیره‌شده بارگذاری شد.";
    showSemanticReview(review);
    return;
   } catch (error) {
    if (error.status !== 404) throw error;
   }
   if (!window.confirm("بازبینی توسط همان مدل محلی انجام می‌شود؛ مستقل نیست و نمره آزمون را تغییر نمی‌دهد. ایجاد بازبینی کمکی؟")) {
    semanticReviewResult.textContent = "ایجاد بازبینی لغو شد؛ نمره آزمون تغییری نکرده است.";
    return;
   }
   semanticReviewResult.textContent = "در حال بازبینی معنایی کمکی…";
   review = await learningApi(reviewPath, {
    method: "POST", body: JSON.stringify({confirm: true})
   });
   showSemanticReview(review);
  } catch (error) {
   semanticReviewResult.textContent = "بازبینی معنایی در دسترس نیست: " + error.message;
  } finally {
   semanticReviewButton.disabled = false;
  }
 });
 form.addEventListener("submit", async event => {
  event.preventDefault();
  submit.disabled = true;
  result.textContent = "در حال ارزیابی پاسخ‌ها…";
  try {
   const outcome = await learningApi("/api/learning/jobs/" + job.id + "/assessment", {
    method: "POST",
    body: JSON.stringify({answers: fields.map(field => field.value)})
   });
   latestAttemptId = outcome.attempt_id;
   semanticReviewButton.hidden = false;
   semanticReviewResult.textContent = "آزمون ثبت شد. بازبینی معنایی اختیاری است و نمره قطعی را تغییر نمی‌دهد.";
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
  const [jobData, taskData] = await Promise.all([
   learningApi("/api/learning/jobs?limit=200").catch(() => ({jobs: []})),
   learningApi("/api/tasks?limit=100").catch(() => ({tasks: []}))
  ]);
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
 } catch (error) { learningEl("paths-output").textContent = error.message; }
}

async function refreshLearningCapabilities() {
 const output = learningEl("capability-map-output");
 if (!output) return;
 output.textContent = "در حال ساخت نقشه از ارزیابی‌های ذخیره‌شده…";
 try {
  const result = await learningApi("/api/learning/capabilities?limit=200");
  const items = result.capabilities || [];
  const labels = {
   not_assessed: "ارزیابی نشده",
   invalid_evidence: "شواهد نامعتبر",
   needs_reinforcement: "نیازمند تقویت",
   provisional: "شواهد اولیه",
   repeated_provisional: "شواهد اولیهٔ تکرارشده",
   practical_provisional: "شواهد عملی تکرارشده (موقت)",
   conflicting_evidence: "تعارض بین آزمون نظری و عملی"
  };
  output.textContent = items.length
   ? items.map(item =>
      item.path_title + " — " + item.capability +
      "\nوضعیت: " + (labels[item.status] || item.status) +
      "\nآخرین نمره: " + (item.latest_score == null ? "ندارد" : item.latest_score + "/100") +
      "؛ تعداد آزمون نظری معتبر: " + item.assessment_count +
      "\nآزمون عملی معتبر: " + item.practical_attempt_count +
      "؛ برنامه‌های آزمون مستقل: " + item.practical_source_count +
      "\nمصرف مدل: " + item.prompt_tokens + " ورودی / " + item.completion_tokens + " خروجی توکن" +
      "؛ زمان استنتاج: " + item.inference_duration_ms + " میلی‌ثانیه" +
      "؛ امتیاز نظری به‌ازای ۱۰۰۰ توکن خروجی: " + (item.score_points_per_1000_output_tokens == null ? "ناموجود" : item.score_points_per_1000_output_tokens) +
      "\nهزینهٔ تخمینی: " + (item.estimated_cost_microusd == null ? "نامشخص؛ نرخ هزینه تنظیم نشده" : item.estimated_cost_microusd + " میکرو‌دلار") +
      "؛ وضعیت هزینه: " + (item.cost_status || "نامشخص") +
      "\nشاخص ارزش: " + (item.learning_value_cost && item.learning_value_cost.provisional_value_points != null ? item.learning_value_cost.provisional_value_points : "ناموجود") +
      "؛ هزینه به‌ازای هر واحد ارزش: " + (item.learning_value_cost && item.learning_value_cost.estimated_cost_microusd_per_value_point != null ? item.learning_value_cost.estimated_cost_microusd_per_value_point : "ناموجود؛ هزینه یا خط مبنای کافی نیست")
     ).join("\n\n") +
     "\n\nشواهد عملی فقط وقتی تکرارپذیر شمرده می‌شود که دست‌کم دو برنامهٔ آزمون متفاوت با منبع زندهٔ مستند وجود داشته باشد؛ این هم به‌تنهایی انتقال عمومی مهارت را اثبات نمی‌کند."
   : "هنوز سرفصلی برای نمایش در نقشهٔ قابلیت‌ها وجود ندارد.";
 } catch (error) {
  output.textContent = "ساخت نقشهٔ قابلیت‌ها ناموفق بود: " + error.message;
 }
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
 learningEl("refresh-due-reviews").addEventListener("click", refreshDueLearningReviews);
 learningEl("refresh-learning-recommendations").addEventListener("click", refreshLearningRecommendations);
 learningEl("refresh-capability-map").addEventListener("click", refreshLearningCapabilities);
 loadLearningCurricula();
 loadLearningPaths();
 refreshDueLearningReviews();
 refreshLearningRecommendations();
});
