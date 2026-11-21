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
  return Object.freeze({version:1,el,api});
 }
 const core=window.dashboardCore&&window.dashboardCore.version===1?window.dashboardCore:createLocalDashboardCore();
 const {el,api}=core;
 const required=["experience-auto-capture-enable","experience-auto-capture-revoke","experience-auto-capture-refresh","experience-auto-capture-confirm","experience-auto-capture-status"];
 if(required.some(id=>!el(id)))return;
let experienceConsentRefreshSequence=0;
async function refreshExperienceAutoCaptureConsent() {
 const requestSequence=++experienceConsentRefreshSequence;
 const output=el("experience-auto-capture-status");
 try {
  const result=await api("/api/experiences/auto-capture/consent");
  if(requestSequence!==experienceConsentRefreshSequence)return;
  const consent=result.consent||{};
  if(document.activeElement!==el("experience-auto-capture-confirm"))
   el("experience-auto-capture-confirm").checked=consent.enabled===true;
  output.textContent=(consent.enabled
   ? "ذخیرهٔ خودکار فعال است؛ فقط پرسش‌های فنیِ دارای منبع ذخیره می‌شوند."
   : "ذخیرهٔ خودکار غیرفعال است؛ هیچ پرسش یا پاسخی خودکار ذخیره نمی‌شود.")+
   "\n\nنسخهٔ سیاست: "+consent.policy_version+
   "\n\nتاریخچهٔ رضایت:\n"+JSON.stringify(consent.events||[],null,2);
 } catch(error) {
  if(requestSequence!==experienceConsentRefreshSequence)return;
  output.textContent="وضعیت رضایت قابل دریافت نیست: "+error.message;
 }
}
el("experience-auto-capture-enable").addEventListener("click",async()=>{
 experienceConsentRefreshSequence+=1;
 if(!el("experience-auto-capture-confirm").checked) {
  el("experience-auto-capture-status").textContent="برای فعال‌سازی، ابتدا رضایت صریح را علامت بزنید.";
  return;
 }
 el("experience-auto-capture-status").textContent="در حال ثبت رضایت…";
 try {
  await api("/api/experiences/auto-capture/consent",{method:"POST",body:JSON.stringify({confirm:true})});
  await refreshExperienceAutoCaptureConsent();
 } catch(error) {
  await refreshExperienceAutoCaptureConsent();
  el("experience-auto-capture-status").textContent="رضایت ثبت نشد: "+error.message;
 }
});
el("experience-auto-capture-revoke").addEventListener("click",async()=>{
 experienceConsentRefreshSequence+=1;
 el("experience-auto-capture-status").textContent="در حال لغو رضایت…";
 try {
  await api("/api/experiences/auto-capture/consent",{method:"DELETE"});
  el("experience-auto-capture-confirm").checked=false;
  await refreshExperienceAutoCaptureConsent();
 } catch(error) {
  await refreshExperienceAutoCaptureConsent();
  el("experience-auto-capture-status").textContent="لغو رضایت انجام نشد: "+error.message;
 }
});
el("experience-auto-capture-refresh").addEventListener("click",refreshExperienceAutoCaptureConsent);
refreshExperienceAutoCaptureConsent();

 Object.defineProperty(window,"experienceMemoryConsent",{
  value:Object.freeze({version:1,refresh:refreshExperienceAutoCaptureConsent}),
  enumerable:true,configurable:false,writable:false
 });
})();
