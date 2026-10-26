"use strict";
function visible(element) {
  if (!element) return false;
  const style = window.getComputedStyle(element);
  return style.display !== "none" && style.visibility !== "hidden" && element.getClientRects().length > 0;
}
function findComposer() {
  const selectors = ["#prompt-textarea","[data-testid='prompt-textarea']","textarea","[contenteditable='true'][role='textbox']","[contenteditable='true']"];
  for (const selector of selectors) {
    const found = Array.from(document.querySelectorAll(selector)).filter(visible);
    if (found.length) return found[found.length - 1];
  }
  return null;
}
function insertPrompt(prompt) {
  if (typeof prompt !== "string" || !prompt.trim() || prompt.length > 20000) throw new Error("Prompt is empty or exceeds the safe size limit.");
  const composer = findComposer();
  if (!composer) throw new Error("Could not safely identify the ChatGPT message composer.");
  composer.focus();
  if (composer instanceof HTMLTextAreaElement || composer instanceof HTMLInputElement) {
    const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(composer), "value");
    if (setter && setter.set) setter.set.call(composer, prompt); else composer.value = prompt;
    composer.dispatchEvent(new InputEvent("input", {bubbles:true,inputType:"insertText",data:prompt}));
  } else {
    composer.textContent = prompt;
    composer.dispatchEvent(new InputEvent("input", {bubbles:true,inputType:"insertText",data:prompt}));
  }
  return {inserted:true};
}
function latestAssistantState() {
  const messages = Array.from(document.querySelectorAll("[data-message-author-role='assistant']")).filter(visible);
  return {messageCount:messages.length, text:messages.length ? (messages[messages.length - 1].innerText || "").trim() : ""};
}
function findSendButton() {
  const selectors = ["[data-testid='send-button']","button[aria-label='Send prompt']","button[aria-label='Send message']","button[aria-label='Send']"];
  for (const selector of selectors) {
    const button = Array.from(document.querySelectorAll(selector)).find(el => visible(el) && !el.disabled && el.getAttribute("aria-disabled") !== "true");
    if (button) return button;
  }
  return null;
}
function isGenerating() {
  const selectors = ["[data-testid='stop-button']","button[aria-label*='Stop generating']","button[aria-label*='Stop response']","button[aria-label*='Stop streaming']"];
  return selectors.some(selector => Array.from(document.querySelectorAll(selector)).some(visible));
}
function submitPrompt() {
  const composer = findComposer();
  if (!composer || !(composer.innerText || composer.value || "").trim()) throw new Error("The ChatGPT composer is empty; the prompt may not have been inserted.");
  const button = findSendButton();
  if (!button) throw new Error("Could not confidently identify ChatGPT's enabled Send button. The prompt is still in the composer; send it manually and use Capture as fallback.");
  button.click();
  return {submitted:true};
}
function captureAnswer() {
  if (isGenerating()) throw new Error("ChatGPT is still generating. Wait until it finishes before capture.");
  const state = latestAssistantState();
  if (!state.messageCount) throw new Error("Could not identify an assistant response on this ChatGPT page.");
  if (state.text.length < 20) throw new Error("The latest assistant response is empty or too short to capture.");
  if (state.text.length > 100000) throw new Error("The latest response exceeds the module's 100,000-character limit.");
  return {text:state.text,messageCount:state.messageCount};
}
let activeMonitor = null;
function startAnswerMonitor(options) {
  if (!options || !Number.isInteger(options.runId) || options.runId < 1) throw new Error("Invalid run ID for response monitor.");
  if (activeMonitor) { clearInterval(activeMonitor.interval); activeMonitor.observer.disconnect(); activeMonitor = null; }
  const baselineCount = Number.isInteger(options.baselineMessageCount) ? options.baselineMessageCount : 0;
  const baselineText = typeof options.baselineAnswer === "string" ? options.baselineAnswer : "";
  const startedAt = Date.now();
  let lastText = "";
  let stableCount = 0;
  let done = false;
  const finish = (kind, payload) => {
    if (done) return;
    done = true;
    clearInterval(monitor.interval);
    monitor.observer.disconnect();
    if (activeMonitor === monitor) activeMonitor = null;
    chrome.runtime.sendMessage(Object.assign({type:kind,runId:options.runId},payload || {}),()=>void chrome.runtime.lastError);
  };
  const check = () => {
    if (done) return;
    if (Date.now() - startedAt > 300000) { finish("answer-monitor-timeout",{error:"Timed out waiting for a complete ChatGPT response. The run is kept for manual recovery."}); return; }
    const state = latestAssistantState();
    const changed = state.messageCount > baselineCount || (state.text && state.text !== baselineText);
    if (!changed || !state.text || state.text.length < 20 || isGenerating()) { lastText = ""; stableCount = 0; return; }
    if (state.text === lastText) stableCount += 1;
    else { lastText = state.text; stableCount = 0; }
    if (stableCount >= 3 && Date.now() - startedAt >= 2500) {
      if (state.text.length > 100000) { finish("answer-monitor-timeout",{error:"The answer exceeds the 100,000-character capture limit."}); return; }
      finish("answer-ready",{text:state.text,messageCount:state.messageCount});
    }
  };
  const observer = new MutationObserver(check);
  const monitor = {observer,interval:null};
  activeMonitor = monitor;
  observer.observe(document.documentElement,{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:["disabled","aria-label","data-testid"]});
  monitor.interval = setInterval(check,1000);
  check();
  return {monitoring:true,timeout_ms:300000};
}
chrome.runtime.onMessage.addListener((message,_sender,sendResponse)=>{
  try {
    if (message && message.type === "ping") sendResponse({ok:true});
    else if (message && message.type === "get-latest-answer") sendResponse({ok:true,state:latestAssistantState()});
    else if (message && message.type === "insert-prompt") sendResponse(Object.assign({ok:true},insertPrompt(message.prompt)));
    else if (message && message.type === "submit-prompt") sendResponse(Object.assign({ok:true},submitPrompt()));
    else if (message && message.type === "start-answer-monitor") sendResponse(Object.assign({ok:true},startAnswerMonitor(message.options)));
    else if (message && message.type === "capture-answer") sendResponse(Object.assign({ok:true},captureAnswer()));
    else sendResponse({ok:false,error:"Unsupported message."});
  } catch(error) {
    sendResponse({ok:false,error:error && error.message ? error.message : "Browser interaction failed."});
  }
  return false;
});
