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

async function addPracticalExerciseForm(output, job, path, topic) {
 let exercises;
 try {
  const catalog = await learningApi("/api/learning/practical/exercises?job_id=" + encodeURIComponent(job.id));
  exercises = catalog.exercises || [];
 } catch (_error) {
  return;
 }
 if (output.dataset.learningJobId !== String(job.id)) return;
 exercises.forEach(exercise => renderPracticalExerciseForm(output, job, path, topic, exercise));
}

function renderPracticalExerciseForm(output, job, path, topic, exercise) {
 const section = document.createElement("section");
 section.className = "learning-practical-exercise";
 const heading = document.createElement("h4");
 heading.textContent = "تمرین عملی با اجرای واقعی کد";
 section.append(heading);
 const description = document.createElement("p");
 description.textContent = "این تمرین برنامه را در Docker sandbox بدون شبکه اجرا می‌کند و نتیجه را با آزمون‌های ورودی/خروجی نسخه‌بندی‌شده می‌سنجد. برای اجرا، رضایت صریح sandbox و Docker پیکربندی‌شده لازم است.";
 section.append(description);
 const exerciseInfo = document.createElement("p");
 exerciseInfo.textContent = exercise.title + "؛ ورودی نمونه: " + JSON.stringify(exercise.input_example) + "؛ خروجی نمونه: " + JSON.stringify(exercise.output_example);
 section.append(exerciseInfo);
 const form = document.createElement("form");
 const source = document.createElement("textarea");
 source.rows = 8;
 source.maxLength = 32768;
 source.required = true;
 source.style.display = "block";
 source.style.width = "100%";
 source.setAttribute("aria-label", "کد پایتون برای تمرین عملی");
 const starter = exercise.id === "python-json-unique-v1"
  ? ["import json", "import sys", "data = json.load(sys.stdin)", "print(json.dumps(sorted(set(data[\"values\"]))))"]
  : exercise.id === "python-json-word-count-v1"
   ? ["import json", "import sys", "data = json.load(sys.stdin)", "print(json.dumps(len(data[\"text\"].split())))"]
   : ["import json", "import sys", "data = json.load(sys.stdin)", "print(json.dumps(sum(data[\"values\"])))"];
 source.value = starter.join(String.fromCharCode(10));
 const submit = document.createElement("button");
 submit.type = "submit";
 submit.textContent = "اجرای آزمون عملی";
 form.append(submit);
 const status = document.createElement("p");
 status.setAttribute("role", "status");
 form.append(status);
 form.addEventListener("submit", async event => {
  event.preventDefault();
  submit.disabled = true;
  status.textContent = "در حال ثبت تمرین…";
  try {
   const queued = await learningApi("/api/learning/practical/exercises/" + encodeURIComponent(exercise.id) + "/submit", {
    method: "POST",
    body: JSON.stringify({job_id: job.id, source_code: source.value})
   });
   const taskId = queued.task && queued.task.id;
   if (!taskId) throw new Error("شناسه کار اجرایی برنگشت.");
   status.textContent = "تمرین در صف اجراست؛ شناسه کار: " + taskId;
   let finished = false;
   for (let attempt = 0; attempt < 120; attempt += 1) {
    await new Promise(resolve => setTimeout(resolve, 1000));
    const task = await learningApi("/api/tasks/" + taskId);
    if (["succeeded", "failed", "cancelled"].includes(task.status)) {
     finished = true;
     if (task.status !== "succeeded") {
      status.textContent = "اجرای تمرین موفق نبود: " + (task.error || task.status);
      break;
     }
     const grade = task.result && task.result.practical_assessment;
     if (!grade) {
      status.textContent = "کد اجرا شد اما گزارش نمره معتبر در دسترس نیست. وضعیت اجرا: " +
       ((task.result && task.result.execution_status) || "نامشخص");
      break;
     }
     const failedCases = (grade.case_results || []).filter(item => !item.passed).map(item => item.case_id);
     status.textContent = "نتیجه آزمون عملی: " + grade.score + " از 100؛ " +
      grade.passed_tests + " آزمون از " + grade.total_tests + " موفق. " +
      (failedCases.length ? "آزمون‌های ناموفق: " + failedCases.join("، ") + ". " : "") +
      (grade.mastery_achieved
       ? "تسلط عملی ثبت شد و سرفصل تکمیل شد."
       : "این نتیجه فقط همین تمرین را می‌سنجد؛ برای تکمیل سرفصل، آستانه و پیش‌نیازهای مسیر باید برقرار باشند.");
     if (grade.mastery_achieved && path && topic && topic.statusSelect) {
      topic.status = "completed";
      topic.statusSelect.value = "completed";
      (path.topics || []).forEach(candidate => {
       candidate.blocked_by = (candidate.blocked_by || []).filter(item => item.id !== topic.id);
       candidate.prerequisites_met = candidate.blocked_by.length === 0;
       if (candidate.startButton) candidate.startButton.disabled = !candidate.prerequisites_met;
       if (candidate.prerequisites_met && candidate.prerequisiteNote) candidate.prerequisiteNote.remove();
      });
     }
     break;
    }
   }
   if (!finished) status.textContent = "تمرین هنوز در حال اجراست. شناسه کار برای پیگیری: " + taskId;
  } catch (error) {
   status.textContent = "تمرین عملی در دسترس نیست: " + error.message;
  } finally {
   submit.disabled = false;
  }
 });
 section.append(form);
 output.append(section);
}
 
function renderLessonResult(output, job, path, topic) {
 output.replaceChildren();
 output.dataset.learningJobId = String(job.id);
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
 qualityNote.textContent = "امتیاز کیفیت ساختاری درس: " + (job.quality ? job.quality.score : "نامشخص") + " از 100. این امتیاز کیفیت محتوا را می‌سنجد، نه میزان یادگیری شما.";
 output.append(qualityNote);
 const evidenceGate = job.quality && job.quality.scientific_acceptance;
 if (evidenceGate) {
  const evidenceNote = document.createElement("p");
  evidenceNote.setAttribute("role", "status");
  if (evidenceGate.accepted) {
   evidenceNote.textContent = "دروازه خودکار شواهد عبور کرد؛ این نتیجه اثبات مستقل صحت علمی نیست.";
  } else {
   evidenceNote.textContent = "دروازه شواهد عبور نکرده است؛ درس به‌عنوان محتوای تأییدشده علمی تلقی نشود. شروط ناموفق: " +
    (evidenceGate.failed_gates || []).join("، ");
  }
  output.append(evidenceNote);
 }
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
 const questionTypeLabels = {
  recall: "بازیابی",
  explanation: "توضیح علت‌ومعلول",
  transfer: "کاربرد و انتقال آموخته‌ها"
 };
 assessment.questions.forEach((question, index) => {
  const label = document.createElement("label");
  const questionType = Array.isArray(assessment.question_types) ? assessment.question_types[index] : null;
  label.textContent = (index + 1) + (questionType && questionTypeLabels[questionType] ? " [" + questionTypeLabels[questionType] + "] " : ". ") + question;
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
 const quizAttempts = Array.isArray(job.quiz_assessment_attempts)
  ? job.quiz_assessment_attempts
  : (Array.isArray(job.assessment_attempts) ? job.assessment_attempts : []).filter(
   attempt => attempt.scoring_method !== "sandbox_practical_v1"
  );
 let latestAttemptId = quizAttempts.length ? quizAttempts[0].id : null;
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
    outcome.correct_count + " پاسخ با پوشش کافی از " + outcome.question_count + "). " +
    outcome.note;
   if (outcome.mastery_achieved) {
    topic.status = "completed";
    topic.statusSelect.value = "completed";
    path.topics.forEach(candidate => {
     candidate.blocked_by = (candidate.blocked_by || []).filter(item => item.id !== topic.id);
     candidate.prerequisites_met = candidate.blocked_by.length === 0;
     if (candidate.startButton) candidate.startButton.disabled = !candidate.prerequisites_met;
     if (candidate.prerequisites_met && candidate.prerequisiteNote) candidate.prerequisiteNote.remove();
    });
    result.textContent += " آستانه تسلط " + outcome.mastery_threshold + "٪ کسب شد؛ تکمیل سرفصل به‌صورت خودکار ثبت شد.";
   } else if (job.path_topic_id) {
    topic.statusSelect.value = "in_progress";
    result.textContent += " برای تکمیل خودکار سرفصل، در آزمون rubric دست‌کم " + outcome.mastery_threshold + "٪ لازم است.";
   }
   result.textContent += " امتیاز کیفیت درس جداگانه محاسبه می‌شود و با نمره یادگیری یکی نیست."
  } catch (error) {
   result.textContent = "خطا در ثبت آزمون: " + error.message;
   submit.disabled = false;
  }
 });
 output.append(form);
 addPracticalExerciseForm(output, job, path, topic);
 const caveat = document.createElement("p");
 caveat.textContent = "آزمون درس‌های جدید بر اساس پوشش واژه‌ای مفاهیم rubric نمره می‌دهد؛ این روش پاسخ‌های هم‌معنا را بهتر می‌پذیرد، اما به‌تنهایی فهم معنایی مستقل یا صحت علمی را اثبات نمی‌کند."
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

let learningPathsPage=1;
async function loadLearningPaths(page=learningPathsPage) {
 learningPathsPage=Math.max(1,Number(page)||1);
 try {
  const data = await learningApi("/api/learning/paths?page="+learningPathsPage+"&page_size=25");
  const p=data.pagination||{page:learningPathsPage,has_previous:learningPathsPage>1,has_next:false,total:(data.paths||[]).length,pages:1};
  learningPathsPage=p.page;
  if(learningEl("learning-path-page-prev")) learningEl("learning-path-page-prev").disabled=!p.has_previous;
  if(learningEl("learning-path-page-next")) learningEl("learning-path-page-next").disabled=!p.has_next;
  if(learningEl("learning-path-page-status")) learningEl("learning-path-page-status").textContent="صفحهٔ "+p.page+(p.pages?" از "+p.pages:"")+" | کل: "+p.total;
  const container = learningEl("paths-container");
  container.replaceChildren();
  learningEl("paths-output").textContent = "تعداد کل مسیرهای ثبت‌شده: " + p.total;
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

   const bulkControls = document.createElement("div");
   bulkControls.className = "path-bulk-controls";
   const startAll = document.createElement("button");
   startAll.type = "button";
   startAll.textContent = "شروع درس بعدیِ مجاز";
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
      "؛ مسیرهای تکمیل‌شده: " + result.skipped_completed_count +
      "؛ درس‌های مسدود به‌علت پیش‌نیاز: " + (result.skipped_prerequisite_count || 0) + ".";
     path.topics.forEach(topic => {
      if (topic.prerequisites_met && topic.status !== "completed") topic.statusSelect.value = "in_progress";
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
     if (value === "completed" && topic.status !== "completed") option.disabled = true;
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
    start.disabled = Array.isArray(topic.blocked_by) && topic.blocked_by.length > 0;
    topic.startButton = start;
    if (start.disabled) {
     const prerequisiteNote = document.createElement("p");
     prerequisiteNote.className = "path-topic-prerequisites";
     prerequisiteNote.textContent = "ابتدا این سرفصل‌ها را تکمیل کنید: " + topic.blocked_by.map(item => item.topic).join("، ");
     topic.prerequisiteNote = prerequisiteNote;
     row.append(prerequisiteNote);
    }
    const stop = document.createElement("button");
    stop.type = "button";
    stop.textContent = "توقف درس";
    stop.disabled = true;
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
    row.append(label, status, score, save, start, stop, reviewLabel, reviewButton, reviewOutput, output);
    details.append(row);
   });
   pathCard.append(progressGroup, details);
   container.append(pathCard);
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
 learningEl("refresh-paths").addEventListener("click",()=>loadLearningPaths(learningPathsPage));
 learningEl("learning-path-page-prev")?.addEventListener("click",()=>loadLearningPaths(learningPathsPage-1));
 learningEl("learning-path-page-next")?.addEventListener("click",()=>loadLearningPaths(learningPathsPage+1));
 learningEl("refresh-due-reviews").addEventListener("click", refreshDueLearningReviews);
 learningEl("refresh-learning-recommendations").addEventListener("click", refreshLearningRecommendations);
 loadLearningCurricula();
 loadLearningPaths();
 refreshDueLearningReviews();
 refreshLearningRecommendations();
});
