(() => {
 "use strict";
 const core=window.dashboardCore;
 const fallback=document.getElementById("chat-output");
 if(!core||core.version!==1){
  if(fallback)fallback.textContent="ماژول گفتگو بارگذاری نشد: قرارداد dashboardCore v1 در دسترس نیست.";
  return;
 }
 const {api,el}=core;
 const required=["chat-messages","chat-conversation-select","chat-output","chat-delete-conversation","chat-new-conversation","chat-refresh-conversations","chat-form","message"];
 const missing=required.filter(id=>!el(id));
 if(missing.length){
  if(fallback)fallback.textContent="ماژول گفتگو به کنترل‌های لازم دسترسی ندارد: "+missing.join(", ");
  return;
 }
 let currentConversationId = null;
 let conversationRefreshInProgress = false;
 let conversationLoadSequence = 0;
 function formatChatTimestamp(value) {
  if (!value) return "";
  const timestamp = String(value).trim();
  // SQLite CURRENT_TIMESTAMP is UTC but has no timezone suffix. Explicitly
  // interpret that legacy format as UTC before converting to the browser's local zone.
  const normalized = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?$/.test(timestamp)
   ? timestamp.replace(" ", "T") + "Z"
   : timestamp;
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return value;
  const pad = number => String(number).padStart(2, "0");
  return date.getFullYear() + "-" + pad(date.getMonth() + 1) + "-" +
   pad(date.getDate()) + " " + pad(date.getHours()) + ":" +
   pad(date.getMinutes()) + ":" + pad(date.getSeconds());
 }
 function renderChatMessages(messages) {
  const container = el("chat-messages");
  container.replaceChildren();
  if (!messages.length) {
   const empty = document.createElement("p");
   empty.className = "muted";
   empty.textContent = "هنوز پیامی در این گفتگو ثبت نشده است.";
   container.append(empty);
   return;
  }
  for (const message of messages) {
   const card = document.createElement("article");
   card.className = "chat-message chat-message-" + message.role;
   const heading = document.createElement("h3");
   heading.textContent = message.role === "user" ? "شما" : "دستیار";
   const body = document.createElement("p");
   body.dir = "auto";
   body.style.whiteSpace = "pre-wrap";
   body.textContent = message.content;
   const time = document.createElement("small");
   time.className = "muted";
   time.textContent = formatChatTimestamp(message.created_at);
   card.append(heading, body, time);
   if (message.role === "assistant" && Number.isInteger(Number(message.id)) && Number(message.id) > 0) {
    const feedback = document.createElement("div");
    feedback.className = "chat-feedback";
    feedback.setAttribute("aria-label", "بازخورد پاسخ");
    const options = [[1, "مفید بود"], [-1, "مفید نبود"]];
    const buttons = [];
    for (const [rating, label] of options) {
     const button = document.createElement("button");
     button.type = "button";
     button.textContent = label;
     button.setAttribute("aria-label", label);
     button.setAttribute("aria-pressed", String(Number(message.feedback_rating) === rating));
     button.addEventListener("click", async () => {
      for (const item of buttons) item.disabled = true;
      try {
       await api(
        "/api/conversations/" + encodeURIComponent(message.conversation_id) +
        "/messages/" + encodeURIComponent(message.id) + "/feedback",
        {method: "POST", body: JSON.stringify({rating})}
       );
       message.feedback_rating = rating;
       for (const item of buttons) {
        const selected = Number(item.dataset.rating) === rating;
        item.setAttribute("aria-pressed", String(selected));
       }
       el("chat-output").textContent = "بازخورد ثبت شد؛ این امتیاز به‌تنهایی صحت دانش را تأیید نمی‌کند.";
      } catch(error) {
       el("chat-output").textContent = "ثبت بازخورد ناموفق بود: " + error.message;
      } finally {
       for (const item of buttons) item.disabled = false;
      }
     });
     button.dataset.rating = String(rating);
     buttons.push(button);
     feedback.append(button);
    }
    card.append(feedback);
   }
   container.append(card);
  }
 }
 async function loadConversationMessages(conversationId) {
  const requestSequence = ++conversationLoadSequence;
  if (!conversationId) { currentConversationId = null; renderChatMessages([]); return; }
  const requestedId = Number(conversationId);
  currentConversationId = requestedId;
  let result;
  try {
   result = await api("/api/conversations/" + encodeURIComponent(requestedId) + "/messages");
  } catch(error) {
   if (requestSequence !== conversationLoadSequence ||
       Number(el("chat-conversation-select").value) !== requestedId) return;
   throw error;
  }
  if (requestSequence !== conversationLoadSequence ||
      Number(el("chat-conversation-select").value) !== requestedId) return;
  if (Number(result.conversation && result.conversation.id) !== requestedId) {
   throw new Error("پاسخ سرویس با گفتگوی انتخاب‌شده مطابقت ندارد.");
  }
  renderChatMessages(result.messages || []);
 }
 async function refreshConversations(options={}) {
  const hasExplicitSelect=Object.prototype.hasOwnProperty.call(options,"selectId");
  const selectId=hasExplicitSelect?options.selectId:currentConversationId;
  const createIfEmpty=options.createIfEmpty!==false;
  if (conversationRefreshInProgress) return;
  conversationRefreshInProgress = true;
  const select = el("chat-conversation-select");
  try {
   let result = await api("/api/conversations?limit=200");
   let conversations = result.conversations || [];
   if (!conversations.length && createIfEmpty) {
    const created = await api("/api/conversations", {method:"POST"});
    conversations = [created.conversation];
   }
   const currentSelection=select.value?Number(select.value):null;
   select.replaceChildren();
   if (!conversations.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "گفتگویی وجود ندارد";
    select.append(option);
    currentConversationId = null;
    el("chat-delete-conversation").disabled = true;
    renderChatMessages([]);
    return;
   }
   for (const conversation of conversations) {
    const option = document.createElement("option");
    option.value = String(conversation.id);
    option.textContent = (conversation.title || "گفتگوی جدید") + " (" + conversation.message_count + ")";
    select.append(option);
   }
   const preferredId=!hasExplicitSelect&&currentSelection?currentSelection:selectId;
   const target = conversations.some(item => Number(item.id) === Number(preferredId))
    ? Number(preferredId) : Number(conversations[0].id);
   select.value = String(target);
   currentConversationId = target;
   el("chat-delete-conversation").disabled = false;
   await loadConversationMessages(target);
  } catch(error) {
   el("chat-output").textContent = "بارگذاری تاریخچهٔ گفتگو ناموفق بود: " + error.message;
  } finally {
   conversationRefreshInProgress = false;
  }
 }
 el("chat-conversation-select").addEventListener("change", async () => {
  const selected = Number(el("chat-conversation-select").value);
  if (!Number.isInteger(selected) || selected < 1) return;
  el("chat-output").textContent = "";
  try { await loadConversationMessages(selected); }
  catch(error) { el("chat-output").textContent = "گفتگو بارگذاری نشد: " + error.message; }
 });
 el("chat-new-conversation").addEventListener("click", async () => {
  const button = el("chat-new-conversation");
  button.disabled = true;
  try {
   const result = await api("/api/conversations", {method:"POST"});
   currentConversationId = Number(result.conversation.id);
   el("chat-output").textContent = "";
   await refreshConversations({selectId:currentConversationId, createIfEmpty:false});
   el("message").focus();
  } catch(error) {
   el("chat-output").textContent = "ایجاد گفتگوی جدید ناموفق بود: " + error.message;
  } finally { button.disabled = false; }
 });
 el("chat-delete-conversation").addEventListener("click", async () => {
  const conversationId = currentConversationId;
  if (!Number.isInteger(conversationId) || conversationId < 1) return;
  const title = el("chat-conversation-select").selectedOptions[0]?.textContent || "گفتگوی انتخاب‌شده";
  if (!window.confirm("گفتگوی «" + title + "» و تمام پیام‌های آن برای همیشه حذف شود؟ این کار قابل بازگشت نیست.")) return;
  const button = el("chat-delete-conversation");
  button.disabled = true;
  try {
   await api("/api/conversations/" + encodeURIComponent(conversationId), {method:"DELETE"});
   currentConversationId = null;
   el("chat-output").textContent = "گفتگو حذف شد.";
   await refreshConversations({selectId:null, createIfEmpty:true});
  } catch(error) {
   el("chat-output").textContent = "حذف گفتگو ناموفق بود: " + error.message;
   button.disabled = !currentConversationId;
  }
 });
 el("chat-refresh-conversations").addEventListener("click", () => refreshConversations());
 el("chat-form").addEventListener("submit", async event => {
  event.preventDefault();
  const button=el("chat-form").querySelector('button[type="submit"]');
  const message=el("message").value.trim();
  if(!message) return;
  if(button) button.disabled=true;
  el("chat-output").textContent="در حال ثبت پیام و صف‌بندی پاسخ…";
  try {
   if(!currentConversationId) {
    const created=await api("/api/conversations",{method:"POST"});
    currentConversationId=Number(created.conversation.id);
   }
   const queued=await api("/api/research/tasks",{method:"POST",body:JSON.stringify({message,conversation_id:currentConversationId})});
   const taskId=queued.task.id;
   el("message").value="";
   await refreshConversations({selectId:currentConversationId,createIfEmpty:false});
   el("chat-output").textContent="پاسخ در صف قرار گرفت: #"+taskId+" ("+queued.task.status+").";
   const poll=async()=>{
    try {
     const current=await api("/api/tasks/"+taskId);
     if(["queued","running","pause_requested","paused","cancel_requested"].includes(current.status)) {
      el("chat-output").textContent="وضعیت پاسخ #"+taskId+": "+current.status;
      setTimeout(poll,1000);
      return;
     }
     if(current.status==="succeeded") {
      await refreshConversations({selectId:currentConversationId,createIfEmpty:false});
      el("chat-output").textContent="پاسخ ذخیره شد.";
     } else {
      await refreshConversations({selectId:currentConversationId,createIfEmpty:false});
      el("chat-output").textContent="پاسخ تکمیل نشد: "+(current.error||current.status);
     }
     if(button) button.disabled=false;
    } catch(error) {
     el("chat-output").textContent="وضعیت پاسخ قابل دریافت نیست: "+error.message;
     if(button) button.disabled=false;
    }
   };
   setTimeout(poll,500);
  } catch(error) {
   el("chat-output").textContent=error.message;
   if(button) button.disabled=false;
  }
 });
 refreshConversations();
})();