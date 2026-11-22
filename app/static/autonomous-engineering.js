(() => {
  "use strict";
  if (window.autonomousEngineeringApp && window.autonomousEngineeringApp.version === 1) return;
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
  if (!status || status.version !== 1) missing.push("وضعیت");
  if (!controls || controls.version !== 1) missing.push("کنترل");
  if (!runs || runs.version !== 1) missing.push("تاریخچه اجرا");
  if (!progress || progress.version !== 1) missing.push("پیشرفت");

  const stopPollers = [];
  function stopPolling() {
    while (stopPollers.length) {
      const stop = stopPollers.pop();
      try { stop(); } catch (_) {}
    }
  }
  function startPolling() {
    if (stopPollers.length) return;
    if (status && status.version === 1) {
      stopPollers.push(core.startPolling({
        intervalMs: 30000, maxIntervalMs: 300000,
        refresh: () => status.refresh()
      }));
    }
    if (runs && runs.version === 1) {
      stopPollers.push(core.startPolling({
        intervalMs: 30000, maxIntervalMs: 300000,
        refresh: () => runs.refresh()
      }));
    }
    if (progress && progress.version === 1) {
      stopPollers.push(core.startPolling({
        intervalMs: 5000, maxIntervalMs: 120000,
        refresh: () => progress.refresh()
      }));
    }
  }

  if (missing.length) showModuleWarning("برخی قابلیت‌های کنسول بارگذاری نشدند: " + missing.join("، "));
  function onPageHide() {
    stopPolling();
  }
  function onPageShow(event) {
    if (event.persisted) startPolling();
  }
  startPolling();
  window.addEventListener("pagehide", onPageHide);
  window.addEventListener("pageshow", onPageShow);
  window.autonomousEngineeringApp = Object.freeze({
    version: 1,
    stop() {
      stopPolling();
      window.removeEventListener("pagehide", onPageHide);
      window.removeEventListener("pageshow", onPageShow);
    }
  });
})();
