"""The Robot Times命令行。

本地版（数据和看板都在你的电脑上）：
  robot-times local init      第一次用：保存设置、申请 Zotero 写入权限
  robot-times local serve     打开看板；今天的推荐还没生成时会自动生成
  robot-times local run       在前台跑一次任务（给系统的计划任务用）
  robot-times doctor          检查 Zotero、大模型、向量模型是否就绪

云端版（GitHub Actions + Cloudflare）：robot-times cloud daily / analyze / news / weekly / settings
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import NoReturn

import httpx
import typer

from .config import CONFIG_DIR, Settings
from .zotero.local_api import ZoteroError, ZoteroLocalClient, note_html

app = typer.Typer(
    add_completion=False,
    help="The Robot Times：每天帮你挑出该读的论文，写好解读，直接进 Zotero。",
)

KEY_FILE = CONFIG_DIR / "zotero_key"


def _zotero(settings: Settings) -> ZoteroLocalClient:
    key = KEY_FILE.read_text(encoding="utf-8").strip() if KEY_FILE.exists() else None
    return ZoteroLocalClient(settings.zotero_url, api_key=key)


def _save_key(z: ZoteroLocalClient) -> None:
    """授权被刷新过（比如旧 key 失效后重新授权）时，把新的长期 key 存下来。"""
    if not (z.key_is_persistent and z.api_key):
        return
    old = KEY_FILE.read_text(encoding="utf-8").strip() if KEY_FILE.exists() else ""
    if old != z.api_key:
        KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        KEY_FILE.write_text(z.api_key, encoding="utf-8")
        KEY_FILE.chmod(0o600)


def _fail(msg: str) -> NoReturn:
    typer.secho(msg, fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


@app.command()
def doctor() -> None:
    """检查运行环境。"""
    from .local.app import load_env

    load_env()
    s = Settings.load()
    has_llm = s.llm_enabled or bool(os.environ.get("DEEPSEEK_API_KEY"))
    z = _zotero(s)
    if z.ping():
        n = len(z.top_items())
        typer.secho(f"✓ Zotero 已连接（服务器 ID {z.server_id[:8]}…，文献库 {n} 条）", fg="green")
    else:
        typer.secho(
            "✗ 连不上 Zotero：请确认 Zotero 10 已打开，并在 设置 → 高级 里开启"
            "“允许此计算机上的其他应用程序与 Zotero 通信”。",
            fg="red",
        )
    typer.echo(
        f"{'✓' if KEY_FILE.exists() else '·'} Zotero 写入权限"
        + ("" if KEY_FILE.exists() else "：未申请，运行 robot-times local init")
    )
    typer.echo(
        f"{'✓' if has_llm else '·'} 大模型"
        + (
            ""
            if has_llm
            else f"：未配置（可选），在 {CONFIG_DIR / '.env'} 里写 DEEPSEEK_API_KEY=你的密钥"
        )
    )
    try:
        import sentence_transformers  # noqa: F401

        typer.echo(f"✓ 向量模型：{s.embed_model}")
    except ImportError:
        typer.echo("· 向量模型未安装，将用词袋向量。安装：uv sync --extra embed")
    typer.echo(f"· arXiv 分类：{', '.join(s.arxiv_categories)}")


@app.command()
def authorize() -> None:
    """申请写入权限。请在 Zotero 弹窗里选 “Always Allow”。"""
    s = Settings.load()
    z = ZoteroLocalClient(s.zotero_url)
    typer.echo("请切换到 Zotero，在弹窗里点 “Always Allow”……")
    try:
        z.authorize()
    except ZoteroError as e:
        _fail(str(e))
    except httpx.HTTPError:
        _fail("连不上 Zotero，先运行 robot-times doctor 看看。")
    if z.key_is_persistent:
        _save_key(z)
        typer.secho(f"✓ 已保存写入权限到 {KEY_FILE}", fg="green")
    else:
        typer.secho(
            "你选的是一次性允许，每次写入都会再弹窗。建议重新运行并选 Always Allow。", fg="yellow"
        )


@app.command(hidden=True)
def recommend(
    days: int = typer.Option(2, help="看最近几天提交的论文；周一建议用 4"),
    top: int = typer.Option(20, help="输出多少篇"),
    max_results: int = typer.Option(400, help="最多从 arXiv 拉多少篇"),
    llm: bool = typer.Option(True, help="配置了 LLM 时，是否用它精排并写中文摘要"),
    out: Path = typer.Option(Path("runs"), help="结果保存目录"),
) -> None:
    """生成今天的推荐。"""
    from .llm.rerank import LLMClient
    from .pipeline import arxiv_ids_in_library
    from .pipeline import recommend as run
    from .ranking.embedder import default_embedder
    from .report import save_run
    from .sources.arxiv_source import fetch_recent

    s = Settings.load()
    z = _zotero(s)
    if not z.ping():
        _fail("连不上 Zotero，先运行 robot-times doctor 看看。")
    try:
        library = z.top_items()
    except ZoteroError as e:
        _fail(str(e))
    if len(library) < 10:
        typer.secho(
            f"提醒：文献库只有 {len(library)} 条，推荐主要靠它判断你的兴趣，条目太少会不准。",
            fg="yellow",
        )
    typer.echo(
        f"文献库 {len(library)} 条，正在从 arXiv 抓 {', '.join(s.arxiv_categories)} 最近 {days} 天的论文……"
    )
    try:
        papers = fetch_recent(s.arxiv_categories, days=days, max_results=max_results)
    except Exception as e:  # arxiv 库的网络错误类型较多，统一提示
        _fail(f"从 arXiv 抓取失败：{e}。请检查网络后重试。")
    if not papers:
        _fail(f"最近 {days} 天没有抓到新论文。周末和节假日 arXiv 不发布，可以用 --days 4 再试。")
    typer.echo(f"抓到 {len(papers)} 篇，正在打分……")
    llm_client = (
        LLMClient(s.llm_base_url, s.llm_api_key, s.llm_model) if (llm and s.llm_enabled) else None
    )
    scored = run(
        papers,
        library,
        default_embedder(s.embed_model),
        weights=s.weights,
        llm=llm_client,
        interests=s.interests,
        known_ids=arxiv_ids_in_library(library),
    )[:top]
    if not scored:
        _fail("新论文都已经在你的文献库里了，今天没有新的推荐。")
    stem = datetime.now().strftime("%Y-%m-%d-%H%M")
    json_path, md_path = save_run(scored, out, stem, s.weights)
    for i, sp in enumerate(scored[:10], 1):
        typer.echo(f"{i:>2}. [{sp.relevance:4.1f}] {sp.paper.title[:90]}")
    typer.secho(f"\n结果：{md_path}（勾选你会读的，再运行 robot-times eval {md_path}）", fg="green")
    typer.echo(f"写入 Zotero：robot-times push {json_path} --top 5")


@app.command(hidden=True)
def push(
    run_json: Path = typer.Argument(..., help="recommend 生成的 JSON 文件"),
    top: int = typer.Option(5, help="写入前几篇"),
    ids: str = typer.Option("", help="只写入这些 arXiv 编号，逗号分隔"),
) -> None:
    """把推荐写进 Zotero（带“Robot Times”标签和中文摘要笔记）。"""
    from .pipeline import arxiv_ids_in_library
    from .report import load_run

    s = Settings.load()
    scored = load_run(run_json)
    if ids:
        wanted = {i.strip() for i in ids.split(",")}
        scored = [sp for sp in scored if sp.paper.id in wanted]
    else:
        scored = scored[:top]
    if not scored:
        _fail("没有要写入的论文。")
    z = _zotero(s)
    try:
        existing = arxiv_ids_in_library(z.top_items())
    except (ZoteroError, httpx.HTTPError):
        _fail("连不上 Zotero，先运行 robot-times doctor 看看。")
    skipped = [sp for sp in scored if sp.paper.id in existing]
    scored = [sp for sp in scored if sp.paper.id not in existing]
    if skipped:
        typer.echo(f"跳过 {len(skipped)} 篇已经在文献库里的论文。")
    if not scored:
        typer.echo("没有需要写入的新论文。")
        return
    notes = [
        note_html(sp.summary_zh, sp.why_zh, f"相关度 {sp.relevance:.1f}") if sp.summary_zh else None
        for sp in scored
    ]
    try:
        keys = z.add_papers([sp.paper for sp in scored], notes, tags=[s.zotero_tag])
    except ZoteroError as e:
        _fail(str(e))
    _save_key(z)
    typer.secho(f"✓ 已写入 {sum(1 for k in keys if k)} 篇，标签“{s.zotero_tag}”。", fg="green")


@app.command(name="eval", hidden=True)
def evaluate(md: Path = typer.Argument(..., help="勾选过的 Markdown 结果文件")) -> None:
    """统计推荐命中率。"""
    from .report import evaluate_markdown

    typer.echo(evaluate_markdown(md).describe())


# ---------------- 云端单人版（GitHub Actions 定时运行） ----------------

cloud = typer.Typer(
    help="云端版：每日推荐、业界简报和周报，由 GitHub Actions 定时运行，数据存 Cloudflare D1。"
)
app.add_typer(cloud, name="cloud")


def _logging(log_file: str = "") -> None:
    import logging

    kw: dict = {"filename": log_file, "encoding": "utf-8"} if log_file else {}
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", **kw)


@cloud.command("daily")
def cloud_daily(
    day: str = typer.Option("", help="按哪一天生成（YYYY-MM-DD），默认今天（北京时间）"),
    max_results: int = typer.Option(800, help="最多从 arXiv 拉多少篇"),
    if_missing: bool = typer.Option(
        False, help="今天 arXiv 发布之后已经生成过就跳过（备用的定时任务用，避免换掉正在读的推荐）"
    ),
) -> None:
    """生成今天的推荐和阅读计划，写进数据库。"""
    from datetime import date as _date

    from .cloud import repo
    from .cloud.daily import DailyDeps, run_daily
    from .cloud.env import llm_from_env, settings_fallback_from_env, store_from_env
    from .ranking.embedder import default_embedder
    from .signals import hf_upvotes
    from .sources.arxiv_html import ArxivHtml
    from .sources.arxiv_source import fetch_recent
    from .zotero.web_api import ZoteroWebClient

    _logging()
    import os

    store = store_from_env()
    if if_missing:
        from .cloud.daily import generated_after_release, today_local

        target = _date.fromisoformat(day) if day else today_local()
        if generated_after_release(store, target):
            typer.echo(f"{target} 的推荐已经生成过，跳过。")
            return
    settings = repo.load_settings(store, settings_fallback_from_env())
    deps = DailyDeps(
        store=store,
        zotero=ZoteroWebClient(
            os.environ.get("ZOTERO_USER_ID", ""), os.environ.get("ZOTERO_API_KEY", "")
        ),
        embedder=default_embedder(
            os.environ.get("ROBOT_TIMES_EMBED_MODEL", "BAAI/bge-small-en-v1.5")
        ),
        fetch_papers=lambda cats, days: fetch_recent(cats, days=days, max_results=max_results),
        hf_upvotes=hf_upvotes,
        llm=llm_from_env(),
        html=ArxivHtml(),
    )
    result = run_daily(deps, settings, _date.fromisoformat(day) if day else None)
    typer.echo(f"{result['date']}：{len(result['picks'])} 篇推荐。{result['headline']}")
    for problem in result.get("stats", {}).get("problems", []):
        typer.secho(f"注意：{problem}", fg=typer.colors.YELLOW)


@cloud.command("analyze")
def cloud_analyze(
    day: str = typer.Option("", help="给哪一天的推荐写解读（YYYY-MM-DD），默认最近一天"),
    force: bool = typer.Option(False, help="已经写过的也重写"),
) -> None:
    """给某一天推荐的论文补写详细解读（中文翻译、全文解读、关键图表），不重新选论文。"""
    import json

    from .cloud import repo
    from .cloud.analyze import analyze_papers
    from .cloud.env import llm_from_env, settings_fallback_from_env, store_from_env
    from .presets import get_preset
    from .sources.arxiv_html import ArxivHtml

    _logging()
    store = store_from_env()
    llm = llm_from_env()
    if llm is None:
        typer.secho("没有配置大模型（DEEPSEEK_API_KEY），写不了解读。", fg=typer.colors.RED)
        raise typer.Exit(1)
    sql = "select data from days " + ("where date = ?" if day else "order by date desc limit 1")
    rows = store.rows(sql, [day] if day else [])
    if not rows:
        typer.secho(f"找不到 {day or '最近一天'} 的推荐。", fg=typer.colors.RED)
        raise typer.Exit(1)
    doc = json.loads(rows[0]["data"])
    papers = repo.papers_by_ids(store, doc.get("picks") or [])
    records = [papers[i] for i in doc.get("picks") or [] if i in papers]
    settings = repo.load_settings(store, settings_fallback_from_env())
    problems: list[str] = []
    n = analyze_papers(
        store,
        llm,
        records,
        settings.get("interests", ""),
        ArxivHtml(),
        problems,
        force=force,
        preset=get_preset(settings),
    )
    typer.echo(f"{doc['date']}：新写了 {n} 篇解读，共 {len(records)} 篇。")
    for problem in problems:
        typer.secho(f"注意：{problem}", fg=typer.colors.YELLOW)


@cloud.command("news")
def cloud_news(
    day: str = typer.Option("", help="按哪一天生成（YYYY-MM-DD），默认今天（北京时间）"),
    if_missing: bool = typer.Option(False, help="今天已经生成过就跳过（备用的定时任务用）"),
) -> None:
    """抓和你方向有关的行业新闻与深度文章，生成今天的业界简报。"""
    from datetime import date as _date

    from .cloud import repo
    from .cloud.daily import today_local
    from .cloud.env import llm_from_env, settings_fallback_from_env, store_from_env
    from .news.digest import run_news

    _logging()
    store = store_from_env()
    target = _date.fromisoformat(day) if day else today_local()
    if if_missing and store.rows("select 1 from news where date = ?", [target.isoformat()]):
        typer.echo(f"{target} 的业界简报已经生成过，跳过。")
        return
    from .news.weather import save_weather

    settings = repo.load_settings(store, settings_fallback_from_env())
    # 每天顺带抓一次当天的天气，看板右上角显示（设置里填了城市才抓）
    save_weather(store, target, str(settings.get("weatherCity") or ""))
    doc = run_news(store, llm_from_env(), settings, target)
    bad = [s["name"] for s in doc["sources"] if not s["ok"]]
    typer.echo(f"{doc['date']}：快讯 {len(doc['news'])} 条，深度 {len(doc['deep'])} 篇。")
    if bad:
        typer.secho(f"这些来源没抓到：{'、'.join(bad)}", fg=typer.colors.YELLOW)


@cloud.command("weekly")
def cloud_weekly(day: str = typer.Option("", help="这一周里的任意一天，默认今天")) -> None:
    """生成本周回顾和下周计划。"""
    from datetime import date as _date

    from .cloud import repo
    from .cloud.env import llm_from_env, settings_fallback_from_env, store_from_env
    from .cloud.weekly import run_weekly

    _logging()
    store = store_from_env()
    settings = repo.load_settings(store, settings_fallback_from_env())
    week = run_weekly(store, llm_from_env(), settings, _date.fromisoformat(day) if day else None)
    typer.echo(f"{week['week']}：{week['headline']}")


@cloud.command("settings")
def cloud_settings(
    categories: str = typer.Option("", help="arXiv 分类，逗号分隔，如 cs.RO,cs.LG"),
    interests: str = typer.Option("", help="一两句话写你的研究兴趣"),
    minutes: int = typer.Option(0, help="每天阅读分钟数"),
) -> None:
    """查看或修改设置（看板的“设置”页改的是同一份）。"""
    import json

    from .cloud import repo
    from .cloud.env import settings_fallback_from_env, store_from_env
    from .config import _split

    store = store_from_env()
    s = repo.load_settings(store, settings_fallback_from_env())
    if categories:
        s["categories"] = _split(categories)
    if interests:
        s["interests"] = interests
    if minutes:
        s["dailyMinutes"] = minutes
    if categories or interests or minutes:
        repo.save_settings(store, s)
    typer.echo(json.dumps(s, ensure_ascii=False, indent=2))


# ---------------- 本地版（数据和看板都在你的电脑上） ----------------

local = typer.Typer(help="本地版：数据存在本机，看板在浏览器里打开，不需要任何云服务。")
app.add_typer(local, name="local")


@local.command("init")
def local_init(
    preset: str = typer.Option("", help="方向预设：robotics（机器人）或 general（通用）"),
    categories: str = typer.Option(
        "", help="arXiv 分类，逗号分隔，如 cs.RO,cs.LG；不填用方向预设的"
    ),
    interests: str = typer.Option("", help="一两句话写你的研究兴趣，越具体推荐越准"),
    zotero_folder: str = typer.Option(
        "", help="加入 Zotero 时放进的文件夹（Zotero 里已有的），不填就放在“我的文库”"
    ),
    weather_city: str = typer.Option("", help="报头天气用的城市，不填就按浏览器定位"),
    authorize: bool = typer.Option(True, help="申请 Zotero 写入权限（Zotero 里会弹窗）"),
    autostart: bool = typer.Option(True, help="开机自动启动看板，并在桌面放一个快捷方式"),
) -> None:
    """第一次用：建数据库、保存设置、申请 Zotero 写入权限。可以重复运行，只改你给出的项。"""
    import json

    from .cloud import repo
    from .config import _split
    from .local.app import (
        DB_FILE,
        ENV_FILE,
        KEY_FILE,
        load_env,
        open_store,
        save_zotero_key,
        zotero_client,
    )

    load_env()
    store = open_store()
    s = repo.load_settings(store)
    changes: dict = {}
    if preset:
        changes["preset"] = preset
    if categories:
        changes["categories"] = _split(categories)
    if interests:
        changes["interests"] = interests
    if zotero_folder:
        changes["zoteroCollection"] = zotero_folder
    if weather_city:
        changes["weatherCity"] = weather_city
    try:
        s = repo.update_settings(s, changes)
    except repo.SettingsError as e:
        _fail(str(e))
    repo.save_settings(store, s)
    typer.secho(f"✓ 数据库：{DB_FILE}", fg="green")
    shown = {
        k: s.get(k)
        for k in ("preset", "categories", "interests", "zoteroCollection", "weatherCity")
    }
    typer.echo(json.dumps(shown, ensure_ascii=False, indent=2))

    z = zotero_client()
    if not z.ping():
        _fail(
            "✗ 连不上 Zotero：请打开 Zotero 10 或更新版本，在 设置 → 高级 里勾选"
            "“允许此计算机上的其他应用程序与 Zotero 通信”，再运行一次。"
        )
    typer.secho(f"✓ Zotero 已连接，文献库 {len(z.top_items())} 条", fg="green")
    if z.api_key:
        typer.secho(f"✓ 已有 Zotero 写入权限（{KEY_FILE}）", fg="green")
    elif authorize:
        typer.echo("请切换到 Zotero，在弹窗里点 “Always Allow”……")
        try:
            z.authorize()
        except (ZoteroError, httpx.HTTPError) as e:
            _fail(f"✗ 没拿到写入权限：{e}")
        if not z.key_is_persistent:
            _fail("你选的是一次性允许，“加入 Zotero”每次都会再弹窗。请重新运行并选 Always Allow。")
        save_zotero_key(z.api_key or "")
        typer.secho("✓ 已拿到 Zotero 写入权限", fg="green")
    has_llm = bool(os.environ.get("ROBOT_TIMES_LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY"))
    if has_llm:
        typer.secho("✓ 已配置大模型", fg="green")
    else:
        typer.echo(
            f"· 没配置大模型（可选）：在 {ENV_FILE} 里写一行 DEEPSEEK_API_KEY=你的密钥，"
            "就会有大模型精排、中文标题摘要和全文解读、“问 AI”，业界简报也会更准。"
        )
    if autostart:
        _autostart(True, start=True)
        typer.secho(
            "现在就可以在浏览器里打开 http://localhost:8765/ （第一次会自动生成今天的推荐）",
            fg="green",
        )
    else:
        typer.secho("下一步：robot-times local serve", fg="green")


@local.command("serve")
def local_serve(
    port: int = typer.Option(8765, help="端口"),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="启动后在浏览器里打开"),
    log_file: str = typer.Option("", help="日志写到这个文件（开机自动启动时用）"),
) -> None:
    """启动本地看板。今天的推荐还没生成时会在后台自动生成（第一次要几分钟）。"""
    import webbrowser

    from .local.app import build
    from .local.server import is_running, serve

    _logging(log_file)
    url = f"http://localhost:{port}/"
    if is_running(port):  # 已经开着（比如开机自动启动了）：直接打开
        typer.secho(f"看板已经在运行：{url}", fg="green")
        if open_browser:
            webbrowser.open(url)
        return
    api = build()
    try:
        httpd = serve(api, port)
    except OSError:
        _fail(f"端口 {port} 被别的程序占用了，换个端口试试：--port 8766。")
    typer.secho(f"看板：{url}（关掉这个窗口或按 Ctrl+C 停止）", fg="green")
    if api.jobs is not None:
        api.jobs.auto()
    if api.updater is not None:
        from .local.update import ticker

        ticker(api.updater)  # 每小时检查一次新版本，开了自动更新就在没人用的时候更新
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        typer.echo("已停止。")


@local.command("autostart")
def local_autostart(
    on: bool = typer.Option(True, "--on/--off", help="打开或关掉开机自动启动"),
    start: bool = typer.Option(True, help="打开时顺便现在就在后台启动看板"),
) -> None:
    """开机自动启动看板，并在桌面放一个“The Robot Times”快捷方式，双击就打开看板。"""
    _autostart(on, start)


def _autostart(on: bool, start: bool = True) -> None:
    import sys
    from pathlib import Path

    from .local import install
    from .local.server import is_running

    plan = install.plan_autostart(sys.platform, Path.home(), Path.cwd(), dict(os.environ))
    if not on:
        gone = install.remove(plan)
        typer.secho(
            "已关掉开机自动启动" + (f"，删掉了：{', '.join(str(p) for p in gone)}" if gone else ""),
            fg="green",
        )
        return
    written = install.apply(plan, start=start, running=is_running)
    for p in written:
        typer.echo(f"  · {p}")
    typer.secho(
        "✓ 已设置开机自动启动看板" + ("，并加了打开看板的快捷方式" if len(written) > 1 else ""),
        fg="green",
    )
    typer.echo(
        f"  看板地址：http://localhost:{install.PORT}/（建议在浏览器里收藏）。不想要了：robot-times local autostart --off"
    )


@local.command("stop")
def local_stop() -> None:
    """停掉正在后台运行的看板（开机自动启动的设置不变，下次开机还会启动）。"""
    from .local.server import stop

    if stop():
        typer.secho("✓ 看板已停止", fg="green")
    else:
        _fail("没能停下来，可以重启电脑。")


@local.command("restart")
def local_restart() -> None:
    """重启后台的看板，比如改了 ~/.robot-times/.env 里的大模型密钥之后。"""
    import time

    from .local import install
    from .local.app import load_env
    from .local.server import is_running, stop

    load_env()
    if not stop():
        _fail("旧的看板没能停下来，可以重启电脑。")
    from .config import CONFIG_DIR

    install._spawn([*install.serve_command(), "--log-file", str(CONFIG_DIR / "serve.log")])
    for _ in range(40):
        if is_running():
            typer.secho(f"✓ 看板已重启：http://localhost:{install.PORT}/", fg="green")
            return
        time.sleep(0.5)
    _fail(f"看板没起来，看看日志：{CONFIG_DIR / 'serve.log'}")


@local.command("update")
def local_update(
    check: bool = typer.Option(False, "--check", help="只检查，不更新"),
) -> None:
    """更新到最新发布的版本（看板开着的话会自动重启）。"""
    from . import __version__
    from .local.app import open_store
    from .local.server import is_running
    from .local.update import UpdateError, Updater, newer

    up = Updater(open_store(), restart=lambda: None)
    latest = up.check(force=True)
    if not latest or not newer(latest["version"], __version__):
        typer.secho(f"已经是最新版本：{__version__}", fg="green")
        return
    typer.echo(f"有新版本：{__version__} → {latest['version']}\n{latest['notes']}")
    if check:
        return
    try:
        up.apply(latest)
    except UpdateError as e:
        _fail(str(e))
    typer.secho(f"✓ 已更新到 {latest['version']}", fg="green")
    if is_running():
        local_restart()


@local.command("export")
def local_export(
    out: str = typer.Argument("robot-times.sql", help="导出到哪个文件"),
) -> None:
    """把本地数据导出成 SQL，用来搬到云端版（见 docs/dev/cloud-setup.md 的“从本地版搬过来”）。"""
    from pathlib import Path

    from .local.app import open_store
    from .local.export import export_sql

    sql = export_sql(open_store())
    Path(out).write_text(sql, encoding="utf-8")
    typer.secho(f"✓ 已导出到 {out}（{sql.count(chr(10)) - 1} 条）", fg="green")
    typer.echo(
        "  导入云端：在 web/app 里运行 npx wrangler d1 execute DB --remote --file "
        + str(Path(out).resolve())
    )


@local.command("run")
def local_run(
    job: str = typer.Argument("auto", help="auto（该跑的都跑）、daily、news 或 weekly"),
) -> None:
    """在前台跑一次任务，给系统的计划任务用（例如每天早上 9:30）。"""
    from .local.app import build

    _logging()
    api = build(background=False)
    jobs = api.jobs
    if job == "auto":
        status = jobs.auto()
    elif job in ("daily", "news", "weekly"):
        status = jobs.start(job, force=True)
    else:
        _fail("任务只能是 auto、daily、news 或 weekly。")
    last = status.get("last")
    if last is None:
        typer.echo("现在没有需要跑的任务。")
    elif not last["ok"]:
        _fail(f"失败：{last['message']}")
    else:
        typer.secho(f"完成：{last['message']}", fg="green")


if __name__ == "__main__":
    app()
