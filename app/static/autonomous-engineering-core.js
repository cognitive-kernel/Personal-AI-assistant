(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const message = $("agent-message");
  function setMessage(text, kind) {
    if (!message) return;
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
        const detail = typeof payload.detail === "string"
          ? payload.detail
          : (typeof payload.message === "string" ? payload.message : "درخواست ناموفق بود.");
        const failure = new Error(detail);
        failure.status = response.status;
        failure.code = payload.code || response.headers.get("X-Error-Code") || null;
        failure.correlationId = payload.correlation_id || response.headers.get("X-Correlation-ID") || null;
        failure.retryable = payload.retryable === true || response.headers.get("X-Retryable") === "true";
        failure.safeDetails = payload.safe_details && typeof payload.safe_details === "object"
          ? payload.safe_details
          : null;
        const diagnostics = [];
        if (failure.code) diagnostics.push("کد: " + String(failure.code));
        if (failure.correlationId) diagnostics.push("شناسهٔ پیگیری: " + String(failure.correlationId));
        if (diagnostics.length) failure.message += " · " + diagnostics.join(" · ");
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
