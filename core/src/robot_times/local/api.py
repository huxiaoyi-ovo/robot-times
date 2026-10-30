"""本地版的看板接口：和云端看板 Worker（web/app/src/index.js）的 /api 一样，页面不用改。

差别只在两处：数据存在本机 SQLite；Zotero 走桌面端的本地接口（localhost:23119），
不经过 zotero.org，加进去马上就能在 Zotero 里看到，PDF 直接存进本机的 Zotero 文件夹。
"""

from __future__ import annotations

import html
import json
import logging
import re
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import httpx

from ..cloud import repo
from ..cloud.store import Store
from ..llm.rerank import LLMClient
from ..llm.style import style_for
from ..presets import PRESETS, get_preset
from ..zotero.local_api import ZoteroError, ZoteroLocalClient

log = logging.getLogger("robot_times.local")

STATUSES = {"todo", "reading", "done", "skip"}
MAX_PDF = 40 * 1024 * 1024
PDF_UA = "robot-times/0.1 (personal Zotero import)"


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class Reply:
    status: int = 200
    body: Any = None
    stream: Iterator[bytes] | None = None  # 问 AI 的流式回答


def lite(p: dict[str, Any]) -> dict[str, Any]:
    """列表里不带长篇解读和摘要译文（打开一篇时再取全文）。"""
    if not p.get("analysis") and not p.get("abstractZh"):
        return p
    rest = {k: v for k, v in p.items() if k not in ("analysis", "abstractZh")}
    analysis = p.get("analysis") or {}
    return {**rest, "hasAnalysis": bool(p.get("analysis")), "cover": analysis.get("cover")}


def note_row(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "paperId": r["paper_id"],
        "status": r["status"],
        "note": r["note"],
        "flags": json.loads(r.get("flags") or "[]"),
        "updatedAt": r["updated_at"],
    }


def ids_in_plan(plan: list[dict[str, Any]] | None) -> list[str]:
    out: list[str] = []
    for x in plan or []:
        out += x.get("paperIds") or (ids_in_plan(x["items"]) if x.get("items") else [])
    return out


def site_info(settings: dict[str, Any]) -> dict[str, Any]:
    p = get_preset(settings)
    return {
        "name": p["site_name"],
        "field": p["field"],
        "presets": [
            {"id": k, "label": x["label"], "siteName": x["site_name"], "field": x["field"]}
            for k, x in PRESETS.items()
        ],
    }


def _creator(name: str) -> dict[str, str]:
    parts = str(name).strip().split()
    if len(parts) >= 2:
        return {"creatorType": "author", "firstName": " ".join(parts[:-1]), "lastName": parts[-1]}
    return {"creatorType": "author", "name": str(name).strip()}


def paper_item(p: dict[str, Any], tag: str) -> dict[str, Any]:
    arxiv = (p.get("source") or "arxiv") == "arxiv"
    return {
        "itemType": "preprint",
        "title": p["title"],
        "creators": [_creator(a) for a in p.get("authors") or []],
        "abstractNote": p.get("abstract") or "",
        "repository": "arXiv" if arxiv else "",
        "archiveID": f"arXiv:{p['id']}" if arxiv else "",
        "date": p.get("date") or "",
        "url": p.get("url") or "",
        "tags": [{"tag": tag}],
    }


def note_html(p: dict[str, Any], my_note: str = "") -> str:
    e = html.escape
    parts = ["<h2>The Robot Times 摘要</h2>"]
    if p.get("titleZh"):
        parts.append(f"<p><b>{e(p['titleZh'])}</b></p>")
    if (p.get("analysis") or {}).get("review"):
        parts.append(f"<p><b>解读：</b>{e(p['analysis']['review'])}</p>")
    if p.get("summary"):
        parts.append(f"<p>{e(p['summary'])}</p>")
    if p.get("why"):
        parts.append(f"<p><b>为什么推荐：</b>{e(p['why'])}</p>")
    if p.get("tip"):
        parts.append(f"<p><b>怎么读：</b>{e(p['tip'])}</p>")
    s = p.get("scores") or {}
    parts.append(
        f"<p><i>综合分 {float(s.get('total') or 0):.1f} · 相关 {s.get('rel', '-')} · "
        f"影响 {s.get('impact', '-')} · 热度 {s.get('buzz', '-')}</i></p>"
    )
    if my_note.strip():
        parts.append("<h2>我的笔记</h2>")
        parts += [
            f"<p>{e(para).replace(chr(10), '<br>')}</p>"
            for para in re.split(r"\n{2,}", my_note.strip())
        ]
    return "".join(parts)


def ask_prompt(
    p: dict[str, Any], interests: str, kind: str, question: str, context: str, my_note: str
) -> str:
    """和看板 Worker 的 askPrompt 一样。"""
    head = (
        f"他的研究兴趣：{interests or '（未填写）'}\n\n"
        f"论文：{p['title']}\n作者：{', '.join(p.get('authors') or [])}\n发表：{p.get('venue') or ''}\n"
        + (f"作者备注：{p['comment']}\n" if p.get("comment") else "")
        + f"摘要：{p.get('abstract') or ''}\n"
        + (f"中文要点：{p['summary']}\n" if p.get("summary") else "")
        + (
            "他文献库里最接近的：" + "；".join(f"《{t}》" for t in p["related"]) + "\n"
            if p.get("related")
            else ""
        )
        + (f'\n他粘贴的原文片段：\n"""\n{context}\n"""\n' if context else "")
    )
    if kind == "check":
        return (
            f'{head}\n他自己写的笔记：\n"""\n{my_note or "（空）"}\n"""\n\n'
            "帮他检查这份笔记，按下面三点给反馈，每点一两句，指出具体是哪句话：\n"
            "1. 问题、核心 idea、关键结果有没有说清；\n"
            "2. 有没有理解偏差（对照摘要和原文片段）；\n"
            "3. 补一句什么能把它和相关工作或他自己的研究连起来。\n"
            "最后给一个改写后的 3–5 句版本：保留他自己的观点和说法，不要替他加没有依据的判断。"
        )
    return (
        f"{head}\n他的问题：{question or '这篇论文最值得带走的一个 idea 是什么？主要局限在哪？'}\n\n"
        "回答方式：第一句直接回答。然后给依据，说明依据来自摘要还是他粘贴的原文。"
        "有不确定的地方，说清要看原文哪一部分才能确定。一般不超过 300 字，问题本身需要展开时可以更长。"
    )


class LocalApi:
    """把一个请求（方法、路径、查询参数、请求体）变成 Reply。HTTP 部分在 server.py。"""

    def __init__(
        self,
        store: Store,
        *,
        zotero: ZoteroLocalClient | None = None,
        llm: LLMClient | None = None,
        llm_stream: Callable[[list[dict[str, str]]], Iterator[bytes]] | None = None,
        jobs: Any = None,  # jobs.Jobs：后台生成推荐、业界简报和周报
        updater: Any = None,  # update.Updater：检查和执行更新
        pdf: bool = True,
        http: httpx.Client | None = None,
    ) -> None:
        self.store = store
        self.zotero = zotero
        self.llm = llm
        self.llm_stream = llm_stream
        self.jobs = jobs
        self.updater = updater
        self.pdf = pdf
        self.http = http or httpx.Client(timeout=60.0, follow_redirects=True)

    # ---------- 路由 ----------

    def handle(self, method: str, path: str, query: dict[str, str], body: bytes) -> Reply:
        try:
            return self._route(method, path.rstrip("/"), query, body)
        except ApiError as e:
            return Reply(e.status, {"error": str(e)})
        except repo.SettingsError as e:
            return Reply(400, {"error": str(e)})
        except ZoteroError as e:
            return Reply(502, {"error": str(e)})
        except httpx.ConnectError:
            return Reply(502, {"error": "连不上 Zotero：请确认 Zotero 已经打开。"})
        except Exception:
            log.exception("接口出错：%s %s", method, path)
            return Reply(500, {"error": "服务器出错了，稍后再试。"})

    def _route(self, m: str, p: str, q: dict[str, str], raw: bytes) -> Reply:
        def body() -> dict[str, Any]:
            if len(raw) > 200_000:
                raise ApiError(413, "内容太长了。")
            try:
                return json.loads(raw) if raw else {}
            except ValueError:
                raise ApiError(400, "请求格式不对。") from None

        def arg(pattern: str) -> str | None:
            g = re.fullmatch(pattern, p)
            return _unquote(g.group(1)) if g else None

        if m == "GET" and p == "/api/bootstrap":
            return Reply(body=self.bootstrap())
        if m == "GET" and p == "/api/papers":
            return Reply(body=self.search(q))
        if m == "GET" and (i := arg(r"/api/paper/([^/]+)")):
            return Reply(body=self.paper(i))
        if m == "PUT" and (i := arg(r"/api/notes/([^/]+)")):
            return Reply(body=self.save_note(i, body()))
        if m == "POST" and (i := arg(r"/api/feedback/([^/]+)")):
            return Reply(body=self.feedback(i, body()))
        if (i := arg(r"/api/zotero/([^/]+)")) and m in ("GET", "POST", "DELETE"):
            act = {"GET": self.zotero_check, "POST": self.zotero_add, "DELETE": self.zotero_remove}
            return Reply(body=act[m](i))
        if m == "GET" and (d := arg(r"/api/news/(\d{4}-\d{2}-\d{2})")):
            return Reply(body=self.news(d))
        if m == "GET" and p == "/api/config":
            return Reply(body=repo.load_settings(self.store))
        if m == "PUT" and p == "/api/config":
            s = repo.update_settings(repo.load_settings(self.store), body())
            repo.save_settings(self.store, s)
            return Reply(body=s)
        if m == "POST" and p == "/api/ask":
            return self.ask(body())
        if m == "POST" and (job := arg(r"/api/run/(daily|weekly|news)")):
            if self.jobs is None:
                raise ApiError(400, "这里不能生成。")
            return Reply(body=self.jobs.start(job, force=True))
        if p == "/api/update" and m in ("GET", "POST"):
            if self.updater is None:
                raise ApiError(400, "这里不能更新。")
            from .update import UpdateError

            try:
                if m == "POST":
                    return Reply(body=self.updater.start())
                if q.get("check"):
                    self.updater.check(force=True)
                return Reply(body=self.updater.status())
            except UpdateError as e:
                raise ApiError(400, str(e)) from None
        if m == "GET" and p == "/api/job":
            return Reply(body=self.jobs.status() if self.jobs else None)
        raise ApiError(404, "没有这个接口。")

    # ---------- 读 ----------

    def bootstrap(self) -> dict[str, Any]:
        st = self.store
        settings = repo.load_settings(st)
        if self.jobs is not None:
            self.jobs.auto()  # 今天的还没生成就在后台补上
        days = [
            json.loads(r["data"])
            for r in st.rows("select data from days order by date desc limit 30")
        ]
        weeks = [
            json.loads(r["data"])
            for r in st.rows("select data from weeks order by week desc limit 12")
        ]
        notes = {r["paper_id"]: note_row(r) for r in st.rows("select * from notes")}
        ids = [
            *(i for d in days for i in [*(d.get("picks") or []), *ids_in_plan(d.get("plan"))]),
            *(
                i
                for w in weeks
                for i in [
                    *(w.get("picks") or []),
                    *(i for t in w.get("trends") or [] for i in t.get("paperIds") or []),
                    *ids_in_plan((w.get("nextWeek") or {}).get("plan")),
                ]
            ),
            *notes,
        ]
        fb = st.rows(
            "select paper_id, action from feedback where action in ('like', 'skip') order by id"
        )
        news = st.rows("select data from news order by date desc limit 1")
        news_dates = st.rows("select date from news order by date desc limit 30")
        return {
            "me": "本机",
            "mode": "local",
            "settings": settings,
            "days": days,
            "weeks": weeks,
            "notes": notes,
            "papers": {k: lite(v) for k, v in repo.papers_by_ids(st, ids).items()},
            "zotero": {
                r["paper_id"]: r["item_key"]
                for r in st.rows("select paper_id, item_key from zotero_items")
            },
            "feedback": {r["paper_id"]: r["action"] for r in fb},
            "news": json.loads(news[0]["data"]) if news else None,
            "newsDates": [r["date"] for r in news_dates],
            "weather": repo.get_json(st, "weather"),
            "site": site_info(settings),
            "job": self.jobs.status() if self.jobs else None,
            "update": self._update_status(),
            "features": {
                "zotero": self.zotero is not None and bool(self.zotero.api_key),
                "ask": self.llm is not None or self.llm_stream is not None,
                "run": self.jobs is not None,
            },
        }

    def _update_status(self) -> dict[str, Any] | None:
        """打开看板时顺便在后台查一下新版本（12 小时最多一次），不让页面等。"""
        u = self.updater
        if u is None:
            return None
        u.last_seen = u.now()
        threading.Thread(target=u.check, daemon=True).start()
        return u.status()

    def search(self, q: dict[str, str]) -> dict[str, Any]:
        text = (q.get("q") or "").strip()[:100]
        status = q.get("status") or ""
        if status and status not in STATUSES:
            raise ApiError(400, "状态不对。")
        limit = min(100, max(1, _int(q.get("limit"), 50)))
        offset = max(0, _int(q.get("offset"), 0))
        like = "%" + re.sub(r"[\\%_]", lambda c: "\\" + c.group(0), text) + "%"
        where = (
            "(? = '' or p.data like ? escape '\\') and (? = '' or coalesce(n.status, 'todo') = ?)"
        )
        args = [text, like, status, status]
        base = f"from papers p left join notes n on n.paper_id = p.id where {where}"
        rows = self.store.rows(
            f"select p.data {base} order by p.added_on desc, p.total desc limit ? offset ?",
            [*args, limit, offset],
        )
        total = self.store.rows(f"select count(*) as n {base}", args)[0]["n"]
        return {"total": total, "papers": [lite(json.loads(r["data"])) for r in rows]}

    def paper(self, pid: str) -> dict[str, Any]:
        rows = self.store.rows("select data from papers where id = ?", [pid])
        if not rows:
            raise ApiError(404, "找不到这篇论文。")
        return json.loads(rows[0]["data"])

    def news(self, day: str) -> dict[str, Any]:
        rows = self.store.rows("select data from news where date = ?", [day])
        if not rows:
            raise ApiError(404, "这一天没有业界简报。")
        return json.loads(rows[0]["data"])

    # ---------- 笔记和反馈 ----------

    def save_note(self, pid: str, b: dict[str, Any]) -> dict[str, Any]:
        self.paper(pid)
        status = b.get("status") or "todo"
        if status not in STATUSES:
            raise ApiError(400, "阅读状态不对。")
        note = str(b.get("note") or "")
        if len(note) > 50_000:
            raise ApiError(413, "笔记太长了，拆成几段吧。")
        flags = (
            [str(f) for f in b.get("flags") or []][:12] if isinstance(b.get("flags"), list) else []
        )
        at = _now()
        self.store.run(
            "insert into notes (paper_id, status, note, flags, updated_at) values (?, ?, ?, ?, ?) "
            "on conflict(paper_id) do update set status = excluded.status, note = excluded.note, "
            "flags = excluded.flags, updated_at = excluded.updated_at",
            [pid, status, note, json.dumps(flags, ensure_ascii=False), at],
        )
        return {"paperId": pid, "status": status, "note": note, "flags": flags, "updatedAt": at}

    def feedback(self, pid: str, b: dict[str, Any]) -> dict[str, Any]:
        self.paper(pid)
        action = b.get("action")
        if action not in ("like", "skip", "clear"):
            raise ApiError(400, "反馈类型不对。")
        self.store.run(
            "delete from feedback where paper_id = ? and action in ('like', 'skip')", [pid]
        )
        if action != "clear":
            self.store.run(
                "insert into feedback (paper_id, action, at) values (?, ?, ?)",
                [pid, action, _now()],
            )
        return {"paperId": pid, "action": None if action == "clear" else action}

    # ---------- Zotero ----------

    def _z(self) -> ZoteroLocalClient:
        if self.zotero is None or not self.zotero.api_key:
            raise ApiError(
                400,
                "还不能写入 Zotero：运行 robot-times local init，在 Zotero 弹窗里点 Always Allow。",
            )
        return self.zotero

    def _done(self, pid: str) -> str | None:
        rows = self.store.rows("select item_key from zotero_items where paper_id = ?", [pid])
        return rows[0]["item_key"] if rows else None

    def _folder(self, z: ZoteroLocalClient, name: str) -> str | None:
        if not name:
            return None
        key = z.find_collection(name)
        if key is None:
            raise ApiError(
                400,
                f"Zotero 里找不到名为“{name}”的文件夹。先在 Zotero 里建好，或者到“设置”里改成已有的文件夹名。",
            )
        return key

    def _pdf_attachment(self, z: ZoteroLocalClient, key: str) -> dict[str, Any] | None:
        for c in z.children(key):
            d = c.get("data", {})
            if d.get("itemType") == "attachment" and d.get("contentType") == "application/pdf":
                return c
        return None

    def _attach_pdf(self, z: ZoteroLocalClient, parent: str, p: dict[str, Any]) -> str | None:
        """下载 arXiv 的 PDF，存进本机 Zotero。返回失败原因，成功返回 None。"""
        if not p.get("pdf"):
            return "这篇没有 PDF 地址"
        try:
            r = self.http.get(p["pdf"], headers={"User-Agent": PDF_UA, "Accept": "application/pdf"})
        except httpx.HTTPError as e:
            return f"下载 PDF 失败（{type(e).__name__}）"
        if r.status_code != 200:
            return f"下载 PDF 失败（HTTP {r.status_code}）"
        if "pdf" not in r.headers.get("content-type", "").lower():
            return "下载到的不是 PDF"
        if len(r.content) > MAX_PDF:
            return "PDF 超过 40 MB，没有存"
        filename = re.sub(r"[^\w.-]+", "_", str(p["id"])) + ".pdf"
        try:
            att = z.create_items(
                [
                    {
                        "itemType": "attachment",
                        "parentItem": parent,
                        "linkMode": "imported_url",
                        "title": "Full Text PDF",
                        "url": p["pdf"],
                        "accessDate": _now(),
                        "contentType": "application/pdf",
                        "charset": "",
                        "filename": filename,
                        "tags": [],
                    }
                ]
            )[0]
            z.upload_file(att, r.content, filename)
        except ZoteroError as e:
            return str(e)
        return None

    def zotero_add(self, pid: str) -> dict[str, Any]:
        z = self._z()
        p = self.paper(pid)
        settings = repo.load_settings(self.store)
        note_rows = self.store.rows("select note from notes where paper_id = ?", [pid])
        folder = str(settings.get("zoteroCollection") or "").strip()
        col = self._folder(z, folder)
        done = self._done(pid)
        existing = z.item(done) if done else None
        if existing:  # 以前加过：补上文件夹和 PDF
            cols = existing.get("data", {}).get("collections") or []
            if col and col not in cols:
                z.update_item(existing, {"collections": [*cols, col]})
            pdf_error = None
            if self.pdf and self._pdf_attachment(z, existing["key"]) is None:
                pdf_error = self._attach_pdf(z, existing["key"], p)
            return {"paperId": pid, "key": existing["key"], "existing": True, "folder": folder,
                    "pdfError": pdf_error, "pdf": self.pdf}  # fmt: skip
        if done:
            self.store.run("delete from zotero_items where paper_id = ?", [pid])
        tag = settings.get("tag") or "Robot Times"
        key = z.create_items([{**paper_item(p, tag), "collections": [col] if col else []}])[0]
        at = _now()
        self.store.run(
            "insert into zotero_items (paper_id, item_key, at) values (?, ?, ?) "
            "on conflict(paper_id) do nothing",
            [pid, key, at],
        )
        self.store.run(
            "insert into feedback (paper_id, action, at) values (?, 'add', ?)", [pid, at]
        )
        pdf_error = self._attach_pdf(z, key, p) if self.pdf else None
        note_error = None
        try:
            my_note = note_rows[0]["note"] if note_rows else ""
            z.create_items([{"itemType": "note", "parentItem": key,
                             "note": note_html(p, my_note), "tags": [{"tag": tag}]}])  # fmt: skip
        except ZoteroError as e:
            note_error = str(e)
        return {"paperId": pid, "key": key, "folder": folder, "pdfError": pdf_error,
                "noteError": note_error, "pdf": self.pdf}  # fmt: skip

    def zotero_check(self, pid: str) -> dict[str, Any]:
        z = self._z()
        done = self._done(pid)
        if not done:
            return {"paperId": pid, "key": None}
        it = z.item(done)
        if it is None:
            self.store.run("delete from zotero_items where paper_id = ?", [pid])
            return {"paperId": pid, "key": None, "gone": True}
        folder = str(repo.load_settings(self.store).get("zoteroCollection") or "").strip()
        in_folder = True
        if folder:
            key = z.find_collection(folder)
            in_folder = key is None or key in (it.get("data", {}).get("collections") or [])
        pdf = (self._pdf_attachment(z, done) is not None) if self.pdf else None
        return {"paperId": pid, "key": done, "web": f"zotero://select/library/items/{done}",
                "pdf": pdf, "inFolder": in_folder, "folder": folder}  # fmt: skip

    def zotero_remove(self, pid: str) -> dict[str, Any]:
        z = self._z()
        done = self._done(pid)
        if done:
            z.trash(done)
        self.store.run("delete from zotero_items where paper_id = ?", [pid])
        self.store.run("delete from feedback where paper_id = ? and action = 'add'", [pid])
        return {"paperId": pid, "key": None}

    # ---------- 问 AI ----------

    def ask(self, b: dict[str, Any]) -> Reply:
        if self.llm_stream is None:
            raise ApiError(
                400,
                "还没配置大模型：在 ~/.robot-times/.env 里填 DEEPSEEK_API_KEY（或 ROBOT_TIMES_LLM_*）。",
            )
        p = self.paper(str(b.get("paperId") or ""))
        settings = repo.load_settings(self.store)
        prompt = ask_prompt(
            p,
            settings.get("interests", ""),
            str(b.get("kind") or ""),
            str(b.get("question") or "")[:2000],
            str(b.get("context") or "")[:60_000],
            str(b.get("note") or "")[:10_000],
        )
        messages = [
            {"role": "system", "content": style_for(get_preset(settings))},
            {"role": "user", "content": prompt},
        ]
        return Reply(stream=self.llm_stream(messages))


def stream_chat(llm_base: str, api_key: str, model: str, http: httpx.Client | None = None):
    """返回一个函数：把消息发给 OpenAI 兼容接口（stream=true），原样转发服务端推送的数据。"""
    client = http or httpx.Client(timeout=httpx.Timeout(30.0, read=300.0))

    def run(messages: list[dict[str, str]]) -> Iterator[bytes]:
        body = {"model": model, "stream": True, "temperature": 0.3, "messages": messages}
        req = client.build_request(
            "POST",
            f"{llm_base.rstrip('/')}/chat/completions",
            json=body,
            headers={"Authorization": f"Bearer {api_key}"},
        )
        try:
            resp = client.send(req, stream=True)
        except httpx.HTTPError as e:
            raise ApiError(502, f"连不上大模型：{type(e).__name__}") from None
        if resp.status_code != 200:  # 先看状态码，出错时看板能显示原因
            resp.close()
            msg = {401: "大模型拒绝了请求：检查密钥。", 402: "大模型账户余额不足。",
                   429: "大模型请求太频繁，稍后再试。"}.get(resp.status_code)  # fmt: skip
            raise ApiError(
                502 if resp.status_code != 429 else 503,
                msg or f"大模型出错了：HTTP {resp.status_code}",
            )

        def chunks() -> Iterator[bytes]:
            try:
                yield from resp.iter_bytes()
            finally:
                resp.close()

        return chunks()

    return run


def _unquote(s: str) -> str:
    from urllib.parse import unquote

    return unquote(s)


def _int(v: str | None, default: int) -> int:
    try:
        return int(v or default)
    except ValueError:
        return default


def _now() -> str:
    return repo.now_iso().replace("+00:00", "Z")
