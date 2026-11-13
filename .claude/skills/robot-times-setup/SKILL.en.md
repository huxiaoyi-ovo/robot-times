---
name: robot-times-setup
description: Install, configure, upgrade or troubleshoot The Robot Times (daily arXiv paper digest for Zotero) — local version on this computer, or the cloud version on GitHub Actions + Cloudflare. Use when the user asks to set it up, says it doesn't work, wants to change research direction, or wants to move from local to cloud.
---

# The Robot Times: install, configure and troubleshoot it for the user

The Robot Times picks the arXiv papers a researcher should read each day, writes a short review of each (in English or Chinese),
plans the reading, and adds papers to Zotero in one click; it also has a daily industry brief and a weekly review.
Repo: <https://github.com/huxiaoyi-ovo/robot-times>. These instructions are self-contained: **you don't need to download the repo first**.
This is the English version of `SKILL.md` in the same folder; keep the two in sync.

Your job is to install it, set it up and confirm it works. The user only does the three things only they can do:
type an admin password once (Linux / macOS packages), click **Always Allow** in the Zotero pop-up, and paste their own LLM key. You do the rest.

**Talk to the user in their language (English or Chinese), plainly; after each step say the result in one sentence, without long explanations.**

## 1. Ask first (all at once)

0. **Language**: whether the dashboard and generated content (gists, reviews, briefs, weekly reviews, Ask AI) should be English or Chinese.
   Default to the language the user is talking to you in; no need to ask.
1. **Research interests**: a sentence or two, as specific as possible (e.g. "legged locomotion control, humanoid whole-body control, sim2real; no pure vision detection").
2. **Field**: robotics (default, arXiv cs.RO) or something else (needs arXiv categories, e.g. `cs.LG`, or `cs.CV,cs.AI`).
3. **Zotero collection** (optional): which existing collection "Add to Zotero" should use; can also be picked from a list after install.
4. **LLM key** (optional, strongly recommended): whether they have a DeepSeek key or another OpenAI-compatible API key.
   Without one they only get the original abstracts, no reviews and no "Ask AI".
5. Only go for the cloud version (section 6) if the user mentions their phone, the cloud, or not wanting to keep the computer on; install the local version by default.

## 2. Rules for keys

- **Don't ask the user to paste keys into the chat.** After install, have them paste it into the setup guide on the dashboard (it's tested right away).
  If they already sent it, use it (see 4.5), but remind them they can rotate it in the DeepSeek console later.
- Keys are stored in `~/.robot-times/.env` (Windows: `%USERPROFILE%\.robot-times\.env`), readable only by the user.

## 3. Install (local version)

First make sure **Zotero 10 or newer** is installed (common Linux locations: `~/Zotero/zotero`, `/opt/zotero`, flatpak `org.zotero.Zotero`;
macOS: `/Applications/Zotero.app`; Windows: Start menu). If not, ask the user to install it from <https://www.zotero.org/download/> and open it once.

Then install The Robot Times. The packages are at `https://github.com/huxiaoyi-ovo/robot-times/releases/latest/download/<file name>`:

| System | File name | Install |
| --- | --- | --- |
| Linux x86_64 (`uname -m` is `x86_64`) | `robot-times_amd64.deb` | `sudo apt install ./robot-times_amd64.deb` |
| Linux ARM (`aarch64`) | `robot-times_arm64.deb` | same as above |
| macOS | `RobotTimes-macOS.pkg` | `sudo installer -pkg RobotTimes-macOS.pkg -target /` |
| Windows | `RobotTimes-Windows-Setup.exe` | `RobotTimes-Windows-Setup.exe /VERYSILENT /SUPPRESSMSGBOXES` (no admin needed) |

- Download to a temp folder with `curl -LO` or PowerShell's `Invoke-WebRequest -OutFile`.
- For the command that needs a password, if you can't type it for the user: give them the exact command to run in a terminal
  (or have them double-click the downloaded package), and ask them to tell you when it's done.
- If the Linux distro isn't Debian / Ubuntu based (no `apt`), or the user doesn't want a system install: use "Appendix: command-line install" at the end.

After installing, start the dashboard (the Linux / macOS packages usually open the browser by themselves; if not, start it by hand):

```bash
/opt/robot-times/launch open                                         # Linux
open "/Applications/The Robot Times.app"                             # macOS
wscript "%LOCALAPPDATA%\Programs\The Robot Times\launch.vbs" open    # Windows
```

The first start downloads Python and dependencies, about a minute (log: `~/.robot-times/setup.log`). Wait until this returns JSON:

```bash
curl -s --noproxy localhost http://localhost:8765/api/job
```

If it keeps returning `preparing`, check the preparing page's state with `curl -s --noproxy localhost http://localhost:8765/status`:
`failed` true means it has already failed (it isn't still preparing); `status`, `hint` and `log` give the reason and the log.
Fix that, then open the app again.

From then on it starts in the background at login and updates itself; nothing else to manage.

## 4. Setup (the same API as the setup guide on the page)

Once the dashboard is up, opening <http://localhost:8765> shows a four-step setup guide the user can click through themselves
(the 中文 / English switch at the top picks the language). You can also do it for them with the API below: writes need
`Content-Type: application/json` and only work from this computer.
On Windows use `curl.exe` (not PowerShell's `curl` alias). Below, `curl` is short for `curl -s --noproxy localhost`.
Add `-H "X-Lang: en"` to get errors and hints in English (without it, the language in the settings is used; Chinese until it's set).

### 4.1 Where things stand

```bash
curl http://localhost:8765/api/setup
```

In the reply, `zotero` has `running` (is Zotero open), `versionOk` (10 or newer), `localApi` (local API enabled),
`authorized` (write access granted) and `canEnable` (whether you can enable the local API for the user); plus `settings`, `llm.configured` and `done`.

### 4.2 Enable Zotero's local API (when `localApi` is false)

Either:
- ask the user to tick "Allow other applications on this computer to communicate with Zotero" in Zotero's **Settings → Advanced**; or
- do it yourself (when `canEnable` is true): ask the user to **quit Zotero completely** (not just minimize), confirm `zotero.running` is false, then

  ```bash
  curl -X POST -H "Content-Type: application/json" http://localhost:8765/api/setup/enable-api
  ```

  and ask the user to open Zotero again; wait for `localApi` to become true.

### 4.3 Authorize writes (when `authorized` is false)

**Tell the user first**: "Zotero is about to show a pop-up; switch to it and click **Always Allow** (not Allow)." Then:

```bash
curl -m 200 -X POST -H "Content-Type: application/json" http://localhost:8765/api/setup/authorize
```

This waits for the user's click (up to 3 minutes). If the error says it was allowed only once, they clicked Allow; ask them to do it again.

### 4.4 Pick the Zotero collection (optional)

```bash
curl http://localhost:8765/api/setup/collections      # existing collection names in the user's Zotero
```

The collection from step 1 must be in this list; otherwise ask them to pick one from it, or leave it empty ("My Library").

### 4.5 LLM (optional)

Ask the user to open <http://localhost:8765>, paste the key in the "LLM" step of the setup guide and save (it's tested right away).
Use the API only if the user already sent you the key (leave `base` and `model` out for DeepSeek):

```bash
curl -X PUT -H "Content-Type: application/json" -d '{"key":"sk-..."}' http://localhost:8765/api/setup/llm
# Other OpenAI-compatible APIs: {"key":"...","base":"https://.../v1","model":"model-name"}; local Ollama: {"key":"ollama","base":"http://localhost:11434/v1","model":"qwen2.5"}
```

On a 400, `error` says why (wrong key, insufficient balance, wrong URL or model name); pass that on to the user.

### 4.6 Finish

```bash
# Robotics; lang is "en" or "zh"
curl -X POST -H "Content-Type: application/json" \
  -d '{"interests":"research interests","zoteroCollection":"collection name or empty string","lang":"en"}' http://localhost:8765/api/setup/finish
# Other fields: add "preset":"general","categories":["cs.LG","cs.AI"]
```

Generating today's picks starts right away (the first run takes a few minutes).

## 5. Confirm it works

1. `curl http://localhost:8765/api/setup`: `done` is true and `zotero.authorized` is true.
2. `curl http://localhost:8765/api/job`: `running` is `daily` while generating; when it's back to null with no error in `last`, it's done.
3. Ask the user to open <http://localhost:8765> and check today's papers are there; have them click "Add to Zotero" on one and
   check the item, PDF and note show up in Zotero.
4. Tell the user: **The Robot Times** in their app list is the way in (they can pin it to the Dock / taskbar);
   research interests, language and the rest can be changed later under Settings on the dashboard.

A more detailed self-check: `~/.robot-times/app/.venv/bin/robot-times doctor`
(Windows: `%USERPROFILE%\.robot-times\app\.venv\Scripts\robot-times.exe doctor`); Zotero, write access and the LLM should all be ✓.

## 6. Cloud version

Follow `docs/dev/cloud-setup.md` step by step (clone first: `git clone https://github.com/huxiaoyi-ovo/robot-times.git`; the doc is in Chinese).
What you can do for the user (when they're logged in to the relevant CLI):
- Create and initialize the database: in `web/app`, `npx wrangler d1 create robot-times`, put the ID in `wrangler.jsonc`, then `npx wrangler d1 migrations apply DB --remote`.
- Set GitHub secrets: `gh secret set ZOTERO_USER_ID` and so on (let the user paste the values in the terminal).
- Run the first job by hand: `gh workflow run daily.yml`, then `gh run watch`.

What only the user can do on the web: create the Zotero API key, the Cloudflare API token and the DeepSeek key; import the repo to create the Worker;
turn on Cloudflare Access and allow only their own email; create a GitHub fine-grained token (this repo only, Actions read/write) and put it in the Worker's `GITHUB_TOKEN`.
Give them links they can click, one step at a time. Cloud keys go only into the repo's GitHub Actions secrets and the Cloudflare Worker's Variables and Secrets.
Once the dashboard is up, they can switch it to English under Settings → Language.

### Moving from local to cloud

Set up the cloud version first, then export locally and import (with the package install, `robot-times` is in `~/.robot-times/app/.venv/bin/`):

```bash
robot-times local export robot-times.sql
cd web/app && npx wrangler d1 execute DB --remote --file ../../robot-times.sql
```

Once the cloud dashboard shows the old picks and notes, the local version can be uninstalled (see "卸载" (Uninstall) in `docs/dev/local-setup.md`).

## 7. Troubleshooting

Ask the dashboard first (the same API as Settings → "Check-up and troubleshooting"):

```bash
curl http://localhost:8765/api/doctor                 # item by item: ok false is a problem, hint is the fix
curl "http://localhost:8765/api/logs?name=serve&lines=200"   # dashboard log; name=setup is the first-run dependency log
curl http://localhost:8765/api/job                    # last has the last run's result; on failure, title and hint explain why
curl -X POST -H "Content-Type: application/json" http://localhost:8765/api/restart   # restart the dashboard
```

The user can click through **Settings → Check-up and troubleshooting** too; when they need help, ask them to click "Copy diagnostics" and send it to you (no keys included).

| Symptom | Cause and fix |
| --- | --- |
| Stuck on "Getting ready" at first start | Check `~/.robot-times/setup.log`; usually downloading Python / dependencies failed over the network. Reopen the app once the network is fine |
| Can't reach Zotero / 403 | Zotero isn't open, or the local API isn't enabled (4.2); Zotero below 10 can't accept writes |
| "Add to Zotero" asks every time | Allow was chosen instead of Always Allow: delete `~/.robot-times/zotero_key`, restart the dashboard and redo 4.3 |
| WSL can't reach Zotero on Windows | Install the Windows version instead; or add `networkingMode=mirrored` under `[wsl2]` in `%UserProfile%\.wslconfig` |
| Dashboard won't open | Reopen the app; if that fails, check `~/.robot-times/serve.log` |
| Stuck on "Generating" or generation fails | Check `~/.robot-times/serve.log`; arXiv rate limits (429) are retried automatically and fall back to RSS; LLM 401/402 means a wrong key or no balance |
| Picks are off-topic | Make the research interests more specific; a Zotero library with fewer than 10 items gives weak signals; use "More like this / Fewer like this" |
| Port 8765 in use | Usually an older command-line install is still running: in that code folder run `uv run robot-times local stop` and `uv run robot-times local autostart --off` |
| Cloud version doesn't run on time | Without `GITHUB_TOKEN` on the Worker it relies on GitHub's own scheduler, which is often hours late; set it up per cloud-setup.md |

More detail: `docs/dev/local-setup.md`, `docs/dev/cloud-setup.md` (in Chinese).

## Appendix: command-line install (no admin rights, or the user wants to change the code)

```bash
uv --version || curl -LsSf https://astral.sh/uv/install.sh | sh          # Windows: powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
git clone --depth 1 https://github.com/huxiaoyi-ovo/robot-times.git ~/robot-times && cd ~/robot-times
uv sync --frozen                                                         # lightweight embedding model (fastembed) by default; --extra embed for the PyTorch one (1–2 GB more)
uv run robot-times local autostart                                       # start at login, and start the dashboard in the background now
```

Then set it up from section 4, same as the package install. (`install.sh` / `install.ps1` ask questions in the terminal and are meant
for the user to run themselves; when you're doing it, use the commands above.)
