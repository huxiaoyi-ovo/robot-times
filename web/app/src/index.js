// The Robot Times 看板 Worker：/api/* 走这里，其余是 public/ 里的静态页面。
//
// 数据在 D1（表结构见 migrations/0001_init.sql，和 core/ 的 Python 代码共用一套）。
// 访问控制：Cloudflare Access 挡在最前面，这里再校验一次 Access 的登录凭证（JWT）和邮箱，
// 这样即使 Access 被误关，接口也不会对外开放。
// （带静态资源的 Worker 拿不到 ctx.access，所以自己校验 Cf-Access-Jwt-Assertion。）
//
// 环境变量（Cloudflare 后台“设置 → 变量和机密”）：
//   ACCESS_TEAM_DOMAIN  可选，例如 yourteam.cloudflareaccess.com；不填时从登录凭证里读
//   ACCESS_AUD          Access 应用的 Application Audience (AUD) Tag
//   ALLOWED_EMAIL       允许登录的邮箱
//   ZOTERO_USER_ID, ZOTERO_API_KEY   写入 Zotero 用（机密）
//   DEEPSEEK_API_KEY    “问 AI”用（机密，可选）
//   GITHUB_TOKEN        “立即生成”按钮用，只需本仓库 Actions 写权限（机密，可选）
//   DEV_NO_AUTH=1       只在本地 wrangler dev 时用（写在 .dev.vars），线上不要设

import { DEFAULT_PRESET, PRESETS, presetFor, styleFor } from "./presets.js";

const JSON_HEADERS = { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" };
const STATUSES = new Set(["todo", "reading", "done", "skip"]);
const CHUNK = 90; // D1 每条语句最多 100 个参数

class HttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

const json = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: JSON_HEADERS });
const nowIso = () => new Date().toISOString().replace(/\.\d+Z$/, "Z");

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (!url.pathname.startsWith("/api/")) return env.ASSETS.fetch(request);
    try {
      const who = await authorize(request, env);
      return await route(request, env, url, who);
    } catch (e) {
      if (e instanceof HttpError) return json({ error: e.message }, e.status);
      console.error(e);
      return json({ error: "服务器出错了，稍后再试。" }, 500);
    }
  },
  // 定时任务（每 30 分钟）：每 3 小时代抓一次拒绝 GitHub 服务器的订阅（如 *.substack.com），存进 D1 给业界简报用；
  // 每次都检查今天的推荐、业界简报、周报，缺了就触发 GitHub 上的任务
  async scheduled(event, env, ctx) {
    const t = new Date(event.scheduledTime || Date.now());
    ctx.waitUntil(
      (async () => {
        if (t.getUTCHours() % 3 === 0 && t.getUTCMinutes() < 30) await refreshFeedCache(env).catch((e) => console.error("feeds", e));
        await autoRun(env, t).catch((e) => console.error("autoRun", e));
      })(),
    );
  },
};

const BROWSER_UA =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36";

export async function refreshFeedCache(env, { pause = 2000 } = {}) {
  const row = await env.DB.prepare("select value from config where key = 'proxy_feeds'").first();
  const urls = (row ? JSON.parse(row.value) : []).filter((u) => /^https?:\/\//.test(u)).slice(0, 20);
  // 一个一个抓，中间停一下：Substack 同时收到几个请求会回 429
  const results = [];
  for (const [i, url] of urls.entries()) {
    if (i && pause) await new Promise((r) => setTimeout(r, pause));
    try {
      const r = await fetch(url, { headers: { "user-agent": BROWSER_UA, accept: "application/rss+xml, application/xml;q=0.9, */*;q=0.8" } });
      const body = r.ok ? (await r.text()).slice(0, 900_000) : "";
      results.push({ url, status: r.status, body });
    } catch (e) {
      results.push({ url, status: 0, body: "", error: String(e).slice(0, 200) });
    }
  }
  const at = nowIso();
  const upsert = "insert into config (key, value) values (?, ?) on conflict(key) do update set value = excluded.value";
  // 抓失败时不覆盖上一次抓到的内容
  const keepGood = `${upsert} where json_extract(config.value, '$.status') != 200`;
  if (results.length) {
    await env.DB.batch(
      results.map((x) =>
        env.DB.prepare(x.status === 200 ? upsert : keepGood).bind(
          `feed_cache:${x.url}`,
          JSON.stringify({ at, status: x.status, body: x.body, error: x.error }),
        ),
      ),
    );
  }
  return results.map(({ url, status }) => ({ url, status }));
}

// ---------------- 登录校验 ----------------

let jwksCache = { at: 0, keys: [] };

function b64urlBytes(s) {
  const b = atob(s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4));
  return Uint8Array.from(b, (c) => c.charCodeAt(0));
}

async function accessKeys(team, force = false) {
  if (!force && Date.now() - jwksCache.at < 3600_000 && jwksCache.keys.length) return jwksCache.keys;
  const resp = await fetch(`https://${team}/cdn-cgi/access/certs`);
  if (!resp.ok) throw new HttpError(503, `暂时拿不到 Cloudflare Access 的公钥（${team} 返回 HTTP ${resp.status}），稍后再试。`);
  jwksCache = { at: Date.now(), keys: (await resp.json()).keys || [] };
  return jwksCache.keys;
}

const TEAM_RE = /^[a-z0-9-]+\.cloudflareaccess\.com$/;
const hostOf = (v) => String(v || "").trim().toLowerCase().replace(/^https?:\/\//, "").replace(/\/.*$/, "");

async function authorize(request, env) {
  if (env.DEV_NO_AUTH === "1") return { email: "dev@localhost" };
  if (!env.ACCESS_AUD || !env.ALLOWED_EMAIL) {
    throw new HttpError(403, "看板还没开启登录保护：请按 docs/dev/cloud-setup.md 开启 Cloudflare Access，并设置 ACCESS_AUD 和 ALLOWED_EMAIL。");
  }
  // Access 通常把凭证放在请求头里；有时只有浏览器带上的 CF_Authorization cookie，两者内容相同，一样校验签名
  const cookie = (request.headers.get("cookie") || "").match(/(?:^|;\s*)CF_Authorization=([^;]+)/);
  const token = request.headers.get("cf-access-jwt-assertion") || (cookie ? decodeURIComponent(cookie[1]) : "");
  if (!token) {
    throw new HttpError(
      403,
      "没有登录凭证：看起来 Cloudflare Access 还没生效。正常情况下，打开看板时会先跳到 Cloudflare 的登录页。" +
        "请到 Cloudflare 里看板的 Worker → Access 确认范围是 All traffic，然后关掉页面重新打开。",
    );
  }
  const parts = token.split(".");
  const invalid = () => new HttpError(403, "登录凭证无效，请刷新页面重新登录。");
  if (parts.length !== 3) throw invalid();
  const dec = (s) => JSON.parse(new TextDecoder().decode(b64urlBytes(s)));
  let header, payload;
  try {
    header = dec(parts[0]);
    payload = dec(parts[1]);
  } catch {
    throw invalid();
  }
  // 团队域名从凭证的签发方读出，只认 *.cloudflareaccess.com；真正防伪靠下面的签名和 AUD 校验
  const team = hostOf(payload.iss);
  if (!TEAM_RE.test(team) || payload.iss !== `https://${team}`) throw invalid();
  const configured = hostOf(env.ACCESS_TEAM_DOMAIN);
  if (configured && configured !== team) {
    throw new HttpError(403, `ACCESS_TEAM_DOMAIN 填的是 ${configured}，但你的团队域名是 ${team}。改成后者，或者直接删掉这个变量。`);
  }
  let jwk = (await accessKeys(team)).find((k) => k.kid === header.kid);
  if (!jwk) jwk = (await accessKeys(team, true)).find((k) => k.kid === header.kid); // 公钥轮换
  if (!jwk || header.alg !== "RS256") throw invalid();
  const key = await crypto.subtle.importKey("jwk", jwk, { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["verify"]);
  const ok = await crypto.subtle.verify(
    "RSASSA-PKCS1-v1_5",
    key,
    b64urlBytes(parts[2]),
    new TextEncoder().encode(`${parts[0]}.${parts[1]}`),
  );
  if (!ok) throw invalid();
  const aud = Array.isArray(payload.aud) ? payload.aud : [payload.aud];
  if (!aud.includes(String(env.ACCESS_AUD).trim())) {
    throw new HttpError(403, "ACCESS_AUD 和这个 Access 应用对不上：到 Cloudflare 里看板的 Worker → Access 页面右下角复制 AUD tag，重新填一次。");
  }
  if (!(payload.exp > Date.now() / 1000)) throw new HttpError(403, "登录已过期，请刷新页面重新登录。");
  const email = String(payload.email || "").toLowerCase();
  if (email !== String(env.ALLOWED_EMAIL).trim().toLowerCase()) {
    throw new HttpError(403, `${email || "这个账号"} 没有访问权限（ALLOWED_EMAIL 填的是别的邮箱）。`);
  }
  return { email };
}

// ---------------- 路由 ----------------

async function route(request, env, url, who) {
  const m = request.method;
  const p = url.pathname.replace(/\/+$/, "");
  let g;
  if (m === "GET" && p === "/api/bootstrap") return json(await bootstrap(env, who));
  if (m === "GET" && p === "/api/papers") return json(await searchPapers(env, url.searchParams));
  if (m === "GET" && (g = p.match(/^\/api\/paper\/([^/]+)$/))) return json(await getPaper(env, decodeURIComponent(g[1])));
  if (m === "PUT" && (g = p.match(/^\/api\/notes\/([^/]+)$/))) return json(await saveNote(env, decodeURIComponent(g[1]), await body(request)));
  if (m === "POST" && (g = p.match(/^\/api\/feedback\/([^/]+)$/))) return json(await feedback(env, decodeURIComponent(g[1]), await body(request)));
  if (m === "POST" && (g = p.match(/^\/api\/zotero\/([^/]+)$/))) return json(await addToZotero(env, decodeURIComponent(g[1])));
  if (m === "GET" && (g = p.match(/^\/api\/zotero\/([^/]+)$/))) return json(await checkZotero(env, decodeURIComponent(g[1])));
  if (m === "DELETE" && (g = p.match(/^\/api\/zotero\/([^/]+)$/))) return json(await removeFromZotero(env, decodeURIComponent(g[1])));
  if (m === "GET" && (g = p.match(/^\/api\/news\/(\d{4}-\d{2}-\d{2})$/))) return json(await getNews(env, g[1]));
  if (m === "GET" && p === "/api/config") return json(await loadSettings(env));
  if (m === "PUT" && p === "/api/config") return json(await saveSettings(env, await body(request)));
  if (m === "POST" && p === "/api/ask") return ask(env, await body(request));
  if (m === "POST" && (g = p.match(/^\/api\/run\/(daily|weekly|news)$/))) return json(await runJob(env, g[1]));
  if (m === "GET" && p === "/api/job") return json(await jobStatus(env));
  throw new HttpError(404, "没有这个接口。");
}

async function body(request) {
  const text = await request.text();
  if (text.length > 200_000) throw new HttpError(413, "内容太长了。");
  try {
    return text ? JSON.parse(text) : {};
  } catch {
    throw new HttpError(400, "请求格式不对。");
  }
}

// ---------------- 数据库 ----------------

// 和研究方向有关的（报名、写作人设、默认 arXiv 分类、新闻来源）在方向预设里，见 presets.js
const DEFAULT_SETTINGS = {
  preset: DEFAULT_PRESET,
  siteName: "",
  field: "",
  weatherCity: "",
  interests: "",
  weights: { relevance: 0.5, impact: 0.3, buzz: 0.2 },
  dailyMinutes: 60,
  top: 12,
  tag: "Robot Times",
  zoteroCollection: "",
  diversity: 0.4,
};

async function all(env, sql, ...params) {
  return (await env.DB.prepare(sql).bind(...params).all()).results || [];
}

async function papersByIds(env, ids) {
  const out = {};
  const uniq = [...new Set(ids)].filter(Boolean);
  for (let i = 0; i < uniq.length; i += CHUNK) {
    const part = uniq.slice(i, i + CHUNK);
    const rows = await all(env, `select data from papers where id in (${part.map(() => "?").join(",")})`, ...part);
    for (const r of rows) {
      const d = lite(JSON.parse(r.data));
      out[d.id] = d;
    }
  }
  return out;
}

// 列表里不带长篇解读和摘要译文（打开一篇时再用 /api/paper/:id 取全文），首屏数据小很多
export function lite(p) {
  if (!p.analysis && !p.abstractZh) return p;
  const { analysis, abstractZh, ...rest } = p;
  return { ...rest, hasAnalysis: Boolean(analysis), cover: analysis?.cover || null };
}

const noteRow = (r) => ({ paperId: r.paper_id, status: r.status, note: r.note, flags: JSON.parse(r.flags || "[]"), updatedAt: r.updated_at });

function idsInPlan(plan) {
  return (plan || []).flatMap((x) => x.paperIds || (x.items ? idsInPlan(x.items) : []));
}

async function bootstrap(env, who) {
  const [job, settings, dayRows, weekRows, noteRows, zRows, fbRows, newsRows, newsDates, weatherRow] = await Promise.all([
    // 今天的还没生成就当场触发，看板上显示等待提示（失败不影响打开看板）
    autoRun(env).catch((e) => (console.error("autoRun", e), null)),
    loadSettings(env),
    all(env, "select data from days order by date desc limit 30"),
    all(env, "select data from weeks order by week desc limit 12"),
    all(env, "select * from notes"),
    all(env, "select paper_id, item_key from zotero_items"),
    all(env, "select paper_id, action from feedback where action in ('like', 'skip') order by id"),
    all(env, "select data from news order by date desc limit 1").catch(() => []),
    all(env, "select date from news order by date desc limit 30").catch(() => []),
    env.DB.prepare("select value from config where key = 'weather'").first().catch(() => null),
  ]);
  const days = dayRows.map((r) => JSON.parse(r.data));
  const weeks = weekRows.map((r) => JSON.parse(r.data));
  const notes = Object.fromEntries(noteRows.map((r) => [r.paper_id, noteRow(r)]));
  const ids = [
    ...days.flatMap((d) => [...(d.picks || []), ...idsInPlan(d.plan)]),
    ...weeks.flatMap((w) => [...(w.picks || []), ...(w.trends || []).flatMap((t) => t.paperIds || []), ...idsInPlan(w.nextWeek?.plan)]),
    ...Object.keys(notes),
  ];
  const feedbackMap = {};
  for (const r of fbRows) feedbackMap[r.paper_id] = r.action;
  return {
    me: who.email,
    settings,
    days,
    weeks,
    notes,
    papers: await papersByIds(env, ids),
    zotero: Object.fromEntries(zRows.map((r) => [r.paper_id, r.item_key])),
    feedback: feedbackMap,
    news: newsRows[0] ? JSON.parse(newsRows[0].data) : null,
    newsDates: newsDates.map((r) => r.date),
    weather: weatherRow ? JSON.parse(weatherRow.value) : null,
    site: siteInfo(settings),
    mode: "cloud",
    job,
    features: {
      zotero: Boolean(env.ZOTERO_USER_ID && env.ZOTERO_API_KEY),
      ask: Boolean(env.DEEPSEEK_API_KEY),
      run: Boolean(env.GITHUB_TOKEN),
    },
  };
}

async function searchPapers(env, q) {
  const text = (q.get("q") || "").trim().slice(0, 100);
  const status = q.get("status") || "";
  if (status && !STATUSES.has(status)) throw new HttpError(400, "状态不对。");
  const limit = Math.min(100, Math.max(1, Number(q.get("limit")) || 50));
  const offset = Math.max(0, Number(q.get("offset")) || 0);
  const like = `%${text.replace(/[\\%_]/g, (c) => "\\" + c)}%`;
  const where = "(?1 = '' or p.data like ?2 escape '\\') and (?3 = '' or coalesce(n.status, 'todo') = ?3)";
  const [rows, count] = await Promise.all([
    all(env, `select p.data from papers p left join notes n on n.paper_id = p.id where ${where} order by p.added_on desc, p.total desc limit ?4 offset ?5`, text, like, status, limit, offset),
    all(env, `select count(*) as n from papers p left join notes n on n.paper_id = p.id where ${where}`, text, like, status),
  ]);
  return { total: count[0]?.n || 0, papers: rows.map((r) => lite(JSON.parse(r.data))) };
}

async function getNews(env, date) {
  const r = await env.DB.prepare("select data from news where date = ?").bind(date).first();
  if (!r) throw new HttpError(404, "这一天没有业界简报。");
  return JSON.parse(r.data);
}

async function getPaper(env, id) {
  const r = await env.DB.prepare("select data from papers where id = ?").bind(id).first();
  if (!r) throw new HttpError(404, "找不到这篇论文。");
  return JSON.parse(r.data);
}

async function saveNote(env, id, b) {
  await getPaper(env, id);
  const status = b.status ?? "todo";
  if (!STATUSES.has(status)) throw new HttpError(400, "阅读状态不对。");
  const note = String(b.note ?? "");
  if (note.length > 50_000) throw new HttpError(413, "笔记太长了，拆成几段吧。");
  const flags = Array.isArray(b.flags) ? b.flags.map(String).slice(0, 12) : [];
  const at = nowIso();
  await env.DB.prepare(
    "insert into notes (paper_id, status, note, flags, updated_at) values (?, ?, ?, ?, ?) " +
      "on conflict(paper_id) do update set status = excluded.status, note = excluded.note, flags = excluded.flags, updated_at = excluded.updated_at",
  )
    .bind(id, status, note, JSON.stringify(flags), at)
    .run();
  return { paperId: id, status, note, flags, updatedAt: at };
}

async function feedback(env, id, b) {
  await getPaper(env, id);
  const action = b.action;
  if (!["like", "skip", "clear"].includes(action)) throw new HttpError(400, "反馈类型不对。");
  // 每篇只保留最后一次喜欢/跳过；“加入 Zotero”单独记
  const stmts = [env.DB.prepare("delete from feedback where paper_id = ? and action in ('like', 'skip')").bind(id)];
  if (action !== "clear") stmts.push(env.DB.prepare("insert into feedback (paper_id, action, at) values (?, ?, ?)").bind(id, action, nowIso()));
  await env.DB.batch(stmts);
  return { paperId: id, action: action === "clear" ? null : action };
}

async function loadSettings(env) {
  const r = await env.DB.prepare("select value from config where key = 'settings'").first();
  const s = { ...DEFAULT_SETTINGS, ...(r ? JSON.parse(r.value) : {}) };
  if (!s.categories?.length) s.categories = [...presetFor(s).categories];
  return s;
}

// 看板要的报名和方向列表（设置里选方向用）
export function siteInfo(settings) {
  const p = presetFor(settings);
  return {
    name: p.site_name,
    field: p.field,
    presets: Object.entries(PRESETS).map(([id, x]) => ({ id, label: x.label, siteName: x.site_name, field: x.field })),
  };
}

async function saveSettings(env, b) {
  const s = await loadSettings(env);
  if (b.categories !== undefined) {
    const cats = (Array.isArray(b.categories) ? b.categories : []).map((c) => String(c).trim()).filter(Boolean);
    if (!cats.length || cats.length > 12 || !cats.every((c) => /^[a-z-]+(\.[A-Za-z-]+)?$/.test(c))) {
      throw new HttpError(400, "arXiv 分类写法不对，例如 cs.RO、cs.LG、eess.SY。");
    }
    s.categories = cats;
  }
  if (b.interests !== undefined) s.interests = String(b.interests).slice(0, 2000);
  if (b.weights !== undefined) {
    const w = {};
    for (const k of ["relevance", "impact", "buzz"]) {
      const v = Number(b.weights[k]);
      if (!(v >= 0 && v <= 1)) throw new HttpError(400, "权重要在 0 到 1 之间。");
      w[k] = Math.round(v * 100) / 100;
    }
    if (w.relevance + w.impact + w.buzz <= 0) throw new HttpError(400, "三个权重不能都是 0。");
    s.weights = w;
  }
  if (b.dailyMinutes !== undefined) s.dailyMinutes = Math.max(15, Math.min(240, Math.round(Number(b.dailyMinutes) || 60)));
  if (b.top !== undefined) s.top = Math.max(3, Math.min(30, Math.round(Number(b.top) || 12)));
  if (b.diversity !== undefined) {
    const v = Number(b.diversity);
    if (!(v >= 0 && v <= 1)) throw new HttpError(400, "多样性要在 0 到 1 之间。");
    s.diversity = Math.round(v * 100) / 100;
  }
  if (b.preset !== undefined) {
    if (!PRESETS[b.preset]) throw new HttpError(400, "没有这个方向预设。");
    s.preset = b.preset;
  }
  if (b.siteName !== undefined) s.siteName = String(b.siteName).trim().slice(0, 60);
  if (b.field !== undefined) s.field = String(b.field).trim().slice(0, 40);
  if (b.weatherCity !== undefined) s.weatherCity = String(b.weatherCity).trim().slice(0, 60);
  if (b.zoteroCollection !== undefined) s.zoteroCollection = String(b.zoteroCollection).trim().slice(0, 80);
  if (b.tag !== undefined) s.tag = String(b.tag).trim().slice(0, 40) || "Robot Times";
  if (b.newsFeeds !== undefined) {
    const feeds = (Array.isArray(b.newsFeeds) ? b.newsFeeds : []).map((u) => String(u).trim()).filter(Boolean);
    if (feeds.length > 30) throw new HttpError(400, "订阅最多 30 个。");
    const bad = feeds.find((u) => !/^https?:\/\/[^\s/]+\.[^\s]+$/.test(u));
    if (bad) throw new HttpError(400, `这个不像 RSS 地址：${bad}`);
    s.newsFeeds = feeds;
  }
  await env.DB.prepare("insert into config (key, value) values ('settings', ?) on conflict(key) do update set value = excluded.value")
    .bind(JSON.stringify(s))
    .run();
  return s;
}

// ---------------- Zotero ----------------

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

function creator(name) {
  const parts = String(name).trim().split(/\s+/);
  if (parts.length >= 2) return { creatorType: "author", firstName: parts.slice(0, -1).join(" "), lastName: parts.at(-1) };
  return { creatorType: "author", name: String(name).trim() };
}

// 和 core/src/robot_times/zotero/local_api.py 的 paper_to_item 保持一致
export function paperToItem(p, tag) {
  const arxiv = (p.source || "arxiv") === "arxiv";
  return {
    itemType: "preprint",
    title: p.title,
    creators: (p.authors || []).map(creator),
    abstractNote: p.abstract || "",
    repository: arxiv ? "arXiv" : "",
    archiveID: arxiv ? `arXiv:${p.id}` : "",
    date: p.date || "",
    url: p.url || "",
    tags: [{ tag }],
  };
}

export function noteHtml(p, myNote) {
  const parts = ["<h2>The Robot Times 摘要</h2>"];
  if (p.titleZh) parts.push(`<p><b>${esc(p.titleZh)}</b></p>`);
  if (p.analysis?.review) parts.push(`<p><b>解读：</b>${esc(p.analysis.review)}</p>`);
  if (p.summary) parts.push(`<p>${esc(p.summary)}</p>`);
  if (p.why) parts.push(`<p><b>为什么推荐：</b>${esc(p.why)}</p>`);
  if (p.tip) parts.push(`<p><b>怎么读：</b>${esc(p.tip)}</p>`);
  const s = p.scores || {};
  parts.push(`<p><i>综合分 ${Number(s.total || 0).toFixed(1)} · 相关 ${s.rel ?? "-"} · 影响 ${s.impact ?? "-"} · 热度 ${s.buzz ?? "-"}</i></p>`);
  if (myNote && myNote.trim()) {
    parts.push("<h2>我的笔记</h2>", ...myNote.trim().split(/\n{2,}/).map((para) => `<p>${esc(para).replace(/\n/g, "<br>")}</p>`));
  }
  return parts.join("");
}

function writeToken() {
  const b = new Uint8Array(16);
  crypto.getRandomValues(b);
  return [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
}

const zBase = (env) => String(env.ZOTERO_API_BASE || "https://api.zotero.org").replace(/\/+$/, "");
const zUrl = (env, path) => `${zBase(env)}/users/${env.ZOTERO_USER_ID}${path}`;
const zHeaders = (env, extra = {}) => ({ "Zotero-API-Key": env.ZOTERO_API_KEY, "Zotero-API-Version": "3", ...extra });

async function zoteroWrite(env, items) {
  const resp = await fetch(zUrl(env, "/items"), {
    method: "POST",
    headers: zHeaders(env, { "Zotero-Write-Token": writeToken(), "content-type": "application/json" }),
    body: JSON.stringify(items),
  });
  if (resp.status === 403) throw new HttpError(502, "Zotero 拒绝写入：检查 ZOTERO_API_KEY 是否勾选了写权限，ZOTERO_USER_ID 是否正确。");
  if (resp.status === 429 || resp.status === 503) throw new HttpError(503, "Zotero 现在很忙，过一分钟再试。");
  if (!resp.ok) throw new HttpError(502, `Zotero 写入失败：HTTP ${resp.status}`);
  const r = await resp.json();
  const key = r.success?.["0"] || r.successful?.["0"]?.key;
  if (!key) throw new HttpError(502, `Zotero 没有接受这个条目：${r.failed?.["0"]?.message || "原因未知"}`);
  return key;
}

function needZotero(env) {
  if (!env.ZOTERO_USER_ID || !env.ZOTERO_API_KEY) throw new HttpError(400, "还没配置 Zotero：在 Cloudflare 后台给看板的 Worker 加上 ZOTERO_USER_ID 和 ZOTERO_API_KEY。");
}

// 读 Zotero 里的这个条目：不存在或在回收站里返回 null
async function zoteroItem(env, key) {
  const resp = await fetch(zUrl(env, `/items/${encodeURIComponent(key)}`), { headers: zHeaders(env) });
  if (resp.status === 404) return null;
  if (resp.status === 403) throw new HttpError(502, "Zotero 拒绝读取：检查 ZOTERO_API_KEY 和 ZOTERO_USER_ID。");
  if (!resp.ok) throw new HttpError(502, `读 Zotero 失败：HTTP ${resp.status}`);
  const item = await resp.json();
  return item?.data?.deleted ? null : item;
}

// 按名字找文件夹（Zotero 里叫“分类”/collection），子文件夹也算
async function findCollection(env, name) {
  for (let start = 0; start < 1000; start += 100) {
    const resp = await fetch(zUrl(env, `/collections?limit=100&start=${start}`), { headers: zHeaders(env) });
    if (!resp.ok) throw new HttpError(502, `读 Zotero 文件夹失败：HTTP ${resp.status}`);
    const rows = await resp.json();
    const hit = rows.find((c) => String(c.data?.name || "").trim() === name);
    if (hit) return hit.key;
    if (rows.length < 100) break;
  }
  throw new HttpError(400, `Zotero 里找不到名为“${name}”的文件夹。先在 Zotero 里建好，或者到“设置”里改成已有的文件夹名。`);
}

// ---- WebDAV：Zotero 用 WebDAV 同步附件时，文件在 <WebDAV 地址>/zotero/<附件编号>.zip，旁边一个 .prop 记修改时间和 MD5 ----
// 地址填得和 Zotero“设置 → 同步 → 文件同步”里一样（例如 https://dav.jianguoyun.com/dav/），会自动加上 zotero/
function webdav(env) {
  if (!env.WEBDAV_URL || !env.WEBDAV_USER || !env.WEBDAV_PASSWORD) return null;
  let base = String(env.WEBDAV_URL).trim().replace(/\/+$/, "");
  if (!/^https?:\/\//.test(base)) base = `https://${base}`;
  if (!/\/zotero$/.test(base)) base += "/zotero";
  return { base: `${base}/`, auth: `Basic ${btoa(unescape(encodeURIComponent(`${env.WEBDAV_USER}:${env.WEBDAV_PASSWORD}`)))}` };
}

const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c >>> 0;
  }
  return t;
})();
function crc32(bytes) {
  let c = 0xffffffff;
  for (let i = 0; i < bytes.length; i++) c = CRC_TABLE[(c ^ bytes[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

// 只含一个文件、不压缩的 zip（Zotero 的 WebDAV 格式就是这样一个 zip）
export function makeZip(name, bytes, date = new Date()) {
  const fname = new TextEncoder().encode(name);
  const crc = crc32(bytes);
  const time = (date.getHours() << 11) | (date.getMinutes() << 5) | (date.getSeconds() >> 1);
  const day = ((date.getFullYear() - 1980) << 9) | ((date.getMonth() + 1) << 5) | date.getDate();
  const local = new DataView(new ArrayBuffer(30));
  [[0, 0x04034b50, 4], [4, 20, 2], [6, 0x0800, 2], [8, 0, 2], [10, time, 2], [12, day, 2], [14, crc, 4], [18, bytes.length, 4], [22, bytes.length, 4], [26, fname.length, 2], [28, 0, 2]]
    .forEach(([o, v, n]) => (n === 4 ? local.setUint32(o, v, true) : local.setUint16(o, v, true)));
  const central = new DataView(new ArrayBuffer(46));
  [[0, 0x02014b50, 4], [4, 20, 2], [6, 20, 2], [8, 0x0800, 2], [10, 0, 2], [12, time, 2], [14, day, 2], [16, crc, 4], [20, bytes.length, 4], [24, bytes.length, 4], [28, fname.length, 2], [30, 0, 2], [32, 0, 2], [34, 0, 2], [36, 0, 2], [38, 0, 4], [42, 0, 4]]
    .forEach(([o, v, n]) => (n === 4 ? central.setUint32(o, v, true) : central.setUint16(o, v, true)));
  const cdOffset = 30 + fname.length + bytes.length, cdSize = 46 + fname.length;
  const end = new DataView(new ArrayBuffer(22));
  [[0, 0x06054b50, 4], [4, 0, 2], [6, 0, 2], [8, 1, 2], [10, 1, 2], [12, cdSize, 4], [16, cdOffset, 4], [20, 0, 2]]
    .forEach(([o, v, n]) => (n === 4 ? end.setUint32(o, v, true) : end.setUint16(o, v, true)));
  const out = new Uint8Array(cdOffset + cdSize + 22);
  let o = 0;
  for (const part of [new Uint8Array(local.buffer), fname, bytes, new Uint8Array(central.buffer), fname, new Uint8Array(end.buffer)]) {
    out.set(part, o);
    o += part.length;
  }
  return out;
}

async function davPut(dav, path, body, type) {
  const resp = await fetch(dav.base + path, { method: "PUT", headers: { authorization: dav.auth, "content-type": type }, body });
  if (resp.status === 401) throw new Error("WebDAV 账号或密码不对（坚果云要用“应用密码”）");
  if (resp.status === 404 || resp.status === 409) throw new Error(`WebDAV 上找不到 zotero 文件夹（${dav.base}），检查 WEBDAV_URL 是否和 Zotero 里填的一样`);
  if (!resp.ok) throw new Error(`写入 WebDAV 失败：HTTP ${resp.status}`);
}

async function davHas(dav, key) {
  const resp = await fetch(`${dav.base}${key}.prop`, { method: "HEAD", headers: { authorization: dav.auth } });
  return resp.ok;
}

// 条目下面的 PDF 附件：{key, uploaded}。uploaded=false 表示附件条目在，但文件没传上去（会显示“找不到文件”）
async function pdfAttachment(env, key) {
  const resp = await fetch(zUrl(env, `/items/${encodeURIComponent(key)}/children`), { headers: zHeaders(env) });
  if (!resp.ok) return null;
  const att = (await resp.json()).find((c) => c.data?.itemType === "attachment" && c.data?.contentType === "application/pdf" && !c.data?.deleted);
  if (!att) return null;
  const dav = webdav(env);
  return { key: att.key, uploaded: dav ? await davHas(dav, att.key).catch(() => false) : Boolean(att.data?.md5), storage: dav ? "webdav" : "zotero" };
}
const hasPdf = async (env, key) => Boolean((await pdfAttachment(env, key))?.uploaded);

async function addToCollection(env, item, colKey) {
  const cols = item.data?.collections || [];
  if (!colKey || cols.includes(colKey)) return;
  const resp = await fetch(zUrl(env, `/items/${encodeURIComponent(item.key)}`), {
    method: "PATCH",
    headers: zHeaders(env, { "If-Unmodified-Since-Version": String(item.version), "content-type": "application/json" }),
    body: JSON.stringify({ collections: [...cols, colKey] }),
  });
  if (!resp.ok && resp.status !== 204) throw new HttpError(502, `放进文件夹失败：HTTP ${resp.status}`);
}

const MAX_PDF = 40 * 1024 * 1024;
const hex = (buf) => [...new Uint8Array(buf)].map((x) => x.toString(16).padStart(2, "0")).join("");
const form = (o) => Object.entries(o).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join("&");

// 像 Zotero 浏览器插件那样把 PDF 存成条目的附件：下载 → 在 Zotero 建附件 → 上传到 Zotero 的文件存储。
// PDF 只在这次请求里经过看板服务（内存里），不保存到我们的服务器。占用的是你 Zotero 账户的存储空间。
// 附件 PDF 存到哪：配了 WEBDAV_* 写进 WebDAV；ZOTERO_PDF=zotero 时传 Zotero 自带存储；都没有就不加 PDF
// （用 WebDAV 同步附件的人如果传到 Zotero 存储，电脑上会显示“找不到文件”，不如交给 Zotero 的“查找可用的 PDF”）
const pdfMode = (env) => (webdav(env) ? "webdav" : String(env.ZOTERO_PDF || "").toLowerCase() === "zotero" ? "zotero" : "none");

async function attachPdf(env, parentKey, p) {
  const log = [];
  const done = async (msg) => {
    // 每一步的结果记进数据库，PDF 没加上时方便排查
    await env.DB.prepare("insert into config (key, value) values ('zotero_debug', ?) on conflict(key) do update set value = excluded.value")
      .bind(JSON.stringify({ at: nowIso(), paper: p.id, parent: parentKey, steps: log, result: msg || "ok" })).run().catch(() => {});
    return msg;
  };
  if (!p.pdf) return done("这篇没有 PDF 地址");
  const pdf = await fetch(p.pdf, { headers: { "user-agent": "robot-times/0.1 (personal Zotero import)", accept: "application/pdf" }, redirect: "follow" });
  log.push(`下载 ${p.pdf}：HTTP ${pdf.status} ${pdf.headers.get("content-type") || ""}`);
  if (!pdf.ok) return done(`下载 PDF 失败（HTTP ${pdf.status}）`);
  if (!/pdf/i.test(pdf.headers.get("content-type") || "")) return done("下载到的不是 PDF");
  const bytes = new Uint8Array(await pdf.arrayBuffer());
  log.push(`大小 ${bytes.length}`);
  if (bytes.length > MAX_PDF) return done("PDF 超过 40 MB，没有上传");
  const filename = `${String(p.id).replace(/[^\w.-]+/g, "_")}.pdf`;
  // 以前传失败留下的空附件就接着用，不再新建一个
  const old = await pdfAttachment(env, parentKey);
  const attKey = old && !old.uploaded ? old.key : await zoteroWrite(env, [{
    itemType: "attachment", parentItem: parentKey, linkMode: "imported_url", title: "Full Text PDF",
    url: p.pdf, accessDate: nowIso(), contentType: "application/pdf", charset: "", filename, tags: [],
  }]);
  log.push(`附件条目 ${attKey}${old && !old.uploaded ? "（沿用）" : ""}`);
  const md5 = hex(await crypto.subtle.digest("MD5", bytes));
  const dav = webdav(env);
  if (dav) {
    // 用 WebDAV 同步附件：按 Zotero 的格式写进 WebDAV，再把 md5 和修改时间记到附件条目上（WebDAV 用户可以直接改这两个字段）
    const mtime = Date.now();
    try {
      await davPut(dav, `${attKey}.zip`, makeZip(filename, bytes), "application/zip");
      log.push(`WebDAV：写入 ${attKey}.zip`);
      await davPut(dav, `${attKey}.prop`, `<properties version="1"><mtime>${mtime}</mtime><hash>${md5}</hash></properties>`, "text/xml");
      log.push(`WebDAV：写入 ${attKey}.prop`);
    } catch (e) {
      log.push(String(e.message || e));
      return done(e.message || "写入 WebDAV 失败");
    }
    const att = await zoteroItem(env, attKey);
    if (att) {
      const resp = await fetch(zUrl(env, `/items/${attKey}`), {
        method: "PATCH",
        headers: zHeaders(env, { "If-Unmodified-Since-Version": String(att.version), "content-type": "application/json" }),
        body: JSON.stringify({ md5, mtime }),
      });
      log.push(`附件记 md5：HTTP ${resp.status}`);
    }
    return done(null);
  }
  const fileUrl = zUrl(env, `/items/${attKey}/file`);
  const formHeaders = zHeaders(env, { "content-type": "application/x-www-form-urlencoded", "If-None-Match": "*" });
  const auth = await fetch(fileUrl, { method: "POST", headers: formHeaders, body: form({ md5, filename, filesize: bytes.length, mtime: Date.now() }) });
  const authText = await auth.text();
  log.push(`申请上传：HTTP ${auth.status} ${authText.slice(0, 200)}`);
  if (auth.status === 413) return done("Zotero 的存储空间满了，PDF 没有上传（条目已加入）");
  if (!auth.ok) return done(`Zotero 不让上传 PDF（HTTP ${auth.status}：${authText.slice(0, 120)}）`);
  const a = JSON.parse(authText);
  if (a.exists) return done(null); // Zotero 里已经有一模一样的文件
  const enc = new TextEncoder();
  const prefix = enc.encode(a.prefix || ""), suffix = enc.encode(a.suffix || "");
  const body = new Uint8Array(prefix.length + bytes.length + suffix.length);
  body.set(prefix, 0); body.set(bytes, prefix.length); body.set(suffix, prefix.length + bytes.length);
  const up = await fetch(a.url, { method: "POST", headers: { "content-type": a.contentType }, body });
  const upText = up.ok ? "" : (await up.text()).slice(0, 200);
  log.push(`上传：HTTP ${up.status} ${upText}`);
  if (up.status !== 201 && !up.ok) return done(`上传 PDF 失败（HTTP ${up.status}）`);
  const reg = await fetch(fileUrl, { method: "POST", headers: formHeaders, body: form({ upload: a.uploadKey }) });
  const regText = reg.status === 204 ? "" : (await reg.text()).slice(0, 200);
  log.push(`登记：HTTP ${reg.status} ${regText}`);
  if (reg.status !== 204 && !reg.ok) return done(`登记 PDF 失败（HTTP ${reg.status}：${regText.slice(0, 120)}）`);
  return done(null);
}

// 设置里填了 Zotero 文件夹名才放进去；没填就只加进文献库根目录
const inboxName = (settings) => String(settings.zoteroCollection || "").trim();

// 打开一篇已入 Zotero 的论文时核对：条目被删掉或进了回收站，就把本地记录也去掉；顺便看 PDF 和文件夹齐不齐
async function checkZotero(env, id) {
  needZotero(env);
  const done = await env.DB.prepare("select item_key from zotero_items where paper_id = ?").bind(id).first();
  if (!done) return { paperId: id, key: null };
  const item = await zoteroItem(env, done.item_key);
  if (!item) {
    await env.DB.prepare("delete from zotero_items where paper_id = ?").bind(id).run();
    return { paperId: id, key: null, gone: true };
  }
  const name = inboxName(await loadSettings(env));
  let inFolder = true;
  if (name) {
    try { inFolder = (item.data?.collections || []).includes(await findCollection(env, name)); } catch { inFolder = true; }
  }
  const att = pdfMode(env) === "none" ? null : await pdfAttachment(env, done.item_key);
  await env.DB.prepare("insert into config (key, value) values ('zotero_check', ?) on conflict(key) do update set value = excluded.value")
    .bind(JSON.stringify({ at: nowIso(), paper: id, item: done.item_key, attachment: att })).run().catch(() => {});
  return { paperId: id, key: done.item_key, web: item.links?.alternate?.href || "", pdf: pdfMode(env) === "none" ? null : Boolean(att?.uploaded), inFolder, folder: name };
}

// 撤销：把条目（连同下面的 PDF 和笔记）移进 Zotero 回收站，可以在 Zotero 里恢复
async function removeFromZotero(env, id) {
  needZotero(env);
  const done = await env.DB.prepare("select item_key from zotero_items where paper_id = ?").bind(id).first();
  if (done) {
    const item = await zoteroItem(env, done.item_key);
    if (item) {
      const resp = await fetch(zUrl(env, `/items/${encodeURIComponent(done.item_key)}`), {
        method: "PATCH",
        headers: zHeaders(env, { "If-Unmodified-Since-Version": String(item.version), "content-type": "application/json" }),
        body: JSON.stringify({ deleted: 1 }),
      });
      if (resp.status === 412) throw new HttpError(409, "这个条目刚在 Zotero 里改过，请再点一次。");
      if (resp.status === 403) throw new HttpError(502, "Zotero 拒绝修改：检查 ZOTERO_API_KEY 是否有写权限。");
      if (!resp.ok && resp.status !== 204) throw new HttpError(502, `从 Zotero 移除失败：HTTP ${resp.status}`);
    }
  }
  await env.DB.batch([
    env.DB.prepare("delete from zotero_items where paper_id = ?").bind(id),
    env.DB.prepare("delete from feedback where paper_id = ? and action = 'add'").bind(id),
  ]);
  return { paperId: id, key: null };
}

// 加入 Zotero：条目放进设置里的文件夹（不填就放在“我的文库”），带上 PDF 附件和摘要笔记。
// 已经加过的再点一次，会把缺的文件夹和 PDF 补上。
async function addToZotero(env, id) {
  needZotero(env);
  const [p, settings, note, done] = await Promise.all([
    getPaper(env, id),
    loadSettings(env),
    env.DB.prepare("select note from notes where paper_id = ?").bind(id).first(),
    env.DB.prepare("select item_key from zotero_items where paper_id = ?").bind(id).first(),
  ]);
  const folder = inboxName(settings);
  const colKey = folder ? await findCollection(env, folder) : null;
  const existing = done ? await zoteroItem(env, done.item_key) : null;
  if (existing) {
    await addToCollection(env, existing, colKey);
    let pdfError = null;
    if (pdfMode(env) !== "none" && !(await hasPdf(env, existing.key))) pdfError = await attachPdf(env, existing.key, p).catch((e) => e.message);
    return { paperId: id, key: existing.key, existing: true, folder, pdfError, pdf: pdfMode(env) !== "none" };
  }
  if (done) await env.DB.prepare("delete from zotero_items where paper_id = ?").bind(id).run(); // 在 Zotero 里删掉了：重新加
  const tag = settings.tag || "Robot Times";
  const key = await zoteroWrite(env, [{ ...paperToItem(p, tag), collections: colKey ? [colKey] : [] }]);
  const at = nowIso();
  await env.DB.batch([
    env.DB.prepare("insert into zotero_items (paper_id, item_key, at) values (?, ?, ?) on conflict(paper_id) do nothing").bind(id, key, at),
    env.DB.prepare("insert into feedback (paper_id, action, at) values (?, 'add', ?)").bind(id, at),
  ]);
  const pdfError = pdfMode(env) === "none" ? null : await attachPdf(env, key, p).catch((e) => e.message || "PDF 没加上");
  let noteError = null;
  try {
    await zoteroWrite(env, [{ itemType: "note", parentItem: key, note: noteHtml(p, note?.note || ""), tags: [{ tag }] }]);
  } catch (e) {
    noteError = e.message; // 条目已经写进去了，笔记失败不算整体失败
  }
  return { paperId: id, key, folder, pdfError, noteError, pdf: pdfMode(env) !== "none" };
}

// ---------------- 问 AI（DeepSeek，流式返回） ----------------



export function askPrompt(p, interests, kind, question, context, myNote) {
  const head =
    `他的研究兴趣：${interests || "（未填写）"}\n\n` +
    `论文：${p.title}\n作者：${(p.authors || []).join(", ")}\n发表：${p.venue || ""}\n` +
    (p.comment ? `作者备注：${p.comment}\n` : "") +
    `摘要：${p.abstract || ""}\n` +
    (p.summary ? `中文要点：${p.summary}\n` : "") +
    ((p.related || []).length ? `他文献库里最接近的：${p.related.map((t) => `《${t}》`).join("；")}\n` : "") +
    (context ? `\n他粘贴的原文片段：\n"""\n${context}\n"""\n` : "");
  if (kind === "check") {
    return (
      `${head}\n他自己写的笔记：\n"""\n${myNote || "（空）"}\n"""\n\n` +
      "帮他检查这份笔记，按下面三点给反馈，每点一两句，指出具体是哪句话：\n" +
      "1. 问题、核心 idea、关键结果有没有说清；\n" +
      "2. 有没有理解偏差（对照摘要和原文片段）；\n" +
      "3. 补一句什么能把它和相关工作或他自己的研究连起来。\n" +
      "最后给一个改写后的 3–5 句版本：保留他自己的观点和说法，不要替他加没有依据的判断。"
    );
  }
  return (
    `${head}\n他的问题：${question || "这篇论文最值得带走的一个 idea 是什么？主要局限在哪？"}\n\n` +
    "回答方式：第一句直接回答。然后给依据，说明依据来自摘要还是他粘贴的原文。" +
    "有不确定的地方，说清要看原文哪一部分才能确定。一般不超过 300 字，问题本身需要展开时可以更长。"
  );
}

async function ask(env, b) {
  if (!env.DEEPSEEK_API_KEY) throw new HttpError(400, "还没配置 DEEPSEEK_API_KEY。");
  const p = await getPaper(env, String(b.paperId || ""));
  const settings = await loadSettings(env);
  const context = String(b.context || "").slice(0, 60_000);
  const myNote = String(b.note || "").slice(0, 10_000);
  const prompt = askPrompt(p, settings.interests, b.kind, String(b.question || "").slice(0, 2000), context, myNote);
  const base = String(env.LLM_BASE_URL || "https://api.deepseek.com").replace(/\/+$/, "");
  const resp = await fetch(`${base}/chat/completions`, {
    method: "POST",
    headers: { authorization: `Bearer ${env.DEEPSEEK_API_KEY}`, "content-type": "application/json" },
    body: JSON.stringify({ model: env.LLM_MODEL || "deepseek-flash", stream: true, temperature: 0.3, messages: [{ role: "system", content: styleFor(presetFor(settings)) }, { role: "user", content: prompt }] }),
  });
  if (resp.status === 401) throw new HttpError(502, "DeepSeek 拒绝了请求：检查 DEEPSEEK_API_KEY。");
  if (resp.status === 402) throw new HttpError(502, "DeepSeek 账户余额不足。");
  if (resp.status === 429) throw new HttpError(503, "DeepSeek 请求太频繁，稍后再试。");
  if (!resp.ok || !resp.body) throw new HttpError(502, `DeepSeek 出错了：HTTP ${resp.status}`);
  return new Response(resp.body, { headers: { "content-type": "text/event-stream; charset=utf-8", "cache-control": "no-store" } });
}

// ---------------- 生成任务（触发 GitHub Actions） ----------------
// GitHub 自带的定时任务经常晚几个小时甚至漏跑，所以由这里按时触发：
// Worker 每 30 分钟检查一次（scheduled），打开看板时也检查一次（bootstrap），今天该有的还没有就触发。
// 需要 GITHUB_TOKEN（只给这个仓库、Actions 读写权限）和 GITHUB_REPO；没配时 GitHub 自己的定时任务照常兜底。

const JOBS = { daily: "今天的推荐", news: "业界简报", weekly: "本周回顾" };
const JOB_MINUTES = { daily: 20, news: 4, weekly: 4 }; // 大约要跑多久，看板上提示用
const BJ = 8 * 3600e3; // 北京时间（没有夏令时）
const DAILY_FROM = 9.5; // 北京时间 9:30 以后才自动生成：arXiv 早上 8 点（冬令时 9 点）发布
const RETRY_MINUTES = 30; // 自动触发后多久内不再触发同一个任务
const MAX_AUTO_PER_DAY = 3; // 一个任务一天最多自动触发几次（一直失败时别把 GitHub 的免费额度用光）

const bjParts = (now) => {
  const d = new Date(now.getTime() + BJ);
  return { date: d.toISOString().slice(0, 10), hour: d.getUTCHours() + d.getUTCMinutes() / 60, dow: d.getUTCDay() };
};
const addDays = (iso, n) => new Date(Date.parse(`${iso}T00:00:00Z`) + n * 864e5).toISOString().slice(0, 10);
export function isoWeek(iso) {
  const d = new Date(`${iso}T00:00:00Z`);
  const day = d.getUTCDay() || 7;
  d.setUTCDate(d.getUTCDate() + 4 - day);
  const y = d.getUTCFullYear();
  const w = Math.ceil(((d - Date.UTC(y, 0, 1)) / 864e5 + 1) / 7);
  return `${y}-W${String(w).padStart(2, "0")}`;
}
// 自动写周报时写哪一周（用那周的周日表示）：周日晚上 8 点后写本周，其他时候补上一周
export function weeklyTarget(now) {
  const t = bjParts(now);
  if (t.dow === 0) return t.hour >= 20 ? t.date : addDays(t.date, -7);
  return addDays(t.date, -t.dow);
}

// 现在该有、但还没有的任务
export async function dueJobs(env, now = new Date()) {
  const t = bjParts(now);
  const out = [];
  if (t.dow >= 1 && t.dow <= 5 && t.hour >= DAILY_FROM) {
    const r = await env.DB.prepare("select json_extract(data, '$.generatedAt') as at from days where date = ?").bind(t.date).first();
    const at = r?.at ? bjParts(new Date(r.at)) : null;
    if (!at || at.date !== t.date || at.hour < 9) out.push("daily"); // 早上 9 点前生成的还是前一天的论文
  }
  if (t.hour >= 8 && !(await env.DB.prepare("select 1 as x from news where date = ?").bind(t.date).first())) out.push("news");
  const target = weeklyTarget(now);
  const [hasWeek, hadDays] = await Promise.all([
    env.DB.prepare("select 1 as x from weeks where week = ?").bind(isoWeek(target)).first(),
    env.DB.prepare("select 1 as x from days where date >= ? and date <= ? limit 1").bind(addDays(target, -6), target).first(),
  ]);
  if (!hasWeek && hadDays && (t.dow !== 0 || t.hour >= 20)) out.push("weekly");
  return out;
}

function ghRepo(env) {
  const repo = String(env.GITHUB_REPO || "").trim();
  return env.GITHUB_TOKEN && /^[\w.-]+\/[\w.-]+$/.test(repo) ? repo : null;
}

async function gh(env, path, init = {}) {
  return fetch(`https://api.github.com/repos/${ghRepo(env)}${path}`, {
    ...init,
    headers: {
      authorization: `Bearer ${env.GITHUB_TOKEN}`,
      accept: "application/vnd.github+json",
      "x-github-api-version": "2022-11-28",
      "user-agent": "robot-times",
      "content-type": "application/json",
    },
  });
}

const ACTIVE = new Set(["queued", "in_progress", "waiting", "requested", "pending"]);
const kindOf = (run) => (String(run.path || "").match(/workflows\/(daily|news|weekly)\.yml/) || [])[1];

// 最近的 GitHub 任务 → 看板上的进度：{running, label, startedAt, queue, minutes, last}
async function ghRuns(env) {
  const r = await gh(env, "/actions/runs?per_page=20");
  if (!r.ok) throw new HttpError(502, `读不到 GitHub 上的任务：HTTP ${r.status}，检查 GITHUB_TOKEN 的权限。`);
  return ((await r.json()).workflow_runs || []).filter(kindOf);
}

function statusOf(runs, dispatched = []) {
  const active = runs.filter((x) => ACTIVE.has(x.status)).sort((a, b) => a.created_at.localeCompare(b.created_at));
  const kinds = [...new Set([...active.map(kindOf), ...dispatched])];
  const running = active.find((x) => x.status === "in_progress");
  const now = running ? kindOf(running) : kinds[0] || null;
  const done = runs.find((x) => x.status === "completed");
  return {
    running: now,
    label: JOBS[now] || "",
    startedAt: running?.run_started_at || null,
    queue: kinds.filter((k) => k !== now),
    minutes: now ? JOB_MINUTES[now] : null,
    last: done
      ? {
          kind: kindOf(done),
          ok: done.conclusion === "success",
          message: done.conclusion === "success" ? "" : `GitHub 上的任务没成功（${done.conclusion}），可以去仓库的 Actions 页面看原因。`,
          at: done.updated_at,
        }
      : null,
  };
}

async function dispatch(env, kind, inputs) {
  const resp = await gh(env, `/actions/workflows/${kind}.yml/dispatches`, {
    method: "POST",
    body: JSON.stringify({ ref: "main", ...(inputs ? { inputs } : {}) }),
  });
  if (resp.status !== 204) throw new HttpError(502, `触发失败：GitHub 返回 HTTP ${resp.status}，检查 GITHUB_TOKEN 的权限。`);
}

// 打开看板和定时检查时调用：该有的还没有、也没在跑，就触发。返回进度（没配 GitHub 或没事可做时返回 null）
export async function autoRun(env, now = new Date()) {
  if (!ghRepo(env)) return null;
  const due = await dueJobs(env, now);
  if (!due.length) return null;
  const runs = await ghRuns(env);
  const busy = new Set(runs.filter((x) => ACTIVE.has(x.status)).map(kindOf));
  const row = await env.DB.prepare("select value from config where key = 'auto_runs'").first();
  const log = row ? JSON.parse(row.value) : {};
  const today = bjParts(now).date;
  const started = [];
  for (const kind of due) {
    const prev = log[kind] || {};
    const count = prev.day === today ? prev.count || 0 : 0;
    const recent = prev.at && now - Date.parse(prev.at) < RETRY_MINUTES * 60e3;
    if (busy.has(kind) || recent || count >= MAX_AUTO_PER_DAY) continue;
    await dispatch(env, kind, kind === "weekly" ? { day: weeklyTarget(now) } : null);
    log[kind] = { day: today, count: count + 1, at: now.toISOString() };
    started.push(kind);
  }
  if (started.length) {
    await env.DB.prepare("insert into config (key, value) values ('auto_runs', ?) on conflict(key) do update set value = excluded.value")
      .bind(JSON.stringify(log))
      .run();
  }
  const status = statusOf(runs, started);
  return status.running || status.queue.length ? status : null;
}

export async function jobStatus(env) {
  if (!ghRepo(env)) return null;
  return statusOf(await ghRuns(env));
}

// 看板上点“现在生成”：同一个任务已经在跑就不再触发
async function runJob(env, job) {
  if (!env.GITHUB_TOKEN) throw new HttpError(400, "还没配置 GITHUB_TOKEN，可以去 GitHub 仓库的 Actions 页面手动运行。");
  if (!ghRepo(env)) throw new HttpError(400, "还没配置 GITHUB_REPO（你的仓库，例如 yourname/robot-times）。");
  const runs = await ghRuns(env);
  const busy = runs.some((x) => ACTIVE.has(x.status) && kindOf(x) === job);
  if (!busy) await dispatch(env, job, null);
  return statusOf(runs, busy ? [] : [job]);
}
