"""命令行端到端测试：用假 Zotero 和假 arXiv 跑完整的 recommend → eval → push 流程。"""

from datetime import date

import httpx
import pytest
from conftest import FakeZotero
from typer.testing import CliRunner

import robot_times.cli as cli
import robot_times.ranking.embedder as embedder_mod
import robot_times.sources.arxiv_source as arxiv_mod
from robot_times.models import Paper
from robot_times.zotero.local_api import ZoteroLocalClient

runner = CliRunner()

LIBRARY = [
    {
        "key": f"L{i}",
        "data": {
            "itemType": "journalArticle",
            "title": title,
            "abstractNote": abstract,
            "dateAdded": "2026-10-01T00:00:00Z",
            **extra,
        },
    }
    for i, (title, abstract, extra) in enumerate(
        [
            ("Humanoid whole-body control", "humanoid robot motion imitation tracking", {}),
            ("Hexapod gait on rough terrain", "six legged robot gait adaptation terrain", {}),
            ("World models for control", "latent world model predicts future robot states", {}),
            (
                "Already saved humanoid paper",
                "humanoid locomotion contacts",
                {"archiveID": "arXiv:2610.31608"},
            ),
        ]
    )
]

PAPERS = [
    Paper(
        "2610.31608",
        "Humanoid Mobility via Contacts",
        "humanoid locomotion contacts",
        ["A B"],
        date(2026, 10, 22),
    ),
    Paper(
        "2610.00001",
        "Humanoid motion imitation at scale",
        "humanoid robot motion imitation tracking",
        ["C D"],
        date(2026, 10, 24),
    ),
    Paper(
        "2610.00002",
        "Hexapod terrain gait",
        "six legged robot gait adaptation on terrain",
        ["E F"],
        date(2026, 10, 24),
    ),
    Paper(
        "2610.00003", "Protein design", "diffusion model for proteins", ["G H"], date(2026, 10, 24)
    ),
]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    fake = FakeZotero(library=[dict(it) for it in LIBRARY])
    monkeypatch.setattr(cli, "KEY_FILE", tmp_path / "home" / "zotero_key")
    monkeypatch.setattr(
        cli,
        "_zotero",
        lambda s: ZoteroLocalClient(
            api_key=cli.KEY_FILE.read_text().strip() if cli.KEY_FILE.exists() else None,
            transport=httpx.MockTransport(fake),
        ),
    )
    monkeypatch.setattr(arxiv_mod, "fetch_recent", lambda *a, **k: list(PAPERS))
    monkeypatch.setattr(
        embedder_mod, "default_embedder", lambda *a, **k: embedder_mod.HashingEmbedder()
    )
    for var in ("ROBOT_TIMES_LLM_BASE_URL", "ROBOT_TIMES_LLM_API_KEY", "ROBOT_TIMES_LLM_MODEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    return fake, tmp_path


def test_doctor_reports_library_size(env):
    result = runner.invoke(cli.app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "文献库 4 条" in result.output


def test_recommend_eval_push_flow(env):
    fake, tmp = env
    result = runner.invoke(cli.app, ["recommend", "--top", "10", "--out", "runs"])
    assert result.exit_code == 0, result.output
    assert "条目太少" in result.output  # 只有 4 条时提醒

    md = next((tmp / "runs").glob("*.md"))
    js = next((tmp / "runs").glob("*.json"))
    text = md.read_text(encoding="utf-8")
    assert "2610.31608" not in text  # 已在库里的不推荐
    lines = [ln for ln in text.splitlines() if ln.startswith("- [ ]")]
    assert "2610.00001" in lines[0] or "2610.00002" in lines[0]
    assert "2610.00003" in lines[-1]  # 无关论文排最后

    md.write_text(text.replace("- [ ]", "- [x]", 1), encoding="utf-8")
    result = runner.invoke(cli.app, ["eval", str(md)])
    assert "前 5 篇里命中 1 篇" in result.output or "勾选了 1 篇" in result.output

    result = runner.invoke(cli.app, ["push", str(js), "--top", "2"])
    assert result.exit_code == 0, result.output
    assert "已写入 2 篇" in result.output
    assert cli.KEY_FILE.exists()  # Always Allow 的 key 已保存
    assert fake.issued_keys == 1

    # 再推一次：已经在库里的会被跳过，不会重复创建
    result = runner.invoke(cli.app, ["push", str(js), "--top", "2"])
    assert "跳过 2 篇" in result.output
    assert len([o for o in fake.created if o["itemType"] == "preprint"]) == 2


def test_recommend_fails_clearly_without_zotero(env, monkeypatch):
    fake, _ = env
    fake.enabled = False
    result = runner.invoke(cli.app, ["recommend"])
    assert result.exit_code == 1
    assert "连不上 Zotero" in result.output


def test_recommend_handles_arxiv_failure(env, monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("timed out")

    monkeypatch.setattr(arxiv_mod, "fetch_recent", boom)
    result = runner.invoke(cli.app, ["recommend"])
    assert result.exit_code == 1
    assert "从 arXiv 抓取失败" in result.output


def test_recommend_with_llm_writes_chinese_summary(env, monkeypatch):
    import json as _json

    import robot_times.llm.rerank as rerank_mod

    monkeypatch.setenv("ROBOT_TIMES_LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("ROBOT_TIMES_LLM_API_KEY", "k")
    monkeypatch.setenv("ROBOT_TIMES_LLM_MODEL", "m")

    def fake_complete(self, prompt, **kw):
        ids = [line[1:].split("]")[0] for line in prompt.splitlines() if line.startswith("[")]
        return _json.dumps(
            [{"id": i, "score": 9, "summary_zh": f"中文摘要{i}", "why_zh": "相关"} for i in ids]
        )

    monkeypatch.setattr(rerank_mod.LLMClient, "complete", fake_complete)
    result = runner.invoke(cli.app, ["recommend", "--out", "runs"])
    assert result.exit_code == 0, result.output
    _, tmp = env
    text = next((tmp / "runs").glob("*.md")).read_text(encoding="utf-8")
    assert "中文摘要2610.00001" in text
    assert "LLM 9" in text
