(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const message = $("agent-message");
  if (!message) return;
  function setMessage(text, kind) {
    message.textContent = text;
    message.dataset.kind = kind || "";
  }
  async function request(path, options) {
    const controller = new AbortController();
    const timeoutMs = path === "status" ? 40000 : path.startsWith("progress") ? 20000 : 120000;
    const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch("/api/autonomous-engineering/" + path, {
        ...(options || {}),
        headers: {
          "Accept": "application/json",
          ...(options && options.body ? {"Content-Type": "application/json"} : {}),
          ...(options && options.headers || {})
        },
        signal: controller.signal
      });
      let payload = {};
      try { payload = await response.json(); } catch (_) {}
      if (!response.ok) {
        const failure = new Error(payload.detail || "درخواست ناموفق بود.");
        failure.status = response.status;
        throw failure;
      }
      return payload;
    } catch (error) {
      if (error && error.name === "AbortError") {
        const timeoutError = new Error("پاسخ سرویس در مهلت " + (timeoutMs / 1000) + " ثانیه دریافت نشد؛ وضعیت سرور و لاگ‌ها را بررسی کنید.");
        timeoutError.ambiguous = true;
        throw timeoutError;
      }
      if (error && !Number.isInteger(error.status)) error.ambiguous = true;
      throw error;
    } finally {
      window.clearTimeout(timeoutId);
    }
  }

  window.autonomousEngineeringCore = Object.freeze({version: 1, $, request, setMessage});
})();
