"""
MQTT обработчики для стримера
"""
import json
from engine.shared.constants import *
from engine.shared.utils import ts
from engine.streamer.recorder import (
    start_motion_recording,
    extend_recording,
    stop_motion_recording,
    motion_recordings
)
from engine.streamer.hls_streamer import (
    start_hls_stream,
    stop_hls_stream,
    restart_hls_stream,
    stream_processes
)
from engine.shared.utils import load_cameras
from models.database import get_db


def on_motion(client, userdata, msg):
    """Callback при получении MQTT-сообщения о движении"""
    try:
        data = json.loads(msg.payload.decode())
        cam_id = str(data.get("camera_id"))

        if data.get("event") == "motion_start":
            if cam_id in motion_recordings:
                print(f"{ts()} {C_YELLOW}📡 [MOTION] Запись уже активна для камеры {cam_id}, продлеваю{C_RESET}")
                extend_recording(cam_id)
            else:
                print(f"{ts()} {C_BLUE}📡 [MOTION] Старт записи для камеры {cam_id}{C_RESET}")
                with get_db() as conn:
                    cam = conn.execute("SELECT * FROM cameras WHERE id=?", (cam_id,)).fetchone()
                if cam:
                    start_motion_recording(dict(cam))
    except Exception as e:
        print(f"{ts()} ⚠️ Ошибка обработки motion: {e}")


def on_cmd(client, userdata, msg):
    """Обработчик команд для стримера"""
    try:
        data = json.loads(msg.payload.decode())
        action = data.get("action")
        cam_id = data.get("camera_id")

        # Старт записи (с защитой от двойного вызова)
        if action == "start_recording" and cam_id:
            if cam_id in motion_recordings:
                print(f"{ts()} {C_YELLOW}📡 [CMD] Запись уже активна для камеры {cam_id}, продлеваю{C_RESET}")
                extend_recording(cam_id)
            else:
                print(f"{ts()} {C_BLUE}📡 [CMD] Старт записи для камеры {cam_id}{C_RESET}")
                with get_db() as conn:
                    cam = conn.execute("SELECT * FROM cameras WHERE id=?", (cam_id,)).fetchone()
                if cam:
                    start_motion_recording(dict(cam))

        # Продление записи
        elif action == "extend_recording" and cam_id:
            print(f"{ts()} 📡 [CMD] Продление записи для камеры {cam_id}")
            extend_recording(cam_id)

        # Остановка записи
        elif action == "stop_recording" and cam_id:
            print(f"{ts()} 📡 [CMD] Остановка записи для камеры {cam_id}")
            stop_motion_recording(cam_id)

        # Запуск стрима
        elif action == "start_stream" and cam_id:
            print(f"{ts()} ▶️ [CMD] Запуск стрима для камеры {cam_id}")
            with get_db() as conn:
                cam = conn.execute("SELECT * FROM cameras WHERE id=?", (cam_id,)).fetchone()
            if cam:
                start_hls_stream(dict(cam))

        # Остановка стрима
        elif action == "stop_stream" and cam_id:
            print(f"{ts()} ⏹️ [CMD] Остановка стрима для камеры {cam_id}")
            stop_hls_stream(cam_id)

        # Остановка детектора → останавливаем запись
        elif action == "stop_detector" and cam_id:
            print(f"{ts()} ⏹️ [CMD] Остановка детектора для камеры {cam_id} → стоп записи")
            stop_motion_recording(cam_id)

        # Перезагрузка конфига
        elif action == "reload_config" and cam_id:
            print(f"{ts()} 📡 [CMD] Перезагрузка конфига для камеры {cam_id}")
            with get_db() as conn:
                cam = conn.execute("SELECT * FROM cameras WHERE id=?", (cam_id,)).fetchone()
            if cam:
                cam_dict = dict(cam)
                if cam_dict.get("enabled") and cam_dict.get("stream_enabled", True):
                    restart_hls_stream(cam_dict)
                else:
                    stop_hls_stream(cam_id)

        # Перезагрузка всех
        elif action == "reload_all":
            print("📡 [CMD] Перезагрузка ВСЕХ стримов")
            cameras = load_cameras()
            for cam in cameras:
                if cam.get("enabled") and cam.get("stream_enabled", True):
                    start_hls_stream(cam)
            print(f"{ts()} 🔄 Перезапущено стримов: {len(stream_processes)}")

        # Пинг от Health Monitor
        elif action == "ping":
            client.publish("spartan/streamer/pong", json.dumps({
                "status": "alive",
                "streams": len(stream_processes),
                "recordings": len(motion_recordings),
                "timestamp": int(time.time())
            }))

    except Exception as e:
        print(f"{ts()} ⚠️ [CMD] Ошибка: {e}")


def on_motion_and_cmd(client, userdata, msg):
    """Обрабатывает и motion, и cmd"""
    if msg.topic.endswith("/motion"):
        on_motion(client, userdata, msg)
    else:
        on_cmd(client, userdata, msg)