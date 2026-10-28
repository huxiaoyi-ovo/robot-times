---
name: robot-times-setup
description: Install, configure, upgrade or troubleshoot The Robot Times (daily arXiv paper digest for Zotero) — local version on this computer, or the cloud version on GitHub Actions + Cloudflare. Use when the user asks to set it up, says it doesn't work, wants to change research direction, or wants to move from local to cloud.
---

# The Robot Times：帮用户装好、配好、排错

The Robot Times 每天从 arXiv 挑出用户该读的论文，写中文解读，排进阅读计划，一键加入 Zotero；另有业界简报和周报。
你的任务是替用户把它装好并确认能用。**和用户说中文，写法直白；每做完一步说一句结果，不要大段解释原理。**

## 先问清楚（一次问完）

1. **用哪种**（默认本地版）：
   - 本地版：数据和看板都在这台电脑，开机自动启动，不需要任何云服务账号。电脑要开着才生成，手机看不了。
   - 云端版：每天自动生成，手机也能看。要 GitHub、Cloudflare 账号，大约 20 分钟，其中几步要用户自己在网页上点。
   - 已经在用本地版、想换云端：走下面的“本地版升级到云端版”。
2. **研究兴趣**：一两句话，越具体越好（例：“足式机器人运动控制、人形机器人全身控制、sim2real；不看纯视觉检测”）。
3. **方向**：机器人（默认，arXiv cs.RO）还是别的（要 arXiv 分类，如 cs.LG、cs.CV,cs.AI）。
4. **Zotero 文件夹**（可选）：“加入 Zotero”时放进哪个已有的文件夹。
5. **大模型**（可选，强烈建议）：有没有 DeepSeek 或其他 OpenAI 兼容接口的密钥。没有就只有英文摘要、没有解读和“问 AI”。

## 密钥的规矩

- **不要让用户把密钥发到聊天里。** 告诉用户自己写进文件或网页设置页；用户已经发了，就照用，但提醒他之后可以换一个。
- 本地版：密钥写在 `~/.robot-times/.env`（Windows：`%USERPROFILE%\.robot-times\.env`），一行 `DEEPSEEK_API_KEY=...`，权限 600。
  其他模型用 `ROBOT_TIMES_LLM_BASE_URL`、`ROBOT_TIMES_LLM_API_KEY`、`ROBOT_TIMES_LLM_MODEL`。
- 云端版：密钥只填进 GitHub 仓库的 Actions secrets 和 Cloudflare Worker 的 Variables and Secrets。
  可以用 `gh secret set 名字` / `npx wrangler secret put 名字`，让用户在终端里自己粘贴值。

## 本地版

前提：Zotero 10 或更新版本已打开，并在 设置 → 高级 里勾选了“允许此计算机上的其他应用程序与 Zotero 通信”。
检查：`curl -s http://localhost:23119/api/` 有返回就对了；连不上就请用户去勾选，再试。

用户自己在终端操作时，最简单的是一键脚本（会自己问问题）：
- Linux / macOS：在仓库根目录 `bash install.sh`
- Windows：`powershell -ExecutionPolicy ByPass -File install.ps1`

你来操作时（脚本的问答你没法回答），直接跑这几步，在仓库根目录：

```bash
uv --version || curl -LsSf https://astral.sh/uv/install.sh | sh     # Windows 见 docs/dev/local-setup.md
uv sync                                                            # 想要更准的推荐：uv sync --extra embed（1–2 GB）
uv run robot-times local init --interests "研究兴趣" [--zotero-folder 文件夹] [--preset general --categories cs.LG]
```

- `init` 会让 Zotero 弹窗，**提醒用户切到 Zotero 点 Always Allow**，否则会卡住或失败。
- `init` 默认设好开机自动启动、加快捷方式，并在后台启动看板。不想要：加 `--no-autostart`，之后用 `uv run robot-times local serve`。
- 写好 `.env` 之后要 `uv run robot-times local restart` 才生效。

确认能用：
1. `uv run robot-times doctor`：Zotero、写入权限、大模型都应该是 ✓。
2. `curl -s http://localhost:8765/api/job`：有 JSON 返回说明看板在运行；`running` 是 `daily` 表示正在生成今天的推荐（第一次要几分钟）。
3. 请用户打开 <http://localhost:8765>，等进度提示消失，确认看到今天的论文；点一篇“加入 Zotero”，确认 Zotero 里出现了条目、PDF 和笔记。

## 云端版

按 `docs/dev/cloud-setup.md` 一步步来。你能代劳的（用户已登录对应 CLI 时）：
- 建数据库并初始化：在 `web/app` 里 `npx wrangler d1 create robot-times`，把 ID 填进 `wrangler.jsonc`，再 `npx wrangler d1 migrations apply DB --remote`。
- 设 GitHub secrets：`gh secret set ZOTERO_USER_ID` 等（值让用户粘贴）。
- 手动跑第一次：`gh workflow run daily.yml`，再 `gh run watch`。

只能用户自己在网页上做的：生成 Zotero API key、Cloudflare API token、DeepSeek key；导入仓库建 Worker；开启 Cloudflare Access 并只允许自己的邮箱；
生成 GitHub fine-grained token（只选这个仓库，Actions 读写）填进 Worker 的 `GITHUB_TOKEN`。给用户能直接点开的链接，一次只说一步。

## 本地版升级到云端版

先把云端版配好（上一节），然后搬数据：

```bash
uv run robot-times local export robot-times.sql
cd web/app && npx wrangler d1 execute DB --remote --file ../../robot-times.sql
```

确认云端看板里能看到以前的推荐和笔记后，可以 `uv run robot-times local autostart --off` 关掉本地版的开机启动。

## 改设置

研究兴趣、方向预设、arXiv 分类、Zotero 文件夹、报名、天气城市、每天读多久、权重，都在看板的“设置”页里改，
也可以 `uv run robot-times local init --interests ...`（只改给出的项）。改完下一次生成推荐时生效，设置页上有“重新生成”按钮。

## 排错

| 现象 | 原因和办法 |
| --- | --- |
| 连不上 Zotero / 403 | Zotero 没开，或没勾选“允许此计算机上的其他应用程序与 Zotero 通信”；Zotero 低于 10 不支持写入 |
| “加入 Zotero”每次都弹窗 | 授权时选了 Allow 而不是 Always Allow：删掉 `~/.robot-times/zotero_key`，重跑 `local init` |
| WSL 里连不上 Windows 上的 Zotero | 在 Windows 上直接跑；或 `%UserProfile%\.wslconfig` 里 `[wsl2]` 加 `networkingMode=mirrored` |
| 看板打不开 | `uv run robot-times local restart`；日志在 `~/.robot-times/serve.log` |
| 一直“正在生成”或生成失败 | 看 `~/.robot-times/serve.log`；arXiv 限流（429）会自动重试并改用 RSS；大模型 401/402 是密钥错或余额不足 |
| 推荐不对口 | 研究兴趣写得更具体；Zotero 文献库里条目太少（少于 10 条会不准）；多点“多推这类 / 少推这类” |
| 端口 8765 被占用 | `uv run robot-times local serve --port 8766`（开机启动用的还是 8765） |
| 云端版没按时生成 | Worker 没配 `GITHUB_TOKEN` 时只能靠 GitHub 自带的定时任务，常常晚几个小时；按 cloud-setup.md 配上 |

更多细节：`docs/dev/local-setup.md`、`docs/dev/cloud-setup.md`。
