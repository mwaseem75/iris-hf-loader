// A very small DOM, just enough to run the view modules in Node (no packages).

class Node {
  constructor() { this.childNodes = []; this.parentNode = null; }
  get firstChild() { return this.childNodes[0] || null; }
  appendChild(child) {
    if (child.parentNode) child.parentNode.removeChild(child);
    child.parentNode = this;
    this.childNodes.push(child);
    return child;
  }
  removeChild(child) {
    this.childNodes = this.childNodes.filter(c => c !== child);
    child.parentNode = null;
    return child;
  }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  get textContent() { return this.childNodes.map(c => c.textContent).join(""); }
  set textContent(v) {
    for (const c of this.childNodes) c.parentNode = null;
    this.childNodes = [];
    if (v !== "" && v !== null && v !== undefined) this.appendChild(new Text(String(v)));
  }
}

class Text extends Node {
  constructor(text) { super(); this.data = text; }
  get textContent() { return this.data; }
  set textContent(v) { this.data = String(v); }
}

class Element extends Node {
  constructor(tag) {
    super();
    this.tagName = tag.toUpperCase();
    this.attributes = {};
    this.listeners = {};
    this.style = {};
    this.className = "";
    this.disabled = false;
    this.hidden = false;
    this.selected = false;
    this._value = undefined;
  }
  setAttribute(k, v) { this.attributes[k] = String(v); }
  getAttribute(k) { return k in this.attributes ? this.attributes[k] : null; }
  removeAttribute(k) { delete this.attributes[k]; }
  addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
  focus() {}
  get value() {
    if (this._value !== undefined) return this._value;
    if (this.tagName === "SELECT") {
      const options = all(this, e => e.tagName === "OPTION");
      const chosen = options.find(o => o.selected) || options[0];
      return chosen ? (chosen._value ?? chosen.getAttribute("value") ?? chosen.textContent) : "";
    }
    return "";
  }
  set value(v) { this._value = String(v); }
  // test helpers
  fire(type, extra = {}) {
    const event = {type, target: this, key: extra.key, preventDefault() {}, ...extra};
    for (const fn of this.listeners[type] || []) fn(event);
    if (type === "click" && this.attributes.onclick) throw new Error("inline handlers are not used");
  }
  click() { if (!this.disabled) this.fire("click"); }
}

export function all(root, pred) {
  const out = [];
  const walk = n => {
    for (const c of n.childNodes) {
      if (c instanceof Element) {
        if (pred(c)) out.push(c);
        walk(c);
      }
    }
  };
  walk(root);
  return out;
}

export function find(root, pred) { return all(root, pred)[0] || null; }
export const byText = (root, tag, text) =>
  find(root, e => e.tagName === tag.toUpperCase() && e.textContent.trim() === text);
export const byClass = (root, cls) => all(root, e => (" " + e.className + " ").includes(" " + cls + " "));
export const byId = (root, id) => find(root, e => e.getAttribute("id") === id);

export function install(baseUrl) {
  const store = {};
  globalThis.document = {
    createElement: tag => new Element(tag),
    createTextNode: text => new Text(text),
    getElementById: () => null,
    querySelectorAll: () => [],
  };
  globalThis.sessionStorage = {
    getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); },
    removeItem: k => { delete store[k]; },
  };
  globalThis.history = {replaceState: (s, t, url) => { globalThis.location.hash = url; }};
  globalThis.location = {hash: ""};
  const realFetch = globalThis.fetch;
  globalThis.fetch = (url, opts) => realFetch(baseUrl + url, opts);
  return new Element("main");
}

export async function waitFor(fn, what, timeout = 30000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    const v = fn();
    if (v) return v;
    await new Promise(r => setTimeout(r, 100));
  }
  throw new Error("timed out waiting for " + what);
}

// Basic accessibility checks on a rendered tree; returns a list of problems.
export function audit(root) {
  const problems = [];
  const labels = new Set(all(root, e => e.tagName === "LABEL").map(l => l.getAttribute("for")));
  const name = e => (e.textContent || "").trim() || e.getAttribute("aria-label") || e.getAttribute("title");
  for (const e of all(root, () => true)) {
    const tag = e.tagName;
    if ((tag === "INPUT" || tag === "SELECT") && !labels.has(e.getAttribute("id")) && !e.getAttribute("aria-label")) {
      problems.push(`${tag.toLowerCase()} #${e.getAttribute("id")} has no label`);
    }
    if ((tag === "BUTTON" || tag === "A") && !name(e)) problems.push(`${tag.toLowerCase()} without a name`);
    if (tag === "A" && !e.getAttribute("href")) problems.push(`link "${name(e)}" without href`);
    if (e.listeners.click && tag !== "BUTTON" && tag !== "A" && tag !== "SUMMARY") problems.push(`clickable ${tag.toLowerCase()}`);
    if (e.getAttribute("role") === "tab" && !e.getAttribute("aria-selected")) problems.push(`tab "${name(e)}" without aria-selected`);
    if (e.getAttribute("role") === "tablist" && !e.getAttribute("aria-label")) problems.push("tablist without a label");
    if (e.getAttribute("role") === "progressbar" && !e.getAttribute("aria-label")) problems.push("progressbar without a label");
  }
  return problems;
}
