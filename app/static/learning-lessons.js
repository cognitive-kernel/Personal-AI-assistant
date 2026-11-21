(() => {
 "use strict";
 function createLocalLearningCore() {
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
 const delay = ms => new Promise(resolve => window.setTimeout(resolve, ms));
 async function waitForLearningJob(jobId, output) {
  for (let attempt = 0; attempt < 600; attempt += 1) {
   const job = await api("/api/learning/jobs/" + jobId);
   if (["completed", "rejected", "failed", "cancelled"].includes(job.status)) return job;
   if (output) output.textContent = "درس در حال تولید است… وضعیت: " + job.status;
   await delay(1500);
  }
  throw new Error("نتیجه نهایی درس در مهلت انتظار دریافت نشد؛ شناسه کار: " + jobId);
 }
 return Object.freeze({
  version: 1, el, api, delay, waitForLearningJob,
  learningStatusLabels: Object.freeze({
   queued:"در صف",running:"در حال اجرا",completed:"درس تولید شد",
   rejected:"ردشده در کنترل کیفیت",failed:"ناموفق",cancelled:"لغوشده"
  }),
  taskStatusLabels: Object.freeze({
   queued:"در صف",running:"در حال اجرا",pause_requested:"درخواست توقف موقت",
   paused:"متوقف موقت",cancel_requested:"درخواست لغو",succeeded:"اجرای وظیفه تمام شد",
   failed:"اجرای وظیفه ناموفق",cancelled:"وظیفه لغو شد"
  })
 });
}

 const core=window.learningCore && window.learningCore.version===1 ? window.learningCore : createLocalLearningCore();
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
 
 let renderLessonResult=(output)=>{output.textContent="نمایش نتیجهٔ درس در دسترس نیست؛ اجرای یادگیری ثبت شده است.";};
 const rendererFactory=window.learningLessonRendererFactory;
 const lessonRenderer=rendererFactory&&rendererFactory.version===1
  ? rendererFactory.createLearningLessonRenderer({
    learningApi,learningEl,learningDelay,waitForLearningJob,learningStatusLabels,taskStatusLabels,
    regenerateLesson:(path,topic,button,output)=>regenerateLesson(path,topic,button,output)
   })
  : null;
 if(lessonRenderer)renderLessonResult=lessonRenderer.renderLessonResult;
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