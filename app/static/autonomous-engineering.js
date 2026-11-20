(() => {
  "use strict";
  const core = window.autonomousEngineeringCore;
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
