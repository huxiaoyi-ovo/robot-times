"""方向预设：把和研究方向有关的东西集中在一处，换方向只改设置里的 preset。

每个预设包括：报名、默认 arXiv 分类、写作人设里的领域和术语示例、给大模型的读者画像、
论文的方向标签（多样性用）、精排的写法示例、业界简报关注的行业和分类、新闻来源分组。
看板的“问 AI”用同样的人设（web/app/src/presets.js），tests/test_prompts.py 检查两边一致。
"""

from __future__ import annotations

from typing import Any

from .i18n import norm

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

# 英文版：设置里 lang 是 en 时，盖在上面的中文字段上（正则、分类、来源这些不分语言的不用写）
PRESETS_EN: dict[str, dict[str, Any]] = {
    "robotics": {
        "label": "Robotics & embodied AI",
        "field": "robot learning",
        "terms": "whole-body control, sim2real, VLA, diffusion policy, ablation, teleoperation, loco-manipulation",
        "evidence": "which robot platform, simulation or real hardware, which baseline, and by how much",
        "limits": "simulation only, a single robot, no ablation, no strong baseline",
        "reader": "knows the basics of RL, imitation learning and whole-body control",
        "topics": ["Humanoids", "Legged robots", "Manipulation", "VLA & robot foundation models",
                   "Navigation & perception", "World models & simulation", "Drones & other platforms", "Other"],
        "topic_words": [
            ("Humanoids", r"humanoid|whole-body control|biped"),
            ("Legged robots", r"quadruped|legged|hexapod|locomotion|gait"),
            ("Drones & other platforms", r"quadrotor|\bdrone|\buav\b|aerial|underwater|autonomous driving|vehicle"),
            ("VLA & robot foundation models", r"vision-language-action|\bvla\b|foundation model|embodied brain|large language"),
            ("Manipulation", r"manipulat|grasp|dexterous|\barm\b|bimanual"),
            ("Navigation & perception", r"navigat|traversab|slam|mapping|perception|following"),
            ("World models & simulation", r"world model|simulat|sim2real|sim-to-real"),
        ],
        "example": {
            "summary_zh": "Quadrupeds fall on stairs mostly because foothold estimates are off. The authors turn the "
            "height map into a foothold probability map and feed it straight to the policy, dropping the "
            "plan-then-track pipeline. On a real Unitree Go2, stair success rises from 62% to 91%, mostly on the way down.",
            "why_zh": "Same problem as the legged sim2real paper in your library; the foothold-probability "
            "representation could drop straight into your terrain adaptation.",
            "tip_zh": "Start with the observation design in Fig. 3 and the ablation in Table 2; the real robot was only tested on stairs.",
        },
        "news_focus": "robotics, embodied AI and the AI industry",
        "news_categories": ["Embodied AI", "Robotics industry", "AI models & products", "AI business & funding",
                            "Policy & safety", "Research"],
        "news_fallback": ("Robotics industry", "AI business & funding"),
    },
    "general": {
        "label": "General (machine learning and other fields)",
        "field": "this field",
        "terms": "method names, model names, dataset names and the field's common abbreviations",
        "evidence": "which datasets and setup, which baseline, and by how much",
        "limits": "small datasets only, no ablation, no strong baseline, best run only",
        "reader": "knows the basics of the field",
        "topics": ["New methods", "Theory", "Applications", "Data & evaluation", "Systems & tools",
                   "Surveys & position papers", "Other"],
        "topic_words": [
            ("Surveys & position papers", r"\bsurvey\b|\breview\b|position paper|perspective"),
            ("Data & evaluation", r"benchmark|dataset|evaluation"),
            ("Theory", r"theorem|\bbound\b|convergence|provabl"),
            ("Systems & tools", r"library|framework|toolkit|system for|open-source"),
        ],
        "example": {
            "summary_zh": "In long-document QA, the more passages you retrieve, the more the model gets pulled off "
            "course by irrelevant text. The authors first have the model score each passage for usefulness, then "
            "pass only the high scorers to the answering model. It gains 4.1 points on average across three "
            "long-document QA benchmarks, at the cost of one extra inference pass.",
            "why_zh": "Same problem as the retrieval-augmented paper in your library; this filter-then-answer step is easy to try.",
            "tip_zh": "Start with the main results in Table 2 and the passage-count ablation in Fig. 4; no comparison with stronger rerankers.",
        },
        "news_focus": "the AI industry",
        "news_categories": ["AI models & products", "AI business & funding", "Policy & safety", "Research"],
        "news_fallback": ("AI models & products", "AI business & funding"),
    },
}  # fmt: skip


# 设置里可以单独覆盖的字段：报名、领域名（写进人设）
OVERRIDES = {"siteName": "site_name", "field": "field"}


def get_preset(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """按设置里的 preset 取预设（没设或写错就用默认的机器人预设），按 lang 换成对应语言，
    再套上设置里单独改过的字段。返回的预设带 lang，提示词按它选语言。"""
    settings = settings or {}
    name = settings.get("preset") if settings.get("preset") in PRESETS else DEFAULT
    lang = norm(settings.get("lang"))
    p = dict(PRESETS[name])
    if lang == "en":
        p.update(PRESETS_EN[name])
    p["lang"] = lang
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

    from . import __version__
    from .llm.style import TEMPLATES

    data = {k: {f: p[f] for f in JS_FIELDS} for k, p in PRESETS.items()}
    data_en = {k: {f: p[f] for f in JS_FIELDS if f in p} for k, p in PRESETS_EN.items()}
    return (
        "// 自动生成，不要手改：改 core/src/robot_times/presets.py 或 llm/style.py 后运行\n"
        "//   uv run python -m robot_times.presets > web/app/src/presets.js\n"
        "// （Worker 主文件只能导出处理函数，所以放在单独的模块里；tests/test_prompts.py 检查两边一致）\n"
        f"export const PRESETS = {json.dumps(data, ensure_ascii=False, indent=2)};\n"
        f"export const PRESETS_EN = {json.dumps(data_en, ensure_ascii=False, indent=2)};\n"
        f'export const DEFAULT_PRESET = "{DEFAULT}";\n'
        f'export const VERSION = "{__version__}"; // 来自 core/pyproject.toml\n'
        f"const TEMPLATES = {json.dumps(TEMPLATES, ensure_ascii=False, indent=2)};\n\n"
        "// 和 Python 的 i18n.norm 一样：en 开头的是英文，其他都当中文\n"
        'export const langOf = v => (String(v || "").trim().toLowerCase().startsWith("en") ? "en" : "zh");\n\n'
        "// 和 Python 的 get_preset 一样：按设置取预设，按 lang 换语言，再套上设置里单独改过的报名和领域名\n"
        "export function presetFor(settings = {}) {\n"
        "  const name = PRESETS[settings.preset] ? settings.preset : DEFAULT_PRESET;\n"
        "  const lang = langOf(settings.lang);\n"
        '  const p = { ...PRESETS[name], ...(lang === "en" ? PRESETS_EN[name] : {}), lang };\n'
        f"  for (const [key, field] of Object.entries({json.dumps(OVERRIDES)})) {{\n"
        '    const v = String(settings[key] || "").trim();\n'
        "    if (v) p[field] = v;\n"
        "  }\n"
        "  return p;\n"
        "}\n\n"
        "export function styleFor(preset) {\n"
        "  const t = TEMPLATES[preset.lang] || TEMPLATES.zh;\n"
        "  return t.replace(/\\{(field|terms|evidence|limits)\\}/g, (_, k) => preset[k]);\n"
        "}\n"
    )


if __name__ == "__main__":
    print(js_module(), end="")
