// 加入 / 补全 / 核对 / 撤销 Zotero：用 node 自带的 sqlite 模拟 D1，用假的 fetch 模拟 Zotero 接口和 arXiv
import { test } from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync, readdirSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import worker, { makeZip } from "../src/index.js";
import { execFileSync } from "node:child_process";
import { writeFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

// Cloudflare Workers 的 crypto.subtle 支持 MD5，Node 不支持：测试里补上
const digest = crypto.subtle.digest.bind(crypto.subtle);
crypto.subtle.digest = async (alg, data) => (alg === "MD5" ? createHash("md5").update(data).digest().buffer : digest(alg, data));

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

const PDF = new TextEncoder().encode("%PDF-1.7 fake paper");

function fakeZotero({ pdfStatus = 200 } = {}) {
  const items = {}, calls = [], uploads = [], opts = { pdfStatus }, dav = {};
  let n = 0;
  globalThis.fetch = async (url, init = {}) => {
    const u = new URL(String(url)), m = init.method || "GET", path = u.pathname;
    calls.push(`${m} ${u.host}${path}`);
    if (u.host === "arxiv.org") return opts.pdfStatus === 200 ? new Response(PDF, { headers: { "content-type": "application/pdf" } }) : new Response("", { status: pdfStatus });
    if (u.host === "dav.test") {
      assert.equal(init.headers.authorization, `Basic ${btoa("me:pw")}`);
      if (m === "PUT") { dav[path] = new Uint8Array(await new Response(init.body).arrayBuffer()); return new Response(null, { status: 201 }); }
      if (m === "HEAD") return new Response(null, { status: dav[path] ? 200 : 404 });
    }
    if (u.host === "s3.test") { uploads.push(new Uint8Array(await new Response(init.body).arrayBuffer())); return new Response("", { status: 201 }); }
    if (path === "/users/42/collections") return new Response(JSON.stringify([{ key: "COLX", data: { name: "其他" } }, { key: "INBOX", data: { name: "Inbox" } }]));
    if (m === "POST" && path === "/users/42/items") {
      const data = JSON.parse(init.body)[0], k = `KEY${++n}`;
      items[k] = { key: k, version: 1, data: { ...data }, links: { alternate: { href: `https://www.zotero.org/me/items/${k}` } } };
      return new Response(JSON.stringify({ success: { 0: k } }));
    }
    const fm = path.match(/^\/users\/42\/items\/(\w+)\/file$/);
    if (fm) {
      const body = new URLSearchParams(init.body);
      assert.equal(init.headers["If-None-Match"], "*");
      if (body.get("upload")) { items[fm[1]].data.md5 = "done"; return new Response(null, { status: 204 }); }
      assert.equal(body.get("md5"), createHash("md5").update(PDF).digest("hex"));
      assert.equal(+body.get("filesize"), PDF.length);
      return new Response(JSON.stringify({ url: "https://s3.test/up", contentType: "multipart/form-data; boundary=x", prefix: "PRE", suffix: "SUF", uploadKey: "UK" }));
    }
    const cm = path.match(/^\/users\/42\/items\/(\w+)\/children$/);
    if (cm) return new Response(JSON.stringify(Object.values(items).filter((i) => i.data.parentItem === cm[1])));
    const key = path.split("/").pop();
    if (m === "GET") return items[key] ? new Response(JSON.stringify(items[key])) : new Response("", { status: 404 });
    if (m === "PATCH") {
      assert.equal(init.headers["If-Unmodified-Since-Version"], String(items[key].version));
      Object.assign(items[key].data, JSON.parse(init.body)); items[key].version++;
      return new Response(null, { status: 204 });
    }
    return new Response("", { status: 400 });
  };
  return { items, calls, uploads, opts, dav };
}

const env = (DB) => ({ DB, DEV_NO_AUTH: "1", ZOTERO_PDF: "zotero", ZOTERO_USER_ID: "42", ZOTERO_API_KEY: "k", ZOTERO_API_BASE: "https://zotero.test", ASSETS: { fetch: () => new Response("") } });
const call = async (e, method, path, body) => (await worker.fetch(new Request(`https://app.test${path}`, { method, body: body && JSON.stringify(body) }), e)).json();
// 测试里都设了 Zotero 文件夹 Inbox（默认不设，只加进文献库根目录）
function paper(DB, id) {
  DB.db.prepare("insert or ignore into config (key, value) values ('settings', ?)").run(JSON.stringify({ zoteroCollection: "Inbox" }));
  DB.db.prepare("insert into papers (id, data, added_on, total) values (?, ?, ?, ?)").run(id, JSON.stringify({ id, title: "T", authors: ["A B"], scores: {}, pdf: `https://arxiv.org/pdf/${id}v1` }), "2026-10-26", 7);
}

test("加入 Zotero：放进 Inbox，PDF 存成附件，再加一条摘要笔记", async () => {
  const DB = fakeD1(); const z = fakeZotero(); const e = env(DB); paper(DB, "2610.1");
  const r = await call(e, "POST", "/api/zotero/2610.1");
  assert.equal(r.folder, "Inbox");
  assert.equal(r.pdfError, null);
  const parent = z.items[r.key];
  assert.deepEqual(parent.data.collections, ["INBOX"]);
  const kids = Object.values(z.items).filter((i) => i.data.parentItem === r.key);
  const att = kids.find((i) => i.data.itemType === "attachment");
  assert.equal(att.data.linkMode, "imported_url");
  assert.equal(att.data.contentType, "application/pdf");
  assert.equal(att.data.md5, "done"); // 上传后登记过
  assert.equal(new TextDecoder().decode(z.uploads[0]), "PRE%PDF-1.7 fake paperSUF");
  assert.ok(kids.some((i) => i.data.itemType === "note"));

  const check = await call(e, "GET", "/api/zotero/2610.1");
  assert.equal(check.pdf, true);
  assert.equal(check.inFolder, true);
});

test("PDF 下载失败时条目照样加入，并说明原因；再点一次会补上 PDF 和文件夹", async () => {
  const DB = fakeD1(); const e = env(DB); paper(DB, "2610.2");
  const z = fakeZotero({ pdfStatus: 403 });
  const r = await call(e, "POST", "/api/zotero/2610.2");
  assert.match(r.pdfError, /403/);
  z.items[r.key].data.collections = []; // 模拟以前加的、不在文件夹里的条目
  const check = await call(e, "GET", "/api/zotero/2610.2");
  assert.equal(check.pdf, false);
  assert.equal(check.inFolder, false);

  z.opts.pdfStatus = 200;
  const fixed = await call(e, "POST", "/api/zotero/2610.2");
  assert.equal(fixed.existing, true);
  assert.equal(fixed.pdfError, null);
  assert.deepEqual(z.items[r.key].data.collections, ["INBOX"]);
  assert.equal((await call(e, "GET", "/api/zotero/2610.2")).pdf, true);
});

test("找不到文件夹时直接报错，不写入；设置里留空就放在我的文库", async () => {
  const DB = fakeD1(); const z = fakeZotero(); const e = env(DB); paper(DB, "2610.3");
  await call(e, "PUT", "/api/config", { zoteroCollection: "不存在" });
  const bad = await worker.fetch(new Request("https://app.test/api/zotero/2610.3", { method: "POST" }), e);
  assert.equal(bad.status, 400);
  assert.match((await bad.json()).error, /找不到名为“不存在”的文件夹/);
  assert.equal(Object.keys(z.items).length, 0);
  await call(e, "PUT", "/api/config", { zoteroCollection: "" });
  const r = await call(e, "POST", "/api/zotero/2610.3");
  assert.deepEqual(z.items[r.key].data.collections, []);
});

test("撤销进回收站；在 Zotero 里删掉的条目核对时清掉本地记录", async () => {
  const DB = fakeD1(); const z = fakeZotero(); const e = env(DB); paper(DB, "2610.4");
  const r = await call(e, "POST", "/api/zotero/2610.4");
  assert.equal((await call(e, "DELETE", "/api/zotero/2610.4")).key, null);
  assert.equal(z.items[r.key].data.deleted, 1);
  DB.db.prepare("insert into zotero_items (paper_id, item_key, at) values ('2610.4', 'GONE', 'x')").run();
  const check = await call(e, "GET", "/api/zotero/2610.4");
  assert.equal(check.gone, true);
});

test("附件条目在但文件没传上去（Zotero 里显示找不到文件）：补全时沿用这个附件把文件传上去", async () => {
  const DB = fakeD1(); const z = fakeZotero(); const e = env(DB); paper(DB, "2610.5");
  const r = await call(e, "POST", "/api/zotero/2610.5");
  const att = Object.values(z.items).find((i) => i.data.parentItem === r.key && i.data.itemType === "attachment");
  delete att.data.md5; // 模拟上传没成功
  const check = await call(e, "GET", "/api/zotero/2610.5");
  assert.equal(check.pdf, false);
  const n = Object.keys(z.items).length;
  const fixed = await call(e, "POST", "/api/zotero/2610.5");
  assert.equal(fixed.pdfError, null);
  assert.equal(Object.keys(z.items).length, n); // 没有再建一个附件
  assert.equal(att.data.md5, "done");
  const dbg = JSON.parse(DB.db.prepare("select value from config where key = 'zotero_debug'").get().value);
  assert.equal(dbg.result, "ok");
  assert.ok(dbg.steps.some((x) => x.includes("沿用")));
});

test("用 WebDAV 同步附件时：PDF 按 Zotero 的格式写进 WebDAV（zotero/<附件>.zip 和 .prop），不传 Zotero 存储", async () => {
  const DB = fakeD1(); const z = fakeZotero();
  const e = { ...env(DB), WEBDAV_URL: "https://dav.test/dav/", WEBDAV_USER: "me", WEBDAV_PASSWORD: "pw" };
  paper(DB, "2610.6");
  const r = await call(e, "POST", "/api/zotero/2610.6");
  assert.equal(r.pdfError, null);
  const att = Object.values(z.items).find((i) => i.data.parentItem === r.key && i.data.itemType === "attachment");
  const md5 = createHash("md5").update(PDF).digest("hex");
  assert.equal(att.data.md5, md5);
  assert.ok(att.data.mtime > 0);
  assert.equal(z.uploads.length, 0); // 没有用 Zotero 自带存储
  const prop = new TextDecoder().decode(z.dav[`/dav/zotero/${att.key}.prop`]);
  assert.match(prop, new RegExp(`<hash>${md5}</hash>`));
  // zip 能被标准工具解开，里面就是这份 PDF
  const dir = mkdtempSync(join(tmpdir(), "zip-"));
  writeFileSync(join(dir, "a.zip"), z.dav[`/dav/zotero/${att.key}.zip`]);
  const inside = execFileSync("python3", ["-c", "import zipfile,sys;z=zipfile.ZipFile(sys.argv[1]);print(z.namelist()[0]);sys.stdout.write(z.read(z.namelist()[0]).decode())", join(dir, "a.zip")]).toString();
  assert.equal(inside, "2610.6.pdf\n%PDF-1.7 fake paper");
  assert.equal((await call(e, "GET", "/api/zotero/2610.6")).pdf, true);
});

test("makeZip：中文文件名也能正常解开", () => {
  const zip = makeZip("论文.pdf", new TextEncoder().encode("abc"));
  const dir = mkdtempSync(join(tmpdir(), "zip-"));
  writeFileSync(join(dir, "b.zip"), zip);
  const out = execFileSync("python3", ["-c", "import zipfile,sys;z=zipfile.ZipFile(sys.argv[1]);assert z.testzip() is None;print(z.namelist()[0], z.read(z.namelist()[0]).decode())", join(dir, "b.zip")]).toString().trim();
  assert.equal(out, "论文.pdf abc");
});

test("没配 WebDAV、也没开 Zotero 存储时不加 PDF（避免电脑上出现找不到文件的空附件）", async () => {
  const DB = fakeD1(); const z = fakeZotero(); const e = { ...env(DB), ZOTERO_PDF: "" }; paper(DB, "2610.7");
  const r = await call(e, "POST", "/api/zotero/2610.7");
  assert.equal(r.pdf, false);
  assert.ok(!z.calls.some((c) => c.includes("arxiv.org")));
  const kids = Object.values(z.items).filter((i) => i.data.parentItem === r.key);
  assert.deepEqual(kids.map((i) => i.data.itemType), ["note"]);
  assert.deepEqual(z.items[r.key].data.collections, ["INBOX"]);
  assert.equal((await call(e, "GET", "/api/zotero/2610.7")).pdf, null);
});
