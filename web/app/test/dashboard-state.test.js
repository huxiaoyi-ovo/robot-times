import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { runInNewContext } from "node:vm";

const page = readFileSync(new URL("../public/index.html", import.meta.url), "utf8");
function section(start, end) {
  const from = page.indexOf(start);
  const to = page.indexOf(end, from);
  assert.ok(from >= 0 && to > from);
  return page.slice(from, to);
}

const code = [
  section("function viewLib()", "/* ---------- 笔记 ---------- */"),
  section("const full = {};", "function figHtml("),
  section("/* ---------- 写入 ---------- */", "/* ---------- 问 AI ---------- */"),
  section("function hideHint()", "(async()=>{\n  try{ await loadAll();"),
  "globalThis.dashboard = { viewLib, loadLib, loadReaderDetails, loadAll, readerPaper, patchNote, flush, setStatus, addZotero, sendFeedback };",
].join("\n");

function dashboard(api, libStatus = "") {
  const S = {
    open: null, tab: "lib", notes: {}, papers: {}, feedback: {}, zotero: {},
    lib: { q: "", status: libStatus, list: null, total: 0, loading: false },
  };
  const saved = {}, retry = { hidden: true }, listeners = {};
  const context = {
    S, api, URLSearchParams,
    $: id => ({ "#saved": saved, "#note-retry": retry })[id],
    noteOf: id => S.notes[id] || {},
    toast() {}, refreshReader() {}, render() {},
    t: text => text, esc: text => text, STATUS: [],
    checkBtn: () => "", titleBtn: () => "", stamps: () => "", scoreEl: () => "", isRead: () => false,
    isLocal: () => true, needsSetup: () => true,
    window: { addEventListener: (name, fn) => { listeners[name] = fn; } },
    zBusy: false, zFix: {},
    setTimeout: () => 1, clearTimeout() {},
  };
  runInNewContext(code, context);
  return { S, saved, retry, listeners, ...context.dashboard };
}

function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}

const idle = () => new Promise(resolve => setImmediate(resolve));

test("keeps an archived library result and its open detail through bootstrap", async () => {
  const d = dashboard(async path => {
    if (path.startsWith("/api/papers?")) return { papers: [{ id: "archived", hasAnalysis: false }], total: 1 };
    if (path === "/api/paper/archived") return { id: "archived", abstractZh: "译文" };
    return { papers: { recent: { id: "recent" } } };
  });
  await d.loadLib();
  await d.loadReaderDetails("archived");
  d.S.open = "archived";
  await d.loadAll();
  assert.equal(d.S.lib.list[0], "archived");
  assert.equal(d.S.papers.archived.id, "archived");
  assert.equal(d.readerPaper("archived").abstractZh, "译文");
});

test("refreshes a filtered library after saving a status that changes page offsets", async () => {
  const papers = Array.from({ length: 51 }, (_, i) => ({ id: String(i + 1) }));
  const read = new Set();
  const d = dashboard(async (path, opts) => {
    if (path === "/api/notes/1") {
      assert.equal(JSON.parse(opts.body).status, "done");
      read.add("1");
      return { updatedAt: "saved" };
    }
    const query = new URLSearchParams(path.split("?")[1]);
    assert.equal(query.get("status"), "todo");
    const offset = Number(query.get("offset"));
    const todo = papers.filter(p => !read.has(p.id));
    return { papers: todo.slice(offset, offset + 50), total: todo.length };
  }, "todo");
  await d.loadLib();
  assert.equal(d.S.lib.list.at(-1), "50");
  d.viewLib();
  d.S.open = "1";
  d.setStatus("1", "done");
  await d.flush("1");
  await idle();
  assert.equal(d.S.lib.total, 50);
  assert.equal(d.S.lib.list[0], "2");
  assert.equal(d.S.lib.list.at(-1), "51");
  d.viewLib();
  assert.equal(d.S.order[0], "1");
  d.S.open = null;
  d.viewLib();
  assert.equal(d.S.order[0], "2");
});

test("keeps a failed draft, retries once, and waits before adding to Zotero", async () => {
  const retrySave = deferred(), calls = [];
  let failure = true;
  const d = dashboard(async (path, opts) => {
    if (path === "/api/bootstrap") return { notes: { p: { note: "old" } } };
    calls.push(path);
    if (path === "/api/zotero/p") return { key: "z-key" };
    assert.equal(JSON.parse(opts.body).note, "draft");
    if (failure) { failure = false; throw new Error("offline"); }
    return retrySave.promise;
  });
  d.S.open = "p";
  d.S.notes.p = { note: "old" };
  d.patchNote("p", { note: "draft" });
  await d.addZotero();
  assert.deepEqual(calls, ["/api/notes/p"]);
  assert.equal(d.saved.textContent, "没保存上");
  assert.equal(d.retry.hidden, false);
  let warned = false;
  d.listeners.beforeunload({ preventDefault() { warned = true; } });
  assert.equal(warned, true);
  await d.loadAll();
  assert.equal(d.S.notes.p.note, "draft");

  const adding = d.addZotero();
  await d.addZotero();
  const pending = d.flush("p");
  assert.equal(d.flush("p"), pending);
  assert.deepEqual(calls, ["/api/notes/p", "/api/notes/p"]);
  retrySave.resolve({ updatedAt: "saved" });
  await adding;
  assert.deepEqual(calls, ["/api/notes/p", "/api/notes/p", "/api/zotero/p"]);
  assert.equal(d.S.zotero.p, "z-key");
  assert.equal(d.saved.textContent, "已保存");
  assert.equal(d.retry.hidden, true);
});

test("merges overlapping bootstraps without treating an earlier response as a local note edit", async () => {
  const earlier = deferred(), later = deferred(), latest = deferred(), stale = deferred();
  const replies = [earlier, later, stale, latest];
  const d = dashboard(path => {
    if (path.startsWith("/api/notes/")) return Promise.resolve({ updatedAt: "saved" });
    return replies.shift().promise;
  });
  d.S.notes.paper = { note: "original" };
  const first = d.loadAll(), second = d.loadAll();
  earlier.resolve({ notes: { paper: { note: "earlier" } } });
  await first;
  later.resolve({ notes: { paper: { note: "later" } } });
  await second;
  assert.equal(d.S.notes.paper.note, "later");

  const slow = d.loadAll(), fast = d.loadAll();
  d.patchNote("paper", { note: "local edit" });
  await d.flush("paper");
  latest.resolve({ notes: { paper: { note: "previous snapshot" }, other: { note: "fresh" } } });
  await fast;
  stale.resolve({ notes: { other: { note: "stale" } } });
  await slow;
  assert.equal(d.S.notes.paper.note, "local edit");
  assert.equal(d.S.notes.paper.updatedAt, "saved");
  assert.equal(d.S.notes.other.note, "fresh");
});

test("keeps feedback toggles consistent during and after concurrent bootstraps", async () => {
  const firstPost = deferred(), laterBootstrap = deferred(), ops = [];
  let bootstrapCount = 0;
  const d = dashboard(async (path, opts) => {
    if (path === "/api/bootstrap") {
      if (++bootstrapCount === 1) return { feedback: {} };
      return laterBootstrap.promise;
    }
    ops.push(JSON.parse(opts.body).action);
    if (ops.length === 1) await firstPost.promise;
  });
  const like = d.sendFeedback("paper", "like");
  await d.loadAll();
  assert.equal(d.S.feedback.paper, "like");
  const clear = d.sendFeedback("paper", "like");
  assert.equal(d.S.feedback.paper, undefined);
  const refresh = d.loadAll();
  firstPost.resolve();
  await Promise.all([like, clear]);
  laterBootstrap.resolve({ feedback: { paper: "like" } });
  await refresh;
  assert.deepEqual(ops, ["like", "clear"]);
  assert.equal(d.S.feedback.paper, undefined);
});
