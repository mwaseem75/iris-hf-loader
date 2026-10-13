// Runs the real view modules against the running server with a minimal fake DOM.
// No imports are started: "Start import" is never clicked.
// Usage: node tests/ui/live.mjs [http://localhost:52773]
import {all, audit, byClass, byId, byText, find, install, waitFor} from "./fakedom.mjs";

const BASE = process.argv[2] || "http://localhost:52773";
const main = install(BASE);
const results = [];
const check = (name, ok, detail = "") => {
  results.push(ok);
  console.log((ok ? "PASS  " : "FAIL  ") + name + (ok || !detail ? "" : "  -> " + detail));
};

const {VERSION} = await import("../../web/js/state.js");
// the same URL as the views use, so this is the same module instance
const api = await import(`../../web/js/api.js?v=${VERSION}`);
const logins = [];
api.onLogin(async message => {               // what the sign-in dialog does
  logins.push(message);
  api.saveLogin("_SYSTEM", logins.length === 1 ? "wrong" : "SYS");
});
const explore = await import(`../../web/js/views/explore.js?v=${VERSION}`);
const dataset = await import(`../../web/js/views/dataset.js?v=${VERSION}`);
const imports = await import(`../../web/js/views/imports.js?v=${VERSION}`);
const tables = await import(`../../web/js/views/tables.js?v=${VERSION}`);
const a11y = name => { const problems = audit(main); check("accessibility: " + name, !problems.length, problems.slice(0, 3).join("; ")); };
const nav = {navigate: () => {}};
const text = el => el ? el.textContent : "";

// ------------------------------------------------------------------ explore
let stop = explore.render(main, {view: "explore", params: {}}, nav);
await waitFor(() => byClass(main, "card").length || byClass(main, "error-panel").length, "popular datasets");
check("sign-in asked, wrong password rejected, then accepted",
      logins.length === 2 && logins[1] === "Wrong username or password.", JSON.stringify(logins));
check("popular datasets listed", byClass(main, "card").length > 0 && text(main).includes("Popular datasets"));
a11y("explore");
stop();

stop = explore.render(main, {view: "explore", params: {q: "imdb"}}, nav);
await waitFor(() => byClass(main, "card").length, "imdb results");
const imdb = find(main, e => e.tagName === "A" && e.getAttribute("href") === "#/dataset/stanfordnlp/imdb");
check("search finds stanfordnlp/imdb with a dataset link", Boolean(imdb));
check("result card shows downloads and badges", imdb && /downloads/.test(text(imdb)) && byClass(imdb, "badge").length > 0);
byText(main, "button", "Likes").click();
await waitFor(() => location.hash.includes("sort=likes"), "sort in URL");
check("sort button updates the URL and pressed state",
      byText(main, "button", "Likes").getAttribute("aria-pressed") === "true");
stop();

stop = explore.render(main, {view: "explore", params: {q: "zzqx-no-such-dataset-8841"}}, nav);
await waitFor(() => text(main).includes("No datasets found") || byClass(main, "card").length, "empty result");
check("empty search shows an empty state with a clear button",
      text(main).includes("No datasets found") && Boolean(byText(main, "button", "Clear search and filters")));
stop();

// ------------------------------------------------------------------ dataset
stop = dataset.render(main, {view: "dataset", id: "openai/gsm8k", params: {}}, nav);
await waitFor(() => byClass(main, "ds-title").length || byClass(main, "error-panel").length, "gsm8k details");
check("dataset header", text(byClass(main, "ds-title")[0]) === "openai/gsm8k");
check("config and split picked", byId(main, "config").value === "main" && byId(main, "split").value === "train",
      byId(main, "config") && byId(main, "config").value + "/" + byId(main, "split").value);
check("overview lists the configs", text(main).includes("main, socratic"));
check("hub link opens in a new tab", Boolean(find(main, e => e.tagName === "A" &&
      e.getAttribute("href") === "https://huggingface.co/datasets/openai/gsm8k" && e.getAttribute("target") === "_blank")));
a11y("dataset");
const tablist = find(main, e => e.getAttribute("role") === "tablist");
tablist.fire("keydown", {key: "ArrowRight"});
check("arrow key moves to the next tab", location.hash.includes("tab=schema") &&
      byText(main, "button", "Schema").getAttribute("tabindex") === "0" && byText(main, "button", "Overview").getAttribute("tabindex") === "-1");
tablist.fire("keydown", {key: "Home"});
check("Home key returns to the first tab", byText(main, "button", "Overview").getAttribute("aria-selected") === "true");

byText(main, "button", "Preview").click();
await waitFor(() => find(main, e => e.tagName === "TABLE") || byClass(main, "error-panel").length, "preview rows");
const firstPage = all(find(main, e => e.tagName === "TBODY"), e => e.tagName === "TR").length;
check("preview shows 20 rows with a pager", firstPage === 20 && /Rows 0 to 19 of 7\.5K/.test(text(main)), text(byClass(main, "pager")[0]));
byText(main, "button", "Next").click();
await waitFor(() => /Rows 20 to 39/.test(text(main)), "next page");
check("next page and URL offset", location.hash.includes("offset=20"));

const split = byId(main, "split");
split.value = "test";
split.fire("change");
await waitFor(() => /Rows 0 to 19 of 1\.3K/.test(text(main)), "test split");
check("switching split reloads the preview", location.hash.includes("split=test"));

byText(main, "button", "Schema").click();
await waitFor(() => find(main, e => e.tagName === "TABLE" && (" " + e.className).includes("plan-cols")) ||
                    byClass(main, "error-panel").length, "schema plan");
check("schema shows IRIS columns from the plan", /question.*string.*question.*VARCHAR/s.test(text(main)));

check("ObjectScript snippet", text(main).includes('do ##class(dc.hf2iris.Loader).Load("openai/gsm8k","HF.gsm8k","test",1000,"main")'));
const start = byText(main, "button", "Start import");
check("Start import is disabled before a plan", start.disabled === true);
byText(main, "button", "Review plan").click();
await waitFor(() => byClass(main, "notice-ok").length || byClass(main, "notice-danger").length ||
                    byClass(main, "error-panel").length, "plan");
check("plan says what the import does", /This import (creates|replaces) HF\.gsm8k with about 1K rows/.test(text(main)),
      text(byClass(main, "notice")[0]));
check("Start import is enabled after a good plan", start.disabled === false);

const table = byId(main, "imp-table");
table.value = "dc_hf2iris.ImportLog";
table.fire("input");
const mode = byId(main, "imp-mode");
mode.value = "append";
mode.fire("change");
byText(main, "button", "Review plan").click();
await waitFor(() => byClass(main, "notice-danger").length || byClass(main, "error-panel").length, "refused plan");
check("plan for a foreign table is refused and Start stays disabled",
      /not created by hf2iris/.test(text(main)) && start.disabled === true);
stop();

stop = dataset.render(main, {view: "dataset", id: "ylecun/mnist", params: {tab: "preview"}}, nav);
await waitFor(() => find(main, e => e.tagName === "TABLE") || byClass(main, "error-panel").length, "mnist preview");
check("images are placeholders, no media URLs", byClass(main, "placeholder").some(td => text(td) === "[image]") &&
      !/assets|Signature/.test(text(main)));
stop();

stop = dataset.render(main, {view: "dataset", id: "bigcode/the-stack", params: {}}, nav);
await waitFor(() => text(main).includes("gated") || byClass(main, "error-panel").length, "gated panel");
check("gated dataset explains HF_TOKEN", text(main).includes("This dataset is gated") && text(main).includes("HF_TOKEN"));
stop();

stop = dataset.render(main, {view: "dataset", id: "no-such-user/no-such-dataset", params: {}}, nav);
await waitFor(() => byClass(main, "error-panel").length, "not found");
check("missing dataset shows an error with Try again",
      text(main).includes("Dataset not found") && Boolean(byText(main, "button", "Try again")));
stop();

// ------------------------------------------------------------------- tables
// Uses whatever loader tables and history exist; parts with nothing to look at are skipped.
const skip = name => console.log("SKIP  " + name);
const known = (await api.api.tables()).items || [];
const sample = known.find(t => t.rows > 50 && t.dataset) || known.find(t => t.rows > 0) || null;

stop = tables.render(main, {view: "tables", id: null, params: {}}, nav);
await waitFor(() => find(main, e => e.tagName === "TABLE") || byClass(main, "empty-state").length || byClass(main, "error-panel").length, "tables list");
a11y("tables list");
if (!known.length) {
  check("no tables yet shows an empty state", text(main).includes("No tables yet"));
} else {
  check("tables list links to every table", known.every(t => find(main, e => e.tagName === "A" && e.getAttribute("href") === "#/tables/" + t.table)));
  const filter = byId(main, "tbl-filter");
  filter.value = "zzqx-no-table";
  filter.fire("input");
  check("table filter", text(main).includes("No matching tables"));
}
stop();

if (sample) {
  stop = tables.render(main, {view: "tables", id: sample.table, params: {}}, nav);
  const pageRows = Math.min(50, sample.rows);
  await waitFor(() => new RegExp(`Rows 0 to ${pageRows - 1} of`).test(text(main)) || byClass(main, "error-panel").length, "table rows");
  check("table detail pages rows by 50", all(find(main, e => e.tagName === "TBODY"), e => e.tagName === "TR").length === pageRows);
  if (sample.rows > 50) {
    byText(main, "button", "Next").click();
    await waitFor(() => /Rows 50 to/.test(text(main)), "table page 2");
    check("next page of a table", location.hash.includes("offset=50"));
  } else skip("next page of a table (fewer than 51 rows)");
  const reimport = byText(main, "a", "Re-import");
  if (sample.dataset) {
    const want = "#/dataset/" + sample.dataset + "?" + new URLSearchParams(Object.entries(
      {config: sample.config || "", split: sample.split || "", table: sample.table}).filter(([, v]) => v)).toString();
    check("re-import opens the dataset with its settings filled in", reimport && reimport.getAttribute("href") === want,
          reimport && reimport.getAttribute("href") + " vs " + want);
  }
  const ttabs = find(main, e => e.getAttribute("role") === "tablist");
  ttabs.fire("keydown", {key: "End"});
  check("End key opens the last tab (imports)", byText(main, "button", "Imports").getAttribute("aria-selected") === "true");
  ttabs.fire("keydown", {key: "ArrowLeft"});
  check("columns tab lists IRIS types", text(main).includes("IRIS type") && /(BIGINT|VARCHAR|DOUBLE)/.test(text(main)));
  a11y("table detail");
  stop();
} else skip("table detail (no tables yet)");

stop = tables.render(main, {view: "tables", id: "dc_hf2iris.ImportLog", params: {}}, nav);
await waitFor(() => byClass(main, "error-panel").length, "foreign table");
check("a table not created by hf2iris is not shown", text(main).includes("Table not found"));
stop();

stop = dataset.render(main, {view: "dataset", id: "rotten_tomatoes", params: {table: "Demo.Reviews", split: "test"}}, nav);
await waitFor(() => byId(main, "imp-table") || byClass(main, "error-panel").length, "prefilled import");
check("import panel prefilled from the URL", byId(main, "imp-table") && byId(main, "imp-table").value === "Demo.Reviews");
stop();

// ------------------------------------------------------------------ imports
const jobs = (await api.api.jobs(20)).items || [];
const historyRows = await api.api.history();
stop = imports.render(main, {view: "imports", id: null, params: {}}, nav);
await waitFor(() => byClass(main, "job").length || byClass(main, "empty-state").length || byClass(main, "error-panel").length, "imports view");
await waitFor(() => find(main, e => e.tagName === "TABLE") || text(main).includes("Nothing imported yet"), "import history");
if (jobs.length) check("background imports are listed", byClass(main, "job").length === Math.min(20, jobs.length));
else check("no background imports yet shows an empty state", text(main).includes("No background imports yet"));
if (historyRows.length) {
  check("history lists earlier imports", text(main).includes(historyRows[0].dataset || historyRows[0].table || ""));
  const wanted = historyRows.some(r => r.status === "ERROR") ? "ERROR" : "OK";
  const status = byId(main, "hist-status");
  status.value = wanted;
  status.fire("change");
  const statuses = byClass(find(main, e => e.tagName === "TBODY"), "badge").map(b => b.textContent);
  check("history status filter", statuses.length > 0 && statuses.every(t => t === wanted), statuses.join(","));
} else {
  check("empty history shows an empty state", text(main).includes("Nothing imported yet"));
}
a11y("imports");
stop();

const failed = results.filter(r => !r).length;
console.log(`UI ${failed ? "FAILED: " + failed + " of " + results.length : "PASSED: " + results.length + " checks"}`);
process.exit(failed ? 1 : 0);
