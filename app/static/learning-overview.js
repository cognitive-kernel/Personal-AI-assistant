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
 const fallback=document.getElementById("due-reviews-output");
 if(!core||core.version!==1){
  if(fallback)fallback.textContent="ماژول مرور و پیشنهاد یادگیری به learningCore v1 نیاز دارد.";
  return;
 }
 const {el:learningEl,api:learningApi,delay:learningDelay}=core;
 let dueLearningReviewsRequestSequence = 0;
 async function refreshDueLearningReviews() {
  const requestSequence = ++dueLearningReviewsRequestSequence;
  const output = learningEl("due-reviews-output");
  if (!output) return;
  output.textContent = "در حال بارگذاری مرورهای سررسیدشده…";
  try {
   const result = await learningApi("/api/learning/reviews/due?limit=100");
   if (requestSequence !== dueLearningReviewsRequestSequence) return;
   const items = result.items || [];
   output.textContent = items.length
    ? items.map(item => item.path_title + " — " + item.topic +
       "\nسررسید: " + item.due_at + "؛ فاصله فعلی: " + item.interval_days + " روز").join("\n\n")
    : "در حال حاضر مرور سررسیدشده‌ای وجود ندارد.";
  } catch (error) {
   if (requestSequence === dueLearningReviewsRequestSequence) {
    output.textContent = "دریافت مرورهای سررسیدشده ناموفق بود: " + error.message;
   }
  }
 }
 
 
 let learningRecommendationsRequestSequence = 0;
 async function refreshLearningRecommendations() {
  const requestSequence = ++learningRecommendationsRequestSequence;
  const output = learningEl("learning-recommendations-output");
  if (!output) return;
  output.textContent = "در حال محاسبهٔ گام بعدی یادگیری…";
  try {
   const result = await learningApi("/api/learning/recommendations?limit=20");
   if (requestSequence !== learningRecommendationsRequestSequence) return;
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
   if (requestSequence === learningRecommendationsRequestSequence) {
    output.textContent = "توصیه‌های یادگیری در دسترس نیست: " + error.message;
   }
  }
 }
 
 
 
 let learningCapabilitiesRequestSequence = 0;
async function refreshLearningCapabilities() {
  const requestSequence = ++learningCapabilitiesRequestSequence;
  const output = learningEl("capability-map-output");
  if (!output) return;
  output.textContent = "در حال ساخت نقشه از ارزیابی‌های ذخیره‌شده…";
  try {
   const result = await learningApi("/api/learning/capabilities?limit=200");
   if (requestSequence !== learningCapabilitiesRequestSequence) return;
   const items = result.capabilities || [];
   const labels = {
    not_assessed: "ارزیابی نشده",
    invalid_evidence: "شواهد نامعتبر",
    needs_reinforcement: "نیازمند تقویت",
    provisional: "شواهد اولیه",
    repeated_provisional: "شواهد اولیهٔ تکرارشده",
    practical_provisional: "شواهد عملی تکرارشده (موقت)",
    transfer_provisional: "انتقال عملی از دانش تأییدشده (موقت)",
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
       "\nحالات انتقال عملی: " + (item.transfer_case_count || 0) +
       "؛ انتقال تکرارپذیر: " + (item.transfer_repeatable ? "بله (موقت)" : "خیر/شواهد ناکافی") +
       "\nروش تمایز سناریوها: " + (item.transfer_novelty_method === "token_set_jaccard"
         ? "شباهت واژگانی؛ آستانه " + Math.round((item.transfer_novelty_threshold || 0.8) * 100) + "%"
         : "نامشخص") +
       "\nمصرف مدل: " + item.prompt_tokens + " ورودی / " + item.completion_tokens + " خروجی توکن" +
       "؛ زمان استنتاج: " + item.inference_duration_ms + " میلی‌ثانیه" +
       "؛ امتیاز نظری به‌ازای ۱۰۰۰ توکن خروجی: " + (item.score_points_per_1000_output_tokens == null ? "ناموجود" : item.score_points_per_1000_output_tokens) +
       "\nهزینهٔ تخمینی: " + (item.estimated_cost_microusd == null ? "نامشخص؛ نرخ هزینه تنظیم نشده" : item.estimated_cost_microusd + " میکرو‌دلار") +
       "؛ وضعیت هزینه: " + (item.cost_status || "نامشخص") +
       "\nشاخص ارزش: " + (item.learning_value_cost && item.learning_value_cost.provisional_value_points != null ? item.learning_value_cost.provisional_value_points : "ناموجود") +
       "؛ هزینه به‌ازای هر واحد ارزش: " + (item.learning_value_cost && item.learning_value_cost.estimated_cost_microusd_per_value_point != null ? item.learning_value_cost.estimated_cost_microusd_per_value_point : "ناموجود؛ هزینه یا خط مبنای کافی نیست")
      ).join("\n\n") +
      "\n\nشواهد عملی فقط وقتی تکرارپذیر شمرده می‌شود که دست‌کم دو برنامهٔ آزمون متفاوت با منبع زندهٔ مستند وجود داشته باشد؛ این هم به‌تنهایی انتقال مهارت عملی را اثبات نمی‌کند."
    : "هنوز سرفصلی برای نمایش در نقشهٔ قابلیت‌ها وجود ندارد.";
  } catch (error) {
   if (requestSequence === learningCapabilitiesRequestSequence) {
    output.textContent = "ساخت نقشهٔ قابلیت‌ها ناموفق بود: " + error.message;
   }
  }
 }
 
 
 window.learningOverview=Object.freeze({
  version:1,
  refreshDueReviews:refreshDueLearningReviews,
  refreshRecommendations:refreshLearningRecommendations,
  refreshCapabilities:refreshLearningCapabilities
 });
 document.addEventListener("DOMContentLoaded",()=>{
  const due=learningEl("refresh-due-reviews");
  const recommendations=learningEl("refresh-learning-recommendations");
  const capabilities=learningEl("refresh-capability-map");
  if(due)due.addEventListener("click",refreshDueLearningReviews);
  if(recommendations)recommendations.addEventListener("click",refreshLearningRecommendations);
  if(capabilities)capabilities.addEventListener("click",refreshLearningCapabilities);
  refreshDueLearningReviews();
  refreshLearningRecommendations();
 });
})();