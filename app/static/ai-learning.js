"use strict";
const byId = id => document.getElementById(id);
let selectedCourse = null;
let courseTopics = [];
let selectedTopicIds = new Set();
let lessonsTopicId = null;
let selectedLessonIds = new Set();
let displayedLessons = [];

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
  const progress = byId("course-progress");
  progress.replaceChildren();
  const total = courseTopics.length;
  const complete = courseTopics.filter(t => Number(t.progress_percent) >= 100).length;
  const overall = total ? Math.round(courseTopics.reduce((sum,t) => sum + Number(t.progress_percent || 0), 0) / total) : 0;
  const summary = node("div", "پیشرفت کل: " + overall + "% · تکمیل‌شده: " + complete + " از " + total + " سرفصل · باقی‌مانده: " + (total-complete));
  const meter = node("progress"); meter.max = 100; meter.value = overall; meter.setAttribute("aria-label","درصد پیشرفت کل");
  progress.append(summary, meter);
  byId("select-all-topics").checked = total > 0 && courseTopics.every(t => selectedTopicIds.has(Number(t.id)));
  if (!courseTopics.length) {
    list.append(node("p", "برای این دوره هنوز سرفصلی وجود ندارد."));
    return;
  }
  courseTopics.forEach((topic, index) => {
    const card = node("article", undefined, "topic-card");
    const top = node("div", undefined, "topic-top");
    const title = node("div");
    const check = node("input"); check.type = "checkbox"; check.checked = selectedTopicIds.has(Number(topic.id));
    check.setAttribute("aria-label", "انتخاب سرفصل " + topic.title);
    check.addEventListener("change", () => { if (check.checked) selectedTopicIds.add(Number(topic.id)); else selectedTopicIds.delete(Number(topic.id)); });
    const heading = node("div", undefined, "topic-heading"); heading.append(check, node("strong", index + 1 + ". " + topic.title));
    title.append(heading);
    title.append(node("div", "تصمیم: " + topic.approval_status + " · پردازش: " + topic.processing_status + " · پیشرفت: " + (topic.progress_percent || 0) + "% · " + (topic.progress_label || ""), "topic-status"));
    const meter = node("progress"); meter.max = 100; meter.value = Number(topic.progress_percent || 0); meter.setAttribute("aria-label","درصد پیشرفت سرفصل");
    title.append(meter);
    if (topic.description) title.append(node("p", topic.description));
    top.append(title);
    card.append(top);
    const actions = node("div", undefined, "topic-actions");
    actions.append(action("تأیید", () => decide(topic.id, "approved"), topic.approval_status === "approved"));
    actions.append(action("رد", () => decide(topic.id, "rejected"), topic.approval_status === "rejected"));
    actions.append(action("ویرایش", () => editTopic(topic)));
    actions.append(action(topic.processing_status === "paused" ? "ادامه سرفصل" : "مکث سرفصل", () => pauseTopic(topic, topic.processing_status !== "paused")));
    actions.append(action("درس‌ها و محتوا", () => showLessons(topic)));
    actions.append(action("بالا", () => moveTopic(index, -1), index === 0));
    actions.append(action("پایین", () => moveTopic(index, 1), index === courseTopics.length - 1));
    actions.append(action("ایجاد درس دستی", () => createLesson(topic), topic.approval_status !== "approved"));
    actions.append(action("حذف", () => deleteTopic(topic), false, true));
    card.append(actions);
    list.append(card);
  });
}
async function pauseTopic(topic, paused) {
  await api("/api/ai-learning/topics/" + topic.id + "/pause", jsonOptions("POST", {paused}));
  await loadCourseDetails();
  message(paused ? "سرفصل مکث شد؛ محتوای ثبت‌شده حفظ شد." : "سرفصل برای ادامه فعال شد.");
}
async function showLessons(topic) {
  if (lessonsTopicId !== Number(topic.id)) selectedLessonIds.clear();
  lessonsTopicId = Number(topic.id);
  const panel = byId("lessons-panel");
  panel.replaceChildren(node("p", "در حال دریافت درس‌ها…"));
  try {
    const result = await api("/api/ai-learning/topics/" + topic.id + "/lessons");
    panel.replaceChildren(node("h4", topic.title));
    displayedLessons = result.items;
    byId("select-all-lessons").checked = result.items.length > 0 && result.items.every(l => selectedLessonIds.has(Number(l.id)));
    if (!result.items.length) {
      panel.append(node("p", "برای این سرفصل هنوز درس ایجاد نشده است. سرفصل را تأیید کنید و «دریافت درس» را اجرا کنید."));
      return;
    }
    for (const lesson of result.items) {
      const card = node("article", undefined, "lesson-card");
      const lessonHeading = node("div", undefined, "topic-heading");
      const lessonCheck = node("input"); lessonCheck.type = "checkbox"; lessonCheck.checked = selectedLessonIds.has(Number(lesson.id));
      lessonCheck.setAttribute("aria-label", "انتخاب درس " + lesson.title);
      lessonCheck.addEventListener("change", () => { if (lessonCheck.checked) selectedLessonIds.add(Number(lesson.id)); else selectedLessonIds.delete(Number(lesson.id)); });
      lessonHeading.append(lessonCheck, node("strong", lesson.title)); card.append(lessonHeading);
      card.append(node("p", "وضعیت: " + lesson.status + " · پیشرفت: " + lesson.progress_percent + "% · " + lesson.progress_label + " · نسخه‌ها: " + lesson.version_count));
      const meter = node("progress"); meter.max = 100; meter.value = lesson.progress_percent; card.append(meter);
      const controls = node("div", undefined, "topic-actions");
      controls.append(action("ویرایش عنوان", async () => {
        const title = window.prompt("عنوان درس:", lesson.title);
        if (title === null || !title.trim()) return;
        await api("/api/ai-learning/lessons/" + lesson.id, jsonOptions("PATCH", {title:title.trim()}));
        await showLessons(topic);
      }));
      controls.append(action(lesson.status === "paused" ? "ادامه درس" : "مکث درس", async () => {
        await api("/api/ai-learning/lessons/" + lesson.id + "/pause", jsonOptions("POST", {paused:lesson.status !== "paused"}));
        await showLessons(topic);
      }));
      controls.append(action("نمایش/ویرایش متن", async () => editLessonContent(lesson, topic)));
      controls.append(action("حذف از فهرست فعال", async () => {
        if (!window.confirm("درس از فهرست فعال پنهان شود؟ سابقه محتوا حفظ می‌شود.")) return;
        await api("/api/ai-learning/lessons/" + lesson.id, {method:"DELETE"});
        await showLessons(topic);
      }, false, true));
      card.append(controls);
      const versions = await api("/api/ai-learning/lessons/" + lesson.id + "/versions");
      if (!versions.items.length) {
        card.append(node("p", "متن درس ثبت نشده است.", "error"));
      } else {
        for (const version of versions.items) {
          const vcard = node("div", undefined, "version-card");
          vcard.append(node("strong", "نسخه " + version.version_no + " · " + version.review_status + " · " + version.created_at));
          const vbuttons = node("div", undefined, "topic-actions");
          vbuttons.append(action("مشاهده متن", async () => {
            const content = await api("/api/ai-learning/versions/" + version.id);
            const box = node("pre", content.markdown || "(متن خالی است)");
            const raw = node("details"); raw.append(node("summary", "پاسخ خام ChatGPT"), node("pre", content.raw_response || "(پاسخ خام ثبت نشده)"));
            vcard.append(box, raw);
          }));
          vbuttons.append(action("منابع", async () => {
            const sources = await api("/api/ai-learning/versions/" + version.id + "/sources");
            const box = node("div", undefined, "source-list");
            if (!sources.items.length) box.append(node("p", "برای این نسخه پیوند منبعی استخراج نشده است."));
            sources.items.forEach(s => {
              const row = node("p", (s.title || s.url || "منبع") + " · " + (s.validation_status || "unverified") + " · " + (s.url || ""));
              if (s.url) row.append(action("بررسی پیوند", async () => { await api("/api/ai-learning/sources/" + s.id + "/check", {method:"POST"}); await showLessons(topic); }));
              box.append(row);
            });
            vcard.append(box);
          }));
          if (version.review_status === "needs_review") {
            vbuttons.append(action("تأیید بازبینی", async () => {
              await api("/api/ai-learning/versions/" + version.id + "/review", jsonOptions("POST", {decision:"reviewed"}));
              await showLessons(topic);
            }));
            vbuttons.append(action("رد نسخه", async () => {
              await api("/api/ai-learning/versions/" + version.id + "/review", jsonOptions("POST", {decision:"rejected"}));
              await showLessons(topic);
            }, false, true));
          }
          if (version.review_status === "reviewed") {
            vbuttons.append(action("انتشار", async () => {
              if (!window.confirm("این نسخه منتشر شود؟")) return;
              await api("/api/ai-learning/versions/" + version.id + "/publish", {method:"POST"});
              await showLessons(topic);
            }));
          }
          if (version.review_status === "published") {
            vbuttons.append(action("لغو انتشار", async () => {
              if (!window.confirm("انتشار لغو شود؟ محتوا و سابقه نسخه حفظ می‌شود.")) return;
              await api("/api/ai-learning/versions/" + version.id + "/unpublish", {method:"POST"});
              await showLessons(topic);
            }, false, true));
          }
          vcard.append(vbuttons); card.append(vcard);
        }
      }
      panel.append(card);
    }
  } catch (error) { panel.replaceChildren(node("p", error.message, "error")); }
}
async function editLessonContent(lesson, topic) {
  const versions = await api("/api/ai-learning/lessons/" + lesson.id + "/versions");
  let current = "";
  if (versions.items.length) {
    const latest = await api("/api/ai-learning/versions/" + versions.items[0].id);
    current = latest.markdown || "";
  }
  const markdown = window.prompt("متن کامل درس را ویرایش کنید. ذخیره، نسخه جدید می‌سازد و نسخه قبلی را حذف نمی‌کند:", current);
  if (markdown === null) return;
  if (!markdown.trim()) { message("متن خالی ذخیره نشد. برای پاسخ ناقص، نسخه اصلاحی با متن کامل ثبت کنید.", true); return; }
  await api("/api/ai-learning/lessons/" + lesson.id + "/versions", jsonOptions("POST", {
    markdown, raw_response:markdown, source_kind:"manual", provider:"manual"
  }));
  await showLessons(topic);
  message("نسخه اصلاحی ذخیره شد و برای بازبینی در انتظار ماند.");
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
byId("outline-import-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (!selectedCourse) { message("ابتدا یک دوره انتخاب کنید.", true); return; }
  try {
    const result = await api("/api/ai-learning/courses/" + selectedCourse + "/outline/import", jsonOptions("POST", {
      raw_response:byId("outline-raw").value,provider:byId("outline-provider").value
    }));
    byId("outline-raw").value = "";
    await loadCourseDetails();
    byId("outline-import-result").textContent = result.imported + " سرفصل استخراج شد؛ همه در انتظار تأیید هستند. شناسه ورود: " + result.run_id;
    message("پاسخ خام ثبت و سرفصل‌ها استخراج شدند؛ هیچ‌کدام خودکار تأیید نشده‌اند.");
  } catch (error) { byId("outline-import-result").textContent = error.message; message(error.message, true); }
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
  selectedTopicIds.clear(); selectedLessonIds.clear(); lessonsTopicId = null;
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
async function refreshLearningControl() {
  const state = await api("/api/ai-learning/control");
  byId("learning-control-status").textContent = state.paused
    ? "وضعیت: مکث فعال است. این وضعیت پس از خاموشی نیز حفظ می‌شود."
    : "وضعیت: مکث فعال نیست. فرایندهای در حال اجرا می‌توانند ادامه پیدا کنند.";
  byId("pause-learning").disabled = !!state.paused;
  byId("resume-learning").disabled = false;
}
byId("select-all-lessons").addEventListener("change", () => {
  if (byId("select-all-lessons").checked) displayedLessons.forEach(l => selectedLessonIds.add(Number(l.id)));
  else displayedLessons.forEach(l => selectedLessonIds.delete(Number(l.id)));
  const topic = courseTopics.find(t => Number(t.id) === lessonsTopicId);
  if (topic) showLessons(topic);
});
async function bulkReviewLessons(ids) {
  if (!ids.length) { message("درسی انتخاب نشده است.", true); return; }
  let reviewed = 0, skipped = 0;
  for (const lessonId of ids) {
    const versions = await api("/api/ai-learning/lessons/" + lessonId + "/versions");
    if (!versions.items.length) { skipped++; continue; }
    const version = versions.items[0];
    if (version.review_status !== "needs_review") { skipped++; continue; }
    const content = await api("/api/ai-learning/versions/" + version.id);
    const markdown = (content.markdown || "").trim();
    const bodyText = markdown.replace(/^#{1,6}\s+.*$/gm, "").trim();
    if (!markdown || bodyText.length < 20) { skipped++; continue; }
    await api("/api/ai-learning/versions/" + version.id + "/review", jsonOptions("POST", {decision:"reviewed"}));
    reviewed++;
  }
  const topic = courseTopics.find(t => Number(t.id) === lessonsTopicId);
  if (topic) await showLessons(topic);
  message("بازبینی تأیید شد: " + reviewed + " درس؛ رد از تأیید خودکار/بدون متن: " + skipped + ". انتشار خودکار انجام نشد.");
}
byId("approve-all-lessons").addEventListener("click", async () => {
  if (!lessonsTopicId) { message("ابتدا روی «درس‌ها و محتوا» برای یک سرفصل بزنید.", true); return; }
  if (!window.confirm("همهٔ نسخه‌های دارای متن این سرفصل بازبینی و تأیید شوند؟ انتشار خودکار انجام نمی‌شود.")) return;
  try { await bulkReviewLessons(displayedLessons.map(l => Number(l.id))); }
  catch (error) { message(error.message, true); }
});
byId("approve-selected-lessons").addEventListener("click", async () => {
  const ids = [...selectedLessonIds];
  if (!ids.length) { message("ابتدا درس‌ها را انتخاب کنید.", true); return; }
  if (!window.confirm("نسخه‌های دارای متن " + ids.length + " درس انتخاب‌شده بازبینی شوند؟")) return;
  try { await bulkReviewLessons(ids); }
  catch (error) { message(error.message, true); }
});
byId("select-all-topics").addEventListener("change", () => {
  selectedTopicIds = new Set(byId("select-all-topics").checked ? courseTopics.map(t => Number(t.id)) : []);
  renderTopics();
});
byId("approve-all-topics").addEventListener("click", async () => {
  if (!courseTopics.length) return;
  if (!window.confirm("همهٔ " + courseTopics.length + " سرفصل این دوره تأیید شوند؟")) return;
  try {
    for (let i=0; i<courseTopics.length; i+=500) {
      await api("/api/ai-learning/topics/decision", jsonOptions("POST", {
        topic_ids:courseTopics.slice(i,i+500).map(t=>Number(t.id)), decision:"approved"
      }));
    }
    await loadCourseDetails();
    message("همهٔ سرفصل‌های این دوره تأیید شدند.");
  } catch (error) { message(error.message, true); }
});
byId("reject-selected-topics").addEventListener("click", async () => {
  const ids = [...selectedTopicIds];
  if (!ids.length) { message("ابتدا سرفصل‌ها را انتخاب کنید.", true); return; }
  if (!window.confirm(ids.length + " سرفصل انتخاب‌شده رد شوند؟")) return;
  try {
    for (let i=0; i<ids.length; i+=500) await api("/api/ai-learning/topics/decision", jsonOptions("POST", {topic_ids:ids.slice(i,i+500),decision:"rejected"}));
    selectedTopicIds.clear(); await loadCourseDetails(); message("سرفصل‌های انتخاب‌شده رد شدند.");
  } catch (error) { message(error.message, true); }
});
byId("fetch-selected-lessons").addEventListener("click", async () => {
  const ids = [...selectedTopicIds].filter(id => {
    const t = courseTopics.find(item => Number(item.id) === id);
    return t && t.approval_status === "approved" && t.processing_status !== "paused" && Number(t.lesson_content_ready_count || 0) === 0;
  });
  if (!ids.length) { message("سرفصل تأییدشده و آمادهٔ دریافت درس انتخاب نشده است.", true); return; }
  if (ids.length > 100) { message("در هر اجرا حداکثر ۱۰۰ سرفصل انتخاب کنید.", true); return; }
  try {
    // Keep the idempotency key bounded independently of selection size. The topic IDs
    // are already in the request body; embedding them here can exceed the API 100-char limit.
    const requestKey = "ui-selected-lessons-" + selectedCourse + "-" + Date.now();
    const result = await api("/api/ai-learning/browser/lessons", jsonOptions("POST", {
      course_id:selectedCourse, topic_ids:ids, request_key:requestKey, force_retry:false
    }));
    message("دریافت درس برای " + ids.length + " سرفصل شروع شد. شناسه اجرا: " + result.run_id);
    await loadCourseDetails();
  } catch (error) { message(error.message, true); }
});
byId("reset-learning-data").addEventListener("click", async () => {
  const confirmation = window.prompt("این عملیات همهٔ سرفصل‌ها، درس‌ها، نسخه‌ها، منابع و سوابق اجرا را حذف می‌کند؛ نام دوره‌ها باقی می‌ماند. برای تأیید دقیقاً RESET را وارد کنید.");
  if (confirmation !== "RESET") { message("پاک‌سازی لغو شد."); return; }
  try {
    await api("/api/ai-learning/pause", {method:"POST"});
    const result = await api("/api/ai-learning/reset-learning-data", {method:"POST"});
    selectedTopicIds.clear();
    byId("lessons-panel").replaceChildren(node("p","داده‌های یادگیری پاک شدند."));
    await loadCourses(selectedCourse);
    await refreshLearningControl();
    message("پاک‌سازی انجام شد. تعداد حذف‌شده‌ها: " + JSON.stringify(result.deleted));
  } catch (error) { message(error.message, true); }
});
byId("pause-learning").addEventListener("click", async () => {
  try {
    await api("/api/ai-learning/pause", {method:"POST"});
    await refreshLearningControl();
    message("مکث سراسری ثبت شد؛ پاسخ‌گیری و تولید درس در اولین نقطه امن متوقف می‌شود.");
  } catch (error) { message(error.message, true); }
});
byId("resume-learning").addEventListener("click", async () => {
  try {
    const state = await api("/api/ai-learning/resume", jsonOptions("POST", {course_id:selectedCourse}));
    await refreshLearningControl();
    message(state.message || "فرایند یادگیری ادامه یافت.");
  } catch (error) { message(error.message, true); }
});
(async function init() {
  try {
    const enabled = await loadStatus();
    if (enabled) {
      await loadCourses();
      await refreshLearningControl();
      window.setInterval(() => { refreshLearningControl().catch(() => {}); if (selectedCourse) loadCourseDetails().catch(() => {}); }, 5000);
    }
  } catch (error) { message(error.message, true); }
})();
