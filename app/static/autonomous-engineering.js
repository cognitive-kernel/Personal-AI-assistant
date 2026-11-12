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
    if (savedModelUnavailable && !installed.includes(selectedModel)) names.unshift(selectedModel);
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
      const contextVariant = /-codex-ctx[0-9]+$/i.test(name);
      const unavailableSavedModel = savedModelUnavailable && name === selectedModel;
      option.textContent = name + (contextVariant
        ? " (نسخهٔ داخلی Context؛ قابل انتخاب نیست)"
        : unavailableSavedModel ? " (ذخیره‌شده؛ نصب نیست)" : "");
      if (contextVariant || unavailableSavedModel) {
        option.dataset.installed = contextVariant ? "true" : "false";
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
      empty.textContent = "اجرایی در سوابق اخیر پیدا نشد یا GitHub CLI دسترسی ندارد.";
      host.append(empty);
      return;
    }
    for (const run of runs) {
      const item = document.createElement("div");
      item.className = "run-item";
      const summary = document.createElement("span");
      summary.textContent = "#" + String(run.id || "—") + " · " + String(run.status || "نامشخص") +
        (run.conclusion ? " · " + String(run.conclusion) : "") +
        (run.created_at ? " · " + String(run.created_at) : "");
      item.append(summary);
      if (typeof run.url === "string" && run.url.startsWith("https://github.com/")) {
        const link = document.createElement("a");
        link.href = run.url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = "جزئیات اجرا";
        item.append(link);
      }
      host.append(item);
    }
  }
  async function request(path, options) {
    const response = await fetch("/api/autonomous-engineering/" + path, {
      headers: {"Accept": "application/json", ...(options && options.body ? {"Content-Type": "application/json"} : {}), ...(options && options.headers || {})},
      ...(options || {})
    });
    let payload = {};
    try { payload = await response.json(); } catch (_) {}
    if (!response.ok) throw new Error(payload.detail || "درخواست ناموفق بود.");
    return payload;
  }
  async function refresh() {
    $("refresh-agent").disabled = true;
    try {
      const data = await request("status");
      const checks = data.checks || {};
      labels.ollama.textContent = stateText(checks.ollama_online);
      $("ollama-detail").textContent = checks.ollama_online ? "پاسخ از 127.0.0.1:11434 دریافت شد." : "سرویس محلی در دسترس نیست.";
      const modelReady = checks.model_installed && checks.model_selectable && checks.model_context_metadata_available && checks.model_context_supported && checks.model_supports_tools;
      labels.model.textContent = stateText(modelReady);
      const contextLength = Number(data.model_context_length);
      const contextText = Number.isSafeInteger(contextLength) && contextLength >= 512
        ? "؛ حداکثر Context مدل: " + contextLength.toLocaleString() + " توکن" + (contextLength >= 64000 ? "؛ حداقل Codex تأمین است" : "؛ Codex حداقل ۶۴٬۰۰۰ توکن می‌خواهد")
        : "؛ حداکثر Context از metadata مدل تأیید نشد";
      const toolsText = checks.model_supports_tools ? "؛ قابلیت tools تأیید شد" : "؛ قابلیت tools تأیید نشد";
      const modelLabel = data.model ? "مدل ذخیره‌شده: " + String(data.model) : "هنوز مدلی ذخیره نشده است";
      const installationText = data.model && checks.model_is_codex_context_variant
        ? "؛ این نسخهٔ داخلی Context است؛ مدل اصلی را انتخاب و ذخیره کنید"
        : data.model && !checks.model_installed ? "؛ مدل ذخیره‌شده روی این سیستم نصب نیست" : "";
      $("model-detail").textContent = modelLabel + installationText + contextText + toolsText;
      renderModels(data.models || data.selectable_models, data.model, checks.model_is_codex_context_variant);
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
        ? "پوشهٔ مشترک پیشرفت برای برنامه قابل خواندن است."
        : "پوشهٔ مشترک را با scripts/configure_autonomous_engineering_progress.ps1 پیکربندی کنید.";
      const ready = data.ready === true;
      const paused = data.paused === true;
      const canDispatch = data.can_dispatch === undefined ? (ready && !paused) : data.can_dispatch === true;
      const pill = $("agent-readiness");
      pill.textContent = paused ? "توقف ایمن فعال است" : (ready ? "آمادهٔ ارسال Workflow" : "راه‌اندازی ناقص");
      pill.className = "status-pill " + (paused ? "status-blocked" : (ready ? "status-ready" : "status-blocked"));
      const active = (data.github && data.github.recent_runs || []).some(run =>
        ["queued", "in_progress", "waiting", "requested", "pending"].includes(run.status));
      dispatchButton.disabled = !canDispatch || active;
      taskButton.disabled = !canDispatch || active;
      cancelButton.disabled = !active;
      pauseButton.disabled = Boolean(data.paused);
      resumeButton.disabled = !data.paused;
      renderRuns(data.github && data.github.recent_runs);
      setMessage(data.paused ? "صف مهندسی متوقف است؛ برای ادامه از آخرین وضعیت، دکمهٔ ادامه را بزنید." : (ready ? "پیش‌نیازهای حساب برنامه آماده‌اند؛ Workflow پیش‌نیازهای حساب Runner را جداگانه بررسی می‌کند." : "برای فعال‌شدن اجرا، موارد ناقص را در راهنمای راه‌اندازی تکمیل کنید."), data.paused || ready ? "success" : "");
    } catch (error) {
      setMessage(error.message || "دریافت وضعیت با خطا مواجه شد.", "error");
      dispatchButton.disabled = true;
      taskButton.disabled = true;
      cancelButton.disabled = true;
      pauseButton.disabled = true;
      resumeButton.disabled = true;
    } finally {
      $("refresh-agent").disabled = false;
    }
  }
  settingsForm.addEventListener("submit", async (event) => {
    event.preventDefault();
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
      await refresh();
    } catch (error) {
      setMessage(error.message, "error");
    } finally {
      saveButton.disabled = false;
    }
  });
  taskForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const task = $("manual-task").value.trim();
    if (!task) {
      setMessage("ابتدا متن وظیفه را وارد کنید.", "error");
      return;
    }
    if (task.length > 2000 || task.includes(String.fromCharCode(0))) {
      setMessage("متن وظیفه نامعتبر یا طولانی است.", "error");
      return;
    }
    taskButton.disabled = true;
    dispatchButton.disabled = true;
    setMessage("در حال ارسال وظیفه به عامل محلی و درخواست شروع چرخه…");
    try {
      const result = await request("dispatch", {
        method: "POST",
        body: JSON.stringify({task})
      });
      $("manual-task").value = "";
      setMessage(result.message || "وظیفه برای عامل ارسال شد.", "success");
      window.setTimeout(refresh, 2500);
    } catch (error) {
      setMessage(error.message, "error");
      await refresh();
    }
  });
  dispatchButton.addEventListener("click", async () => {
    dispatchButton.disabled = true;
    setMessage("در حال ارسال درخواست اجرای Workflow به GitHub…");
    try {
      const result = await request("dispatch", {method: "POST"});
      setMessage(result.message || "درخواست ارسال شد.", "success");
      window.setTimeout(refresh, 2500);
    } catch (error) {
      setMessage(error.message, "error");
    } finally {
      dispatchButton.disabled = false;
    }
  });
  pauseButton.addEventListener("click", async () => {
    pauseButton.disabled = true;
    try {
      const result = await request("pause", {method: "POST"});
      setMessage(result.message || "توقف ایمن ثبت شد.", "success");
      await refresh();
    } catch (error) {
      setMessage(error.message, "error");
      pauseButton.disabled = false;
    }
  });
  resumeButton.addEventListener("click", async () => {
    resumeButton.disabled = true;
    try {
      const result = await request("resume", {method: "POST"});
      setMessage(result.message || "ادامهٔ صف درخواست شد.", "success");
      window.setTimeout(refresh, 1500);
    } catch (error) {
      setMessage(error.message, "error");
      resumeButton.disabled = false;
    }
  });
  cancelButton.addEventListener("click", async () => {
    if (!window.confirm("اجرای فعال عامل در GitHub Actions لغو شود؟")) return;
    cancelButton.disabled = true;
    try {
      const result = await request("cancel", {method: "POST"});
      setMessage(result.message || "درخواست لغو ارسال شد.", "success");
      window.setTimeout(refresh, 1500);
    } catch (error) {
      setMessage(error.message, "error");
    }
  });

  let progressBusy = false;
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
      const valid = points.map((point,index) => ({point,index,value:Number(point[line.key])})).filter(item => Number.isFinite(item.value));
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
  async function refreshProgress() {
    if (progressBusy) return;
    progressBusy = true;
    const period = $("progress-period").value;
    const params = new URLSearchParams({period});
    if (period === "custom") {
      const start = $("progress-start").value, end = $("progress-end").value;
      if (!start || !end) {
        $("progress-chart-caption").textContent = "تاریخ شروع و پایان را انتخاب کنید.";
        progressBusy = false;
        return;
      }
      params.set("start_date", start);
      params.set("end_date", end);
    }
    try {
      const data = await request("progress?" + params.toString());
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
      $("engineering-progress").textContent = latest && Number.isFinite(Number(latest.engineering_percent))
        ? Number(latest.engineering_percent).toFixed(2) + "%" : "—";
      $("overall-progress").textContent = latest && Number.isFinite(Number(latest.overall_percent))
        ? Number(latest.overall_percent).toFixed(2) + "%" : "—";
      $("progress-updated").textContent = current && current.updated_at
        ? "آخرین رویداد: " + new Date(current.updated_at).toLocaleTimeString()
        : data.storage_configured
          ? "فضای ثبت آماده است؛ در انتظار اجرای عامل"
          : "فضای ثبت پیشرفت آماده نیست؛ راه‌اندازی یک‌باره لازم است";
      renderProgressChart(snapshots);
      renderProgressEvents(data.events);
    } catch (error) {
      $("progress-chart-caption").textContent = error.message || "دریافت دادهٔ پیشرفت ناموفق بود.";
    } finally {
      progressBusy = false;
    }
  }
  $("progress-period").addEventListener("change", () => {
    const custom = $("progress-period").value === "custom";
    $("progress-start-wrap").hidden = !custom;
    $("progress-end-wrap").hidden = !custom;
    if (!custom) refreshProgress();
  });
  $("refresh-progress").addEventListener("click", refreshProgress);
  $("refresh-agent").addEventListener("click", refresh);
  refresh();
  refreshProgress();
  window.setInterval(refresh, 30000);
  window.setInterval(refreshProgress, 5000);
})();