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
 const fallback=document.getElementById("kaggle-quota-status") || document.getElementById("oci-limits-status");
 if(!core||core.version!==1){
  if(fallback)fallback.textContent="ماژول سهمیه‌های ابری بارگذاری نشد: قرارداد dashboardCore v1 در دسترس نیست.";
  return;
 }
 const {api,el}=core;
 const required=["kaggle-quota-status","kaggle-quota-consent","kaggle-cli-path","kaggle-quota-enable","kaggle-quota-revoke","kaggle-quota-refresh","oci-limits-status","oci-limits-consent","oci-cli-path","oci-config-path","oci-profile","oci-region","oci-compartment-id","oci-limits-enable","oci-limits-revoke","oci-limits-refresh"];
 const missing=required.filter(id=>!el(id));
 if(missing.length){
  if(fallback)fallback.textContent="ماژول سهمیه‌های ابری به کنترل‌های لازم دسترسی ندارد: "+missing.join(", ");
  return;
 }
 let kaggleQuotaRefreshSequence=0;
 async function refreshKaggleQuota() {
  const requestSequence=++kaggleQuotaRefreshSequence;
  try {
   const result=await api("/api/cloud-resources/kaggle-quota");
   if(requestSequence!==kaggleQuotaRefreshSequence)return;
   const consent=result.consent||{};
   const hasConsent=!!consent.enabled&&!!consent.user_consent;
   if(document.activeElement!==el("kaggle-quota-consent"))el("kaggle-quota-consent").checked=hasConsent;
   if(document.activeElement!==el("kaggle-cli-path"))el("kaggle-cli-path").value=consent.executable_path||"";
   el("kaggle-quota-refresh").disabled=!hasConsent;
   const snapshots=result.snapshots||[];
   const events=result.consent_events||[];
   const state=consent.enabled&&consent.user_consent
    ? "رضایت فعال است؛ بررسی سهمیه فقط با دکمهٔ دستی اجرا می‌شود."
    : "رضایت غیرفعال است؛ هیچ دسترسی به Kaggle انجام نمی‌شود.";
   el("kaggle-quota-status").textContent=state+"\n\nآخرین سهمیهٔ ذخیره‌شده:\n"+
    (snapshots.length?JSON.stringify(snapshots,null,2):"هنوز سهمیه‌ای ثبت نشده است.")+
    "\n\nتاریخچهٔ رضایت:\n"+JSON.stringify(events,null,2);
  } catch(error) {
   if(requestSequence!==kaggleQuotaRefreshSequence)return;
   el("kaggle-quota-refresh").disabled=true;
   el("kaggle-quota-status").textContent="وضعیت سهمیه قابل دریافت نیست: "+error.message;
  }
 }
 el("kaggle-quota-enable").addEventListener("click",async()=>{
  kaggleQuotaRefreshSequence+=1;
  const path=el("kaggle-cli-path").value.trim();
  if(!el("kaggle-quota-consent").checked) {
   el("kaggle-quota-status").textContent="برای فعال‌سازی، ابتدا چک‌باکس رضایت صریح را علامت بزنید.";
   return;
  }
  if(!path) {
   el("kaggle-quota-status").textContent="مسیر کامل فایل اجرایی Kaggle CLI الزامی است.";
   return;
  }
  el("kaggle-quota-status").textContent="در حال ثبت رضایت…";
  try {
   await api("/api/cloud-resources/kaggle-quota/consent",{
    method:"PATCH",
    body:JSON.stringify({enabled:true,user_consent:true,executable_path:path})
   });
   await refreshKaggleQuota();
  } catch(error) {
   await refreshKaggleQuota();
   el("kaggle-quota-status").textContent="رضایت ذخیره نشد: "+error.message;
  }
 });
 el("kaggle-quota-revoke").addEventListener("click",async()=>{
  kaggleQuotaRefreshSequence+=1;
  el("kaggle-quota-refresh").disabled=true;
  el("kaggle-quota-status").textContent="در حال لغو رضایت…";
  try {
   await api("/api/cloud-resources/kaggle-quota/consent",{
    method:"PATCH",
    body:JSON.stringify({enabled:false,user_consent:false,executable_path:""})
   });
   el("kaggle-cli-path").value="";
   el("kaggle-quota-consent").checked=false;
   await refreshKaggleQuota();
  } catch(error) {
   await refreshKaggleQuota();
   el("kaggle-quota-status").textContent="لغو رضایت انجام نشد: "+error.message;
  }
 });
 el("kaggle-quota-refresh").addEventListener("click",async()=>{
  kaggleQuotaRefreshSequence+=1;
  el("kaggle-quota-refresh").disabled=true;
  el("kaggle-quota-status").textContent="در حال خواندن سهمیه از CLI رسمی…";
  try {
   await api("/api/cloud-resources/kaggle-quota/refresh",{method:"POST"});
   await refreshKaggleQuota();
  } catch(error) {
   await refreshKaggleQuota();
   el("kaggle-quota-status").textContent="سهمیه دریافت نشد: "+error.message;
  }
 });
 
 
 
 
 let ociLimitsRefreshSequence=0;
 async function refreshOciLimits() {
  const requestSequence=++ociLimitsRefreshSequence;
  try {
   const result=await api("/api/cloud-resources/oci-limits");
   if(requestSequence!==ociLimitsRefreshSequence)return;
   const consent=result.consent||{};
   const hasConsent=!!consent.enabled&&!!consent.user_consent;
   if(document.activeElement!==el("oci-limits-consent"))el("oci-limits-consent").checked=hasConsent;
   for(const [field,key] of [
    ["oci-cli-path","executable_path"],["oci-config-path","config_file"],
    ["oci-profile","profile"],["oci-region","region"],["oci-compartment-id","compartment_id"]
   ]) {
    if(document.activeElement!==el(field))el(field).value=consent[key]||"";
   }
   el("oci-limits-refresh").disabled=!hasConsent;
   const snapshots=result.snapshots||[];
   const events=result.consent_events||[];
   const state=consent.enabled&&consent.user_consent
    ? "رضایت فعال است؛ درخواست به CLI فقط با دکمهٔ دستی اجرا می‌شود."
    : "رضایت غیرفعال است؛ هیچ درخواست OCI اجرا نمی‌شود.";
   el("oci-limits-status").textContent=state+
    "\n\nمحدودیت‌های ذخیره‌شده:\n"+
    (snapshots.length?JSON.stringify(snapshots,null,2):"هنوز snapshot محدودیت ثبت نشده است.")+
    "\n\nمجوز اجرای remote: false\n\nتاریخچهٔ رضایت:\n"+JSON.stringify(events,null,2);
  } catch(error) {
   if(requestSequence!==ociLimitsRefreshSequence)return;
   el("oci-limits-refresh").disabled=true;
   el("oci-limits-status").textContent="وضعیت محدودیت‌های OCI قابل دریافت نیست: "+error.message;
  }
 }
 el("oci-limits-enable").addEventListener("click",async()=>{
  ociLimitsRefreshSequence+=1;
  if(!el("oci-limits-consent").checked) {
   el("oci-limits-status").textContent="برای فعال‌سازی، ابتدا رضایت صریح را علامت بزنید.";
   return;
  }
  const body={
   enabled:true,user_consent:true,
   executable_path:el("oci-cli-path").value.trim(),
   config_file:el("oci-config-path").value.trim(),
   profile:el("oci-profile").value.trim(),
   region:el("oci-region").value.trim(),
   compartment_id:el("oci-compartment-id").value.trim()
  };
  el("oci-limits-status").textContent="در حال ثبت تنظیمات و رضایت…";
  try {
   await api("/api/cloud-resources/oci-limits/consent",{method:"PATCH",body:JSON.stringify(body)});
   await refreshOciLimits();
  } catch(error) {
   await refreshOciLimits();
   el("oci-limits-status").textContent="رضایت OCI ذخیره نشد: "+error.message;
  }
 });
 el("oci-limits-revoke").addEventListener("click",async()=>{
  ociLimitsRefreshSequence+=1;
  el("oci-limits-refresh").disabled=true;
  el("oci-limits-status").textContent="در حال لغو رضایت و پاک‌سازی snapshotها…";
  try {
   await api("/api/cloud-resources/oci-limits/consent",{
    method:"PATCH",
    body:JSON.stringify({
     enabled:false,user_consent:false,executable_path:"",config_file:"",
     profile:"",region:"",compartment_id:""
    })
   });
   el("oci-limits-consent").checked=false;
   for(const id of ["oci-cli-path","oci-config-path","oci-profile","oci-region","oci-compartment-id"]) el(id).value="";
   await refreshOciLimits();
  } catch(error) {
   await refreshOciLimits();
   el("oci-limits-status").textContent="لغو رضایت OCI انجام نشد: "+error.message;
  }
 });
 el("oci-limits-refresh").addEventListener("click",async()=>{
  ociLimitsRefreshSequence+=1;
  el("oci-limits-refresh").disabled=true;
  el("oci-limits-status").textContent="در حال اجرای فرمان خواندنی OCI limits value list…";
  try {
   await api("/api/cloud-resources/oci-limits/refresh",{method:"POST"});
   await refreshOciLimits();
  } catch(error) {
   await refreshOciLimits();
   el("oci-limits-status").textContent="محدودیت‌های OCI دریافت نشد: "+error.message;
  }
 });
 
 refreshKaggleQuota();
 refreshOciLimits();
})();