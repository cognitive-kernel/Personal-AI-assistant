const learningEl = id => document.getElementById(id);
async function learningApi(path, options = {}) {
 const response = await fetch(path, {headers: {"Content-Type":"application/json"}, ...options});
 const data = await response.json().catch(() => ({}));
 if (!response.ok) throw new Error(data.detail || ("HTTP " + response.status));
 return data;
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
    const row = document.createElement("div"); row.className = "path-topic";
    const label = document.createElement("label"); label.textContent = topic.position + ". " + topic.topic;
    const status = document.createElement("select");
    [["pending","شروع‌نشده"],["in_progress","در حال یادگیری"],["completed","تکمیل‌شده"]].forEach(([value,title]) => {
     const option = document.createElement("option"); option.value = value; option.textContent = title; status.append(option);
    });
    status.value = topic.status;
    const score = document.createElement("input"); score.type = "number"; score.min = "0"; score.max = "100"; score.step = "0.01";
    score.placeholder = "نمره اختیاری"; score.value = topic.latest_score == null ? "" : topic.latest_score;
    const save = document.createElement("button"); save.type = "button"; save.textContent = "ذخیره وضعیت";
    save.addEventListener("click", async () => {
     save.disabled = true;
     try {
      const latest_score = score.value === "" ? null : Number(score.value);
      await learningApi("/api/learning/paths/" + path.id + "/topics/" + topic.id, {
       method: "PATCH", body: JSON.stringify({status: status.value, latest_score})
      });
      await loadLearningPaths();
     } catch (error) { learningEl("paths-output").textContent = error.message; save.disabled = false; }
    });
    row.append(label, status, score, save); details.append(row);
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
