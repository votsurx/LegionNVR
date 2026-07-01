"""
MQTT обработчик для детектора
"""
import time
import json
import traceback
from engine.shared.constants import *
from engine.shared.utils import ts, load_detector_cameras
from engine.shared.mqtt_utils import send_mqtt_command
from models.database import get_db


def on_cmd(client, userdata, msg):
    """Обработчик MQTT команд"""
    try:
        data = json.loads(msg.payload.decode())
        action = data.get("action")
        cam_id = data.get("camera_id")

        if action == "reload_config":
            _handle_reload_config(client, userdata, cam_id)
        elif action == "ping":
            _handle_ping(client, userdata)
        elif action == "start_detector":
            _handle_start_detector(client, userdata, cam_id)
        elif action == "stop_detector":
            _handle_stop_detector(userdata, cam_id)

    except Exception as e:
        print(f"{ts()} ⚠️ [CMD] Ошибка: {e}")


def _handle_reload_config(client, userdata, cam_id):
    """Обрабатывает перезагрузку конфига камеры"""
    print(f"{ts()} 📡 [CMD] Перезагрузка конфига для камеры {cam_id}")

    with get_db() as conn:
        cam = conn.execute("SELECT * FROM cameras WHERE id=?", (cam_id,)).fetchone()

    if not cam:
        print(f"{ts()} ⚠️ Камера {cam_id} не найдена в БД")
        return

    cam_dict = dict(cam)
    found = False

    for det in userdata["detectors"]:
        if str(det.camera["id"]) == str(cam_id):
            found = True

            # ✅ ЗАЩИТА ОТ ДВОЙНОГО ВЫЗОВА
            if getattr(det, '_reloading', False):
                print(f"{ts()} ⚠️ [{det.camera['name']}] Уже перезагружается, пропускаю")
                return
            det._reloading = True

            print(f"{ts()} ⏹️ [{det.camera['name']}] Перезагружаю настройки...")

            # Останавливаем с защитой
            try:
                det.stop()
                time.sleep(2)  # Даём время на освобождение ресурсов
            except Exception as e:
                print(f"{ts()} {C_YELLOW}⚠️ [{det.camera['name']}] Ошибка остановки: {e}{C_RESET}")

            # Обновляем настройки
            det.camera = cam_dict
            det.threshold = cam_dict.get("motion_threshold", 2.0)
            det.cooldown = cam_dict.get("motion_cooldown", 5)
            det.enabled = cam_dict.get("enabled", True) and cam_dict.get("motion_enabled", True)

            # Обновляем AI-детектор
            if hasattr(det, 'ai_detector'):
                det.ai_detector.enabled = cam_dict.get("ai_enabled", False)
                det.ai_detector.classes = cam_dict.get("ai_classes", [0])
                det.ai_detector.confidence = cam_dict.get("ai_confidence", 0.5)
                det.ai_detector.frame_skip = cam_dict.get("ai_frame_skip", 5)
                det.ai_detector.boxes_enabled = cam_dict.get("ai_boxes_enabled", 1)
                det.ai_detector.boxes_shift = cam_dict.get("ai_boxes_shift", 0)

                # Переинициализируем модель если нужно
                if det.ai_detector.enabled and not det.ai_detector.model:
                    det.ai_detector._init_model()

            # Загружаем зоны
            try:
                from engine.detector.zones import load_zones
                load_zones(det)
            except Exception as e:
                print(f"{ts()} {C_RED}❌ [{det.camera['name']}] Ошибка загрузки зон: {e}{C_RESET}")

            det.warmup_frames = 0

            # Запускаем если нужно
            if det.enabled:
                print(f"{ts()} ▶️ [{det.camera['name']}] Запускаю детектор...")
                try:
                    result = det.start()
                    if result:
                        print(f"{ts()} ✅ [{det.camera['name']}] Детектор запущен (порог: {det.threshold}%, зон: {len(det.zones)})")
                    else:
                        print(f"{ts()} {C_RED}❌ [{det.camera['name']}] Не удалось запустить{C_RESET}")
                except Exception as e:
                    print(f"{ts()} {C_RED}❌ [{det.camera['name']}] Ошибка запуска: {e}{C_RESET}")
                    import traceback
                    traceback.print_exc()
            else:
                print(f"{ts()} ⏸️ [{det.camera['name']}] Детектор отключён")

            # Сбрасываем флаг перезагрузки
            det._reloading = False
            break

    # Если камера не найдена в активных — создаём новый детектор
    if not found and cam_dict.get("enabled") and cam_dict.get("motion_enabled"):
        print(f"{ts()} 🆕 Создаю новый детектор для камеры {cam_id}")
        from engine.detector.motion_detector import MotionDetector
        mqtt_client = userdata.get("mqtt_client", client)
        det = MotionDetector(cam_dict, mqtt_client)
        if det.start():
            userdata["detectors"].append(det)
            print(f"{ts()} ✅ [{det.camera['name']}] Детектор создан и запущен")
        else:
            print(f"{ts()} {C_RED}❌ [{cam_dict['name']}] Не удалось запустить{C_RESET}")
    elif not found:
        print(f"{ts()} ⏸️ Камера {cam_id} отключена или детектор выключен — пропускаю")


def _handle_ping(client, userdata):
    mqtt_client = userdata.get("mqtt_client", client)
    mqtt_client.publish("spartan/detector/pong", json.dumps({
        "status": "alive",
        "cameras": len(userdata.get("detectors", [])),
        "timestamp": int(time.time())
    }))


def _handle_start_detector(client, userdata, cam_id):
    print(f"{ts()} ▶️ [CMD] Запуск детектора для камеры {cam_id}")
    # Аналогично reload_config


def _handle_stop_detector(userdata, cam_id):
    print(f"{ts()} ⏹️ [CMD] Остановка детектора для камеры {cam_id}")
    for det in userdata["detectors"]:
        if str(det.camera["id"]) == str(cam_id):
            det.enabled = False
            det.stop()
            break