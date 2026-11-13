"""英文版：设置里的 lang 决定生成内容的语言，看板请求的 X-Lang 决定报错和提示的语言。全部离线。"""

import json
import re
import threading
from datetime import date

import pytest
from test_cloud import FakeLibrary, candidates
from test_local import fetch, make_api, paper

from robot_times.cloud import repo
from robot_times.cloud.daily import DailyDeps, run_daily
from robot_times.cloud.store import SqliteStore
from robot_times.cloud.weekly import run_weekly
from robot_times.i18n import L, norm, using
from robot_times.llm import analysis
from robot_times.llm.style import style_for
from robot_times.local.diagnose import explain
from robot_times.local.server import serve
from robot_times.planning import day_plan, week_plan
from robot_times.presets import PRESETS, PRESETS_EN, get_preset
from robot_times.ranking.embedder import HashingEmbedder

CJK = re.compile(r"[一-鿿]")


def test_lang_helpers():
    assert norm("en") == norm("en-US") == "en" and norm(None) == norm("fr") == "zh"
    assert L("中", "en") == "中"
    with using("en"):
        assert L("中", "en") == "en"
    assert L("中", "en") == "中"


def test_english_presets_cover_every_preset_and_have_no_chinese():
    assert set(PRESETS_EN) == set(PRESETS)
    for key in PRESETS:
        p = get_preset({"preset": key, "lang": "en"})
        assert p["lang"] == "en" and p["topics"][-1] == "Other"
        assert {t for t, _ in p["topic_words"]} <= set(p["topics"])
        assert set(p["news_fallback"]) <= set(p["news_categories"])
        for field in ("label", "field", "terms", "evidence", "limits", "reader", "news_focus"):
            assert not CJK.search(p[field]), (key, field)
        assert not CJK.search(json.dumps(p["example"]) + style_for(p))
    # 老设置里没有 lang：还是中文
    assert get_preset({})["lang"] == "zh" and "用中文" in style_for(get_preset({}))


def test_settings_accept_only_zh_or_en():
    s = repo.update_settings(repo.DEFAULT_SETTINGS, {"lang": "en"})
    assert s["lang"] == "en"
    with pytest.raises(repo.SettingsError):
        repo.update_settings(repo.DEFAULT_SETTINGS, {"lang": "fr"})
    with using("en"), pytest.raises(repo.SettingsError, match="Language must be"):
        repo.update_settings(repo.DEFAULT_SETTINGS, {"lang": "fr"})


class EnglishLLM:
    """记下提示词，按类型返回英文 JSON。"""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def complete(self, prompt, system="", **kw):
        self.calls.append((system, prompt))
        if "trends" in prompt:
            return json.dumps(
                {"headline": "Humanoids week", "summary": "Three sentences.", "trends": []}
            )
        if '"headline"' in prompt:
            return json.dumps({"headline": "RL humanoid walks", "intro": "Read the first one."})
        ids = re.findall(r"^\[(\d{4}\.\d{5})\]", prompt, re.M)
        return json.dumps({"papers": [{"id": i, "score": 8, "summary_zh": "Gist.", "why_zh": "Why.",
                                       "tip_zh": "Fig. 2.", "topic": "Humanoids"} for i in ids]})  # fmt: skip


def test_english_daily_and_weekly_use_english_prompts_and_text():
    store = SqliteStore()
    settings = {**repo.DEFAULT_SETTINGS, "lang": "en", "top": 5, "interests": "humanoids"}
    llm = EnglishLLM()
    deps = DailyDeps(store=store, zotero=FakeLibrary(), embedder=HashingEmbedder(),
                     fetch_papers=lambda c, n: candidates(), hf_upvotes=lambda d: {"2610.00001": 7},
                     llm=llm)  # fmt: skip
    day = run_daily(deps, settings, date(2026, 10, 19))
    assert day["headline"] == "RL humanoid walks"
    assert all(not CJK.search(i["task"]) for i in day["plan"])
    for system, prompt in llm.calls:
        assert "Write in English" in system and not CJK.search(prompt.split("\n\n")[0])
    papers = repo.papers_by_ids(store, day["picks"])
    assert any(p.get("signals") == "7 Hugging Face upvotes" for p in papers.values())
    assert {p.get("topic") for p in papers.values()} <= {"Humanoids", "Other", ""} | {
        t for t in get_preset({"lang": "en"})["topics"]
    }
    week = run_weekly(store, llm, settings, date(2026, 10, 25))
    assert week["headline"] == "Humanoids week" and week["range"] == "Oct 19–Oct 25"
    assert week["nextWeek"]["range"].startswith("From Oct 26")
    assert not CJK.search(json.dumps(week["nextWeek"]["plan"]))


def test_english_analysis_skips_translation():
    class LLM:
        def complete(self, prompt, system="", **kw):
            assert "title_zh" not in prompt and "What it does:" in prompt
            assert "Write in English" in system
            return json.dumps({"title_zh": "不该用", "abstract_zh": "不该用", "review": "What it does: x",
                               "figures": []})  # fmt: skip

    out = analysis.analyze(
        LLM(), {"title": "T", "abstract": "A"}, None, "", get_preset({"lang": "en"})
    )
    assert out["titleZh"] == "" and out["abstractZh"] == "" and out["analysis"]["review"]


def test_plans_in_english():
    assert not CJK.search(json.dumps(day_plan(60, ["d"], ["a"], ["s"], reading=["r"], lang="en")))
    plan = week_plan(date(2026, 10, 26), 60, ["d1", "d2"], ["a1"], lang="en")
    assert plan[0]["day"] == "Mon 10/26" and not CJK.search(json.dumps(plan))


def test_explain_reads_english_errors_and_answers_in_the_current_language():
    with using("en"):
        e = explain("Can't reach Zotero: make sure Zotero is open.", "daily")
        assert e["title"] == "Can't reach Zotero" and "retry:daily" in e["actions"]
        assert explain("LLM error (402)")["title"].startswith("Insufficient")
    assert explain("连不上 Zotero：请确认 Zotero 已经打开。")["title"] == "连不上 Zotero"


@pytest.fixture
def server(tmp_path):
    (tmp_path / "index.html").write_text("<title>x</title>", encoding="utf-8")
    api, _ = make_api()
    paper(api.store)
    httpd = serve(api, 0, tmp_path)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", api
    httpd.shutdown()


def test_server_messages_follow_x_lang_then_settings(server):
    url, api = server
    status, text = fetch(url + "/api/paper/nope", headers={"X-Lang": "en"})
    assert status == 404 and json.loads(text)["error"] == "Paper not found."
    assert json.loads(fetch(url + "/api/paper/nope")[1])["error"] == "找不到这篇论文。"
    repo.save_settings(api.store, {**repo.load_settings(api.store), "lang": "en"})
    assert json.loads(fetch(url + "/api/paper/nope")[1])["error"] == "Paper not found."
    boot = json.loads(fetch(url + "/api/bootstrap")[1])
    assert boot["site"]["presets"][0]["label"] == "Robotics & embodied AI"
