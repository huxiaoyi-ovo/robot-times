// 看板 Worker 的离线测试：登录校验（Cloudflare Access JWT）和 Zotero 条目格式。
// 运行：cd web/app && npm test
import assert from "node:assert/strict";
import { test } from "node:test";
import worker, { noteHtml, paperToItem, refreshFeedCache } from "../src/index.js";

const TEAM = "team.cloudflareaccess.com";
const AUD = "aud-123";
const b64url = (buf) => Buffer.from(buf).toString("base64url");

const { publicKey, privateKey } = await crypto.subtle.generateKey(
  { name: "RSASSA-PKCS1-v1_5", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" },
  true,
  ["sign", "verify"],
);
const jwk = { ...(await crypto.subtle.exportKey("jwk", publicKey)), kid: "k1", alg: "RS256" };

async function token(claims, kid = "k1") {
  const head = b64url(JSON.stringify({ alg: "RS256", kid, typ: "JWT" }));
  const body = b64url(JSON.stringify({ aud: [AUD], iss: `https://${TEAM}`, exp: Date.now() / 1000 + 600, email: "me@example.com", ...claims }));
  const sig = await crypto.subtle.sign("RSASSA-PKCS1-v1_5", privateKey, new TextEncoder().encode(`${head}.${body}`));
  return `${head}.${body}.${b64url(sig)}`;
}

globalThis.fetch = async (url) => {
  assert.equal(String(url), `https://${TEAM}/cdn-cgi/access/certs`);
  return new Response(JSON.stringify({ keys: [jwk] }));
};

const db = {
  prepare: () => ({
    bind: () => ({ first: async () => null, all: async () => ({ results: [] }), run: async () => ({}) }),
    first: async () => null,
  }),
};
const env = { ACCESS_TEAM_DOMAIN: TEAM, ACCESS_AUD: AUD, ALLOWED_EMAIL: "Me@Example.com", DB: db };
const call = (headers = {}, e = env) => worker.fetch(new Request("https://x/api/config", { headers }), e);

test("不填 ACCESS_TEAM_DOMAIN 也能用；填错了会提示正确的值", async () => {
  const { ACCESS_TEAM_DOMAIN, ...rest } = env;
  assert.equal((await call({ "cf-access-jwt-assertion": await token({}) }, rest)).status, 200);
  const r = await call({ "cf-access-jwt-assertion": await token({}) }, { ...env, ACCESS_TEAM_DOMAIN: "wrong.cloudflareaccess.com" });
  assert.equal(r.status, 403);
  assert.match((await r.json()).error, /team\.cloudflareaccess\.com/);
  const full = { ...env, ACCESS_TEAM_DOMAIN: `https://${TEAM}/cdn-cgi/access/certs` };
  assert.equal((await call({ "cf-access-jwt-assertion": await token({}) }, full)).status, 200);
});

test("没有开启 Access 时拒绝访问", async () => {
  const r = await call({}, { DB: db });
  assert.equal(r.status, 403);
  assert.match((await r.json()).error, /Cloudflare Access/);
});

test("有效凭证可以访问，邮箱不区分大小写", async () => {
  const r = await call({ "cf-access-jwt-assertion": await token({}) });
  assert.equal(r.status, 200);
  assert.equal((await r.json()).tag, "Robot Times");
});

test("只有 CF_Authorization cookie 时也能通过", async () => {
  const r = await call({ cookie: `foo=1; CF_Authorization=${await token({})}; bar=2` });
  assert.equal(r.status, 200);
});

test("别人的邮箱、过期、别的应用、伪造签名都被拒绝", async () => {
  for (const t of [
    await token({ email: "other@example.com" }),
    await token({ exp: Date.now() / 1000 - 5 }),
    await token({ aud: ["other"] }),
    await token({ iss: "https://evil.cloudflareaccess.com" }),
    (await token({})).replace(/\.[^.]+$/, ".AAAA"),
    await token({}, "unknown"),
    "not-a-jwt",
  ]) {
    assert.equal((await call({ "cf-access-jwt-assertion": t })).status, 403);
  }
  assert.equal((await call({ "cf-access-authenticated-user-email": "me@example.com" })).status, 403);
  assert.equal((await call({ cookie: "CF_Authorization=" + (await token({ email: "x@y.z" })) })).status, 403);
});

test("Zotero 条目格式和 Python 版一致", () => {
  const p = { id: "2610.00001", title: "T", authors: ["Ann Lee", "Plato"], abstract: "A", date: "2026-10-24", url: "https://arxiv.org/abs/2610.00001", source: "arxiv" };
  assert.deepEqual(paperToItem(p, "Robot Times"), {
    itemType: "preprint",
    title: "T",
    creators: [
      { creatorType: "author", firstName: "Ann", lastName: "Lee" },
      { creatorType: "author", name: "Plato" },
    ],
    abstractNote: "A",
    repository: "arXiv",
    archiveID: "arXiv:2610.00001",
    date: "2026-10-24",
    url: "https://arxiv.org/abs/2610.00001",
    tags: [{ tag: "Robot Times" }],
  });
  const html = noteHtml({ ...p, summary: "<x>", scores: { total: 7.25, rel: 8 } }, "第一段\n\n第二段");
  assert.match(html, /&lt;x&gt;/);
  assert.match(html, /综合分 7\.3/);
  assert.match(html, /<h2>我的笔记<\/h2><p>第一段<\/p><p>第二段<\/p>/);
});

test("入口文件只导出函数（Workers 运行时不接受其他类型的导出）", async () => {
  const mod = await import("../src/index.js");
  for (const [name, value] of Object.entries(mod)) {
    if (name === "default") continue;
    assert.equal(typeof value, "function", `导出 ${name} 不是函数`);
  }
});

test("定时代抓被拒绝的订阅，结果写进 config", async () => {
  const writes = [];
  const fakeDb = {
    prepare: (sql) => ({
      first: async () => (sql.includes("proxy_feeds") ? { value: JSON.stringify(["https://a.substack.com/feed", "https://b.ex/feed"]) } : null),
      bind: (...args) => ({ sql, args }),
    }),
    batch: async (stmts) => writes.push(...stmts),
  };
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (url, init) => {
    assert.match(init.headers["user-agent"], /Chrome/);
    return String(url).includes("substack") ? new Response("<rss>ok</rss>") : new Response("no", { status: 403 });
  };
  try {
    const out = await refreshFeedCache({ DB: fakeDb }, { pause: 0 });
    assert.deepEqual(out.map((x) => x.status), [200, 403]);
    assert.equal(writes.length, 2);
    assert.doesNotMatch(writes[0].sql, / where /); // 抓到了：直接覆盖
    assert.match(writes[1].sql, /where json_extract\(config\.value/); // 没抓到：不覆盖旧的好内容
    assert.equal(writes[0].args[0], "feed_cache:https://a.substack.com/feed");
    assert.equal(JSON.parse(writes[0].args[1]).body, "<rss>ok</rss>");
  } finally {
    globalThis.fetch = realFetch;
  }
});

test("列表数据不带长篇解读", async () => {
  const { lite } = await import("../src/index.js");
  const p = { id: "1", title: "T", titleZh: "中文", abstractZh: "译文", analysis: { tldr: "一句话", critique: "很长" } };
  const l = lite(p);
  assert.equal(l.analysis, undefined);
  assert.equal(l.abstractZh, undefined);
  assert.equal(l.titleZh, "中文");
  assert.equal(l.hasAnalysis, true);
  assert.deepEqual(lite({ id: "2", title: "x" }), { id: "2", title: "x" });
});

test("方向预设：报名和“问 AI”的人设跟着设置走，个人化的都不写死", async () => {
  const { presetFor, styleFor } = await import("../src/presets.js");
  const { siteInfo } = await import("../src/index.js");
  assert.equal(siteInfo({}).name, "The Robot Times");
  assert.equal(siteInfo({ preset: "general" }).name, "The Preprint Times");
  assert.equal(siteInfo({ preset: "general", siteName: "The Vision Times" }).name, "The Vision Times");
  assert.ok(siteInfo({}).presets.some((p) => p.id === "general"));
  const s = styleFor(presetFor({ preset: "general", field: "计算机视觉" }));
  assert.match(s, /做计算机视觉的资深研究者/);
  assert.doesNotMatch(s, /[{}]/);
});
