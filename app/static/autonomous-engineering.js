(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const message = $("agent-message");
  const dispatchButton = $("dispatch-agent");
  const cancelButton = $("cancel-agent");
  const saveButton = $("save-agent-settings");
  const settingsForm = $("agent-settings-form");
  const labels = {
    ollama: $("ollama-state"), model: $("model-state"), codex: $("codex-state"),
    runner: $("runner-state"), github: $("github-state")
  };
  function setMessage(text, kind) {
    message.textContent = text;
    message.dataset.kind = kind || "";
  }
  function stateText(ok) { return ok ? "آماده" : "نیازمند راه‌اندازی"; }
  function renderModels(models, selectedModel) {
    const select = $("ollama-model");
    if (document.activeElement === select) return;
    const names = Array.isArray(models) ? models.filter(name => typeof name === "string") : [];
    if (!names.includes("gpt-oss:20b")) names.unshift("gpt-oss:20b");
    if (selectedModel && !names.includes(selectedModel)) names.unshift(selectedModel);
    select.replaceChildren();
    for (const name of [...new Set(names)]) {
      const option = document.createElement("option");
      option.value = name;
      option.textContent = name + (name === "gpt-oss:20b" ? " (پیش‌فرض)" : "");
      select.append(option);
    }
    select.value = selectedModel || "gpt-oss:20b";
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
      labels.model.textContent = stateText(checks.model_installed);
      $("model-detail").textContent = "مدل انتخاب‌شده: " + String(data.model || "نامشخص") +
        (checks.model_installed ? "" : "؛ نصب نشده است. برای نصب: ollama pull " + String(data.model || ""));
      renderModels(data.models, data.model);
      if (document.activeElement !== $("runs-per-day")) {
        $("runs-per-day").value = String(data.runs_per_day || 1);
      }
      labels.codex.textContent = stateText(checks.codex_installed);
      $("codex-detail").textContent = data.codex && data.codex.version ? data.codex.version : "Codex CLI پیدا نشد.";
      labels.runner.textContent = stateText(checks.runner_running);
      $("runner-detail").textContent = checks.runner_running ? "فرایند Runner.Listener فعال است." : "Runner محلی فعال نیست.";
      const githubReady = checks.github_cli_authenticated && checks.github_runs_available;
      labels.github.textContent = stateText(githubReady);
      $("github-detail").textContent = !checks.github_cli_authenticated
        ? "gh auth login لازم است."
        : !checks.github_runs_available
          ? "دریافت وضعیت اجراهای GitHub ناموفق است؛ دسترسی Actions را بررسی کنید."
          : "GitHub CLI احراز هویت شده و وضعیت اجراها قابل دریافت است.";
      const ready = data.ready === true;
      const pill = $("agent-readiness");
      pill.textContent = ready ? "آمادهٔ اجرا" : "راه‌اندازی ناقص";
      pill.className = "status-pill " + (ready ? "status-ready" : "status-blocked");
      const active = (data.github && data.github.recent_runs || []).some(run =>
        ["queued", "in_progress", "waiting", "requested", "pending"].includes(run.status));
      dispatchButton.disabled = !ready || active;
      cancelButton.disabled = !active;
      renderRuns(data.github && data.github.recent_runs);
      setMessage(ready ? "همهٔ پیش‌نیازهای محلی آماده‌اند." : "برای فعال‌شدن اجرا، موارد ناقص را در راهنمای راه‌اندازی تکمیل کنید.", ready ? "success" : "");
    } catch (error) {
      setMessage(error.message || "دریافت وضعیت با خطا مواجه شد.", "error");
      dispatchButton.disabled = true;
      cancelButton.disabled = true;
    } finally {
      $("refresh-agent").disabled = false;
    }
  }
  settingsForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    saveButton.disabled = true;
    const customModel = $("custom-model").value.trim();
    const model = customModel || $("ollama-model").value;
    const runsPerDay = Number($("runs-per-day").value);
    setMessage("در حال ذخیرهٔ مدل و تعداد اجرا در تنظیمات مخزن GitHub…");
    try {
      const result = await request("settings", {
        method: "POST",
        body: JSON.stringify({model, runs_per_day: runsPerDay})
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
      const current = data.current;
      if (current && Number.isFinite(Number(current.percent))) {
        const percent = Math.max(0, Math.min(100, Number(current.percent)));
        $("cycle-progress-value").textContent = percent + "%";
        $("cycle-progress").value = percent;
        $("current-phase").textContent = String(current.phase_label || "مرحلهٔ جاری") + " · " + String(current.status || "");
        $("current-activity").textContent = String(current.activity || "فعالیت جاری ثبت نشده است.");
        $("plan-progress").textContent = Number(current.plan_total) > 0
          ? "پیشرفت برنامه: " + Number(current.plan_completed || 0) + " از " + Number(current.plan_total) + " گام"
          : "درصد مرحله‌ای؛ برنامهٔ جزئی عامل هنوز گزارش نشده است.";
      } else {
        $("cycle-progress-value").textContent = "—";
        $("cycle-progress").value = 0;
        $("current-phase").textContent = "چرخهٔ فعالی گزارش نشده است.";
        $("current-activity").textContent = "با شروع اجرای عامل، فعالیت جاری در این قسمت نمایش داده می‌شود.";
        $("plan-progress").textContent = "";
      }
      const snapshots = data.history || [];
      const latest = snapshots.length ? snapshots[snapshots.length - 1] : null;
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
\n  $("refresh-agent").addEventListener("click", refresh);\n  refresh();\n  refreshProgress();\n  window.setInterval(refresh, 30000);\n  window.setInterval(refreshProgress, 5000);
})();