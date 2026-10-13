// Dataset details: overview, schema, row preview and the import panel.
import {api} from "../api.js?v=5";
import {badge, copyText, emptyState, errorPanel, h, mount, skeleton, tabs, toast} from "../dom.js?v=5";
import {buildHash, defaultTable, formatBytes, formatCount, objectScriptCall, sqlCall, timeAgo} from "../state.js?v=5";

const ROWS = 20;
const TABS = [["overview", "Overview"], ["schema", "Schema"], ["preview", "Preview"]];

export function render(main, route, ctx = {}) {
  const id = route.id;
  const p = route.params;
  const state = {config: p.config || "", split: p.split || "", tab: TABS.some(t => t[0] === p.tab) ? p.tab : "overview",
                 offset: Math.max(0, Number(p.offset) || 0)};
  const controllers = new Set();
  let poll = null;
  let info = null;
  let planCache = {key: "", promise: null};

  function signal() {
    const c = new AbortController();
    controllers.add(c);
    return c.signal;
  }

  function syncUrl() {
    history.replaceState(null, "", buildHash("dataset", {config: state.config, split: state.split,
      tab: state.tab === "overview" ? "" : state.tab, offset: state.offset || ""}, id));
  }

  mount(main, h("div", {class: "page"}, skeleton(2, "skeleton-card"), skeleton(6, "skeleton-card")));
  if (ctx.setTitle) ctx.setTitle(id);

  async function loadDetails() {
    try {
      info = await api.dataset(id, signal());
    } catch (e) {
      if (e.name === "AbortError") return;
      mount(main, h("div", {class: "page"}, backLink(), e.code === "gated" ? gatedPanel(e) : errorPanel(e, loadDetails,
        e.code === "not_found" ? "Dataset not found" : "Could not load the dataset")));
      return;
    }
    const configs = info.configs || [];
    if (!configs.some(c => c.name === state.config)) state.config = configs.length ? configs[0].name : "";
    const splits = currentConfig().splits || [];
    if (!splits.some(s => s.name === state.split)) state.split = (splits.find(s => s.name === "train") || splits[0] || {}).name || "";
    syncUrl();
    page();
  }

  function currentConfig() {
    return (info.configs || []).find(c => c.name === state.config) || {splits: [], features: []};
  }

  function currentSplit() {
    return (currentConfig().splits || []).find(s => s.name === state.split) || {};
  }

  // ----------------------------------------------------------------- page
  let tabBody = h("div");
  let tabCtl = null;
  let importPanel = null;

  function page() {
    const [owner, name] = info.id.includes("/") ? info.id.split("/") : ["", info.id];
    importPanel = importBox();
    tabCtl = tabs(TABS, state.tab, (key, panel) => {
      state.tab = key;
      tabBody = panel;
      syncUrl();
      ({overview, schema, preview})[key]();
    }, "Dataset views");
    mount(main, h("div", {class: "page"},
      backLink(),
      h("header", {class: "ds-head"},
        h("h1", {class: "ds-title"}, owner ? h("span", {class: "owner", text: owner + "/"}) : null, name),
        info.resolvedFrom ? h("p", {class: "muted", text: `"${info.resolvedFrom}" is an old id for ${info.id}.`}) : null,
        h("div", {class: "badges"},
          info.gated ? badge("Gated", "warn") : null,
          info.private ? badge("Private", "warn") : null,
          (info.tasks || []).map(t => badge(t, "accent")),
          info.sizeCategory ? badge(info.sizeCategory) : null,
          (info.license || []).map(l => badge("license: " + l)),
          (info.languages || []).slice(0, 6).map(l => badge(l))),
        h("div", {class: "ds-stats"},
          h("span", {}, h("strong", {text: formatCount(info.downloads)}), " downloads"),
          h("span", {}, h("strong", {text: formatCount(info.likes)}), " likes"),
          info.lastModified ? h("span", {text: "updated " + timeAgo(info.lastModified)}) : null,
          h("a", {href: info.hubUrl, target: "_blank", rel: "noopener noreferrer", text: "View on Hugging Face"}),
          h("button", {class: "btn btn-sm only-narrow", type: "button", text: "Jump to import",
                       onclick: () => importPanel.el.scrollIntoView({behavior: "smooth"})}))),
      h("div", {class: "ds-layout"},
        h("section", {class: "panel", "aria-label": "Dataset contents"},
          picker(),
          tabCtl.list,
          tabCtl.panel),
        h("aside", {class: "ds-side"}, importPanel.el))));
    selectTab(state.tab);
  }

  function backLink() {
    return h("a", {href: "#/explore", class: "muted", text: "\u2190 Back to Explore"});
  }

  function picker() {
    const configs = info.configs || [];
    const configSelect = h("select", {id: "config", onchange: e => {
      state.config = e.target.value;
      state.split = ((currentConfig().splits || [])[0] || {}).name || "";
      state.offset = 0;
      syncUrl();
      page();
    }}, configs.map(c => h("option", {value: c.name, text: c.name, selected: c.name === state.config})));
    const splitSelect = h("select", {id: "split", onchange: e => {
      state.split = e.target.value;
      state.offset = 0;
      syncUrl();
      importPanel.reset();
      selectTab(state.tab);
    }}, (currentConfig().splits || []).map(s => h("option", {value: s.name, selected: s.name === state.split,
      text: s.name + (s.rows != null ? "  (" + formatCount(s.rows) + " rows" + (s.bytes ? ", " + formatBytes(s.bytes) : "") + ")" : "")})));
    return h("div", {class: "picker"},
      h("div", {class: "field"}, h("label", {for: "config", text: "Config"}), configSelect),
      h("div", {class: "field"}, h("label", {for: "split", text: "Split"}), splitSelect),
      !info.viewer.available ? h("p", {class: "muted", text: "Row counts and previews are not available for this dataset."}) : null);
  }

  function selectTab(key) {
    tabCtl.select(key);
  }

  // ------------------------------------------------------------- overview
  function overview() {
    const split = currentSplit();
    mount(tabBody, h("div", {class: "stack"},
      h("p", {class: "description", text: info.description || "This dataset has no description."}),
      h("dl", {class: "kv"},
        h("dt", {text: "Dataset id"}), h("dd", {class: "mono", text: info.id}),
        h("dt", {text: "Latest commit"}), h("dd", {class: "mono", text: info.sha || "-"}),
        h("dt", {text: "Configs"}), h("dd", {text: (info.configs || []).map(c => c.name).join(", ") || "-"}),
        h("dt", {text: "Selected split"}), h("dd", {text: state.split ? `${state.split}: ${split.rows != null ? formatCount(split.rows) + " rows" : "row count unknown"}${split.bytes ? ", " + formatBytes(split.bytes) : ""}` : "-"}),
        h("dt", {text: "Columns"}), h("dd", {text: String((currentConfig().features || []).length || "-")}),
        h("dt", {text: "Dataset viewer"}), h("dd", {text: info.viewer.available ? (info.viewer.partial ? "available (partial)" : "available") : "not available"}))));
  }

  // --------------------------------------------------------------- schema
  function schema() {
    const features = currentConfig().features || [];
    const body = h("div", {class: "stack"}, h("p", {class: "muted", text: "Loading the IRIS columns from an import plan..."}));
    mount(tabBody, features.length ? null : h("p", {class: "muted", text: "The dataset viewer did not report columns; the plan below shows what the import would create."}), body);
    const values = importPanel.values();
    getPlan(values).then(plan => {
      const rows = (plan.columns || []).map(c => {
        const f = features.find(x => x.name === c.source);
        return h("tr", {},
          h("td", {class: "mono", text: c.source}),
          h("td", {class: "type", text: f ? f.type : (c.note && c.note.startsWith("label names") ? "added by the loader" : "-")}),
          h("td", {class: "mono", text: c.name}),
          h("td", {class: "type", text: c.type}),
          h("td", {class: "muted", text: c.note || ""}));
      });
      mount(body,
        rows.length ? h("div", {class: "table-wrap"}, h("table", {class: "data plan-cols"},
          h("thead", {}, h("tr", {}, ["Dataset column", "Hugging Face type", "IRIS column", "IRIS type", "Note"].map(t => h("th", {scope: "col", text: t})))),
          h("tbody", {}, rows))) : emptyState("Columns unknown", "The column types are only known once the data is read."),
        (plan.warnings || []).map(w => h("p", {class: "muted", text: w})));
    }).catch(e => {
      if (e.name !== "AbortError") mount(body, errorPanel(e, schema, "Could not build the import plan"));
    });
  }

  // -------------------------------------------------------------- preview
  function preview(source = "viewer") {
    if (!state.split) { mount(tabBody, emptyState("No split", "This config has no splits to preview.")); return; }
    mount(tabBody, skeleton(8, "skeleton-card"));
    api.preview({id: info.id, config: state.config, split: state.split, offset: state.offset, rows: ROWS,
                 source: source === "stream" ? "stream" : ""}, signal()).then(showRows).catch(e => {
      if (e.name === "AbortError") return;
      if (e.code === "preview_unavailable") {
        mount(tabBody, h("div", {class: "stack"},
          h("div", {class: "notice notice-warn", text: e.message.replace(/ Add source=stream.*$/, "")}),
          h("p", {class: "muted", text: "The first rows can be read directly from the dataset files instead. That can take up to a minute."}),
          h("div", {}, h("button", {class: "btn", text: "Read the first rows directly", onclick: () => {
            state.offset = 0;
            mount(tabBody, h("div", {class: "row"}, h("span", {class: "spinner"}), h("span", {class: "muted", text: "Reading the first rows from the dataset files..."})));
            preview("stream");
          }}))));
      } else {
        mount(tabBody, errorPanel(e, () => preview(source), "Could not load rows"));
      }
    });
  }

  function showRows(data) {
    if (!data.rows.length) { mount(tabBody, emptyState("No rows", "This split is empty or the offset is past its end.")); return; }
    const head = h("tr", {}, h("th", {scope: "col", class: "num", text: "#"}),
      data.columns.map((c, i) => h("th", {scope: "col", title: data.features[i] ? data.features[i].type : ""}, c, " ",
        data.features[i] ? h("span", {class: "type", text: shortType(data.features[i].type)}) : null)));
    const body = data.rows.map((row, r) => h("tr", {},
      h("td", {class: "num", text: String(data.offset + r)}),
      row.map(v => {
        const text = v === null ? "null" : typeof v === "object" ? JSON.stringify(v) : String(v);
        const placeholder = v === null || /^\[(image|audio|video|pdf|media)\]$/.test(text);
        return h("td", {class: placeholder ? "placeholder" : "", title: text, text});
      })));
    const total = data.total;
    const last = data.offset + data.rows.length;
    mount(tabBody, h("div", {class: "stack"},
      data.source === "stream" ? h("div", {class: "notice", text: "First rows read directly from the dataset files."}) : null,
      h("div", {class: "table-wrap"}, h("table", {class: "data"}, h("thead", {}, head), h("tbody", {}, body))),
      h("div", {class: "pager"},
        h("span", {text: `Rows ${formatCount(data.offset)} to ${formatCount(last - 1)}` + (total != null ? ` of ${formatCount(total)}` : "") +
          (data.truncated ? "; long values are shortened, hover to see more" : "")}),
        data.source === "viewer" ? h("div", {class: "row"},
          h("button", {class: "btn btn-sm", text: "Previous", disabled: data.offset === 0,
                       onclick: () => { state.offset = Math.max(0, data.offset - ROWS); syncUrl(); preview(); }}),
          h("button", {class: "btn btn-sm", text: "Next", disabled: total != null ? last >= total : data.rows.length < ROWS,
                       onclick: () => { state.offset = last; syncUrl(); preview(); }})) : null)));
  }

  function shortType(t) {
    return t.length > 24 ? t.slice(0, 22) + "..." : t;
  }

  // ---------------------------------------------------------------- plan
  function getPlan(values) {
    const body = {dataset: info.id, config: state.config, split: state.split, ...values};
    const key = JSON.stringify(body);
    if (planCache.key !== key) planCache = {key, promise: api.plan(body, signal())};
    return planCache.promise;
  }

  // -------------------------------------------------------------- import
  function importBox() {
    const table = h("input", {id: "imp-table", value: p.table || defaultTable(info.id), autocomplete: "off"});
    const limit = h("input", {id: "imp-limit", type: "number", min: "0", step: "1", value: "1000"});
    const mode = h("select", {id: "imp-mode"},
      [["replace", "Replace"], ["append", "Append"], ["fail", "Fail"]].map(([v, t]) => h("option", {value: v, text: t})));
    const revision = h("input", {id: "imp-rev", placeholder: "latest", autocomplete: "off"});
    const planBtn = h("button", {class: "btn", type: "button", text: "Review plan"});
    const startBtn = h("button", {class: "btn btn-primary", type: "button", text: "Start import", disabled: true});
    const out = h("div", {class: "stack", "aria-live": "polite"});
    const snippet = h("pre", {class: "snippet"});
    const sqlSnippet = h("pre", {class: "snippet"});
    const where = h("p", {class: "muted"});
    let plan = null;

    const values = () => ({table: table.value.trim(), limit: Math.max(0, Number(limit.value) || 0), mode: mode.value,
                           revision: revision.value.trim()});
    function refreshSnippets() {
      const v = {dataset: info.id, config: state.config === "default" ? "" : state.config, split: state.split, ...values()};
      snippet.textContent = objectScriptCall(v);
      sqlSnippet.textContent = sqlCall(v);
      where.textContent = `${state.config || "default"} / ${state.split || "-"}`;
    }
    function reset() {
      plan = null;
      startBtn.disabled = true;
      mount(out);
      refreshSnippets();
    }
    for (const el of [table, limit, mode, revision]) el.addEventListener("input", reset);
    mode.addEventListener("change", reset);

    planBtn.addEventListener("click", async () => {
      planBtn.disabled = true;
      mount(out, h("div", {class: "row"}, h("span", {class: "spinner"}), h("span", {class: "muted", text: "Checking the dataset and the target table..."})));
      try {
        plan = await getPlan(values());
        showPlan(plan);
      } catch (e) {
        if (e.name !== "AbortError") mount(out, errorPanel(e, null, "Plan failed"));
      } finally {
        planBtn.disabled = false;
      }
    });

    function showPlan(pl) {
      const refused = pl.action === "refused";
      startBtn.disabled = refused;
      const verb = {create: "creates", replace: "replaces", append: "appends to"}[pl.action];
      mount(out,
        h("div", {class: "notice " + (refused ? "notice-danger" : "notice-ok")},
          refused ? pl.reason : `This import ${verb} ${pl.table}` +
            (pl.rowsEstimate != null ? ` with about ${formatCount(pl.rowsEstimate)} rows` : "") + "."),
        h("p", {class: "muted", text: `${(pl.columns || []).length} columns, commit ${String(pl.commit || "").slice(0, 7)}` +
          (pl.download && pl.download.bytes ? `, about ${formatBytes(pl.download.bytes)} to download` : "")}),
        (pl.warnings || []).map(w => h("p", {class: "muted", text: w})),
        h("button", {class: "link-btn", type: "button", text: "Show the columns", onclick: () => selectTab("schema")}));
    }

    startBtn.addEventListener("click", async () => {
      startBtn.disabled = true;
      planBtn.disabled = true;
      try {
        const job = await api.startJob({dataset: info.id, config: state.config, split: state.split, ...values()});
        follow(job);
      } catch (e) {
        planBtn.disabled = false;
        mount(out, errorPanel(e, null, "The import could not start"));
      }
    });

    function follow(job) {
      const bar = h("div", {style: "width: 0%"});
      const progress = h("div", {class: "progress indeterminate", role: "progressbar", "aria-label": "Import progress"}, bar);
      const line = h("p", {class: "muted"});
      const cancel = h("button", {class: "btn btn-sm btn-danger", type: "button", text: "Cancel import", onclick: async () => {
        cancel.disabled = true;
        try { await api.cancelJob(job.id); line.textContent = "Cancel requested; the import stops before its next batch of rows."; }
        catch (e) { toast(e.message, "error"); }
      }});
      mount(out, h("div", {class: "stack"}, h("div", {class: "row"}, h("strong", {text: "Import #" + job.id}),
        h("span", {class: "spacer"}), cancel), progress, line));
      const phases = {queued: "Waiting to start", resolving: "Looking up the dataset", fetching: "Downloading rows from Hugging Face",
                      inserting: "Writing rows to IRIS", finishing: "Finishing"};
      const tick = async () => {
        let j;
        try { j = await api.job(job.id, signal()); } catch (e) { if (e.name !== "AbortError") line.textContent = e.message; return; }
        const running = j.state === "queued" || j.state === "running";
        if (j.rowsTotal) {
          progress.className = "progress";
          bar.style.width = Math.round(100 * j.rowsDone / j.rowsTotal) + "%";
          progress.setAttribute("aria-valuenow", String(Math.round(100 * j.rowsDone / j.rowsTotal)));
        }
        if (running) {
          line.textContent = (phases[j.phase || j.state] || j.state) + (j.rowsTotal ? `: ${formatCount(j.rowsDone)} of ${formatCount(j.rowsTotal)} rows` : "...") +
            (j.elapsed != null ? ` (${j.elapsed} s)` : "");
          return;
        }
        clearInterval(poll);
        poll = null;
        planBtn.disabled = false;
        cancel.remove();
        if (j.state === "ok") {
          bar.style.width = "100%";
          progress.className = "progress";
          mount(out, h("div", {class: "notice notice-ok"},
            `Imported ${formatCount(j.result.rows)} rows into ${j.result.table} in ${j.result.seconds} s.`),
            h("p", {class: "muted"}, "Commit " + String(j.result.commit || "").slice(0, 7) + ". ",
              h("a", {href: buildHash("tables", {}, j.result.table), text: "Open the table"}), " or ",
              h("a", {href: "#/imports", text: "see all imports"}), "."));
          toast(`Imported ${formatCount(j.result.rows)} rows into ${j.result.table}`, "ok");
        } else {
          mount(out, h("div", {class: "notice " + (j.state === "cancelled" ? "notice-warn" : "notice-danger")},
            (j.state === "cancelled" ? "Import cancelled. " : j.state === "interrupted" ? "Import interrupted. " : "Import failed. ") + (j.error || "")));
        }
        planCache = {key: "", promise: null};
      };
      poll = setInterval(tick, 1500);
      tick();
    }

    refreshSnippets();
    const el = h("section", {class: "panel stack", "aria-labelledby": "imp-title"},
      h("div", {}, h("h2", {id: "imp-title", text: "Import into IRIS"}), where),
      h("div", {class: "form-grid"},
        h("div", {class: "field full"}, h("label", {for: "imp-table", text: "Target table"}), table),
        h("div", {class: "field"}, h("label", {for: "imp-limit", text: "Row limit"}), limit,
          h("span", {class: "hint", text: "0 = all rows"})),
        h("div", {class: "field"}, h("label", {for: "imp-mode", text: "If the table exists"}), mode),
        h("div", {class: "field full"}, h("label", {for: "imp-rev", text: "Revision (optional)"}), revision,
          h("span", {class: "hint", text: "A commit hash pins the data."}))),
      h("div", {class: "row"}, planBtn, startBtn),
      out,
      h("details", {}, h("summary", {class: "muted", text: "Same import in ObjectScript or SQL"}),
        h("div", {class: "stack", style: "margin-top: 8px"},
          snippet, h("button", {class: "btn btn-sm", type: "button", text: "Copy ObjectScript", onclick: () => copyText(snippet.textContent)}),
          sqlSnippet, h("button", {class: "btn btn-sm", type: "button", text: "Copy SQL", onclick: () => copyText(sqlSnippet.textContent)}))));
    return {el, values, reset};
  }

  function gatedPanel(e) {
    return h("div", {class: "panel stack", role: "alert"},
      h("h2", {text: "This dataset is gated"}),
      h("p", {text: e.message}),
      h("p", {class: "muted", text: "Open the dataset on Hugging Face, accept its terms, create an access token, and start the container with HF_TOKEN set. The token stays on the IRIS server."}),
      h("div", {}, h("a", {class: "btn", href: "https://huggingface.co/datasets/" + id, target: "_blank", rel: "noopener noreferrer", text: "Open on Hugging Face"})));
  }

  loadDetails();
  return () => {
    for (const c of controllers) c.abort();
    if (poll) clearInterval(poll);
  };
}
