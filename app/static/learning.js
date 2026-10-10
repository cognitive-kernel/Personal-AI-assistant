const learningEl = id => document.getElementById(id);

async function learningApi(path, options = {}) {
 const response = await fetch(path, {headers: {"Content-Type":"application/json"}, ...options});
 const data = await response.json().catch(() => ({}));
 if (!response.ok) throw new Error(data.detail || ("HTTP " + response.status));
 return data;
}

const learningDelay = ms => new Promise(resolve => setTimeout(resolve, ms));

async function waitForLearningJob(jobId, output) {
 for (let attempt = 0; attempt < 120; attempt += 1) {
  const job = await learningApi("/api/learning/jobs/" + jobId);
  if (["completed", "rejected", "failed"].includes(job.status)) return job;
  output.textContent = "درس در حال تولید است… وضعیت: " + job.status + " (شناسه کار: " + jobId + ")";
  await learningDelay(1500);
 }
 throw new Error("زمان انتظار تمام شد؛ کار ممکن است هنوز در پس‌زمینه در حال اجرا باشد. شناسه کار: " + jobId);
}

function renderLessonResult(output, job, path, topic) {
 output.replaceChildren();
 if (job.status !== "completed") {
  output.textContent = job.error || (job.status === "rejected"
   ? "درس به دلیل کنترل کیفیت ذخیره نشد. موضوع را دقیق‌تر کنید و دوباره تلاش کنید."
   : "تولید درس موفق نبود. وضعیت کار: " + job.status);
  return;
 }

 const lesson = document.createElement("p");
 lesson.textContent = job.lesson || "متن درس در پاسخ موجود نیست.";
 output.append(lesson);

 const assessment = job.assessment;
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
   const scoreField = topic.row.querySelector(".path-topic-score");
   if (scoreField) scoreField.value = String(outcome.score);
   await learningApi("/api/learning/paths/" + path.id + "/topics/" + topic.id, {
    method: "PATCH",
    body: JSON.stringify({status: "in_progress", latest_score: outcome.score})
   });
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

async function startLesson(path, topic, button, output) {
 button.disabled = true;
 output.textContent = "در حال ثبت کار یادگیری…";
 try {
  await learningApi("/api/learning/paths/" + path.id + "/topics/" + topic.id, {
   method: "PATCH",
   body: JSON.stringify({status: "in_progress"})
  });
  topic.statusSelect.value = "in_progress";
  const job = await learningApi("/api/learning/jobs", {
   method: "POST",
   body: JSON.stringify({topic: topic.topic})
  });
  output.textContent = "کار یادگیری ثبت شد؛ شناسه: " + job.id;
  const finished = await waitForLearningJob(job.id, output);
  renderLessonResult(output, finished, path, topic);
 } catch (error) {
  output.textContent = "خطا در شروع درس: " + error.message;
 } finally {
  button.disabled = false;
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
    const score = document.createElement("input");
    score.className = "path-topic-score";
    score.type = "number";
    score.min = "0";
    score.max = "100";
    score.step = "0.01";
    score.placeholder = "نمره اختیاری";
    score.value = topic.latest_score == null ? "" : topic.latest_score;
    const save = document.createElement("button");
    save.type = "button";
    save.textContent = "ذخیره وضعیت";
    save.addEventListener("click", async () => {
     save.disabled = true;
     try {
      const latest_score = score.value === "" ? null : Number(score.value);
      await learningApi("/api/learning/paths/" + path.id + "/topics/" + topic.id, {
       method: "PATCH", body: JSON.stringify({status: status.value, latest_score})
      });
      await loadLearningPaths();
     } catch (error) {
      learningEl("paths-output").textContent = error.message;
      save.disabled = false;
     }
    });
    const start = document.createElement("button");
    start.type = "button";
    start.textContent = "شروع درس";
    const output = document.createElement("div");
    output.className = "path-topic-lesson";
    start.addEventListener("click", () => startLesson(path, topic, start, output));
    row.append(label, status, score, save, start, output);
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
