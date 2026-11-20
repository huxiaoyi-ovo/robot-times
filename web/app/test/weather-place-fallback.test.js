import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const page = readFileSync(new URL("../public/index.html", import.meta.url), "utf8");
const section = (start, end) => page.slice(page.indexOf(start), page.indexOf(end, page.indexOf(start)));
const script = [
  section("function weatherEl(", "/* 点天气"),
  section("const WX_CODES=", "function render(){"),
  section("let bootstrapSeq=", "(async()=>{\n  try{ await loadAll()"),
].join("\n");

function weatherPage(daily) {
  const old = {name:"Old", lat:1, lon:1};
  const live = {city:"Old", live:true, code:1, temp:6};
  const S = {ready:true, tab:"today", weather:live, notes:{}, papers:{}, lib:{}, days:[]};
  const storage = new Map([["wx-place", JSON.stringify(old)]]);
  const elements = new Map();
  const element = selector => {
    if (!elements.has(selector)) elements.set(selector, {});
    return elements.get(selector);
  };
  const context = vm.createContext({
    S, navigator:{}, URLSearchParams, $:element, TABS:[], WX_ICONS:{}, pending:{}, noteVersions:new Map(), feedbackVersions:new Map(), feedbackWrites:new Map(),
    document:{body:{className:""}, querySelectorAll:() => []}, closeWxPop() {},
    t:x => x, esc:x => String(x||""), isEn:() => false, fmtDate:x => x, longDate:x => x,
    isRead:() => false, needsSetup:() => true,
    localStorage:{
      getItem:k => storage.get(k)??null,
      setItem:(k,v) => storage.set(k,v),
      removeItem:k => storage.delete(k),
    },
    api:async () => ({weather:daily}),
    fetch:async () => ({ok:false, status:503}),
  });
  vm.runInContext(script, context);
  return {S, live, element, storage, run:js => vm.runInContext(js, context)};
}

test("切换城市失败后使用日报里的预报", async () => {
  const daily = {city:"Daily", code:2, tmin:3, tmax:10};
  const board = weatherPage(daily);
  await board.run("loadAll()");
  assert.equal(board.S.weather, board.live);
  await board.run('chooseWxPlace({name:"New", lat:2, lon:2})');
  assert.equal(JSON.parse(board.storage.get("wx-place")).name, "New");
  assert.equal(board.S.weather, daily);
  assert.match(board.element("#mastsub").innerHTML, /Daily/);
});

test("切回自动定位后没有预报也能再选地点", async () => {
  const board = weatherPage(null);
  await board.run("loadAll()");
  await board.run("chooseWxPlace(null)");
  assert.equal(board.storage.has("wx-place"), false);
  assert.equal(board.S.weather, null);
  assert.match(board.element("#mastsub").innerHTML, /data-wx>天气地点<\/button>/);
  assert.match(board.element("#m1r").innerHTML, /data-wx>天气地点<\/button>/);
});

test("同地点的定时刷新失败时保留原来有效的实时天气", async () => {
  const board = weatherPage({city:"Daily", code:2, tmin:3, tmax:10});
  await board.run("loadAll()");
  await board.run("liveWeather(true)");
  assert.equal(board.S.weather, board.live);
});
