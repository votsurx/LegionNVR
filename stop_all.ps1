# stop_all.ps1
Write-Host "========================================" -ForegroundColor Red
Write-Host "  🛑  LEGION NVR - STOP ALL SERVICES" -ForegroundColor Red
Write-Host "========================================" -ForegroundColor Red
Write-Host ""
Write-Host "Останавливаем все сервисы..." -ForegroundColor Yellow
Write-Host ""

# Убиваем все Python процессы
Get-Process -Name python -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Host "  ⏹️  Остановлен PID: $($_.Id)" -ForegroundColor Red
    Stop-Process -Id $_.Id -Force
}

Write-Host ""
Write-Host "✅ ВСЕ СЕРВИСЫ ОСТАНОВЛЕНЫ!" -ForegroundColor Green
Write-Host ""
Read-Host "Нажмите Enter для выхода"