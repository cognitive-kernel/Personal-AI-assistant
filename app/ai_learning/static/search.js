export const SEARCH_CONTRACT_VERSION=1;
export function createSearchModule(core){
 if(!core||core.CORE_CONTRACT_VERSION!==1)throw new Error("AI-learning search requires core contract v1");
 const {api,say,$,el,act}=core;
let searchRequestSequence=0;
$("search-form").onsubmit=e=>{e.preventDefault();act(async()=>{
 const requestSequence=++searchRequestSequence;let r;try{r=await api("/search?query="+encodeURIComponent($("search-query").value))}catch(error){if(requestSequence!==searchRequestSequence)return;throw error}if(requestSequence!==searchRequestSequence)return;
 const root=$("search-results");root.replaceChildren();
 if(!r.items.length)root.append(el("p","نتیجه‌ای یافت نشد."));
 for(const x of r.items){const card=el("article",undefined,"result");card.append(el("h3",x.course_title+" ← "+x.topic_title+" ← "+x.lesson_title),el("p","نسخه "+x.version_no+"؛ بخش "+x.chunk_id,"muted"),el("pre",x.body,"content"));root.append(card)}
 const feedback=el("section",undefined,"feedback-controls");
 feedback.append(el("p","آیا این نتایج برای پرسش شما مفید بودند؟","muted"));
 const helpful=el("button","مفید بود"),notHelpful=el("button","مفید نبود"),status=el("span","","muted");
 helpful.type="button";notHelpful.type="button";
 const submitFeedback=async rating=>{helpful.disabled=true;notHelpful.disabled=true;try{await api("/feedback",{method:"POST",body:JSON.stringify({query_trace_id:r.trace_id,rating,comment:""})});status.textContent="بازخورد ثبت شد.";say("بازخورد جست‌وجو ثبت شد.")}catch(error){helpful.disabled=false;notHelpful.disabled=false;status.textContent=error.message;say(error.message,true)}};
 helpful.addEventListener("click",()=>submitFeedback(1));notHelpful.addEventListener("click",()=>submitFeedback(-1));
 feedback.append(helpful,notHelpful,status);root.append(feedback);
 say(r.count+" نتیجه بازیابی شد.")
})};

 return Object.freeze({});
}
