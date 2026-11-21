(() => {
 "use strict";
 const core = window.learningCore;
 const fallback = document.getElementById("paths-output");
 if (!core || core.version !== 1) {
  if (fallback) fallback.textContent = "ماژول تطبیق برنامهٔ درسی به قرارداد learningCore v1 نیاز دارد.";
  return;
 }
 const {api: learningApi} = core;

 function createCurriculumControls(path) {
  let proposalRequestSequence = 0;
     const proposalControls = document.createElement("section");
     proposalControls.className = "curriculum-proposal-controls";
     const generateProposalsButton = document.createElement("button");
     generateProposalsButton.type = "button";
     generateProposalsButton.textContent = "تولید پیشنهادهای تطبیق مسیر";
     const proposalOutput = document.createElement("div");
     proposalOutput.className = "curriculum-proposal-output";
     proposalOutput.setAttribute("aria-live", "polite");
  
     function renderCurriculumProposals(result) {
      proposalOutput.replaceChildren();
      const proposals = result.proposals || [];
      if (!proposals.length) {
       proposalOutput.textContent = "پیشنهاد تازه‌ای وجود ندارد؛ امتیازها و تغییر منابع را دوباره بررسی کنید.";
       return;
      }
      proposals.forEach(proposal => {
       const card = document.createElement("article");
       card.className = "curriculum-proposal";
       const heading = document.createElement("h4");
       heading.textContent = (proposal.proposal_type === "reinforce_topic" ? "تقویت سرفصل" : "بازبینی منبع درس") +
        " — " + proposal.proposed_topic + " (" + proposal.status + ")";
       const rationale = document.createElement("p");
       rationale.textContent = proposal.rationale;
       const evidence = document.createElement("pre");
       evidence.textContent = JSON.stringify(proposal.evidence || {}, null, 2);
       card.append(heading, rationale, evidence);
       if (proposal.status === "pending") {
        const note = document.createElement("textarea");
        note.rows = 2;
        note.maxLength = 1000;
        note.placeholder = "دلیل تصمیم (حداقل ۲۰ نویسه)";
        note.setAttribute("aria-label", "دلیل تصمیم برای " + proposal.proposed_topic);
        const actionOutput = document.createElement("p");
        actionOutput.setAttribute("role", "status");
        for (const [decision, label] of [["accept", "پذیرش پیشنهاد"], ["reject", "رد پیشنهاد"]]) {
         const action = document.createElement("button");
         action.type = "button";
         action.textContent = label;
         action.addEventListener("click", async () => {
          const reviewNote = note.value.trim();
          if (reviewNote.length < 20) {
           actionOutput.textContent = "برای ثبت تصمیم، توضیحی با حداقل ۲۰ نویسه وارد کنید.";
           return;
          }
          action.disabled = true;
          actionOutput.textContent = "در حال ثبت تصمیم…";
          try {
           const response = await learningApi("/api/learning/curriculum-proposals/" + proposal.id + "/review", {
            method: "POST",
            body: JSON.stringify({decision, note: reviewNote})
           });
           actionOutput.textContent = response.regeneration
            ? "تصمیم ثبت شد؛ درس جایگزین در صف قرار گرفت. شناسه کار: " + response.regeneration.job_id
            : "تصمیم ثبت شد؛ نسخه‌ها و سوابق قبلی حفظ شدند.";
           await refreshCurriculumProposals();
          } catch (error) {
           actionOutput.textContent = "ثبت تصمیم ناموفق بود: " + error.message;
          } finally {
           action.disabled = false;
          }
         });
         card.append(action);
        }
        card.append(note, actionOutput);
       }
       proposalOutput.append(card);
      });
     }
  
     async function refreshCurriculumProposals() {
      const requestSequence = ++proposalRequestSequence;
      proposalOutput.textContent = "در حال دریافت پیشنهادهای ثبت‌شده…";
      try {
       const result = await learningApi("/api/learning/paths/" + path.id + "/curriculum-proposals");
       if (requestSequence !== proposalRequestSequence) return;
       renderCurriculumProposals(result);
      } catch (error) {
       if (requestSequence === proposalRequestSequence) proposalOutput.textContent = "دریافت پیشنهادهای تطبیق مسیر ناموفق بود: " + error.message;
      }
     }
  
     generateProposalsButton.addEventListener("click", async () => {
      const requestSequence = ++proposalRequestSequence;
      generateProposalsButton.disabled = true;
      proposalOutput.textContent = "در حال تحلیل نمره‌های ذخیره‌شده و تغییرات منابع…";
      try {
       const result = await learningApi("/api/learning/paths/" + path.id + "/curriculum-proposals/generate", {
        method: "POST"
       });
       if (requestSequence !== proposalRequestSequence) return;
       renderCurriculumProposals(result);
      } catch (error) {
       if (requestSequence === proposalRequestSequence) proposalOutput.textContent = "تولید پیشنهادهای تطبیق مسیر ناموفق بود: " + error.message;
      } finally {
       generateProposalsButton.disabled = false;
      }
     });
     proposalControls.append(generateProposalsButton, proposalOutput);
  return proposalControls;
 }

 window.learningCurriculum = Object.freeze({version: 1, createControls: createCurriculumControls});
})();
