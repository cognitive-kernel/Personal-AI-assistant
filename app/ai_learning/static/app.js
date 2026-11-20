import {CORE_CONTRACT_VERSION,say} from "./core.js?v=20261120-3";
import {COURSES_CONTRACT_VERSION,loadCourses,refreshCourseProgress,setLessonLoader,setRunHistoryLoader} from "./courses.js?v=20261120-3";
import {RUNS_CONTRACT_VERSION,loadRuns} from "./runs.js?v=20261120-3";
import {LESSONS_CONTRACT_VERSION,loadLessons} from "./lessons.js?v=20261120-3";
import {CONTROLS_CONTRACT_VERSION,refreshLearningControl} from "./controls.js?v=20261120-3";
import {SEARCH_CONTRACT_VERSION,initSearch} from "./search.js?v=20261120-3";

const moduleContracts=[CORE_CONTRACT_VERSION,COURSES_CONTRACT_VERSION,RUNS_CONTRACT_VERSION,LESSONS_CONTRACT_VERSION,CONTROLS_CONTRACT_VERSION,SEARCH_CONTRACT_VERSION];
if(moduleContracts.some(version=>version!==1)){
 const message="قرارداد نسخهٔ ماژول‌های رابط یادگیری ناسازگار است.";
 say(message,true);
 throw new Error(message);
}
setLessonLoader(loadLessons);
setRunHistoryLoader(loadRuns);
initSearch();

loadCourses(Number(localStorage.getItem("aiLearningCourseId"))||undefined)
 .then(async()=>{await refreshLearningControl();say("ماژول آماده است. مکث و بازیابی برای دریافت سرفصل و تولید دسته‌های درس فعال است؛ محتوای درس‌ها پیش از انتشار نیازمند بازبینی است.");window.setInterval(()=>{refreshLearningControl().catch(()=>{});refreshCourseProgress().catch(()=>{})},5000)})
 .catch(e=>say(e.message,true));
