# Показать последние 50 строк лога выбранного сервиса
param(
    [string]$Service = "web_server",
    [int]$Lines = 50
)

$logFile = "logs\$Service.log"
if (Test-Path $logFile) {
    Get-Content $logFile -Tail $Lines
} else {
    Write-Host "Файл $logFile не найден" -ForegroundColor Red
}