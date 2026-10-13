// Pure helpers without browser APIs: routes, snippets and formatting.

export const VERSION = "5";

// "#/dataset/stanfordnlp/imdb?tab=preview" -> {view: "dataset", id: "stanfordnlp/imdb", params: {tab: "preview"}}
// "#/tables/Demo.IMDB" -> {view: "tables", id: "Demo.IMDB", ...}; "#/imports" -> {view: "imports", id: null, ...}
export function parseHash(hash) {
  const text = String(hash || "").replace(/^#\/?/, "");
  const [path, query = ""] = text.split("?");
  const params = Object.fromEntries(new URLSearchParams(query));
  const parts = path.split("/").filter(Boolean).map(decodeURIComponent);
  if (parts[0] === "dataset" && parts.length >= 2) {
    return {view: "dataset", id: parts.slice(1, 3).join("/"), params};
  }
  if (parts[0] === "tables") return {view: "tables", id: parts[1] || null, params};
  if (parts[0] === "imports") return {view: "imports", id: null, params};
  return {view: "explore", id: null, params};
}

export function buildHash(view, params = {}, id = null) {
  const query = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") query.set(k, v);
  }
  const path = id && (view === "dataset" || view === "tables") ? view + "/" + id.split("/").map(encodeURIComponent).join("/") : view;
  const qs = query.toString();
  return "#/" + path + (qs ? "?" + qs : "");
}

// The equivalent calls for an import, without trailing default arguments.
function trimDefaults(args, defaults) {
  const out = args.slice();
  while (out.length > 1 && out[out.length - 1] === defaults[out.length - 1]) out.pop();
  return out;
}

export function objectScriptCall({dataset, table = "", split = "train", limit = 0, config = "", mode = "replace", revision = ""}) {
  const s = v => '"' + String(v ?? "").replace(/"/g, '""') + '"';
  const args = trimDefaults([dataset, table || "", split || "train", Number(limit) || 0, config || "", mode || "replace", revision || ""],
                            [null, "", "train", 0, "", "replace", ""]);
  return "do ##class(dc.hf2iris.Loader).Load(" + args.map(a => typeof a === "number" ? String(a) : s(a)).join(",") + ")";
}

export function sqlCall({dataset, table = "", split = "train", limit = 0, config = "", mode = "replace", revision = ""}) {
  const s = v => (v === "" || v === null || v === undefined) ? "NULL" : "'" + String(v).replace(/'/g, "''") + "'";
  const args = trimDefaults([dataset, table || "", split || "train", Number(limit) || 0, config || "", mode || "replace", revision || ""],
                            [null, "", "train", 0, "", "replace", ""]);
  return "SELECT dc_hf2iris.load_dataset(" + args.map(a => typeof a === "number" ? String(a) : s(a)).join(", ") + ")";
}

export function formatCount(n) {
  if (n === null || n === undefined || n === "") return "-";
  const v = Number(n);
  if (!isFinite(v)) return "-";
  if (Math.abs(v) < 1000) return String(v);
  const units = [["B", 1e9], ["M", 1e6], ["K", 1e3]];
  for (const [u, size] of units) {
    if (Math.abs(v) >= size) {
      const x = v / size;
      return (x >= 100 ? Math.round(x) : Math.round(x * 10) / 10) + u;
    }
  }
  return String(v);
}

export function formatBytes(n) {
  if (n === null || n === undefined || n === "") return "-";
  let v = Number(n);
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return (i === 0 ? v : v >= 100 ? Math.round(v) : Math.round(v * 10) / 10) + " " + units[i];
}

export function timeAgo(iso, now = Date.now()) {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (!isFinite(t)) return "";
  const s = Math.max(0, Math.round((now - t) / 1000));
  const steps = [[60, "second"], [60, "minute"], [24, "hour"], [30, "day"], [12, "month"], [Infinity, "year"]];
  let v = s;
  for (const [size, unit] of steps) {
    if (v < size) return v <= 1 && unit === "second" ? "just now" : v + " " + unit + (v === 1 ? "" : "s") + " ago";
    v = Math.floor(v / size);
  }
  return "";
}

// The table an import writes to by default: "HF.<name>" (the server cleans it further).
export function defaultTable(id) {
  const name = String(id || "").split("/").pop() || "data";
  return "HF." + name.replace(/[^A-Za-z0-9_]/g, "_").replace(/^_+|_+$/g, "");
}

export const TASKS = [
  "text-classification", "token-classification", "question-answering", "summarization",
  "translation", "text-generation", "sentence-similarity", "fill-mask", "zero-shot-classification",
  "table-question-answering", "tabular-classification", "tabular-regression", "time-series-forecasting",
  "image-classification", "object-detection", "automatic-speech-recognition", "feature-extraction"];

export const SIZES = ["n<1K", "1K<n<10K", "10K<n<100K", "100K<n<1M", "1M<n<10M", "10M<n<100M", "100M<n<1B", "n>1B"];

export const SORTS = [["downloads", "Downloads"], ["likes", "Likes"], ["trending", "Trending"], ["updated", "Recently updated"]];

// ------------------------------------------------------------------ theme
export const THEME_KEY = "hf2iris.theme";
export const themeFor = saved => (saved === "light" ? "light" : "dark");

// WCAG contrast of two colors: "#rrggbb" or "rgba(r, g, b, a)" (blended over `under`).
export function parseColor(text, under = [0, 0, 0]) {
  const t = String(text).trim();
  let m = t.match(/^#([0-9a-f]{6})$/i);
  if (m) return [0, 2, 4].map(i => parseInt(m[1].slice(i, i + 2), 16));
  m = t.match(/^rgba?\(([^)]+)\)$/i);
  if (m) {
    const [r, g, b, a = 1] = m[1].split(",").map(Number);
    return [r, g, b].map((v, i) => Math.round(v * a + under[i] * (1 - a)));
  }
  throw new Error("unsupported color " + t);
}

function luminance([r, g, b]) {
  const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}

export function contrast(a, b) {
  const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
}

// Server times ("2026-10-12 22:41:22") are shown as they are, without the seconds.
export const serverTime = t => (t ? String(t).slice(0, 16) : "");
