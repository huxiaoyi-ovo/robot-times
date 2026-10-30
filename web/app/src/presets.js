// 自动生成，不要手改：改 core/src/robot_times/presets.py 或 llm/style.py 后运行
//   uv run python -m robot_times.presets > web/app/src/presets.js
// （Worker 主文件只能导出处理函数，所以放在单独的模块里；tests/test_prompts.py 检查两边一致）
export const PRESETS = {
  "robotics": {
    "label": "机器人与具身智能",
    "site_name": "The Robot Times",
    "categories": [
      "cs.RO"
    ],
    "field": "机器人学习",
    "terms": "whole-body control、sim2real、VLA、diffusion policy、ablation、teleoperation、loco-manipulation",
    "evidence": "什么机器人平台、仿真还是真机、和哪个基线比、高了多少",
    "limits": "只在仿真、只有一种机器人、没有 ablation、没和强基线比"
  },
  "general": {
    "label": "通用（机器学习等其他方向）",
    "site_name": "The Preprint Times",
    "categories": [
      "cs.LG"
    ],
    "field": "这个方向",
    "terms": "方法名、模型名、数据集名和领域里通用的缩写",
    "evidence": "用的什么数据集和实验设置、和哪个基线比、高了多少",
    "limits": "只在小数据集上、没有 ablation、没和强基线比、只报了最好的一次结果"
  }
};
export const DEFAULT_PRESET = "robotics";
export const VERSION = "0.1.1"; // 来自 core/pyproject.toml
const TEMPLATE = "你是一位做{field}的资深研究者，在帮一位研究生朋友筛论文、读论文。写作要求：\n- 用中文，像同行之间说话：直接、具体、有判断。先说结论，再说依据。\n- 专业术语保留英文原词，不要硬翻：{terms} 等。\n- 用事实和数字说话：{evidence}。摘要里没有的数字不要编。\n- 不写空话：不用“本文提出了一种新颖的”“具有重要意义”“显著提升”“为……提供了新思路”“值得关注”这类话。\n- 有态度：值得读就说读哪部分、能借鉴什么；有明显局限（{limits}）就直说。\n- 只依据给你的信息；信息不够时说“摘要里没说”，不要猜。\n- 写出来的文字是直接给他本人看的，称呼他为“你”，不要用“他”“该研究者”。";

// 和 Python 的 get_preset 一样：按设置取预设，再套上设置里单独改过的报名和领域名
export function presetFor(settings = {}) {
  const p = { ...(PRESETS[settings.preset] || PRESETS[DEFAULT_PRESET]) };
  for (const [key, field] of Object.entries({"siteName": "site_name", "field": "field"})) {
    const v = String(settings[key] || "").trim();
    if (v) p[field] = v;
  }
  return p;
}

export function styleFor(preset) {
  return TEMPLATE.replace(/\{(field|terms|evidence|limits)\}/g, (_, k) => preset[k]);
}
