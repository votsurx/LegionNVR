# restart_web.ps1
param(
    [int]$PIDtoKill = 0
)

Write-Host "🔄 Перезапуск Web Server..." -ForegroundColor Yellow

# Ждём пока старый процесс умрёт
Start-Sleep -Seconds 2

# Убиваем если ещё жив
if ($PIDtoKill -gt 0) {
    try {
        Stop-Process -Id $PIDtoKill -Force -ErrorAction SilentlyContinue
        Write-Host "⏹️ Процесс $PIDtoKill остановлен" -ForegroundColor Green
    } catch {
        Write-Host "⚠️ Не удалось остановить процесс $PIDtoKill" -ForegroundColor Yellow
    }
}

Start-Sleep -Seconds 1

# Запускаем новый веб-сервер
$scriptPath = Split-Path -Parent $MyInvocation.MyCommand.Path
Start-Process pwsh -ArgumentList "-NoExit -ExecutionPolicy Bypass -File `"$scriptPath\start_web.ps1`"" -WindowStyle Normal

Write-Host "✅ Web Server перезапущен!" -ForegroundColor Green