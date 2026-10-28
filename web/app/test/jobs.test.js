// 自动生成：今天该有的还没有就触发 GitHub 上的任务；看板上的进度。用 node 自带的 sqlite 模拟 D1，假的 fetch 模拟 GitHub。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import worker, { autoRun, dueJobs, isoWeek, weeklyTarget } from "../src/index.js";

function fakeD1() {
  const db = new DatabaseSync(":memory:");
  for (const f of readdirSync(new URL("../migrations/", import.meta.url)).sort()) db.exec(readFileSync(new URL(`../migrations/${f}`, import.meta.url), "utf8"));
  const stmt = (sql, args = []) => ({
    bind: (...a) => stmt(sql, a),
    first: async () => db.prepare(sql).get(...args) ?? null,
    all: async () => ({ results: db.prepare(sql).all(...args) }),
    run: async () => db.prepare(sql).run(...args),
  });
  return { db, prepare: (sql) => stmt(sql), batch: async (list) => { for (const s of list) await s.run(); } };
}

const day = (DB, date, generatedAt) => DB.db.prepare("insert or replace into days (date, data) values (?, ?)").run(date, JSON.stringify({ date, picks: [], generatedAt }));
const BJ = (s) => new Date(`${s}+08:00`); // 北京时间

// 假 GitHub：记下触发了什么，runs 是当前的任务列表
function fakeGitHub(runs = []) {
  const gh = { runs, dispatched: [] };
  globalThis.fetch = async (url, init = {}) => {
    const u = new URL(String(url));
    assert.equal(init.headers.authorization, "Bearer T");
    if (u.pathname === "/repos/me/robot-times/actions/runs") return new Response(JSON.stringify({ workflow_runs: gh.runs }));
    const m = u.pathname.match(/^\/repos\/me\/robot-times\/actions\/workflows\/(\w+)\.yml\/dispatches$/);
    if (m && init.method === "POST") {
      gh.dispatched.push([m[1], JSON.parse(init.body)]);
      return new Response(null, { status: 204 });
    }
    return new Response("unexpected", { status: 500 });
  };
  return gh;
}
const run = (kind, status, extra = {}) => ({ path: `.github/workflows/${kind}.yml`, status, created_at: "2026-10-27T01:40:00Z", updated_at: "2026-10-27T01:50:00Z", ...extra });

test("周和日期：ISO 周、自动写哪一周的周报", () => {
  assert.equal(isoWeek("2026-10-25"), "2026-W43");
  assert.equal(isoWeek("2026-10-26"), "2026-W44");
  assert.equal(isoWeek("2027-01-01"), "2026-W53");
  assert.equal(weeklyTarget(BJ("2026-10-27T12:00:00")), "2026-10-25"); // 周二：补上一周
  assert.equal(weeklyTarget(BJ("2026-11-01T19:00:00")), "2026-10-25"); // 周日晚 8 点前：还是上一周
  assert.equal(weeklyTarget(BJ("2026-11-01T20:30:00")), "2026-11-01");
});

test("该生成什么：工作日 9:30 后的推荐、8 点后的业界简报、缺了的周报", async () => {
  const DB = fakeD1();
  assert.deepEqual(await dueJobs({ DB }, BJ("2026-10-27T07:00:00")), []);
  assert.deepEqual(await dueJobs({ DB }, BJ("2026-10-27T09:00:00")), ["news"]); // 推荐要等到 9:30
  assert.deepEqual(await dueJobs({ DB }, BJ("2026-10-27T12:30:00")), ["daily", "news"]);
  day(DB, "2026-10-27", "2026-10-27T00:30:00Z"); // 北京时间 8:30 生成的：还是前一天的论文
  assert.deepEqual(await dueJobs({ DB }, BJ("2026-10-27T12:30:00")), ["daily", "news"]);
  day(DB, "2026-10-27", "2026-10-27T01:45:00Z");
  DB.db.prepare("insert into news (date, data) values ('2026-10-27', '{}')").run();
  assert.deepEqual(await dueJobs({ DB }, BJ("2026-10-27T12:30:00")), []);
  day(DB, "2026-10-23", "2026-10-23T01:45:00Z"); // 上周有推荐、没有周报
  assert.deepEqual(await dueJobs({ DB }, BJ("2026-10-27T12:30:00")), ["weekly"]);
  DB.db.prepare("insert into weeks (week, data) values ('2026-W43', '{}')").run();
  assert.deepEqual(await dueJobs({ DB }, BJ("2026-10-31T12:30:00")), ["news"]); // 周六不生成推荐
});

test("自动触发：缺了就触发，在跑的不重复触发，30 分钟内不再触发，一天最多 3 次", async () => {
  const DB = fakeD1();
  const env = { DB, GITHUB_TOKEN: "T", GITHUB_REPO: "me/robot-times" };
  const gh = fakeGitHub([run("news", "in_progress", { run_started_at: "2026-10-27T04:30:00Z" })]);
  const st = await autoRun(env, BJ("2026-10-27T12:30:00"));
  assert.deepEqual(gh.dispatched, [["daily", { ref: "main" }]]); // 业界简报在跑，不再触发
  assert.equal(st.running, "news");
  assert.deepEqual(st.queue, ["daily"]);

  gh.runs = [];
  DB.db.prepare("insert into news (date, data) values ('2026-10-27', '{}')").run(); // 业界简报跑完了
  assert.equal(await autoRun(env, BJ("2026-10-27T12:50:00")), null); // 推荐刚触发过
  assert.equal(gh.dispatched.length, 1);
  await autoRun(env, BJ("2026-10-27T13:10:00"));
  await autoRun(env, BJ("2026-10-27T13:50:00"));
  await autoRun(env, BJ("2026-10-27T14:30:00"));
  assert.equal(gh.dispatched.filter(([k]) => k === "daily").length, 3);

  day(DB, "2026-10-23", "2026-10-23T01:45:00Z");
  gh.dispatched = [];
  await autoRun(env, BJ("2026-10-28T12:30:00"));
  assert.deepEqual(gh.dispatched.map(([k]) => k), ["daily", "news", "weekly"]);
  assert.deepEqual(gh.dispatched[2][1], { ref: "main", inputs: { day: "2026-10-25" } });
});

test("没配 GitHub 时不触发；看板接口返回进度", async () => {
  const DB = fakeD1();
  assert.equal(await autoRun({ DB }, BJ("2026-10-27T12:30:00")), null);
  const gh = fakeGitHub([run("daily", "completed", { conclusion: "failure" })]);
  const env = { DB, DEV_NO_AUTH: "1", GITHUB_TOKEN: "T", GITHUB_REPO: "me/robot-times" };
  const job = await (await worker.fetch(new Request("https://x/api/job"), env)).json();
  assert.equal(job.running, null);
  assert.equal(job.last.ok, false);
  assert.match(job.last.message, /Actions/);
  const started = await (await worker.fetch(new Request("https://x/api/run/news", { method: "POST" }), env)).json();
  assert.equal(started.running, "news");
  assert.deepEqual(gh.dispatched, [["news", { ref: "main" }]]);
});
