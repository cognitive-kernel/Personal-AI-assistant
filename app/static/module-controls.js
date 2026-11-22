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
    stopping: "در حال توقف",
    unavailable: "وضعیت نامشخص",
    disabled: "غیرفعال"
  };
  const actions = [
    { id: "start", label: "شروع", title: "فعال‌سازی پردازش وظایف صف‌شده و جدید؛ کارهای مکث‌شده جداگانه ادامه داده نمی‌شوند." },
    { id: "pause", label: "مکث", title: "توقف در نقطهٔ امن و حفظ checkpoint وظایف فعال." },
    { id: "resume", label: "ادامه", title: "فعال‌سازی ماژول و بازگرداندن وظایف مکث‌شده به صف." },
    { id: "finish", label: "پایان تدریجی", title: "اجازهٔ اتمام کارهای فعال و جلوگیری از شروع کار جدید." },
    { id: "force_stop", label: "توقف اجباری", title: "مکث وظایف منتظر برای ادامهٔ بعدی و درخواست لغو تعاونی برای وظایف فعال." }
  ];

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = String(text);
    return node;
  }

  function actionDescription(module, action) {
    if (module.id === "backup-scheduler") {
      return {
        start: "فعال‌سازی زمان‌بند؛ نخستین snapshot پس از یک بازهٔ کامل اجرا می‌شود.",
        pause: "توقف برنامهٔ snapshotهای بعدی پس از تکمیل snapshot جاری.",
        resume: "فعال‌سازی دوبارهٔ زمان‌بند پشتیبان‌گیری.",
        finish: "تکمیل snapshot جاری و سپس پایان زمان‌بند.",
        force_stop: "ارسال فوری درخواست توقف؛ snapshot در حال اجرا به‌صورت ناامن قطع نمی‌شود."
      }[action.id];
    }
    if (module.id === "ai-learning-browser") {
      return {
        start: "برداشتن مکث/پایان و شروع قدیمی‌ترین اجرای صف‌شده.",
        pause: "مکث سراسری و نگه‌داشتن اجرای فعال برای بازیابی امن.",
        resume: "ادامه از صف پایدار و بازیابی اجرای قبلی.",
        finish: "اجازهٔ اتمام اجرای جاری و جلوگیری از شروع اجرای بعدی.",
        force_stop: "لغو تعاونی اجرای فعال؛ اجراهای صف‌شده برای ادامهٔ بعدی محفوظ می‌مانند."
      }[action.id];
    }
    return action.title;
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
    if (module.message) card.append(element("p", "module-control-note", module.message));
    if (Number.isInteger(module.interval_hours)) {
      card.append(element("p", "module-control-counts", "بازهٔ پشتیبان‌گیری: " + module.interval_hours + " ساعت"));
    }
    if (module.last_backup_status && module.last_backup_status !== "never") {
      card.append(element("p", "module-control-counts", "آخرین snapshot: " + module.last_backup_status));
    }

    const counts = module.task_counts || {};
    const summary = element("p", "module-control-counts",
      "در صف: " + (counts.queued || 0) +
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
      button.title = actionDescription(module, action) || action.title;
      button.dataset.action = action.id;
      const stateValue = module.state;
      const active = Number(counts.active || 0);
      const pending = Number(counts.pending || 0);
      button.disabled = busyModules.has(module.id) ||
        (Array.isArray(module.disabled_actions) && module.disabled_actions.includes(action.id)) ||
        (action.id === "start" && stateValue === "running") ||
        (action.id === "pause" && stateValue !== "running") ||
        (action.id === "resume" &&
          !["paused", "draining", "stopped"].includes(stateValue) &&
          Number(counts.paused || 0) === 0 &&
          Number(counts.pause_requested || 0) === 0) ||
        (action.id === "finish" && stateValue !== "running") ||
        (action.id === "force_stop" && stateValue === "stopped" && active === 0);
      button.addEventListener("click", () => void performAction(module, action.id));
      toolbar.append(button);
    }
    card.append(toolbar);
    return card;
  }

  async function refresh() {
    refreshButton.disabled = true;
    try {
      const result = await api("/api/tasks/modules");
      if (!result || !Array.isArray(result.modules)) throw new Error("ساختار پاسخ فهرست ماژول‌ها معتبر نیست.");
      container.replaceChildren(...result.modules.map(renderModule));
      output.textContent = "وضعیت ماژول‌ها به‌روز شد.";
    } catch (error) {
      output.textContent = "دریافت وضعیت ماژول‌ها ناموفق بود: " + (error.message || "خطای نامشخص");
    } finally {
      refreshButton.disabled = false;
    }
  }

  async function performAction(module, action) {
    if (busyModules.has(module.id)) return;
    if (action === "force_stop") {
      const confirmation = module.id === "backup-scheduler"
        ? "درخواست توقف زمان‌بند ثبت شود؟ snapshot در حال اجرا، در صورت وجود، باید ایمن کامل شود."
        : module.id === "ai-learning-browser"
          ? "اجرای فعال یادگیری لغو تعاونی شود؟ اجراهای صف‌شده برای ادامهٔ بعدی حفظ می‌شوند."
          : "وظایف منتظر برای ادامهٔ بعدی مکث شوند و برای وظایف فعال درخواست لغو ثبت شود؟ پردازش فعال در اولین نقطهٔ کنترل تعاونی متوقف می‌شود.";
      if (!window.confirm(confirmation)) return;
    }

    busyModules.add(module.id);
    output.textContent = "در حال اجرای فرمان «" + action + "» برای " + module.name + "…";
    let finalMessage = "";
    try {
      const result = await api(
        "/api/tasks/modules/" + encodeURIComponent(module.id) + "/" + action,
        { method: "POST" }
      );
      finalMessage = result.module?.message || "فرمان ماژول ثبت شد.";
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
