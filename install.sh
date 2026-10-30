#!/usr/bin/env bash
# The Robot Times 一键安装（Linux / macOS）：装好本地版，开机自动启动，打开看板。
#
# 在仓库文件夹里运行：  bash install.sh
# 或者不先下载代码：    curl -LsSf https://raw.githubusercontent.com/huxiaoyi-ovo/robot-times/main/install.sh | bash
#                      （这时会把代码下载到 ~/robot-times）
#
# 会做这些事，每一步都会说：
#   1. 没有 uv 就装上（Python 环境管理工具，会自动装好 Python）
#   2. 安装依赖
#   3. 检查 Zotero 有没有打开、有没有允许本机程序访问
#   4. 问你的研究兴趣、Zotero 文件夹、大模型密钥（可跳过）
#   5. 初始化：保存设置、申请 Zotero 写入权限、设成开机自动启动、在后台启动看板
# 可以重复运行，已经装好的步骤会跳过。
set -euo pipefail

REPO_URL="${ROBOT_TIMES_REPO:-https://github.com/huxiaoyi-ovo/robot-times.git}"
DIR="${ROBOT_TIMES_DIR:-$HOME/robot-times}"
DATA="${ROBOT_TIMES_HOME:-$HOME/.robot-times}"
URL="http://localhost:8765/"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok() { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die() { printf '\n\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }
# 通过 curl | bash 运行时标准输入是脚本本身，问问题要从终端读；没有终端（自动化）时都当作直接回车
has_tty() { { : </dev/tty; } 2>/dev/null; }
ask() {
  local __v=""
  if has_tty; then read -r -p "  $1" __v </dev/tty || true; fi
  printf '%s' "$__v"
}
ask_secret() {
  local __v=""
  if has_tty; then
    printf '  %s' "$1" >/dev/tty
    stty -echo </dev/tty 2>/dev/null || true
    read -r __v </dev/tty || true
    stty echo </dev/tty 2>/dev/null || true
    printf '\n' >/dev/tty
  fi
  printf '%s' "$__v"
}

# ---------- 0. 找到代码 ----------
here="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [ -n "$here" ] && [ -f "$here/core/pyproject.toml" ]; then
  DIR="$here"
elif [ -f "$DIR/core/pyproject.toml" ]; then
  say "更新代码（$DIR）"
  git -C "$DIR" pull --ff-only || warn "没能自动更新，用现有的代码继续"
else
  [ -n "$REPO_URL" ] || die "不知道从哪里下载代码：先 git clone 仓库再在里面运行 bash install.sh，或者设置 ROBOT_TIMES_REPO=仓库地址"
  command -v git >/dev/null || die "需要 git：Ubuntu/Debian 运行 sudo apt install git，macOS 运行 xcode-select --install"
  say "下载代码到 $DIR"
  git clone --depth 1 "$REPO_URL" "$DIR"
fi
cd "$DIR"
ok "代码在 $DIR"

# ---------- 1. uv ----------
say "1/5 检查 uv"
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
if ! command -v uv >/dev/null; then
  command -v curl >/dev/null || die "需要 curl：Ubuntu/Debian 运行 sudo apt install curl"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
command -v uv >/dev/null || die "uv 没装上，可以按 https://docs.astral.sh/uv/ 手动安装后再运行"
ok "uv $(uv --version | awk '{print $2}')"

# ---------- 2. 依赖 ----------
say "2/5 安装依赖（第一次要一两分钟）"
extra=""
if ! uv run --no-sync python -c "import sentence_transformers" >/dev/null 2>&1; then
  ans="$(ask '要装更准的向量模型吗？推荐更准，但要下载 1–2 GB（PyTorch）。[y/N] ')"
  case "$ans" in [yY]*) extra="--extra embed" ;; esac
else
  extra="--extra embed"
fi
# shellcheck disable=SC2086
uv sync --frozen $extra
ok "依赖装好了"

# ---------- 3. Zotero ----------
say "3/5 检查 Zotero"
until curl -s -o /dev/null --noproxy localhost -m 3 "http://localhost:23119/api/"; do
  warn "连不上 Zotero。请："
  echo "     1) 打开 Zotero（需要 10 或更新版本：帮助 → 关于 Zotero 里看版本号）"
  echo "     2) 编辑 → 设置（macOS 是 Zotero → 设置）→ 高级，勾选“允许此计算机上的其他应用程序与 Zotero 通信”"
  ans="$(ask '弄好了按回车重试，输入 q 退出：')"
  case "$ans" in [qQ]*) die "已退出。Zotero 准备好以后再运行一次 bash install.sh" ;; esac
done
ok "Zotero 已连接"

# ---------- 4. 问几个问题 ----------
say "4/5 设置"
args=()
already="$(uv run --no-sync python -c "
from robot_times.cloud import repo; from robot_times.local.app import open_store
print(repo.load_settings(open_store()).get('interests',''))" 2>/dev/null || true)"
if [ -n "$already" ]; then
  ok "研究兴趣：$already（要改就在看板的“设置”里改）"
else
  echo "  研究兴趣写一两句话，越具体推荐越准。例如：足式机器人运动控制、人形机器人全身控制、sim2real；不看纯视觉检测"
  interests="$(ask '你的研究兴趣：')"
  [ -n "$interests" ] && args+=(--interests "$interests")
  echo "  默认看机器人方向（arXiv 的 cs.RO）。其他方向填 arXiv 分类，例如 cs.LG 或 cs.CV,cs.AI；直接回车用默认。"
  cats="$(ask 'arXiv 分类：')"
  if [ -n "$cats" ]; then args+=(--preset general --categories "$cats"); fi
  folder="$(ask '“加入 Zotero”时放进哪个文件夹（Zotero 里已有的文件夹名，回车就放在“我的文库”）：')"
  [ -n "$folder" ] && args+=(--zotero-folder "$folder")
fi

mkdir -p "$DATA"
if grep -qsE '^(DEEPSEEK_API_KEY|ROBOT_TIMES_LLM_API_KEY)=.+' "$DATA/.env"; then
  ok "已配置大模型密钥（$DATA/.env）"
else
  echo "  大模型是可选的，但强烈建议：有了它才有中文摘要、全文解读、精排和“问 AI”。"
  echo "  DeepSeek 的密钥在 https://platform.deepseek.com 申请（每天几毛钱）。输入时不显示，回车跳过。"
  key="$(ask_secret 'DeepSeek 密钥：')"
  if [ -n "$key" ]; then
    touch "$DATA/.env" && chmod 600 "$DATA/.env"
    grep -v '^DEEPSEEK_API_KEY=' "$DATA/.env" > "$DATA/.env.tmp" || true
    printf 'DEEPSEEK_API_KEY=%s\n' "$key" >> "$DATA/.env.tmp"
    mv "$DATA/.env.tmp" "$DATA/.env" && chmod 600 "$DATA/.env"
    ok "密钥存在 $DATA/.env（只在你电脑上）"
  else
    warn "跳过了。以后想加：在 $DATA/.env 里写一行 DEEPSEEK_API_KEY=你的密钥，再运行 uv run robot-times local restart"
  fi
fi

# ---------- 5. 初始化 ----------
say "5/5 初始化（Zotero 会弹窗问是否允许写入，请点 Always Allow）"
uv run --no-sync robot-times local init ${args[@]+"${args[@]}"}

for _ in $(seq 1 20); do
  curl -s -o /dev/null --noproxy localhost -m 2 "$URL" && break
  sleep 1
done
say "装好了：$URL"
echo "  · 以后开机会自动在后台启动，直接打开上面的地址就行；也可以在应用列表里搜“The Robot Times”"
echo "  · 第一次打开会自动生成今天的推荐，要几分钟，页面上有进度提示"
echo "  · 不想开机启动：uv run robot-times local autostart --off（在 $DIR 里运行）"
if command -v xdg-open >/dev/null; then xdg-open "$URL" >/dev/null 2>&1 || true
elif command -v open >/dev/null; then open "$URL" || true
fi
