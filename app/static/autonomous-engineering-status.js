(() => {
  "use strict";
  const core = window.autonomousEngineeringCore;
  if (!core || core.version !== 1) return;
  const { $, request, setMessage } = core;
  const dispatchButton = $("dispatch-agent");
  const cancelButton = $("cancel-agent");
  const pauseButton = $("pause-agent");
  const resumeButton = $("resume-agent");
  const taskButton = $("send-agent-task");
  let lastPendingWork = null;
  const labels = {
    ollama: $("ollama-state"), model: $("model-state"), codex: $("codex-state"),
    runner: $("runner-state"), github: $("github-state"), storage: $("storage-state")
  };
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
  let statusRefreshPromise = null;
  // Serialize post-mutation snapshots. When multiple mutations finish while an
  // older poll is in flight, each continuation must not start a competing
  // refresh that can overwrite a newer authoritative status snapshot.
  let statusMutationRefreshPromise = null;
  let dispatchPendingUntil = 0;
  let dispatchUnlockTimer = null;
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
    if (dispatchUnlockTimer !== null) window.clearTimeout(dispatchUnlockTimer);
    dispatchUnlockTimer = window.setTimeout(() => {
      dispatchUnlockTimer = null;
      syncDispatchControls();
    }, 30000);
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
    // Queue mutation refreshes behind one another. Capturing the old poll
    // before awaiting it is not sufficient: two mutations can otherwise both
    // resume after that poll and launch competing status requests.
    const previousMutation = statusMutationRefreshPromise;
    let current;
    current = (async () => {
      if (previousMutation) {
        try {
          await previousMutation;
        } catch (_) {
          // A failed earlier refresh must not block a later authoritative read.
        }
      }
      const previousPoll = statusRefreshPromise;
      if (previousPoll) {
        try {
          await previousPoll;
        } catch (_) {
          // A failed pre-mutation poll must not prevent authoritative re-reading.
        }
      }
      let statusRequest;
      statusRequest = refreshStatus().finally(() => {
        if (statusRefreshPromise === statusRequest) statusRefreshPromise = null;
      });
      statusRefreshPromise = statusRequest;
      const result = await statusRequest;
      window.dispatchEvent(new Event("autonomous-engineering:status-mutated"));
      return result;
    })();
    statusMutationRefreshPromise = current;
    try {
      return await current;
    } finally {
      if (statusMutationRefreshPromise === current) statusMutationRefreshPromise = null;
    }
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
      lastPendingWork = data.pending_work && typeof data.pending_work === "object"
        ? data.pending_work
        : {available: false, blocked: true, checkpoint: {available: true, verified: false}};
      const brokerDiagnostic = typeof lastPendingWork.reason === "string"
        ? lastPendingWork.reason.slice(0, 240)
        : "";
      labels.storage.textContent = stateText(checks.progress_storage_accessible);
      $("storage-detail").textContent = checks.progress_storage_accessible
        ? "سرویس Broker محلی آماده است؛ وضعیت پایدار از کانال محدود محلی قابل خواندن است."
        : "سرویس Broker آماده نیست. نصب یا ترمیم آن فقط پس از فشردن دکمهٔ زیر و تأیید مجوز مدیر انجام می‌شود. Runner برای نصب Broker لازم نیست؛ Runner فقط قابلیت اجرای Workflow را فراهم می‌کند." +
          (brokerDiagnostic ? " تشخیص فعلی: " + brokerDiagnostic : "");
      const setupBrokerButton = $("setup-state-broker");
      setupBrokerButton.hidden = checks.progress_storage_accessible === true;
      setupBrokerButton.disabled = checks.progress_storage_accessible === true;
      const checkpoint = lastPendingWork.checkpoint && typeof lastPendingWork.checkpoint === "object"
        ? lastPendingWork.checkpoint
        : null;
      const checkpointState = $("checkpoint-state");
      const checkpointDetail = $("checkpoint-detail");
      if (!lastPendingWork.available) {
        checkpointState.textContent = "قابل بررسی نیست";
        checkpointDetail.textContent = brokerDiagnostic || "سرویس وضعیت پایدار پاسخ معتبر نداد؛ شروع هر چرخه تا بازیابی اتصال متوقف است.";
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
      const blockerLabels = {
        pending_task: "وظیفهٔ ذخیره‌شده",
        pending_owner: "فرادادهٔ مالکیت Runner",
        pending_pr: "PR تعیین‌تکلیف‌نشده",
        cleanup_in_progress: "عملیات پاک‌سازی ناتمام",
        checkpoint: "checkpoint بازیابی‌شدنی یا تأییدنشده"
      };
      const blockerKeys = new Set(
        Object.entries(lastPendingWork.markers || {})
          .filter(([, present]) => present === true)
          .map(([key]) => key)
      );
      if (checkpoint && (checkpoint.available === true || checkpoint.verified === false)) {
        blockerKeys.add("checkpoint");
      }
      if (typeof lastPendingWork.reason === "string") {
        lastPendingWork.reason.split(",").map(value => value.trim()).filter(Boolean)
          .forEach(key => blockerKeys.add(key));
      }
      const blockerSummary = [...blockerKeys].map(key => blockerLabels[key]).filter(Boolean).join("، ");
      $("manual-task-state").textContent = lastPendingWork.blocked
        ? "وظیفهٔ جدید مسدود است" +
          (blockerSummary ? "؛ علت ثبت‌شده: " + blockerSummary : "؛ علت دقیق از سرویس وضعیت قابل تشخیص نیست") +
          ". ارسال وظیفهٔ جدید غیرفعال است. اجرای بدون متن برای ادامه یا تطبیق همان کار قبلی است."
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
      // Run history refreshes independently and after committed mutations.
      // Rendering this status snapshot here bypasses its request sequence and
      // can overwrite a newer, explicitly refreshed run list with stale data.

      setMessage(data.paused ? "صف مهندسی متوقف است؛ برای ادامه از آخرین وضعیت، دکمهٔ ادامه را بزنید." : (ready ? "پیش‌نیازهای حساب برنامه آماده‌اند؛ Workflow پیش‌نیازهای حساب Runner را جداگانه بررسی می‌کند." : "برای فعال‌شدن اجرا، موارد ناقص را در راهنمای راه‌اندازی تکمیل کنید."), data.paused || ready ? "success" : "");
      return true;
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
      return false;
    } finally {
      $("refresh-agent").disabled = false;
    }
  }

  $("refresh-agent").addEventListener("click", refresh);
  function armDispatchUnlockTimer() {
    if (dispatchUnlockTimer !== null) window.clearTimeout(dispatchUnlockTimer);
    dispatchUnlockTimer = null;
    const remaining = dispatchPendingUntil - Date.now();
    if (remaining <= 0) {
      syncDispatchControls();
      return;
    }
    dispatchUnlockTimer = window.setTimeout(() => {
      dispatchUnlockTimer = null;
      syncDispatchControls();
    }, remaining);
  }
  function stop() {
    if (dispatchUnlockTimer !== null) window.clearTimeout(dispatchUnlockTimer);
    dispatchUnlockTimer = null;
  }
  function resume() {
    armDispatchUnlockTimer();
    syncDispatchControls();
  }
  window.autonomousEngineeringStatus = Object.freeze({
    version: 1,
    refresh,
    stop,
    resume,
    canStartDispatch,
    canStartManualTask,
    getPendingWork: () => lastPendingWork,
    setDispatchSubmitting(value) { dispatchSubmitting = Boolean(value); syncDispatchControls(); },
    syncDispatchControls,
    lockDispatchVisibilityWindow
  });
})();
