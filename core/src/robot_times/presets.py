"""方向预设：把和研究方向有关的东西集中在一处，换方向只改设置里的 preset。

每个预设包括：报名、默认 arXiv 分类、写作人设里的领域和术语示例、给大模型的读者画像、
论文的方向标签（多样性用）、精排的写法示例、业界简报关注的行业和分类、新闻来源分组。
看板的“问 AI”用同样的人设（web/app/src/presets.js），tests/test_prompts.py 检查两边一致。
"""

from __future__ import annotations

from typing import Any

PRESETS: dict[str, dict[str, Any]] = {
    "robotics": {
        "label": "机器人与具身智能",
        "site_name": "The Robot Times",
        "categories": ["cs.RO"],
        "field": "机器人学习",
        "terms": "whole-body control、sim2real、VLA、diffusion policy、ablation、teleoperation、loco-manipulation",
        "evidence": "什么机器人平台、仿真还是真机、和哪个基线比、高了多少",
        "limits": "只在仿真、只有一种机器人、没有 ablation、没和强基线比",
        "reader": "懂 RL、模仿学习、whole-body control 这些基础",
        "topics": ["人形机器人", "足式与多足", "机械臂操作", "VLA 与机器人大模型", "导航与感知",
                   "世界模型与仿真", "无人机与其他平台", "其他"],
        "topic_words": [
            ("人形机器人", r"humanoid|whole-body control|biped"),
            ("足式与多足", r"quadruped|legged|hexapod|locomotion|gait"),
            ("无人机与其他平台", r"quadrotor|\bdrone|\buav\b|aerial|underwater|autonomous driving|vehicle"),
            ("VLA 与机器人大模型", r"vision-language-action|\bvla\b|foundation model|embodied brain|large language"),
            ("机械臂操作", r"manipulat|grasp|dexterous|\barm\b|bimanual"),
            ("导航与感知", r"navigat|traversab|slam|mapping|perception|following"),
            ("世界模型与仿真", r"world model|simulat|sim2real|sim-to-real"),
        ],
        "example": {
            "summary_zh": "四足机器人上下楼梯时，落脚点估计不准是摔倒的主要原因。作者把高度图转成落脚点概率图，"
            "直接作为策略的观测，省掉了先规划再跟踪的两段式结构。Unitree Go2 真机上楼梯成功率从 62% 提到 91%，"
            "提升主要来自下楼梯。",
            "why_zh": "和你库里那篇足式 sim2real 是同一个问题，落脚点概率这个表示可以直接搬到你的地形适应里。",
            "tip_zh": "先看 Fig. 3 的观测设计和 Table 2 的 ablation；真机只测了楼梯一种地形。",
        },
        "news_focus": "机器人、具身智能和 AI 行业",
        "news_categories": ["具身智能", "机器人产业", "AI 模型与产品", "AI 产业与资本", "政策与安全", "研究"],
        "news_groups": ["robotics", "ai"],
        "news_keywords": r"robot|humanoid|embodied|manipulat|locomot|quadruped|unitree|figure|agility|"
        r"boston dynamics|tesla optimus|机器人|具身|人形|灵巧手|宇树|智元|vla|world model|世界模型",
        "news_fallback": ("机器人产业", "AI 产业与资本"),
    },
    "general": {
        "label": "通用（机器学习等其他方向）",
        "site_name": "The Preprint Times",
        "categories": ["cs.LG"],
        "field": "这个方向",
        "terms": "方法名、模型名、数据集名和领域里通用的缩写",
        "evidence": "用的什么数据集和实验设置、和哪个基线比、高了多少",
        "limits": "只在小数据集上、没有 ablation、没和强基线比、只报了最好的一次结果",
        "reader": "懂这个领域的基础知识",
        "topics": ["新方法", "理论分析", "应用", "数据与评测", "系统与工具", "综述与观点", "其他"],
        "topic_words": [
            ("综述与观点", r"\bsurvey\b|\breview\b|position paper|perspective"),
            ("数据与评测", r"benchmark|dataset|evaluation"),
            ("理论分析", r"theorem|\bbound\b|convergence|provabl"),
            ("系统与工具", r"library|framework|toolkit|system for|open-source"),
        ],
        "example": {
            "summary_zh": "长文档问答里，检索到的段落越多，模型越容易被无关内容带偏。作者先让模型给每段打一个"
            "“有没有用”的分，再只把高分段落交给回答模型。在三个长文档问答基准上平均提高 4.1 个点，代价是多一次推理。",
            "why_zh": "和你库里那篇检索增强的工作是同一个问题，这个先筛后答的做法可以直接试。",
            "tip_zh": "先看 Table 2 的主结果和 Fig. 4 的段落数消融；没有和更强的重排模型比。",
        },
        "news_focus": "AI 行业",
        "news_categories": ["AI 模型与产品", "AI 产业与资本", "政策与安全", "研究"],
        "news_groups": ["ai"],
        "news_keywords": r"\bai\b|model|llm|gpt|claude|gemini|openai|anthropic|deepmind|大模型|人工智能",
        "news_fallback": ("AI 模型与产品", "AI 产业与资本"),
    },
}  # fmt: skip

DEFAULT = "robotics"


# 设置里可以单独覆盖的字段：报名、领域名（写进人设）
OVERRIDES = {"siteName": "site_name", "field": "field"}


def get_preset(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """按设置里的 preset 取预设（没设或写错就用默认的机器人预设），再套上设置里单独改过的字段。"""
    settings = settings or {}
    p = dict(PRESETS.get(settings.get("preset") or DEFAULT, PRESETS[DEFAULT]))
    for key, field in OVERRIDES.items():
        if str(settings.get(key) or "").strip():
            p[field] = str(settings[key]).strip()
    return p


# 看板 Worker 用到的字段（“问 AI”的写作人设、报名、默认分类）
JS_FIELDS = ("label", "site_name", "categories", "field", "terms", "evidence", "limits")


def js_module() -> str:
    """生成 web/app/src/presets.js。改了预设或写作模板后运行：

    uv run python -m robot_times.presets > web/app/src/presets.js
    """
    import json

    from .llm.style import TEMPLATE

    data = {k: {f: p[f] for f in JS_FIELDS} for k, p in PRESETS.items()}
    return (
        "// 自动生成，不要手改：改 core/src/robot_times/presets.py 或 llm/style.py 后运行\n"
        "//   uv run python -m robot_times.presets > web/app/src/presets.js\n"
        "// （Worker 主文件只能导出处理函数，所以放在单独的模块里；tests/test_prompts.py 检查两边一致）\n"
        f"export const PRESETS = {json.dumps(data, ensure_ascii=False, indent=2)};\n"
        f'export const DEFAULT_PRESET = "{DEFAULT}";\n'
        f"const TEMPLATE = {json.dumps(TEMPLATE, ensure_ascii=False)};\n\n"
        "// 和 Python 的 get_preset 一样：按设置取预设，再套上设置里单独改过的报名和领域名\n"
        "export function presetFor(settings = {}) {\n"
        "  const p = { ...(PRESETS[settings.preset] || PRESETS[DEFAULT_PRESET]) };\n"
        f"  for (const [key, field] of Object.entries({json.dumps(OVERRIDES)})) {{\n"
        '    const v = String(settings[key] || "").trim();\n'
        "    if (v) p[field] = v;\n"
        "  }\n"
        "  return p;\n"
        "}\n\n"
        "export function styleFor(preset) {\n"
        "  return TEMPLATE.replace(/\\{(field|terms|evidence|limits)\\}/g, (_, k) => preset[k]);\n"
        "}\n"
    )


if __name__ == "__main__":
    print(js_module(), end="")
