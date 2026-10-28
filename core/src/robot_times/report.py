"""把一次推荐结果存成 JSON（给程序用）和 Markdown（给你勾选评测用）。

评测方法：在 Markdown 里把你真会读的论文从 `- [ ]` 改成 `- [x]`，
然后运行 `robot-times eval <文件>`，看前 5 / 10 / 20 篇里命中了几篇。
这就是调推荐算法时最重要的数据。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .models import ScoredPaper
from .pipeline import total_score

_LINE = re.compile(r"^- \[( |x|X)\] \*\*[\d.]+\*\* .*`(?P<id>[^`]+)`")


def save_run(
    scored: list[ScoredPaper], out_dir: Path, stem: str, weights: dict[str, float]
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{stem}.json"
    md_path = out_dir / f"{stem}.md"
    json_path.write_text(
        json.dumps([sp.to_dict() for sp in scored], ensure_ascii=False, indent=1), encoding="utf-8"
    )
    md_path.write_text(render_markdown(scored, stem, weights), encoding="utf-8")
    return json_path, md_path


def load_run(json_path: Path) -> list[ScoredPaper]:
    data = json.loads(Path(json_path).read_text(encoding="utf-8"))
    return [ScoredPaper.from_dict(d) for d in data]


def render_markdown(scored: list[ScoredPaper], title: str, weights: dict[str, float]) -> str:
    lines = [
        f"# The Robot Times 推荐 {title}",
        "",
        "把你真会读的论文改成 `- [x]`，然后运行 `robot-times eval` 这个文件。",
        "",
    ]
    for sp in scored:
        p = sp.paper
        parts = [f"相关 {sp.relevance:.1f}"]
        if sp.llm_score is not None:
            parts.append(f"LLM {sp.llm_score:.0f}")
        lines.append(
            f"- [ ] **{total_score(sp, weights):.1f}** [{p.title}]({p.url}) `{p.id}`"
            f" <sub>{' · '.join(parts)}</sub>"
        )
        if sp.summary_zh:
            lines.append(f"    - {sp.summary_zh}")
        if sp.why_zh:
            lines.append(f"    - 为什么：{sp.why_zh}")
    return "\n".join(lines) + "\n"


@dataclass
class EvalResult:
    total: int
    picked: int
    hits_at: dict[int, int]

    def describe(self) -> str:
        rows = [f"一共 {self.total} 篇，你勾选了 {self.picked} 篇。"]
        for k, hits in self.hits_at.items():
            if k <= self.total:
                rows.append(f"前 {k} 篇里命中 {hits} 篇（{hits / k:.0%}）")
        return "\n".join(rows)


def evaluate_markdown(md_path: Path, ks: tuple[int, ...] = (5, 10, 20)) -> EvalResult:
    marks: list[bool] = []
    for line in Path(md_path).read_text(encoding="utf-8").splitlines():
        m = _LINE.match(line)
        if m:
            marks.append(line[3] in "xX")
    return EvalResult(
        total=len(marks),
        picked=sum(marks),
        hits_at={k: sum(marks[:k]) for k in ks},
    )
