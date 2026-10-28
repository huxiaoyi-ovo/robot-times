"""影响力和热度两个分项（0–10 分）。

- 热度：Hugging Face Daily Papers 的点赞数，5–10 分；不在榜上记 5 分（中性）；拿不到数据时整项不参与打分。
- 影响力：先用 arXiv comments / journal_ref 里写明的发表处判断（如 “Accepted to ICRA 2027”），
  顶会顶刊 8 分，其他写明已发表的 6 分，没写的 5 分（中性）。
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from datetime import date

import httpx

from .models import Paper

HF_URL = "https://huggingface.co/api/daily_papers"

TOP_VENUES = [
    "Science Robotics", "IJRR", "T-RO", "TRO", "RA-L", "RAL", "ICRA", "IROS", "CoRL", "RSS",
    "Humanoids", "NeurIPS", "ICLR", "ICML", "CVPR", "ICCV", "ECCV", "AAAI", "IJCAI",
    "Science", "Nature",
]  # fmt: skip
_ACCEPTED = re.compile(r"\b(accepted|to appear|published|camera[- ]ready|oral|spotlight)\b", re.I)


def hf_upvotes(
    days: Iterable[date], transport: httpx.BaseTransport | None = None
) -> dict[str, int]:
    """拉取这些天的 Hugging Face Daily Papers，返回 {arXiv 编号: 点赞数}。"""
    out: dict[str, int] = {}
    errors: list[str] = []
    days = list(days)
    with httpx.Client(transport=transport, timeout=20.0, follow_redirects=True) as http:
        for d in days:
            try:
                resp = http.get(HF_URL, params={"date": d.isoformat()})
                resp.raise_for_status()
                items = resp.json()
            except (httpx.HTTPError, ValueError) as e:  # 某一天没有榜单或请求失败，跳过这一天
                errors.append(f"{d.isoformat()}: {type(e).__name__} {str(e)[:120]}")
                continue
            for item in items if isinstance(items, list) else []:
                paper = item.get("paper", {}) if isinstance(item, dict) else {}
                pid = str(paper.get("id", ""))
                if pid:
                    out[pid] = max(out.get(pid, 0), int(paper.get("upvotes", 0) or 0))
    if days and len(errors) == len(days):
        raise RuntimeError("；".join(errors))
    return out


def buzz_score(upvotes: int) -> float:
    """点赞数 → 5–10。不在榜上和 0 赞都算 5 分（中性，和影响力一样），约 200 赞满分，按对数增长。

    大部分论文不会上 Hugging Face 的榜，没上榜不代表不好，所以不扣分。
    """
    if upvotes <= 0:
        return 5.0
    return round(5.0 + 5.0 * min(1.0, math.log1p(upvotes) / math.log1p(200)), 1)


def venue_impact(paper: Paper) -> tuple[float, str | None]:
    """返回 (影响力分, 识别出的发表处)。"""
    text = " ".join(filter(None, [paper.comment, paper.venue]))
    if not text:
        return 5.0, None
    for v in TOP_VENUES:
        if re.search(rf"(?<![A-Za-z]){re.escape(v)}(?![A-Za-z])", text):
            if _ACCEPTED.search(text) or paper.venue:
                return 8.0, v
            return 6.0, v  # 只提到投稿，还没接收
    if paper.venue or _ACCEPTED.search(text):
        return 6.0, None
    return 5.0, None
