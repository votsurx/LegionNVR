# setup.ps1 — Установка Legion NVR V4.0
Write-Host "========================================" -ForegroundColor Magenta
Write-Host "  🛡️  LEGION NVR V4.0 — INSTALLER" -ForegroundColor Magenta
Write-Host "========================================" -ForegroundColor Magenta
Write-Host ""

# Проверка прав администратора
if (-NOT ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")) {
    Write-Host "❌ Запустите от имени Администратора!" -ForegroundColor Red
    pause
    exit
}

# 1. Проверка Python
Write-Host "[1/5] Проверка Python..." -ForegroundColor Yellow
`$python = Get-Command python -ErrorAction SilentlyContinue
if (-not `$python) {
    Write-Host "❌ Python не найден!" -ForegroundColor Red
    Write-Host "   Скачать: https://www.python.org/downloads/" -ForegroundColor Yellow
    Write-Host "   ☑️ Add Python to PATH при установке!" -ForegroundColor Yellow
    pause
    exit
}
Write-Host "✅ Python найден" -ForegroundColor Green

# 2. Установка зависимостей
Write-Host "[2/5] Установка Python-зависимостей..." -ForegroundColor Yellow
pip install flask flask-login paho-mqtt opencv-python werkzeug
pip install ultralytics
Write-Host "✅ Зависимости установлены" -ForegroundColor Green

# 3. Проверка ffmpeg
Write-Host "[3/5] Проверка ffmpeg..." -ForegroundColor Yellow
`$ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
if (-not `$ffmpeg) {
    if (Test-Path "C:\ffmpeg\bin\ffmpeg.exe") {
        Write-Host "✅ ffmpeg найден в C:\ffmpeg\bin\" -ForegroundColor Green
        `$env:Path += ";C:\ffmpeg\bin"
    } else {
        Write-Host "⚠️ ffmpeg не найден!" -ForegroundColor Yellow
        Write-Host "   Скачать: https://ffmpeg.org/download.html" -ForegroundColor Yellow
        Write-Host "   Распаковать в C:\ffmpeg" -ForegroundColor Yellow
    }
} else {
    Write-Host "✅ ffmpeg найден" -ForegroundColor Green
}

# 4. Проверка Mosquitto
Write-Host "[4/5] Проверка Mosquitto MQTT..." -ForegroundColor Yellow
`$mosquitto = Get-Service mosquitto -ErrorAction SilentlyContinue
if (-not `$mosquitto) {
    Write-Host "⚠️ Mosquitto не установлен (можно позже)" -ForegroundColor Yellow
    Write-Host "   Скачать: https://mosquitto.org/download/" -ForegroundColor Yellow
} else {
    if (`$mosquitto.Status -ne "Running") {
        Start-Service mosquitto
    }
    Write-Host "✅ Mosquitto работает" -ForegroundColor Green
}

# 5. Создание папок
Write-Host "[5/5] Создание папок..." -ForegroundColor Yellow
New-Item -ItemType Directory -Force -Path streams, recordings | Out-Null
Write-Host "✅ Папки созданы" -ForegroundColor Green

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  ✅ УСТАНОВКА ЗАВЕРШЕНА!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Запуск: .\start_all.ps1" -ForegroundColor Cyan
Write-Host "Веб: http://localhost:8080" -ForegroundColor Cyan
Write-Host "Логин: admin / admin123" -ForegroundColor Cyan
Write-Host ""
pause
