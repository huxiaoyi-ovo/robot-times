import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const page = readFileSync(new URL("../public/index.html", import.meta.url), "utf8");
const section = (start, end) => page.slice(page.indexOf(start), page.indexOf(end, page.indexOf(start)));
const script = [
  section("async function runJob(", "function failDismissed("),
  section("let jobTimer=null", "/* ---------- 事件 ---------- */"),
  section("let bootstrapSeq=", "(async()=>{\n  try{ await loadAll()"),
].join("\n");

function pendingResponse() {
  let resolve;
  const response = new Promise(done => { resolve = done; });
  return {response, resolve};
}

function board(api) {
  let poll;
  const clears = [];
  const notifications = [];
  const S = {mode:"local", notes:{}, papers:{}, lib:{}, job:{running:"daily"}};
  const context = vm.createContext({
    S, api, pending:{}, noteVersions:new Map(), feedbackVersions:new Map(), feedbackWrites:new Map(), showJob() {}, render() {}, t: key => key,
    toast: message => notifications.push(message), needsSetup: () => true,
    setInterval: tick => { assert.equal(poll, undefined); poll = tick; return 1; },
    clearInterval: id => clears.push(id),
  });
  vm.runInContext(script + "\nwatchJob()", context);
  return {S, clears, notifications, poll,
    startWeekly: () => vm.runInContext('runJob("weekly", {})', context)};
}

const idle = {running:null, last:{ok:true, message:"日报"}};
const weekly = {running:"weekly"};

test("旧的空闲状态不会覆盖用户刚开始的生成任务", async () => {
  const oldStatus = pendingResponse();
  const dashboard = board(path => {
    if (path === "/api/job") return oldStatus.response;
    assert.equal(path, "/api/run/weekly");
    return weekly;
  });
  const oldPoll = dashboard.poll();
  await dashboard.startWeekly();
  oldStatus.resolve(idle);
  await oldPoll;
  assert.equal(dashboard.S.job, weekly);
  assert.deepEqual(dashboard.clears, []);
});

test("旧的刷新快照不会覆盖新任务或停止轮询", async () => {
  const oldBootstrap = pendingResponse();
  const bootstrapStarted = pendingResponse();
  const dashboard = board(path => {
    if (path === "/api/job") return idle;
    if (path === "/api/bootstrap") {
      bootstrapStarted.resolve();
      return oldBootstrap.response;
    }
    assert.equal(path, "/api/run/weekly");
    return weekly;
  });
  const oldPoll = dashboard.poll();
  await bootstrapStarted.response;
  await dashboard.startWeekly();
  oldBootstrap.resolve({mode:"local", job:idle, notes:{}});
  await oldPoll;
  assert.equal(dashboard.S.job, weekly);
  assert.deepEqual(dashboard.clears, []);
});

test("完成后的第一次刷新失败时继续轮询并保留完成提示", async () => {
  let refreshes = 0;
  const dashboard = board(path => {
    if (path === "/api/job") return idle;
    assert.equal(path, "/api/bootstrap");
    if (++refreshes === 1) throw new Error("offline");
    return {mode:"local", job:idle, notes:{}};
  });
  await dashboard.poll();
  assert.deepEqual(dashboard.clears, []);
  assert.deepEqual(dashboard.notifications, []);
  await dashboard.poll();
  assert.equal(refreshes, 2);
  assert.deepEqual(dashboard.clears, [1]);
  assert.deepEqual(dashboard.notifications, ["生成好了：日报"]);
});
