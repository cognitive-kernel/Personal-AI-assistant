(function initializePageNavigation() {
  "use strict";
  const navigation = document.querySelector(".dashboard-nav");
  const main = document.getElementById("main-content");
  if (!navigation || !main) return;

  const links = Array.from(navigation.querySelectorAll("a[data-page]"));
  const pages = new Map();
  for (const section of main.querySelectorAll(":scope > section[id], :scope > .row > section[id]")) {
    if (section.id !== "storage-degraded-banner") pages.set(section.id, section);
  }
  if (!pages.has("chat-section")) return;

  const routeMatch = window.location.pathname.match(/^\/page\/([^/]+)\/?$/);
  let requested = "chat-section";
  if (routeMatch) {
    try {
      requested = decodeURIComponent(routeMatch[1]);
    } catch (_error) {
      // A malformed percent-encoded path must not prevent dashboard initialization.
      requested = "chat-section";
    }
  }
  const pageId = pages.has(requested) ? requested : "chat-section";
  const isStoragePage = pageId === "storage-health-section";

  for (const [id, section] of pages) {
    section.hidden = id !== pageId;
    section.classList.toggle("page-hidden", id !== pageId);
  }

  // Keep operational diagnostics with the dedicated storage page, not the chat home.
  const refreshButton = document.getElementById("refresh");
  const healthOutput = document.getElementById("health");
  const degradedBanner = document.getElementById("storage-degraded-banner");
  if (refreshButton) refreshButton.hidden = !isStoragePage;
  if (healthOutput) healthOutput.hidden = !isStoragePage;
  if (degradedBanner && !isStoragePage) degradedBanner.hidden = true;

  for (const link of links) {
    const id = link.dataset.page;
    link.href = id === "chat-section" ? "/" : "/page/" + encodeURIComponent(id);
    if (id === pageId) {
      link.setAttribute("aria-current", "page");
    } else {
      link.removeAttribute("aria-current");
    }
  }

  const activeSection = pages.get(pageId);
  const heading = activeSection && activeSection.querySelector("h2");
  if (heading) {
    document.title = heading.textContent.trim() + " — دستیار هوش مصنوعی شخصی";
  } else {
    document.title = "دستیار هوش مصنوعی شخصی";
  }
  document.documentElement.dataset.dashboardPage = pageId;
})();