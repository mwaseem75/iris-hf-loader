// The only place that talks to the IRIS REST API. Hugging Face is never called from
// the browser; HF_TOKEN stays on the server.

const BASE = "/api/hf2iris";
export const AUTH_KEY = "hf2iris.auth";
export const USER_KEY = "hf2iris.user";

export class ApiError extends Error {
  constructor(status, code, message) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

const CODES = {400: "bad_request", 401: "unauthorized", 403: "gated", 404: "not_found", 409: "conflict",
               429: "rate_limited", 502: "upstream", 504: "timeout"};

// The API's 401 has no WWW-Authenticate header, so the browser never prompts;
// the app asks for credentials through this handler and retries.
let askLogin = async () => { throw new ApiError(401, "unauthorized", "Sign in required"); };
export function onLogin(handler) { askLogin = handler; }

export function signedInUser() { return sessionStorage.getItem(USER_KEY) || ""; }

export function saveLogin(user, password) {
  sessionStorage.setItem(AUTH_KEY, btoa(unescape(encodeURIComponent(user + ":" + password))));
  sessionStorage.setItem(USER_KEY, user);
}

export function signOut() {
  sessionStorage.removeItem(AUTH_KEY);
  sessionStorage.removeItem(USER_KEY);
}

export async function request(path, {method = "GET", body, signal} = {}) {
  for (let attempt = 0; ; attempt++) {
    const auth = sessionStorage.getItem(AUTH_KEY);
    const headers = {Accept: "application/json"};
    if (auth) headers.Authorization = "Basic " + auth;
    if (body !== undefined) headers["Content-Type"] = "application/json";
    let r;
    try {
      r = await fetch(BASE + path, {method, headers, signal,
                                    body: body === undefined ? undefined : JSON.stringify(body)});
    } catch (e) {
      if (e.name === "AbortError") throw e;
      throw new ApiError(0, "network", "The IRIS server could not be reached.");
    }
    if (r.status === 401 && attempt < 3) {
      sessionStorage.removeItem(AUTH_KEY);
      await askLogin(auth ? "Wrong username or password." : "");
      continue;
    }
    const data = await r.json().catch(() => ({}));
    if (!r.ok) {
      throw new ApiError(r.status, data.code || CODES[r.status] || "error", data.error || r.statusText || "Request failed");
    }
    return data;
  }
}

function qs(params = {}) {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") q.set(k, v);
  }
  const s = q.toString();
  return s ? "?" + s : "";
}

export const api = {
  status: (signal) => request("/status", {signal}),
  search: (params, signal) => request("/datasets" + qs(params), {signal}),
  dataset: (id, signal) => request("/dataset" + qs({id}), {signal}),
  preview: (params, signal) => request("/dataset/preview" + qs(params), {signal}),
  plan: (body, signal) => request("/plan", {method: "POST", body, signal}),
  startJob: (body) => request("/jobs", {method: "POST", body}),
  job: (id, signal) => request("/jobs/" + encodeURIComponent(id), {signal}),
  cancelJob: (id) => request("/jobs/" + encodeURIComponent(id) + "/cancel", {method: "POST", body: {}}),
  jobs: (limit, signal) => request("/jobs" + qs({limit}), {signal}),
  history: (signal) => request("/history", {signal}),
  tables: (signal) => request("/tables", {signal}),
  table: (name, signal) => request("/tables/" + encodeURIComponent(name), {signal}),
  tableRows: (name, offset, rows, signal) => request("/preview/" + encodeURIComponent(name) + qs({offset, rows}), {signal}),
};
