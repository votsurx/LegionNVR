"""
Конфигурация проекта
Загружается из БД или из дефолтных значений
"""
import os
from models.database import get_db

def get_config():
    """Загружает все настройки из БД"""
    config = {
        "recordings_path": "recordings",           # для MP4 (тревожные)
        "hls_recordings_path": "recordings",       # для HLS-сегментов (24/7)
        "streams_path": "streams",
        "ffmpeg_path": None,
        "hls_time": 1,
        "hls_list_size": 0,
        "record_retention_days": 7,
        "mqtt_broker": "127.0.0.1",
        "mqtt_port": 1883,
        "mqtt_username": "",
        "mqtt_password": ""
    }

    try:
        with get_db() as conn:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
            for row in rows:
                key = row['key']
                value = row['value']
                if key in config:
                    # Преобразуем типы
                    if key in ("mqtt_port", "hls_time", "hls_list_size", "record_retention_days"):
                        config[key] = int(value)
                    elif key == "recordings_path":
                        config[key] = value
                    elif key == "streams_path":
                        config[key] = value
                    elif key == "ffmpeg_path":
                        config[key] = value
    except:
        pass

    return config