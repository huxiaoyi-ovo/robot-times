// 运行页面内的渲染逻辑，跳过会请求在线服务的启动流程。
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { Script } from "node:vm";

const html = readFileSync(new URL("../public/index.html", import.meta.url), "utf8");
const inline = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const startup = "/* ---------- 启动 ---------- */";
assert.ok(inline.includes(startup));
const script = new Script(
  inline.split(startup)[0] + "globalThis.views={S,viewToday,viewWeek};})();",
  { filename: "index.html" },
);

function dashboard(language = "en-US") {
  const context = {
    location: { hash: "#today" },
    navigator: { language },
    document: { querySelector() {}, addEventListener() {} },
    window: { addEventListener() {} },
    addEventListener() {},
    IntersectionObserver: class { observe() {} },
  };
  script.runInNewContext(context);
  return context.views;
}

test("今日保留未排入计时计划的推荐，并可在阅读器里翻到", () => {
  const { S, viewToday } = dashboard();
  S.papers = {
    deep: { id: "deep", title: "Close read", tier: "deep" },
    scan: { id: "scan", title: "Skim this", tier: "scan" },
  };
  S.days = [{
    date: "2026-11-20",
    picks: ["deep", "scan"],
    plan: [
      { mode: "deep", minutes: 10, paperIds: ["deep"] },
      { mode: "note", minutes: 5, paperIds: ["deep"] },
    ],
  }];

  const shortened = viewToday();
  assert.match(shortened, /More recommendations/);
  assert.match(shortened, /data-open="scan"/);
  assert.doesNotMatch(shortened, /skim titles, \d+ min/);
  assert.match(shortened, /15 min, read 0 \/ 1/);
  assert.deepEqual(Array.from(S.order), ["deep", "scan"]);

  S.days[0].plan.push({ mode: "scan", minutes: 5, paperIds: ["scan"] });
  const scheduled = viewToday();
  assert.doesNotMatch(scheduled, /More recommendations/);
  assert.equal((scheduled.match(/data-open="scan"/g) || []).length, 1);
  assert.match(scheduled, /skim titles, 5 min/);
});

test("周报摘要采用该期工作日的真实扫读和笔记时长", () => {
  for (const [language, expected] of [
    ["en-US", /keep 5 minutes for the day's new picks and 5 for notes/],
    ["zh-CN", /留 5 分钟扫当天的新推荐、5 分钟写笔记/],
  ]) {
    const { S, viewWeek } = dashboard(language);
    const items = [
      { mode: "scan", minutes: 5, paperIds: [] },
      { mode: "deep", minutes: 5, paperIds: [] },
      { mode: "note", minutes: 5, paperIds: [] },
    ];
    S.weeks = [{ week: "2026-W47", nextWeek: {
      range: "15 minutes",
      plan: [{ day: "Mon 11/23", items }, { day: "Tue 11/24", items }],
    } }];
    const weekly = viewWeek();
    assert.match(weekly, expected);
    assert.doesNotMatch(weekly, /10 minutes|10 分钟/);

    S.weeks[0].nextWeek.plan[1].items = [
      { mode: "scan", minutes: 7, paperIds: [] },
      { mode: "deep", minutes: 5, paperIds: [] },
      { mode: "note", minutes: 3, paperIds: [] },
    ];
    const variable = viewWeek();
    assert.doesNotMatch(variable, expected);
    assert.match(variable, /class="m">7 (?:分钟|min)/);
    assert.match(variable, /class="m">3 (?:分钟|min)/);
  }
});
