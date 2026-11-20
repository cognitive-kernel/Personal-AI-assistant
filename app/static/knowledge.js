(() => {
 "use strict";
 const core=window.dashboardCore;
 const fallback=document.getElementById("knowledge-output");
 if(!core||core.version!==1){
  if(fallback)fallback.textContent="ماژول پایگاه دانش بارگذاری نشد: قرارداد dashboardCore v1 در دسترس نیست.";
  return;
 }
 const {api,el}=core;
 const required=["knowledge-output","knowledge-source-status","refresh-knowledge","refresh-knowledge-sources","knowledge-source-ingest-form","knowledge-source-search-form","knowledge-source-url","knowledge-source-title","knowledge-source-ingest","knowledge-source-search","knowledge-source-query","knowledge-source-limit","knowledge-source-search-output"];
 const missing=required.filter(id=>!el(id));
 if(missing.length){
  if(fallback)fallback.textContent="ماژول پایگاه دانش به کنترل‌های لازم دسترسی ندارد: "+missing.join(", ");
  return;
 }
 el("refresh-knowledge").addEventListener("click", async () => {
  try { el("knowledge-output").textContent=JSON.stringify(await api("/api/knowledge"),null,2); }
  catch(error) { el("knowledge-output").textContent=error.message; }
 });
 async function refreshKnowledgeSources() {
  const output=el("knowledge-source-status");
  try {
   const result=await api("/api/knowledge/sources");
   output.textContent=JSON.stringify(result,null,2);
   return result;
  } catch(error) {
   output.textContent="بارگذاری منابع ناموفق بود: "+error.message;
   throw error;
  }
 }
 el("refresh-knowledge-sources").addEventListener("click",async()=>{
  const button=el("refresh-knowledge-sources");
  button.disabled=true;
  try { await refreshKnowledgeSources(); }
  catch(_error) {}
  finally { button.disabled=false; }
 });
 el("knowledge-source-ingest-form").addEventListener("submit",async event=>{
  event.preventDefault();
  const button=el("knowledge-source-ingest");
  const status=el("knowledge-source-status");
  const payload={
   url:el("knowledge-source-url").value.trim(),
   title:el("knowledge-source-title").value.trim() || null
  };
  if(!payload.url) return;
  button.disabled=true;
  status.textContent="در حال دریافت و ثبت منبع…";
  try {
   const result=await api("/api/knowledge/sources",{method:"POST",body:JSON.stringify(payload)});
   let listError=null;
   try { await refreshKnowledgeSources(); }
   catch(error) { listError=error; }
   status.textContent="ثبت منبع انجام شد.\n"+JSON.stringify(result,null,2)
    +(listError ? "\n\nبارگذاری فهرست منابع ناموفق بود: "+listError.message : "\n\nفهرست منابع به‌روز شد.");
  } catch(error) {
   status.textContent="ثبت منبع ناموفق بود: "+error.message;
  } finally {
   button.disabled=false;
  }
 });
 el("knowledge-source-search-form").addEventListener("submit",async event=>{
  event.preventDefault();
  const button=el("knowledge-source-search");
  const output=el("knowledge-source-search-output");
  const query=el("knowledge-source-query").value.trim();
  const limit=Number(el("knowledge-source-limit").value);
  if(!query) return;
  button.disabled=true;
  output.textContent="در حال جست‌وجو…";
  try {
   const result=await api("/api/knowledge/sources/search",{method:"POST",body:JSON.stringify({query,limit})});
   output.textContent=JSON.stringify(result,null,2);
  } catch(error) {
   output.textContent="جست‌وجو ناموفق بود: "+error.message;
  } finally {
   button.disabled=false;
  }
 });
 
})();