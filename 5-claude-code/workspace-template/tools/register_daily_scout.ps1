# 每天定时拉取素材（不花 Token）。在工作区目录下运行：
#   powershell -ExecutionPolicy Bypass -File tools\register_daily_scout.ps1
#   powershell -ExecutionPolicy Bypass -File tools\register_daily_scout.ps1 -Time 07:30 -Proxy http://127.0.0.1:7890
# 取消：Unregister-ScheduledTask -TaskName DigestDailyScout -Confirm:$false
param([string]$Time = "08:00", [string]$Proxy = "")
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$WS = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$py = Join-Path $WS ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Write-Host "没找到 $py，先运行准备脚本。"; exit 1 }

$setProxy = ""
if ($Proxy) { $setProxy = "set HTTPS_PROXY=$Proxy&& set HTTP_PROXY=$Proxy&& " }
$cmd = "/c cd /d `"$WS`" && set PYTHONIOENCODING=utf-8&& $setProxy`"$py`" tools\scout.py fetch >> scout\fetch.log 2>&1"

$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $cmd
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries
Register-ScheduledTask -TaskName "DigestDailyScout" -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null

Write-Host "已设置：每天 $Time 拉取素材到 $WS\scout\inbox\（电脑当时关机的话，开机后补跑）。"
Write-Host "日志：$WS\scout\fetch.log。现在先手动跑一次试试："
& $py (Join-Path $WS "tools\scout.py") fetch
