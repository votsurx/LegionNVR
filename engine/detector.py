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
        self.enabled = camera.get("enabled", True)  # ✅ Добавлено

        # Параметры из БД
        self.threshold = camera.get("motion_threshold", 2.0)
        self.cooldown = camera.get("cooldown_sec", 5)

        # ✅ ДОБАВЛЯЕМ МИНИМАЛЬНЫЙ ПОРОГ ДЛЯ ЛОГОВ
        self.log_min_threshold = 5.0  # Не показывать движение ниже 5%

        # Зоны детекции
        self.zones = []
        self._load_zones()

        # ✅ ЗАДЕРЖКА ПЕРЕД ОСТАНОВКОЙ (сек)
        self.motion_end_delay = 2.0
        self.motion_end_time = None
        self.motion_end_timer = None

        # Счётчик разогрева
        self.warmup_frames = 0
        self.WARMUP_NEEDED = 25
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 5
        self._reconnect_delay = 2

    def _load_zones(self):
        """Загружает зоны детекции из БД"""
        try:
            conn = get_db()
            rows = conn.execute(
                "SELECT * FROM detection_zones WHERE camera_id=? AND enabled=1",
                (self.camera["id"],)
            ).fetchall()
            conn.close()

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

        # Закрываем старый кап если есть
        if self.cap:
            self.cap.release()
            self.cap = None

        # Пытаемся подключиться с повторными попытками
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

        print(f"🔍 [{self.camera['name']}] Детектор запущен (порог: {self.threshold}%)")
        return True

    def stop(self):
        """Останавливает детектор"""
        self.running = False
        if self.cap:
            self.cap.release()
            self.cap = None
        print(f"⏹️ [{self.camera['name']}] Детектор остановлен")

    def restart(self):
        """Перезапускает детектор"""
        self.stop()
        time.sleep(1)
        return self.start()

    def loop(self):
        """Один цикл детекции (вызывается из внешнего цикла)"""
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

        # ✅ ПРОГРЕВ ВЫНЕСЕН СЮДА! (ПОСЛЕ ОБРАБОТКИ ЗОН)
        if self.warmup_frames < self.WARMUP_NEEDED:
            self.warmup_frames += 1
            if self.warmup_frames % 5 == 0:
                print(f"🔥 [{self.camera['name']}] Прогрев: {self.warmup_frames}/{self.WARMUP_NEEDED}")
            return

        motion_pixels = np.count_nonzero(fgmask)
        motion_percent = motion_pixels / (320 * 240) * 100
        now = time.time()

        if motion_percent > self.threshold:
            # ✅ ДВИЖЕНИЕ ЕСТЬ
            print(f"📊 [{self.camera['name']}] Движение: {motion_percent:.1f}% (порог: {self.threshold}%)")

            # ✅ ОТМЕНЯЕМ ТАЙМЕР ОСТАНОВКИ (если был)
            if self.motion_end_timer:
                self.motion_end_timer.cancel()
                self.motion_end_timer = None
                print(f"⏸️ [{self.camera['name']}] Движение возобновилось, отмена остановки")

            if not self.motion_active:
                self.motion_active = True
                self.motion_start_time = time.time()
                self._publish("motion_start", motion_percent)
                send_mqtt_command(self.camera['id'], 'start_recording')
                print(f"🔴 [{self.camera['name']}] ДВИЖЕНИЕ! Старт записи")
        else:
            # ✅ ДВИЖЕНИЯ НЕТ — ЗАПУСКАЕМ ТАЙМЕР ОСТАНОВКИ
            if self.motion_active:
                if self.motion_end_timer is None:
                    print(f"⏳ [{self.camera['name']}] Движение прекратилось, ждём {self.motion_end_delay} сек...")
                    self.motion_end_timer = threading.Timer(
                        self.motion_end_delay,
                        self._stop_motion
                    )
                    self.motion_end_timer.daemon = True
                    self.motion_end_timer.start()

    def _stop_motion(self):
        """Останавливает запись после задержки"""
        if self.motion_active:
            self.motion_active = False
            self._publish("motion_end", 0)
            send_mqtt_command(self.camera['id'], 'stop_recording')
            print(f"🟢 [{self.camera['name']}] Движение прекратилось, запись остановлена")
        self.motion_end_timer = None

    def _publish(self, event_type, percent):
        """Публикует MQTT событие"""
        topic = f"spartan/{self.camera['id']}/motion"
        payload = json.dumps({
            "camera_id": self.camera["id"],
            "camera_name": self.camera["name"],
            "event": f"motion_{event_type}",
            "percent": round(percent, 2),
            "timestamp": int(time.time())
        })
        self.mqtt.publish(topic, payload)

        # Логируем в БД
        try:
            conn = get_db()
            conn.execute(
                "INSERT INTO events (camera_id, event_type, details) VALUES (?, ?, ?)",
                (self.camera["id"], f"motion_{event_type}", json.dumps({"percent": round(percent, 2)}))
            )
            conn.commit()
            conn.close()
        except:
            pass

    def _publish_status(self, status):
        """Публикует статус камеры"""
        topic = f"spartan/{self.camera['id']}/status"
        payload = json.dumps({
            "camera_id": self.camera["id"],
            "camera_name": self.camera["name"],
            "status": status,  # 'online' или 'offline'
            "timestamp": int(time.time())
        })
        self.mqtt.publish(topic, payload)


def load_cameras():
    """Загружает камеры из БД с включённым детектором"""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM cameras WHERE enabled=1 AND motion_enabled=1"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def on_cmd(client, userdata, msg):
    """Обработчик команд управления"""
    try:
        data = json.loads(msg.payload.decode())
        action = data.get("action")
        cam_id = data.get("camera_id")

        if action == "reload_config":
            print(f"📡 [CMD] Перезагрузка конфига для камеры {cam_id}")
            conn = get_db()
            cam = conn.execute("SELECT * FROM cameras WHERE id=?", (cam_id,)).fetchone()
            conn.close()

            if cam:
                cam_dict = dict(cam)
                for det in userdata["detectors"]:
                    if str(det.camera["id"]) == str(cam_id):
                        det.threshold = cam_dict.get("motion_threshold", 2.0)
                        det.cooldown = cam_dict.get("motion_cooldown", 5)

                        if not cam_dict.get("motion_enabled", True):
                            print(f"⏸️ [{det.camera['name']}] Детектор остановлен по команде")
                            det.disable()
                        else:
                            det.enable()
                            det.camera = cam_dict
                            det._load_zones()

                        print(f"✅ [{det.camera['name']}] Настройки применены (порог: {det.threshold}%)")
                        break

        elif action == "start_detector":
            print(f"▶️ [CMD] Запуск детектора для камеры {cam_id}")
            for det in userdata["detectors"]:
                if str(det.camera["id"]) == str(cam_id):
                    det.enable()
                    break

        elif action == "stop_detector":
            print(f"⏹️ [CMD] Остановка детектора для камеры {cam_id}")
            for det in userdata["detectors"]:
                if str(det.camera["id"]) == str(cam_id):
                    det.disable()
                    break

        elif action == "snapshot":
            cam_id = data.get("camera_id")
            print(f"📸 [CMD] Запрос снимка для камеры {cam_id}")

    except Exception as e:
        print(f"⚠️ [CMD] Ошибка: {e}")


def main():
    print("[Legion NVR] Motion Detector")
    print(f"[MQTT] {MQTT_BROKER}:{MQTT_PORT}")

    # Подключаем MQTT
    mqtt_client = mqtt.Client()
    mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)

    # Загружаем камеры
    cameras = load_cameras()
    print(f"[Cameras] with detector: {len(cameras)}")

    detectors = []
    for cam in cameras:
        det = MotionDetector(cam, mqtt_client)
        if det.start():
            detectors.append(det)

    mqtt_client.user_data_set({"detectors": detectors})
    mqtt_client.on_message = on_cmd
    mqtt_client.subscribe("spartan/+/cmd")
    mqtt_client.loop_start()

    print(f"[Detectors] Active: {len(detectors)}")
    print(f"[Subscriptions] spartan/+/cmd")
    print("[Running] Working... (Ctrl+C to exit)")

    try:
        while True:
            for det in detectors:
                if det.running and det.enabled:
                    det.loop()
            time.sleep(0.05)
    except KeyboardInterrupt:
        print("\n[Stopping] Shutting down...")
        for det in detectors:
            det.stop()
        mqtt_client.loop_stop()
        mqtt_client.disconnect()


if __name__ == '__main__':
    main()