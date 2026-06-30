"""
Общие утилиты
"""
import os
import time
import shutil
import glob
from engine.shared.constants import C_CYAN, C_RESET
from models.database import get_db


def ts():
    """Возвращает цветную временную метку [HH:MM:SS]"""
    return f"{C_CYAN}[{time.strftime('%H:%M:%S')}]{C_RESET}"


def find_ffmpeg():
    """Ищет ffmpeg в системе"""
    if shutil.which("ffmpeg"):
        return "ffmpeg"
    for p in ["C:/ffmpeg/bin/ffmpeg.exe", "C:/ffmpeg/ffmpeg.exe", "/usr/bin/ffmpeg"]:
        if os.path.exists(p):
            return p
    return None


def get_recordings_path():
    """Получает путь к папке записей из настроек"""
    try:
        with get_db() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key='recordings_path'").fetchone()
        if row:
            return row[0]
    except:
        pass
    return "recordings"


def load_cameras():
    """Загружает все включённые камеры"""
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM cameras WHERE enabled=1").fetchall()
    return [dict(r) for r in rows]


def load_detector_cameras():
    """Загружает камеры с включённым детектором"""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM cameras WHERE enabled=1 AND motion_enabled=1"
        ).fetchall()
    return [dict(r) for r in rows]