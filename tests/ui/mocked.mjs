// Offline test of import monitoring with a fake API: nothing is imported, no server needed.
// Usage: node tests/ui/mocked.mjs
import {audit, byClass, byId, byText, find, install, waitFor} from "./fakedom.mjs";

const main = install("");
const results = [];
const check = (name, ok, detail = "") => {
  results.push(ok);
  console.log((ok ? "PASS  " : "FAIL  ") + name + (ok || !detail ? "" : "  -> " + detail));
};
const text = el => el ? el.textContent : "";
const json = (data, status = 200) => new Response(JSON.stringify(data), {status, headers: {"Content-Type": "application/json"}});

// ------------------------------------------------------------- fake API
const calls = [];
let job7 = {id: "7", state: "running", phase: "inserting", rowsDone: 1000, rowsTotal: 4000, dataset: "x/y", table: "HF.y",
            split: "train", config: null, mode: "replace", createdAt: "2026-10-13 10:00:00", elapsed: 5, user: "_SYSTEM",
            cancelRequested: false, result: null, error: null};
const job9 = [
  {id: "9", state: "running", phase: "fetching", rowsDone: 0, rowsTotal: 0, elapsed: 1},
  {id: "9", state: "running", phase: "inserting", rowsDone: 2000, rowsTotal: 4000, elapsed: 2},
  {id: "9", state: "ok", phase: null, rowsDone: 4000, rowsTotal: 4000, elapsed: 3,
   result: {rows: 4000, table: "HF.y", seconds: 3.2, commit: "abc1234def"}}];
let job9Step = 0;
globalThis.fetch = async (url, opts = {}) => {
  const path = url.replace("/api/hf2iris", "");
  calls.push((opts.method || "GET") + " " + path);
  if (path.startsWith("/jobs?")) return json({items: [job7], count: 1});
  if (path === "/history") return json([]);
  if (path === "/jobs/7/cancel") {
    job7 = {...job7, cancelRequested: true};
    setTimeout(() => { job7 = {...job7, state: "cancelled", phase: null, error: "Import cancelled. The rows of this import were rolled back."}; }, 300);
    return json(job7);
  }
  if (path.startsWith("/dataset?")) {
    return json({id: "x/y", downloads: 10, likes: 1, gated: false, private: false, license: [], tasks: [], languages: [],
                 hubUrl: "https://huggingface.co/datasets/x/y", sha: "abc1234def", viewer: {available: true, partial: false},
                 configs: [{name: "default", features: [{name: "text", type: "string"}], splits: [{name: "train", rows: 4000, bytes: 1000}]}]});
  }
  if (path === "/plan") {
    return json({action: "create", reason: null, table: "HF.y", rowsEstimate: 4000, commit: "abc1234def",
                 columns: [{source: "text", name: "text_", type: "VARCHAR(4000)"}], warnings: [], download: {bytes: null}});
  }
  if (path === "/jobs" && opts.method === "POST") {
    globalThis.lastStart = JSON.parse(opts.body);
    return json({id: "9", state: "queued"}, 202);
  }
  if (path === "/jobs/9") return json(job9[Math.min(job9Step++, job9.length - 1)]);
  return json({error: "unexpected " + path}, 500);
};
sessionStorage.setItem("hf2iris.auth", "x");

const {VERSION} = await import("../../web/js/state.js");
const imports = await import(`../../web/js/views/imports.js?v=${VERSION}`);
const dataset = await import(`../../web/js/views/dataset.js?v=${VERSION}`);

// --------------------------------------------------------- imports view
let stop = imports.render(main, {view: "imports", params: {}}, {});
await waitFor(() => byClass(main, "job").length, "running job");
const bar = find(main, e => e.getAttribute("role") === "progressbar");
check("running job shows progress", bar && bar.getAttribute("aria-valuenow") === "25" && /Writing rows: 1K of 4K rows \(5 s\)/.test(text(main)),
      bar && bar.getAttribute("aria-valuenow") + " " + text(main).slice(0, 200));
check("accessibility with a running job", audit(main).length === 0, audit(main).join("; "));
const cancel = byText(main, "button", "Cancel");
check("cancel button names the job", cancel && cancel.getAttribute("aria-label") === "Cancel import 7");
cancel.click();
await waitFor(() => calls.includes("POST /jobs/7/cancel"), "cancel request");
await waitFor(() => byClass(main, "badge").some(b => b.textContent === "cancelled"), "cancelled state", 10000);
check("job ends as cancelled with the reason", text(main).includes("rolled back"));
const after = calls.length;
await new Promise(r => setTimeout(r, 2600));
check("polling stops once no job is running", calls.length === after, `${calls.length - after} more calls`);
stop();

// ------------------------------------------------- dataset import panel
stop = dataset.render(main, {view: "dataset", id: "x/y", params: {}}, {});
await waitFor(() => byText(main, "button", "Review plan"), "import panel");
const start = byText(main, "button", "Start import");
byText(main, "button", "Review plan").click();
await waitFor(() => !start.disabled, "plan accepted");
check("plan enables Start import", text(main).includes("This import creates HF.y with about 4K rows"));
start.click();
await waitFor(() => globalThis.lastStart, "job started");
check("start sends the import settings", JSON.stringify(globalThis.lastStart) ===
      JSON.stringify({dataset: "x/y", config: "default", split: "train", table: "HF.y", limit: 1000, mode: "replace", revision: ""}),
      JSON.stringify(globalThis.lastStart));
await waitFor(() => /Writing rows to IRIS: 2K of 4K rows/.test(text(main)), "insert progress", 10000);
check("panel shows insert progress", true);
await waitFor(() => text(main).includes("Imported 4K rows into HF.y"), "finished import", 10000);
check("finished import links to the table and to all imports",
      Boolean(find(main, e => e.tagName === "A" && e.getAttribute("href") === "#/tables/HF.y")) &&
      Boolean(find(main, e => e.tagName === "A" && e.getAttribute("href") === "#/imports")));
check("Review plan is usable again", byText(main, "button", "Review plan").disabled === false);
stop();

const failed = results.filter(r => !r).length;
console.log(`MOCKED ${failed ? "FAILED: " + failed + " of " + results.length : "PASSED: " + results.length + " checks"}`);
process.exit(failed ? 1 : 0);
