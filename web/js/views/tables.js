// Tables created by the loader: list, and one table with rows, columns and imports.
import {api} from "../api.js?v=5";
import {badge, copyText, emptyState, errorPanel, h, mount, skeleton, tabs} from "../dom.js?v=5";
import {buildHash, formatCount, serverTime} from "../state.js?v=5";

const ROWS = 50;
const TABS = [["rows", "Rows"], ["columns", "Columns"], ["imports", "Imports"]];

export function render(main, route, ctx = {}) {
  return route.id ? detail(main, route, ctx) : list(main, ctx);
}

function list(main, ctx) {
  if (ctx.setTitle) ctx.setTitle("Tables");
  const controller = new AbortController();
  let items = [];
  const box = h("div", {}, skeleton(5));
  const filterInput = h("input", {id: "tbl-filter", type: "search", placeholder: "Table or dataset", autocomplete: "off",
                                  oninput: () => show()});
  mount(main, h("div", {class: "page"},
    h("div", {class: "page-head"},
      h("div", {}, h("h1", {text: "Tables"}), h("p", {text: "Tables in IRIS that were created by imports."})),
      h("div", {class: "field"}, h("label", {for: "tbl-filter", text: "Filter"}), filterInput)),
    box));

  async function load() {
    try {
      items = (await api.tables(controller.signal)).items || [];
      show();
    } catch (e) {
      if (e.name !== "AbortError") mount(box, errorPanel(e, load, "Could not load the tables"));
    }
  }

  function show() {
    if (!items.length) {
      mount(box, emptyState("No tables yet", "Import a dataset and its table appears here.",
        h("a", {class: "btn", href: "#/explore", text: "Explore datasets"})));
      return;
    }
    const text = filterInput.value.trim().toLowerCase();
    const rows = items.filter(t => !text || (t.table + " " + (t.dataset || "")).toLowerCase().includes(text));
    if (!rows.length) { mount(box, emptyState("No matching tables", "Change the filter.")); return; }
    mount(box, h("div", {class: "panel"}, h("div", {class: "table-wrap"}, h("table", {class: "data wrap"},
      h("thead", {}, h("tr", {}, ["Table", "Rows", "Columns", "Source", "Commit", "Last import"].map(t => h("th", {scope: "col", text: t})))),
      h("tbody", {}, rows.map(t => h("tr", {},
        h("td", {}, h("a", {class: "mono", href: buildHash("tables", {}, t.table), text: t.table})),
        h("td", {class: "num", text: formatCount(t.rows)}),
        h("td", {class: "num", text: String(t.columnCount)}),
        h("td", {}, t.dataset ? [h("a", {class: "mono", href: buildHash("dataset", {config: t.config || "", split: t.split || ""}, t.dataset),
                                        text: t.dataset}), h("span", {class: "muted", text: " " + [t.config, t.split].filter(Boolean).join(" / ")})] : "-"),
        h("td", {class: "mono", text: t.commit ? t.commit.slice(0, 7) : "-"}),
        h("td", {class: "nowrap", title: "server time", text: serverTime(t.lastImport) || "-"}))))))));
  }

  load();
  return () => controller.abort();
}

function detail(main, route, ctx) {
  const name = route.id;
  if (ctx.setTitle) ctx.setTitle(name);
  const controllers = new Set();
  const signal = () => { const c = new AbortController(); controllers.add(c); return c.signal; };
  let info = null;
  let offset = Math.max(0, Number(route.params.offset) || 0);
  const tab = TABS.some(t => t[0] === route.params.tab) ? route.params.tab : "rows";
  mount(main, h("div", {class: "page"}, skeleton(2), skeleton(6)));

  async function load() {
    try {
      info = await api.table(name, signal());
    } catch (e) {
      if (e.name === "AbortError") return;
      mount(main, h("div", {class: "page"}, back(), errorPanel(e, load, e.code === "not_found" ? "Table not found" : "Could not load the table")));
      return;
    }
    page();
  }

  function back() {
    return h("a", {href: "#/tables", class: "muted", text: "\u2190 All tables"});
  }

  function syncUrl(key) {
    history.replaceState(null, "", buildHash("tables", {tab: key === "rows" ? "" : key, offset: offset || ""}, name));
  }

  function page() {
    const sql = `SELECT TOP 100 * FROM ${info.table}`;
    const reimport = info.dataset ? buildHash("dataset", {config: info.config || "", split: info.split || "", table: info.table}, info.dataset) : null;
    const ctl = tabs(TABS, tab, (key, panel) => { syncUrl(key); ({rows, columns, imports})[key](panel); }, "Table views");
    mount(main, h("div", {class: "page"},
      back(),
      h("header", {class: "ds-head"},
        h("h1", {class: "ds-title", text: info.table}),
        h("div", {class: "ds-stats"},
          h("span", {}, h("strong", {text: formatCount(info.rows)}), " rows"),
          h("span", {}, h("strong", {text: String(info.columnCount)}), " columns"),
          info.dataset ? h("span", {}, "from ", h("a", {class: "mono", href: buildHash("dataset", {}, info.dataset), text: info.dataset}),
                           [info.config, info.split].filter(Boolean).length ? " (" + [info.config, info.split].filter(Boolean).join(" / ") + ")" : "") : null,
          info.commit ? h("span", {class: "mono", text: "@" + info.commit.slice(0, 7)}) : null,
          info.lastImport ? h("span", {title: "server time", text: "last import " + serverTime(info.lastImport)}) : null),
        h("div", {class: "row"},
          h("button", {class: "btn btn-sm", type: "button", text: "Copy SELECT", onclick: () => copyText(sql)}),
          reimport ? h("a", {class: "btn btn-sm", href: reimport, text: "Re-import"}) : null)),
      h("section", {class: "panel", "aria-label": "Table contents"}, ctl.list, ctl.panel)));
    ctl.select(tab);
  }

  function rows(panel) {
    mount(panel, skeleton(6));
    api.tableRows(info.table, offset, ROWS, signal()).then(data => {
      if (!data.rows.length) { mount(panel, emptyState("No rows", offset ? "This page is past the end of the table." : "The table is empty.")); return; }
      const last = offset + data.rows.length;
      mount(panel, h("div", {class: "stack"},
        h("div", {class: "table-wrap"}, h("table", {class: "data"},
          h("thead", {}, h("tr", {}, h("th", {scope: "col", class: "num", text: "#"}), data.columns.map(c => h("th", {scope: "col", text: c})))),
          h("tbody", {}, data.rows.map((r, i) => h("tr", {}, h("td", {class: "num", text: String(offset + i)}),
            r.map(v => h("td", {title: String(v), text: String(v)}))))))),
        h("div", {class: "pager"},
          h("span", {text: `Rows ${formatCount(offset)} to ${formatCount(last - 1)} of ${formatCount(data.total)}`}),
          h("div", {class: "row"},
            h("button", {class: "btn btn-sm", type: "button", text: "Previous", disabled: offset === 0,
                         onclick: () => { offset = Math.max(0, offset - ROWS); syncUrl("rows"); rows(panel); }}),
            h("button", {class: "btn btn-sm", type: "button", text: "Next", disabled: last >= data.total,
                         onclick: () => { offset = last; syncUrl("rows"); rows(panel); }})))));
    }).catch(e => { if (e.name !== "AbortError") mount(panel, errorPanel(e, () => rows(panel), "Could not load rows")); });
  }

  function columns(panel) {
    mount(panel, h("div", {class: "table-wrap"}, h("table", {class: "data"},
      h("thead", {}, h("tr", {}, h("th", {scope: "col", text: "Column"}), h("th", {scope: "col", text: "IRIS type"}))),
      h("tbody", {}, (info.columns || []).map(c => h("tr", {}, h("td", {class: "mono", text: c.name}), h("td", {class: "type", text: c.type})))))));
  }

  function imports(panel) {
    if (!(info.imports || []).length) { mount(panel, emptyState("No imports recorded", "")); return; }
    mount(panel, h("div", {class: "table-wrap"}, h("table", {class: "data wrap"},
      h("thead", {}, h("tr", {}, ["When", "Dataset", "Split", "Rows", "Commit", "Status", "Message"].map(t => h("th", {scope: "col", text: t})))),
      h("tbody", {}, info.imports.map(r => h("tr", {},
        h("td", {class: "nowrap", title: "server time", text: serverTime(r.createdAt)}),
        h("td", {class: "mono", text: r.dataset || "-"}),
        h("td", {text: [r.config, r.split].filter(Boolean).join(" / ") || "-"}),
        h("td", {class: "num", text: r.rows === "" ? "-" : formatCount(r.rows)}),
        h("td", {class: "mono", text: r.commit ? String(r.commit).slice(0, 7) : "-"}),
        h("td", {}, badge(r.status, r.status === "OK" ? "ok" : "danger")),
        h("td", {class: "message", text: r.message || ""})))))));
  }

  load();
  return () => { for (const c of controllers) c.abort(); };
}
