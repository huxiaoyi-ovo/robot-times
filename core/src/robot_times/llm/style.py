"""所有大模型调用共用的写作风格（作为 system 消息），按方向预设填进领域、术语和证据要求。

看板 Worker 的“问 AI”用的是同一个模板（web/app/src/presets.js 的 styleFor），改这里要同步改那里；
tests/test_prompts.py 会检查两边生成的文字一致。
"""

from __future__ import annotations

from typing import Any

from ..presets import PRESETS, get_preset

TEMPLATE = """你是一位做{field}的资深研究者，在帮一位研究生朋友筛论文、读论文。写作要求：
- 用中文，像同行之间说话：直接、具体、有判断。先说结论，再说依据。
- 专业术语保留英文原词，不要硬翻：{terms} 等。
- 用事实和数字说话：{evidence}。摘要里没有的数字不要编。
- 不写空话：不用“本文提出了一种新颖的”“具有重要意义”“显著提升”“为……提供了新思路”“值得关注”这类话。
- 有态度：值得读就说读哪部分、能借鉴什么；有明显局限（{limits}）就直说。
- 只依据给你的信息；信息不够时说“摘要里没说”，不要猜。
- 写出来的文字是直接给他本人看的，称呼他为“你”，不要用“他”“该研究者”。"""


def style_for(preset: dict[str, Any] | str | None = None) -> str:
    p = PRESETS.get(preset, get_preset()) if isinstance(preset, str) or preset is None else preset
    return TEMPLATE.format(
        field=p["field"], terms=p["terms"], evidence=p["evidence"], limits=p["limits"]
    )


STYLE = style_for()  # 默认预设（机器人）的写作风格
