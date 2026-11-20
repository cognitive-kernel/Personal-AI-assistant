import {api,say,$,act} from "./core.js?v=20261120-3";
import {getCourseId,loadCourses} from "./courses.js?v=20261120-3";
import {loadRuns} from "./runs.js?v=20261120-3";
export const CONTROLS_CONTRACT_VERSION=1;
let learningControlRequestSequence=0;
export async function refreshLearningControl(){
 const requestSequence=++learningControlRequestSequence;
 const state=await api("/control");
 if(requestSequence!==learningControlRequestSequence)return;
 $("learning-control-status").textContent=state.paused
  ?"وضعیت: مکث سراسری فعال است؛ این تنظیم پس از خاموشی نیز حفظ می‌شود."
    +(state.retry_pending?" بازیابی خودکار پس از "+state.retry_after+" انجام می‌شود.":"")
  :state.retry_pending
   ?"وضعیت: به‌دلیل خطای موقت ChatGPT، صف تا "+state.retry_after+" در مکث خودکار است؛ کارهای باقی‌مانده پس از آن ادامه پیدا می‌کنند."
   :"وضعیت: مکث سراسری فعال نیست. پس از راه‌اندازی مجدد نیز می‌توانید بازیابی را بزنید.";
 if(state.worker_active) $("learning-control-status").textContent+=" worker مرورگر فعال است؛ ممکن است در پردازه دیگری اجرا شود.";
 else if(state.worker_recovery_pending) $("learning-control-status").textContent+=" مالک قبلی worker منقضی شده و بازیابی هنوز انجام نشده است.";
 $("pause-learning").disabled=!!state.paused;
 $("resume-learning").disabled=false;
}
$("pause-learning").onclick=()=>act(async()=>{
 await api("/pause",{method:"POST"});
 await refreshLearningControl();
 say("مکث سراسری ثبت شد؛ کار جاری در اولین نقطه امن متوقف می‌شود.");
});
$("resume-learning").onclick=()=>act(async()=>{
 const result=await api("/resume",{method:"POST",body:JSON.stringify({course_id:getCourseId()})});
 await refreshLearningControl();
 await loadRuns();
 say(result.message||"فرایند یادگیری ادامه یافت.");
});
$("reset-learning-data").onclick=()=>act(async()=>{
 if(!confirm("همهٔ سرفصل‌ها، درس‌ها، نسخه‌های محتوا، سوابق اجرا و ردپاهای جست‌وجوی این ماژول پاک می‌شوند. تعریف دوره‌ها باقی می‌ماند. ادامه می‌دهید؟"))return;
 const phrase=prompt("برای تأیید پاک‌سازی کامل، عبارت RESET AI LEARNING DATA را دقیقاً وارد کنید:");
 if(phrase!=="RESET AI LEARNING DATA") { say("پاک‌سازی لغو شد."); return; }
 const result=await api("/reset-learning-data",{method:"POST",body:JSON.stringify({confirmation:phrase})});
 await loadCourses(getCourseId()||undefined);
 $("select-all-topics").checked=false;
 $("select-all-course-lessons").checked=false;
 $("select-all-runs").checked=false;
 say("پاک‌سازی انجام شد. سرفصل‌ها: "+result.deleted.topics+"، درس‌ها: "+result.deleted.lessons+"، نسخه‌ها: "+result.deleted.versions+"، سوابق اجرا: "+result.deleted.runs+" حذف شدند؛ دوره‌ها باقی ماندند و آماده شروع مجدد هستند.");
});
