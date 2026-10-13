// node --test tests/ui/state.test.mjs
import assert from "node:assert/strict";
import {readFileSync, readdirSync, statSync} from "node:fs";
import {join, dirname} from "node:path";
import {fileURLToPath} from "node:url";
import test from "node:test";
import {VERSION, buildHash, defaultTable, formatBytes, formatCount, objectScriptCall, parseHash, sqlCall, timeAgo}
  from "../../web/js/state.js";

const WEB = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "web");

test("routes", () => {
  assert.deepEqual(parseHash(""), {view: "explore", id: null, params: {}});
  assert.deepEqual(parseHash("#/explore?q=imdb&sort=likes"), {view: "explore", id: null, params: {q: "imdb", sort: "likes"}});
  assert.deepEqual(parseHash("#/dataset/stanfordnlp/imdb?tab=preview&offset=20"),
                   {view: "dataset", id: "stanfordnlp/imdb", params: {tab: "preview", offset: "20"}});
  assert.equal(parseHash("#/dataset/rotten_tomatoes").id, "rotten_tomatoes");
  assert.equal(parseHash("#/nonsense").view, "explore");
  assert.equal(buildHash("explore", {q: "a b", sort: "", task: null}), "#/explore?q=a+b");
  assert.equal(buildHash("dataset", {tab: "schema"}, "openai/gsm8k"), "#/dataset/openai/gsm8k?tab=schema");
  const hash = buildHash("dataset", {config: "main", split: "test"}, "openai/gsm8k");
  assert.deepEqual(parseHash(hash), {view: "dataset", id: "openai/gsm8k", params: {config: "main", split: "test"}});
});

test("snippets", () => {
  assert.equal(objectScriptCall({dataset: "stanfordnlp/imdb"}), 'do ##class(dc.hf2iris.Loader).Load("stanfordnlp/imdb")');
  assert.equal(objectScriptCall({dataset: "stanfordnlp/imdb", table: "Demo.IMDB", split: "test", limit: 1000}),
               'do ##class(dc.hf2iris.Loader).Load("stanfordnlp/imdb","Demo.IMDB","test",1000)');
  assert.equal(objectScriptCall({dataset: "openai/gsm8k", config: "main", revision: "abc"}),
               'do ##class(dc.hf2iris.Loader).Load("openai/gsm8k","","train",0,"main","replace","abc")');
  assert.equal(objectScriptCall({dataset: 'a/b"c'}), 'do ##class(dc.hf2iris.Loader).Load("a/b""c")');
  assert.equal(sqlCall({dataset: "openai/gsm8k", table: "Demo.G", split: "test", limit: 50, config: "main"}),
               "SELECT dc_hf2iris.load_dataset('openai/gsm8k', 'Demo.G', 'test', 50, 'main')");
  assert.equal(sqlCall({dataset: "x/y", revision: "r1"}),
               "SELECT dc_hf2iris.load_dataset('x/y', NULL, 'train', 0, NULL, 'replace', 'r1')");
  assert.equal(sqlCall({dataset: "o'k/y"}), "SELECT dc_hf2iris.load_dataset('o''k/y')");
});

test("formatting", () => {
  assert.equal(formatCount(999), "999");
  assert.equal(formatCount(173262), "173K");
  assert.equal(formatCount(1287), "1.3K");
  assert.equal(formatCount(14868862), "14.9M");
  assert.equal(formatCount(null), "-");
  assert.equal(formatBytes(512), "512 B");
  assert.equal(formatBytes(83446840), "79.6 MB");
  const now = Date.parse("2026-10-13T12:00:00Z");
  assert.equal(timeAgo("2026-10-13T11:59:59Z", now), "just now");
  assert.equal(timeAgo("2026-10-13T10:00:00Z", now), "2 hours ago");
  assert.equal(timeAgo("2026-09-13T12:00:00Z", now), "1 month ago");
  assert.equal(timeAgo("", now), "");
  assert.equal(defaultTable("stanfordnlp/imdb"), "HF.imdb");
  assert.equal(defaultTable("x/rotten-tomatoes.v2"), "HF.rotten_tomatoes_v2");
});

function files(dir) {
  return readdirSync(dir).flatMap(f => statSync(join(dir, f)).isDirectory() ? files(join(dir, f)) : [join(dir, f)]);
}

test("web files are ASCII (IRIS serves them as ISO-8859-1)", () => {
  for (const f of files(WEB)) {
    const bad = [...readFileSync(f, "utf8")].findIndex(c => c.charCodeAt(0) > 127);
    assert.equal(bad, -1, `${f} has a non-ASCII character at ${bad}`);
  }
});

test("every script and stylesheet URL carries the same version", () => {
  const index = readFileSync(join(WEB, "index.html"), "utf8");
  assert.match(index, new RegExp(`app\\.css\\?v=${VERSION}"`));
  assert.match(index, new RegExp(`js/app\\.js\\?v=${VERSION}"`));
  for (const f of files(join(WEB, "js"))) {
    for (const [, spec] of readFileSync(f, "utf8").matchAll(/from "(\.[^"]+)"/g)) {
      assert.ok(spec.endsWith(`?v=${VERSION}`), `${f}: import "${spec}" should end with ?v=${VERSION}`);
    }
  }
});

test("routes for imports and tables", async () => {
  const s = await import("../../web/js/state.js");
  assert.deepEqual(s.parseHash("#/imports"), {view: "imports", id: null, params: {}});
  assert.deepEqual(s.parseHash("#/tables"), {view: "tables", id: null, params: {}});
  assert.deepEqual(s.parseHash("#/tables/Demo.IMDB?tab=columns"), {view: "tables", id: "Demo.IMDB", params: {tab: "columns"}});
  assert.equal(s.buildHash("tables", {offset: 50}, "Demo.IMDB"), "#/tables/Demo.IMDB?offset=50");
  assert.equal(s.themeFor("light"), "light");
  assert.equal(s.themeFor(null), "dark");
  assert.equal(s.serverTime("2026-10-12 22:41:22"), "2026-10-12 22:41");
});

function tokens(css, selector) {
  const block = css.slice(css.indexOf(selector)).match(/\{([^}]*)\}/)[1];
  return Object.fromEntries([...block.matchAll(/--([\w-]+):\s*([^;]+);/g)].map(m => [m[1], m[2].trim()]));
}

test("text contrast is at least 4.5:1 in both themes", async () => {
  const {contrast, parseColor} = await import("../../web/js/state.js");
  const css = readFileSync(join(WEB, "app.css"), "utf8");
  const dark = tokens(css, ':root, [data-theme="dark"]');
  const themes = {dark, light: {...dark, ...tokens(css, '[data-theme="light"]')}};
  for (const [name, t] of Object.entries(themes)) {
    const c = (key, under = "surface") => parseColor(t[key], parseColor(t[under]));
    const pairs = [
      ["text", "bg"], ["text", "surface"], ["text", "surface-2"], ["text", "surface-3"],
      ["text-2", "bg"], ["text-2", "surface"], ["text-2", "surface-2"],
      ["muted", "bg"], ["muted", "surface"], ["muted", "surface-2"],
      ["accent-strong", "bg"], ["accent-strong", "surface"], ["on-accent", "accent"],
      ["ok", "surface"], ["warn", "surface"], ["danger", "surface"]];
    for (const [fg, bg] of pairs) {
      const ratio = contrast(c(fg), c(bg));
      assert.ok(ratio >= 4.5, `${name}: --${fg} on --${bg} is ${ratio.toFixed(2)}:1`);
    }
    // badges and notices: colored text on a translucent tint over a panel
    for (const [fg, soft] of [["accent-strong", "accent-soft"], ["ok", "ok-soft"], ["warn", "warn-soft"], ["danger", "danger-soft"],
                              ["notice-ok", "ok-soft"], ["notice-warn", "warn-soft"], ["notice-danger", "danger-soft"]]) {
      const ratio = contrast(c(fg), c(soft));
      assert.ok(ratio >= 4.5, `${name}: --${fg} on --${soft} is ${ratio.toFixed(2)}:1`);
    }
    // non-text: focus ring and form control outlines need 3:1
    for (const [fg, bg] of [["focus", "bg"], ["focus", "surface"], ["input-border", "bg"], ["input-border", "surface"],
                            ["input-border", "surface-2"]]) {
      const ratio = contrast(c(fg), c(bg));
      assert.ok(ratio >= 3, `${name}: --${fg} on --${bg} is ${ratio.toFixed(2)}:1`);
    }
  }
});
