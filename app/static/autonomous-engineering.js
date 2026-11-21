(() => {
  "use strict";
 function createLocalAutonomousEngineeringCore() {
 const $ = id => document.getElementById(id);
 const message = $("agent-message");
 function setMessage(text, kind) {
  if (!message) return;
  message.textContent = text;
  message.dataset.kind = kind || "";
 }
 async function request(path, options) {
  const controller = new AbortController();
  const timeoutMs = path === "status" ? 40000 : path.startsWith("progress") ? 20000 : 120000;
  const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
   const response = await fetch("/api/autonomous-engineering/" + path, {
    ...(options || {}),
    headers: {
     "Accept": "application/json",
     ...(options && options.body ? {"Content-Type": "application/json"} : {}),
     ...(options && options.headers || {})
    },
    signal: controller.signal
   });
   let payload = {};
   try { payload = await response.json(); } catch (_) {}
   if (!response.ok) {
    const failure = new Error(payload.detail || "درخواست ناموفق بود.");
    failure.status = response.status;
    throw failure;
   }
   return payload;
  } catch (error) {
   if (error && error.name === "AbortError") {
    const timeoutError = new Error("پاسخ سرویس در مهلت " + (timeoutMs / 1000) + " ثانیه دریافت نشد؛ وضعیت سرور و لاگ‌ها را بررسی کنید.");
    timeoutError.ambiguous = true;
    throw timeoutError;
   }
   if (error && !Number.isInteger(error.status)) error.ambiguous = true;
   throw error;
  } finally {
   window.clearTimeout(timeoutId);
  }
 }
 return Object.freeze({version: 1, $, request, setMessage});
}

  const core = window.autonomousEngineeringCore && window.autonomousEngineeringCore.version === 1 ? window.autonomousEngineeringCore : createLocalAutonomousEngineeringCore();
  const status = window.autonomousEngineeringStatus;
  const controls = window.autonomousEngineeringControls;
  const runs = window.autonomousEngineeringRuns;
  const progress = window.autonomousEngineeringProgress;
  if (!core || core.version !== 1) {
    const message = document.getElementById("agent-message");
    if (message) { message.textContent = "ماژول زیرساخت کنسول مهندسی بارگذاری نشد."; message.dataset.kind = "error"; }
    return;
  }
  const missing = [];
  if (status && status.version === 1) {
    void status.refresh(); window.setInterval(() => void status.refresh(), 30000);
  } else missing.push("وضعیت");
  if (!controls || controls.version !== 1) missing.push("کنترل");
  if (runs && runs.version === 1) {
    void runs.refresh(); window.setInterval(() => void runs.refresh(), 30000);
  } else missing.push("تاریخچه اجرا");
  if (progress && progress.version === 1) {
    void progress.refresh(); window.setInterval(() => void progress.refresh(), 5000);
  } else missing.push("پیشرفت");
  if (missing.length) core.setMessage("برخی قابلیت‌های کنسول بارگذاری نشدند: " + missing.join("، "), "error");
})();
