"use strict";
const API = "http://127.0.0.1:8000/api/ai-learning";
const el = id => document.getElementById(id);
function status(text,error){el("status").textContent=text;el("status").style.color=error?"#c33":"";}
async function api(path){
  const response=await fetch(API+path,{cache:"no-store"});
  const data=await response.json().catch(()=>({}));
  if(!response.ok) throw new Error(data.detail||("Local API returned HTTP "+response.status));
  return data;
}
async function loadTopics(){
  const courseId=el("course").value,wrap=el("topics-wrap"),output=el("topics");
  output.replaceChildren();
  el("select-all-topics").textContent="انتخاب همه سرفصل‌های تأییدشده";
  if(!courseId||el("run-type").value!=="lesson"){wrap.hidden=true;return;}
  wrap.hidden=false;
  const course=await api("/courses/"+courseId);
  const topics=(course.topics||[]).filter(topic=>topic.approval_status==="approved");
  if(!topics.length){output.textContent="این دوره سرفصل تأییدشده ندارد.";return;}
  topics.forEach(topic=>{
    const label=document.createElement("label"),input=document.createElement("input"),text=document.createElement("span");
    input.type="checkbox";input.value=String(topic.id);
    text.textContent=topic.position+". "+topic.title;
    label.append(input,text);output.append(label);
  });
}
async function load(){
  const state=await api("/status");
  if(!state.enabled){status("ماژول محلی خاموش است؛ AI_LEARNING_ENABLED=1 را تنظیم کنید.",true);el("prepare").disabled=true;return;}
  const courses=await api("/courses");
  el("course").replaceChildren();
  courses.items.forEach(course=>{const option=document.createElement("option");option.value=String(course.id);option.textContent=course.title+" (شناسه "+course.id+")";el("course").append(option);});
  if(!courses.items.length){status("ابتدا در برنامه محلی یک دوره ایجاد کنید.",true);el("prepare").disabled=true;return;}
  await loadTopics();
  const active=await chrome.runtime.sendMessage({type:"get-active-run"});
  if(active.run_id) status("اجرای "+active.run_id+" در حال پیگیری است. اگر ارسال خودکار انجام نشد، پاسخ را پس از ارسال دستی ثبت کنید.");
  else if(active.last_status) status("آخرین اجرا "+(active.last_status.run_id||"")+" — "+active.last_status.status+": "+active.last_status.message);
  else status("متصل به ماژول محلی.");
}
el("course").addEventListener("change",()=>loadTopics().catch(e=>status(e.message,true)));
el("run-type").addEventListener("change",()=>loadTopics().catch(e=>status(e.message,true)));
el("select-all-topics").addEventListener("click",()=>{
  const boxes=Array.from(el("topics").querySelectorAll("input[type=checkbox]"));
  if(!boxes.length)return;
  const selectAll=boxes.some(box=>!box.checked);
  boxes.forEach(box=>box.checked=selectAll);
  el("select-all-topics").textContent=selectAll?"لغو انتخاب همه":"انتخاب همه سرفصل‌های تأییدشده";
});
el("prepare").addEventListener("click",async()=>{
  el("prepare").disabled=true;
  try{
    const type=el("run-type").value;
    const topicIds=Array.from(el("topics").querySelectorAll("input:checked")).map(input=>Number(input.value));
    if(type==="lesson"&&!topicIds.length)throw new Error("حداقل یک سرفصل تأییدشده انتخاب کنید.");
    const payload={course_id:Number(el("course").value),run_type:type,topic_ids:topicIds,selection_mode:topicIds.length>1?"selected":"single",request_key:"chrome-"+crypto.randomUUID()};
    const response=await chrome.runtime.sendMessage({type:"prepare-run",payload});
    if(!response||!response.ok)throw new Error(response&&response.error?response.error:"Browser bridge failed.");
    status(response.result.submitted?"درخواست ارسال شد؛ پاسخ پس از کامل‌شدن به‌صورت خودکار ثبت می‌شود.":"درخواست در ChatGPT درج شد اما ارسال خودکار انجام نشد؛ آن را دستی ارسال کنید.");
  }catch(error){status(error.message,true);}
  finally{el("prepare").disabled=false;}
});
el("capture").addEventListener("click",async()=>{
  el("capture").disabled=true;
  try{
    const response=await chrome.runtime.sendMessage({type:"capture-run"});
    if(!response||!response.ok)throw new Error(response&&response.error?response.error:"Capture failed.");
    const result=response.result;
    status("پاسخ ثبت شد. وضعیت اجرا: "+result.status+(result.error_message?" — "+result.error_message:""));
  }catch(error){status(error.message,true);}
  finally{el("capture").disabled=false;}
});
load().catch(error=>status(error.message,true));
