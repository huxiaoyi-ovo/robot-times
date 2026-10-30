# 本地版：在自己电脑上用

数据和看板都在你的电脑上，不需要 GitHub、Cloudflare，也不需要 Zotero 账号密钥。
代价是电脑要开着才会生成，手机上看不了；想要这两样，用[云端版](cloud-setup.md)。

## 安装包（推荐）

从 [最新版本](https://github.com/huxiaoyi-ovo/robot-times/releases/latest) 下载你系统的安装包，双击安装，
然后在自动打开的网页上按引导设置（连 Zotero、填研究兴趣、配大模型）。详见 [README](../../README.md#快速开始)。

安装包里只有一个 [uv](https://docs.astral.sh/uv/) 和项目代码。第一次打开时：

- 代码复制到 `~/.robot-times/app`（以后的自动更新只改这里，不需要管理员密码）
- uv 下载 Python 3.12 和依赖（装在 `~/.robot-times/app/.venv`，日志在 `~/.robot-times/setup.log`）
- 浏览器先显示“正在准备”，好了自动变成设置引导

开机自动启动：Linux 是 `/etc/xdg/autostart/the-robot-times.desktop`，Windows 是“启动”文件夹里的快捷方式，
macOS 是第一次打开时登记的 `~/Library/LaunchAgents/com.therobottimes.dashboard.plist`。

**卸载**：Linux `sudo apt remove robot-times`；Windows 在“设置 → 应用”里卸载；
macOS 把“应用程序”里的 The Robot Times 拖进废纸篓，再删掉上面那个 LaunchAgents 文件。
你的数据（推荐记录、设置、密钥）在 `~/.robot-times`，卸载不会删，不要了手动删掉这个文件夹。

## 命令行安装

适合想自己改代码的人。大约 10 分钟。

先打开 Zotero（10 或更新版本），在 设置 → 高级 里勾选“允许此计算机上的其他应用程序与 Zotero 通信”。然后：

- **Linux / macOS**：在终端里

  ```bash
  git clone https://github.com/huxiaoyi-ovo/robot-times.git robot-times && cd robot-times
  bash install.sh
  ```

- **Windows**：下载代码后，在仓库文件夹里打开 PowerShell

  ```powershell
  powershell -ExecutionPolicy ByPass -File install.ps1
  ```

脚本会装好需要的工具和依赖，问你的研究兴趣、Zotero 文件夹和大模型密钥（可以跳过），
Zotero 弹窗时点 Always Allow，最后设成开机自动启动并打开看板。可以重复运行。

下面是脚本做的事情的分步说明，想手动装或者脚本出了问题时看。

## 1. 准备

1. **Zotero 10 或更新版本**。打开 设置 → 高级，勾选“允许此计算机上的其他应用程序与 Zotero 通信”。
   验证：浏览器打开 <http://localhost:23119/api/users/0/items/top?limit=3>，能看到 JSON 就对了。
2. **uv**（Python 环境管理工具，会自动装好 Python）：
   - macOS / Linux：`curl -LsSf https://astral.sh/uv/install.sh | sh`
   - Windows（PowerShell）：`powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
3. **下载代码并安装依赖**：

   ```bash
   git clone https://github.com/huxiaoyi-ovo/robot-times.git robot-times && cd robot-times
   uv sync                  # 想要更准的推荐：uv sync --extra embed（会装 PyTorch，体积较大）
   ```

## 2. 初始化

```bash
uv run robot-times local init --interests "足式机器人运动控制、人形机器人全身控制、sim2real" --zotero-folder Inbox
```

- `--interests`：一两句话写你的研究兴趣，越具体推荐越准。
- `--zotero-folder`：点“加入 Zotero”时放进哪个文件夹，要是 Zotero 里已有的；不填就放在“我的文库”。
- `--preset general --categories cs.LG`：不是机器人方向时用通用预设，并写上你的 arXiv 分类。
- `--weather-city 杭州`：报头天气用的城市；不填就按浏览器定位。

运行时 Zotero 会弹窗问是否允许写入，选 **Always Allow**。可以重复运行，只会改你给出的项；
之后在看板的“设置”里改也一样。

初始化最后会顺便：

- **设成开机自动启动**：以后每次开机登录，看板都在后台开着，不用再开终端。
  macOS 是“登录项”（`~/Library/LaunchAgents/com.therobottimes.dashboard.plist`），
  Windows 是“启动”文件夹里的 `The Robot Times.vbs`，Linux 是 `~/.config/autostart/the-robot-times.desktop`。
- **在桌面放一个“The Robot Times”快捷方式**：双击就在浏览器里打开看板。
- **现在就在后台启动看板**，直接打开 <http://localhost:8765> 就能看到。

不想要自动启动：`uv run robot-times local autostart --off`（会删掉自动启动项和桌面快捷方式）；
想重新打开：`uv run robot-times local autostart`。初始化时不想装：加 `--no-autostart`。

**大模型（可选，强烈建议）**：有了它才有精排、中文标题和摘要、全文解读和“问 AI”。
在 `~/.robot-times/.env`（Windows 是 `C:\Users\你的用户名\.robot-times\.env`）里写一行：

```ini
DEEPSEEK_API_KEY=你的密钥
```

DeepSeek 在 <https://platform.deepseek.com> 申请，每天的用量约几毛钱。也可以用其他 OpenAI 兼容的模型：

```ini
ROBOT_TIMES_LLM_BASE_URL=https://api.example.com/v1
ROBOT_TIMES_LLM_API_KEY=你的密钥
ROBOT_TIMES_LLM_MODEL=模型名
```

## 3. 打开看板

直接打开 <http://localhost:8765>，或者双击桌面上的快捷方式。第一次打开时页面上会提示收藏：
按 ⌘D（Windows 按 Ctrl+D）；Chrome 和 Edge 还会出现“安装成应用”的按钮，装好后它就像一个单独的应用，
在程序坞或开始菜单里打开。浏览器不允许程序自己往收藏夹里加东西，所以这一步要你点一下。

没设自动启动时，先运行 `uv run robot-times local serve`（终端窗口要一直开着）。今天的推荐还没生成时会在后台自动生成，看板上方显示进度，
好了自动刷新。第一次要几分钟；业界简报和周报也会按需补上（周报在周日晚上 6 点后）。

## 4. 让它每天自己跑（可选）

不想每天开看板等它生成，可以交给系统的计划任务，每天早上跑一次 `robot-times local run`
（arXiv 在北京时间早上 8 到 9 点发布新论文，所以 9:30 左右合适）：

- **macOS / Linux**：`crontab -e`，加一行（路径换成你的）：

  ```cron
  30 9 * * 1-5 cd /path/to/robot-times && /path/to/uv run robot-times local run >> ~/.robot-times/run.log 2>&1
  ```

- **Windows**：

  ```powershell
  schtasks /create /tn "The Robot Times" /sc weekly /d MON,TUE,WED,THU,FRI /st 09:30 /tr "cmd /c cd /d C:\path\to\robot-times && uv run robot-times local run"
  ```

到时间电脑要开着、Zotero 要打开（推荐要读你的文献库）。

## 数据放在哪

都在 `~/.robot-times`（可以用环境变量 `ROBOT_TIMES_HOME` 换地方）：

| 文件 | 内容 |
| --- | --- |
| `robot-times.db` | 推荐、解读、笔记、设置、业界简报（SQLite） |
| `zotero_key` | Zotero 写入权限 |
| `.env` | 大模型密钥（自己建） |

PDF 和笔记写进你本机的 Zotero，不会上传到任何地方。

## 常见问题

先看看板：生成失败时报头下面有原因和按钮；**设置 → 体检和排查** 能一项项检查、看日志（`~/.robot-times/serve.log`、`setup.log`）、
复制诊断信息（不含密钥）、重启看板；开机自动运行、导出备份也在设置页里。

- **连不上 Zotero**：确认 Zotero 开着，并且勾选了“允许此计算机上的其他应用程序与 Zotero 通信”。
  Zotero 装在 Windows、代码在 WSL 里跑时，WSL 默认访问不到 Windows 的 `localhost:23119`：
  直接在 Windows 上跑，或者在 `%UserProfile%\.wslconfig` 里加上 `[wsl2]` 和 `networkingMode=mirrored`。
- **端口被占用**：`uv run robot-times local serve --port 8766`。
- **自动启动的看板出了问题**：日志在 `~/.robot-times/serve.log`。
- **中文乱码（Windows）**：先运行 `$env:PYTHONUTF8 = "1"`。
- **国内网络**：装依赖慢可以给 uv 配镜像（环境变量 `UV_DEFAULT_INDEX`，例如
  `https://pypi.tuna.tsinghua.edu.cn/simple`）；向量模型第一次运行会从 Hugging Face 下载。
- **检查环境**：`uv run robot-times doctor`。

## 参考

- Zotero 本地 API：<https://www.zotero.org/support/dev/web_api/v3/local_api>
- arXiv API 使用条款（每 3 秒最多 1 次请求）：<https://info.arxiv.org/help/api/tou.html>
