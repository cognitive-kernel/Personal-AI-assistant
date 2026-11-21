(() => {
 "use strict";
 function createCloudResourceCards({el,api,appendText,refreshCloudResources}) {
  if(typeof el!=="function"||typeof api!=="function"||typeof appendText!=="function"||typeof refreshCloudResources!=="function")
   throw new TypeError("Cloud resource cards require the v1 view dependencies.");
  function renderCloudCandidates(candidates) {
 const container=el("cloud-candidates");
 container.replaceChildren();
 if(!candidates.length) {
  appendText(container,"p","هنوز نامزدی ثبت نشده است. اجرای جست‌وجوی جدید را انتخاب کنید.");
  return;
 }
 candidates.forEach(candidate=>{
  const card=document.createElement("article");
  card.className="cloud-candidate";
  appendText(card,"h3",candidate.title||candidate.provider_domain);
  const linkLine=document.createElement("p");
  const link=document.createElement("a");
  try {
   const source=new URL(candidate.source_url);
   if(source.protocol!=="https:") throw new Error("HTTPS required");
   link.href=source.href; link.target="_blank"; link.rel="noopener noreferrer";
   link.textContent=candidate.source_url;
   linkLine.append(link);
  } catch(_) {
   linkLine.textContent="نشانی منبع نامعتبر است و پیوند باز نمی‌شود.";
  }
  card.append(linkLine);
  appendText(card,"p","دامنه: "+candidate.provider_domain+" | بازبینی: "+candidate.review_status+" | ظرفیت: "+candidate.availability_status+" | تازگی: "+candidate.freshness_status,"cloud-status");
  const authorityLabel=candidate.source_authority==="recognized_official_domain"
   ? "دامنهٔ رسمی شناخته‌شده"
   : "دامنهٔ رسمی در فهرست شناخته‌شده نیست";
  appendText(card,"p","شناخت دامنه: "+authorityLabel,"cloud-status");
  const sourceClassificationLabels={
   recognized_provider_domain:"صفحه روی دامنهٔ رسمی شناخته‌شده (تشخیص دامنه‌ای؛ نه تأیید محتوا)",
   likely_intermediary_article:"مقاله/مقایسهٔ واسطهٔ احتمالی — ادعاها را در سایت ارائه‌دهنده بررسی کنید",
   unclassified_unverified_domain:"نوع منبع نامشخص؛ دامنهٔ رسمی تأیید نشده"
  };
  appendText(card,"p","نوع منبع: "+(sourceClassificationLabels[candidate.source_classification]||sourceClassificationLabels.unclassified_unverified_domain),"cloud-status");
  const assessment=candidate.eligibility_assessment||{status:"needs_review",execution_authorized:false,blockers:["پیش‌ارزیابی در دسترس نیست."],review_needed:[]};
  const assessmentLabels={blocked:"مسدود",needs_review:"نیازمند بازبینی",ready_for_provider_adapter:"آماده برای adapter ارائه‌دهنده"};
  appendText(card,"p","پیش‌ارزیابی: "+(assessmentLabels[assessment.status]||assessment.status)+" | اجرای راه دور مجاز: "+(assessment.execution_authorized?"بله":"خیر"),"cloud-status");
  const details=document.createElement("details");
  const summary=document.createElement("summary");
  summary.textContent="جزئیات شواهد و ادعاها ("+(candidate.observed_claims||[]).length+")";
  details.append(summary);
  (assessment.blockers||[]).forEach(reason=>appendText(details,"p","مانع: "+reason,"muted"));
  (assessment.review_needed||[]).forEach(reason=>appendText(details,"p","نیاز بعدی: "+reason,"muted"));
  appendText(details,"p","سیگنال‌های متنی: "+((candidate.resource_signals||[]).join("، ")||"موردی شناسایی نشد"));
  appendText(details,"p","آخرین مشاهده: "+candidate.last_seen_at);
  appendText(details,"p",candidate.evidence_excerpt||"خلاصه شواهد موجود نیست.","cloud-evidence");
  appendText(details,"h4","ادعاهای استخراج‌شده از متن (هنوز تأییدنشده)");
  const claims=candidate.observed_claims||[];
  if(!claims.length) appendText(details,"p","ادعای ساختاریافته‌ای با الگوهای فعلی پیدا نشد.","muted");
  else {
   const claimList=document.createElement("ul");
   claims.forEach(claim=>{
    const item=document.createElement("li");
    appendText(item,"span",claim.claim_type+": "+JSON.stringify(claim.value)+(claim.unit?" "+claim.unit:"")+" — «"+claim.evidence_quote+"» ["+claim.review_status+"] ");
    const controls=document.createElement("span"); controls.className="cloud-actions";
    const accept=document.createElement("button"); accept.type="button"; accept.textContent="تأیید ادعا";
    accept.disabled=candidate.freshness_status==="stale"||candidate.review_status!=="approved";
    const reject=document.createElement("button"); reject.type="button"; reject.textContent="رد ادعا";
    reject.disabled=candidate.freshness_status==="stale";
    const claimFeedback=document.createElement("span"); claimFeedback.className="muted";
    const sendClaimReview=async decision=>{
     claimFeedback.textContent="در حال ذخیره…"; accept.disabled=true; reject.disabled=true;
     try {
      await api("/api/cloud-resources/"+candidate.id+"/claims/"+claim.id+"/review",{
       method:"PATCH",
       body:JSON.stringify({
        expected_source_content_sha256:claim.source_content_sha256,
        review_status:decision
       })
      });
      claimFeedback.textContent="ذخیره شد.";
      await refreshCloudResources();
     } catch(error) {
      claimFeedback.textContent="ذخیره نشد: "+error.message;
      accept.disabled=candidate.freshness_status==="stale"||candidate.review_status!=="approved";
      reject.disabled=candidate.freshness_status==="stale";
     }
    };
    accept.addEventListener("click",()=>sendClaimReview("accepted"));
    reject.addEventListener("click",()=>sendClaimReview("rejected"));
    controls.append(accept,reject,claimFeedback); item.append(controls); claimList.append(item);
   });
   details.append(claimList);
  }
  if(candidate.freshness_status==="stale") {
   appendText(details,"p","شواهد قدیمی است؛ پیش از بازبینی باید منبع دوباره کشف شود.","muted");
  }
  const form=document.createElement("form");
  const grid=document.createElement("div"); grid.className="cloud-form-grid";
  const reviewLabel=document.createElement("label"); reviewLabel.textContent="تصمیم بازبینی";
  const review=document.createElement("select"); review.required=true;
  [["","انتخاب تصمیم"],["approved","تأیید شواهد"],["rejected","رد منبع"]].forEach(([value,label])=>{
   const option=document.createElement("option"); option.value=value; option.textContent=label; review.append(option);
  });
  if(["approved","rejected"].includes(candidate.review_status)) review.value=candidate.review_status;
  review.addEventListener("change",()=>{if(review.value==="rejected"&&availability.value==="available")availability.value="unknown";});
  reviewLabel.append(review); grid.append(reviewLabel);
  const availabilityLabel=document.createElement("label"); availabilityLabel.textContent="وضعیت ظرفیت";
  const availability=document.createElement("select");
  [["unknown","نامشخص"],["available","در دسترس (بررسی‌شده)"],["unavailable","در دسترس نیست"],["requires_auth","نیازمند احراز هویت"]].forEach(([value,label])=>{
   const option=document.createElement("option"); option.value=value; option.textContent=label; availability.append(option);
  });
  availability.value=candidate.availability_status||"unknown";
  availabilityLabel.append(availability); grid.append(availabilityLabel);
  const termsLabel=document.createElement("label"); termsLabel.textContent="نشانی HTTPS شرایط استفاده (برای تأیید الزامی)";
  const terms=document.createElement("input"); terms.type="url"; terms.placeholder="https://…"; terms.setAttribute("aria-label", "نشانی HTTPS شرایط استفاده"); terms.value=candidate.terms_url||"";
  termsLabel.append(terms); grid.append(termsLabel);
  const quotaLabel=document.createElement("label"); quotaLabel.textContent="سهمیه‌های تأییدشده به‌شکل JSON";
  const quota=document.createElement("textarea"); quota.rows=3; quota.value=JSON.stringify(candidate.quota||{},null,2);
  quotaLabel.append(quota); grid.append(quotaLabel);
  form.append(grid);
  const actions=document.createElement("div"); actions.className="cloud-actions";
  const submit=document.createElement("button"); submit.type="submit"; submit.textContent="ذخیره بازبینی";
  submit.disabled=candidate.freshness_status==="stale";
  actions.append(submit); form.append(actions);
  const feedback=document.createElement("p"); feedback.className="muted"; form.append(feedback);
  form.addEventListener("submit",async event=>{
   event.preventDefault(); feedback.textContent="در حال ذخیره…"; submit.disabled=true;
   try {
    let quotaValue;
    try { quotaValue=JSON.parse(quota.value||"{}"); } catch(_) { throw new Error("JSON سهمیه معتبر نیست."); }
    if(!quotaValue||Array.isArray(quotaValue)||typeof quotaValue!=="object") throw new Error("سهمیه باید یک شیء JSON باشد.");
    const result=await api("/api/cloud-resources/"+candidate.id+"/review",{
     method:"PATCH",
     body:JSON.stringify({
      expected_content_sha256:candidate.content_sha256,
      review_status:review.value,
      availability_status:availability.value,
      quota:quotaValue,
      terms_url:terms.value.trim()||null
     })
    });
    feedback.textContent="بازبینی ذخیره شد: "+result.candidate.review_status;
    await refreshCloudResources();
   } catch(error) {
    feedback.textContent="ذخیره نشد: "+error.message;
    submit.disabled=candidate.freshness_status==="stale";
   }
  });
  details.append(form);
  const deleteButton=document.createElement("button");
  deleteButton.type="button";
  deleteButton.className="danger";
  deleteButton.textContent="حذف منبع";
  deleteButton.setAttribute("aria-label","حذف منبع "+(candidate.title||candidate.id));
  deleteButton.addEventListener("click",async()=>{
   if(!window.confirm("منبع «"+(candidate.title||candidate.source_url)+"» حذف شود؟ اگر در تاریخچهٔ برنامه‌های انتخاب ثبت شده باشد، حذف توسط سرور رد می‌شود.")) return;
   deleteButton.disabled=true;
   try {
    await api("/api/cloud-resources/"+encodeURIComponent(candidate.id),{method:"DELETE"});
    await refreshCloudResources();
   } catch(error) {
    const feedback=appendText(card,"p","حذف منبع انجام نشد: "+error.message,"muted");
    feedback.setAttribute("role","status");
    deleteButton.disabled=false;
   }
  });
  const deleteActions=document.createElement("div"); deleteActions.className="cloud-actions";
  deleteActions.append(deleteButton);
  card.append(details,deleteActions); container.append(card);
 });
}

  return Object.freeze({renderCloudCandidates});
 }
 Object.defineProperty(window,"cloudResourceCardsFactory",{
  value:Object.freeze({version:1,createCloudResourceCards}),
  enumerable:true,configurable:false,writable:false
 });
})();
