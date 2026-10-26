"use strict";
const API = "http://127.0.0.1:8000/api/ai-learning";

async function api(path, options) {
  const response = await fetch(API + path, Object.assign({
    headers: {"Content-Type":"application/json"},
    cache: "no-store"
  }, options || {}));
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || ("Local API returned HTTP " + response.status));
  return data;
}
function sendToTab(tabId, message) {
  return new Promise((resolve, reject) => {
    chrome.tabs.sendMessage(tabId, message, response => {
      const error = chrome.runtime.lastError;
      if (error) reject(new Error(error.message));
      else if (!response || response.ok !== true) reject(new Error((response && response.error) || "No response from ChatGPT tab."));
      else resolve(response);
    });
  });
}
async function ensureContentScript(tabId) {
  try {
    await sendToTab(tabId, {type:"ping"});
  } catch (_) {
    await chrome.scripting.executeScript({target:{tabId:tabId}, files:["content.js"]});
  }
}
async function findOrOpenChatTab() {
  const tabs = await chrome.tabs.query({url:["https://chatgpt.com/*","https://chat.openai.com/*"]});
  if (tabs.length && tabs[0].id !== undefined) {
    await chrome.tabs.update(tabs[0].id, {active:true});
    return tabs[0];
  }
  return await chrome.tabs.create({url:"https://chatgpt.com/", active:true});
}
async function prepareRun(payload) {
  const created = await api("/runs", {method:"POST",body:JSON.stringify(payload)});
  const started = await api("/runs/" + created.id + "/start", {method:"POST"});
  const tab = await findOrOpenChatTab();
  if (tab.id === undefined) throw new Error("Could not open a ChatGPT tab.");
  await chrome.storage.local.set({activeRunId:started.id, activeRunTabId:tab.id, baselineMessageCount:null, baselineAnswer:""});
  await new Promise((resolve, reject) => {
    let done = false;
    const finish = (fn, value) => {
      if (done) return;
      done = true;
      chrome.tabs.onUpdated.removeListener(listener);
      clearTimeout(timeout);
      fn(value);
    };
    const deliver = async () => {
      try {
        await ensureContentScript(tab.id);
        const baseline = await sendToTab(tab.id, {type:"get-latest-answer"});
        await chrome.storage.local.set({
          baselineMessageCount: baseline.state.messageCount,
          baselineAnswer: baseline.state.text || ""
        });
        const result = await sendToTab(tab.id, {type:"insert-prompt", prompt:started.prompt_text});
        finish(resolve, {run_id:started.id, tab_id:tab.id, inserted:result.inserted});
      } catch (error) { finish(reject, error); }
    };
    const listener = (updatedId, changeInfo) => {
      if (updatedId === tab.id && changeInfo.status === "complete") deliver();
    };
    const timeout = setTimeout(() => finish(reject, new Error("Timed out waiting for ChatGPT. Open the tab and try again.")), 25000);
    chrome.tabs.onUpdated.addListener(listener);
    chrome.tabs.get(tab.id).then(current => {
      if (current.status === "complete") deliver();
    }).catch(error => finish(reject,error));
  });
}
async function captureRun() {
  const stored = await chrome.storage.local.get(["activeRunId","activeRunTabId","baselineMessageCount","baselineAnswer"]);
  const runId = stored.activeRunId;
  if (!runId) throw new Error("No active learning run. Prepare a prompt first.");
  const tabs = await chrome.tabs.query({active:true, currentWindow:true});
  const tab = tabs[0];
  if (!tab || tab.id === undefined) throw new Error("No active tab found.");
  if (stored.activeRunTabId !== tab.id) throw new Error("Switch to the same ChatGPT tab where this prompt was inserted before capturing.");
  const result = await sendToTab(tab.id, {type:"capture-answer"});
  if (Number.isInteger(stored.baselineMessageCount) &&
      result.messageCount <= stored.baselineMessageCount &&
      result.text === (stored.baselineAnswer || "")) {
    throw new Error("No new assistant response was detected for this run. Send the prepared prompt, wait for completion, then capture again.");
  }
  const completed = await api("/runs/" + runId + "/complete", {
    method:"POST", body:JSON.stringify({response_text:result.text})
  });
  if (completed.status === "succeeded") await chrome.storage.local.remove(["activeRunId","activeRunTabId","baselineMessageCount","baselineAnswer"]);
  return {run_id:runId,status:completed.status,topics:completed.topics || [],error_message:completed.error_message || null};
}
chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message && message.type === "prepare-run") {
    prepareRun(message.payload).then(result => sendResponse({ok:true,result})).catch(error => sendResponse({ok:false,error:error.message}));
    return true;
  }
  if (message && message.type === "capture-run") {
    captureRun().then(result => sendResponse({ok:true,result})).catch(error => sendResponse({ok:false,error:error.message}));
    return true;
  }
  if (message && message.type === "get-active-run") {
    chrome.storage.local.get("activeRunId").then(value => sendResponse({ok:true,run_id:value.activeRunId || null}));
    return true;
  }
  return false;
});
