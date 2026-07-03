// General dashboard behavior: the light/dark theme toggle (Settings page +
// navbar button) and small HTMX UX niceties. No backend calls here — theme
// preference is purely client-side (localStorage), since there's no
// per-user settings storage for it and building one wasn't in scope for
// this milestone (see docs/DASHBOARD.md's Settings section).

const THEME_STORAGE_KEY = "job-automation-theme";

function applyTheme(theme) {
  document.documentElement.setAttribute("data-bs-theme", theme);
}

function toggleTheme() {
  const current = document.documentElement.getAttribute("data-bs-theme") || "light";
  const next = current === "light" ? "dark" : "light";
  applyTheme(next);
  localStorage.setItem(THEME_STORAGE_KEY, next);
}

(function initTheme() {
  const stored = localStorage.getItem(THEME_STORAGE_KEY);
  if (stored) {
    applyTheme(stored);
  }
})();

// Briefly highlight any element HTMX just swapped in, so an approve/reject/
// status-transition click has a visible confirmation beyond the new badge
// text itself.
document.body.addEventListener("htmx:afterSwap", function (event) {
  event.detail.target.classList.add("htmx-flash");
  setTimeout(() => event.detail.target.classList.remove("htmx-flash"), 600);
});
