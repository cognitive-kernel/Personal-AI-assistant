(() => {
  "use strict";

  const container = document.getElementById("module-controls-container");
  const output = document.getElementById("module-controls-output");
  const refreshButton = document.getElementById("refresh-module-controls");
  const taskManagerSection = document.getElementById("task-manager-section");
  const dashboard = window.dashboardCore;
  if (!container || !output || !refreshButton || !taskManagerSection || !dashboard || typeof dashboard.api !== "function") return;

  const { api } = dashboard;
  const busyModules = new Set();
  const stateLabels = {
    running: "فعال",
    paused: "مکث",
    draining: "در حال پایان",
    stopped: "متوقف",
    finished: "پایان‌یافته",
    finished_pending: "پایان کار فعال؛ صف محفوظ",
    unavailable: "وضعیت نامشخص"
  };
  const actions = [
    { id: "start", label: "شروع", title: "فعال‌سازی پردازش وظایف صف‌شده و جدید؛ کارهای مکث‌شده جداگانه ادامه داده نمی‌شوند." },
    { id: "pause", label: "مکث", title: "توقف در نقطهٔ امن و حفظ checkpoint وظایف فعال." },
    { id: "resume", label: "ادامه", title: "فعال‌سازی ماژول و بازگرداندن وظایف مکث‌شده به صف." },
    { id: "finish", label: "پایان تدریجی", title: "اجازهٔ اتمام کارهای فعال و جلوگیری از شروع کار جدید." },
    { id: "force_stop", label: "توقف اجباری", title: "لغو وظایف منتظر و درخواست لغو تعاونی برای وظایف فعال." }
  ];

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = String(text);
    return node;
  }

  function renderModule(module) {
    const card = element("article", "module-control-card");
    card.dataset.moduleId = module.id;
    const heading = element("div", "module-control-heading");
    const title = element("h3", "", module.name);
    const state = element("span", "module-control-state", stateLabels[module.display_state] || stateLabels[module.state] || module.state);
    state.dataset.state = module.display_state || module.state;
    heading.append(title, state);
    card.append(heading);
    card.append(element("p", "muted", module.description));

    const counts = module.task_counts || {};
    const summary = element("p", "module-control-counts",
      module.external
        ? "اجراهای فعال: " + (counts.active || 0)
        : "در صف: " + (counts.queued || 0) +
          " · فعال: " + (counts.active || 0) +
          " · مکث‌شده: " + (counts.paused || 0) +
          " · لغوشده: " + (counts.cancelled || 0) +
          " · موفق: " + (counts.succeeded || 0) +
          " · ناموفق: " + (counts.failed || 0)
    );
    card.append(summary);

    const toolbar = element("div", "module-control-actions");
    for (const action of actions) {
      const button = element("button", action.id === "force_stop" ? "danger" : "secondary", action.label);
      button.type = "button";
      button.title = module.external ? ({
        start: "فعال‌سازی چرخه و ارسال یک اجرای جدید؛ مکث قبلی خودکار برداشته نمی‌شود.",
        pause: "جلوگیری از شروع وظیفهٔ بعدی؛ وظیفهٔ جاری ممکن است تکمیل شود.",
        resume: "برداشتن مکث و ادامه از وضعیت پایدار؛ یک اجرای جدید درخواست می‌شود.",
        finish: "غیرفعال‌سازی چرخه‌های بعدی؛ اجرای جاری تا پایان ادامه می‌یابد.",
        force_stop: "غیرفعال‌سازی و درخواست لغو اجرای فعال در GitHub Actions."
      })[action.id] : action.title;
      button.dataset.action = action.id;
      const stateValue = module.state;
      const active = Number(counts.active || 0);
      const pending = Number(counts.pending || 0);
      const externalDisabled = module.external && (
        (action.id === "start" && ["running", "paused", "draining", "stopped"].includes(stateValue)) ||
        (action.id === "pause" && stateValue !== "running") ||
        (action.id === "resume" && !["paused", "stopped"].includes(stateValue)) ||
        (action.id === "finish" && !["running", "paused"].includes(stateValue)) ||
        (action.id === "force_stop" && ["stopped", "finished"].includes(stateValue) && active === 0)
      );
      const taskDisabled = !module.external && (
        (action.id === "start" && stateValue === "running") ||
        (action.id === "pause" && stateValue !== "running") ||
        (action.id === "resume" &&
          !["paused", "draining"].includes(stateValue) &&
          Number(counts.paused || 0) === 0 &&
          Number(counts.pause_requested || 0) === 0) ||
        (action.id === "finish" && stateValue !== "running") ||
        (action.id === "force_stop" && stateValue === "stopped" && active === 0 && pending === 0)
      );
      button.disabled = busyModules.has(module.id) || Boolean(externalDisabled || taskDisabled);
      button.addEventListener("click", () => void performAction(module, action.id));
      toolbar.append(button);
    }
    card.append(toolbar);
    return card;
  }

  function renderUnavailableAutonomousModule(message) {
    const card = element("article", "module-control-card");
    card.dataset.moduleId = "autonomous-engineering";
    const heading = element("div", "module-control-heading");
    heading.append(
      element("h3", "", "مهندسی خودکار"),
      element("span", "module-control-state", stateLabels.unavailable)
    );
    heading.lastChild.dataset.state = "unavailable";
    card.append(heading);
    card.append(element("p", "muted", "چرخهٔ مستقل GitHub Actions، Runner و Codex"));
    card.append(element("p", "module-control-counts", message));
    const toolbar = element("div", "module-control-actions");
    for (const action of actions) {
      const button = element("button", action.id === "force_stop" ? "danger" : "secondary", action.label);
      button.type = "button";
      button.disabled = true;
      button.title = "تا زمانی که وضعیت واقعی GitHub قابل تأیید نباشد، کنترل غیرفعال است.";
      toolbar.append(button);
    }
    card.append(toolbar);
    return card;
  }

  function renderAutonomousModule(status) {
    const active = Number(status.active_runs || 0);
    return renderModule({
      id: "autonomous-engineering",
      name: "مهندسی خودکار",
      description: "چرخهٔ مستقل GitHub Actions، Runner و Codex",
      state: status.state,
      display_state: status.state,
      external: true,
      controlBase: "/api/autonomous-engineering",
      task_counts: { active, pending: 0, queued: 0, paused: 0, pause_requested: 0 }
    });
  }

  async function refresh() {
    refreshButton.disabled = true;
    try {
      const [taskResult, autonomousResult] = await Promise.allSettled([
        api("/api/tasks/modules"),
        api("/api/autonomous-engineering/lifecycle")
      ]);
      const cards = [];
      if (taskResult.status === "fulfilled" && Array.isArray(taskResult.value?.modules)) {
        cards.push(...taskResult.value.modules.map(renderModule));
      } else {
        output.textContent = "دریافت وضعیت ماژول‌های صف پایدار ناموفق بود.";
      }
      if (autonomousResult.status === "fulfilled" && autonomousResult.value?.available === true) {
        cards.push(renderAutonomousModule(autonomousResult.value));
      } else {
        const message = autonomousResult.status === "fulfilled"
          ? autonomousResult.value?.message || "وضعیت GitHub قابل تأیید نیست."
          : autonomousResult.reason?.message || "دریافت وضعیت GitHub ناموفق بود.";
        cards.push(renderUnavailableAutonomousModule(message));
      }
      container.replaceChildren(...cards);
      if (taskResult.status === "fulfilled") output.textContent = "وضعیت ماژول‌ها به‌روز شد.";
    } catch (error) {
      output.textContent = "دریافت وضعیت ماژول‌ها ناموفق بود: " + (error.message || "خطای نامشخص");
    } finally {
      refreshButton.disabled = false;
    }
  }

  async function performAction(module, action) {
    if (busyModules.has(module.id)) return;
    if (action === "force_stop" && !window.confirm(
      module.external
        ? "چرخه‌های بعدی مهندسی غیرفعال و اجرای فعال GitHub Actions برای لغو علامت‌گذاری شود؟ پایان پردازهٔ Runner باید جداگانه تأیید شود."
        : "تمام وظایف منتظر این ماژول لغو شوند و برای وظایف فعال درخواست لغو ثبت شود؟ پردازش فعال در اولین نقطهٔ کنترل تعاونی متوقف می‌شود."
    )) return;

    busyModules.add(module.id);
    output.textContent = "در حال اجرای فرمان «" + action + "» برای " + module.name + "…";
    let finalMessage = "";
    try {
      const actionPath = action === "force_stop" ? "force-stop" : action;
      const endpoint = module.controlBase
        ? module.controlBase + "/" + actionPath
        : "/api/tasks/modules/" + encodeURIComponent(module.id) + "/" + action;
      const result = await api(endpoint, { method: "POST" });
      finalMessage = result.module?.message || result.message || "فرمان ماژول ثبت شد.";
    } catch (error) {
      finalMessage = "فرمان ماژول اجرا نشد: " + (error.message || "خطای نامشخص");
    } finally {
      busyModules.delete(module.id);
      await refresh();
      output.textContent = finalMessage;
    }
  }

  refreshButton.addEventListener("click", () => void refresh());
  document.addEventListener("DOMContentLoaded", () => {
    if (!taskManagerSection.hidden) void refresh();
  }, { once: true });
})();
