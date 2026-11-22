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

  function startPolling({intervalMs, maxIntervalMs, refresh, jitterRatio = 0.15}) {
    if (!Number.isFinite(intervalMs) || intervalMs < 250 ||
        !Number.isFinite(maxIntervalMs) || maxIntervalMs < intervalMs ||
        typeof refresh !== "function" ||
        !Number.isFinite(jitterRatio) || jitterRatio < 0 || jitterRatio > 0.5) {
      throw new TypeError("Invalid autonomous-engineering polling configuration.");
    }
    let stopped = false;
    let inFlight = false;
    let timerId = null;
    let failures = 0;

    function schedule(delayMs) {
      if (stopped || document.hidden) return;
      if (timerId !== null) window.clearTimeout(timerId);
      const jitter = delayMs > 0
        ? Math.floor(delayMs * ((Math.random() * 2 - 1) * jitterRatio))
        : 0;
      timerId = window.setTimeout(tick, Math.max(0, delayMs + jitter));
    }

    async function tick() {
      timerId = null;
      if (stopped || document.hidden || inFlight) return;
      inFlight = true;
      let succeeded = false;
      try {
        succeeded = (await refresh()) !== false;
      } catch (_) {
        succeeded = false;
      } finally {
        inFlight = false;
      }
      if (stopped) return;
      if (succeeded) {
        failures = 0;
        schedule(intervalMs);
      } else {
        failures = Math.min(failures + 1, 8);
        schedule(Math.min(maxIntervalMs, intervalMs * (2 ** failures)));
      }
    }

    function onVisibilityChange() {
      if (document.hidden) {
        if (timerId !== null) window.clearTimeout(timerId);
        timerId = null;
      } else {
        schedule(0);
      }
    }

    document.addEventListener("visibilitychange", onVisibilityChange);
    schedule(0);
    return function stopPolling() {
      if (stopped) return;
      stopped = true;
      if (timerId !== null) window.clearTimeout(timerId);
      timerId = null;
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }

  window.autonomousEngineeringCore = Object.freeze({
    version: 1, $, request, setMessage, startPolling
  });
})();
