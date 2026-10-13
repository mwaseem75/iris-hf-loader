// DOM helpers. Text always goes in through textContent, never innerHTML.

// h("button", {class: "btn", onclick: fn, "aria-label": "x"}, "text", child, [more])
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "value") el.value = v;
    else if (k === "disabled" || k === "hidden" || k === "checked" || k === "selected") el[k] = Boolean(v);
    else el.setAttribute(k, v === true ? "" : String(v));
  }
  append(el, children);
  return el;
}

export function append(el, children) {
  for (const c of [].concat(children)) {
    if (c === null || c === undefined || c === false) continue;
    if (Array.isArray(c)) append(el, c);
    else el.appendChild(typeof c === "object" ? c : document.createTextNode(String(c)));
  }
  return el;
}

export function mount(el, ...children) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return append(el, children);
}

export function skeleton(lines = 3, cls = "skeleton-card") {
  return h("div", {class: cls, "aria-hidden": "true"},
    Array.from({length: lines}, (_, i) => h("div", {class: "skeleton-line" + (i === 0 ? " wide" : "")})));
}

// An error panel with an optional retry button; err may be an ApiError.
export function errorPanel(err, retry, title = "Something went wrong") {
  const hints = {
    rate_limited: "Hugging Face is limiting requests from this server. Wait a minute, or set HF_TOKEN.",
    timeout: "Hugging Face did not answer in time.",
    gated: "This dataset needs accepted terms on huggingface.co and a token (HF_TOKEN) in the container.",
    not_found: "Check the dataset id, config and split.",
    upstream: "Hugging Face returned an error.",
    network: "The IRIS server could not be reached.",
  };
  return h("div", {class: "panel error-panel", role: "alert"},
    h("h3", {text: title}),
    h("p", {text: err && err.message ? err.message : String(err)}),
    err && hints[err.code] ? h("p", {class: "muted", text: hints[err.code]}) : null,
    retry ? h("button", {class: "btn", onclick: retry, text: "Try again"}) : null);
}

export function emptyState(title, text, action) {
  return h("div", {class: "empty-state"}, h("h3", {text: title}), text ? h("p", {class: "muted", text}) : null, action || null);
}

export function badge(text, kind = "") {
  return h("span", {class: "badge" + (kind ? " badge-" + kind : ""), text});
}

let toastBox = null;
export function toast(message, kind = "info") {
  toastBox = toastBox || document.getElementById("toasts");
  if (!toastBox) return;
  const t = h("div", {class: "toast toast-" + kind, role: kind === "error" ? "alert" : "status", text: message});
  toastBox.appendChild(t);
  setTimeout(() => t.remove(), 5000);
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast("Copied to the clipboard", "ok");
  } catch (e) {
    toast("Copy failed; select the text and copy it yourself", "error");
  }
}

// Tabs following the WAI-ARIA pattern: arrow keys, Home and End move between tabs,
// only the selected tab is in the Tab order. items: [[key, label]].
export function tabs(items, selected, onSelect, label) {
  const panel = h("div", {role: "tabpanel", tabindex: "0"});
  const buttons = items.map(([key, text]) => h("button", {type: "button", role: "tab", id: "tab-" + key, text,
                                                          "aria-controls": "tabpanel", onclick: () => select(key)}));
  panel.setAttribute("id", "tabpanel");
  const list = h("div", {class: "tabs", role: "tablist", "aria-label": label}, buttons);
  list.addEventListener("keydown", e => {
    const i = items.findIndex(([key]) => key === current);
    const next = {ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: items.length - 1}[e.key];
    if (next === undefined) return;
    e.preventDefault();
    const key = items[(next + items.length) % items.length][0];
    select(key);
    buttons[items.findIndex(([k]) => k === key)].focus();
  });
  let current = selected;
  function select(key) {
    current = key;
    buttons.forEach((b, i) => {
      const on = items[i][0] === key;
      b.setAttribute("aria-selected", String(on));
      b.setAttribute("tabindex", on ? "0" : "-1");
    });
    panel.setAttribute("aria-labelledby", "tab-" + key);
    onSelect(key, panel);
  }
  return {list, panel, select};
}
