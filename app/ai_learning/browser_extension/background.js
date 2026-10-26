"use strict";
const API = "http://127.0.0.1:8000/api/ai-learning";
const ACTIVE_KEYS = ["activeRunId","activeRunTabId","baselineMessageCount","baselineAnswer"];
async function api(path, options) {
  const response = await fetch(API + path, Object.assign({headers:{"Content-Type":"application/json"},cache:"no-store"},options || {}));
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || ("Local API returned HTTP " + response.status));
  return data;
}
function sendToTab(tabId,message) {
  return new Promise((resolve,reject)=>{
    chrome.tabs.sendMessage(tabId,message,response=>{
      const error=chrome.runtime.lastError;
      if(error) reject(new Error(error.message));
      else if(!response || response.ok!==true) reject(new Error((response && response.error) || "No response from ChatGPT tab."));
      else resolve(response);
    });
  });
}
async function ensureContentScript(tabId) {
  try { await sendToTab(tabId,{type:"ping"}); }
  catch (_) { await chrome.scripting.executeScript({target:{tabId},files:["content.js"]}); await sendToTab(tabId,{type:"ping"}); }
}
async function findOrOpenChatTab() {
  const tabs=await chrome.tabs.query({url:["https://chatgpt.com/*","https://chat.openai.com/*"]});
  if(tabs.length && tabs[0].id!==undefined) { await chrome.tabs.update(tabs[0].id,{active:true}); return tabs[0]; }
  return chrome.tabs.create({url:"https://chatgpt.com/",active:true});
}
async function setBadge(text,color) {
  try { await chrome.action.setBadgeText({text}); if(color) await chrome.action.setBadgeBackgroundColor({color}); } catch (_) {}
}
async function saveStatus(status) {
  await chrome.storage.local.set({lastRunStatus:{...status,updatedAt:new Date().toISOString()}});
}
async function refreshLearningTabs() {
  const tabs = await chrome.tabs.query({url:["http://127.0.0.1:8000/ai-learning*","http://localhost:8000/ai-learning*"]});
  await Promise.all(tabs.filter(tab => Number.isInteger(tab.id)).map(tab => chrome.tabs.reload(tab.id).catch(() => {})));
}
async function prepareRun(payload) {
  const created=await api("/runs",{method:"POST",body:JSON.stringify(payload)});
  const started=await api("/runs/"+created.id+"/start",{method:"POST"});
  const tab=await findOrOpenChatTab();
  if(tab.id===undefined) throw new Error("Could not open a ChatGPT tab.");
  await chrome.storage.local.set({activeRunId:started.id,activeRunTabId:tab.id,baselineMessageCount:null,baselineAnswer:""});
  await setBadge("…","#2463a6");
  await saveStatus({run_id:started.id,status:"running",message:"Preparing ChatGPT request."});
  await new Promise((resolve,reject)=>{
    let done=false,delivering=false;
    const finish=(fn,value)=>{if(done)return;done=true;chrome.tabs.onUpdated.removeListener(listener);clearTimeout(timeout);fn(value);};
    const deliver=async()=>{
      if(done||delivering)return;
      delivering=true;
      try {
        await ensureContentScript(tab.id);
        const baseline=await sendToTab(tab.id,{type:"get-latest-answer"});
        await chrome.storage.local.set({baselineMessageCount:baseline.state.messageCount,baselineAnswer:baseline.state.text||""});
        const inserted=await sendToTab(tab.id,{type:"insert-prompt",prompt:started.prompt_text});
        await sendToTab(tab.id,{type:"start-answer-monitor",options:{runId:started.id,baselineMessageCount:baseline.state.messageCount,baselineAnswer:baseline.state.text||""}});
        let submitted=true,submitMessage="Prompt sent automatically.";
        try { await sendToTab(tab.id,{type:"submit-prompt"}); }
        catch(error) { submitted=false;submitMessage="Prompt inserted but could not be auto-sent: "+error.message+" Send it manually; the extension will still try to capture the answer."; await setBadge("MAN","#a66b00"); }
        await saveStatus({run_id:started.id,status:"running",message:submitMessage});
        finish(resolve,{run_id:started.id,tab_id:tab.id,inserted:inserted.inserted,submitted,message:submitMessage});
      } catch(error) { delivering=false;finish(reject,error); }
    };
    const listener=(updatedId,changeInfo)=>{if(updatedId===tab.id&&changeInfo.status==="complete")deliver();};
    const timeout=setTimeout(()=>finish(reject,new Error("Timed out waiting for ChatGPT. Open the tab and try again.")),30000);
    chrome.tabs.onUpdated.addListener(listener);
    chrome.tabs.get(tab.id).then(current=>{if(current.status==="complete")deliver();}).catch(error=>finish(reject,error));
  });
}
const completingRuns = new Set();
async function completeCapturedRun(runId,tabId,text) {
  if(completingRuns.has(runId)) return {ignored:true};
  completingRuns.add(runId);
  try {
    const stored=await chrome.storage.local.get(ACTIVE_KEYS);
    if(stored.activeRunId!==runId || stored.activeRunTabId!==tabId) return {ignored:true};
    const completed=await api("/runs/"+runId+"/complete",{method:"POST",body:JSON.stringify({response_text:text})});
    const terminal=["succeeded","needs_user_action","failed","cancelled"].includes(completed.status);
    if(terminal) await chrome.storage.local.remove(ACTIVE_KEYS);
    await saveStatus({run_id:runId,status:completed.status,message:completed.error_message || (completed.status==="succeeded"?"Answer captured and saved.":"Answer captured; manual review is required."),topics:completed.topics||[]});
    await setBadge(completed.status==="succeeded"?"OK":completed.status==="needs_user_action"?"!":"ERR",completed.status==="succeeded"?"#247a43":"#a66b00");
    if(completed.status==="succeeded" || completed.status==="needs_user_action") await refreshLearningTabs();
    return {run_id:runId,status:completed.status,topics:completed.topics||[],error_message:completed.error_message||null};
  } finally {
    completingRuns.delete(runId);
  }
}
async function markNeedsAction(runId,errorCode,errorMessage) {
  const stored=await chrome.storage.local.get(ACTIVE_KEYS);
  if(stored.activeRunId!==runId) return;
  try {
    await api("/runs/"+runId+"/needs-user-action",{method:"POST",body:JSON.stringify({error_code:errorCode,error_message:errorMessage})});
  } catch (_) {}
  await chrome.storage.local.remove(ACTIVE_KEYS);
  await saveStatus({run_id:runId,status:"needs_user_action",message:errorMessage});
  await setBadge("MAN","#a66b00");
  await refreshLearningTabs();
}
async function captureRun() {
  const stored=await chrome.storage.local.get(ACTIVE_KEYS);
  if(!stored.activeRunId) throw new Error("No active learning run. Start a request first.");
  const tabs=await chrome.tabs.query({active:true,currentWindow:true}),tab=tabs[0];
  if(!tab||tab.id===undefined) throw new Error("No active tab found.");
  if(stored.activeRunTabId!==tab.id) throw new Error("Switch to the same ChatGPT tab where the prompt was inserted.");
  const result=await sendToTab(tab.id,{type:"capture-answer"});
  if(Number.isInteger(stored.baselineMessageCount)&&result.messageCount<=stored.baselineMessageCount&&result.text===(stored.baselineAnswer||"")) throw new Error("No new assistant response was detected. Send the prepared prompt and wait for completion.");
  return completeCapturedRun(stored.activeRunId,tab.id,result.text);
}
chrome.runtime.onMessage.addListener((message,sender,sendResponse)=>{
  if(message&&message.type==="prepare-run") {
    prepareRun(message.payload).then(result=>sendResponse({ok:true,result})).catch(async error=>{await setBadge("ERR","#b42318");await saveStatus({status:"failed",message:error.message});sendResponse({ok:false,error:error.message});});
    return true;
  }
  if(message&&message.type==="capture-run") {
    captureRun().then(result=>sendResponse({ok:true,result})).catch(error=>sendResponse({ok:false,error:error.message}));
    return true;
  }
  if(message&&message.type==="get-active-run") {
    chrome.storage.local.get([...ACTIVE_KEYS,"lastRunStatus"]).then(value=>sendResponse({ok:true,run_id:value.activeRunId||null,last_status:value.lastRunStatus||null}));
    return true;
  }
  if(message&&message.type==="answer-ready") {
    const runId=message.runId,tabId=sender.tab&&sender.tab.id;
    if(!Number.isInteger(runId)||!Number.isInteger(tabId)||typeof message.text!=="string") return false;
    completeCapturedRun(runId,tabId,message.text).then(result=>sendResponse({ok:true,result})).catch(async error=>{
      await markNeedsAction(runId,"automatic_capture_failed","Automatic capture failed: "+error.message);
      sendResponse({ok:false,error:error.message});
    });
    return true;
  }
  if(message&&message.type==="answer-monitor-timeout") {
    const runId=message.runId;
    markNeedsAction(runId,"response_timeout",message.error||"Automatic capture timed out; use manual capture.")
      .then(()=>sendResponse({ok:true})).catch(error=>sendResponse({ok:false,error:error.message}));
    return true;
  }
  return false;
});
