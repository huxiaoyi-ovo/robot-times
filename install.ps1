# The Robot Times 一键安装（Windows）：装好本地版，开机自动启动，打开看板。
#
# 在仓库文件夹里，用 PowerShell 运行：
#   powershell -ExecutionPolicy ByPass -File install.ps1
# 可以重复运行，已经装好的步骤会跳过。
$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$Dir = $PSScriptRoot
$Data = if ($env:ROBOT_TIMES_HOME) { $env:ROBOT_TIMES_HOME } else { Join-Path $HOME ".robot-times" }
$Url = "http://localhost:8765/"

function Say($m) { Write-Host "`n$m" -ForegroundColor White }
function Ok($m) { Write-Host "  √ $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  ! $m" -ForegroundColor Yellow }
function Die($m) { Write-Host "`n× $m" -ForegroundColor Red; exit 1 }

if (-not (Test-Path (Join-Path $Dir "core\pyproject.toml"))) { Die "请在仓库文件夹里运行这个脚本（要能看到 core 文件夹）。" }
Set-Location $Dir
Ok "代码在 $Dir"

# ---------- 1. uv ----------
Say "1/5 检查 uv"
$env:Path = "$HOME\.local\bin;$env:Path"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  $env:Path = "$HOME\.local\bin;$env:Path"
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { Die "uv 没装上，可以按 https://docs.astral.sh/uv/ 手动安装后再运行。" }
Ok (uv --version)

# ---------- 2. 依赖 ----------
Say "2/5 安装依赖（第一次要一两分钟）"
$extra = @()
uv run --no-sync python -c "import sentence_transformers" 2>$null
if ($LASTEXITCODE -eq 0) { $extra = @("--extra", "embed") }
else {
  $ans = Read-Host "  要装更准的向量模型吗？推荐更准，但要下载 1–2 GB（PyTorch）。[y/N]"
  if ($ans -match "^[yY]") { $extra = @("--extra", "embed") }
}
uv sync @extra
if ($LASTEXITCODE -ne 0) { Die "依赖没装上，看上面的报错。" }
Ok "依赖装好了"

# ---------- 3. Zotero ----------
Say "3/5 检查 Zotero"
while ($true) {
  try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 "http://localhost:23119/api/" | Out-Null; break }
  catch {
    Warn "连不上 Zotero。请："
    Write-Host "     1) 打开 Zotero（需要 10 或更新版本：帮助 → 关于 Zotero 里看版本号）"
    Write-Host "     2) 编辑 → 设置 → 高级，勾选“允许此计算机上的其他应用程序与 Zotero 通信”"
    $ans = Read-Host "  弄好了按回车重试，输入 q 退出"
    if ($ans -match "^[qQ]") { Die "已退出。Zotero 准备好以后再运行一次。" }
  }
}
Ok "Zotero 已连接"

# ---------- 4. 设置 ----------
Say "4/5 设置"
$initArgs = @()
$already = uv run --no-sync python -c "from robot_times.cloud import repo; from robot_times.local.app import open_store; print(repo.load_settings(open_store()).get('interests',''))" 2>$null
if ($already) { Ok "研究兴趣：$already（要改就在看板的“设置”里改）" }
else {
  Write-Host "  研究兴趣写一两句话，越具体推荐越准。例如：足式机器人运动控制、人形机器人全身控制、sim2real"
  $interests = Read-Host "  你的研究兴趣"
  if ($interests) { $initArgs += @("--interests", $interests) }
  Write-Host "  默认看机器人方向（arXiv 的 cs.RO）。其他方向填 arXiv 分类，例如 cs.LG；直接回车用默认。"
  $cats = Read-Host "  arXiv 分类"
  if ($cats) { $initArgs += @("--preset", "general", "--categories", $cats) }
  $folder = Read-Host "  “加入 Zotero”时放进哪个文件夹（回车就放在“我的文库”）"
  if ($folder) { $initArgs += @("--zotero-folder", $folder) }
}

New-Item -ItemType Directory -Force -Path $Data | Out-Null
$envFile = Join-Path $Data ".env"
if ((Test-Path $envFile) -and (Select-String -Path $envFile -Pattern '^(DEEPSEEK_API_KEY|ROBOT_TIMES_LLM_API_KEY)=.+' -Quiet)) {
  Ok "已配置大模型密钥（$envFile）"
} else {
  Write-Host "  大模型是可选的，但强烈建议：有了它才有中文摘要、全文解读、精排和“问 AI”。"
  Write-Host "  DeepSeek 的密钥在 https://platform.deepseek.com 申请（每天几毛钱）。输入时不显示，回车跳过。"
  $sec = Read-Host "  DeepSeek 密钥" -AsSecureString
  $key = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
  if ($key) {
    $lines = @(); if (Test-Path $envFile) { $lines = Get-Content $envFile | Where-Object { $_ -notmatch '^DEEPSEEK_API_KEY=' } }
    $lines += "DEEPSEEK_API_KEY=$key"
    [IO.File]::WriteAllLines($envFile, [string[]]$lines)  # UTF-8 不带 BOM，免得读不出密钥
    Ok "密钥存在 $envFile（只在你电脑上）"
  } else { Warn "跳过了。以后想加：在 $envFile 里写一行 DEEPSEEK_API_KEY=你的密钥，再运行 uv run robot-times local restart" }
}

# ---------- 5. 初始化 ----------
Say "5/5 初始化（Zotero 会弹窗问是否允许写入，请点 Always Allow）"
uv run --no-sync robot-times local init @initArgs
if ($LASTEXITCODE -ne 0) { Die "初始化没成功，看上面的提示。" }

Say "装好了：$Url"
Write-Host "  · 以后开机会自动在后台启动，直接打开上面的地址，或双击桌面上的“The Robot Times”"
Write-Host "  · 第一次打开会自动生成今天的推荐，要几分钟，页面上有进度提示"
Write-Host "  · 不想开机启动：在 $Dir 里运行 uv run robot-times local autostart --off"
Start-Sleep -Seconds 3
Start-Process $Url
