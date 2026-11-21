/* Shared browser-side dashboard primitives.
 * Versioned contract consumed by isolated feature modules.
 */
(() => {
 "use strict";
 const el = id => document.getElementById(id);
 async function api(path, options = {}) {
  const response = await fetch(path, {
   headers: {"Content-Type":"application/json"},
   ...options
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || ("HTTP " + response.status));
  return data;
 }
 function appendText(parent, tag, text, className) {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  parent.append(node);
  return node;
 }
 window.dashboardCore = Object.freeze({
  version: 1,
  el,
  api,
  appendText
 });
})();
