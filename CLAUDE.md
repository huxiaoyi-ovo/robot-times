# 给 Claude 的项目说明

The Robot Times：帮研究生每天从 arXiv 和社区热度里筛出该读的论文、写解读，写回 Zotero；另有业界简报和周报。
开源项目，两种部署：本地版和云端版。整体设计见 `docs/architecture.md`。

## 约定

- Python 用 uv workspace（`core/`）；看板是 `web/app/`（Cloudflare Worker，测试 `npm test`）。命令：`uv run pytest`、`uv run ruff check .`、`uv run ruff format .`。提交前三项都要通过。
- 面向用户的文案中英文都要有（设置里的 lang），写法直白；代码注释可以中文。
  - Python：`i18n.L("中文", "English")`，按当前语言选（本地看板按请求的 X-Lang，后台任务按设置）。
  - 页面 `index.html`：`t("中文")`，英文写进同一文件里的 `EN` 表；占位写成 `{n}`。
  - Worker `index.js`：`L("中文", "English")`；Zotero 笔记、问 AI 按设置里的 lang。
  - 生成内容的提示词（精排、解读、早报导语、周报、业界简报、问 AI）各有英文版，方向预设的英文字段在 `presets.py` 的 `PRESETS_EN`。
  - `tests/test_i18n.py` 检查英文路径里不混进中文。
- 外部服务（Zotero、arXiv、LLM）在测试里一律用 `httpx.MockTransport` 或假对象，测试不能联网。
- arXiv API 每 3 秒最多 1 次请求；arXiv 的 PDF 不要转存到我们的服务器。
- 隐私边界：云端只存兴趣画像和论文 ID，PDF 和笔记不离开用户电脑。
- 产品名里不要直接用 “Zotero”（商标），可以写 “for Zotero”。
- 和研究方向有关的东西（报名、写作人设、方向标签、新闻来源）放在 `core/src/robot_times/presets.py`，不要写死在别处；
  改了预设或 `llm/style.py` 要重新生成 `web/app/src/presets.js`（`uv run python -m robot_times.presets > web/app/src/presets.js`）。
- 看板接口有两份实现：云端 `web/app/src/index.js`（Worker）和本地 `core/src/robot_times/local/api.py`，页面共用 `web/app/public/index.html`。
  改接口或返回的字段要两边一起改，各自补测试。
- Git 提交一律用仓库所有者的身份（huxiaoyi-ovo，GitHub 隐私邮箱），提交信息里不加 Co-Authored-By 或其他署名行。
- 不要把个人信息（姓名、城市、数据库 ID、仓库地址、Zotero 文件夹名）写进代码默认值，放进设置或部署变量。
