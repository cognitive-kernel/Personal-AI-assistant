/* Browser-assisted learning course UI.
 * Owns course creation, course list/detail rendering, and bounded status polling.
 * Shared dependency contract v1: dashboardCore.{api,el}.
 */
(() => {
 "use strict";
 function createLocalDashboardCore() {
 const el = id => document.getElementById(id);
 async function api(path, options = {}) {
  const response = await fetch(path, {
   headers: {"Content-Type": "application/json", ...(options.headers || {})},
   ...options
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || ("HTTP " + response.status));
  return payload;
 }
 function appendText(parent, tag, text, className) {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  parent.append(node);
  return node;
 }
 return Object.freeze({version: 1, el, api, appendText});
}

 const core=window.dashboardCore && window.dashboardCore.version===1 ? window.dashboardCore : createLocalDashboardCore();
 if(!core||core.version!==1||typeof core.api!=="function"||typeof core.el!=="function"){
  const output=document.getElementById("browser-learning-output");
  if(output) output.textContent="ماژول یادگیری مرورگری بارگذاری نشد: قرارداد هستهٔ داشبورد در دسترس نیست.";
  throw new Error("Browser learning module requires dashboardCore contract v1.");
 }
 const {api,el}=core;
 const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
 let coursesLoadSequence=0;
async function refreshBrowserLearningCourses() {
 const requestSequence = ++coursesLoadSequence;
 const container = el("browser-learning-courses");
 if (!container) return;
 container.textContent = "در حال بارگذاری دوره‌های هوش‌به‌هوش…";
 try {
  const result = await api("/api/learning/browser/courses?limit=50");
  if (requestSequence !== coursesLoadSequence) return;
  container.replaceChildren();
  const courses = result.courses || [];
  if (!courses.length) {
   container.textContent = "هنوز دوره‌ای ثبت نشده است.";
   return;
  }
  for (const item of courses) {
   const card = document.createElement("article");
   card.className = "browser-learning-course";
   const heading = document.createElement("h4");
   heading.textContent = "#" + item.id + " — " + (item.title || item.topic);
   const meta = document.createElement("p");
   meta.textContent = "وضعیت: " + item.status + " | موضوع: " + item.topic;
   card.append(heading, meta);
   if (item.error) {
    const error = document.createElement("p");
    error.className = "muted";
    error.textContent = "جزئیات خطا: " + item.error;
    card.append(error);
   }
   const open = document.createElement("button");
   open.type = "button";
   open.textContent = "نمایش سرفصل‌ها و درس‌های ذخیره‌شده";
   open.addEventListener("click", () => loadBrowserLearningCourse(item.id, card));
   card.append(open);
   if (["failed", "interrupted"].includes(item.status)) {
    const retry = document.createElement("button");
    retry.type = "button";
    retry.textContent = "ادامه / تلاش دوباره";
    retry.addEventListener("click", async () => {
     retry.disabled = true;
     try {
      await api("/api/learning/browser/courses/" + item.id + "/retry", {method: "POST", body: "{}"});
      await refreshBrowserLearningCourses();
      pollBrowserLearningCourse(item.id);
     } catch (error) {
      retry.disabled = false;
      el("browser-learning-output").textContent = "ادامهٔ دوره ناموفق بود: " + error.message;
     }
    });
    card.append(retry);
   }
   container.append(card);
  }
 } catch (error) {
  if (requestSequence === coursesLoadSequence) {
   container.textContent = "بارگذاری فهرست دوره‌ها ناموفق بود: " + error.message;
  }
 }
}

async function loadBrowserLearningCourse(courseId, card) {
 try {
  const course = await api("/api/learning/browser/courses/" + courseId);
  let detail = card.querySelector(".browser-learning-detail");
  if (detail) detail.remove();
  detail = document.createElement("div");
  detail.className = "browser-learning-detail";
  const heading = document.createElement("h5");
  heading.textContent = "سرفصل‌های دوره — " + (course.title || course.topic);
  detail.append(heading);
  const summary = document.createElement("p");
  const lessons = course.lessons || [];
  const completed = lessons.filter(lesson => lesson.status === "completed").length;
  summary.textContent = "پیشرفت: " + completed + " از " + lessons.length + " درس ذخیره شده | وضعیت دوره: " + course.status +
   (course.outline_sha256 ? " | SHA-256 سرفصل: " + course.outline_sha256 : "");
  detail.append(summary);
  for (const lesson of lessons) {
   const section = document.createElement("details");
   const title = document.createElement("summary");
   title.textContent = lesson.position + ". " + lesson.topic + " — " + lesson.status;
   section.append(title);
   if (lesson.error) {
    const error = document.createElement("p");
    error.textContent = "خطا: " + lesson.error;
    section.append(error);
   }
   if (lesson.content) {
    const provenance = document.createElement("p");
    provenance.textContent = "اعتماد: " + (lesson.trust_status || "unverified") +
      " | منبع: " + (lesson.source_url || "نامشخص") +
      " | SHA-256 محتوا: " + (lesson.content_sha256 || "ثبت نشده") +
      " | SHA-256 درخواست: " + (lesson.prompt_sha256 || "ثبت نشده");
    const body = document.createElement("pre");
    body.textContent = lesson.content;
    section.append(provenance, body);
   }
   detail.append(section);
  }
  card.append(detail);
 } catch (error) {
  el("browser-learning-output").textContent = "دریافت جزئیات دوره ناموفق بود: " + error.message;
 }
}

async function pollBrowserLearningCourse(courseId) {
 const output = el("browser-learning-output");
 for (let attempt = 0; attempt < 1800; attempt += 1) {
  try {
   const course = await api("/api/learning/browser/courses/" + courseId);
   const lessons = course.lessons || [];
   const completed = lessons.filter(lesson => lesson.status === "completed").length;
   output.textContent = "دوره #" + courseId + " — " + (course.title || course.topic) +
    "\nوضعیت: " + course.status + "\nدرس‌های ذخیره‌شده: " + completed + " از " + lessons.length +
    (course.error ? "\nخطا: " + course.error : "");
   if (!["queued", "running"].includes(course.status)) {
    await refreshBrowserLearningCourses();
    return;
   }
  } catch (error) {
   output.textContent = "وضعیت دوره قابل دریافت نیست: " + error.message;
   return;
  }
  await delay(3000);
 }
 output.textContent = "پایش خودکار پس از ۹۰ دقیقه متوقف شد؛ وضعیت دوره در فهرست ذخیره شده است.";
}

const browserLearningForm = el("browser-learning-form");
if (browserLearningForm) {
 browserLearningForm.addEventListener("submit", async event => {
  event.preventDefault();
  const button = el("browser-learning-start");
  const output = el("browser-learning-output");
  button.disabled = true;
  output.textContent = "در حال ثبت دوره و شروع ارتباط با Chrome…";
  try {
   const course = await api("/api/learning/browser/courses", {
    method: "POST",
    body: JSON.stringify({
     topic: el("browser-learning-topic").value,
     cdp_url: el("browser-learning-cdp").value,
     max_lessons: Number(el("browser-learning-max-lessons").value)
    })
   });
   output.textContent = "دوره #" + course.id + " ثبت شد؛ در حال اتصال به ChatGPT و دریافت سرفصل‌ها…";
   await refreshBrowserLearningCourses();
   pollBrowserLearningCourse(course.id);
  } catch (error) {
   output.textContent = "شروع یادگیری هوش‌به‌هوش ناموفق بود: " + error.message;
  } finally {
   button.disabled = false;
  }
 });
}
const browserLearningRefresh = el("browser-learning-refresh");
if (browserLearningRefresh) browserLearningRefresh.addEventListener("click", refreshBrowserLearningCourses);
refreshBrowserLearningCourses();

})();
