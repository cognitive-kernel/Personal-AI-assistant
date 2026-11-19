(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const message = $("agent-message");
  const dispatchButton = $("dispatch-agent");
  const cancelButton = $("cancel-agent");
  const pauseButton = $("pause-agent");
  const resumeButton = $("resume-agent");
  const saveButton = $("save-agent-settings");
  const settingsForm = $("agent-settings-form");
  const taskForm = $("agent-task-form");
  const taskButton = $("send-agent-task");
  let lastPendingWork = null;
  const labels = {
    ollama: $("ollama-state"), model: $("model-state"), codex: $("codex-state"),
    runner: $("runner-state"), github: $("github-state"), storage: $("storage-state")
  };
  function setMessage(text, kind) {
    message.textContent = text;
    message.dataset.kind = kind || "";
  }
  function stateText(ok) { return ok ? "آماده" : "نیازمند راه‌اندازی"; }
  function renderModels(models, selectedModel, selectedModelIsContextVariant) {
    const select = $("ollama-model");
    if (document.activeElement === select) return;
    const installed = [...new Set(
      Array.isArray(models) ? models.filter(name => typeof name === "string" && name.trim()) : []
    )];
    const savedModelUnavailable = Boolean(selectedModel) && (!installed.includes(selectedModel) || selectedModelIsContextVariant);
    const names = [...installed];
    if (savedModelUnavailable) names.unshift(selectedModel);
    select.replaceChildren();
    if (!installed.length && !savedModelUnavailable) {
      const option = document.createElement("option");
      option.value = "";
      option.disabled = true;
      option.textContent = "هیچ مدل اصلی قابل انتخابی از Ollama شناسایی نشد";
      select.append(option);
      select.value = "";
      return;
    }
    if (!selectedModel || savedModelUnavailable) {
      const placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.disabled = true;
      placeholder.selected = true;
      placeholder.textContent = selectedModelIsContextVariant
        ? "مدل ذخیره‌شده نسخهٔ داخلی Context است؛ مدل اصلی را انتخاب کنید"
        : savedModelUnavailable
          ? "مدل ذخیره‌شده نصب نیست؛ مدل دیگری انتخاب کنید"
          : "ابتدا مدل نصب‌شده را انتخاب کنید";
      select.append(placeholder);
    }
    for (const name of names) {
      const option = document.createElement("option");
      option.value = name;
      option.textContent = name + (savedModelUnavailable && name === selectedModel
        ? (selectedModelIsContextVariant ? " (نسخهٔ داخلی؛ قابل انتخاب نیست)" : " (ذخیره‌شده؛ نصب نیست)") : "");
      if (savedModelUnavailable && name === selectedModel) {
        option.dataset.installed = "false";
        option.disabled = true;
      }
      select.append(option);
    }
    select.value = selectedModel && installed.includes(selectedModel) && !selectedModelIsContextVariant ? selectedModel : "";
  }
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
  let runsRequestSequence = 0;
  async function refreshRuns() {
    const requestSequence = ++runsRequestSequence;
    try {
      const data = await request("runs");
      if (requestSequence !== runsRequestSequence) return;
      renderRuns(data.runs);
    } catch (error) {
      if (requestSequence !== runsRequestSequence) return;
      const host = $("recent-runs");
      const note = document.createElement("p");
      note.className = "muted";
      note.textContent = "دریافت تاریخچهٔ کامل اجراها ناموفق بود: " + (error.message || "خطای نامشخص");
      host.replaceChildren(note);
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
      await refreshProgress();
    } catch (error) {
      status.textContent = error.message || "پاک‌سازی ناموفق بود.";
      setMessage(status.textContent, "error");
    } finally {
      button.disabled = false;
    }
  });

  let statusRefreshPromise = null;
  let dispatchPendingUntil = 0;
  let dispatchSubmitting = false;
  let lastCanDispatch = false;
  let lastActiveRun = false;
  let statusKnown = false;
  function canStartDispatch() {
    return statusKnown && lastCanDispatch && !lastActiveRun && !dispatchSubmitting && Date.now() >= dispatchPendingUntil;
  }
  function canStartManualTask() {
    const pending = lastPendingWork;
    const checkpoint = pending && pending.checkpoint && typeof pending.checkpoint === "object"
      ? pending.checkpoint
      : null;
    return canStartDispatch() &&
      pending && pending.available === true &&
      pending.blocked !== true &&
      !(checkpoint && checkpoint.available === true);
  }
  function syncDispatchControls() {
    const allowed = canStartDispatch();
    const pending = lastPendingWork;
    const checkpoint = pending && pending.checkpoint && typeof pending.checkpoint === "object"
      ? pending.checkpoint
      : null;
    const checkpointNeedsReview = Boolean(
      checkpoint && checkpoint.available === true && checkpoint.verified !== true
    );
    dispatchButton.disabled = !allowed || checkpointNeedsReview;
    taskButton.disabled = !canStartManualTask() || checkpointNeedsReview;
  }
  function lockDispatchVisibilityWindow() {
    dispatchPendingUntil = Date.now() + 30000;
    window.setTimeout(syncDispatchControls, 30000);
  }
  function refresh(force = false) {
    if (force) return refreshAfterMutation();
    if (statusRefreshPromise) return statusRefreshPromise;
    let current;
    current = refreshStatus().finally(() => {
      if (statusRefreshPromise === current) statusRefreshPromise = null;
    });
    statusRefreshPromise = current;
    return current;
  }
  async function refreshAfterMutation() {
    // Wait for any status request that began before the mutation to settle.
    // Only then reserve a new single-flight request, so stale snapshots can
    // never satisfy the post-mutation refresh.
    const previous = statusRefreshPromise;
    if (previous) {
      try {
        await previous;
      } catch (_) {
        // A failed pre-mutation poll must not prevent authoritative re-reading.
      }
    }
    let current;
    current = refreshStatus().finally(() => {
      if (statusRefreshPromise === current) statusRefreshPromise = null;
    });
    statusRefreshPromise = current;
    return current;
  }
  async function refreshStatus() {
    $("refresh-agent").disabled = true;
    try {
      const data = await request("status");
      const checks = data.checks || {};
      labels.ollama.textContent = stateText(checks.ollama_online);
      $("ollama-detail").textContent = checks.ollama_online ? "پاسخ از 127.0.0.1:11434 دریافت شد." : "سرویس محلی در دسترس نیست.";
      const modelReady = checks.model_installed && checks.model_selectable && checks.model_context_metadata_available && checks.model_context_supported && checks.model_supports_tools;
      labels.model.textContent = stateText(modelReady);
      const contextLength = Number(data.model_context_length);
      const recommendedContext = Number(data.model_recommended_context_length);
      const contextText = Number.isSafeInteger(contextLength) && contextLength > 0
        ? "؛ حداکثر Context مدل: " + contextLength.toLocaleString() + " توکن" +
          (Number.isSafeInteger(recommendedContext) && recommendedContext > 0
            ? "؛ Context امن متناسب با سخت‌افزار: " + recommendedContext.toLocaleString() + " توکن"
            : "؛ ظرفیت امن متناسب با سخت‌افزار قابل محاسبه نیست")
        : "؛ حداکثر Context از metadata مدل تأیید نشد";
      const toolsText = checks.model_supports_tools
        ? (data.model_tools_verification_method === "live_tool_call_probe"
          ? "؛ قابلیت tools با آزمون واقعی تأیید شد"
          : "؛ قابلیت tools در metadata مدل تأیید شد")
        : checks.model_tools_metadata_available
          ? "؛ مدل قابلیت tools را اعلام نکرده است"
          : data.model_tools_verification_method === "skipped_no_safe_context"
            ? "؛ آزمون tools تا تأیید ظرفیت امن Context اجرا نشد"
            : data.model_tools_verification_method === "probe_required"
              ? "؛ برای تأیید واقعی قابلیت tools، مدل را از بخش تنظیمات ذخیره/آزمایش کنید"
              : data.model_tools_verification_method === "probe_unavailable"
                ? "؛ ابزار بررسی زنده قابلیت tools در دسترس نیست"
                : "؛ metadata موجود نیست و آزمون واقعی tools نیز موفق نشد";
      const modelLabel = data.model ? "مدل ذخیره‌شده: " + String(data.model) : "هنوز مدلی ذخیره نشده است";
      const installationText = data.model && checks.model_is_codex_context_variant
        ? "؛ این نسخهٔ داخلی Context است؛ مدل اصلی را انتخاب و ذخیره کنید"
        : data.model && !checks.model_installed ? "؛ مدل ذخیره‌شده روی این سیستم نصب نیست" : "";
      $("model-detail").textContent = modelLabel + installationText + contextText + toolsText;
      renderModels(data.selectable_models || data.models, data.model, checks.model_is_codex_context_variant);
      const executionMode = data.execution_mode || "continuous";
      if (document.activeElement !== $("execution-mode")) {
        $("execution-mode").value = executionMode;
      }
      dispatchButton.textContent = executionMode === "continuous"
        ? "شروع صف پیوسته"
        : "اجرای یک چرخهٔ زمان‌بندی‌شده";
      if (document.activeElement !== $("interval-hours")) {
        $("interval-hours").value = String(data.interval_hours || 24);
      }
      labels.codex.textContent = checks.codex_installed ? "موجود" : "در حساب برنامه نصب نیست";
      $("codex-detail").textContent = data.codex && data.codex.version
        ? data.codex.version + "؛ این بررسی فقط حساب برنامه را پوشش می‌دهد."
        : "حساب Runner جداگانه و هنگام اجرای Workflow بررسی می‌شود.";
      labels.runner.textContent = stateText(checks.runner_running);
      $("runner-detail").textContent = checks.runner_running ? "فرایند Runner.Listener فعال است." : "Runner محلی فعال نیست.";
      const githubReady = checks.github_cli_authenticated && checks.github_runs_available;
      labels.github.textContent = stateText(githubReady);
      $("github-detail").textContent = !checks.github_cli_authenticated
        ? "gh auth login لازم است."
        : !checks.github_runs_available
          ? "دریافت وضعیت اجراهای GitHub ناموفق است؛ دسترسی Actions را بررسی کنید."
          : "GitHub CLI احراز هویت شده و وضعیت اجراها قابل دریافت است.";
      labels.storage.textContent = stateText(checks.progress_storage_accessible);
      $("storage-detail").textContent = checks.progress_storage_accessible
        ? "سرویس Broker محلی آماده است؛ حساب برنامه از کانال محدود محلی استفاده می‌کند و به مجوز مستقیم پوشه نیاز ندارد."
        : "سرویس Broker فضای پیشرفت آماده نیست. برنامه هنگام شروع برای نصب آن درخواست مجوز مدیر می‌کند؛ در صورت ردشدن UAC یا نبود Runner، اسکریپت ensure_autonomous_state_broker.ps1 را از PowerShell مدیر اجرا کنید.";
      lastPendingWork = data.pending_work && typeof data.pending_work === "object"
        ? data.pending_work
        : {available: false, blocked: true, checkpoint: {available: true, verified: false}};
      const checkpoint = lastPendingWork.checkpoint && typeof lastPendingWork.checkpoint === "object"
        ? lastPendingWork.checkpoint
        : null;
      const checkpointState = $("checkpoint-state");
      const checkpointDetail = $("checkpoint-detail");
      if (!lastPendingWork.available) {
        checkpointState.textContent = "قابل بررسی نیست";
        checkpointDetail.textContent = "سرویس وضعیت پایدار پاسخ معتبر نداد؛ شروع هر چرخه تا بازیابی اتصال متوقف است.";
      } else if (checkpoint && checkpoint.available === true && checkpoint.verified === true) {
        checkpointState.textContent = "ذخیره‌شده و قابل بازیابی";
        checkpointDetail.textContent = "وظیفهٔ " + String(checkpoint.task || 1) +
          " از اجرای " + String(checkpoint.run_id || "نامشخص") +
          "؛ " + Number(checkpoint.patch_bytes || 0).toLocaleString() +
          " بایت تغییر ذخیره‌شده. اجرای بدون متن، همین وظیفه را ادامه می‌دهد.";
      } else if (checkpoint && checkpoint.available === true) {
        checkpointState.textContent = "نیازمند بررسی";
        checkpointDetail.textContent = "وجود checkpoint تأیید شد اما تطبیق فراداده و patch موفق نبود؛ اجرای خودکار و پذیرش کار جدید مسدود است.";
      } else if (lastPendingWork.blocked) {
        checkpointState.textContent = "کار قبلی معلق است";
        checkpointDetail.textContent = "یک وظیفه، PR یا عملیات پایدار قبلی تعیین‌تکلیف نشده است؛ کار جدید تا تعیین‌تکلیف آن پذیرفته نمی‌شود.";
      } else {
        checkpointState.textContent = "checkpoint معلقی وجود ندارد";
        checkpointDetail.textContent = "فضای وضعیت بررسی شد و checkpoint بازیابی‌نشده‌ای ثبت نشده است.";
      }
      $("manual-task-state").textContent = lastPendingWork.blocked
        ? "کار پایدار قبلی باقی مانده است؛ ارسال وظیفهٔ جدید غیرفعال است. دکمهٔ اجرای چرخه بدون متن، مسیر ادامه یا تطبیق همان کار است."
        : "وظیفهٔ جدید فقط وقتی پذیرفته می‌شود که هیچ task، PR یا checkpoint قبلی باقی نمانده باشد.";
      if (checkpoint && checkpoint.available === true) {
        dispatchButton.textContent = checkpoint.verified === true
          ? "ادامهٔ وظیفهٔ ذخیره‌شده"
          : "checkpoint نیازمند بررسی است";
      } else if (lastPendingWork.blocked) {
        dispatchButton.textContent = "ادامه از وضعیت قبلی";
      } else {
        dispatchButton.textContent = executionMode === "continuous"
          ? "شروع صف پیوسته"
          : "اجرای یک چرخهٔ زمان‌بندی‌شده";
      }
      const ready = data.ready === true;
      const paused = data.paused === true;
      const canDispatch = data.can_dispatch === undefined ? (ready && !paused) : data.can_dispatch === true;
      const pill = $("agent-readiness");
      pill.textContent = paused ? "توقف ایمن فعال است" : (ready ? "آمادهٔ ارسال Workflow" : "راه‌اندازی ناقص");
      pill.className = "status-pill " + (paused ? "status-blocked" : (ready ? "status-ready" : "status-blocked"));
      const active = (data.github && data.github.recent_runs || []).some(run =>
        ["queued", "in_progress", "waiting", "requested", "pending"].includes(run.status));
      if (active) dispatchPendingUntil = 0;
      lastCanDispatch = canDispatch;
      lastActiveRun = active;
      statusKnown = true;
      syncDispatchControls();
      cancelButton.disabled = !active;
      pauseButton.disabled = Boolean(data.paused);
      resumeButton.disabled = !data.paused;
      renderRuns(data.github && data.github.recent_runs);
      void refreshRuns();
      setMessage(data.paused ? "صف مهندسی متوقف است؛ برای ادامه از آخرین وضعیت، دکمهٔ ادامه را بزنید." : (ready ? "پیش‌نیازهای حساب برنامه آماده‌اند؛ Workflow پیش‌نیازهای حساب Runner را جداگانه بررسی می‌کند." : "برای فعال‌شدن اجرا، موارد ناقص را در راهنمای راه‌اندازی تکمیل کنید."), data.paused || ready ? "success" : "");
    } catch (error) {
      statusKnown = false;
      lastPendingWork = null;
      setMessage(error.message || "دریافت وضعیت با خطا مواجه شد.", "error");
      syncDispatchControls();
      dispatchButton.disabled = true;
      taskButton.disabled = true;
      cancelButton.disabled = true;
      pauseButton.disabled = true;
      resumeButton.disabled = true;
    } finally {
      $("refresh-agent").disabled = false;
    }
  }
  let settingsSaving = false;
  settingsForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (settingsSaving) return;
    settingsSaving = true;
    saveButton.disabled = true;
    const customModel = $("custom-model").value.trim();
    const model = customModel || $("ollama-model").value;
    const intervalHours = Number($("interval-hours").value);
    const executionMode = $("execution-mode").value;
    setMessage("در حال ذخیرهٔ مدل و حالت اجرای عامل در تنظیمات مخزن GitHub…");
    try {
      const result = await request("settings", {
        method: "POST",
        body: JSON.stringify({model, interval_hours: intervalHours, execution_mode: executionMode})
      });
      $("custom-model").value = "";
      setMessage(result.message || "تنظیمات ذخیره شد.", "success");
      await refresh(true);
    } catch (error) {
      const detail = error.message || "ذخیرهٔ تنظیمات ناموفق بود.";
      await refresh(true);
      setMessage(detail, "error");
    } finally {
      saveButton.disabled = false;
      settingsSaving = false;
    }
  });
  const pullModelButton = $("pull-model");
  const pullModelStatus = $("model-pull-status");
  let modelPullActive = false;
  async function monitorModelPull(jobId) {
    while (modelPullActive) {
      const state = await request("models/pull/" + encodeURIComponent(jobId));
      const percent = Number.isSafeInteger(state.progress) ? " · " + state.progress + "%" : "";
      pullModelStatus.textContent = String(state.message || "در حال دریافت مدل…") + percent;
      if (state.status === "completed") {
        modelPullActive = false;
        pullModelStatus.textContent = "مدل " + state.model + " با موفقیت نصب شد؛ در حال بازخوانی فهرست مدل‌ها…";
        setMessage(pullModelStatus.textContent, "success");
        await refresh(true);
        return;
      }
      if (state.status === "failed") {
        modelPullActive = false;
        throw new Error(state.error || state.message || "دانلود مدل ناموفق بود.");
      }
      await new Promise(resolve => window.setTimeout(resolve, 1500));
    }
  }
  pullModelButton.addEventListener("click", async () => {
    if (modelPullActive) return;
    const model = $("custom-model").value.trim() || $("ollama-model").value;
    if (!model) {
      pullModelStatus.textContent = "ابتدا شناسهٔ مدل را وارد یا از فهرست انتخاب کنید.";
      return;
    }
    if (!window.confirm("مدل «" + model + "» از طریق سرویس محلی Ollama دریافت شود؟ حجم دانلود ممکن است زیاد باشد.")) return;
    modelPullActive = true;
    pullModelButton.disabled = true;
    pullModelStatus.textContent = "در حال ایجاد عملیات دانلود مدل…";
    try {
      const job = await request("models/pull", {
        method: "POST",
        body: JSON.stringify({model})
      });
      await monitorModelPull(job.job_id);
    } catch (error) {
      modelPullActive = false;
      pullModelStatus.textContent = error.message || "دانلود مدل ناموفق بود.";
      setMessage(pullModelStatus.textContent, "error");
    } finally {
      pullModelButton.disabled = false;
    }
  });
  taskForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!canStartManualTask()) {
      setMessage(
        lastPendingWork && lastPendingWork.blocked
          ? "کار پایدار قبلی یا checkpoint هنوز تعیین‌تکلیف نشده است؛ برای ادامه، از دکمهٔ اجرای بدون متن استفاده کنید."
          : "وضعیت پایدار برای ارسال وظیفهٔ جدید آماده نیست؛ پس از به‌روزرسانی وضعیت دوباره تلاش کنید.",
        "error"
      );
      return;
    }
    const task = $("manual-task").value.trim();
    if (!task) {
      setMessage("ابتدا متن وظیفه را وارد کنید.", "error");
      return;
    }
    if (task.length > 2000 || task.includes(String.fromCharCode(0))) {
      setMessage("متن وظیفه نامعتبر یا طولانی است.", "error");
      return;
    }
    dispatchSubmitting = true;
    taskButton.disabled = true;
    dispatchButton.disabled = true;
    setMessage("در حال ارسال وظیفه به عامل محلی و درخواست شروع چرخه…");
    try {
      const result = await request("dispatch", {
        method: "POST",
        body: JSON.stringify({task})
      });
      lockDispatchVisibilityWindow();
      $("manual-task").value = "";
      setMessage(result.message || "وظیفه برای عامل ارسال شد.", "success");
      await refresh(true);
    } catch (error) {
      if (error.ambiguous || error.status >= 500) lockDispatchVisibilityWindow();
      const detail = error.message || "ارسال وظیفه ناموفق بود.";
      await refresh(true);
      setMessage(detail, "error");
    } finally {
      dispatchSubmitting = false;
      syncDispatchControls();
    }
  });
  dispatchButton.addEventListener("click", async () => {
    if (!canStartDispatch()) return;
    dispatchSubmitting = true;
    dispatchButton.disabled = true;
    taskButton.disabled = true;
    setMessage("در حال ارسال درخواست اجرای Workflow به GitHub…");
    try {
      const result = await request("dispatch", {method: "POST"});
      lockDispatchVisibilityWindow();
      setMessage(result.message || "درخواست ارسال شد.", "success");
    } catch (error) {
      if (error.ambiguous || error.status >= 500) lockDispatchVisibilityWindow();
      setMessage(error.message || "ارسال درخواست ناموفق بود.", "error");
    } finally {
      await refresh(true);
      dispatchSubmitting = false;
      syncDispatchControls();
    }
  });
  pauseButton.addEventListener("click", async () => {
    pauseButton.disabled = true;
    try {
      const result = await request("pause", {method: "POST"});
      setMessage(result.message || "توقف ایمن ثبت شد.", "success");
      await refresh(true);
    } catch (error) {
      const detail = error.message || "توقف ایمن ثبت نشد.";
      await refresh(true);
      setMessage(detail, "error");
    }
  });
  resumeButton.addEventListener("click", async () => {
    resumeButton.disabled = true;
    try {
      const result = await request("resume", {method: "POST"});
      lockDispatchVisibilityWindow();
      setMessage(result.message || "ادامهٔ صف درخواست شد.", "success");
      await refresh(true);
    } catch (error) {
      if (error.ambiguous || error.status >= 500) lockDispatchVisibilityWindow();
      const detail = error.message || "ادامهٔ صف ثبت نشد.";
      await refresh(true);
      setMessage(detail, "error");
    }
  });
  cancelButton.addEventListener("click", async () => {
    if (!window.confirm("اجرای فعال عامل در GitHub Actions لغو شود؟")) return;
    cancelButton.disabled = true;
    try {
      const result = await request("cancel", {method: "POST"});
      setMessage(result.message || "درخواست لغو ارسال شد.", "success");
      await refresh(true);
    } catch (error) {
      const detail = error.message || "لغو اجرای فعال ناموفق بود.";
      await refresh(true);
      setMessage(detail, "error");
    }
  });

  let progressBusy = false;
  let progressRefreshQueued = false;
  const svgNS = "http://www.w3.org/2000/svg";
  function svgNode(name, attrs, text) {
    const node = document.createElementNS(svgNS, name);
    for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, String(value));
    if (text !== undefined) node.textContent = String(text);
    return node;
  }
  function renderProgressChart(points) {
    const chart = $("progress-chart");
    chart.replaceChildren();
    const width = 800, height = 260, left = 58, right = 24, top = 18, bottom = 42;
    const plotWidth = width - left - right, plotHeight = height - top - bottom;
    for (const value of [0, 25, 50, 75, 100]) {
      const y = top + plotHeight * (1 - value / 100);
      chart.append(svgNode("line", {x1:left,y1:y,x2:width-right,y2:y,class:"chart-grid"}));
      chart.append(svgNode("text", {x:left-12,y:y+4,"text-anchor":"end",class:"chart-axis"}, value + "%"));
    }
    if (!Array.isArray(points) || !points.length) {
      $("progress-chart-caption").textContent = "برای این بازه هنوز snapshot ثبت‌شده‌ای وجود ندارد.";
      return;
    }
    const xAt = index => points.length === 1 ? left + plotWidth / 2 : left + plotWidth * index / (points.length - 1);
    const yAt = value => top + plotHeight * (1 - Math.max(0, Math.min(100, Number(value) || 0)) / 100);
    const series = [
      {key:"engineering_percent", className:"chart-engineering"},
      {key:"overall_percent", className:"chart-overall"}
    ];
    for (const line of series) {
      const valid = points.map((point,index) => ({point,index,value:Number(point[line.key])}))
        .filter(item => item.point[line.key] !== null && item.point[line.key] !== undefined &&
          Number.isFinite(item.value) && item.value >= 0 && item.value <= 100);
      if (!valid.length) continue;
      const d = valid.map((item,index) => (index ? "L" : "M") + xAt(item.index) + " " + yAt(item.value)).join(" ");
      chart.append(svgNode("path",{d,class:line.className,fill:"none"}));
      for (const item of valid) {
        chart.append(svgNode("circle",{cx:xAt(item.index),cy:yAt(item.value),r:4,class:line.className}));
      }
    }
    const labelStep = Math.max(1, Math.ceil(points.length / 6));
    points.forEach((point,index) => {
      if (index % labelStep !== 0 && index !== points.length - 1) return;
      chart.append(svgNode("text",{x:xAt(index),y:height-12,"text-anchor":"middle",class:"chart-axis"},String(point.period || point.date || "")));
    });
    $("progress-chart-caption").textContent = points.length + " نقطهٔ ثبت‌شده؛ هر نقطه از scorecard ذخیره‌شده در همان تاریخ استخراج شده است.";
  }
  function renderProgressEvents(events) {
    const host = $("progress-events");
    host.replaceChildren();
    if (!Array.isArray(events) || !events.length) {
      const item = document.createElement("li");
      item.className = "muted";
      item.textContent = "هنوز رویدادی ثبت نشده است.";
      host.append(item);
      return;
    }
    for (const event of events.slice(-20).reverse()) {
      const item = document.createElement("li");
      const title = document.createElement("strong");
      title.textContent = String(event.phase_label || "فعالیت عامل");
      const detail = document.createElement("span");
      detail.textContent = String(event.activity || "");
      const time = document.createElement("small");
      const parsed = event.updated_at ? new Date(event.updated_at) : null;
      time.textContent = parsed && !Number.isNaN(parsed.getTime()) ? parsed.toLocaleString() : "";
      item.append(title, detail, time);
      host.append(item);
    }
  }
  async function refreshProgress({queueIfBusy = false} = {}) {
    if (progressBusy) {
      if (queueIfBusy) progressRefreshQueued = true;
      return;
    }
    progressBusy = true;
    const period = $("progress-period").value;
    const start = $("progress-start").value, end = $("progress-end").value;
    const selectionIsCurrent = () => $("progress-period").value === period &&
      (period !== "custom" || ($("progress-start").value === start && $("progress-end").value === end));
    const params = new URLSearchParams({period});
    if (period === "custom") {
      if (!start || !end) {
        $("progress-chart-caption").textContent = "تاریخ شروع و پایان را انتخاب کنید.";
        progressBusy = false;
        return;
      }
      if (start > end) {
        $("progress-chart-caption").textContent = "تاریخ شروع باید قبل از تاریخ پایان باشد.";
        progressBusy = false;
        return;
      }
      params.set("start_date", start);
      params.set("end_date", end);
    }
    try {
      const data = await request("progress?" + params.toString());
      if (!selectionIsCurrent()) {
        progressRefreshQueued = true;
        return;
      }
      if (data.error) throw new Error(data.error);
      const current = data.current;
      if (current) {
        if (current.plan_percent !== null && current.plan_percent !== undefined && Number.isFinite(Number(current.plan_percent))) {
          const percent = Math.max(0, Math.min(100, Number(current.plan_percent)));
          $("cycle-progress-value").textContent = percent + "%";
          $("cycle-progress").value = percent;
        } else {
          $("cycle-progress-value").textContent = "—";
          $("cycle-progress").value = 0;
        }
        const statusLabels = {
          awaiting_review: "وضعیت قدیمی؛ در انتظار همگام‌سازی",
          running: "در حال اجرا",
          queued: "در صف اجرا",
          completed: "پایان اجرا",
          failed: "ناموفق"
        };
        const status = statusLabels[current.status] || String(current.status || "");
        $("current-phase").textContent = String(current.phase_label || "مرحلهٔ جاری") + (status ? " · " + status : "");
        $("current-activity").textContent = String(current.activity || "فعالیت جاری ثبت نشده است.");
        $("plan-progress").textContent = Number(current.plan_total) > 0
          ? "پیشرفت برنامه: " + Number(current.plan_completed || 0) + " از " + Number(current.plan_total) + " گام"
          : "درصد دقیق برنامه تا زمان ثبت گام‌های عامل در دسترس نیست.";
      } else {
        $("cycle-progress-value").textContent = "—";
        $("cycle-progress").value = 0;
        $("current-phase").textContent = "چرخهٔ فعالی گزارش نشده است.";
        $("current-activity").textContent = "با شروع اجرای عامل، فعالیت جاری در این قسمت نمایش داده می‌شود.";
        $("plan-progress").textContent = "";
      }
      const snapshots = data.history || [];
      const latest = data.latest_snapshot || (snapshots.length ? snapshots[snapshots.length - 1] : null);
      const engineeringPercent = latest && latest.engineering_percent;
      const overallPercent = latest && latest.overall_percent;
      $("engineering-progress").textContent = engineeringPercent !== null && engineeringPercent !== undefined &&
        Number.isFinite(Number(engineeringPercent)) && Number(engineeringPercent) >= 0 && Number(engineeringPercent) <= 100
        ? Number(engineeringPercent).toFixed(2) + "%" : "—";
      $("overall-progress").textContent = overallPercent !== null && overallPercent !== undefined &&
        Number.isFinite(Number(overallPercent)) && Number(overallPercent) >= 0 && Number(overallPercent) <= 100
        ? Number(overallPercent).toFixed(2) + "%" : "—";
      $("progress-updated").textContent = current && current.updated_at
        ? "آخرین رویداد: " + new Date(current.updated_at).toLocaleTimeString()
        : data.storage_configured
          ? "فضای ثبت آماده است؛ در انتظار اجرای عامل"
          : "فضای ثبت پیشرفت آماده نیست؛ راه‌اندازی یک‌باره لازم است";
      renderProgressChart(snapshots);
      renderProgressEvents(data.events);
    } catch (error) {
      if (!selectionIsCurrent()) {
        progressRefreshQueued = true;
        return;
      }
      $("progress-chart-caption").textContent = error.message || "دریافت دادهٔ پیشرفت ناموفق بود.";
    } finally {
      progressBusy = false;
      if (progressRefreshQueued) {
        progressRefreshQueued = false;
        void refreshProgress();
      }
    }
  }
  $("progress-period").addEventListener("change", () => {
    const custom = $("progress-period").value === "custom";
    $("progress-start-wrap").hidden = !custom;
    $("progress-end-wrap").hidden = !custom;
    if (!custom) refreshProgress({queueIfBusy: true});
  });
  $("refresh-progress").addEventListener("click", () => refreshProgress({queueIfBusy: true}));
  $("refresh-agent").addEventListener("click", refresh);
  refresh();
  refreshProgress();
  window.setInterval(refresh, 30000);
  window.setInterval(refreshProgress, 5000);
})();