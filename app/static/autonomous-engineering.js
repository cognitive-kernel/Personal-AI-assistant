(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const message = $("agent-message");
  const dispatchButton = $("dispatch-agent");
  const cancelButton = $("cancel-agent");
  const labels = {
    ollama: $("ollama-state"), model: $("model-state"), codex: $("codex-state"),
    runner: $("runner-state"), github: $("github-state")
  };
  function setMessage(text, kind) {
    message.textContent = text;
    message.dataset.kind = kind || "";
  }
  function stateText(ok) { return ok ? "آماده" : "نیازمند راه‌اندازی"; }
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
      headers: {"Accept": "application/json", ...(options && options.headers || {})},
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
      $("model-detail").textContent = "مدل انتخاب‌شده: " + String(data.model || "نامشخص");
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
  $("refresh-agent").addEventListener("click", refresh);
  refresh();
  window.setInterval(refresh, 30000);
})();