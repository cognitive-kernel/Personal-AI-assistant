(() => {
  "use strict";
  function showModuleWarning(message) {
    const host = document.getElementById("agent-message");
    if (!host) return;
    let warning = document.getElementById("agent-module-warning");
    if (!warning) {
      warning = document.createElement("p");
      warning.id = "agent-module-warning";
      warning.className = "notice";
      warning.setAttribute("role", "alert");
      warning.setAttribute("aria-live", "assertive");
      host.insertAdjacentElement("afterend", warning);
    }
    warning.textContent = message;
  }
  const core = window.autonomousEngineeringCore;
  const status = window.autonomousEngineeringStatus;
  const controls = window.autonomousEngineeringControls;
  const runs = window.autonomousEngineeringRuns;
  const progress = window.autonomousEngineeringProgress;
  if (!core || core.version !== 1) {
    showModuleWarning("ماژول زیرساخت کنسول مهندسی بارگذاری نشد.");
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
  if (missing.length) showModuleWarning("برخی قابلیت‌های کنسول بارگذاری نشدند: " + missing.join("، "));
})();
