<div align="center">

# The Robot Times

**Your own morning newspaper of new papers**

Picks the arXiv papers worth your time each day, writes a short review of each, plans your reading, and adds them to Zotero in one click.

[Quick start](#quick-start) · [Daily use](#daily-use) · [FAQ](#faq) · [中文](README.md)

![Today's edition](docs/images/today.png)

</div>

> The interface and the generated summaries are currently in Chinese.

## What it does

- **Picks papers**: learns your interests from your Zotero library and a short description, and selects a dozen papers from each day's arXiv release.
- **Reviews them**: a translated title and abstract for every pick; for the key papers it reads the full text and writes a short critical review with the main figures.
- **Plans your reading**: one paper to read closely, a few abstracts, titles to skim — sized to the time you have each day.
- **Adds to Zotero**: one click files the paper into a collection you choose, with the PDF and a summary note.
- **Learns from you**: "more like this", "less like this", adding to Zotero and marking as read all shape future picks.
- **Also**: a daily industry brief, a weekly review with next week's plan, and "ask AI" about any paper.

Robotics is the default; switch fields in Settings.

## Quick start

Runs on your own computer; your data stays local. About 10 minutes.

1. **Zotero**: install [Zotero](https://www.zotero.org/download/) 10 or newer, open it, and enable
   *Settings → Advanced → Allow other applications on this computer to communicate with Zotero*.
2. **Install**:
   - Linux / macOS: `curl -LsSf https://raw.githubusercontent.com/huxiaoyi-ovo/robot-times/main/install.sh | bash`
   - Windows: [download the code](https://github.com/huxiaoyi-ovo/robot-times/archive/refs/heads/main.zip), unzip, open PowerShell in the folder and run
     `powershell -ExecutionPolicy ByPass -File install.ps1`

   The installer asks for your research interests, arXiv categories, a Zotero collection, and an optional LLM key.
   Click **Always Allow** when Zotero asks for permission.
3. **Open** <http://localhost:8765>. The first edition takes a few minutes. The dashboard starts at login from then on.

**LLM key** (optional, recommended): without it you get English abstracts only. With it you get translations, reviews, better ranking and "ask AI".
DeepSeek is the default (a few cents a day); any OpenAI-compatible endpoint works, including Ollama. The key stays in `~/.robot-times/.env`.

**Other ways to install**: ask a coding agent (Claude Code, Codex…) to "set up The Robot Times" — the bundled
[setup skill](.claude/skills/robot-times-setup/SKILL.md) guides it. Or run the **cloud version** (GitHub Actions + Cloudflare, free tiers, readable on your phone):
see [docs/dev/cloud-setup.md](docs/dev/cloud-setup.md).

## Daily use

1. Open the dashboard in the morning: today's plan is ready.
2. Open a paper for the summary, review and figures; click **Add to Zotero** if it is useful.
3. Click "more like this" / "less like this" to steer future picks.
4. Take notes in the paper view; they go to Zotero with the paper.
5. On Sunday evening, read the weekly review.

## FAQ

- **Can't connect to Zotero**: make sure Zotero 10+ is running with the local API enabled (step 1).
- **Picks are off-topic**: describe your interests more precisely, and give feedback for a few days. A Zotero library with fewer than 10 items gives weak signals.
- **Where is my data?** Everything is in `~/.robot-times`; PDFs and notes go to your local Zotero. Only paper titles and abstracts are sent to the LLM you configure.

More: [docs/dev/local-setup.md](docs/dev/local-setup.md) (Chinese).

## Development

```bash
uv sync && uv run pytest && uv run ruff check .
cd web/app && npm install && npm test
```

Design overview: [docs/architecture.md](docs/architecture.md). New research fields are presets in [`presets.py`](core/src/robot_times/presets.py).

## Credits

Paper data from [arXiv](https://arxiv.org) (rate-limited, no PDF re-hosting) and [Hugging Face Papers](https://huggingface.co/papers).
Zotero is a trademark of the Corporation for Digital Scholarship; this is an independent tool, not affiliated with Zotero or arXiv.

[MIT License](LICENSE)
