"""业界新闻和深度文章的来源。

kind：
  rss    RSS / Atom（Substack 的地址都是 https://<名字>.substack.com/feed）
  html   没有 RSS 的网站：抓列表页，按 pattern 匹配文章链接
  aihot  AIHOT 的 JSON 接口（https://aihot.news/openapi-v1.json）
  hn     Hacker News（Algolia 搜索接口），只收分数够高的
type：news = 快讯，deep = 深度文章
group：robotics = 机器人方向，ai = AI 行业；方向预设的 news_groups 决定用哪几组（见 presets.py）

注意：AIHOT 只允许个人非商业使用免费调用；做成收费产品前要先取得授权（见 https://aihot.news/terms）。
新增来源也可以不改代码：在看板“设置”里填 RSS 地址。
"""

from __future__ import annotations

from typing import Any

SOURCES: list[dict[str, Any]] = [
    # ---------- 快讯：机器人和具身智能 ----------
    {"id": "robotreport", "name": "The Robot Report", "kind": "rss", "type": "news",
     "url": "https://www.therobotreport.com/feed/"},
    {"id": "spectrum", "name": "IEEE Spectrum", "kind": "rss", "type": "news",
     "url": "https://spectrum.ieee.org/feeds/topic/robotics.rss"},
    {"id": "tc-robotics", "name": "TechCrunch", "kind": "rss", "type": "news",
     "url": "https://techcrunch.com/category/robotics/feed/"},
    {"id": "nvidia-robotics", "name": "NVIDIA 技术博客", "kind": "rss", "type": "news",
     "url": "https://developer.nvidia.com/blog/category/robotics/feed/"},
    {"id": "hn", "name": "Hacker News", "kind": "hn", "type": "news",
     "url": "https://hn.algolia.com/api/v1/search_by_date",
     "queries": ["humanoid", "robotics", "robot", "embodied AI"], "min_points": 40},
    # ---------- 快讯：AI 行业 ----------
    {"id": "aihot", "name": "AIHOT", "kind": "aihot", "type": "news",
     "url": "https://aihot.news/api/v1/items"},
    {"id": "tc-ai", "name": "TechCrunch", "kind": "rss", "type": "news",
     "url": "https://techcrunch.com/category/artificial-intelligence/feed/"},
    {"id": "verge-ai", "name": "The Verge", "kind": "rss", "type": "news",
     "url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"},
    {"id": "mittr", "name": "MIT Technology Review", "kind": "rss", "type": "news",
     "url": "https://www.technologyreview.com/feed/"},
    {"id": "deepmind", "name": "Google DeepMind", "kind": "rss", "type": "news",
     "url": "https://deepmind.google/blog/rss.xml"},
    {"id": "openai", "name": "OpenAI", "kind": "rss", "type": "news",
     "url": "https://openai.com/news/rss.xml"},
    {"id": "hf-blog", "name": "Hugging Face", "kind": "rss", "type": "news",
     "url": "https://huggingface.co/blog/feed.xml"},
    {"id": "qbitai", "name": "量子位", "kind": "rss", "type": "news", "lang": "zh",
     "url": "https://www.qbitai.com/feed"},
    # ---------- 深度：分析机构、投资人、研究者的长文 ----------
    {"id": "robostrategy", "name": "RoboStrategy", "kind": "html", "type": "deep",
     "url": "https://robostrategy.co/news/robostrategy-articles",
     "pattern": r"^https://robostrategy\.co/news/(?!robostrategy-articles$|media$|portfolio-updates$)[a-z0-9-]+$"},
    {"id": "itcanthink", "name": "It Can Think（Chris Paxton）", "kind": "rss", "type": "deep",
     "url": "https://itcanthink.substack.com/feed"},
    {"id": "robopapers", "name": "RoboPapers", "kind": "rss", "type": "deep",
     "url": "https://robopapers.substack.com/feed"},
    {"id": "robotsandstartups", "name": "Robots & Startups", "kind": "rss", "type": "deep",
     "url": "https://robotsandstartups.substack.com/feed"},
    {"id": "importai", "name": "Import AI（Jack Clark）", "kind": "rss", "type": "deep",
     "url": "https://importai.substack.com/feed"},
    {"id": "interconnects", "name": "Interconnects（Nathan Lambert）", "kind": "rss", "type": "deep",
     "url": "https://www.interconnects.ai/feed"},
    {"id": "semianalysis", "name": "SemiAnalysis", "kind": "rss", "type": "deep",
     "url": "https://newsletter.semianalysis.com/feed"},
    {"id": "brooks", "name": "Rodney Brooks", "kind": "rss", "type": "deep",
     "url": "https://rodneybrooks.com/feed/"},
    {"id": "gradient", "name": "The Gradient", "kind": "rss", "type": "deep",
     "url": "https://thegradient.pub/rss/"},
]  # fmt: skip


# 机器人方向的来源，其余都算 AI 行业
ROBOTICS = {"robotreport", "spectrum", "tc-robotics", "nvidia-robotics", "hn", "robostrategy",
            "itcanthink", "robopapers", "robotsandstartups", "brooks"}  # fmt: skip
for _s in SOURCES:
    _s.setdefault("group", "robotics" if _s["id"] in ROBOTICS else "ai")


def sources_for(groups: list[str]) -> list[dict[str, Any]]:
    """方向预设要的几组来源。"""
    return [s for s in SOURCES if s["group"] in groups]


def custom_sources(urls: list[str]) -> list[dict[str, Any]]:
    """设置里填的 RSS 地址，统一当作深度文章来源。"""
    out = []
    for i, url in enumerate(urls):
        url = str(url).strip()
        if url.startswith("https://") or url.startswith("http://"):
            host = url.split("/")[2]
            out.append(
                {"id": f"custom-{i}", "name": host, "kind": "rss", "type": "deep", "url": url}
            )
    return out
