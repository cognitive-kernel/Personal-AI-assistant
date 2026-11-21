(() => {
 "use strict";
 function createTaskView({el,refreshTasks,resetCursor}) {
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
     resetCursor();
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

  return Object.freeze({updateTaskDashboard,updateTaskBulkControls});
 }
 Object.defineProperty(window, "taskViewFactory", {
  value: Object.freeze({version:1,createTaskView}),
  enumerable: true,
  configurable: false,
  writable: false
 });
})();
