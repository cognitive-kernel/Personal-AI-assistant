(() => {
  "use strict";
  const core = window.autonomousEngineeringCore;
  const status = window.autonomousEngineeringStatus;
  if (!core || core.version !== 1 || !status || status.version !== 1) return;
  const { $, request, setMessage } = core;
  const saveButton = $("save-agent-settings");
  const settingsForm = $("agent-settings-form");
  const taskForm = $("agent-task-form");
  const taskButton = $("send-agent-task");
  const dispatchButton = $("dispatch-agent");
  const cancelButton = $("cancel-agent");
  const pauseButton = $("pause-agent");
  const resumeButton = $("resume-agent");
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
      await status.refresh(true);
    } catch (error) {
      const detail = error.message || "ذخیرهٔ تنظیمات ناموفق بود.";
      await status.refresh(true);
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
        await status.refresh(true);
        setMessage(pullModelStatus.textContent, "success");
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
    if (!status.canStartManualTask()) {
      setMessage(
        status.getPendingWork() && status.getPendingWork().blocked
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
    status.setDispatchSubmitting(true);
    taskButton.disabled = true;
    dispatchButton.disabled = true;
    setMessage("در حال ارسال وظیفه به عامل محلی و درخواست شروع چرخه…");
    try {
      const result = await request("dispatch", {
        method: "POST",
        body: JSON.stringify({task})
      });
      status.lockDispatchVisibilityWindow();
      $("manual-task").value = "";
      setMessage(result.message || "وظیفه برای عامل ارسال شد.", "success");
      await status.refresh(true);
    } catch (error) {
      if (error.ambiguous || error.retryable || error.status >= 500) status.lockDispatchVisibilityWindow();
      const detail = error.message || "ارسال وظیفه ناموفق بود.";
      await status.refresh(true);
      setMessage(detail, "error");
    } finally {
      status.setDispatchSubmitting(false);
      status.syncDispatchControls();
    }
  });
  dispatchButton.addEventListener("click", async () => {
    if (!status.canStartDispatch()) return;
    status.setDispatchSubmitting(true);
    dispatchButton.disabled = true;
    taskButton.disabled = true;
    setMessage("در حال ارسال درخواست اجرای Workflow به GitHub…");
    try {
      const result = await request("dispatch", {method: "POST"});
      status.lockDispatchVisibilityWindow();
      await status.refresh(true);
      setMessage(result.message || "درخواست ارسال شد.", "success");
    } catch (error) {
      if (error.ambiguous || error.retryable || error.status >= 500) status.lockDispatchVisibilityWindow();
      const detail = error.message || "ارسال درخواست ناموفق بود.";
      await status.refresh(true);
      setMessage(detail, "error");
    } finally {
      status.setDispatchSubmitting(false);
      status.syncDispatchControls();
    }
  });
  pauseButton.addEventListener("click", async () => {
    pauseButton.disabled = true;
    try {
      const result = await request("pause", {method: "POST"});
      await status.refresh(true);
      setMessage(result.message || "توقف ایمن ثبت شد.", "success");
    } catch (error) {
      const detail = error.message || "توقف ایمن ثبت نشد.";
      await status.refresh(true);
      setMessage(detail, "error");
    }
  });
  resumeButton.addEventListener("click", async () => {
    resumeButton.disabled = true;
    try {
      const result = await request("resume", {method: "POST"});
      status.lockDispatchVisibilityWindow();
      await status.refresh(true);
      setMessage(result.message || "ادامهٔ صف درخواست شد.", "success");
    } catch (error) {
      if (error.ambiguous || error.retryable || error.status >= 500) status.lockDispatchVisibilityWindow();
      const detail = error.message || "ادامهٔ صف ثبت نشد.";
      await status.refresh(true);
      setMessage(detail, "error");
    }
  });
  cancelButton.addEventListener("click", async () => {
    if (!window.confirm("اجرای فعال عامل در GitHub Actions لغو شود؟")) return;
    cancelButton.disabled = true;
    try {
      const result = await request("cancel", {method: "POST"});
      await status.refresh(true);
      setMessage(result.message || "درخواست لغو ارسال شد.", "success");
    } catch (error) {
      const detail = error.message || "لغو اجرای فعال ناموفق بود.";
      await status.refresh(true);
      setMessage(detail, "error");
    }
  });


  window.autonomousEngineeringControls = Object.freeze({version: 1});
})();
