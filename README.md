# 🛡️ Legion NVR

Лёгкий open-source NVR (Network Video Recorder) на Python для Windows/Linux.

**Возможности:**
- 🔐 Веб-интерфейс с авторизацией (Flask + Flask-Login)
- 📷 Подключение IP-камер по RTSP
- 🎥 HLS-стриминг с низкой задержкой (ffmpeg)
- 🔍 Детектор движения (OpenCV)
- 📡 MQTT-уведомления (совместимость с Home Assistant)
- 📼 Запись по тревоге + кольцевая запись
- 🗂️ SQLite для хранения настроек и событий

**Архитектура:**
┌─────────────────┐ MQTT ┌─────────────────┐
│ Motion Detector │──────────────→│ Stream Engine │
│ (OpenCV) │ motion_start │ (ffmpeg HLS+REC)│
└─────────────────┘ └─────────────────┘
│
↓ HLS-файлы
┌─────────────────┐
│ Web Server │
│ (Flask: 8080) │
└─────────────────┘

**Быстрый старт (Windows):**

```powershell
# 1. Установи зависимости
pip install -r requirements.txt

# 2. Установи Mosquitto (MQTT брокер)
# https://mosquitto.org/download/

# 3. Запусти три процесса
start "Web" python web_server.py
start "Stream" python engine/streamer.py
start "Detector" python engine/detector.py

# 4. Открой браузер
http://localhost:8080

Логин по умолчанию: admin / admin123

MQTT-топики:

spartan/{id}/motion — события движения

spartan/{id}/recording — статус записи

Структура проекта:

├── web_server.py          # Flask веб-сервер
├── engine/
│   ├── detector.py        # Детектор движения (OpenCV + MQTT)
│   └── streamer.py        # HLS стриминг + запись (ffmpeg + MQTT)
├── models/                # Модели БД (SQLite)
├── web/                   # Flask blueprints (auth, routes, api)
├── templates/             # HTML шаблоны
├── static/                # CSS, JS
└── requirements.txt       # Зависимости

