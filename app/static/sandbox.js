/* Local sandbox controls and task lifecycle.
 * Shared dependency contract v1: dashboardCore.{api,el}.
 */
(() => {
 "use strict";
 const core=window.dashboardCore;
 if(!core||core.version!==1||typeof core.api!=="function"||typeof core.el!=="function"){
  const status=document.getElementById("sandbox-status");
  if(status) status.textContent="ماژول Sandbox بارگذاری نشد؛ قرارداد هستهٔ داشبورد معتبر نیست.";
  throw new Error("Sandbox module requires dashboardCore contract v1.");
 }
 const {api,el}=core;
let activeSandboxTaskId = null;
let sandboxConsentRefreshSequence=0;
async function refreshSandbox() {
 const requestSequence=++sandboxConsentRefreshSequence;
 const status=el("sandbox-status");
 const runButton=el("sandbox-run");
 try {
  const result=await api("/api/sandbox/consent");
  if(requestSequence!==sandboxConsentRefreshSequence)return;
  const consent=result.consent||{};
  const enabled=result.execution_enabled===true;
  el("sandbox-consent-confirm").checked=consent.enabled===true;
  runButton.disabled=!enabled;
  status.textContent=JSON.stringify({
   configured:result.runtime_configured===true,
   consent_enabled:consent.enabled===true,
   execution_enabled:enabled,
   policy_version:consent.policy_version||null,
   consented_at:consent.consented_at||null,
   revoked_at:consent.revoked_at||null,
   audit_events:consent.events||[]
  },null,2);
 } catch(error) {
  if(requestSequence!==sandboxConsentRefreshSequence)return;
  runButton.disabled=true;
  status.textContent="وضعیت sandbox قابل دریافت نیست؛ اجرا غیرفعال می‌ماند. "+error.message;
 }
}
el("sandbox-refresh").addEventListener("click",refreshSandbox);
el("sandbox-cancel").addEventListener("click",async()=>{
 if(activeSandboxTaskId===null) return;
 const taskId=activeSandboxTaskId;
 el("sandbox-cancel").disabled=true;
 el("sandbox-run-output").textContent="درخواست لغو وظیفهٔ #"+taskId+"…";
 try {
  await api("/api/tasks/"+taskId+"/cancel",{method:"POST"});
  el("sandbox-run-output").textContent="درخواست لغو ارسال شد؛ وضعیت نهایی در حال بررسی است.";
 } catch(error) {
  el("sandbox-run-output").textContent="لغو وظیفه ثبت نشد: "+error.message;
  el("sandbox-cancel").disabled=false;
 }
});
el("sandbox-consent-grant").addEventListener("click",async()=>{
 sandboxConsentRefreshSequence+=1;
 if(!el("sandbox-consent-confirm").checked) {
  el("sandbox-status").textContent="برای ثبت رضایت، ابتدا چک‌باکس رضایت صریح را فعال کنید.";
  return;
 }
 el("sandbox-status").textContent="در حال ثبت رضایت…";
 try {
  await api("/api/sandbox/consent",{method:"POST",body:JSON.stringify({confirm:true})});
  await refreshSandbox();
 } catch(error) {
  el("sandbox-status").textContent="رضایت ثبت نشد؛ اجرا غیرفعال می‌ماند. "+error.message;
  el("sandbox-run").disabled=true;
 }
});
el("sandbox-consent-revoke").addEventListener("click",async()=>{
 sandboxConsentRefreshSequence+=1;
 el("sandbox-status").textContent="در حال لغو رضایت…";
 el("sandbox-run").disabled=true;
 try {
  await api("/api/sandbox/consent",{method:"DELETE"});
  await refreshSandbox();
 } catch(error) {
  el("sandbox-status").textContent="لغو رضایت ناموفق بود: "+error.message;
 }
});
el("sandbox-run-form").addEventListener("submit",async event=>{
 event.preventDefault();
 const output=el("sandbox-run-output");
 const source=el("sandbox-source").value;
 if(!source.trim()) { output.textContent="کد خالی قابل اجرا نیست."; return; }
 if(el("sandbox-run").disabled) { output.textContent="اجرا غیرفعال است؛ وضعیت و رضایت را بررسی کنید."; return; }
 el("sandbox-run").disabled=true;
 output.textContent="در حال ثبت وظیفهٔ sandbox…";
 try {
  const queued=await api("/api/sandbox/run",{method:"POST",body:JSON.stringify({
   source_code:source,
   timeout_seconds:Number(el("sandbox-timeout").value),
   max_output_bytes:Number(el("sandbox-output-limit").value)
  })});
  const task=queued.task;
  activeSandboxTaskId=task.id;
  el("sandbox-cancel").disabled=false;
  output.textContent="وظیفهٔ #"+task.id+" ثبت شد؛ وضعیت: "+task.status;
  for(let attempt=0;attempt<180;attempt++) {
   await new Promise(resolve=>setTimeout(resolve,1000));
   const current=await api("/api/tasks/"+task.id);
   output.textContent=JSON.stringify({
    id:current.id,status:current.status,attempts:current.attempts,
    result:current.result,error:current.error,checkpoint:current.checkpoint
   },null,2);
   if(["succeeded","failed","cancelled","paused"].includes(current.status)) {
    activeSandboxTaskId=null;
    el("sandbox-cancel").disabled=true;
    break;
   }
  }
 } catch(error) {
  output.textContent="اجرای sandbox ثبت یا پیگیری نشد: "+error.message;
 } finally {
  if(activeSandboxTaskId===null) el("sandbox-cancel").disabled=true;
  await refreshSandbox();
 }
});

 refreshSandbox();
})();
