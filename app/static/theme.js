/* Local theme preference; no network requests or external assets. */
(function initializeDashboardTheme(){
 const button=document.getElementById("theme-toggle");
 if(!button) return;
 const root=document.documentElement;
 let saved=null;
 try { saved=window.localStorage.getItem("dashboard-theme"); } catch (_) {}
 const prefersDark=Boolean(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
 function applyTheme(dark, persist){
  root.dataset.theme=dark ? "dark" : "light";
  button.setAttribute("aria-pressed",String(dark));
  button.textContent=dark ? "حالت روشن" : "حالت تیره";
  button.setAttribute("aria-label",dark ? "پوستهٔ تیره فعال است؛ تغییر به پوستهٔ روشن" : "پوستهٔ روشن فعال است؛ تغییر به پوستهٔ تیره");
  if(persist){ try { window.localStorage.setItem("dashboard-theme",dark ? "dark" : "light"); } catch (_) {} }
 }
 applyTheme(saved==="dark" || (saved===null && prefersDark),false);
 button.addEventListener("click",()=>applyTheme(root.dataset.theme!=="dark",true));
})();
