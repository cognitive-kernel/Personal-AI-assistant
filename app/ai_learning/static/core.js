export const CORE_CONTRACT_VERSION=1;
export function createCore(){
 const API="/api/ai-learning";
 const $=id=>document.getElementById(id);
async function api(path,options={}){const r=await fetch(API+path,{cache:"no-store",headers:{"Content-Type":"application/json",...(options.headers||{})},...options});const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.detail||("HTTP "+r.status));return d}
function say(s,bad=false){$("status").textContent=s;$("status").className=bad?"error":"success"}
function el(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n}
function data(form){return Object.fromEntries(new FormData(form).entries())}
function progressMeter(percent,label){const value=Math.max(0,Math.min(100,Number(percent)||0));const wrap=el("div",undefined,"progress-wrap");const meta=el("div",undefined,"progress-meta");meta.append(el("span",label||"پیشرفت دریافت محتوا"),el("strong",value+"٪"));const track=el("div",undefined,"progress-track"),bar=el("span",undefined,"progress-bar");bar.style.width=value+"%";track.append(bar);wrap.append(meta,track);return wrap}
async function act(fn){try{await fn()}catch(e){say(e.message,true)}}

 return Object.freeze({CORE_CONTRACT_VERSION,API,$,api,say,el,data,progressMeter,act});
}
