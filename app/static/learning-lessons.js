(() => {
 "use strict";
 const core=window.learningCore;
 const fallback=document.getElementById("paths-output");
 if(!core||core.version!==1){
  if(fallback)fallback.textContent="ماژول تولید و ارزیابی درس به learningCore v1 نیاز دارد.";
  return;
 }
 const {el:learningEl,api:learningApi,delay:learningDelay,waitForLearningJob,learningStatusLabels,taskStatusLabels}=core;
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
     reviewResult.textContent = "بررسی ذخیره شد. ارزش درس: " + saved.value_rating + " از 5. " + saved.note
      + (saved.correction_job ? " شناسه job اصلاحی: " + saved.correction_job.id + "؛ نسخه قبلی حفظ شده است." : "");
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
  let pendingSubmissionKey = null;
  let pendingAnswersSnapshot = null;
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
   const answers = fields.map(field => field.value);
   const answersSnapshot = JSON.stringify(answers);
   if (!pendingSubmissionKey || pendingAnswersSnapshot !== answersSnapshot) {
    pendingAnswersSnapshot = answersSnapshot;
    pendingSubmissionKey = "assessment-" + job.id + "-" + Date.now().toString(36) + "-" +
     Math.random().toString(36).slice(2, 12);
   }
   try {
    const outcome = await learningApi("/api/learning/jobs/" + job.id + "/assessment", {
     method: "POST",
     body: JSON.stringify({answers, idempotency_key: pendingSubmissionKey})
    });
    pendingSubmissionKey = null;
    pendingAnswersSnapshot = null;
    latestAttemptId = outcome.attempt_id;
    semanticReviewButton.hidden = false;
    semanticReviewResult.textContent = "آزمون ثبت شد. بازبینی معنایی اختیاری است و نمره قطعی را تغییر نمی‌دهد.";
    const rubricBased = outcome.scoring_method === "rubric_concept_coverage_v1";
    const scoreUnit = rubricBased ? "پوشش معیارهای مفهومی" : "پاسخ منطبق";
    const threshold = rubricBased ? 80 : 70;
    const pathStatus = outcome.path_topic_status === "completed"
     ? "سرفصل با عبور از آستانهٔ " + threshold + " تکمیل شد."
     : "برای تکمیل سرفصل باید حداقل " + threshold + " از 100 کسب شود.";
    result.textContent = "نمره: " + outcome.score + " از 100 (" +
     outcome.correct_count + " " + scoreUnit + " از " + outcome.question_count + "). " +
     pathStatus + " " + outcome.note;
    if (Array.isArray(outcome.per_question)) {
     result.textContent += " نمرهٔ هر سؤال: " + outcome.per_question.map(item =>
      item.question_index + ": " + item.score
     ).join("، ") + ".";
    }
    result.textContent += " این نمره به‌تنهایی اثبات فهم کامل یا صحت علمی مستقل نیست.";
   } catch (error) {
    result.textContent = "خطا در ثبت آزمون: " + error.message;
    submit.disabled = false;
   }
  });
  output.append(form);
  const caveat = document.createElement("p");
  caveat.textContent = "در صورت وجود rubric معتبر، نمره بر اساس پوشش معیارهای مفهومی محاسبه می‌شود؛ در آزمون‌های قدیمی از تطبیق متنی استفاده می‌شود. هیچ‌کدام به‌تنهایی اثبات قطعی فهم یا تأیید علمی مستقل نیستند.";
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
 
 
 window.learningLessons=Object.freeze({version:1,startLesson,stopLesson});
})();