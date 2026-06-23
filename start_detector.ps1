# start_detector.ps1
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  🔍  LEGION NVR - MOTION DETECTOR" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "📡 MQTT: 127.0.0.1:1883" -ForegroundColor Green
Write-Host ""

# Переходим в папку скрипта
$scriptPath = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptPath

# Запускаем Detector
python engine/detector.py