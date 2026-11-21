/* Scroll-position state for the persistent in-page navigation. */
(function initializeCurrentSectionNavigation(){
 const navigation=document.querySelector('.dashboard-nav');
 if(!navigation) return;
 const links=Array.from(navigation.querySelectorAll('a[href^="#"]'));
 const sections=links.map(link=>document.getElementById(link.hash.slice(1))).filter(Boolean);
 if(!sections.length) return;
 let scheduled=false;
 let navigationTarget=null;
 function setCurrentSection(sectionId){
  const currentHash="#"+sectionId;
  for(const link of links){
   if(link.getAttribute('href')===currentHash){
    link.setAttribute('aria-current','location');
    link.classList.add('is-current');
   }else{
    link.removeAttribute('aria-current');
    link.classList.remove('is-current');
   }
  }
 }
 function updateCurrentSection(){
  scheduled=false;
  const marker=Math.min(Math.max(window.innerHeight*0.28,120),240);
  if(navigationTarget){
   const target=document.getElementById(navigationTarget.slice(1));
   if(target){
    const top=target.getBoundingClientRect().top;
    if(top>marker){
     setCurrentSection(target.id);
     return;
    }
    if(top>=-window.innerHeight){
     navigationTarget=null;
     setCurrentSection(target.id);
     return;
    }
   }
   navigationTarget=null;
  }
  let current=sections[0];
  let currentTop=-Infinity;
  let firstVisibleTop=Infinity;
  let firstVisibleSection=sections[0];
  for(const section of sections){
   // Navigation order can differ from document order; select the nearest section above the viewport marker.
   const top=section.getBoundingClientRect().top;
   if(top<firstVisibleTop){firstVisibleTop=top;firstVisibleSection=section;}
   if(top<=marker && top>currentTop){current=section;currentTop=top;}
  }
  if(currentTop===-Infinity) current=firstVisibleSection;
  setCurrentSection(current.id);
 }
 function scheduleUpdate(){
  if(scheduled) return;
  scheduled=true;
  window.requestAnimationFrame(updateCurrentSection);
 }
 navigation.addEventListener('click',event=>{
  const link=event.target.closest('a[href^="#"]');
  if(link && navigation.contains(link)){
   navigationTarget=link.hash;
   setCurrentSection(link.hash.slice(1));
  }
 });
 window.addEventListener('wheel',()=>{navigationTarget=null;},{passive:true});
 window.addEventListener('touchstart',()=>{navigationTarget=null;},{passive:true});
 window.addEventListener('scroll',scheduleUpdate,{passive:true});
 window.addEventListener('resize',scheduleUpdate,{passive:true});
 window.addEventListener('hashchange',scheduleUpdate);
 updateCurrentSection();
})();
