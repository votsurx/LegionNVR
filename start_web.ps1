# start_web.ps1
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  🖥️  LEGION NVR - WEB SERVER" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "🌐 http://localhost:8080" -ForegroundColor Green
Write-Host ""

# Переходим в папку скрипта
$scriptPath = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptPath

# Запускаем Web Server
python web_server.py