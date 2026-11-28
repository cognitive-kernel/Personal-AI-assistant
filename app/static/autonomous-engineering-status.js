(() => {
  "use strict";
  const core = window.autonomousEngineeringCore;
  if (!core || core.version !== 1) return;
  const { $, request, setStatusMessage } = core;
  const dispatchButton = $("dispatch-agent");
  const cancelButton = $("cancel-agent");
  const pauseButton = $("pause-agent");
  const resumeButton = $("resume-agent");
  const taskButton = $("send-agent-task");
  const clearQueueButton = $("clear-agent-queue");
  const editedSettings = new Map();
  for (const id of ["ollama-model", "execution-mode", "interval-hours", "custom-model"]) {
    $(id).addEventListener("input", () => {
      editedSettings.set(id, { value: $(id).value });
    });
  }
  function acknowledgeSettings(savedEdits) {
    for (const [id, edit] of savedEdits) {
      if (editedSettings.get(id) !== edit) continue;
      editedSettings.delete(id);
      if (id === "custom-model") $(id).value = "";
    }
  }
  let lastPendingWork = null;
  const labels = {
    ollama: $("ollama-state"), model: $("model-state"), codex: $("codex-state"),
    runner: $("runner-state"), github: $("github-state"), storage: $("storage-state")
  };
  function stateText(ok) { return ok ? "آماده" : "نیازمند راه‌اندازی"; }
  function incompleteProbes(data) {
    const messages = new Map();
    for (const [field, message] of [
      ["status_probe_unavailable", "ظرفیت بررسی وضعیت موقتاً پر است."],
      ["status_probe_timeouts", "بررسی وضعیت در مهلت مقرر پاسخ نداد."],
      ["status_probe_errors", "بررسی وضعیت با خطا مواجه شد."]
    ]) {
      if (!Array.isArray(data[field])) continue;
      for (const probe of data[field]) {
        if (typeof probe !== "string") continue;
        const name = probe === "github_settings" ? "github" : probe === "pending_work" ? "storage" : probe;
        if (Object.hasOwn(labels, name) && name !== "model") messages.set(name, message);
      }
    }
    return messages;
  }
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
    const draft = editedSettings.get(select.id);
    const draftAvailable = draft && installed.includes(draft.value) &&
      !(savedModelUnavailable && draft.value === selectedModel);
    if (!selectedModel || savedModelUnavailable || (draft && !draftAvailable)) {
      const placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.disabled = true;
      placeholder.selected = true;
      placeholder.textContent = draft && !draftAvailable
        ? "مدل انتخاب‌شده در فهرست مدل‌های قابل استفاده نیست"
        : selectedModelIsContextVariant
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
    const savedValue = installed.includes(selectedModel) && !selectedModelIsContextVariant ? selectedModel : "";
    select.value = draft ? (draftAvailable ? draft.value : "") : savedValue;
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
  let lastActiveRunId = null;
  let statusKnown = false;
  let queueClearAvailable = false;
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
    clearQueueButton.disabled = !(statusKnown && queueClearAvailable && !lastActiveRun &&
      !dispatchSubmitting && Date.now() >= dispatchPendingUntil &&
      pending?.available === true && pending.blocked === true);
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
      const data = await request("status", {cache: "no-store"});
      const checks = data.checks || {};
      const incomplete = incompleteProbes(data);
      const githubKnown = !incomplete.has("github");
      const modelsKnown = githubKnown && !incomplete.has("ollama");
      const storageKnown = !incomplete.has("storage");
      labels.ollama.textContent = stateText(checks.ollama_online);
      $("ollama-detail").textContent = checks.ollama_online ? "پاسخ از 127.0.0.1:11434 دریافت شد." : "سرویس محلی در دسترس نیست.";
      const modelReady = checks.model_installed && checks.model_selectable && checks.model_context_metadata_available && checks.model_context_supported && checks.model_supports_tools;
      labels.model.textContent = stateText(modelReady);
      const contextLength = Number(data.model_context_length);
      const recommendedContext = Number(data.model_recommended_context_length);
      const contextBlockers = {
        kv_metadata: "Ollama ابعاد لازم برای محاسبهٔ حافظهٔ KV را گزارش نکرد.",
        weight_metadata: "اندازهٔ معتبر وزن مدل از Ollama دریافت نشد.",
        resident_context: "Ollama اندازهٔ Context مدلِ در حال اجرا را گزارش نکرد. پس از پایان استفادهٔ فعلی مدل، وضعیت را بازخوانی کنید.",
        memory_budget: "با اطلاعات فعلی، ظرفیت امن Context تأیید نشد. حافظهٔ آزاد و سقف حافظهٔ تنظیم‌شده را بررسی کنید.",
        probe_failed: "بررسی اطلاعات حافظه یا مدل کامل نشد. وضعیت را بازخوانی کنید."
      };
      const contextBlocker = data.model_context_blocker;
      const contextDiagnosis = typeof contextBlocker === "string" && Object.hasOwn(contextBlockers, contextBlocker)
        ? "؛ " + contextBlockers[contextBlocker]
        : "";
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
      $("model-detail").textContent = modelLabel + installationText + contextText + contextDiagnosis + toolsText;
      if (modelsKnown) {
        renderModels(data.selectable_models || data.models, data.model, checks.model_is_codex_context_variant);
      } else {
        labels.model.textContent = "تأیید نشده";
        $("model-detail").textContent = incomplete.get("ollama") || incomplete.get("github");
      }
      const executionMode = data.execution_mode || "continuous";
      if (githubKnown && !editedSettings.has("execution-mode")) {
        $("execution-mode").value = executionMode;
      }
      if (githubKnown && !editedSettings.has("interval-hours")) {
        $("interval-hours").value = [1, 2, 4, 6, 12, 24].includes(Number(data.interval_hours)) ? String(Number(data.interval_hours)) : "24";
      }
      labels.codex.textContent = checks.codex_installed ? "موجود" : "در حساب برنامه نصب نیست";
      $("codex-detail").textContent = data.codex && data.codex.version
        ? data.codex.version + "؛ این بررسی فقط حساب برنامه را پوشش می‌دهد."
        : "حساب Runner جداگانه و هنگام اجرای Workflow بررسی می‌شود.";
      const nativeWorkflowBlocked = checks.native_runner_workflow_compatible === false;
      labels.runner.textContent = stateText(checks.runner_running);
      if (nativeWorkflowBlocked) {
        const ownership = data.runner?.local_ownership;
        const descriptions = {
          verified: "مالکیت محلی تأیید شد",
          not_found: "Runner بومی پیدا نشد",
          mismatch: "حساب یا فضای وضعیت Runner سازگار نیست",
          ambiguous: "چند Runner بومی شناسایی شد",
          unverifiable: "مالکیت Runner قابل تأیید نیست"
        };
        labels.runner.textContent = Object.hasOwn(descriptions, ownership)
          ? descriptions[ownership]
          : descriptions.unverifiable;
        $("runner-detail").textContent = "ارسال Workflow در حالت بومی غیرفعال است.";
      } else {
        $("runner-detail").textContent = checks.runner_running ? "فرایند Runner.Listener فعال است." : "Runner محلی فعال نیست.";
      }
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
      const nativeState = data.state_backend === "native";
      $("storage-detail").textContent = nativeState
        ? (checks.progress_storage_accessible ? "فضای وضعیت بومی بدون سرویس خارجی آماده است." : "فضای وضعیت بومی آماده نیست؛ مسیر و دسترسی حساب فعلی را بررسی کنید.")
        : checks.progress_storage_accessible
        ? "سرویس Broker محلی آماده است؛ وضعیت پایدار از کانال محدود محلی قابل خواندن است."
        : "سرویس Broker آماده نیست. نصب یا ترمیم آن فقط پس از فشردن دکمهٔ زیر و تأیید مجوز مدیر انجام می‌شود. Runner برای نصب Broker لازم نیست؛ Runner فقط قابلیت اجرای Workflow را فراهم می‌کند." +
          (brokerDiagnostic ? " تشخیص فعلی: " + brokerDiagnostic : "");
      const setupBrokerButton = $("setup-state-broker");
      setupBrokerButton.dataset.nativeState = nativeState ? "true" : "false";
      setupBrokerButton.textContent = nativeState ? "راه‌اندازی فضای وضعیت بومی" : "راه‌اندازی یا ترمیم Broker";
      setupBrokerButton.hidden = !storageKnown || checks.progress_storage_accessible === true;
      const migrationButton = $("stage-native-migration");
      migrationButton.hidden = !storageKnown || !nativeState || checks.progress_storage_accessible === true;
      migrationButton.disabled = migrationButton.hidden || !githubKnown || data.paused !== true;
      setupBrokerButton.disabled = setupBrokerButton.hidden;
      for (const [name, message] of incomplete) {
        labels[name].textContent = "تأیید نشده";
        $(name + "-detail").textContent = message;
      }
      const checkpoint = lastPendingWork.checkpoint && typeof lastPendingWork.checkpoint === "object"
        ? lastPendingWork.checkpoint
        : null;
      const checkpointState = $("checkpoint-state");
      const checkpointDetail = $("checkpoint-detail");
      if (!lastPendingWork.available) {
        checkpointState.textContent = "قابل بررسی نیست";
        checkpointDetail.textContent = incomplete.get("storage") || brokerDiagnostic || "سرویس وضعیت پایدار پاسخ معتبر نداد؛ شروع هر چرخه تا بازیابی اتصال متوقف است.";
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
      const requiredProbesKnown = modelsKnown && storageKnown && !incomplete.has("runner");
      const ready = !nativeWorkflowBlocked && requiredProbesKnown && data.ready === true;
      const paused = githubKnown && data.paused === true;
      const canDispatch = data.can_dispatch === undefined ? (ready && !paused) : data.can_dispatch === true;
      const activeRun = (data.github && data.github.recent_runs || []).find(run =>
        ["queued", "in_progress", "waiting", "requested", "pending", "suspended"].includes(run.status));
      const active = Boolean(activeRun);
      if (githubKnown) {
        lastActiveRunId = activeRun && Number.isSafeInteger(Number(activeRun.id)) && Number(activeRun.id) > 0
          ? Number(activeRun.id)
          : null;
        if (active) dispatchPendingUntil = 0;
        lastActiveRun = active;
        cancelButton.disabled = !active;
        pauseButton.disabled = paused;
        resumeButton.disabled = !paused || nativeWorkflowBlocked;
      }
      lastCanDispatch = !nativeWorkflowBlocked && requiredProbesKnown && canDispatch;
      queueClearAvailable = githubKnown && githubReady && storageKnown &&
        checks.runner_running === true && !nativeWorkflowBlocked;
      statusKnown = true;
      syncDispatchControls();
      const pill = $("agent-readiness");
      const readyToDispatch = ready && lastCanDispatch && !lastActiveRun && !(checkpoint?.available === true && checkpoint.verified !== true);
      pill.textContent = !requiredProbesKnown ? "آمادگی اجرا تأیید نشده"
        : paused ? "توقف ایمن فعال است"
        : lastActiveRun ? "Workflow در صف یا در حال اجرا"
        : readyToDispatch ? "آمادهٔ ارسال Workflow"
        : ready ? "ارسال Workflow فعلاً مسدود است"
        : "راه‌اندازی ناقص";
      pill.className = "status-pill " + (readyToDispatch ? "status-ready" : "status-blocked");
      // Run history refreshes independently and after committed mutations.
      // Rendering this status snapshot here bypasses its request sequence and
      // can overwrite a newer, explicitly refreshed run list with stale data.

      if (incomplete.size) {
        setStatusMessage("بخشی از وضعیت تأیید نشد؛ برای بررسی دوباره، تازه‌سازی کنید." +
          (data.correlation_id ? " · شناسهٔ پیگیری: " + data.correlation_id : ""), "error");
      } else if (lastActiveRun && !paused) {
        const runLabel = lastActiveRunId === null ? "" : " (" + lastActiveRunId + ")";
        setStatusMessage("Workflow" + runLabel + " در صف یا در حال اجراست؛ ارسال و ادامهٔ دوباره پس از پایان آن ممکن است.");
      } else {
        setStatusMessage(paused ? "صف مهندسی متوقف است؛ برای ادامه از آخرین وضعیت، دکمهٔ ادامه را بزنید." : (ready ? "پیش‌نیازهای حساب برنامه آماده‌اند؛ Workflow پیش‌نیازهای حساب Runner را جداگانه بررسی می‌کند." : "برای فعال‌شدن اجرا، موارد ناقص را در راهنمای راه‌اندازی تکمیل کنید."), paused || ready ? "success" : "");
      }
      return incomplete.size === 0;
    } catch (error) {
      statusKnown = false;
      lastPendingWork = null;
      setStatusMessage(error.message || "دریافت وضعیت با خطا مواجه شد.", "error");
      syncDispatchControls();
      dispatchButton.disabled = true;
      taskButton.disabled = true;
      // A transient poll failure must not remove Cancel/Pause/Resume from the operator.
      return false;
    } finally {
      $("refresh-agent").disabled = false;
    }
  }

  $("refresh-agent").addEventListener("click", async () => {
    const feedback = $("manual-refresh-feedback");
    feedback.textContent = "در حال بازخوانی وضعیت…";
    const refreshed = await refresh();
    const time = new Date().toLocaleTimeString("fa-IR");
    feedback.textContent = (refreshed ? "وضعیت بازخوانی شد" : "بازخوانی وضعیت کامل نشد") + " · " + time;
  });
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
    captureSettingsEdits: () => new Map(editedSettings),
    acknowledgeSettings,
    stop,
    resume,
    canStartDispatch,
    canStartManualTask,
    getPendingWork: () => lastPendingWork,
    getActiveRunId: () => lastActiveRunId,
    setDispatchSubmitting(value) { dispatchSubmitting = Boolean(value); syncDispatchControls(); },
    syncDispatchControls,
    lockDispatchVisibilityWindow
  });
})();
