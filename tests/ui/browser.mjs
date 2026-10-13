// Real-browser check: headless Chrome with a throwaway profile, driven over the DevTools
// protocol (Node's built-in WebSocket, no packages). Only visits the local app.
// Usage: node tests/ui/browser.mjs [--imports] [--screenshots]
//   --imports      also imports stanfordnlp/imdb (test split, 3000 rows) into Demo.IMDB from the UI
//   --screenshots  saves PNGs to docs/screenshots
// Env: IRIS_WEB_PORT (52773), IRIS_USERNAME (_SYSTEM), IRIS_PASSWORD (SYS), CHROME (path to Chrome)
import {spawn} from "node:child_process";
import {existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync} from "node:fs";
import {tmpdir} from "node:os";
import {dirname, join} from "node:path";
import {fileURLToPath} from "node:url";

const APP = `http://localhost:${process.env.IRIS_WEB_PORT || 52773}/hf2iris/index.html`;
const USER = process.env.IRIS_USERNAME || "_SYSTEM";
const PASSWORD = process.env.IRIS_PASSWORD || "SYS";
const IMPORTS = process.argv.includes("--imports");
const SHOTS = process.argv.includes("--screenshots");
const SHOT_DIR = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "docs", "screenshots");
const DESKTOP = {width: 1440, height: 900, deviceScaleFactor: 1, mobile: false};
const PHONE = {width: 390, height: 844, deviceScaleFactor: 2, mobile: true};

const results = [];
const check = (name, ok, detail = "") => {
  results.push(ok);
  console.log((ok ? "PASS  " : "FAIL  ") + name + (ok || !detail ? "" : "  -> " + detail));
};

function findChrome() {
  const candidates = [process.env.CHROME,
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    process.env.LOCALAPPDATA && join(process.env.LOCALAPPDATA, "Google/Chrome/Application/chrome.exe"),
    "/usr/bin/google-chrome", "/usr/bin/chromium", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"];
  return candidates.find(p => p && existsSync(p));
}

// ---------------------------------------------------------------- Chrome
const chromePath = findChrome();
if (!chromePath) { console.log("FAIL  Chrome not found; set CHROME to its path"); process.exit(1); }
const profile = mkdtempSync(join(tmpdir(), "hf2iris-chrome-"));
const chrome = spawn(chromePath, ["--headless=new", "--remote-debugging-port=0", `--user-data-dir=${profile}`,
  "--no-first-run", "--no-default-browser-check", "--disable-extensions", "--disable-gpu", "--disable-sync",
  "--window-size=1440,900", "about:blank"], {stdio: ["ignore", "ignore", "pipe"]});
const port = await new Promise((resolve, reject) => {
  let err = "";
  chrome.stderr.on("data", d => {
    err += d;
    const m = err.match(/DevTools listening on ws:\/\/127\.0\.0\.1:(\d+)\//);
    if (m) resolve(m[1]);
  });
  setTimeout(() => reject(new Error("Chrome did not start: " + err.slice(0, 300))), 20000);
});

const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
const ws = new WebSocket(targets.find(t => t.type === "page").webSocketDebuggerUrl);
await new Promise(r => ws.addEventListener("open", r, {once: true}));
let nextId = 0;
const pending = new Map();
const consoleErrors = [];
ws.addEventListener("message", ({data}) => {
  const msg = JSON.parse(data);
  if (msg.id && pending.has(msg.id)) {
    const {resolve, reject} = pending.get(msg.id);
    pending.delete(msg.id);
    msg.error ? reject(new Error(msg.error.message)) : resolve(msg.result);
  } else if (msg.method === "Runtime.exceptionThrown") {
    consoleErrors.push("exception: " + (msg.params.exceptionDetails.exception || {}).description);
  } else if (msg.method === "Runtime.consoleAPICalled" && msg.params.type === "error") {
    consoleErrors.push("console.error: " + msg.params.args.map(a => a.value || a.description).join(" "));
  } else if (msg.method === "Log.entryAdded" && msg.params.entry.level === "error") {
    const e = msg.params.entry;
    // expected: the 401 that opens the sign-in dialog, 404 answers the tests ask for, the favicon
    const expected = /favicon\.ico/.test(e.url || "") || (/\/api\/hf2iris\//.test(e.url || "") && /status of (401|403|404|409)/.test(e.text));
    if (!expected) consoleErrors.push(`${e.source}: ${e.text} ${e.url || ""}`);
  }
});
const send = (method, params = {}) => new Promise((resolve, reject) => {
  const id = ++nextId;
  pending.set(id, {resolve, reject});
  ws.send(JSON.stringify({id, method, params}));
});

async function js(expression) {
  const r = await send("Runtime.evaluate", {expression, returnByValue: true, awaitPromise: true});
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.text + " " + ((r.exceptionDetails.exception || {}).description || ""));
  return r.result.value;
}

async function until(expression, what, timeout = 30000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    if (await js(expression)) return true;
    await new Promise(r => setTimeout(r, 200));
  }
  throw new Error("timed out waiting for " + what);
}

const q = s => JSON.stringify(s);
const go = hash => js(`location.hash = ${q(hash)}; true`);
const fill = (sel, value) => js(`(() => { const e = document.querySelector(${q(sel)}); e.value = ${q(value)};
  e.dispatchEvent(new Event("input", {bubbles: true})); e.dispatchEvent(new Event("change", {bubbles: true})); return true; })()`);
const clickText = (tag, text) => js(`(() => { const e = [...document.querySelectorAll(${q(tag)})].find(x => x.textContent.trim() === ${q(text)});
  if (!e) throw new Error("no ${tag} " + ${q(text)}); e.click(); return true; })()`);
const textHas = s => `document.body.innerText.includes(${q(s)})`;
async function shot(name, size = DESKTOP) {
  if (!SHOTS) return;
  await js(`document.querySelectorAll(".toast").forEach(t => t.remove()); true`);
  await new Promise(r => setTimeout(r, 400));          // let transitions settle
  const {data} = await send("Page.captureScreenshot", {format: "png", captureBeyondViewport: false});
  mkdirSync(SHOT_DIR, {recursive: true});
  writeFileSync(join(SHOT_DIR, name + ".png"), Buffer.from(data, "base64"));
  console.log("      saved docs/screenshots/" + name + ".png");
  void size;
}

// ------------------------------------------------------------------ flows
let exitCode = 0;
try {
  await send("Page.enable");
  await send("Runtime.enable");
  await send("Log.enable");
  await send("Emulation.setDeviceMetricsOverride", DESKTOP);
  await send("Page.navigate", {url: APP});

  // sign-in
  await until(`document.getElementById("loginDialog") && document.getElementById("loginDialog").open`, "sign-in dialog");
  check("sign-in dialog opens on the first API call", true);
  await fill("#loginUser", USER);
  await fill("#loginPass", "wrong-password");
  await js(`document.getElementById("loginForm").requestSubmit(); true`);
  await until(`document.getElementById("loginError").textContent.includes("Wrong")`, "wrong password message");
  check("wrong password is rejected", true);
  await fill("#loginPass", PASSWORD);
  await js(`document.getElementById("loginForm").requestSubmit(); true`);
  await until(`!document.getElementById("loginDialog").open && document.getElementById("userName").textContent === ${q(USER)}`, "signed in");
  check("signed in; user shown in the top bar", true);

  // explore
  await until(`document.querySelectorAll(".card").length > 0`, "popular datasets");
  await until(`!document.getElementById("statusPill").hidden`, "status pill");
  check("explore shows popular datasets and the token status", true);
  check("dark theme by default", await js(`document.documentElement.dataset.theme === "dark"`));
  await fill("#globalQuery", "imdb");
  await js(`document.getElementById("globalSearch").requestSubmit(); true`);
  await until(`!!document.querySelector('a.card[href="#/dataset/stanfordnlp/imdb"]')`, "imdb result");
  check("global search finds stanfordnlp/imdb", true);
  await shot("explore");
  await js(`document.querySelector('a.card[href="#/dataset/stanfordnlp/imdb"]').click(); true`);
  await until(`!!document.querySelector(".ds-title")`, "dataset page");
  check("clicking a result opens the dataset", await js(`document.title.startsWith("stanfordnlp/imdb")`));
  await js(`history.back(); true`);
  await until(`location.hash.startsWith("#/explore") && document.querySelectorAll(".card").length > 0`, "back to explore");
  check("the back button returns to the search", await js(`location.hash.includes("q=imdb")`));

  // dataset
  await go("#/dataset/openai/gsm8k?tab=preview");
  await until(`document.querySelectorAll("table.data tbody tr").length === 20`, "preview rows");
  check("dataset preview renders 20 rows", true);
  await shot("dataset-preview");
  await clickText("button", "Schema");
  await until(`!!document.querySelector("table.plan-cols")`, "schema plan");
  check("schema tab shows the planned IRIS columns", await js(textHas("VARCHAR")));
  await shot("dataset-schema");
  await js(`document.querySelector('[role="tablist"] [aria-selected="true"]').focus(); true`);
  await js(`document.activeElement.dispatchEvent(new KeyboardEvent("keydown", {key: "ArrowRight", bubbles: true})); true`);
  check("arrow keys move between tabs in a real browser",
        await js(`document.activeElement.getAttribute("role") === "tab" && document.activeElement.textContent === "Preview"`));

  // navigation
  await clickText("nav a", "Imports");
  await until(`!!document.querySelector("main h1") && document.querySelector("main h1").textContent === "Imports"`, "imports page");
  await clickText("nav a", "Tables");
  await until(`document.querySelector("main h1").textContent === "Tables"`, "tables page");
  check("side navigation opens Imports and Tables", await js(`document.querySelector('nav a[aria-current="page"]').textContent === "Tables"`));

  // theme
  await js(`document.getElementById("themeToggle").click(); true`);
  check("theme toggle switches to light", await js(`document.documentElement.dataset.theme === "light"`));
  await go("#/dataset/openai/gsm8k?tab=preview");
  await send("Page.reload");
  await until(`document.readyState === "complete" && document.querySelectorAll("table.data tbody tr").length === 20`, "reload", 30000);
  check("light theme and sign-in survive a reload",
        await js(`document.documentElement.dataset.theme === "light" && !document.getElementById("loginDialog").open`));
  await shot("light-theme");
  await js(`document.getElementById("themeToggle").click(); true`);

  // phone width
  await send("Emulation.setDeviceMetricsOverride", PHONE);
  await go("#/explore");
  await until(`document.querySelectorAll(".card").length > 0`, "explore on a phone");
  const noScroll = `document.documentElement.scrollWidth <= window.innerWidth + 1`;
  check("phone width: no sideways scrolling on Explore", await js(noScroll),
        await js(`document.documentElement.scrollWidth + " > " + window.innerWidth`));
  check("phone width: navigation shown as a bar", await js(`getComputedStyle(document.querySelector(".sidenav")).flexDirection === "row"`));
  check("phone width: every navigation link is fully visible", await js(`(() => {
    const nav = document.querySelector(".sidenav").getBoundingClientRect();
    return [...document.querySelectorAll(".sidenav a")].every(a => { const r = a.getBoundingClientRect();
      return r.height >= 36 && r.top >= nav.top - 1 && r.bottom <= nav.bottom + 1; }); })()`),
        await js(`JSON.stringify([document.querySelector(".sidenav").getBoundingClientRect().height,
                                  [...document.querySelectorAll(".sidenav a")].map(a => Math.round(a.getBoundingClientRect().height))])`));
  check("phone width: a new page starts at the top, with the top bar in view", await js(`window.scrollY === 0`));
  await shot("phone-explore", PHONE);
  await go("#/dataset/openai/gsm8k");
  await until(`!!document.querySelector(".ds-title")`, "dataset on a phone");
  check("phone width: no sideways scrolling on a dataset page", await js(noScroll),
        await js(`document.documentElement.scrollWidth + " > " + window.innerWidth`));
  check("phone width: 'Jump to import' is offered", await js(`[...document.querySelectorAll("button")].some(b => b.textContent === "Jump to import" && b.offsetParent)`));
  await send("Emulation.setDeviceMetricsOverride", DESKTOP);

  // a real import from the UI
  if (IMPORTS) {
    await go("#/dataset/stanfordnlp/imdb?split=test");
    await until(`!!document.getElementById("imp-table")`, "import panel");
    await fill("#imp-table", "Demo.IMDB");
    await fill("#imp-limit", "3000");
    await clickText("button", "Review plan");
    await until(`!!document.querySelector(".notice-ok, .notice-danger")`, "plan");
    check("plan for Demo.IMDB", await js(textHas("This import")));
    await clickText("button", "Start import");
    await until(`/(Downloading rows|Writing rows)/.test(document.body.innerText)`, "import progress", 60000);
    check("import progress is shown", true);
    await shot("import-progress");
    await until(textHas("Imported 3K rows into Demo.IMDB"), "finished import", 240000);
    check("import finishes and links to the table", await js(`!!document.querySelector('a[href="#/tables/Demo.IMDB"]')`));
    await go("#/imports");
    await until(`document.querySelectorAll(".job").length > 0 && !!document.querySelector("table.data")`, "imports page");
    check("the import is listed with its result", await js(textHas("Demo.IMDB")));
    await shot("imports");
    await go("#/tables/Demo.IMDB");
    await until(`document.querySelectorAll("table.data tbody tr").length === 50`, "table rows");
    check("the new table opens with its rows", await js(textHas("Rows 0 to 49 of 3K")));
    await shot("table-detail");
  }

  check("no JavaScript errors in the console", consoleErrors.length === 0, consoleErrors.slice(0, 5).join(" | "));
} catch (e) {
  check("browser flow", false, e.message);
  if (consoleErrors.length) console.log("      console: " + consoleErrors.slice(0, 5).join(" | "));
} finally {
  try { ws.close(); } catch (e) { /* closing */ }
  chrome.kill();
  await new Promise(r => setTimeout(r, 800));
  try { rmSync(profile, {recursive: true, force: true, maxRetries: 5, retryDelay: 300}); } catch (e) { /* temp folder */ }
}
const failed = results.filter(r => !r).length;
exitCode = failed ? 1 : 0;
console.log(`BROWSER ${failed ? "FAILED: " + failed + " of " + results.length : "PASSED: " + results.length + " checks"}`);
process.exit(exitCode);
