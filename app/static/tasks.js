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
 const fallback=document.getElementById("tasks-output");
 if(!core||core.version!==1){
  if(fallback)fallback.textContent="ماژول وظایف بارگذاری نشد: قرارداد dashboardCore v1 در دسترس نیست.";
  return;
 }
 const {el,api,appendText}=core;
 const required=["tasks-container","tasks-output","refresh-tasks","task-status-filter","task-search","task-select-all","bulk-pause-tasks","bulk-resume-tasks","bulk-cancel-tasks","load-more-tasks"];
 const missing=required.filter(id=>!el(id));
 if(missing.length){
  if(fallback)fallback.textContent="ماژول وظایف به کنترل‌های لازم دسترسی ندارد: "+missing.join(", ");
  return;
 }
 let taskPollScheduled=false;
 function updateTaskDashboard() {
  const container=el("tasks-container");
  const cards=Array.from(container.querySelectorAll("article[data-task-status]"));
  const query=(el("task-search")?.value||"").trim().toLocaleLowerCase();
  const counts={queued:0,running:0,paused:0,failed:0,succeeded:0,cancelled:0};
  let visible=0;
  cards.forEach(card=>{
   const status=card.dataset.taskStatus||"";
   if(Object.hasOwn(counts,status)) counts[status]+=1;
   const haystack=(card.dataset.taskSearch||card.textContent).toLocaleLowerCase();
   const matches=!query||haystack.includes(query);
   card.hidden=!matches;
   if(matches) visible+=1;
  });
  const summary=el("task-summary");
  summary.replaceChildren();
  const items=[
   ["بارگذاری‌شده",cards.length],
   ["نمایش مطابق جست‌وجو",visible],
   ["در صف",counts.queued],
   ["در حال اجرا",counts.running],
   ["ناموفق",counts.failed],
   ["موفق",counts.succeeded]
  ];
  items.forEach(([label,value])=>{
   const item=document.createElement("span");
   item.className="task-summary-item";
   const strong=document.createElement("strong"); strong.textContent=String(value);
   const text=document.createElement("span"); text.textContent=label;
   item.append(strong,text); summary.append(item);
  });
 
  const statusFilter=el("task-status-filter")?.value||"";
  let emptyState=container.querySelector(".task-empty-state");
  if(!emptyState) {
   emptyState=document.createElement("div");
   emptyState.className="task-empty-state";
   emptyState.setAttribute("role","status");
   container.append(emptyState);
  }
  if(visible===0) {
   emptyState.hidden=false;
   emptyState.replaceChildren();
   const hasFilters=Boolean(query||statusFilter);
   const heading=document.createElement("h3");
   heading.textContent=cards.length===0&&!hasFilters
    ? "هنوز وظیفه‌ای ثبت نشده است."
    : "هیچ وظیفه‌ای با فیلترهای فعلی پیدا نشد.";
   const description=document.createElement("p");
   description.textContent=cards.length===0&&!hasFilters
    ? "پس از ثبت یک درخواست یا شروع یک فرایند، وضعیت آن در این بخش نمایش داده می‌شود."
    : "عبارت جست‌وجو یا فیلتر وضعیت را تغییر دهید، یا فیلترها را پاک کنید.";
   emptyState.append(heading,description);
   if(hasFilters) {
    const clearFilters=document.createElement("button");
    clearFilters.type="button";
    clearFilters.className="task-empty-clear";
    clearFilters.textContent="پاک کردن فیلترها";
    clearFilters.addEventListener("click",()=>{
     el("task-search").value="";
     el("task-status-filter").value="";
     taskNextCursor=null;
     refreshTasks();
    });
    emptyState.append(clearFilters);
   }
  } else {
   emptyState.hidden=true;
  }
  updateTaskBulkControls();
 }
 function updateTaskBulkControls() {
  const cards=Array.from(el("tasks-container").querySelectorAll("article[data-task-status]"));
  const selected=cards.filter(card=>card.querySelector(".task-select")?.checked);
  const count=selected.length;
  el("task-selected-count").textContent="انتخاب‌شده: "+new Intl.NumberFormat("fa-IR").format(count);
  const eligible={
   pause:selected.filter(card=>["queued","running"].includes(card.dataset.taskStatus)).length,
   resume:selected.filter(card=>card.dataset.taskStatus==="paused").length,
   cancel:selected.filter(card=>["queued","running","pause_requested","paused"].includes(card.dataset.taskStatus)).length
  };
  el("bulk-pause-tasks").disabled=eligible.pause===0;
  el("bulk-resume-tasks").disabled=eligible.resume===0;
  el("bulk-cancel-tasks").disabled=eligible.cancel===0;
  const selectAll=el("task-select-all");
  selectAll.checked=cards.length>0&&selected.length===cards.length;
  selectAll.indeterminate=selected.length>0&&selected.length<cards.length;
 }
 async function runBulkTaskAction(action) {
  const cards=Array.from(el("tasks-container").querySelectorAll("article[data-task-status]"));
  const selected=cards.filter(card=>card.querySelector(".task-select")?.checked);
  const allowed={
   pause:new Set(["queued","running"]),
   resume:new Set(["paused"]),
   cancel:new Set(["queued","running","pause_requested","paused"])
  };
  const eligible=selected.filter(card=>allowed[action].has(card.dataset.taskStatus));
  if(!eligible.length) {
   el("tasks-output").textContent="برای این عملیات، وظیفهٔ انتخاب‌شدهٔ واجد شرایط وجود ندارد.";
   return;
  }
  if(action==="cancel"&&!window.confirm("لغو "+eligible.length+" وظیفهٔ واجد شرایط انجام شود؟ این عملیات ممکن است قابل بازگشت نباشد.")) return;
  const buttons=["bulk-pause-tasks","bulk-resume-tasks","bulk-cancel-tasks"].map(id=>el(id));
  buttons.forEach(button=>button.disabled=true);
  let succeeded=0;
  const failures=[];
  for(const card of eligible) {
   const taskId=card.dataset.taskId;
   try {
    await api("/api/tasks/"+encodeURIComponent(taskId)+"/"+action,{method:"POST"});
    succeeded++;
   } catch(error) {
    failures.push("#"+taskId+": "+error.message);
   }
  }
  taskNextCursor=null;
  await refreshTasks();
  const actionLabel={pause:"توقف",resume:"ادامه",cancel:"لغو"}[action];
  el("tasks-output").textContent=actionLabel+" گروهی پایان یافت؛ موفق: "+succeeded+"، ناموفق: "+failures.length+
   (failures.length?" — "+failures.slice(0,3).join("؛ "):"");
  updateTaskBulkControls();
 }
 function renderTasks(tasks,{append=false}={}) {
  const container=el("tasks-container");
  if(!append) container.replaceChildren();
  if(!tasks.length && !append) {
   updateTaskDashboard();
   return;
  }
  if(!tasks.length) { updateTaskDashboard(); return; }
  tasks.forEach(task=>{
   const card=document.createElement("article"); card.className="cloud-candidate task-card";
   card.dataset.taskId=String(task.id);
   card.dataset.taskStatus=String(task.status||"");
   const taskStatusLabels={
    queued:"در صف",running:"در حال اجرا",pause_requested:"درخواست توقف",paused:"متوقف",
    cancel_requested:"درخواست لغو",cancelled:"لغوشده",succeeded:"موفق",failed:"ناموفق"
   };
   const taskStatusLabel=taskStatusLabels[task.status]||"نامشخص";
   card.dataset.taskSearch=("#"+task.id+" "+task.task_type+" "+task.status+" "+taskStatusLabel).toLocaleLowerCase();
   const selectLabel=document.createElement("label"); selectLabel.className="task-select-label";
   const selectInput=document.createElement("input"); selectInput.type="checkbox"; selectInput.className="task-select";
   selectInput.setAttribute("aria-label","انتخاب وظیفه "+task.id);
   selectInput.addEventListener("change",updateTaskDashboard);
   selectLabel.append(selectInput,document.createTextNode(" انتخاب")); card.append(selectLabel);
   appendText(card,"h3","#"+task.id+" — "+task.task_type);
   const statusLine=appendText(card,"p","وضعیت: "+taskStatusLabel+" | اولویت: "+task.priority+" | تلاش: "+task.attempts+"/"+task.max_attempts,"cloud-status task-status-line");
   statusLine.setAttribute("aria-label","وضعیت وظیفه "+task.id+": "+taskStatusLabel);
   statusLine.title="وضعیت داخلی: "+String(task.status||"unknown");
   const taskStatusClass={
    queued:"is-queued",running:"is-running",pause_requested:"is-pause-requested",paused:"is-paused",
    cancel_requested:"is-cancel-requested",cancelled:"is-cancelled",succeeded:"is-succeeded",failed:"is-failed"
   }[task.status];
   if(taskStatusClass) statusLine.classList.add(taskStatusClass);
   statusLine.dataset.status=String(task.status||"");
   if(task.created_at) appendText(card,"p","ایجاد: "+task.created_at,"muted");
   if(task.error || task.result) {
    const diagnostics=document.createElement("details");
    diagnostics.className="task-diagnostics";
    const summary=document.createElement("summary");
    summary.textContent=task.error ? (task.result ? "جزئیات خطا و نتیجه" : "مشاهدهٔ خطای وظیفه") : "مشاهدهٔ نتیجهٔ وظیفه";
    diagnostics.append(summary);
    if(task.error) {
     const errorHeading=document.createElement("h4");
     errorHeading.textContent="خطا";
     const errorText=document.createElement("p");
     errorText.className="task-error-detail";
     errorText.textContent=String(task.error).slice(0,3000);
     diagnostics.append(errorHeading,errorText);
    }
    if(task.result) {
     const resultHeading=document.createElement("h4");
     resultHeading.textContent="نتیجه";
     const result=document.createElement("pre");
     result.className="task-result-detail";
     try {
      result.textContent=JSON.stringify(task.result,null,2).slice(0,3000);
     } catch {
      result.textContent="نتیجه قابل نمایش نیست.";
     }
     diagnostics.append(resultHeading,result);
    }
    card.append(diagnostics);
   }
   const actions=document.createElement("div"); actions.className="cloud-actions";
   if(["queued","paused"].includes(task.status)) {
    const editControls=document.createElement("div"); editControls.className="task-edit-controls";
    const priorityLabel=document.createElement("label");
    priorityLabel.textContent="اولویت";
    const priorityInput=document.createElement("input"); priorityInput.type="number"; priorityInput.min="-100000"; priorityInput.max="100000"; priorityInput.step="1"; priorityInput.value=String(task.priority??0);
    priorityInput.setAttribute("aria-label","اولویت وظیفه "+task.id);
    const prioritySave=document.createElement("button"); prioritySave.type="button"; prioritySave.textContent="ذخیره اولویت"; prioritySave.setAttribute("aria-label","ذخیره اولویت وظیفه "+task.id);
    prioritySave.addEventListener("click",async()=>{
     const value=Number(priorityInput.value);
     if(!Number.isInteger(value)||value < -100000||value > 100000) {
      el("tasks-output").textContent="اولویت باید عدد صحیح بین ۱۰۰۰۰۰- و ۱۰۰۰۰۰ باشد."; priorityInput.focus(); return;
     }
     prioritySave.disabled=true;
     try {
      await api("/api/tasks/"+task.id+"/priority",{method:"PATCH",body:JSON.stringify({priority:value})});
      await refreshTasks();
      el("tasks-output").textContent="اولویت وظیفه #"+task.id+" ذخیره شد.";
     } catch(error) { el("tasks-output").textContent="ذخیره اولویت ناموفق بود: "+error.message; }
     finally { prioritySave.disabled=false; }
    });
    priorityLabel.append(priorityInput); editControls.append(priorityLabel,prioritySave);
 
    const scheduleLabel=document.createElement("label");
    scheduleLabel.textContent="زمان اجرای برنامه‌ریزی‌شده";
    const scheduleInput=document.createElement("input"); scheduleInput.type="datetime-local";
    scheduleInput.setAttribute("aria-label","زمان اجرای وظیفه "+task.id);
    const currentSchedule=task.scheduled_at?new Date(task.scheduled_at):null;
    const dateForInput=currentSchedule&&!Number.isNaN(currentSchedule.getTime())?currentSchedule:new Date(Date.now()+5*60*1000);
    const localOffset=dateForInput.getTimezoneOffset()*60000;
    scheduleInput.value=new Date(dateForInput.getTime()-localOffset).toISOString().slice(0,16);
    const nowOffset=new Date().getTimezoneOffset()*60000;
    scheduleInput.min=new Date(Date.now()-nowOffset).toISOString().slice(0,16);
    const scheduleSave=document.createElement("button"); scheduleSave.type="button"; scheduleSave.textContent="ذخیره زمان"; scheduleSave.setAttribute("aria-label","ذخیره زمان اجرای وظیفه "+task.id);
    scheduleSave.addEventListener("click",async()=>{
     if(!scheduleInput.value) { el("tasks-output").textContent="زمان اجرای وظیفه را مشخص کنید."; scheduleInput.focus(); return; }
     const scheduledAt=new Date(scheduleInput.value);
     if(Number.isNaN(scheduledAt.getTime())) { el("tasks-output").textContent="زمان واردشده معتبر نیست."; scheduleInput.focus(); return; }
     if(scheduledAt.getTime()<=Date.now()) { el("tasks-output").textContent="زمان اجرا باید در آینده باشد."; scheduleInput.focus(); return; }
     scheduleSave.disabled=true;
     try {
      await api("/api/tasks/"+task.id+"/schedule",{method:"PATCH",body:JSON.stringify({scheduled_at:scheduledAt.toISOString()})});
      await refreshTasks();
      el("tasks-output").textContent="زمان اجرای وظیفه #"+task.id+" ذخیره شد.";
     } catch(error) { el("tasks-output").textContent="ذخیره زمان‌بندی ناموفق بود: "+error.message; }
     finally { scheduleSave.disabled=false; }
    });
    scheduleLabel.append(scheduleInput); editControls.append(scheduleLabel,scheduleSave);
    card.append(editControls);
   }
   const historyButton=document.createElement("button"); historyButton.type="button"; historyButton.textContent="تاریخچه رویدادها";
   historyButton.setAttribute("aria-label","نمایش تاریخچه رویدادهای وظیفه "+task.id);
   let historyEvents=[];
   let historyCursor=null;
   let historyOutput=null;
   let historyLoadMore=null;
   async function loadTaskHistory({append=false}={}) {
    historyButton.disabled=true;
    if(historyLoadMore) historyLoadMore.disabled=true;
    try {
     const query=new URLSearchParams({limit:"20"});
     if(append&&historyCursor) query.set("before_id",String(historyCursor));
     const history=await api("/api/tasks/"+task.id+"/events?"+query.toString());
     const page=Array.isArray(history.events)?history.events:[];
     historyEvents=append?historyEvents.concat(page):page;
     historyCursor=history.next_before_id||null;
     if(!historyOutput) {
      historyOutput=document.createElement("pre");
      historyOutput.className="task-events-output";
      historyOutput.setAttribute("aria-label","تاریخچه رویدادهای وظیفه "+task.id);
      card.append(historyOutput);
     }
     historyOutput.textContent=JSON.stringify(historyEvents,null,2);
     if(history.has_more) {
      if(!historyLoadMore) {
       historyLoadMore=document.createElement("button");
       historyLoadMore.type="button";
       historyLoadMore.textContent="رویدادهای قدیمی‌تر";
       historyLoadMore.setAttribute("aria-label","بارگذاری رویدادهای قدیمی‌تر وظیفه "+task.id);
       historyLoadMore.addEventListener("click",async()=>{
        try { await loadTaskHistory({append:true}); }
        catch(error) { if(historyOutput) historyOutput.textContent="تاریخچه قابل دریافت نیست: "+error.message; }
       });
      }
      if(!historyLoadMore.isConnected) card.append(historyLoadMore);
     } else if(historyLoadMore) {
      historyLoadMore.remove();
     }
    } finally {
     historyButton.disabled=false;
     if(historyLoadMore) historyLoadMore.disabled=false;
    }
   }
   historyButton.addEventListener("click",async()=>{
    try { await loadTaskHistory(); }
    catch(error) {
     if(!historyOutput) {
      historyOutput=document.createElement("pre");
      historyOutput.className="task-events-output";
      card.append(historyOutput);
     }
     historyOutput.textContent="تاریخچه قابل دریافت نیست: "+error.message;
    }
   });
   actions.append(historyButton);
   const addAction=(label,path)=>{
    const button=document.createElement("button"); button.type="button"; button.textContent=label;
    button.addEventListener("click",async()=>{
     button.disabled=true;
     try { await api(path,{method:"POST"}); await refreshTasks(); }
     catch(error) { el("tasks-output").textContent="عملیات وظیفه ناموفق بود: "+error.message; button.disabled=false; }
    });
    actions.append(button);
   };
   if(["queued","running"].includes(task.status)) addAction("توقف","/api/tasks/"+task.id+"/pause");
   if(task.status==="paused") addAction("ادامه","/api/tasks/"+task.id+"/resume");
   if(["queued","running","pause_requested","paused"].includes(task.status)) addAction("لغو","/api/tasks/"+task.id+"/cancel");
   if(["queued","paused","cancelled","succeeded","failed"].includes(task.status)) {
    const deleteButton=document.createElement("button");
    deleteButton.type="button";
    deleteButton.className="danger";
    deleteButton.textContent="حذف وظیفه";
    deleteButton.setAttribute("aria-label","حذف وظیفه "+task.id);
    deleteButton.addEventListener("click",async()=>{
     if(!window.confirm("وظیفهٔ #"+task.id+" («"+task.task_type+"») و تاریخچهٔ رویدادهای وابسته حذف شود؟ این کار قابل بازگشت نیست.")) return;
     deleteButton.disabled=true;
     try {
      await api("/api/tasks/"+encodeURIComponent(task.id),{method:"DELETE"});
      taskNextCursor=null;
      await refreshTasks();
      el("tasks-output").textContent="وظیفهٔ #"+task.id+" حذف شد.";
     } catch(error) {
      el("tasks-output").textContent="حذف وظیفه ناموفق بود: "+error.message;
      deleteButton.disabled=false;
     }
    });
    actions.append(deleteButton);
   }
   if(actions.childElementCount) card.append(actions);
   container.append(card);
  });
  updateTaskDashboard();
 }
 let taskNextCursor=null;
 let taskRefreshSequence=0;
 async function refreshTasks({append=false}={}) {
   const requestSequence=++taskRefreshSequence;
   const loadMore=el("load-more-tasks");
   const statusFilter=el("task-status-filter").value;
   try {
    const query=new URLSearchParams({limit:"50"});
    if(statusFilter) query.set("statuses",statusFilter);
    if(append&&taskNextCursor) query.set("cursor",taskNextCursor);
    const result=await api("/api/tasks?"+query.toString());
    if(requestSequence!==taskRefreshSequence||el("task-status-filter").value!==statusFilter)return;
    const tasks=result.tasks||[];
    renderTasks(tasks,{append});
    taskNextCursor=result.next_cursor||null;
    loadMore.hidden=!taskNextCursor;
    el("tasks-output").textContent="وظایف نمایش‌داده‌شده: "+el("tasks-container").querySelectorAll("article").length+(result.has_more?" — موارد بیشتری موجود است.":"");
    const active=tasks.some(task=>["queued","running","pause_requested","cancel_requested"].includes(task.status));
    if(active&&!taskPollScheduled) {
     taskPollScheduled=true;
     setTimeout(()=>{taskPollScheduled=false;refreshTasks();},2000);
    }
   } catch(error) {
    if(requestSequence===taskRefreshSequence&&el("task-status-filter").value===statusFilter)
     el("tasks-output").textContent="فهرست وظایف دریافت نشد: "+error.message;
   }
  }
  el("refresh-tasks").addEventListener("click",()=>{taskNextCursor=null;refreshTasks();});
 el("task-status-filter").addEventListener("change",()=>{taskNextCursor=null;refreshTasks();});
 el("task-search").addEventListener("input",updateTaskDashboard);
 el("task-select-all").addEventListener("change",()=>{
  const checked=el("task-select-all").checked;
  el("tasks-container").querySelectorAll(".task-select").forEach(input=>{input.checked=checked;});
  updateTaskDashboard();
 });
 el("bulk-pause-tasks").addEventListener("click",()=>runBulkTaskAction("pause"));
 el("bulk-resume-tasks").addEventListener("click",()=>runBulkTaskAction("resume"));
 el("bulk-cancel-tasks").addEventListener("click",()=>runBulkTaskAction("cancel"));
 el("load-more-tasks").addEventListener("click",()=>refreshTasks({append:true}));
 
 refreshTasks();
})();