# start_all.ps1
Write-Host "========================================" -ForegroundColor Magenta
Write-Host "  🛡️  LEGION NVR - START ALL SERVICES" -ForegroundColor Magenta
Write-Host "========================================" -ForegroundColor Magenta
Write-Host ""
Write-Host "Запускаем все сервисы..." -ForegroundColor Yellow
Write-Host ""

# Путь к папке скрипта
$scriptPath = Split-Path -Parent $MyInvocation.MyCommand.Path

# Запускаем Web Server
Start-Process pwsh -ArgumentList "-NoExit -ExecutionPolicy Bypass -File `"$scriptPath\start_web.ps1`"" -WindowStyle Normal
Start-Sleep -Seconds 1

# Запускаем Detector
Start-Process pwsh -ArgumentList "-NoExit -ExecutionPolicy Bypass -File `"$scriptPath\start_detector.ps1`"" -WindowStyle Normal
Start-Sleep -Seconds 1

# Запускаем Streamer
Start-Process pwsh -ArgumentList "-NoExit -ExecutionPolicy Bypass -File `"$scriptPath\start_streamer.ps1`"" -WindowStyle Normal

Write-Host ""
Write-Host "✅ ВСЕ СЕРВИСЫ ЗАПУЩЕНЫ!" -ForegroundColor Green
Write-Host ""
Write-Host "Окна:"
Write-Host "  🖥️  Web Server     - http://localhost:8080" -ForegroundColor Cyan
Write-Host "  🔍  Motion Detector - engine/detector/main.py" -ForegroundColor Cyan
Write-Host "  🎥  Stream Engine  - engine/streamer/main.py" -ForegroundColor Cyan
Write-Host ""
Write-Host "Нажмите любую клавишу для выхода..." -ForegroundColor Yellow
Read-Host