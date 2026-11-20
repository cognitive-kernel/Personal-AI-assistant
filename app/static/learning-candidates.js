/* Learning-candidate UI: isolated from the dashboard shell. */
(() => {
 const shared = window.PersonalAssistantUI;
 if (!shared || typeof shared.api !== "function" || typeof shared.el !== "function") {
  console.error("Learning-candidate UI contract is unavailable; panel remains inactive.");
  return;
 }
 const { api, el } = shared;
async function refreshLearningCandidates() {
 const container=el("learning-candidates");
 container.textContent="در حال بارگذاری نامزدهای موقت…";
 try {
  const result=await api("/api/learning/candidates?limit=50");
  container.replaceChildren();
  const items=result.items||[];
  if(!items.length){container.textContent="نامزد موقتی ثبت نشده است.";return;}
  for(const item of items){
   const card=document.createElement("article");
   const heading=document.createElement("h4"); heading.textContent="#"+item.id+" — "+item.topic;
   const meta=document.createElement("p");
   meta.textContent="وضعیت: "+item.status+" | صحت hash: "+(item.integrity_valid?"تأیید شد":"نامعتبر")+" | منابع: "+(item.source_urls||[]).length;
   const preview=document.createElement("p"); preview.textContent=item.content_preview||"پیش‌نمایش موجود نیست.";
   const failure=document.createElement("p"); failure.className="muted";
   failure.textContent=item.failure_reason?"جزئیات وضعیت: "+item.failure_reason:"";
   const actions=document.createElement("div"); actions.className="cloud-actions";
   const detailButton=document.createElement("button"); detailButton.type="button"; detailButton.textContent="نمایش متن و شواهد کامل"; detailButton.disabled=false;
   detailButton.addEventListener("click",async()=>{
    try{
     const detail=await api("/api/learning/candidates/"+item.id);
     let pre=card.querySelector("pre.candidate-detail");
     if(!pre){pre=document.createElement("pre");pre.className="candidate-detail";card.append(pre);}
     pre.textContent=JSON.stringify({integrity_valid:detail.candidate.integrity_valid,content:detail.candidate.content,source_urls:detail.candidate.source_urls,quality_report:detail.candidate.quality_report,test_plan:detail.candidate.test_plan,test_results:detail.candidate.test_results,failure_reason:detail.candidate.failure_reason},null,2);
    }catch(error){meta.textContent=error.message;}
   });
   const reviewButton=document.createElement("button"); reviewButton.type="button"; reviewButton.textContent="شروع بررسی وب و peer-AI";
   reviewButton.hidden=item.status!=="awaiting_test";
   reviewButton.disabled=item.status!=="awaiting_test"||!item.integrity_valid;
   reviewButton.addEventListener("click",async()=>{
    reviewButton.disabled=true; meta.textContent="در حال ثبت کار بررسی در صف پایدار…";
    try{
     const requestId=(globalThis.crypto&&typeof globalThis.crypto.randomUUID==="function")?globalThis.crypto.randomUUID():(Date.now().toString(36)+"-"+Math.random().toString(36).slice(2));
     const queued=await api("/api/learning/candidates/"+item.id+"/review",{method:"POST",body:JSON.stringify({confirm:true,request_id:requestId,query:item.topic,web_mode:"search"})});
     meta.textContent="کار بررسی ثبت شد؛ شناسهٔ کار: "+queued.task_id+"؛ وضعیت: "+queued.task_status+". نتیجه به‌تنهایی دانش را تأیید نمی‌کند.";
    }catch(error){meta.textContent=error.message;reviewButton.disabled=false;}
   });
   const historyButton=document.createElement("button"); historyButton.type="button"; historyButton.textContent="نمایش سوابق وب و peer-AI";
   const readinessButton=document.createElement("button"); readinessButton.type="button"; readinessButton.textContent="نمایش وضعیت شواهد و تناقض‌ها";
   readinessButton.addEventListener("click",async()=>{try{const readiness=await api("/api/learning/candidates/"+item.id+"/readiness");let pre=card.querySelector(".candidate-readiness");if(!pre){pre=document.createElement("pre");pre.className="candidate-readiness";card.append(pre);}pre.textContent=JSON.stringify(readiness,null,2);}catch(error){meta.textContent=error.message;}});
   historyButton.addEventListener("click",async()=>{
    try{
     const history=await api("/api/learning/candidates/"+item.id+"/review-history");
     let pre=card.querySelector(".candidate-review-history");
     if(!pre){pre=document.createElement("pre");pre.className="candidate-review-history";card.append(pre);}
     pre.textContent=JSON.stringify(history,null,2);
    }catch(error){meta.textContent=error.message;}
   });
   const planExists=Array.isArray(item.test_plan)?item.test_plan.length>0:Object.keys(item.test_plan||{}).length>0;
   const planButton=document.createElement("button"); planButton.type="button";
   planButton.textContent=planExists?"طرح آزمون ثبت شده":"ثبت طرح آزمون";
   planButton.hidden=item.status!=="awaiting_test";
   planButton.disabled=item.status!=="awaiting_test"||planExists||!item.integrity_valid;
   planButton.addEventListener("click",()=>{
    if(card.querySelector("form"))return;
    const form=document.createElement("form");
    const name=document.createElement("input"); name.required=true; name.maxLength=120; name.placeholder="نام آزمون";
    const objective=document.createElement("textarea"); objective.required=true; objective.minLength=10; objective.maxLength=1000; objective.rows=2; objective.placeholder="هدف آزمون";
    const expected=document.createElement("textarea"); expected.required=true; expected.minLength=10; expected.maxLength=1000; expected.rows=2; expected.placeholder="توضیح نتیجهٔ مورد انتظار از oracle مستقل";
    const oracleSelect=document.createElement("select");
    const noOracle=document.createElement("option"); noOracle.value=""; noOracle.textContent="Oracle نسخه‌دار اختیاری — انتخاب نشده"; oracleSelect.append(noOracle);
    const claimSelect=document.createElement("select");
    const noClaim=document.createElement("option"); noClaim.value=""; noClaim.textContent="ادعای canonical oracle را انتخاب کنید"; claimSelect.append(noClaim);
    let oracleCatalog=[];
    api("/api/learning/candidates/oracles").then(catalog=>{
     oracleCatalog=Array.isArray(catalog.items)?catalog.items:[];
     for(const entry of oracleCatalog){
      const option=document.createElement("option");
      option.value=entry.oracle_id+"::"+entry.version;
      option.textContent=entry.oracle_id+" v"+entry.version+" ("+entry.domain+")";
      oracleSelect.append(option);
     }
    }).catch(error=>{status.textContent="بارگذاری کاتالوگ oracle ناموفق بود: "+error.message;});
    oracleSelect.addEventListener("change",()=>{
     claimSelect.replaceChildren();
     const placeholder=document.createElement("option"); placeholder.value=""; placeholder.textContent="ادعای canonical oracle را انتخاب کنید"; claimSelect.append(placeholder);
     const selected=oracleSelect.value.split("::");
     const entry=oracleCatalog.find(item=>item.oracle_id===selected[0]&&String(item.version)===selected[1]);
     if(entry){
      for(const claim of entry.canonical_claims||[]){
       const option=document.createElement("option"); option.value=claim; option.textContent=claim; claimSelect.append(option);
      }
     }
     claimSelect.required=Boolean(oracleSelect.value);
    });
    const oracleHelp=document.createElement("p"); oracleHelp.textContent="کاتالوگ Oracle موقت است و به بازبینی مستقل انسانی نیاز دارد. تطبیق فقط ادعای canonical نسخه‌دار را با متن نامزد مقایسه می‌کند؛ درستی معنایی کل درس را ثابت نمی‌کند و دانش را ارتقا نمی‌دهد.";
    const testCode=document.createElement("textarea"); testCode.maxLength=8192; testCode.rows=4; testCode.placeholder="کد آزمون Python اختیاری؛ برای اجرای sandbox لازم است";
    const expectedStdout=document.createElement("textarea"); expectedStdout.maxLength=1000; expectedStdout.rows=2; expectedStdout.placeholder="stdout دقیق مورد انتظار؛ print معمولی newline پایانی دارد";
    const note=document.createElement("textarea"); note.required=true; note.minLength=20; note.maxLength=1000; note.rows=2; note.placeholder="یادداشت بررسی انسانی oracle (حداقل ۲۰ نویسه)";
    const confirmLabel=document.createElement("label"); const confirm=document.createElement("input"); confirm.type="checkbox"; confirm.required=true; confirmLabel.append(confirm,document.createTextNode("ثبت این طرح را تأیید می‌کنم"));
    const oracleLabel=document.createElement("label"); const oracle=document.createElement("input"); oracle.type="checkbox"; oracle.required=true; oracleLabel.append(oracle,document.createTextNode("نتیجهٔ مورد انتظار با مرجع مستقل بررسی شده است"));
    const submit=document.createElement("button"); submit.type="submit"; submit.textContent="ذخیرهٔ طرح بدون اجرای آزمون";
    const status=document.createElement("p"); status.setAttribute("role","status");
    form.append(name,objective,expected,oracleSelect,claimSelect,oracleHelp,testCode,expectedStdout,note,confirmLabel,oracleLabel,submit,status);
    form.addEventListener("submit",async event=>{
     event.preventDefault(); submit.disabled=true;
     try{
      const test={name:name.value,objective:objective.value,expected_result:expected.value,python_test_code:testCode.value.trim()||null,expected_stdout:testCode.value.trim()?expectedStdout.value:null};
      if(oracleSelect.value){
       const selected=oracleSelect.value.split("::");
       test.oracle_id=selected[0]; test.oracle_version=Number(selected[1]); test.claim=claimSelect.value;
      }
      const saved=await api("/api/learning/candidates/"+item.id+"/test-plan",{method:"POST",body:JSON.stringify({confirm:confirm.checked,oracle_reviewed:oracle.checked,review_note:note.value,tests:[test]})});
      status.textContent=saved.note||"طرح آزمون ذخیره شد؛ آزمون اجرا نشده است.";
      await refreshLearningCandidates();
     }catch(error){status.textContent=error.message;submit.disabled=false;}
    });
    card.append(form);
   });
   const sandboxButton=document.createElement("button"); sandboxButton.type="button"; sandboxButton.textContent="اجرای آزمون‌های Python در sandbox";
   sandboxButton.hidden=item.status!=="awaiting_test";
   sandboxButton.disabled=item.status!=="awaiting_test"||!item.integrity_valid||!planExists||item.test_plan?.oracle_reviewed!==true||!Array.isArray(item.test_plan?.tests)||item.test_plan.tests.length<1||item.test_plan.tests.length>3||item.test_plan.tests.some(test=>typeof test.python_test_code!=="string"||!test.python_test_code.trim()||typeof test.expected_stdout!=="string");
   sandboxButton.addEventListener("click",async()=>{
    if(!window.confirm("کد آزمون ثبت‌شده در محیط Docker بدون شبکه اجرا و stdout دقیق مقایسه می‌شود. نتیجه به‌تنهایی درستی علمی را ثابت نمی‌کند و دانش را ارتقا نمی‌دهد. ادامه می‌دهید؟"))return;
    sandboxButton.disabled=true; meta.textContent="در حال ثبت آزمون محدود sandbox در صف پایدار…";
    try{
     const requestId=(globalThis.crypto&&typeof globalThis.crypto.randomUUID==="function")?globalThis.crypto.randomUUID():(Date.now().toString(36)+"-"+Math.random().toString(36).slice(2));
     const queued=await api("/api/learning/candidates/"+item.id+"/sandbox-test",{method:"POST",body:JSON.stringify({confirm:true,request_id:requestId})});
     meta.textContent="آزمون sandbox در صف ثبت شد؛ شناسهٔ کار: "+queued.task_id+". نتیجهٔ آزمون ثبت شد؛ دانش ارتقا نمی‌یابد.";
    }catch(error){meta.textContent=error.message;sandboxButton.disabled=false;}
   });
   actions.append(detailButton,historyButton,readinessButton,reviewButton,planButton,sandboxButton);
   card.append(heading,meta,preview,failure,actions);
   container.append(card);
  }
 }catch(error){container.textContent="بارگذاری نامزدهای موقت ناموفق بود: "+error.message;}
}
el("refresh-learning-candidates").addEventListener("click",refreshLearningCandidates);
})();
