"""
Зоны детекции
"""
import json
from engine.shared.constants import *
from engine.shared.utils import ts
from models.database import get_db


def load_zones(detector):
    """Загружает зоны детекции из БД"""
    try:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT * FROM detection_zones WHERE camera_id=? AND enabled=1",
                (detector.camera["id"],)
            ).fetchall()

        detector.zones = []
        for row in rows:
            zone = dict(row)
            try:
                zone["points"] = json.loads(zone["points_json"])
                detector.zones.append(zone)
            except Exception as e:
                print(f"{ts()} {C_RED}⚠️ [{detector.camera['name']}] Ошибка парсинга зоны: {e}{C_RESET}")

        if detector.zones:
            print(f"{ts()} 🎯 [{detector.camera['name']}] Загружено зон: {len(detector.zones)}")
    except Exception as e:
        print(f"{ts()} {C_RED}⚠️ [{detector.camera['name']}] Ошибка загрузки зон: {e}{C_RESET}")
        detector.zones = []