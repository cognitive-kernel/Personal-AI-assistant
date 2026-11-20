(() => {
 "use strict";
 const core=window.dashboardCore;
 const fallback=document.getElementById("health");
 if(!core||core.version!==1){
  if(fallback)fallback.textContent="ماژول سلامت ذخیره‌سازی بارگذاری نشد: قرارداد dashboardCore v1 در دسترس نیست.";
  return;
 }
 const {api,el}=core;
 const required=["health","refresh","storage-degraded-banner","storage-degraded-message"];
 const missing=required.filter(id=>!el(id));
 if(missing.length){
  if(fallback)fallback.textContent="ماژول سلامت به کنترل‌های لازم دسترسی ندارد: "+missing.join(", ");
  return;
 }
 async function refresh() {
  try {
   const health = await api("/api/health");
   el("health").textContent = JSON.stringify(health, null, 2);
   const degraded = health.status === "degraded" && health.reason === "untracked_storage_schema";
   const banner = el("storage-degraded-banner");
   if (banner) banner.hidden = !degraded;
   if (degraded) {
    el("storage-degraded-message").textContent = health.message || "ساختار پایگاه داده نیازمند بررسی است؛ عملیات دیتابیسی مسدود است و هیچ مهاجرت یا تعمیری انجام نشده است.";
   }
  } catch(error) {
   el("health").textContent = error.message;
   const banner = el("storage-degraded-banner");
   if (banner) banner.hidden = true;
  }
 }
 
 el("refresh").addEventListener("click", () => { refresh(); const settings=window.dashboardSettings; if(settings && settings.version===1) settings.refresh(); });
 refresh();
 
 el("refresh").addEventListener("click",refresh);
 refresh();
})();