// Imports: background jobs (with progress and cancel) and the import history.
import {api} from "../api.js?v=5";
import {badge, emptyState, errorPanel, h, mount, skeleton, toast} from "../dom.js?v=5";
import {buildHash, formatCount, serverTime} from "../state.js?v=5";

const STATE_BADGE = {queued: "accent", running: "accent", ok: "ok", error: "danger", cancelled: "warn", interrupted: "warn"};
const PHASES = {resolving: "Looking up the dataset", fetching: "Downloading rows", inserting: "Writing rows",
                finishing: "Finishing"};

export function render(main, route, ctx = {}) {
  if (ctx.setTitle) ctx.setTitle("Imports");
  let controller = null;
  let timer = null;
  let filter = {status: "", text: ""};
  let history = [];

  const jobsBox = h("div", {"aria-live": "polite"}, skeleton(2));
  const historyBox = h("div", {}, skeleton(4));
  const statusSelect = h("select", {id: "hist-status", onchange: e => { filter.status = e.target.value; showHistory(); }},
    [["", "All"], ["OK", "Succeeded"], ["ERROR", "Failed"]].map(([v, t]) => h("option", {value: v, text: t})));
  const textInput = h("input", {id: "hist-text", type: "search", placeholder: "Dataset or table", autocomplete: "off",
    oninput: e => { filter.text = e.target.value.trim().toLowerCase(); showHistory(); }});

  mount(main, h("div", {class: "page"},
    h("div", {class: "page-head"}, h("div", {}, h("h1", {text: "Imports"}),
      h("p", {text: "Background imports with their progress, and every import into IRIS so far."}))),
    h("section", {class: "panel", "aria-labelledby": "jobs-title"},
      h("div", {class: "panel-head"}, h("h2", {id: "jobs-title", text: "Background imports"})), jobsBox),
    h("section", {class: "panel", "aria-labelledby": "hist-title"},
      h("div", {class: "panel-head"}, h("h2", {id: "hist-title", text: "Import history"}),
        h("div", {class: "toolbar"},
          h("div", {class: "field"}, h("label", {for: "hist-status", text: "Status"}), statusSelect),
          h("div", {class: "field"}, h("label", {for: "hist-text", text: "Filter"}), textInput))),
      historyBox)));

  async function load() {
    clearTimeout(timer);
    if (controller) controller.abort();
    controller = new AbortController();
    const signal = controller.signal;
    let jobs;
    try {
      [jobs, history] = await Promise.all([api.jobs(20, signal), api.history(signal)]);
    } catch (e) {
      if (e.name === "AbortError") return;
      mount(jobsBox, errorPanel(e, load, "Could not load the imports"));
      mount(historyBox);
      return;
    }
    showJobs(jobs.items || []);
    showHistory();
    if ((jobs.items || []).some(j => j.state === "queued" || j.state === "running")) timer = setTimeout(load, 2000);
  }

  function showJobs(items) {
    if (!items.length) {
      mount(jobsBox, emptyState("No background imports yet", "Start one from a dataset page.",
        h("a", {class: "btn", href: "#/explore", text: "Explore datasets"})));
      return;
    }
    mount(jobsBox, h("ul", {class: "job-list"}, items.map(job)));
  }

  function job(j) {
    const running = j.state === "queued" || j.state === "running";
    const pct = j.rowsTotal ? Math.round(100 * j.rowsDone / j.rowsTotal) : null;
    const cancel = running ? h("button", {class: "btn btn-sm btn-danger", type: "button", text: "Cancel",
      "aria-label": `Cancel import ${j.id}`, disabled: j.cancelRequested,
      onclick: async () => {
        cancel.disabled = true;
        try { await api.cancelJob(j.id); toast(`Cancel requested for import ${j.id}`); load(); }
        catch (e) { cancel.disabled = false; toast(e.message, "error"); }
      }}) : null;
    return h("li", {class: "job"},
      h("div", {class: "job-head"},
        badge(j.state, STATE_BADGE[j.state] || ""),
        h("strong", {text: "#" + j.id}),
        h("a", {href: buildHash("dataset", {config: j.config || "", split: j.split}, j.dataset), class: "mono", text: j.dataset}),
        h("span", {class: "muted", text: "\u2192"}),
        j.state === "ok" ? h("a", {href: buildHash("tables", {}, j.table), class: "mono", text: j.table})
                         : h("span", {class: "mono", text: j.table}),
        h("span", {class: "spacer"}),
        h("span", {class: "muted", title: "server time", text: serverTime(j.createdAt)}),
        cancel),
      running ? h("div", {class: "stack"},
        h("div", {class: "progress" + (pct === null ? " indeterminate" : ""), role: "progressbar",
                  "aria-label": `Import ${j.id} progress`, "aria-valuenow": pct === null ? null : String(pct),
                  "aria-valuemin": "0", "aria-valuemax": "100"},
          h("div", {style: `width: ${pct === null ? 0 : pct}%`})),
        h("p", {class: "muted", text: (j.cancelRequested ? "Cancel requested. " : "") + (PHASES[j.phase] || "Waiting to start") +
          (j.rowsTotal ? `: ${formatCount(j.rowsDone)} of ${formatCount(j.rowsTotal)} rows` : "") +
          (j.elapsed != null ? ` (${j.elapsed} s)` : "")})) : null,
      !running ? h("details", {},
        h("summary", {class: "muted", text: j.state === "ok"
          ? `${formatCount(j.result && j.result.rows)} rows in ${j.elapsed} s` : "Details"}),
        j.state === "ok" && j.result ? h("dl", {class: "kv"},
          h("dt", {text: "Columns"}), h("dd", {class: "mono", text: Object.entries(j.result.columns || {}).map(([c, t]) => c + " " + t).join(", ")}),
          h("dt", {text: "Commit"}), h("dd", {class: "mono", text: j.result.commit || "-"}),
          j.result.classLabels ? [h("dt", {text: "Labels"}), h("dd", {text: Object.entries(j.result.classLabels)
            .map(([c, names]) => c + ": " + names.join(", ")).join("; ")})] : null,
          h("dt", {text: "Started by"}), h("dd", {text: j.user || "-"})) : h("p", {class: "job-error", text: j.error || "No details."})) : null);
  }

  function showHistory() {
    if (!history.length) {
      mount(historyBox, emptyState("Nothing imported yet", "Imports from this page, REST, SQL and ObjectScript all appear here."));
      return;
    }
    const rows = history.filter(r => (!filter.status || r.status === filter.status) &&
      (!filter.text || (r.dataset + " " + r.table).toLowerCase().includes(filter.text)));
    if (!rows.length) {
      mount(historyBox, emptyState("No matching imports", "Change the status or the filter."));
      return;
    }
    mount(historyBox, h("div", {class: "table-wrap"}, h("table", {class: "data wrap"},
      h("thead", {}, h("tr", {}, ["When", "Dataset", "Split", "Table", "Rows", "Time", "Status", "Message"]
        .map(t => h("th", {scope: "col", text: t})))),
      h("tbody", {}, rows.map(r => h("tr", {},
        h("td", {class: "nowrap", title: "server time", text: serverTime(r.createdAt)}),
        h("td", {}, r.dataset ? h("a", {class: "mono", href: buildHash("dataset", {config: r.config || "", split: r.split}, r.dataset),
                                       text: r.dataset}) : "-"),
        h("td", {text: [r.config, r.split].filter(Boolean).join(" / ") || "-"}),
        h("td", {}, r.status === "OK" && r.table ? h("a", {class: "mono", href: buildHash("tables", {}, r.table), text: r.table})
                                                 : h("span", {class: "mono", text: r.table || "-"})),
        h("td", {class: "num", text: r.rows === "" || r.rows === null ? "-" : formatCount(r.rows)}),
        h("td", {class: "num", text: r.seconds === "" || r.seconds === null ? "-" : r.seconds + " s"}),
        h("td", {}, badge(r.status, r.status === "OK" ? "ok" : "danger")),
        h("td", {class: "message", text: r.message || ""})))))));
  }

  load();
  return () => { clearTimeout(timer); if (controller) controller.abort(); };
}
