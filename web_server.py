"""
Legion NVR - Web Server V6.0
Запуск: python web_server.py
"""
from flask import Flask
from flask_login import LoginManager
from models.database import init_db, get_db
from models.user import User
from web.auth import auth_bp, login_manager
from web import register_blueprints
import os
import threading
from engine.shared.config import get_config
from engine.health_monitor_det import DetectorHealer
from engine.health_monitor_str import StreamerHealer
from engine.health_monitor_mqtt import MQTThealer
from engine.shared.logger import get_logger

logger = get_logger("web_server")

# Создаём приложение
app = Flask(__name__)
app.secret_key = os.urandom(24).hex()

# Инициализируем Flask-Login
login_manager.init_app(app)
login_manager.login_view = 'auth.login'

# Регистрируем все blueprints
register_blueprints(app)

# Инициализация БД и создание админа
with app.app_context():
    init_db()
    if not User.get_by_username('admin'):
        User.create('admin', 'admin123', 'admin')
        logger.info("👤 Создан пользователь: admin / admin123")

if __name__ == '__main__':
    print("=" * 50)
    print("[Legion NVR] Web Server V6.0")
    print("=" * 50)
    print("[Web] http://localhost:8081")

    det_healer = DetectorHealer()
    str_healer = StreamerHealer()
    mqtt_healer = MQTThealer()
    
    threading.Thread(target=mqtt_healer.heal_loop, daemon=True).start()
    threading.Thread(target=det_healer.heal_loop, daemon=True).start()
    threading.Thread(target=str_healer.heal_loop, daemon=True).start()

    app.run(host='0.0.0.0', port=8081, threaded=True)