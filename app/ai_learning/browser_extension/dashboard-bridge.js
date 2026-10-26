"use strict";
(() => {
  const origins = new Set(["http://127.0.0.1:8000", "http://localhost:8000"]);
  window.addEventListener("message", async event => {
    if (event.source !== window || event.origin !== location.origin || !origins.has(event.origin)) return;
    const data = event.data;
    if (!data || data.source !== "ai-learning-dashboard" || data.type !== "start-outline") return;
    if (!Number.isInteger(data.courseId) || data.courseId < 1 || typeof data.requestId !== "string") return;
    try {
      const result = await chrome.runtime.sendMessage({type:"prepare-run",payload:{
        course_id:data.courseId,run_type:"outline",topic_ids:[],selection_mode:"single",
        request_key:"dashboard-outline-"+data.courseId+"-"+Date.now()
      }});
      window.postMessage({source:"ai-learning-extension-bridge",requestId:data.requestId,
        ok:Boolean(result&&result.ok),result:result&&result.result,
        error:result&&result.error},location.origin);
    } catch (error) {
      window.postMessage({source:"ai-learning-extension-bridge",requestId:data.requestId,
        ok:false,error:String(error&&error.message||error)},location.origin);
    }
  });
})();
