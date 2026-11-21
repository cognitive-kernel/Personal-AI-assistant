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
 const fallback=document.getElementById("settings-output");
 if(!core||core.version!==1){
  if(fallback)fallback.textContent="ماژول تنظیمات بارگذاری نشد: قرارداد dashboardCore v1 در دسترس نیست.";
  return;
 }
 const {api,el}=core;
 const required=["settings-form","settings-output","ollama-model-options","refresh-ollama-models","hardware-diagnostics","learning-cost-profiles","learning-external-review-providers","learning-task-budget","programming-context"];
 const missing=required.filter(id=>!el(id));
 if(missing.length){
  if(fallback)fallback.textContent="ماژول تنظیمات به کنترل‌های لازم دسترسی ندارد: "+missing.join(", ");
  return;
 }
 let settingsLoadSequence=0;
 async function refreshSettings(){
  const requestSequence=++settingsLoadSequence;
  try { const s = await api("/api/settings"); if(requestSequence!==settingsLoadSequence)return; el("cpu-resource-limit").value=s.cpu_resource_limit_percent ?? 90; el("ram-resource-limit").value=s.ram_resource_limit_percent ?? 90; el("gpu-resource-limit").value=s.gpu_resource_limit_percent ?? 90; el("gpu-enabled").checked=s.gpu_enabled === true; el("ram-budget-max").value=s.ram_budget_max_gb ?? 25; el("ram-safety-reserve").value=s.ram_safety_reserve_gb ?? 4; const fields={"chat-provider":"chat_llm_provider","chat-base-url":"chat_llm_base_url","chat-api-key-env":"chat_llm_api_key_env","research-plan-provider":"research_plan_llm_provider","research-plan-base-url":"research_plan_llm_base_url","research-plan-api-key-env":"research_plan_llm_api_key_env","research-plan-model":"research_plan_ollama_model","learning-provider":"learning_llm_provider","learning-base-url":"learning_llm_base_url","learning-api-key-env":"learning_llm_api_key_env","programming-provider":"programming_llm_provider","programming-base-url":"programming_llm_base_url","programming-api-key-env":"programming_llm_api_key_env","factual-review-provider":"factual_review_llm_provider","factual-review-base-url":"factual_review_llm_base_url","factual-review-api-key-env":"factual_review_llm_api_key_env","embedding-provider":"embedding_llm_provider","embedding-base-url":"embedding_llm_base_url","embedding-api-key-env":"embedding_llm_api_key_env","embedding-model":"ollama_embedding_model","chat-keep-alive":"chat_model_keep_alive","learning-keep-alive":"learning_model_keep_alive","programming-keep-alive":"programming_model_keep_alive","chat-model":"chat_ollama_model","chat-ctx":"chat_ollama_num_ctx","chat-threads":"chat_ollama_num_thread","chat-timeout":"chat_ollama_timeout_seconds","chat-concurrency":"chat_max_concurrent_jobs","chat-source-limit":"chat_source_max_chars","learning-model":"learning_ollama_model","learning-ctx":"learning_ollama_num_ctx","learning-threads":"learning_ollama_num_thread","learning-timeout":"learning_ollama_timeout_seconds","learning-concurrency":"learning_max_concurrent_jobs","learning-source-limit":"learning_source_max_chars","programming-model":"programming_ollama_model","programming-ctx":"programming_ollama_num_ctx","programming-threads":"programming_ollama_num_thread","programming-timeout":"programming_ollama_timeout_seconds","programming-concurrency":"programming_max_concurrent_jobs","programming-source-limit":"programming_source_max_chars","factual-review-model":"factual_review_ollama_model","learning-min-quality":"learning_model_minimum_quality_score"}; for(const [id,key] of Object.entries(fields)) el(id).value=s[key] ?? s.ollama_model ?? ""; el("programming-context").maxLength=Number(s.programming_source_max_chars ?? 12000); el("learning-cost-profiles").value=JSON.stringify(s.learning_model_cost_profiles || {},null,2); el("learning-external-review-providers").value=JSON.stringify(s.learning_external_review_providers || [],null,2); el("learning-task-budget").value=s.learning_task_budget_microusd == null ? "" : s.learning_task_budget_microusd; refreshOllamaModels(); } catch(error) { if(requestSequence===settingsLoadSequence) el("settings-output").textContent=error.message; }
 }
 let ollamaModelsRequestSequence=0;
 async function refreshOllamaModels() {
  const requestSequence=++ollamaModelsRequestSequence;
  const list=el("ollama-model-options");
  if(!list) return;
  list.replaceChildren();
  const providers=["chat","research-plan","learning","programming","factual-review","embedding"];
  const providerSnapshot=providers.map(name=>el(name+"-provider")?.value||"").join("|");
  const isCurrent=()=>requestSequence===ollamaModelsRequestSequence &&
   providerSnapshot===providers.map(name=>el(name+"-provider")?.value||"").join("|");
  if(providers.every(name=>el(name+"-provider")?.value==="openai_compatible")) {
   for(const id of ["chat-model-installed","learning-model-installed","programming-model-installed"]) {
    const select=el(id);
    if(select) select.replaceChildren(new Option("مدل‌ها از endpoint انتخاب‌شده دریافت می‌شوند",""));
   }
   for(const profile of modelContextProfiles) {
    if(!isCurrent()) return;
    await refreshModelContextHint(profile);
   }
   if(!isCurrent()) return;
   el("settings-output").textContent="تمام مسیرها از provider سازگار استفاده می‌کنند؛ فهرست مدل Ollama لازم نیست.";
   return;
  }
  const installedSelects = [
   ["chat-model-installed","chat-model"],
   ["learning-model-installed","learning-model"],
   ["programming-model-installed","programming-model"]
  ];
  for(const [selectId] of installedSelects) {
   const select=el(selectId);
   if(select) select.replaceChildren(new Option("انتخاب مدل نصب‌شده",""));
  }
  try {
   const result=await api("/api/models");
   if(!isCurrent()) return;
   const models=Array.isArray(result.models)?result.models:[];
   for(const model of models) {
    if(!model || typeof model.name!=="string" || !model.name.trim()) continue;
    const label=model.size ? model.name+" — "+Math.round(model.size/1073741824*10)/10+" GiB" : model.name;
    const datalistOption=document.createElement("option");
    datalistOption.value=model.name;
    datalistOption.label=label;
    list.append(datalistOption);
    for(const [selectId] of installedSelects) {
     const select=el(selectId);
     if(select) select.add(new Option(label,model.name));
    }
   }
   for(const [selectId,inputId] of installedSelects) {
    const select=el(selectId), input=el(inputId);
    if(select && input && models.some(model=>model && model.name===input.value.trim())) select.value=input.value.trim();
   }
   el("settings-output").textContent="مدل‌های نصب‌شدهٔ Ollama: "+models.filter(model=>model&&typeof model.name==="string").length;
  } catch(error) {
   if(isCurrent()) el("settings-output").textContent="فهرست مدل‌های نصب‌شده دریافت نشد؛ نام مدل را می‌توانید دستی وارد کنید. "+error.message;
  }
  for(const profile of modelContextProfiles) {
   if(!isCurrent()) return;
   await refreshModelContextHint(profile);
  }
  if(!isCurrent()) return;
  await refreshHardwareDiagnostics(modelContextProfiles[0]);
 }
 
 const modelContextProfiles = [
  ["chat","chat-model","chat-ctx","chat-ctx-hint"],
  ["learning","learning-model","learning-ctx","learning-ctx-hint"],
  ["programming","programming-model","programming-ctx","programming-ctx-hint"]
 ];
 const modelContextHintRequestSequences=new Map();
 async function refreshModelContextHint(profile, applyRecommendation=false) {
  const [purpose,modelId,contextId,hintId]=profile;
  const model=el(modelId).value.trim();
  let context=Number(el(contextId).value);
  const requestSequence=(modelContextHintRequestSequences.get(purpose)||0)+1;
  modelContextHintRequestSequences.set(purpose,requestSequence);
  const provider=el(purpose+"-provider")?.value||"";
  const isCurrent=()=>modelContextHintRequestSequences.get(purpose)===requestSequence &&
   el(modelId)?.value.trim()===model && Number(el(contextId)?.value)===context &&
   el(purpose+"-provider")?.value===provider;
  const input=el(contextId);
  const hint=el(hintId);
  if(el(purpose+"-provider")?.value==="openai_compatible") {
   input.removeAttribute("max");
   input.setCustomValidity("");
   hint.textContent="OpenAI-compatible: سقف context از سمت endpoint/model تعیین می‌شود؛ این مقدار به‌صورت دستی اعمال می‌شود.";
   return;
  }
  if(!model) { hint.textContent="ابتدا مدل را انتخاب کنید."; input.setCustomValidity(""); return; }
  hint.textContent="در حال دریافت metadata مدل و برآورد حافظه…";
  try {
   let data=await api("/api/model-details?name="+encodeURIComponent(model)+"&context_tokens="+encodeURIComponent(context));
   if(!isCurrent()) return;
   const maximum=data.maximum_context_tokens;
   if(applyRecommendation && Number.isInteger(data.recommended_context_tokens) && data.recommended_context_tokens>0) {
    context=data.recommended_context_tokens;
    el(contextId).value=String(context);
    data=await api("/api/model-details?name="+encodeURIComponent(model)+"&context_tokens="+encodeURIComponent(context));
    if(!isCurrent()) return;
   }
   if(Number.isInteger(maximum) && maximum>0) {
    input.max=String(maximum);
    const invalid=context>maximum;
    input.setCustomValidity(invalid?"مقدار context از حداکثر پشتیبانی مدل بیشتر است.":"");
    const estimate=data.memory_estimate||{};
    const modelSpecs=[data.parameter_size,data.quantization].filter(Boolean).join(" / ");
    const estimateText=estimate.total_ram_gib_upper_bound!=null
     ? " | سقف محافظه‌کارانه RAM در اجرای CPU-only و این context: "+estimate.total_ram_gib_upper_bound.toFixed(2)+" GiB (KV cache با بودجه‌بندی FP32)"
     : " | پیشنهاد امن محاسبه نشد؛ اندازه مدل، ابعاد KV cache یا RAM آزاد قابل‌تأیید نیست.";
    const recommended=data.recommended_context_tokens;
    hint.textContent="حداکثر پشتیبانی مدل: "+maximum.toLocaleString()+" توکن؛ پیشنهاد امن بر اساس منابع فعلی: "+(Number.isInteger(recommended)?recommended.toLocaleString():"نامشخص")+" توکن؛ مقدار انتخابی: "+context.toLocaleString()+" توکن"+(modelSpecs?"؛ اندازه/کوانتیزیشن: "+modelSpecs:"")+estimateText
     +(invalid?" | مقدار فعلی بیش از حد مجاز است.":"");
   } else {
    input.removeAttribute("max");
    input.setCustomValidity("");
    hint.textContent="حداکثر context در metadata این مدل موجود نیست؛ برای جلوگیری از تنظیم اشتباه، امکان تأیید context وجود ندارد.";
   }
  } catch(error) {
   if(isCurrent()) {
    input.setCustomValidity("");
    hint.textContent="اطلاعات مدل دریافت نشد: "+error.message;
   }
  }
 }
 let hardwareDiagnosticsRequestSequence=0;
 async function refreshHardwareDiagnostics(profile=modelContextProfiles[0]) {
  const output=el("hardware-diagnostics");
  if(!output) return;
  const requestSequence=++hardwareDiagnosticsRequestSequence;
  const [purpose,modelId,contextId]=profile;
  const model=el(modelId)?.value.trim()||"";
  const context=Number(el(contextId)?.value||0);
  const provider=el(purpose+"-provider")?.value||"";
  const isCurrent=()=>requestSequence===hardwareDiagnosticsRequestSequence &&
   el(modelId)?.value.trim()===model && Number(el(contextId)?.value||0)===context &&
   el(purpose+"-provider")?.value===provider;
  try {
   const query=new URLSearchParams({purpose});
   if(model) query.set("model",model);
   if(context>0) query.set("context_tokens",String(context));
   const data=await api("/api/hardware/diagnostics?"+query.toString());
   if(!isCurrent()) return;
   const h=data.hardware||{};
   const cpu=h.cpu_utilization_percent==null?"نامشخص":h.cpu_utilization_percent+"% مصرف / "+h.cpu_available_percent+"% ظرفیت آزاد تقریبی";
   const ram=h.available_ram_gb==null?"نامشخص":h.available_ram_gb+" GiB آزاد از "+h.total_ram_gb+" GiB";
   const gpuLines=(h.gpu_devices||[]).map(g=>{
    const utilization=g.utilization_percent==null?"مصرف نامشخص":g.utilization_percent+"% مصرف";
    const dedicated=g.memory_free_mb==null?"حافظه اختصاصی آزاد نامشخص":Math.round(g.memory_free_mb/1024*10)/10+" GiB آزاد از "+(g.memory_total_mb==null?"نامشخص":Math.round(g.memory_total_mb/1024*10)/10+" GiB اختصاصی");
    const shared=g.shared_memory_total_mb==null?"حافظه اشتراکی نامشخص":(g.shared_memory_free_mb==null?"حافظه اشتراکی آزاد نامشخص":Math.round(g.shared_memory_free_mb/1024*10)/10+" GiB آزاد از "+Math.round(g.shared_memory_total_mb/1024*10)/10+" GiB اشتراکی");
    const usable=g.usable_memory_free_mb==null?"مقدار حافظه قابل‌اندازه‌گیری نامشخص":Math.round(g.usable_memory_free_mb/1024*10)/10+" GiB حافظه آزاد قابل‌اندازه‌گیری ("+(g.usable_memory_scope||"نامشخص")+")";
    return (g.vendor||"GPU")+" — "+(g.name||"نامشخص")+"؛ "+utilization+"؛ "+dedicated+"؛ "+shared+"؛ "+usable+"؛ سنجش: "+(g.telemetry||"نامشخص");
   });
   const running=(data.ollama_running_models||[]).map(item=>{
    const vram=item.size_vram_bytes==null?"VRAM تخصیص‌یافته نامشخص":(item.size_vram_bytes/1024**3).toFixed(2)+" GiB VRAM تخصیص‌یافته";
    return item.name+": context "+(item.context_length??"نامشخص")+"؛ "+vram;
   });
   const selected=data.selected_model;
   const externalProvider=data.provider==="openai_compatible";
   const estimate=selected?.memory_estimate?.total_ram_gib_float16;
   const settings=await api("/api/settings");
   if(!isCurrent()) return;
   const availableRam=h.available_ram_gb==null?NaN:Number(h.available_ram_gb);
   const budget=Number.isFinite(availableRam)
    ? Math.max(0,Math.min(availableRam,Number(settings.ram_budget_max_gb||25))-Number(settings.ram_safety_reserve_gb||4))
    : null;
   const fit=externalProvider?"مدیریت RAM و context مدل بر عهده endpoint انتخاب‌شده است؛ این داشبورد فقط سخت‌افزار میزبان برنامه را نمایش می‌دهد."
    :estimate==null?"قابلیت تناسب RAM نامشخص است؛ metadata کافی برای KV cache وجود ندارد."
    :budget==null?"تناسب RAM نامشخص است؛ سنجش RAM آزاد در دسترس نیست."
    :(estimate<=budget?"برآورد RAM در بودجه امن فعلی جا می‌شود.":"برآورد RAM از بودجه امن فعلی بیشتر است.");
   const gpuEnabled=settings.gpu_enabled===true;
   const gpuRunning=(data.ollama_running_models||[]).find(item=>item.name===model);
   const gpuStatus=externalProvider?"وضعیت GPU مدل توسط endpoint انتخاب‌شده مدیریت می‌شود و از Ollama قابل مشاهده نیست."
    :gpuRunning?.processor==="GPU"?"Ollama برای این مدل VRAM تخصیص داده است."
    :gpuRunning?.processor==="CPU"?"این مدل اکنون روی CPU اجرا می‌شود."
    :(gpuEnabled && h.gpu_available?"GPU شناسایی شده و گزینه فعال است؛ استفاده واقعی تا بارگذاری مدل تأیید نمی‌شود."
     :gpuEnabled?"GPU فعال شده اما دستگاه/درایور قابل شناسایی نیست."
     :"استفاده از GPU در تنظیمات غیرفعال است.");
   const gpuEstimate=selected?.memory_estimate?.full_gpu_vram_gib_float16;
   const gpuFreeValues=(h.gpu_devices||[]).map(g=>Object.prototype.hasOwnProperty.call(g,"usable_memory_free_mb")?g.usable_memory_free_mb:g.memory_free_mb).filter(value=>value!==null && value!==undefined && Number.isFinite(Number(value)));
   const gpuFreeGb=gpuFreeValues.length?gpuFreeValues.reduce((sum,value)=>sum+Number(value),0)/1024:null;
   const gpuMemoryComplete=(h.gpu_devices||[]).every(g=>g.usable_memory_scope==="dedicated+shared" || ["nvidia-smi","rocm-smi"].includes(g.telemetry));
   const gpuFit=gpuEstimate==null?"نیاز VRAM تخمینی نامشخص است."
    :gpuFreeGb==null?"مقدار VRAM آزاد قابل‌اندازه‌گیری نیست."
    :(gpuEstimate<=gpuFreeGb?"برآورد full-offload در حافظه اندازه‌گیری‌شده جا می‌شود."
     :gpuMemoryComplete?"برآورد full-offload از VRAM آزاد بیشتر است؛ ممکن است اجرای ترکیبی CPU/GPU یا CPU لازم شود."
     :"حافظه آزاد فقط به‌صورت جزئی اندازه‌گیری شده؛ تناسب full-offload نامشخص است.");
   const effectiveContext=data.runtime_limits?.num_ctx;
   const contextStatus=effectiveContext==null?"": "؛ context مؤثر پس از محدودیت سخت‌افزار: "+Number(effectiveContext).toLocaleString()+" توکن"+(effectiveContext<context?" (کمتر از مقدار انتخابی)": "");
   output.textContent="Provider: "+(externalProvider?"OpenAI-compatible":"Ollama")+"؛ CPU: "+h.logical_cpu_count+" thread منطقی؛ "+cpu+"؛ RAM: "+ram+"؛ GPU: "+(gpuLines.length?gpuLines.join(" | "):"هیچ GPU شناسایی نشد یا اطلاعات دستگاه در دسترس نیست.")+contextStatus
    +(externalProvider?"؛ "+fit:(running.length?"؛ تخصیص واقعی Ollama: "+running.join(" | "):"؛ مدل فعالی در Ollama گزارش نشده است."))
    +(estimate!=null?"؛ نیاز تخمینی RAM مدل در context انتخابی: "+estimate+" GiB. "+fit:"؛ "+fit)+"؛ وضعیت GPU: "+gpuStatus+(gpuEstimate!=null?"؛ VRAM لازم برای offload کامل (تخمینی): "+gpuEstimate+" GiB؛ "+gpuFit:"؛ "+gpuFit);
  } catch(error) { if(isCurrent()) output.textContent="تحلیل سخت‌افزار در دسترس نیست: "+error.message; }
 }
 for(const profile of modelContextProfiles) {
  const [purpose,modelId,contextId]=profile;
  const installedSelect=el(purpose+"-model-installed");
  const refreshProfileDiagnostics=async()=>{await refreshModelContextHint(profile);await refreshHardwareDiagnostics(profile);};
  installedSelect?.addEventListener("change",async()=>{
   const selected=installedSelect.value;
   if(!selected) return;
   el(modelId).value=selected;
   await refreshModelContextHint(profile,true);
   await refreshHardwareDiagnostics(profile);
  });
  el(modelId)?.addEventListener("input",()=>{
   if(installedSelect && installedSelect.value!==el(modelId).value.trim()) installedSelect.value="";
  });
  el(modelId)?.addEventListener("change",async()=>{await refreshModelContextHint(profile,true);await refreshHardwareDiagnostics(profile);});
  el(purpose+"-provider")?.addEventListener("change",refreshProfileDiagnostics);
  el(modelId)?.addEventListener("blur",refreshProfileDiagnostics);
  el(contextId)?.addEventListener("input",()=>{el(contextId).setCustomValidity("");});
  el(contextId)?.addEventListener("change",refreshProfileDiagnostics);
 }
 
 el("refresh-ollama-models").addEventListener("click", refreshOllamaModels);
 el("settings-form").addEventListener("submit", async event => {
  event.preventDefault();
  let learningCostProfiles;
  let externalReviewProviders;
  try {
   learningCostProfiles = JSON.parse(el("learning-cost-profiles").value || "{}");
   if (!learningCostProfiles || typeof learningCostProfiles !== "object" || Array.isArray(learningCostProfiles)) throw new Error("پروفایل هزینه باید یک شیء JSON باشد.");
   externalReviewProviders = JSON.parse(el("learning-external-review-providers").value || "[]");
   if (!Array.isArray(externalReviewProviders)) throw new Error("فهرست providerهای بیرونی باید آرایهٔ JSON باشد.");
  } catch(error) {
   el("settings-output").textContent = "JSON تنظیمات یادگیری نامعتبر است: " + error.message;
   return;
  }
  const budgetText = el("learning-task-budget").value.trim();
  const learningBudget = budgetText === "" ? null : Number(budgetText);
  if (learningBudget !== null && (!Number.isSafeInteger(learningBudget) || learningBudget < 0)) {
   el("settings-output").textContent = "بودجه باید عدد صحیح غیرمنفی میکرودلار باشد یا خالی بماند.";
   return;
  }
  const values={
   cpu_resource_limit_percent:Number(el("cpu-resource-limit").value),ram_resource_limit_percent:Number(el("ram-resource-limit").value),gpu_resource_limit_percent:Number(el("gpu-resource-limit").value),gpu_enabled:el("gpu-enabled").checked,ram_budget_max_gb:Number(el("ram-budget-max").value),ram_safety_reserve_gb:Number(el("ram-safety-reserve").value),
   chat_llm_provider:el("chat-provider").value,chat_llm_base_url:el("chat-base-url").value.trim(),chat_llm_api_key_env:el("chat-api-key-env").value.trim(),
   research_plan_llm_provider:el("research-plan-provider").value,research_plan_llm_base_url:el("research-plan-base-url").value.trim(),research_plan_llm_api_key_env:el("research-plan-api-key-env").value.trim(),research_plan_ollama_model:el("research-plan-model").value.trim(),
   learning_llm_provider:el("learning-provider").value,learning_llm_base_url:el("learning-base-url").value.trim(),learning_llm_api_key_env:el("learning-api-key-env").value.trim(),
   programming_llm_provider:el("programming-provider").value,programming_llm_base_url:el("programming-base-url").value.trim(),programming_llm_api_key_env:el("programming-api-key-env").value.trim(),
   chat_ollama_model:el("chat-model").value.trim(),chat_ollama_num_ctx:Number(el("chat-ctx").value),chat_ollama_num_thread:Number(el("chat-threads").value),chat_ollama_timeout_seconds:Number(el("chat-timeout").value),chat_max_concurrent_jobs:Number(el("chat-concurrency").value),chat_source_max_chars:Number(el("chat-source-limit").value),chat_model_keep_alive:el("chat-keep-alive").value,
   learning_model_cost_profiles:learningCostProfiles,learning_external_review_providers:externalReviewProviders,learning_model_minimum_quality_score:Number(el("learning-min-quality").value),learning_task_budget_microusd:learningBudget,
   learning_ollama_model:el("learning-model").value.trim(),learning_ollama_num_ctx:Number(el("learning-ctx").value),learning_ollama_num_thread:Number(el("learning-threads").value),learning_ollama_timeout_seconds:Number(el("learning-timeout").value),learning_max_concurrent_jobs:Number(el("learning-concurrency").value),learning_source_max_chars:Number(el("learning-source-limit").value),learning_model_keep_alive:el("learning-keep-alive").value,
   programming_ollama_model:el("programming-model").value.trim(),programming_ollama_num_ctx:Number(el("programming-ctx").value),programming_ollama_num_thread:Number(el("programming-threads").value),programming_ollama_timeout_seconds:Number(el("programming-timeout").value),programming_max_concurrent_jobs:Number(el("programming-concurrency").value),programming_source_max_chars:Number(el("programming-source-limit").value),programming_model_keep_alive:el("programming-keep-alive").value,
   factual_review_llm_provider:el("factual-review-provider").value,factual_review_llm_base_url:el("factual-review-base-url").value.trim(),factual_review_llm_api_key_env:el("factual-review-api-key-env").value.trim(),
   embedding_llm_provider:el("embedding-provider").value,embedding_llm_base_url:el("embedding-base-url").value.trim(),embedding_llm_api_key_env:el("embedding-api-key-env").value.trim(),ollama_embedding_model:el("embedding-model").value.trim(),
   factual_review_ollama_model:el("factual-review-model").value.trim()
  };
  // Preserve the old generic keys for compatibility with older internal callers.
  values.ollama_model=values.chat_ollama_model; values.ollama_num_ctx=values.chat_ollama_num_ctx; values.ollama_num_thread=values.chat_ollama_num_thread; values.ollama_timeout_seconds=values.chat_ollama_timeout_seconds;
  try {
   const temporary=el("settings-temporary").checked;
   const saved=await api("/api/settings",{method:"PATCH",body:JSON.stringify({values,temporary})});
   el("programming-context").maxLength=values.programming_source_max_chars; el("settings-output").textContent=(temporary?"تنظیمات موقت تا راه‌اندازی مجدد اعمال شد.\n":"تنظیمات به‌صورت دائمی ذخیره شد.\n")+JSON.stringify(saved,null,2);
  } catch(error) { el("settings-output").textContent="ذخیره تنظیمات ناموفق بود: "+error.message; }
 });
 
 window.dashboardSettings=Object.freeze({version:1,refresh:refreshSettings});
 refreshSettings();
})();