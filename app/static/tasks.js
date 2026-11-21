(() => {
 "use strict";
 function createLocalDashboardCore() {
  const el=id=>document.getElementById(id);
  async function api(path,options={}) {
   const response=await fetch(path,{headers:{"Content-Type":"application/json",...(options.headers||{})},...options});
   const payload=await response.json().catch(()=>({}));
   if(!response.ok)throw new Error(payload.detail||("HTTP "+response.status));
   return payload;
  }
  function appendText(parent,tag,text,className) {
   const node=document.createElement(tag);
   node.textContent=text;
   if(className)node.className=className;
   parent.append(node);
   return node;
  }
  return Object.freeze({version:1,el,api,appendText});
 }
 const core=window.dashboardCore&&window.dashboardCore.version===1?window.dashboardCore:createLocalDashboardCore();
 const {el,api,appendText}=core;
 const output=el("tasks-output");
 const required=["tasks-container","tasks-output","refresh-tasks","task-status-filter","task-search","task-select-all","bulk-pause-tasks","bulk-resume-tasks","bulk-cancel-tasks","load-more-tasks"];
 const missing=required.filter(id=>!el(id));
 if(missing.length){if(output)output.textContent="ماژول وظایف به کنترل‌های لازم دسترسی ندارد: "+missing.join(", ");return;}
 const viewFactory=window.taskViewFactory;
 const cardsFactory=window.taskCardsFactory;
 if(!viewFactory||viewFactory.version!==1||typeof viewFactory.createTaskView!=="function"||
    !cardsFactory||cardsFactory.version!==1||typeof cardsFactory.createTaskCards!=="function"){
  output.textContent="زیرماژول نمایش یا کارت وظایف بارگذاری نشد؛ سایر بخش‌های داشبورد فعال هستند.";
  return;
 }
 let taskPollScheduled=false,taskNextCursor=null,taskRefreshSequence=0;
 const resetCursor=()=>{taskNextCursor=null;};
 async function refreshTasks({append=false}={}) {
  const requestSequence=++taskRefreshSequence;
  const loadMore=el("load-more-tasks");
  const statusFilter=el("task-status-filter").value;
  try {
   const query=new URLSearchParams({limit:"50"});
   if(statusFilter)query.set("statuses",statusFilter);
   if(append&&taskNextCursor)query.set("cursor",taskNextCursor);
   const result=await api("/api/tasks?"+query.toString());
   if(requestSequence!==taskRefreshSequence||el("task-status-filter").value!==statusFilter)return;
   const tasks=result.tasks||[];
   cards.renderTasks(tasks,{append});
   taskNextCursor=result.next_cursor||null;
   loadMore.hidden=!taskNextCursor;
   output.textContent="وظایف نمایش‌داده‌شده: "+el("tasks-container").querySelectorAll("article").length+(result.has_more?" — موارد بیشتری موجود است.":"");
   const active=tasks.some(task=>["queued","running","pause_requested","cancel_requested"].includes(task.status));
   if(active&&!taskPollScheduled){
    taskPollScheduled=true;
    setTimeout(()=>{taskPollScheduled=false;refreshTasks();},2000);
   }
  }catch(error){
   if(requestSequence===taskRefreshSequence&&el("task-status-filter").value===statusFilter)
    output.textContent="فهرست وظایف دریافت نشد: "+error.message;
  }
 }
 const view=viewFactory.createTaskView({el,refreshTasks,resetCursor});
 const cards=cardsFactory.createTaskCards({
  el,api,appendText,refreshTasks,resetCursor,
  updateTaskDashboard:view.updateTaskDashboard,
  updateTaskBulkControls:view.updateTaskBulkControls
 });
 el("refresh-tasks").addEventListener("click",()=>{resetCursor();refreshTasks();});
 el("task-status-filter").addEventListener("change",()=>{resetCursor();refreshTasks();});
 el("task-search").addEventListener("input",view.updateTaskDashboard);
 el("task-select-all").addEventListener("change",()=>{
  const checked=el("task-select-all").checked;
  el("tasks-container").querySelectorAll(".task-select").forEach(input=>{input.checked=checked;});
  view.updateTaskDashboard();
 });
 el("bulk-pause-tasks").addEventListener("click",()=>cards.runBulkTaskAction("pause"));
 el("bulk-resume-tasks").addEventListener("click",()=>cards.runBulkTaskAction("resume"));
 el("bulk-cancel-tasks").addEventListener("click",()=>cards.runBulkTaskAction("cancel"));
 el("load-more-tasks").addEventListener("click",()=>refreshTasks({append:true}));
 refreshTasks();
})();
