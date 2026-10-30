---
name: robot-times-setup
description: Install, configure, upgrade or troubleshoot The Robot Times (daily arXiv paper digest for Zotero) — local version on this computer, or the cloud version on GitHub Actions + Cloudflare. Use when the user asks to set it up, says it doesn't work, wants to change research direction, or wants to move from local to cloud.
---

# The Robot Times：帮用户装好、配好、排错

The Robot Times 每天从 arXiv 挑出用户该读的论文，写中文解读，排进阅读计划，一键加入 Zotero；另有业界简报和周报。
仓库：<https://github.com/huxiaoyi-ovo/robot-times>。这份说明自成一体，**不需要先把仓库下载下来**。

你的任务是替用户把它装好、设置好，并确认能用。用户只需要做三件只有他能做的事：
输入一次管理员密码（Linux / macOS 装安装包时）、在 Zotero 弹窗里点 **Always Allow**、自己填大模型密钥。其余都由你来做。

**和用户说中文，写法直白；每做完一步说一句结果，不要大段解释原理。**

## 1. 先问清楚（一次问完）

1. **研究兴趣**：一两句话，越具体越好（例：“足式机器人运动控制、人形机器人全身控制、sim2real；不看纯视觉检测”）。
2. **方向**：机器人（默认，arXiv cs.RO）还是别的（要 arXiv 分类，如 `cs.LG`，或 `cs.CV,cs.AI`）。
3. **Zotero 文件夹**（可选）：“加入 Zotero”时放进哪个已有的文件夹；可以装好后再从列表里选。
4. **大模型密钥**（可选，强烈建议）：有没有 DeepSeek 或其他 OpenAI 兼容接口的密钥。没有就只有英文摘要，没有中文解读和“问 AI”。
5. 只有用户提到手机、云端、不想让电脑一直开着时，才走第 6 节的云端版；默认装本地版。

## 2. 密钥的规矩

- **不要让用户把密钥发到聊天里。** 装好后让用户在看板的设置引导里自己粘贴（会马上测试能不能用）。
  用户已经发了，就照用（见 4.5），但提醒他之后可以去 DeepSeek 后台换一个。
- 密钥保存在 `~/.robot-times/.env`（Windows：`%USERPROFILE%\.robot-times\.env`），只有用户自己能读。

## 3. 安装（本地版）

先确认装了 **Zotero 10 或更新版本**（Linux 常见位置：`~/Zotero/zotero`、`/opt/zotero`、flatpak `org.zotero.Zotero`；
macOS：`/Applications/Zotero.app`；Windows：开始菜单）。没有就请用户从 <https://www.zotero.org/download/> 装好并打开一次。

然后装 The Robot Times。安装包在 `https://github.com/huxiaoyi-ovo/robot-times/releases/latest/download/<文件名>`：

| 系统 | 文件名 | 安装 |
| --- | --- | --- |
| Linux x86_64（`uname -m` 是 `x86_64`） | `robot-times_amd64.deb` | `sudo apt install ./robot-times_amd64.deb` |
| Linux ARM（`aarch64`） | `robot-times_arm64.deb` | 同上 |
| macOS | `RobotTimes-macOS.pkg` | `sudo installer -pkg RobotTimes-macOS.pkg -target /` |
| Windows | `RobotTimes-Windows-Setup.exe` | `RobotTimes-Windows-Setup.exe /VERYSILENT /SUPPRESSMSGBOXES`（不需要管理员） |

- 下载到临时文件夹，用 `curl -LO` 或 PowerShell 的 `Invoke-WebRequest -OutFile`。
- 要输密码的那条命令，你没法替用户输时：把命令原样给用户，请他在终端里运行（或者双击下载好的安装包），装好告诉你。
- Linux 不是 Debian / Ubuntu 系（没有 `apt`），或者用户不想装到系统里：用文末“附：命令行安装”。

装好后启动看板（Linux / macOS 的安装包装完一般会自己打开浏览器，没打开就手动启动）：

```bash
/opt/robot-times/launch open                                         # Linux
open "/Applications/The Robot Times.app"                             # macOS
wscript "%LOCALAPPDATA%\Programs\The Robot Times\launch.vbs" open    # Windows
```

第一次启动会下载 Python 和依赖，大约一分钟（日志：`~/.robot-times/setup.log`）。等这条命令有 JSON 返回：

```bash
curl -s --noproxy localhost http://localhost:8765/api/job
```

以后开机会自动在后台启动，有新版本自己更新，不需要再管。

## 4. 设置（和网页上的设置引导是同一套接口）

看板起来后，用户打开 <http://localhost:8765> 会看到四步设置引导，可以让用户自己点完。
你也可以用下面的接口替用户做完：写操作要带 `Content-Type: application/json`，只能从本机访问。
Windows 上用 `curl.exe`（不是 PowerShell 的 `curl` 别名）。下面把 `curl -s --noproxy localhost` 简写成 `curl`。

### 4.1 看现在到哪一步

```bash
curl http://localhost:8765/api/setup
```

返回里的 `zotero`：`running`（Zotero 开着没有）、`versionOk`（是不是 10 以上）、`localApi`（本地接口开没开）、
`authorized`（有没有写入权限）、`canEnable`（能不能替用户打开本地接口）；`settings`、`llm.configured`、`done`。

### 4.2 打开 Zotero 的本地接口（`localApi` 是 false 时）

两种办法，选一种：
- 请用户在 Zotero 的 **设置 → 高级** 里勾选“允许此计算机上的其他应用程序与 Zotero 通信”。
- 或者你来改（`canEnable` 为 true 时）：请用户**完全退出 Zotero**（不是最小化），确认 `zotero.running` 变成 false 后，

  ```bash
  curl -X POST -H "Content-Type: application/json" http://localhost:8765/api/setup/enable-api
  ```

  再请用户重新打开 Zotero，等 `localApi` 变成 true。

### 4.3 授权写入（`authorized` 是 false 时）

**先跟用户说**：“马上 Zotero 会弹一个窗口，请切过去点 **Always Allow**（不是 Allow）。” 然后：

```bash
curl -m 200 -X POST -H "Content-Type: application/json" http://localhost:8765/api/setup/authorize
```

这条会等用户点完（最多 3 分钟）。返回错误说“只允许一次”，就是点成了 Allow，请用户再来一次。

### 4.4 选 Zotero 文件夹（可选）

```bash
curl http://localhost:8765/api/setup/collections      # 用户 Zotero 里已有的文件夹名
```

用户第 1 步说的文件夹要在这个列表里；不在就请他从列表里选，或者留空（放在“我的文库”）。

### 4.5 大模型（可选）

请用户打开 <http://localhost:8765>，在设置引导的“大模型”那一步粘贴密钥，点保存（会马上测试）。
用户已经把密钥发给你了，才用接口（`base`、`model` 不填就是 DeepSeek）：

```bash
curl -X PUT -H "Content-Type: application/json" -d '{"key":"sk-..."}' http://localhost:8765/api/setup/llm
# 其他 OpenAI 兼容接口：{"key":"...","base":"https://.../v1","model":"模型名"}；本机 Ollama：{"key":"ollama","base":"http://localhost:11434/v1","model":"qwen2.5"}
```

返回 400 时 `error` 里有原因（密钥不对、余额不足、地址或模型名不对），照着告诉用户。

### 4.6 完成

```bash
# 机器人方向
curl -X POST -H "Content-Type: application/json" \
  -d '{"interests":"研究兴趣","zoteroCollection":"文件夹名或空字符串"}' http://localhost:8765/api/setup/finish
# 其他方向：加上 "preset":"general","categories":["cs.LG","cs.AI"]
```

完成后会马上开始生成今天的推荐（第一次要几分钟）。

## 5. 确认能用

1. `curl http://localhost:8765/api/setup`：`done` 是 true，`zotero.authorized` 是 true。
2. `curl http://localhost:8765/api/job`：`running` 是 `daily` 表示正在生成；变回 null、`last` 里没有错误就是好了。
3. 请用户打开 <http://localhost:8765>，确认看到今天的论文；点一篇“加入 Zotero”，确认 Zotero 里出现了条目、PDF 和笔记。
4. 告诉用户：应用列表里的 **The Robot Times** 就是入口（可以固定到程序坞 / 任务栏）；研究兴趣等以后在看板的“设置”里改。

更细的自检：`~/.robot-times/app/.venv/bin/robot-times doctor`（Windows：`%USERPROFILE%\.robot-times\app\.venv\Scripts\robot-times.exe doctor`），
Zotero、写入权限、大模型都应该是 ✓。

## 6. 云端版

按 `docs/dev/cloud-setup.md` 一步步来（需要先 `git clone https://github.com/huxiaoyi-ovo/robot-times.git`）。你能代劳的（用户已登录对应 CLI 时）：
- 建数据库并初始化：在 `web/app` 里 `npx wrangler d1 create robot-times`，把 ID 填进 `wrangler.jsonc`，再 `npx wrangler d1 migrations apply DB --remote`。
- 设 GitHub secrets：`gh secret set ZOTERO_USER_ID` 等（值让用户在终端里自己粘贴）。
- 手动跑第一次：`gh workflow run daily.yml`，再 `gh run watch`。

只能用户自己在网页上做的：生成 Zotero API key、Cloudflare API token、DeepSeek key；导入仓库建 Worker；开启 Cloudflare Access 并只允许自己的邮箱；
生成 GitHub fine-grained token（只选这个仓库，Actions 读写）填进 Worker 的 `GITHUB_TOKEN`。给用户能直接点开的链接，一次只说一步。
云端的密钥只填进 GitHub 仓库的 Actions secrets 和 Cloudflare Worker 的 Variables and Secrets。

### 本地版搬到云端版

先把云端版配好，然后在本地导出、导入（安装包装的，`robot-times` 在 `~/.robot-times/app/.venv/bin/` 里）：

```bash
robot-times local export robot-times.sql
cd web/app && npx wrangler d1 execute DB --remote --file ../../robot-times.sql
```

确认云端看板里能看到以前的推荐和笔记后，可以卸载本地版（见 `docs/dev/local-setup.md` 的“卸载”）。

## 7. 排错

先问看板自己（和设置页“体检和排查”是同一套接口）：

```bash
curl http://localhost:8765/api/doctor                 # 一项项检查：ok 为 false 的就是问题，hint 是办法
curl "http://localhost:8765/api/logs?name=serve&lines=200"   # 看板日志；name=setup 是第一次安装依赖的日志
curl http://localhost:8765/api/job                    # last 里有上次生成的结果；失败时 title、hint 说明原因
curl -X POST -H "Content-Type: application/json" http://localhost:8765/api/restart   # 重启看板
```

用户可以在 **设置 → 体检和排查** 里自己点；需要人帮忙时，让用户点“复制诊断信息”发给你（不含密钥）。

| 现象 | 原因和办法 |
| --- | --- |
| 第一次打开一直“正在准备” | 看 `~/.robot-times/setup.log`；多半是网络下载 Python / 依赖失败，网络好了重新打开应用 |
| 连不上 Zotero / 403 | Zotero 没开，或本地接口没打开（4.2）；Zotero 低于 10 不支持写入 |
| “加入 Zotero”每次都弹窗 | 授权时选了 Allow 而不是 Always Allow：删掉 `~/.robot-times/zotero_key`，重启看板后重做 4.3 |
| WSL 里连不上 Windows 上的 Zotero | 直接装 Windows 版；或 `%UserProfile%\.wslconfig` 里 `[wsl2]` 加 `networkingMode=mirrored` |
| 看板打不开 | 重新打开应用；还不行看 `~/.robot-times/serve.log` |
| 一直“正在生成”或生成失败 | 看 `~/.robot-times/serve.log`；arXiv 限流（429）会自动重试并改用 RSS；大模型 401/402 是密钥错或余额不足 |
| 推荐不对口 | 研究兴趣写得更具体；Zotero 文献库里条目太少（少于 10 条会不准）；多点“多推这类 / 少推这类” |
| 端口 8765 被占用 | 多半是以前用命令行装的那份还开着：在那份代码文件夹里 `uv run robot-times local stop` 和 `uv run robot-times local autostart --off` |
| 云端版没按时生成 | Worker 没配 `GITHUB_TOKEN` 时只能靠 GitHub 自带的定时任务，常常晚几个小时；按 cloud-setup.md 配上 |

更多细节：`docs/dev/local-setup.md`、`docs/dev/cloud-setup.md`。

## 附：命令行安装（不用管理员权限，或者用户想改代码）

```bash
uv --version || curl -LsSf https://astral.sh/uv/install.sh | sh          # Windows：powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
git clone --depth 1 https://github.com/huxiaoyi-ovo/robot-times.git ~/robot-times && cd ~/robot-times
uv sync --frozen                                                         # 想要更准的推荐：加 --extra embed（1–2 GB）
uv run robot-times local autostart                                       # 开机自动启动，并现在就在后台启动看板
```

之后和安装包一样，从第 4 节开始设置。（`install.sh` / `install.ps1` 会在终端里问问题，适合用户自己运行，你来做时用上面几条。）
