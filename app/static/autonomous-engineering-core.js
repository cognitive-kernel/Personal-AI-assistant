(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const message = $("agent-message");
  const statusMessage = $("agent-status-message");
  function setMessage(text, kind) {
    if (!message) return;
    message.textContent = text;
    message.dataset.kind = kind || "";
  }
  function setStatusMessage(text, kind) {
    if (!statusMessage) return;
    statusMessage.textContent = text;
    statusMessage.dataset.kind = kind || "";
  }
  function createCorrelationId() {
    const cryptoApi = window.crypto;
    if (cryptoApi && typeof cryptoApi.randomUUID === "function") {
      return cryptoApi.randomUUID().replace(/-/g, "").toLowerCase();
    }
    if (!cryptoApi || typeof cryptoApi.getRandomValues !== "function") {
      throw new Error("مرورگر امکان تولید شناسهٔ امن برای درخواست را ندارد.");
    }
    const bytes = new Uint8Array(16);
    cryptoApi.getRandomValues(bytes);
    return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
  }

  async function request(path, options) {
    const controller = new AbortController();
    const defaultTimeoutMs = path === "status" ? 40000 : path.startsWith("progress") ? 20000 : 120000;
    const requestedTimeoutMs = options && options.timeoutMs;
    if (requestedTimeoutMs !== undefined &&
        (!Number.isSafeInteger(requestedTimeoutMs) || requestedTimeoutMs < 1 || requestedTimeoutMs > 120000)) {
      throw new RangeError("Invalid autonomous-engineering request timeout.");
    }
    const timeoutMs = requestedTimeoutMs === undefined ? defaultTimeoutMs : requestedTimeoutMs;
    const requestOptions = {...(options || {})};
    delete requestOptions.timeoutMs;
    const correlationId = createCorrelationId();
    const requestHeaders = new Headers(requestOptions.headers || {});
    requestHeaders.set("Accept", "application/json");
    if (requestOptions.body && !requestHeaders.has("Content-Type")) {
      requestHeaders.set("Content-Type", "application/json");
    }
    requestHeaders.set("X-Correlation-ID", correlationId);
    const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch("/api/autonomous-engineering/" + path, {
        ...requestOptions,
        headers: requestHeaders,
        signal: controller.signal
      });
      const responseText = await response.text();
      let payload = {};
      if (responseText.trim()) {
        try {
          payload = JSON.parse(responseText);
        } catch (_) {
          const invalid = new Error("پاسخ سرویس JSON معتبر نیست؛ نتیجهٔ درخواست قابل تأیید نیست.");
          invalid.status = response.ok ? 502 : response.status;
          invalid.code = "AUTONOMOUS_ENGINEERING_INVALID_RESPONSE";
          throw invalid;
        }
      } else if (response.ok) {
        const empty = new Error("پاسخ سرویس خالی بود؛ نتیجهٔ درخواست قابل تأیید نیست.");
        empty.status = 502;
        empty.code = "AUTONOMOUS_ENGINEERING_EMPTY_RESPONSE";
        throw empty;
      }
      if (!response.ok) {
        const detail = typeof payload.detail === "string"
          ? payload.detail
          : (typeof payload.message === "string" ? payload.message : "درخواست ناموفق بود.");
        const failure = new Error(detail);
        failure.status = response.status;
        failure.code = payload.code || response.headers.get("X-Error-Code") || null;
        failure.correlationId = payload.correlation_id || response.headers.get("X-Correlation-ID") || correlationId;
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
      const responseCorrelationId = response.headers.get("X-Correlation-ID") || correlationId;
      if (responseCorrelationId && payload && typeof payload === "object" && !Array.isArray(payload) && !payload.correlation_id) {
        payload.correlation_id = responseCorrelationId;
      }
      return payload;
    } catch (error) {
      if (error && error.name === "AbortError") {
        const timeoutError = new Error("پاسخ سرویس در مهلت " + (timeoutMs / 1000) + " ثانیه دریافت نشد؛ وضعیت سرور و لاگ‌ها را بررسی کنید. · شناسهٔ پیگیری: " + correlationId);
        timeoutError.ambiguous = true;
        timeoutError.correlationId = correlationId;
        throw timeoutError;
      }
      if (error && typeof error === "object" && !Number.isInteger(error.status)) {
        error.ambiguous = true;
        if (!error.correlationId) error.correlationId = correlationId;
        const errorMessage = typeof error.message === "string" ? error.message : "";
        if (!errorMessage.includes(correlationId)) {
          error.message = (errorMessage ? errorMessage + " · " : "") + "شناسهٔ پیگیری: " + correlationId;
        }
      }
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
      const boundedDelay = Math.min(maxIntervalMs, Math.max(0, delayMs + jitter));
      timerId = window.setTimeout(tick, boundedDelay);
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
    version: 1, $, request, setMessage, setStatusMessage, startPolling
  });
})();
