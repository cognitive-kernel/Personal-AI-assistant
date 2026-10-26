"use strict";
function visible(element) {
  if (!element) return false;
  const style = window.getComputedStyle(element);
  return style.display !== "none" && style.visibility !== "hidden" && element.getClientRects().length > 0;
}
function findComposer() {
  const selectors = [
    "#prompt-textarea",
    "[data-testid='prompt-textarea']",
    "textarea",
    "[contenteditable='true'][role='textbox']",
    "[contenteditable='true']"
  ];
  for (const selector of selectors) {
    const found = Array.from(document.querySelectorAll(selector)).filter(visible);
    if (found.length) return found[found.length - 1];
  }
  return null;
}
function insertPrompt(prompt) {
  if (typeof prompt !== "string" || !prompt.trim() || prompt.length > 20000) {
    throw new Error("Prompt is empty or exceeds the safe size limit.");
  }
  const composer = findComposer();
  if (!composer) throw new Error("Could not safely identify the ChatGPT message composer.");
  composer.focus();
  if (composer instanceof HTMLTextAreaElement || composer instanceof HTMLInputElement) {
    const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(composer), "value");
    if (setter && setter.set) setter.set.call(composer, prompt);
    else composer.value = prompt;
    composer.dispatchEvent(new InputEvent("input", {bubbles:true,inputType:"insertText",data:prompt}));
  } else {
    composer.textContent = prompt;
    composer.dispatchEvent(new InputEvent("input", {bubbles:true,inputType:"insertText",data:prompt}));
  }
  return {inserted:true, note:"Prompt inserted only. Review it and click ChatGPT's own Send button yourself."};
}
function captureAnswer() {
  const busy = document.querySelector("[data-testid='stop-button'],button[aria-label*='Stop generating'],button[aria-label*='Stop response']");
  if (busy && visible(busy)) throw new Error("ChatGPT may still be generating. Wait until it finishes before capture.");
  const messages = Array.from(document.querySelectorAll("[data-message-author-role='assistant']")).filter(visible);
  if (!messages.length) throw new Error("Could not identify an assistant response on this ChatGPT page.");
  const text = (messages[messages.length - 1].innerText || "").trim();
  if (text.length < 20) throw new Error("The latest assistant response is empty or too short to capture.");
  if (text.length > 100000) throw new Error("The latest response exceeds the module's 100,000-character limit.");
  return {text:text};
}
chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  try {
    if (message && message.type === "ping") sendResponse({ok:true});
    else if (message && message.type === "insert-prompt") sendResponse(Object.assign({ok:true}, insertPrompt(message.prompt)));
    else if (message && message.type === "capture-answer") sendResponse(Object.assign({ok:true}, captureAnswer()));
    else sendResponse({ok:false,error:"Unsupported message."});
  } catch (error) {
    sendResponse({ok:false,error:error && error.message ? error.message : "Browser interaction failed."});
  }
  return false;
});
