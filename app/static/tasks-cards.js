(() => {
 "use strict";
 function createTaskCards({
  el,api,appendText,refreshTasks,resetCursor,updateTaskDashboard,updateTaskBulkControls
 }) {
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
  resetCursor();
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
      resetCursor();
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

  return Object.freeze({renderTasks,runBulkTaskAction});
 }
 window.taskCardsFactory = Object.freeze({version:1,createTaskCards});
})();
