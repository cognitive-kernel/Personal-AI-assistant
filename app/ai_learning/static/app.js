const MODULE_VERSION="20261120-5";
function importWithTimeout(path,timeoutMs=5000){
 return Promise.race([
  import(path),
  new Promise((_,reject)=>window.setTimeout(()=>reject(new Error("module load timeout: "+path)),timeoutMs)),
 ]);
}
const moduleImports=await Promise.allSettled([
 importWithTimeout("./core.js?v="+MODULE_VERSION),
 importWithTimeout("./courses.js?v="+MODULE_VERSION),
 importWithTimeout("./runs.js?v="+MODULE_VERSION),
 importWithTimeout("./lessons.js?v="+MODULE_VERSION),
 importWithTimeout("./controls.js?v="+MODULE_VERSION),
 importWithTimeout("./search.js?v="+MODULE_VERSION),
]);
const moduleFailures=[];

function createFallbackCore(){
 const API="/api/ai-learning";
 const $=id=>document.getElementById(id);
 async function api(path,options={}){const response=await fetch(API+path,{cache:"no-store",headers:{"Content-Type":"application/json",...(options.headers||{})},...options});const payload=await response.json().catch(()=>({}));if(!response.ok)throw new Error(payload.detail||("HTTP "+response.status));return payload}
 function say(message,bad=false){const status=$("status");if(status){status.textContent=message;status.className=bad?"error":"success"}else if(bad)console.error(message)}
 function el(tag,text,cls){const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(cls)node.className=cls;return node}
 function data(form){return Object.fromEntries(new FormData(form).entries())}
 function progressMeter(percent,label){const value=Math.max(0,Math.min(100,Number(percent)||0));const wrap=el("div",undefined,"progress-wrap"),meta=el("div",undefined,"progress-meta");meta.append(el("span",label||"پیشرفت دریافت محتوا"),el("strong",value+"٪"));const track=el("div",undefined,"progress-track"),bar=el("span",undefined,"progress-bar");bar.style.width=value+"%";track.append(bar);wrap.append(meta,track);return wrap}
 async function act(fn){try{await fn()}catch(error){say(error.message,true)}}
 return Object.freeze({CORE_CONTRACT_VERSION:1,API,$,api,say,el,data,progressMeter,act});
}

function loadFeature(result,label,contractName,factoryName,core,dependencies={}){
 if(result.status!=="fulfilled"){moduleFailures.push(label+" (بارگذاری ناموفق)");return null}
 const module=result.value;
 if(module[contractName]!==1||typeof module[factoryName]!=="function"){moduleFailures.push(label+" (قرارداد ناسازگار)");return null}
 try{return module[factoryName](core,dependencies)}catch(error){moduleFailures.push(label+" (راه‌اندازی ناموفق: "+error.message+")");return null}
}

const coreResult=moduleImports[0];
let core;
try{
 if(coreResult.status!=="fulfilled"||coreResult.value.CORE_CONTRACT_VERSION!==1)throw new Error("core module unavailable");
 core=coreResult.value.createCore();
 if(!core||core.CORE_CONTRACT_VERSION!==1)throw new Error("core contract mismatch");
}catch(error){
 core=createFallbackCore();
 moduleFailures.push("هستهٔ مشترک (نسخهٔ پشتیبان داخلی: "+error.message+")");
}

const courses=loadFeature(moduleImports[1],"دوره‌ها","COURSES_CONTRACT_VERSION","createCoursesModule",core);
const getCourseId=courses?.getCourseId||(()=>Number(core.$("course-select")?.value)||null);
const getTopics=courses?.getTopics||(()=>[]);
const isCurrentCourse=courses?.isCurrentCourse||((id)=>id===getCourseId());
const loadCourse=courses?.loadCourse|| (async()=>{});
const runs=loadFeature(moduleImports[2],"سوابق اجرا","RUNS_CONTRACT_VERSION","createRunsModule",core,{getCourseId,isCurrentCourse,loadCourse});
const lessons=loadFeature(moduleImports[3],"درس‌ها","LESSONS_CONTRACT_VERSION","createLessonsModule",core,{
 getCourseId,getTopics,isCurrentCourse,
 refreshCourseProgress:courses?.refreshCourseProgress|| (async()=>{}),
 returnToTopic:courses?.returnToTopic||(()=>{}),
 loadCourse,loadRuns:runs?.loadRuns|| (async()=>{}),
});
if(courses){
 courses.setRunHistoryLoader(runs?.loadRuns|| (async()=>core.say("ماژول سوابق اجرا در دسترس نیست.",true)));
 courses.setLessonLoader(lessons?.loadLessons|| (async()=>core.say("ماژول درس‌ها در دسترس نیست.",true)));
}
const controls=loadFeature(moduleImports[4],"کنترل سراسری","CONTROLS_CONTRACT_VERSION","createControlsModule",core,{
 getCourseId,loadCourses:courses?.loadCourses|| (async()=>{}),loadRuns:runs?.loadRuns|| (async()=>{}),
});
const search=loadFeature(moduleImports[5],"جست‌وجو","SEARCH_CONTRACT_VERSION","createSearchModule",core);

function disableControls(ids,message){for(const id of ids){const control=core.$(id);if(control){control.disabled=true;control.title=message}}}
function disableForms(ids,message){for(const id of ids){const form=core.$(id);form?.querySelectorAll("input,select,button,textarea").forEach(control=>{control.disabled=true;control.title=message})}}
if(!courses){disableForms(["course-form","outline-form","topic-form"],"ماژول مدیریت دوره‌ها در دسترس نیست.");disableControls(["course-select","reload-course","delete-course","select-all-topics","approve-selected","approve-all-topics","learn-selected-topics","reject-selected","save-order","reset-learning-data"],"ماژول مدیریت دوره‌ها در دسترس نیست.")}
if(!lessons)disableControls(["approve-all-lessons","recover-deleted-lessons","publish-all-lessons","show-all-lessons","select-all-course-lessons","approve-selected-course-lessons"],"ماژول درس‌ها در دسترس نیست.");
if(!runs)disableControls(["stop-active-runs","delete-selected-runs"],"ماژول سوابق اجرا در دسترس نیست.");
if(!controls)disableControls(["pause-learning","resume-learning","reset-learning-data"],"ماژول کنترل سراسری در دسترس نیست.");
if(!search){const button=core.$("search-form")?.querySelector('button[type="submit"]');if(button)button.disabled=true}
if(!runs&&core.$("runs"))core.$("runs").append(core.el("p","ماژول سوابق اجرا در دسترس نیست."));

if(courses){
 courses.loadCourses(Number(localStorage.getItem("aiLearningCourseId"))||undefined).catch(error=>core.say(error.message,true));
}else core.say("ماژول مدیریت دوره‌ها در دسترس نیست؛ سایر بخش‌های بارگذاری‌شده فعال می‌مانند.",true);

if(controls)controls.refreshLearningControl().catch(error=>core.say(error.message,true));
if(moduleFailures.length)core.say("برخی ماژول‌ها در دسترس نیستند: "+moduleFailures.join("، ")+"؛ بخش‌های مستقل فعال باقی مانده‌اند.",true);else core.say("راه‌انداز رابط یادگیری فعال شد؛ وضعیت هر ماژول مستقل مدیریت می‌شود.");
window.setInterval(()=>{
 controls?.refreshLearningControl().catch(()=>{});
 courses?.refreshCourseProgress().catch(()=>{});
},5000);
