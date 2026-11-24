(() => {
  "use strict";
  const core = window.autonomousEngineeringCore;
  if (!core || core.version !== 1) return;
  const { $, request } = core;
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
      return true;
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
        return true;
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
      return true;
    } catch (error) {
      if (!selectionIsCurrent()) {
        progressRefreshQueued = true;
        return;
      }
      $("progress-chart-caption").textContent = error.message || "دریافت دادهٔ پیشرفت ناموفق بود.";
      return false;
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

  window.addEventListener("autonomous-engineering:cleanup-completed", () => { void refreshProgress({queueIfBusy: true}); });
  window.autonomousEngineeringProgress = Object.freeze({version: 1, refresh: refreshProgress});
})();
