# 整体设计

The Robot Times 每天做三件事：挑论文、写解读、排阅读计划；另外每天一份业界简报、每周一份回顾。
同一套 Python 代码（`core/`）和同一个页面（`web/app/public/index.html`）支撑两种部署。

## 每天的推荐怎么来

```
arXiv 新论文（API，被限流时改用 RSS）
  → 去掉文献库里已有的、以前推过的
  → 向量相似度：和你的 Zotero 文献库、点过“多推这类”或加入过 Zotero 的论文像不像；和点过“少推这类”的越像扣分越多
  → 加上影响（写明被顶会顶刊接收）和热度（Hugging Face 点赞）
  → 前 60 篇交给大模型精排：打分、写中文要点、推荐理由、怎么读、方向标签
  → 按多样性选出每天的十几篇（同一方向不扎堆），分成精读 / 看摘要 / 扫读，按你每天的时间排计划
  → 每篇读 arXiv 的 HTML 全文，写中文标题和摘要、一段解读评论，挑两张关键图（图片直接链接 arXiv，不转存）
```

代码：`pipeline.py`（打分和多样性）、`llm/rerank.py`（精排）、`llm/analysis.py`（解读）、`cloud/daily.py`（把这些串起来存进数据库）。
写作风格和研究方向有关的东西都在 `presets.py`：换方向只是换一个预设。

## 两种部署

| | 本地版 | 云端版 |
| --- | --- | --- |
| 数据 | 本机 SQLite（`~/.robot-times/robot-times.db`） | Cloudflare D1（同一套表，见 `core/src/robot_times/cloud/schema.sql`） |
| 看板接口 | `core/src/robot_times/local/`：标准库 HTTP 服务，只监听本机 | `web/app/src/index.js`：Cloudflare Worker，用 Cloudflare Access 登录 |
| 什么时候生成 | 开机自启的看板发现今天的还没有，就在后台生成（`local/jobs.py`） | Worker 每 30 分钟和打开看板时检查，缺了就触发 GitHub Actions（`daily.yml` 等）；GitHub 自带的定时任务兜底 |
| 读文献库、加入 Zotero | Zotero 桌面端的本地接口（`localhost:23119`，Zotero 10+），PDF 直接存进本机 | Zotero Web API，经 Zotero 同步到电脑 |
| 大模型 | 自己的密钥，写在 `~/.robot-times/.env` | 自己的密钥，填在 GitHub 和 Cloudflare 的 Secrets 里 |

两边的看板接口返回一样的数据，页面不用区分；页面只根据 `mode` 显示不同的说明文字。
改接口时两边要一起改（各自有测试）。本地数据可以用 `robot-times local export` 搬到云端。

## 原则

- **数据归用户**：没有托管服务。本地版什么都不上传（除了发给你配置的大模型的论文标题和摘要）；云端版存在你自己的 Cloudflare 账号里。
- **尊重来源**：arXiv 每 3 秒最多 1 次请求，PDF 不转存；业界简报只存链接和自己写的摘要。
- **不写死个人信息**：报名、研究方向、城市、Zotero 文件夹、仓库地址都是设置或部署变量。
- **测试不联网**：外部服务一律用假的（`httpx.MockTransport`、假 D1）。
