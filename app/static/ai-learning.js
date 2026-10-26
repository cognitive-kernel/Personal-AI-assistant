"use strict";
const byId = id => document.getElementById(id);
let selectedCourse = null;
let courseTopics = [];

async function api(path, options) {
  const response = await fetch(path, Object.assign({
    headers: {"Content-Type":"application/json"},
    cache: "no-store"
  }, options || {}));
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || ("HTTP " + response.status));
  return body;
}
function jsonOptions(method, body) {
  return {method: method, body: JSON.stringify(body)};
}
function message(text, isError) {
  const el = byId("status");
  el.textContent = text;
  el.className = "status" + (isError ? " error" : "");
}
function node(tag, text, className) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (className) el.className = className;
  return el;
}
async function loadStatus() {
  const s = await api("/api/ai-learning/status");
  if (!s.enabled) {
    message("ماژول غیرفعال است. برای فعال‌سازی، AI_LEARNING_ENABLED=1 را تنظیم و برنامه را مجدداً اجرا کنید.", true);
    return false;
  }
  message("ماژول فعال است؛ بازیابی دانش: " + (s.retrieval_enabled ? "فعال" : "غیرفعال"));
  return true;
}
async function loadCourses(preferredId) {
  const result = await api("/api/ai-learning/courses");
  const select = byId("course-select");
  select.replaceChildren();
  if (!result.items.length) {
    select.append(node("option", "هنوز دوره‌ای ثبت نشده است"));
    select.value = "";
    selectedCourse = null;
    byId("course-details").replaceChildren();
    byId("topics-list").replaceChildren();
    return;
  }
  result.items.forEach(course => {
    const option = node("option", course.title + " (" + course.topic_count + " سرفصل)");
    option.value = String(course.id);
    select.append(option);
  });
  const wanted = preferredId || selectedCourse;
  select.value = String(result.items.some(c => String(c.id) === String(wanted)) ? wanted : result.items[0].id);
  selectedCourse = Number(select.value);
  await loadCourseDetails();
}
async function loadCourseDetails() {
  if (!selectedCourse) return;
  const course = await api("/api/ai-learning/courses/" + selectedCourse);
  courseTopics = course.topics || [];
  const details = byId("course-details");
  details.replaceChildren();
  details.append(node("p", "وضعیت: " + course.status + " · زبان: " + course.language + " · منبع: " + course.provider));
  renderTopics();
}
function renderTopics() {
  const list = byId("topics-list");
  list.replaceChildren();
  if (!courseTopics.length) {
    list.append(node("p", "برای این دوره هنوز سرفصلی وجود ندارد."));
    return;
  }
  courseTopics.forEach((topic, index) => {
    const card = node("article", undefined, "topic-card");
    const top = node("div", undefined, "topic-top");
    const title = node("div");
    title.append(node("strong", index + 1 + ". " + topic.title));
    title.append(node("div", "تصمیم: " + topic.approval_status + " · پردازش: " + topic.processing_status, "topic-status"));
    if (topic.description) title.append(node("p", topic.description));
    top.append(title);
    card.append(top);
    const actions = node("div", undefined, "topic-actions");
    actions.append(action("تأیید", () => decide(topic.id, "approved"), topic.approval_status === "approved"));
    actions.append(action("رد", () => decide(topic.id, "rejected"), topic.approval_status === "rejected"));
    actions.append(action("ویرایش", () => editTopic(topic)));
    actions.append(action("بالا", () => moveTopic(index, -1), index === 0));
    actions.append(action("پایین", () => moveTopic(index, 1), index === courseTopics.length - 1));
    actions.append(action("ایجاد درس", () => createLesson(topic), topic.approval_status !== "approved"));
    actions.append(action("حذف", () => deleteTopic(topic), false, true));
    card.append(actions);
    list.append(card);
  });
}
function action(label, callback, disabled, danger) {
  const button = node("button", label, danger ? "secondary" : "");
  button.type = "button";
  button.disabled = Boolean(disabled);
  if (danger) button.classList.add("error");
  button.addEventListener("click", async () => {
    button.disabled = true;
    try { await callback(); } catch (error) { message(error.message, true); }
    finally { button.disabled = false; }
  });
  return button;
}
async function decide(topicId, decision) {
  await api("/api/ai-learning/topics/decision", jsonOptions("POST", {topic_ids:[topicId],decision:decision}));
  await loadCourseDetails();
  message("تصمیم سرفصل ذخیره شد.");
}
async function editTopic(topic) {
  const title = window.prompt("عنوان سرفصل:", topic.title);
  if (title === null) return;
  const description = window.prompt("توضیحات:", topic.description || "");
  if (description === null) return;
  await api("/api/ai-learning/topics/" + topic.id, jsonOptions("PATCH", {title:title,description:description}));
  await loadCourseDetails();
}
async function moveTopic(index, delta) {
  const reordered = courseTopics.slice();
  const other = index + delta;
  [reordered[index], reordered[other]] = [reordered[other], reordered[index]];
  await api("/api/ai-learning/courses/" + selectedCourse + "/topic-order", jsonOptions("PUT", {topic_ids:reordered.map(t => t.id)}));
  await loadCourseDetails();
}
async function deleteTopic(topic) {
  if (!window.confirm("سرفصل و درس‌های وابسته از فهرست فعال پنهان شوند؟ سابقه برای ردیابی نگهداری می‌شود.")) return;
  await api("/api/ai-learning/topics/" + topic.id, {method:"DELETE"});
  await loadCourseDetails();
}
async function createLesson(topic) {
  const title = window.prompt("عنوان درس:");
  if (!title || !title.trim()) return;
  const lesson = await api("/api/ai-learning/topics/" + topic.id + "/lessons", jsonOptions("POST", {title:title.trim()}));
  const markdown = window.prompt("متن درس را وارد کنید (Markdown قابل قبول است):");
  if (markdown === null || !markdown.trim()) {
    message("درس ایجاد شد، اما هنوز نسخه محتوا ندارد.");
    return;
  }
  const version = await api("/api/ai-learning/lessons/" + lesson.id + "/versions", jsonOptions("POST", {
    markdown:markdown,raw_response:markdown,source_kind:"manual",provider:"manual"
  }));
  if (!window.confirm("نسخه ذخیره شد. پس از بازبینی دستی، آن را برای استفاده دستیار آماده کنیم؟")) {
    message("نسخه در وضعیت نیازمند بازبینی ذخیره شد.");
    return;
  }
  await api("/api/ai-learning/versions/" + version.id + "/review", jsonOptions("POST", {decision:"reviewed"}));
  if (window.confirm("انتشار نسخه برای جست‌وجوی داخلی مجاز است؟")) {
    await api("/api/ai-learning/versions/" + version.id + "/publish", {method:"POST"});
    message("نسخه منتشر شد؛ جست‌وجو همچنان به feature flag جداگانه نیاز دارد.");
  } else {
    message("نسخه بازبینی شد اما منتشر نشد.");
  }
}
byId("course-form").addEventListener("submit", async event => {
  event.preventDefault();
  try {
    const course = await api("/api/ai-learning/courses", jsonOptions("POST", {
      title:byId("course-title").value,provider:byId("course-provider").value,
      language:byId("course-language").value,target_level:"beginner_to_advanced",
      scope_notes:byId("course-notes").value
    }));
    byId("course-form").reset();
    await loadCourses(course.id);
    message("دوره ایجاد شد.");
  } catch (error) { message(error.message, true); }
});
byId("topic-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (!selectedCourse) { message("ابتدا یک دوره انتخاب کنید.", true); return; }
  try {
    await api("/api/ai-learning/courses/" + selectedCourse + "/topics", jsonOptions("POST", {
      title:byId("topic-title").value,description:byId("topic-description").value
    }));
    byId("topic-form").reset();
    await loadCourseDetails();
    message("سرفصل اضافه شد و منتظر تأیید است.");
  } catch (error) { message(error.message, true); }
});
byId("course-select").addEventListener("change", async () => {
  selectedCourse = Number(byId("course-select").value) || null;
  try { await loadCourseDetails(); } catch (error) { message(error.message, true); }
});
byId("reload-courses").addEventListener("click", async () => {
  try { await loadCourses(); message("فهرست به‌روز شد."); } catch (error) { message(error.message, true); }
});
byId("search-form").addEventListener("submit", async event => {
  event.preventDefault();
  const output = byId("search-results");
  output.replaceChildren(node("p", "در حال جست‌وجو…"));
  try {
    const query = byId("search-query").value;
    const limit = byId("search-limit").value;
    const result = await api("/api/ai-learning/search?query=" + encodeURIComponent(query) + "&limit=" + encodeURIComponent(limit));
    output.replaceChildren();
    if (!result.items.length) output.append(node("p", "نتیجه‌ای در دانش منتشرشده پیدا نشد."));
    result.items.forEach(item => {
      const card = node("article", undefined, "result-card");
      card.append(node("strong", item.course_title + " / " + item.topic_title + " / " + item.lesson_title));
      card.append(node("p", "نسخه: " + item.version_no + " · شناسه بخش: " + item.chunk_id + " · شناسه محتوا: " + item.content_version_id));
      card.append(node("p", item.heading_path || "بدون عنوان"));
      card.append(node("p", item.body));
      output.append(card);
    });
  } catch (error) { output.replaceChildren(node("p", error.message, "error")); }
});
(async function init() {
  try {
    const enabled = await loadStatus();
    if (enabled) await loadCourses();
  } catch (error) { message(error.message, true); }
})();
