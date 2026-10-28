"""用 LLM 精排并写中文摘要。接口兼容 OpenAI 的 /chat/completions，
所以可以指向 OpenAI、Anthropic 的兼容端点、DeepSeek，或本地 Ollama（http://localhost:11434/v1）。
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx

from ..models import ScoredPaper
from ..presets import DEFAULT, PRESETS, get_preset
from .style import STYLE, style_for

TOPICS = PRESETS[DEFAULT]["topics"]  # 默认预设的方向标签（兼容旧代码）
BATCH = 5  # 每批 5 篇：输出短一些，不容易被截断
WORKERS = 4  # 并发请求数

PROMPT = """他的研究兴趣：{interests}

下面是 {n} 篇今天的候选论文。每篇有编号、标题、摘要，以及他 Zotero 文献库里和它最接近的论文
（向量检索得到，仅供参考，不一定真的相关）。

逐篇输出：
- score：0–10 的整数，这篇对他值不值得读。
  9–10：正中他的方向，方法或结论他大概率用得上。一批里通常最多一两篇。
  7–8：同方向的扎实工作，值得看摘要和图。
  4–6：相邻方向，知道有这篇就行。
  0–3：和他的方向基本无关。
  要拉开差距，不要整体打高分。
- summary_zh：2–3 句，总共不超过 130 字。第一句说要解决什么问题（具体到场景），第二句用一句话说清核心 idea
  （不要罗列模块），第三句说结果（平台、仿真还是真机、对比基线、数字）。不要用“本文”“该论文”“针对……问题”开头。
- why_zh：1 句，不超过 60 字，对他说（用“你”）：能借鉴什么，或者和你库里哪篇是什么关系（同一问题的不同做法、
  可以接在后面、用了同样的平台或数据）。分数低就直说为什么不必读。
- tip_zh：1 句，不超过 60 字，怎么读：先看哪张图或哪一节，读的时候留意什么。score 低于 7 时写空字符串。
- topic：这篇主要属于哪个方向，从这些里选一个：{topics}。

写法示例（内容是虚构的，只示范写法）：
{{"id": "0000.00000", "score": 8,
 "summary_zh": "{ex_summary}",
 "why_zh": "{ex_why}",
 "tip_zh": "{ex_tip}"}}

只输出一个 JSON 对象：{{"papers": [每篇一个对象，字段为 id、score、summary_zh、why_zh、tip_zh、topic]}}，不要输出其他文字。

{papers}
"""


class LLMClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 480.0,  # 解读一篇论文要写三四千字，留足时间
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._http = httpx.Client(
            transport=transport,
            timeout=timeout,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    def complete(
        self,
        prompt: str,
        system: str = STYLE,
        temperature: float = 0.3,
        json_mode: bool = False,
        max_tokens: int | None = None,
    ) -> str:
        messages = [{"role": "system", "content": system}] if system else []
        messages.append({"role": "user", "content": prompt})
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens:
            body["max_tokens"] = max_tokens
        if json_mode:  # 让服务端保证输出是合法 JSON；不支持的服务会返回 400，那就去掉再试一次
            body["response_format"] = {"type": "json_object"}
        url = f"{self.base_url}/chat/completions"
        resp = self._http.post(url, json=body)
        if json_mode and resp.status_code == 400:
            body.pop("response_format")
            resp = self._http.post(url, json=body)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


def parse_json_array(text: str) -> list[dict[str, Any]]:
    """宽松解析：允许外面包着 ```json 代码块或前后有一句话。"""
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1)
    stripped = text.strip()
    if stripped.startswith("{"):  # {"papers": [...]} 形式
        obj = json.loads(stripped)
        for v in obj.values() if isinstance(obj, dict) else []:
            if isinstance(v, list):
                return [d for d in v if isinstance(d, dict)]
        raise ValueError("LLM 返回的对象里没有数组")
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1:
        raise ValueError("LLM 没有返回 JSON 数组")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, list):
        raise ValueError("LLM 返回的不是数组")
    return [d for d in data if isinstance(d, dict)]


def _paper_block(sp: ScoredPaper) -> str:
    p = sp.paper
    lines = [f"[{p.id}] {p.title}", p.abstract[:1500]]
    if p.comment:
        lines.append(f"作者备注：{p.comment[:200]}")
    if sp.related:
        lines.append("他库里最接近的：" + "；".join(f"《{t}》" for t in sp.related))
    return "\n".join(lines)


def _apply(
    by_id: dict[str, ScoredPaper], results: list[dict[str, Any]], topics: list[str] = TOPICS
) -> int:
    n = 0
    for r in results:
        sp = by_id.get(str(r.get("id", "")).strip("[] "))
        if sp is None:
            continue
        try:
            sp.llm_score = max(0.0, min(10.0, float(r.get("score", 0))))
        except (TypeError, ValueError):
            pass
        sp.summary_zh = str(r.get("summary_zh", "") or "")
        sp.why_zh = str(r.get("why_zh", "") or "")
        sp.tip_zh = str(r.get("tip_zh", "") or "")
        topic = str(r.get("topic", "") or "").strip()
        sp.topic = topic if topic in topics else ("其他" if topic else "")
        n += 1
    return n


def rerank(
    client: LLMClient,
    items: list[ScoredPaper],
    interests: str,
    errors: list[str] | None = None,
    preset: dict[str, Any] | None = None,
) -> list[ScoredPaper]:
    """就地补上 llm_score、summary_zh、why_zh、tip_zh、topic。失败的批次保持原样，原因记进 errors。

    preset：方向预设（写作人设、方向标签、写法示例），默认机器人。
    """
    p = preset or get_preset()
    topics = p["topics"]
    system = style_for(p)
    by_id = {sp.paper.id: sp for sp in items}
    batches = [items[i : i + BATCH] for i in range(0, len(items), BATCH)]

    def run(batch: list[ScoredPaper]) -> list[dict[str, Any]]:
        prompt = PROMPT.format(
            interests=interests or "（未填写，请按摘要质量判断）",
            n=len(batch),
            topics="、".join(topics),
            ex_summary=p["example"]["summary_zh"],
            ex_why=p["example"]["why_zh"],
            ex_tip=p["example"]["tip_zh"],
            papers="\n\n".join(_paper_block(sp) for sp in batch),
        )
        return parse_json_array(client.complete(prompt, system=system, json_mode=True))

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(run, b) for b in batches]
        for b, f in zip(batches, futures, strict=True):
            try:
                _apply(by_id, f.result(), topics)
            except Exception as e:  # 一批失败不影响其他批
                if errors is not None:
                    errors.append(
                        f"{b[0].paper.id} 等 {len(b)} 篇：{type(e).__name__} {str(e)[:150]}"
                    )
    return items


def parse_json_object(text: str) -> dict[str, Any]:
    """宽松解析一个 JSON 对象（允许 ```json 代码块或前后多一句话）。"""
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1:
        raise ValueError(f"LLM 没有返回 JSON 对象（返回了 {len(text)} 个字符）")
    body = text[start : end + 1] if end > start else text[start:]
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        # 长文里偶尔有没转义的引号，或者输出被截断：尽量修好再读
        from json_repair import repair_json

        data = repair_json(body, return_objects=True)
    if not isinstance(data, dict) or not data:
        raise ValueError("LLM 返回的不是对象")
    return data
