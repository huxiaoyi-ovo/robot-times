# 云端单人版：一次性配置清单

整套流程：GitHub Actions 每天早上跑推荐 → 结果存进 Cloudflare D1 → 在看板 `robot-times` 里读、记笔记、点“加入 Zotero”
→ Zotero 官方同步把条目带到你电脑上。架构说明见 [docs/architecture.md](../architecture.md)。

先在 Cloudflare 建一个 D1 数据库 `robot-times`，按 `web/app/migrations/` 里的 SQL 初始化，记下它的 Database ID，
并填进 `web/app/wrangler.jsonc` 的 `database_id`。下面这些只能你本人在网页上操作，
**密钥只填进对应网站的设置页，不要发到聊天里或提交进仓库。**

大约 20 分钟。

## 1. 准备四样东西

| 要什么 | 去哪里拿 | 记下来 |
| --- | --- | --- |
| Zotero 用户 ID 和 API key | <https://www.zotero.org/settings/keys>。页面上写着 “Your user ID for use in API calls is ……”。点 “Create new private key”，勾选 **Allow library access**、**Allow notes access**、**Allow write access** | `ZOTERO_USER_ID`、`ZOTERO_API_KEY` |
| Cloudflare 账号 ID | Cloudflare 后台首页右侧 “Account ID”（或 Workers & Pages 页右侧） | `CF_ACCOUNT_ID` |
| Cloudflare API token | 右上角头像 → My Profile → API Tokens → Create Token → Create Custom Token。权限只加一条：**Account · D1 · Edit** | `CF_API_TOKEN` |
| DeepSeek API key | <https://platform.deepseek.com> → API keys（需要先充一点余额，每天用量约几毛钱） | `DEEPSEEK_API_KEY` |

## 2. GitHub：让每日任务跑起来

你 fork 的仓库 → Settings → Secrets and variables → Actions → New repository secret，逐个添加：

| 名字 | 值 |
| --- | --- |
| `ZOTERO_USER_ID` | 第 1 步记下的 |
| `ZOTERO_API_KEY` | 第 1 步记下的 |
| `CF_ACCOUNT_ID` | 第 1 步记下的 |
| `CF_API_TOKEN` | 第 1 步记下的 |
| `D1_DATABASE_ID` | 上面记下的 Database ID |
| `DEEPSEEK_API_KEY` | 第 1 步记下的 |

然后 Actions → **daily** → Run workflow，跑一次。第一次要下载模型，大约 5 到 10 分钟。
绿色对勾就说明今天的推荐已经写进数据库了；红叉的话点进去看最后几行报错。

之后工作日北京时间 9:40 左右自动运行（配了下面的 `GITHUB_TOKEN` 时由看板按时触发，否则靠 GitHub 自带的定时任务，可能晚几个小时）（arXiv 北京时间早上 8 到 9 点发布新论文，周末不发布），周日晚上 8 点左右生成周报。GitHub 的定时任务常常晚几分钟到半小时。
任务失败时 GitHub 会给你发邮件。

> 注意：仓库连续 60 天没有任何提交时，GitHub 会自动停掉定时任务，需要去 Actions 页面点一下重新启用。

## 3. Cloudflare：部署看板

1. Workers & Pages → Create → **Import a repository** → 选你 fork 的仓库。
2. 项目名填 `robot-times`；**Root directory（根目录）填 `web/app`**；构建命令留空；部署命令用默认的 `npx wrangler deploy`。
3. 部署完成后，进入 `robot-times` → Settings → Domains & Routes → `workers.dev` 那一行点 **Enable Cloudflare Access**。
   再点 **Manage Cloudflare Access**，把允许登录的人改成只有你的邮箱。
4. 在 Zero Trust 里找到两个值：
   - 团队域名：Zero Trust → Settings（设置）→ Team name and domain，形如 `xxxx.cloudflareaccess.com`
   - AUD：Zero Trust → Access → Applications → 点开 `robot-times` 的应用 → **Application Audience (AUD) Tag**
5. `robot-times` → Settings → Variables and Secrets，逐个添加（类型都选 **Secret**）：

| 名字 | 值 | 必须吗 |
| --- | --- | --- |
| `ALLOWED_EMAIL` | 你登录用的邮箱 | 必须 |
| `ACCESS_TEAM_DOMAIN` | robot-times → Access 页右下角 **JWKS** 那条链接，整条复制粘贴即可 | 建议填（不填也能用，填了更安全） |
| `ACCESS_AUD` | robot-times → Access 页右下角的 **AUD tag** | 必须 |
| `ZOTERO_USER_ID` | 同上 | 必须（“加入 Zotero”用） |
| `ZOTERO_API_KEY` | 同上 | 必须 |
| `DEEPSEEK_API_KEY` | 同上 | 可选（“问 AI”用） |
| `GITHUB_TOKEN` | GitHub → Settings → Developer settings → Fine-grained tokens，只选你 fork 的这个仓库，权限 **Actions: Read and write** | 强烈建议：有了它，看板每 30 分钟检查一次、打开看板时也检查一次，今天的推荐和业界简报还没有就马上触发生成（GitHub 自带的定时任务经常晚几个小时甚至漏跑）；“立即生成”按钮也靠它 |
| `GITHUB_REPO` | 你的仓库，例如 `yourname/robot-times` | 和 `GITHUB_TOKEN` 一起填（变量） |
| `WEBDAV_URL` | 和 Zotero“设置 → 同步 → 文件同步”里填的地址一样，例如 `https://dav.jianguoyun.com/dav/` | 用 WebDAV 同步附件时必填（变量） |
| `WEBDAV_USER` | WebDAV 账号（坚果云是登录邮箱） | 同上（变量） |
| `WEBDAV_PASSWORD` | WebDAV 密码（坚果云在“账户信息 → 安全选项 → 第三方应用管理”生成的应用密码） | 同上，选 **Secret** |

打开 `https://robot-times.<你的子域>.workers.dev`，先跳到 Cloudflare 的登录页，输邮箱收验证码，然后就能看到今天的推荐。
没设好上面三个必须项时，页面会提示缺什么。

## 4. Zotero 电脑端

- 设置 → 同步 → 登录同一个 zotero.org 账号，勾选“自动同步”。看板里点“加入 Zotero”后，同步一次就会出现在“设置”里指定的文件夹（不填就在“我的文库”根目录），带“Robot Times”标签、PDF 附件和一条中文摘要笔记。
- PDF：默认不加，同步后在条目上右键“查找可用的 PDF”，Zotero 会自己从 arXiv 下载。想让看板直接加：用 WebDAV 同步附件的配上面三个 `WEBDAV_*`（按 Zotero 的格式写 `zotero/<附件编号>.zip` + `.prop`）；用 Zotero 自带存储的加变量 `ZOTERO_PDF=zotero`。

## 日常怎么用

1. 早上打开看板“今日”：按计划扫标题、看摘要、精读一篇。
2. 想读的标“在读”，不相关的点“少推这类”，对口的点“多推这类”。之后的推荐会跟着调整。
3. 精读时在详情里写笔记（自动保存），写完点“加入 Zotero”，笔记会一起进 Zotero。
4. 周日晚上看“本周”：趋势总结和下周每天的安排。
5. 方向变了就去“设置”改方向预设、arXiv 分类、研究兴趣、每天时间和权重；天气城市、报名也在那里改。

## 本地调试（可选）

```bash
# Python 任务，用本地 SQLite 代替 D1
ROBOT_TIMES_STORE=sqlite:dev.db uv run robot-times cloud settings --categories cs.RO --interests "人形机器人全身控制"
ROBOT_TIMES_STORE=sqlite:dev.db ZOTERO_USER_ID=... ZOTERO_API_KEY=... uv run robot-times cloud daily

# 看板
cd web/app && npm install
printf 'DEV_NO_AUTH=1\n' > .dev.vars        # 只在本地跳过登录校验
npm run db:local && npx wrangler dev
npm test
```

## 从本地版搬过来

已经在用本地版，想换成云端版（手机也能看、电脑不用开着）：先按上面配好云端版，再把本地的推荐、笔记、反馈和设置搬过去。

```bash
uv run robot-times local export robot-times.sql            # 在仓库根目录
cd web/app && npx wrangler d1 execute DB --remote --file ../../robot-times.sql
```

`wrangler` 第一次用会让你在浏览器里登录 Cloudflare。搬完以后可以关掉本地版的开机启动：
`uv run robot-times local autostart --off`。
