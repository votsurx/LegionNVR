"""
Legion NVR - Motion Detector
Запуск: python engine/detector.py
Читает камеры из БД, детектит движение, публикует MQTT
"""
import os
import sys
import io
import cv2
import numpy as np
import paho.mqtt.client as mqtt
import json
import time
import sys
import threading

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.stdout.reconfigure(line_buffering=True)

from models.database import get_db

MQTT_BROKER = "127.0.0.1"
MQTT_PORT = 1883


# ════════════════════════════════════════════════════════════
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ (вне класса!)
# ════════════════════════════════════════════════════════════

def send_mqtt_command(camera_id, action, params=None):
    """Отправляет MQTT команду"""
    try:
        client = mqtt.Client()
        client.connect("127.0.0.1", 1883, 5)
        payload = {
            'action': action,
            'camera_id': camera_id,
            'timestamp': int(time.time())
        }
        if params:
            payload.update(params)
        client.publish(f"spartan/{camera_id}/cmd", json.dumps(payload))
        client.disconnect()
        return True
    except Exception as e:
        print(f"❌ MQTT ошибка: {e}")
        return False


def load_cameras():
    """Загружает камеры из БД с включённым детектором"""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM cameras WHERE enabled=1 AND motion_enabled=1"
        ).fetchall()
    return [dict(r) for r in rows]


def on_cmd(client, userdata, msg):
    """Обработчик MQTT команд"""
    try:
        data = json.loads(msg.payload.decode())
        action = data.get("action")
        cam_id = data.get("camera_id")

        if action == "reload_config":
            print(f"📡 [CMD] Перезагрузка конфига для камеры {cam_id}")
            with get_db() as conn:
                cam = conn.execute("SELECT * FROM cameras WHERE id=?", (cam_id,)).fetchone()

            if cam:
                cam_dict = dict(cam)

                # ✅ ИЩЕМ КАМЕРУ В СПИСКЕ АКТИВНЫХ ДЕТЕКТОРОВ
                found = False
                for det in userdata["detectors"]:
                    if str(det.camera["id"]) == str(cam_id):
                        found = True

                        # ✅ ОБНОВЛЯЕМ НАСТРОЙКИ
                        print(f"⏹️ [{det.camera['name']}] Перезагружаю настройки...")
                        det.stop()

                        det.camera = cam_dict
                        det.threshold = cam_dict.get("motion_threshold", 2.0)
                        det.cooldown = cam_dict.get("motion_cooldown", 5)
                        det.enabled = cam_dict.get("enabled", True) and cam_dict.get("motion_enabled", True)
                        det._load_zones()
                        det.warmup_frames = 0

                        # ✅ ЗАПУСКАЕМ ЕСЛИ НУЖНО
                        if det.enabled:
                            print(f"▶️ [{det.camera['name']}] Запускаю детектор...")
                            det.start()
                            print(f"✅ [{det.camera['name']}] Порог: {det.threshold}%, Зон: {len(det.zones)}, Cooldown: {det.cooldown} сек")
                        else:
                            print(f"⏸️ [{det.camera['name']}] Детектор отключён (motion_enabled=0)")
                        break

                # ✅ ЕСЛИ НЕ НАШЛИ — СОЗДАЁМ НОВЫЙ ДЕТЕКТОР
                if not found:
                    print(f"🆕 Камера {cam_id} не найдена в активных — создаю новый детектор")
                    if cam_dict.get("enabled") and cam_dict.get("motion_enabled"):
                        # Получаем MQTT клиент из первого детектора или создаём новый
                        mqtt_client = userdata.get("mqtt_client", client)
                        det = MotionDetector(cam_dict, mqtt_client)
                        if det.start():
                            userdata["detectors"].append(det)
                            print(f"✅ [{det.camera['name']}] Детектор создан и запущен")
                        else:
                            print(f"❌ [{cam_dict['name']}] Не удалось запустить детектор")
                    else:
                        print(f"⏸️ Камера {cam_id} отключена или детектор выключен — пропускаю")

        elif action == "start_detector":
            print(f"▶️ [CMD] Запуск детектора для камеры {cam_id}")
            with get_db() as conn:
                cam = conn.execute("SELECT * FROM cameras WHERE id=?", (cam_id,)).fetchone()

            if cam:
                cam_dict = dict(cam)

                # Ищем или создаём
                found = False
                for det in userdata["detectors"]:
                    if str(det.camera["id"]) == str(cam_id):
                        found = True
                        det.camera = cam_dict
                        det.threshold = cam_dict.get("motion_threshold", 2.0)
                        det.cooldown = cam_dict.get("motion_cooldown", 5)
                        det._load_zones()
                        det.enabled = True
                        det.warmup_frames = 0
                        det.start()
                        print(f"✅ [{det.camera['name']}] Детектор запущен")
                        break

                if not found:
                    print(f"🆕 Создаю детектор для камеры {cam_id}")
                    mqtt_client = userdata.get("mqtt_client", client)
                    det = MotionDetector(cam_dict, mqtt_client)
                    if det.start():
                        userdata["detectors"].append(det)

        elif action == "stop_detector":
            print(f"⏹️ [CMD] Остановка детектора для камеры {cam_id}")
            for det in userdata["detectors"]:
                if str(det.camera["id"]) == str(cam_id):
                    det.enabled = False
                    det.stop()
                    print(f"⏹️ [{det.camera['name']}] Детектор остановлен")
                    break

    except Exception as e:
        print(f"⚠️ [CMD] Ошибка: {e}")


# ════════════════════════════════════════════════════════════
# КЛАСС ДЕТЕКТОРА
# ════════════════════════════════════════════════════════════

class MotionDetector:
    def __init__(self, camera, mqtt_client):
        self.camera = camera
        self.mqtt = mqtt_client
        self.cap = None
        self.fgbg = cv2.createBackgroundSubtractorMOG2(
            history=300,
            varThreshold=25,
            detectShadows=False
        )
        self.motion_active = False
        self.last_motion_time = 0
        self.running = False
        self.enabled = camera.get("enabled", True)

        self.threshold = camera.get("motion_threshold", 2.0)
        self.cooldown = camera.get("motion_cooldown", 5)
        self.log_min_threshold = 5.0

        self.zones = []
        self._load_zones()

        self.motion_end_delay = camera.get("motion_end_delay", 2.0)
        self.motion_end_time = None
        self.motion_end_timer = None

        self.warmup_frames = 0
        self.WARMUP_NEEDED = 25
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 5
        self._reconnect_delay = 2

        # 🤖 AI НАСТРОЙКИ
        self.ai_enabled = camera.get("ai_enabled", False)
        self.ai_model = None
        self.ai_classes = camera.get("ai_classes", [0])  # 0=человек, 2=машина
        self.ai_confidence = camera.get("ai_confidence", 0.5)
        self.ai_frame_skip = camera.get("ai_frame_skip", 5)  # каждый 5-й кадр
        self.frame_count = 0

        if self.ai_enabled:
            self._init_ai()

    def _init_ai(self):
        """Инициализация YOLO"""
        try:
            from ultralytics import YOLO
            print(f"🤖 [{self.camera['name']}] Загружаю YOLOv8n...")
            self.ai_model = YOLO('yolov8n.pt')

            # ✅ ПАРСИМ ai_classes (может быть строка из БД)
            if isinstance(self.ai_classes, str):
                import json
                try:
                    self.ai_classes = json.loads(self.ai_classes)
                except:
                    self.ai_classes = [0]  # По умолчанию — человек

            print(f"✅ [{self.camera['name']}] YOLOv8n загружен! Классы: {self.ai_classes}")
        except Exception as e:
            print(f"❌ [{self.camera['name']}] Ошибка загрузки YOLO: {e}")
            self.ai_enabled = False

    def _load_zones(self):
        """Загружает зоны детекции из БД"""
        try:
            with get_db() as conn:
                rows = conn.execute(
                    "SELECT * FROM detection_zones WHERE camera_id=? AND enabled=1",
                    (self.camera["id"],)
                ).fetchall()

            self.zones = []
            for row in rows:
                zone = dict(row)
                zone["points"] = json.loads(zone["points_json"])
                self.zones.append(zone)

            if self.zones:
                print(f"🎯 [{self.camera['name']}] Загружено зон: {len(self.zones)}")
        except Exception as e:
            print(f"⚠️ [{self.camera['name']}] Ошибка загрузки зон: {e}")
            self.zones = []

    def enable(self):
        """Включает детектор"""
        if self.enabled:
            return
        self.enabled = True
        self._reconnect_attempts = 0
        print(f"✅ [{self.camera['name']}] Детектор ВКЛЮЧЕН")
        self.start()

    def disable(self):
        """Выключает детектор"""
        if not self.enabled:
            return
        self.enabled = False
        self.stop()
        print(f"⏹️ [{self.camera['name']}] Детектор ВЫКЛЮЧЕН")

    def start(self):
        """Запускает детектор"""
        if not self.enabled:
            print(f"⏸️ [{self.camera['name']}] Камера отключена, детектор не запущен")
            return False

        rtsp_url = self.camera.get("rtsp_sub") or self.camera.get("rtsp_main")

        if self.cap:
            self.cap.release()
            self.cap = None

        for attempt in range(self._max_reconnect_attempts):
            self.cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
            if self.cap.isOpened():
                break
            print(f"⚠️ [{self.camera['name']}] Попытка {attempt+1}/{self._max_reconnect_attempts} подключиться...")
            time.sleep(self._reconnect_delay)

        if not self.cap or not self.cap.isOpened():
            print(f"❌ [{self.camera['name']}] Не могу открыть RTSP: {rtsp_url}")
            self.cap = None
            return False

        self.cap.set(cv2.CAP_PROP_FPS, 5)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.running = True
        self.warmup_frames = 0
        self._reconnect_attempts = 0

        mode = "🤖 AI + MOG2" if self.ai_enabled else "🔍 MOG2"
        print(f"{mode} [{self.camera['name']}] Детектор запущен (порог: {self.threshold}%)")
        return True

    def stop(self):
        """Останавливает детектор"""
        self.running = False
        if self.cap:
            self.cap.release()
            self.cap = None

    def restart(self):
        """Перезапускает детектор"""
        self.stop()
        time.sleep(1)
        return self.start()

    def restart_with_config(self):
        """Перезапускает детектор с новыми настройками"""
        print(f"🔄 [{self.camera['name']}] Перезапуск с новыми настройками...")
        self.stop()
        time.sleep(0.5)
        self.start()

    def loop(self):
        """Один цикл детекции"""
        if not self.running or not self.enabled:
            return

        if self.cap is None:
            self._reconnect_attempts += 1
            if self._reconnect_attempts <= self._max_reconnect_attempts:
                self.start()
            return

        ret, frame = self.cap.read()
        if not ret:
            self._reconnect_attempts += 1
            if self._reconnect_attempts <= self._max_reconnect_attempts:
                print(f"⚠️ [{self.camera['name']}] Потеря потока, переподключение...")
                self.start()
            else:
                self.running = False
                self.cap.release()
                self.cap = None
                print(f"❌ [{self.camera['name']}] Потеря потока, детектор остановлен")
            return

        self._reconnect_attempts = 0

        small = cv2.resize(frame, (320, 240))
        fgmask = self.fgbg.apply(small)

        # Применяем зоны детекции
        if self.zones:
            mask = np.zeros((240, 320), dtype=np.uint8)
            for zone in self.zones:
                scale_x = 320 / frame.shape[1]
                scale_y = 240 / frame.shape[0]
                pts = np.array([[(int(p["x"] * scale_x), int(p["y"] * scale_y)) for p in zone["points"]]], dtype=np.int32)
                if zone["zone_type"] == "include":
                    cv2.fillPoly(mask, pts, 255)
                else:
                    cv2.fillPoly(mask, pts, 0)
            has_include = any(z["zone_type"] == "include" for z in self.zones)
            if has_include:
                fgmask = cv2.bitwise_and(fgmask, mask)
            else:
                exclude_mask = cv2.bitwise_not(mask)
                fgmask = cv2.bitwise_and(fgmask, exclude_mask)

        # Прогрев
        if self.warmup_frames < self.WARMUP_NEEDED:
            self.warmup_frames += 1
            if self.warmup_frames % 5 == 0:
                print(f"🔥 [{self.camera['name']}] Прогрев: {self.warmup_frames}/{self.WARMUP_NEEDED}")
            return

        motion_pixels = np.count_nonzero(fgmask)
        motion_percent = motion_pixels / (320 * 240) * 100

        # 🤖 ЭТАП 1: MOG2 ПРОВЕРЯЕТ ДВИЖЕНИЕ
        if motion_percent > self.threshold:

            # 🤖 ЭТАП 2: ЕСЛИ AI ВКЛЮЧЕН — ЗАПУСКАЕМ YOLO
            if self.ai_enabled and self.ai_model:
                self.frame_count += 1
                if self.frame_count % self.ai_frame_skip == 0:
                    # Запускаем YOLO на полном кадре
                    ai_result = self._ai_detect(frame)

                    if ai_result:
                        # ✅ AI ПОДТВЕРДИЛ — ЭТО ЧЕЛОВЕК/МАШИНА!
                        self._trigger_motion(motion_percent, ai_result)
                    else:
                        # ❌ AI ОТКЛОНИЛ — ЛОЖНАЯ ТРЕВОГА
                        if motion_percent > self.log_min_threshold:
                            print(f"🤖 [{self.camera['name']}] Ложная тревога отфильтрована AI ({motion_percent:.1f}%)")
                else:
                    # Пропускаем кадр, но если движение сильное — проверяем AI
                    if motion_percent > self.threshold * 3:
                        ai_result = self._ai_detect(frame)
                        if ai_result:
                            self._trigger_motion(motion_percent, ai_result)
            else:
                # AI выключен — работаем как раньше
                self._trigger_motion(motion_percent, None)
        else:
            # Движения нет — таймер остановки
            if self.motion_active:
                if self.motion_end_timer is None:
                    self.motion_end_timer = threading.Timer(
                        self.motion_end_delay,
                        self._stop_motion
                    )
                    self.motion_end_timer.daemon = True
                    self.motion_end_timer.start()

    def _ai_detect(self, frame):
        try:
            results = self.ai_model(frame, verbose=False, conf=self.ai_confidence)
            boxes = results[0].boxes

            if boxes is None or len(boxes) == 0:
                return None

            detected = {'person': 0, 'car': 0, 'motorcycle': 0, 'dog': 0, 'cat': 0, 'total': 0}

            for box in boxes:
                cls = int(box.cls[0])
                conf = float(box.conf[0])

                # ✅ ПРОВЕРЯЕМ, ЧТО ai_classes — СПИСОК
                ai_classes = self.ai_classes
                if isinstance(ai_classes, str):
                    import json
                    ai_classes = json.loads(ai_classes)

                if cls == 0 and 0 in ai_classes:
                    detected['person'] += 1
                elif cls == 2 and 2 in ai_classes:
                    detected['car'] += 1
                elif cls == 3 and 3 in ai_classes:
                    detected['motorcycle'] += 1
                elif cls == 16 and 16 in ai_classes:
                    detected['dog'] += 1
                elif cls == 17 and 17 in ai_classes:
                    detected['cat'] += 1

                detected['total'] += 1

            if detected['total'] > 0:
                return detected
            return None

        except Exception as e:
            print(f"⚠️ [{self.camera['name']}] Ошибка YOLO: {e}")
            return None

    def _trigger_motion(self, motion_percent, ai_result):
        """Триггерит тревогу"""
        # Отменяем таймер остановки
        if self.motion_end_timer:
            self.motion_end_timer.cancel()
            self.motion_end_timer = None

        if not self.motion_active:
            self.motion_active = True
            self.motion_start_time = time.time()

            # Формируем описание
            if ai_result:
                desc = []
                if ai_result['person'] > 0:
                    desc.append(f"👤 x{ai_result['person']}")
                if ai_result['car'] > 0:
                    desc.append(f"🚗 x{ai_result['car']}")
                print(f"🤖 [{self.camera['name']}] AI ТРЕВОГА! {', '.join(desc)} ({motion_percent:.1f}%)")
            else:
                print(f"📊 [{self.camera['name']}] Движение: {motion_percent:.1f}%")

            self._publish("motion_start", motion_percent, ai_result)
            send_mqtt_command(self.camera['id'], 'start_recording')
            print(f"🔴 [{self.camera['name']}] Старт записи!")

    def _stop_motion(self):
        """Останавливает запись после задержки"""
        if self.motion_active:
            self.motion_active = False
            self._publish("motion_end", 0)
            send_mqtt_command(self.camera['id'], 'stop_recording')
            print(f"🟢 [{self.camera['name']}] Движение прекратилось, запись остановлена")
        self.motion_end_timer = None

    def _publish(self, event_type, percent, ai_result=None):
        """Публикует MQTT событие"""
        topic = f"spartan/{self.camera['id']}/motion"
        payload_dict = {
            "camera_id": self.camera["id"],
            "camera_name": self.camera["name"],
            "event": f"motion_{event_type}",
            "percent": round(percent, 2),
            "timestamp": int(time.time())
        }
        if ai_result:
            payload_dict["ai"] = ai_result

        payload = json.dumps(payload_dict)
        self.mqtt.publish(topic, payload)

        # Логируем в БД
        try:
            with get_db() as conn:
                conn.execute(
                    "INSERT INTO events (camera_id, event_type, details) VALUES (?, ?, ?)",
                    (self.camera["id"], f"motion_{event_type}", json.dumps(payload_dict))
                )
                conn.commit()
        except:
            pass

    def _publish_status(self, status):
        """Публикует статус камеры"""
        topic = f"spartan/{self.camera['id']}/status"
        payload = json.dumps({
            "camera_id": self.camera["id"],
            "camera_name": self.camera["name"],
            "status": status,
            "timestamp": int(time.time())
        })
        self.mqtt.publish(topic, payload)


# ════════════════════════════════════════════════════════════
# ГЛАВНАЯ ФУНКЦИЯ
# ════════════════════════════════════════════════════════════

def main():
    print("[Legion NVR] Motion Detector")
    print(f"[MQTT] {MQTT_BROKER}:{MQTT_PORT}")

    # Подключаем MQTT
    mqtt_client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)

    # Загружаем камеры (если нет - не падаем)
    cameras = []
    try:
        cameras = load_cameras()
    except Exception as e:
        print(f"⚠️ Ошибка загрузки камер: {e}")

    print(f"[Cameras] with detector: {len(cameras)}")

    # Создаём детекторы (если есть камеры)
    detectors = []
    for cam in cameras:
        try:
            det = MotionDetector(cam, mqtt_client)
            if det.start():
                detectors.append(det)
        except Exception as e:
            print(f"⚠️ Ошибка создания детектора для {cam.get('name', '?')}: {e}")

    # ✅ ВСЕГДА ПОДПИСЫВАЕМСЯ НА MQTT
    mqtt_client.user_data_set({
        "detectors": detectors,
        "mqtt_client": mqtt_client
    })
    mqtt_client.on_message = on_cmd
    mqtt_client.subscribe("spartan/+/cmd")
    mqtt_client.loop_start()

    print(f"[Detectors] Active: {len(detectors)}")
    print(f"[Subscriptions] spartan/+/cmd")
    print("[Running] Working... (Ctrl+C to exit)")

    # ✅ БЕСКОНЕЧНЫЙ ЦИКЛ
    try:
        while True:
            for det in detectors:
                try:
                    if det.running and det.enabled:
                        det.loop()
                except Exception as e:
                    print(f"⚠️ Ошибка цикла детекции: {e}")
            time.sleep(0.05)
    except KeyboardInterrupt:
        print("\n[Stopping] Shutting down...")
    finally:
        for det in detectors:
            try:
                det.stop()
            except:
                pass
        try:
            mqtt_client.loop_stop()
            mqtt_client.disconnect()
        except:
            pass


if __name__ == '__main__':
    main()