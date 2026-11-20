import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const page = readFileSync(new URL("../public/index.html", import.meta.url), "utf8");
const section = (start, end) => page.slice(page.indexOf(start), page.indexOf(end, page.indexOf(start)));
const script = [
  section("function render(){", "// 报头随滚动淡出"),
  section("let aiCtl=null", "function figHtml(f, i)"),
  section("function openReader(id", "function refreshAnalysis(id)"),
  "function refreshAnalysis(id){ updates.push(readerPaper(id)); }",
].join("\n");
const settle = () => new Promise(resolve => setImmediate(resolve));

function pageWithReader(api) {
  const noop = () => {};
  const element = {
    classList: {contains: () => false, remove:noop, add:noop},
    style: {setProperty:noop},
    querySelectorAll: () => [],
    querySelector: () => ({focus:noop}),
  };
  const S = {
    ready:true, tab:"today", papers:{p:{id:"p", title:"Original", hasAnalysis:false}},
    order:["p"], zotero:{},
  };
  const updates = [];
  const context = vm.createContext({
    S, updates, api, $: () => element, document:{}, TIER_MODE:{}, MODES:{},
    articleHtml:() => "", refreshReader:noop, watchReveal:noop, checkZotero:noop,
    esc: x => x, t: x => x, clearTimeout:noop, renderChrome:noop, needsSetup: () => false,
    viewToday:() => "", viewNews:noop, viewWeek:noop, viewLib:noop, viewNotes:noop, viewCfg:noop,
  });
  vm.runInContext(script, context);
  return {S, updates, open: () => vm.runInContext('openReader("p")', context),
    render: () => vm.runInContext("render()", context)};
}

test("仍打开的论文从摘要切换为完整解读", async () => {
  let requests = 0;
  const reader = pageWithReader(path => {
    assert.equal(path, "/api/paper/p");
    requests++;
    return Promise.resolve({id:"p", title:"Older detail", abstractZh:"摘要",
      analysis: requests === 1 ? null : {review:"解读"}});
  });
  reader.open();
  await settle();
  assert.equal(reader.updates.at(-1).abstractZh, "摘要");

  reader.S.papers = {p:{id:"p", title:"New title", hasAnalysis:true}};
  reader.render();
  await settle();
  assert.equal(requests, 2);
  assert.equal(reader.updates.at(-1).analysis.review, "解读");
  assert.equal(reader.updates.at(-1).title, "New title");
  assert.equal(reader.S.papers.p.title, "New title");

  reader.open();
  await settle();
  assert.equal(requests, 2);
});

test("摘要请求还没结束时发现新解读，之后补取且保留新标题", async () => {
  const requests = [];
  const reader = pageWithReader(() => new Promise(resolve => requests.push(resolve)));
  reader.open();
  reader.S.papers = {p:{id:"p", title:"Latest title", hasAnalysis:true}};
  reader.render();
  assert.equal(requests.length, 1);

  requests[0]({id:"p", title:"Old detail", abstractZh:"摘要", analysis:null});
  await settle();
  assert.equal(reader.updates.at(-1).title, "Latest title");
  assert.equal(requests.length, 2);

  requests[1]({id:"p", title:"Old detail", analysis:{review:"解读"}});
  await settle();
  assert.equal(reader.S.papers.p.title, "Latest title");
  assert.equal(reader.updates.at(-1).analysis.review, "解读");
});
