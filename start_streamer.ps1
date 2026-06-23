# start_streamer.ps1
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  🎥  LEGION NVR - STREAM ENGINE" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "📡 MQTT: 127.0.0.1:1883" -ForegroundColor Green
Write-Host ""

# Переходим в папку скрипта
$scriptPath = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptPath

# Запускаем Streamer
python engine/streamer.py