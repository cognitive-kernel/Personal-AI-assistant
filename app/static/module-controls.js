(() => {
  "use strict";

  const container = document.getElementById("module-controls-container");
  const output = document.getElementById("module-controls-output");
  const refreshButton = document.getElementById("refresh-module-controls");
  const dashboard = window.dashboardCore;
  if (!container || !output || !refreshButton || !dashboard || typeof dashboard.api !== "function") return;

  const { api } = dashboard;
  const busyModules = new Set();
  const stateLabels = {
    running: "فعال",
    paused: "مکث",
    draining: "در حال پایان",
    stopped: "متوقف",
    finished: "پایان‌یافته"
  };
  const actions = [
    { id: "start", label: "شروع", title: "فعال‌سازی دریافت وظایف جدید؛ کارهای مکث‌شده جداگانه ادامه داده نمی‌شوند." },
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
      button.title = action.title;
      button.dataset.action = action.id;
      const stateValue = module.state;
      const active = Number(counts.active || 0);
      const pending = Number(counts.pending || 0);
      button.disabled = busyModules.has(module.id) ||
        (action.id === "start" && stateValue === "running") ||
        (action.id === "pause" && stateValue !== "running") ||
        (action.id === "resume" && stateValue === "running") ||
        (action.id === "finish" && stateValue !== "running") ||
        (action.id === "force_stop" && stateValue === "stopped" && active === 0 && pending === 0);
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
    if (action === "force_stop" && !window.confirm(
      "تمام وظایف منتظر این ماژول لغو شوند و برای وظایف فعال درخواست لغو ثبت شود؟ پردازش فعال در اولین نقطهٔ کنترل تعاونی متوقف می‌شود."
    )) return;

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
  void refresh();
})();
