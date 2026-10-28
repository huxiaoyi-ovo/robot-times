"""读 arXiv 的 HTML 版论文（https://arxiv.org/html/<id>）：正文分节的文字、图和表。

只在生成解读时临时读一次：正文交给大模型，不保存；图只记 arXiv 上的图片地址和图注，
看板直接从 arXiv 加载图片，不转存到我们的服务器。不是每篇论文都有 HTML 版（作者没交 LaTeX 源码、
或者转换失败），没有时返回 None，解读就只依据摘要。
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup, Tag

log = logging.getLogger("robot_times.arxiv_html")

BASE = "https://arxiv.org/html/"
UA = "robot-times/0.1 (personal paper digest)"
MIN_INTERVAL = 3.1  # arXiv 要求每 3 秒最多 1 次请求
TEXT_BUDGET = 30_000  # 交给大模型的正文最多多少字符
MAX_FIGURES = 24
SKIP_SECTIONS = re.compile(r"acknowledg|reference|bibliograph|致谢|contribution", re.I)
_WS = re.compile(r"\s+")


@dataclass
class Figure:
    id: str  # F1、F2……按出现顺序编号，给大模型挑图用
    kind: str  # figure / table
    label: str  # 论文里的编号，如 “Figure 3”“Table 2”
    caption: str
    images: list[str] = field(default_factory=list)  # arXiv 上的图片地址
    rows: list[list[str]] = field(default_factory=list)  # 表格的单元格文字（没有图片的表）

    def to_dict(self) -> dict:
        d = {"id": self.id, "kind": self.kind, "label": self.label, "caption": self.caption}
        if self.images:
            d["images"] = self.images
        if self.rows:
            d["rows"] = self.rows
        return d


@dataclass
class FullText:
    url: str
    sections: list[tuple[str, str]]  # (节标题, 正文)
    figures: list[Figure]

    def text(self, budget: int = TEXT_BUDGET) -> str:
        """按节拼成一段文字；超出预算时每节按比例截短，保证每节都有开头。"""
        lens = [len(body) for _, body in self.sections]
        cap = _water_fill(lens, budget)
        parts = []
        for (title, body), n in zip(self.sections, lens, strict=True):
            cut = body if n <= cap else body[:cap].rsplit(" ", 1)[0] + " ……（本节后面省略）"
            parts.append(f"## {title}\n{cut}")
        return "\n\n".join(parts)


def _water_fill(lens: list[int], budget: int) -> int:
    """找一个每节上限 cap，使 sum(min(len, cap)) 不超过 budget。"""
    if sum(lens) <= budget or not lens:
        return max(lens, default=0)
    lo, hi = 0, max(lens)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if sum(min(n, mid) for n in lens) <= budget:
            lo = mid
        else:
            hi = mid - 1
    return lo


def _clean(text: str) -> str:
    return _WS.sub(" ", text).strip()


def _text_of(node: Tag) -> str:
    return _clean(node.get_text(" "))


def _absolute(page_url: str, src: str) -> str:
    # 页面地址是 https://arxiv.org/html/2610.31608，图片写的是相对地址 2610.31608v1/fig1.png，
    # 和浏览器一样按页面所在目录解析：https://arxiv.org/html/2610.31608v1/fig1.png
    return urljoin(page_url, src)


def _table_rows(fig: Tag) -> list[list[str]]:
    table = fig.find("table")
    if not table:
        return []
    rows = []
    for tr in table.find_all("tr")[:16]:
        cells = [_text_of(td)[:60] for td in tr.find_all(["td", "th"])][:10]
        if any(cells):
            rows.append(cells)
    return rows


def parse(html: str, page_url: str) -> FullText | None:
    soup = BeautifulSoup(html, "html.parser")
    doc = soup.find("article", class_="ltx_document") or soup.find(class_="ltx_page_content")
    if doc is None:
        return None
    # 数学公式换成 LaTeX 原文，读起来比 MathML 拆出来的符号清楚
    for m in doc.find_all("math"):
        m.replace_with(f" {m.get('alttext', '')} ")
    for junk in doc.select(".ltx_note, .ltx_bibliography, .ltx_page_footer, nav, .ltx_authors"):
        junk.decompose()

    figures: list[Figure] = []
    for fig in doc.find_all("figure", class_=["ltx_figure", "ltx_table"]):
        if fig.find_parent("figure"):  # 子图算在外层的图里
            continue
        cap_el = fig.find("figcaption")
        caption = _text_of(cap_el) if cap_el else ""
        tag = cap_el.find(class_="ltx_tag") if cap_el else None
        label = _text_of(tag).rstrip(":：").strip() if tag else ""
        if label and caption.startswith(label):
            caption = caption[len(label) :].lstrip(" :：")
        images = [
            _absolute(page_url, img["src"])
            for img in fig.find_all("img")
            if img.get("src") and not img["src"].endswith(".svg")
        ][:4]
        kind = "table" if "ltx_table" in (fig.get("class") or []) else "figure"
        rows = _table_rows(fig) if kind == "table" and not images else []
        if not images and not rows:
            continue
        figures.append(
            Figure(
                id=f"F{len(figures) + 1}",
                kind=kind,
                label=label or ("Table" if kind == "table" else "Figure"),
                caption=caption[:600],
                images=images,
                rows=rows,
            )
        )
        if len(figures) >= MAX_FIGURES:
            break
    for fig in doc.find_all("figure"):
        fig.decompose()

    sections: list[tuple[str, str]] = []
    for sec in doc.find_all("section", class_="ltx_section", recursive=True):
        if sec.find_parent("section", class_="ltx_section"):
            continue
        h = sec.find(re.compile("^h[1-6]$"))
        title = _text_of(h) if h else ""
        if SKIP_SECTIONS.search(title):
            continue
        if h:
            h.extract()
        body = _text_of(sec)
        if len(body) > 80:
            sections.append((title or "（无标题）", body))
    if not sections:
        return None
    return FullText(url=page_url, sections=sections, figures=figures)


class ArxivHtml:
    """按 arXiv 的频率要求逐篇读取。"""

    def __init__(
        self, transport: httpx.BaseTransport | None = None, interval: float = MIN_INTERVAL
    ):
        self._http = httpx.Client(
            transport=transport, timeout=30.0, follow_redirects=True, headers={"User-Agent": UA}
        )
        self._interval = interval
        self._last = 0.0

    def get(self, arxiv_id: str) -> FullText | None:
        wait = self._last + self._interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        url = BASE + arxiv_id
        try:
            resp = self._http.get(url)
        except httpx.HTTPError as e:
            log.warning("读不到 %s 的 HTML 版：%s", arxiv_id, e)
            return None
        finally:
            self._last = time.monotonic()
        if resp.status_code != 200:
            log.info("%s 没有 HTML 版（HTTP %s）", arxiv_id, resp.status_code)
            return None
        try:
            return parse(resp.text, str(resp.url))
        except Exception as e:  # 页面结构变了不影响当天推荐
            log.warning("解析 %s 的 HTML 版失败：%s", arxiv_id, e)
            return None
