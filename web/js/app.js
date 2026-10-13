// App shell: sign-in, top bar, navigation, theme and the hash router.
import {api, onLogin, saveLogin, signOut, signedInUser} from "./api.js?v=5";
import {THEME_KEY, buildHash, parseHash, themeFor} from "./state.js?v=5";
import * as explore from "./views/explore.js?v=5";
import * as dataset from "./views/dataset.js?v=5";
import * as imports from "./views/imports.js?v=5";
import * as tables from "./views/tables.js?v=5";

const $ = id => document.getElementById(id);
const views = {explore, dataset, imports, tables};
const APP = "iris-hf-loader";
let cleanup = null;
let firstRender = true;

// --------------------------------------------------------------- sign-in
let pending = null;
function login(message) {
  if (!pending) {
    pending = new Promise(resolve => {
      const dialog = $("loginDialog");
      $("loginError").textContent = message || "";
      $("loginPass").value = "";
      $("loginForm").onsubmit = e => {
        e.preventDefault();
        saveLogin($("loginUser").value.trim(), $("loginPass").value);
        dialog.close();
        pending = null;
        showUser();
        resolve();
      };
      dialog.addEventListener("cancel", e => e.preventDefault());   // Esc does not skip sign-in
      if (!dialog.open) dialog.showModal();
      $("loginPass").focus();
    });
  }
  return pending;
}
onLogin(login);

function showUser() {
  const user = signedInUser();
  $("userBox").hidden = !user;
  $("userName").textContent = user;
}

$("signOut").addEventListener("click", () => {
  signOut();
  showUser();
  login("");
});

// ------------------------------------------------------------------ theme
function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  const button = $("themeToggle");
  const next = theme === "dark" ? "light" : "dark";
  button.setAttribute("aria-label", `Switch to ${next} theme`);
  button.title = `Switch to ${next} theme`;
  button.textContent = theme === "dark" ? "Light" : "Dark";
}

$("themeToggle").addEventListener("click", () => {
  const theme = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
  try { localStorage.setItem(THEME_KEY, theme); } catch (e) { /* private mode */ }
  applyTheme(theme);
});

// ----------------------------------------------------------------- status
async function loadStatus() {
  try {
    const s = await api.status();
    const pill = $("statusPill");
    pill.hidden = false;
    pill.className = "status-pill" + (s.hfToken ? " ok" : "");
    pill.textContent = s.hfToken ? "HF token set" : "No HF token";
    pill.title = s.hfToken ? "Gated and private datasets you have access to can be used."
                           : "Public datasets only. Start the container with HF_TOKEN for gated datasets.";
  } catch (e) { /* the status pill is optional */ }
}

// ----------------------------------------------------------------- router
function setTitle(text) {
  document.title = text ? `${text} \u00B7 ${APP}` : APP;
}

function render() {
  const route = parseHash(location.hash);
  if (cleanup) { try { cleanup(); } catch (e) { /* ignore */ } }
  cleanup = null;
  for (const a of document.querySelectorAll("[data-nav]")) {
    const active = a.dataset.nav === route.view || (a.dataset.nav === "explore" && route.view === "dataset");
    if (active) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  }
  const main = $("main");
  cleanup = views[route.view].render(main, route, {navigate, setTitle}) || null;
  if (route.view === "explore") $("globalQuery").value = route.params.q || "";
  if (!firstRender) {               // a new page starts at the top; screen readers start at its content
    window.scrollTo(0, 0);
    main.focus({preventScroll: true});
  }
  firstRender = false;
}

export function navigate(view, params, id) {
  const hash = buildHash(view, params, id);
  if (location.hash === hash) render();
  else location.hash = hash;
}

$("globalSearch").addEventListener("submit", e => {
  e.preventDefault();
  navigate("explore", {q: $("globalQuery").value.trim()});
});

window.addEventListener("hashchange", render);
applyTheme(themeFor(document.documentElement.getAttribute("data-theme")));
showUser();
render();
loadStatus();
