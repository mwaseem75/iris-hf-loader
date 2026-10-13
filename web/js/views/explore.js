// Explore: search Hugging Face datasets through the IRIS backend.
import {api} from "../api.js?v=5";
import {badge, emptyState, errorPanel, h, mount, skeleton} from "../dom.js?v=5";
import {SIZES, SORTS, TASKS, buildHash, formatCount, timeAgo} from "../state.js?v=5";

const PAGE = 20;
const MAX = 50;

export function render(main, route, {navigate, setTitle}) {
  if (setTitle) setTitle("Explore");
  const p = route.params;
  const state = {q: p.q || "", sort: p.sort || "downloads", task: p.task || "", size: p.size || "",
                 lang: p.lang || "", limit: Math.min(MAX, Number(p.limit) || PAGE)};
  let controller = null;
  let timer = null;
  let hasResults = false;

  const query = h("input", {type: "search", id: "q", class: "grow", value: state.q, autocomplete: "off",
                            placeholder: "Search by name or keyword", "aria-label": "Search datasets"});
  const sortButtons = SORTS.map(([value, label]) =>
    h("button", {type: "button", "aria-pressed": String(value === state.sort), text: label,
                 onclick: () => update({sort: value, limit: PAGE})}));
  const select = (id, label, options, value, key) => h("div", {class: "field"},
    h("label", {for: id, text: label}),
    h("select", {id, onchange: e => update({[key]: e.target.value, limit: PAGE})},
      h("option", {value: "", text: "Any"}),
      options.map(o => h("option", {value: o, text: o, selected: o === value}))));
  const language = h("input", {id: "lang", value: state.lang, placeholder: "e.g. en", size: 8,
                               onchange: e => update({lang: e.target.value.trim(), limit: PAGE})});

  const results = h("div", {"aria-live": "polite", "aria-busy": "false"});
  mount(main, h("div", {class: "page"},
    h("div", {class: "page-head"},
      h("div", {}, h("h1", {text: "Explore datasets"}),
        h("p", {text: "Search the Hugging Face Hub, look inside a dataset, and import it into an IRIS table."}))),
    h("div", {class: "panel stack"},
      h("div", {class: "toolbar"}, query, h("div", {class: "segmented", role: "group", "aria-label": "Sort by"}, sortButtons)),
      h("div", {class: "toolbar"},
        select("task", "Task", TASKS, state.task, "task"),
        select("size", "Size", SIZES, state.size, "size"),
        h("div", {class: "field"}, h("label", {for: "lang", text: "Language"}), language))),
    results));

  query.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => update({q: query.value.trim(), limit: PAGE}), 300);
  });
  query.addEventListener("keydown", e => {
    if (e.key === "Enter") { clearTimeout(timer); update({q: query.value.trim(), limit: PAGE}); }
  });

  // Keeps the URL in step without re-rendering the whole view.
  function update(changes) {
    Object.assign(state, changes);
    sortButtons.forEach((b, i) => b.setAttribute("aria-pressed", String(SORTS[i][0] === state.sort)));
    history.replaceState(null, "", buildHash("explore", {q: state.q, sort: state.sort === "downloads" ? "" : state.sort,
      task: state.task, size: state.size, lang: state.lang, limit: state.limit === PAGE ? "" : state.limit}));
    load();
  }

  async function load() {
    if (controller) controller.abort();
    controller = new AbortController();
    const signal = controller.signal;
    results.setAttribute("aria-busy", "true");
    if (!hasResults) mount(results, h("div", {class: "results"}, Array.from({length: 6}, () => skeleton())));
    try {
      const data = await api.search({search: state.q, sort: state.sort, task: state.task, size: state.size,
                                     language: state.lang, limit: state.limit}, signal);
      show(data);
    } catch (e) {
      if (e.name === "AbortError") return;
      hasResults = false;
      mount(results, errorPanel(e, load, "Search failed"));
    } finally {
      if (!signal.aborted) results.setAttribute("aria-busy", "false");
    }
  }

  function show(data) {
    const filtered = state.q || state.task || state.size || state.lang;
    hasResults = data.items.length > 0;
    if (!data.items.length) {
      mount(results, emptyState("No datasets found",
        filtered ? "Try fewer words, another spelling, or remove a filter." : "The Hub returned no datasets.",
        filtered ? h("button", {class: "btn", text: "Clear search and filters",
                                onclick: () => navigate("explore", {})}) : null));
      return;
    }
    const heading = filtered ? `${data.count} dataset${data.count === 1 ? "" : "s"}` :
      "Popular datasets, sorted by " + SORTS.find(s => s[0] === state.sort)[1].toLowerCase();
    mount(results,
      h("div", {class: "stack"},
        h("div", {class: "row"}, h("span", {class: "count-line", text: heading}), h("span", {class: "spacer"}),
          data.cached ? h("span", {class: "count-line", text: "cached"}) : null),
        h("div", {class: "results"}, data.items.map(card)),
        data.count >= state.limit && state.limit < MAX ? h("div", {class: "result-foot"},
          h("button", {class: "btn", text: "Show more", onclick: () => update({limit: Math.min(MAX, state.limit + PAGE)})})) : null));
  }

  function card(d) {
    const [owner, name] = d.id.includes("/") ? d.id.split("/") : ["", d.id];
    return h("a", {class: "card", href: buildHash("dataset", {}, d.id)},
      h("div", {class: "card-title"}, owner ? h("span", {class: "owner", text: owner + "/"}) : null, name),
      h("p", {class: "card-desc", text: d.description || "No description."}),
      h("div", {class: "badges"},
        d.gated ? badge("Gated", "warn") : null,
        (d.tasks || []).slice(0, 2).map(t => badge(t, "accent")),
        d.sizeCategory ? badge(d.sizeCategory) : null,
        (d.license || []).slice(0, 1).map(l => badge(l))),
      h("div", {class: "card-meta"},
        h("span", {}, h("strong", {text: formatCount(d.downloads)}), " downloads"),
        h("span", {}, h("strong", {text: formatCount(d.likes)}), " likes"),
        d.lastModified ? h("span", {text: "updated " + timeAgo(d.lastModified)}) : null));
  }

  load();
  return () => { clearTimeout(timer); if (controller) controller.abort(); };
}
