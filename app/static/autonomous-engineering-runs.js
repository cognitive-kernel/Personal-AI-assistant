(() => {
  "use strict";
  const core = window.autonomousEngineeringCore;
  if (!core || core.version !== 1) return;
  const { $, request, setMessage } = core;
  function renderRuns(runs) {
    const host = $("recent-runs");
    host.replaceChildren();
    if (!Array.isArray(runs) || !runs.length) {
      const empty = document.createElement("p");
      empty.className = "muted";
      empty.textContent = "اجرای ثبت‌شده‌ای پیدا نشد یا GitHub CLI دسترسی ندارد.";
      host.append(empty);
      return;
    }
    for (const run of runs) {
      const item = document.createElement("article");
      item.className = "run-item";
      const summary = document.createElement("div");
      summary.className = "run-summary";
      const heading = document.createElement("strong");
      heading.textContent = "#" + String(run.id || "—") + " · " +
        String(run.display_title || run.name || "اجرای عامل مهندسی");
      const metadata = document.createElement("span");
      metadata.textContent = String(run.status || "نامشخص") +
        (run.conclusion ? " · " + String(run.conclusion) : "") +
        (run.created_at ? " · " + String(run.created_at) : "");
      summary.append(heading, metadata);
      item.append(summary);
      if (run.failure_summary || (run.status === "completed" && run.conclusion && !["success", "neutral", "skipped"].includes(run.conclusion))) {
        const failureSummary = document.createElement("p");
        failureSummary.className = "run-failure-summary";
        const label = run.status === "completed" ? "علت خطا: " : "آخرین تشخیص: ";
        failureSummary.textContent = run.failure_summary
          ? label + String(run.failure_summary)
          : "این اجرا ناموفق بوده است؛ برای دیدن مرحله و علت ثبت‌شده، گزارش اجرا را باز کنید.";
        item.append(failureSummary);
      }
      const controls = document.createElement("div");
      controls.className = "run-controls";
      if (typeof run.url === "string" && run.url.startsWith("https://github.com/")) {
        const link = document.createElement("a");
        link.href = run.url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = "GitHub";
        controls.append(link);
      }
      if (Number.isSafeInteger(Number(run.id)) && Number(run.id) > 0) {
        const logsButton = document.createElement("button");
        logsButton.type = "button";
        logsButton.className = "secondary";
        logsButton.dataset.runAction = "logs";
        logsButton.dataset.runId = String(run.id);
        logsButton.textContent = "گزارش اجرا و علت خطا";
        controls.append(logsButton);
        if (run.status === "completed") {
          const deleteButton = document.createElement("button");
          deleteButton.type = "button";
          deleteButton.className = "danger-button";
          deleteButton.dataset.runAction = "delete";
          deleteButton.dataset.runId = String(run.id);
          deleteButton.textContent = "حذف اجرای قدیمی";
          controls.append(deleteButton);
        }
      }
      item.append(controls);
      host.append(item);
    }
  }
  let runsRequestSequence = 0;
  async function refreshRuns() {
    const requestSequence = ++runsRequestSequence;
    try {
      const data = await request("runs");
      if (requestSequence !== runsRequestSequence) return true;
      renderRuns(data.runs);
      return true;
    } catch (error) {
      if (requestSequence !== runsRequestSequence) return;
      const host = $("recent-runs");
      const note = document.createElement("p");
      note.className = "muted";
      note.textContent = "دریافت تاریخچهٔ کامل اجراها ناموفق بود: " + (error.message || "خطای نامشخص");
      host.replaceChildren(note);
      return false;
    }
  }

  $("recent-runs").addEventListener("click", async (event) => {
    const button = event.target.closest("button[data-run-action]");
    if (!button || button.disabled) return;
    const runId = button.dataset.runId;
    if (!Number.isSafeInteger(Number(runId)) || Number(runId) <= 0) return;
    if (button.dataset.runAction === "logs") {
      const item = button.closest(".run-item");
      if (!item) return;
      let panel = item.querySelector(".run-log-panel");
      if (!panel) {
        panel = document.createElement("section");
        panel.className = "run-log-panel";
        const diagnosis = document.createElement("section");
        diagnosis.className = "run-diagnosis";
        const diagnosisHeading = document.createElement("h3");
        diagnosisHeading.textContent = "علت این اجرا";
        const diagnosisSummary = document.createElement("p");
        diagnosisSummary.className = "run-diagnosis-summary";
        const diagnosisStage = document.createElement("p");
        diagnosisStage.className = "run-diagnosis-stage";
        const diagnosisEntries = document.createElement("div");
        diagnosisEntries.className = "run-diagnostic-entries";
        diagnosis.append(diagnosisHeading, diagnosisSummary, diagnosisStage, diagnosisEntries);
        const failuresHeading = document.createElement("h4");
        failuresHeading.textContent = "مرحله‌های ناموفق GitHub";
        const failures = document.createElement("ul");
        failures.className = "run-failures";
        const rawDetails = document.createElement("details");
        rawDetails.className = "run-raw-logs";
        const rawSummary = document.createElement("summary");
        rawSummary.textContent = "گزارش خام GitHub";
        const pre = document.createElement("pre");
        pre.className = "run-log-text";
        rawDetails.append(rawSummary, pre);
        panel.append(diagnosis, failuresHeading, failures, rawDetails);
        item.append(panel);
      }
      panel.hidden = false;
      const pre = panel.querySelector(".run-log-text");
      const failures = panel.querySelector(".run-failures");
      const diagnosisSummary = panel.querySelector(".run-diagnosis-summary");
      const diagnosisStage = panel.querySelector(".run-diagnosis-stage");
      const diagnosisEntries = panel.querySelector(".run-diagnostic-entries");
      diagnosisSummary.textContent = "در حال استخراج علت از گزارش Runner و GitHub…";
      diagnosisStage.textContent = "";
      diagnosisEntries.replaceChildren();
      pre.textContent = "در حال دریافت گزارش خام از GitHub…";
      failures.replaceChildren();
      button.disabled = true;
      try {
        const data = await request("runs/" + runId + "/logs");
        diagnosisSummary.textContent = data.error_summary || (
          data.run && data.run.status !== "completed"
            ? "اجرا هنوز پایان نیافته است؛ پس از پایان، گزارش نهایی علت دقیق‌تری ارائه می‌کند."
            : "علت اختصاصی از گزارش قابل استخراج نبود؛ مرحله‌های ناموفق و گزارش خام را بررسی کنید."
        );
        diagnosisStage.textContent = data.failure_stage
          ? "مرحله: " + String(data.failure_stage)
          : "";
        const diagnostic = data.diagnostic && Array.isArray(data.diagnostic.diagnostics)
          ? data.diagnostic.diagnostics
          : [];
        if (diagnostic.length) {
          for (const detail of diagnostic) {
            const entry = document.createElement("article");
            entry.className = "run-diagnostic-entry";
            const metadata = document.createElement("strong");
            metadata.textContent = [
              detail.stage || "مرحله نامشخص",
              detail.task ? "وظیفه " + detail.task : "",
              detail.attempt ? "تلاش " + detail.attempt : "",
              detail.recorded_at || ""
            ].filter(Boolean).join(" · ");
            const summary = document.createElement("p");
            summary.textContent = detail.summary || "جزئیات علت ثبت نشده است.";
            entry.append(metadata, summary);
            if (detail.detail) {
              const detailDisclosure = document.createElement("details");
              const detailLabel = document.createElement("summary");
              detailLabel.textContent = "گزارش توضیحی Codex / جزئیات خطا";
              const detailText = document.createElement("pre");
              detailText.className = "run-diagnostic-detail";
              detailText.textContent = String(detail.detail);
              detailDisclosure.append(detailLabel, detailText);
              entry.append(detailDisclosure);
            }
            diagnosisEntries.append(entry);
          }
        } else if (data.error_summary && /TASK_BLOCKED|blocked/i.test(data.error_summary)) {
          const note = document.createElement("p");
          note.className = "run-diagnostic-note";
          note.textContent = "برای این اجرای قدیمی، متن توضیحی نهایی Codex در فضای پایدار ذخیره نشده بود؛ فقط پیام انسداد در لاگ GitHub موجود است. اجراهای جدید گزارش توضیحی را جداگانه نگه می‌دارند.";
          diagnosisEntries.append(note);
        }
        for (const job of data.failures || []) {
          const entry = document.createElement("li");
          const failedSteps = Array.isArray(job.failed_steps)
            ? job.failed_steps.map(step => step.name + " (" + step.conclusion + ")").join("، ")
            : "";
          entry.textContent = job.name + " · " + String(job.conclusion || job.status || "نامشخص") +
            (failedSteps ? " · مراحل ناموفق: " + failedSteps : "");
          failures.append(entry);
        }
        if (!failures.childElementCount) {
          const entry = document.createElement("li");
          entry.textContent = "برای این اجرا مرحلهٔ ناموفقی در گزارش GitHub ثبت نشده است.";
          failures.append(entry);
        }
        pre.textContent = data.logs || "گزارش خامی برای نمایش وجود ندارد.";
      } catch (error) {
        pre.textContent = error.message || "دریافت گزارش اجرا ناموفق بود.";
      } finally {
        button.disabled = false;
      }
      return;
    }
    if (button.dataset.runAction === "delete") {
      if (!window.confirm("اجرای پایان‌یافتهٔ #" + runId + " از تاریخچهٔ GitHub حذف شود؟ این عملیات قابل بازگشت نیست.")) return;
      button.disabled = true;
      try {
        const result = await request("runs/" + runId, {method: "DELETE"});
        setMessage(result.message || "اجرای قدیمی حذف شد.", "success");
        await refreshRuns();
      } catch (error) {
        setMessage(error.message || "حذف اجرای قدیمی ناموفق بود.", "error");
        button.disabled = false;
      }
    }
  });

  $("refresh-runs").addEventListener("click", refreshRuns);

  $("cleanup-agent-temp").addEventListener("click", async () => {
    const button = $("cleanup-agent-temp");
    const status = $("cleanup-agent-status");
    if (!window.confirm("داده‌های موقت پیشرفت عامل پاک شوند؟ تاریخچه، checkpointها و وظایف/PRهای پایدار حفظ می‌شوند؛ هنگام اجرای فعال پاک‌سازی انجام نمی‌شود.")) return;
    button.disabled = true;
    status.textContent = "در حال بررسی اجرای فعال و وضعیت پایدار…";
    try {
      const result = await request("cleanup", {method: "POST"});
      status.textContent = result.message || ("پاک‌سازی انجام شد؛ " + result.files_removed + " فایل حذف شد.");
      setMessage(status.textContent, "success");
      window.dispatchEvent(new Event("autonomous-engineering:cleanup-completed"));
    } catch (error) {
      status.textContent = error.message || "پاک‌سازی ناموفق بود.";
      setMessage(status.textContent, "error");
    } finally {
      button.disabled = false;
    }
  });


  window.addEventListener("autonomous-engineering:status-mutated", () => { void refreshRuns(); });
  window.autonomousEngineeringRuns = Object.freeze({version: 1, refresh: refreshRuns});
})();
