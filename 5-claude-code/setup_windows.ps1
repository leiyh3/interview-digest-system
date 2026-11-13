# 访谈精读：Windows 一键准备
# 用法（PowerShell）：powershell -ExecutionPolicy Bypass -File setup_windows.ps1 [工作区路径]
# 默认工作区：%USERPROFILE%\digest-workspace。重复运行是安全的：会更新 skill 和工具，不覆盖你的 CLAUDE.md、数据和每期文件。
param([string]$WS = "$HOME\digest-workspace")
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"
$HERE = Split-Path -Parent $MyInvocation.MyCommand.Path
$PKG = Split-Path -Parent $HERE

Write-Host "== 1/4 检查并安装 ffmpeg 和 Python（winget）"
$needRestart = $false
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
  winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
  $needRestart = $true
}
if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
  winget install --id Python.Python.3.12 -e --accept-source-agreements --accept-package-agreements
  $needRestart = $true
}
if ($needRestart) {
  Write-Host ""
  Write-Host "刚装了 ffmpeg 或 Python。请关掉这个窗口，开一个新的 PowerShell，再运行一次本脚本（让 PATH 生效）。"
  exit 0
}

Write-Host "== 2/4 建工作区：$WS"
foreach ($d in "", "data", "episodes", "tools", ".claude", ".claude\agents", ".claude\commands", ".claude\skills") {
  New-Item -ItemType Directory -Force -Path (Join-Path $WS $d) | Out-Null
}
if (-not (Test-Path "$WS\CLAUDE.md")) { Copy-Item "$HERE\workspace-template\CLAUDE.md" $WS }
if (-not (Test-Path "$WS\.claude\settings.json")) { Copy-Item "$HERE\workspace-template\.claude\settings.json" "$WS\.claude\" }
Copy-Item "$HERE\workspace-template\.claude\agents\*" "$WS\.claude\agents\" -Force
Copy-Item "$HERE\workspace-template\.claude\commands\*" "$WS\.claude\commands\" -Force
Copy-Item "$HERE\workspace-template\tools\*" "$WS\tools\" -Force
Copy-Item "$PKG\2-cover-studio\cover-studio.html" "$WS\tools\" -Force
foreach ($d in "scout", "scout\inbox", "scout\pool", "scout\captions") {
  New-Item -ItemType Directory -Force -Path (Join-Path $WS $d) | Out-Null
}
foreach ($f in "sources.json", "pool.md") {
  if (-not (Test-Path "$WS\scout\$f")) { Copy-Item "$HERE\workspace-template\scout\$f" "$WS\scout\" }
}
if (Test-Path "$WS\.claude\skills\interview-digest-video") { Remove-Item -Recurse -Force "$WS\.claude\skills\interview-digest-video" }
Copy-Item -Recurse "$PKG\1-skill\interview-digest-video" "$WS\.claude\skills\"

Write-Host "== 3/4 Python 虚拟环境和依赖（第一次装 whisper 要下载 PyTorch，比较久）"
Set-Location $WS
if (-not (Test-Path ".venv")) { py -3 -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install --upgrade pip | Out-Null
& .\.venv\Scripts\python.exe -m pip install pillow numpy openai-whisper yt-dlp

Write-Host "== 4/4 检查环境并试跑"
& .\.venv\Scripts\python.exe tools\check_env.py --smoke

Write-Host ""
Write-Host "准备好了。接下来："
Write-Host "  1. 用记事本打开 $WS\CLAUDE.md，填账号信息"
Write-Host "  2. cd $WS ; claude"
Write-Host "  3. 每日素材：运行 .venv\Scripts\python.exe tools\scout.py check 检查素材源，再运行 tools\register_daily_scout.ps1 设定时任务"
Write-Host "  4. 在 Claude 里输入 /scout 挑选题，或 /digest episodes/<某一期> 做视频"
