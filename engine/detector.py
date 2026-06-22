"""
Legion NVR - Motion Detector
Запуск: python engine/detector.py
Читает камеры из БД, детектит движение, публикует MQTT
"""
import io
import cv2
import numpy as np
import paho.mqtt.client as mqtt
import json
import time
import os
import sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# Добавляем родительскую папку в path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.stdout.reconfigure(line_buffering=True)

from models.database import get_db

MQTT_BROKER = "127.0.0.1"
MQTT_PORT = 1883

class MotionDetector:
    def __init__(self, camera, mqtt_client):
        self.camera = camera
        self.mqtt = mqtt_client
        self.cap = None
        self.fgbg = cv2.createBackgroundSubtractorMOG2(history=300, varThreshold=25, detectShadows=False)
        self.motion_active = False
        self.last_motion_time = 0
        self.running = False
        
        # Параметры из БД
        self.threshold = camera.get("motion_threshold", 2.0)
        self.cooldown = camera.get("cooldown_sec", 5)

        # Зоны детекции
        self.zones = []  
        self._load_zones() 
    
    def start(self):
        rtsp_url = self.camera.get("rtsp_sub") or self.camera.get("rtsp_main")
        
        self.cap = cv2.VideoCapture(rtsp_url)
        if not self.cap.isOpened():
            print(f"❌ [{self.camera['name']}] Не могу открыть RTSP: {rtsp_url}")
            return False
        
        self.cap.set(cv2.CAP_PROP_FPS, 5)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.running = True
        
        # Сбрасываем счётчик разогрева ПЕРЕД return
        self.warmup_frames = 0
        self.WARMUP_NEEDED = 25
        
        print(f"🔍 [{self.camera['name']}] Детектор запущен (порог: {self.threshold}%)")
        return True
    
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

    def loop(self):
        """Один цикл детекции (вызывается из внешнего цикла)"""
        if not self.running:
            return
        
        ret, frame = self.cap.read()
        if not ret:
            return
        
        small = cv2.resize(frame, (320, 240))
        fgmask = self.fgbg.apply(small)

        # Применяем зоны детекции
        if self.zones:
            mask = np.zeros((240, 320), dtype=np.uint8)
            
            for zone in self.zones:
                # Масштабируем точки зоны под 320x240
                scale_x = 320 / frame.shape[1]
                scale_y = 240 / frame.shape[0]
                pts = np.array([[(int(p["x"] * scale_x), int(p["y"] * scale_y)) for p in zone["points"]]], dtype=np.int32)
                
                if zone["zone_type"] == "include":
                    # Включаем зону в маску
                    cv2.fillPoly(mask, pts, 255)
                else:
                    # Исключаем зону из маски
                    cv2.fillPoly(mask, pts, 0)
            
            # Если есть include-зоны, применяем маску
            has_include = any(z["zone_type"] == "include" for z in self.zones)
            if has_include:
                fgmask = cv2.bitwise_and(fgmask, mask)
            else:
                # Только exclude-зоны — инвертируем
                exclude_mask = cv2.bitwise_not(mask)
                fgmask = cv2.bitwise_and(fgmask, exclude_mask)
                
                # Пропускаем первые кадры для разогрева фона
                if self.warmup_frames < self.WARMUP_NEEDED:
                    self.warmup_frames += 1
                    return
        
        motion_pixels = np.count_nonzero(fgmask)
        motion_percent = motion_pixels / (320 * 240) * 100
        now = time.time()
        
        if motion_percent > self.threshold:
            if not self.motion_active and (now - self.last_motion_time > self.cooldown):
                self.motion_active = True
                self.last_motion_time = now
                self._publish("start", motion_percent)
                print(f"🔴 [{self.camera['name']}] ДВИЖЕНИЕ! {motion_percent:.1f}%")
        else:
            if self.motion_active:
                self.motion_active = False
                self._publish("end", 0)
                print(f"🟢 [{self.camera['name']}] движение прекратилось")
    
    def _publish(self, event_type, percent):
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
    
    def stop(self):
        self.running = False
        if self.cap:
            self.cap.release()

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
        
        if action == "reload_config":
            cam_id = data.get("camera_id")
            print(f"📡 [CMD] Перезагрузка конфига для камеры {cam_id}")
            # Перечитываем настройки из БД
            conn = get_db()
            cam = conn.execute("SELECT * FROM cameras WHERE id=?", (cam_id,)).fetchone()
            conn.close()
            
            if cam:
                cam_dict = dict(cam)
                # Ищем детектор для этой камеры
                for det in userdata["detectors"]:
                    if str(det.camera["id"]) == str(cam_id):
                        # Обновляем настройки без перезапуска
                        det.threshold = cam_dict.get("motion_threshold", 2.0)
                        det.cooldown = cam_dict.get("motion_cooldown", 5)
                        
                        # Если детектор выключен — останавливаем, включен — запускаем
                        if not cam_dict.get("motion_enabled"):
                            print(f"⏸️ [{det.camera['name']}] Детектор остановлен по команде")
                            det.running = False
                        else:
                            if not det.running:
                                print(f"▶️ [{det.camera['name']}] Детектор запущен по команде")
                                det.camera = cam_dict  # обновляем все поля
                                det._load_zones()  # перезагружаем зоны
                                det.start()
                        
                        print(f"✅ [{det.camera['name']}] Настройки применены (порог: {det.threshold}%)")
                        break
        
        elif action == "snapshot":
            cam_id = data.get("camera_id")
            print(f"📸 [CMD] Запрос снимка для камеры {cam_id}")
            # TODO: сохранить кадр
            
    except Exception as e:
        print(f"⚠️ [CMD] Ошибка: {e}")

def main():
    print("🛡️ Legion NVR - Motion Detector")
    print(f"📡 MQTT: {MQTT_BROKER}:{MQTT_PORT}")
    
    # Подключаем MQTT
    mqtt_client = mqtt.Client()
    mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
    
    # Загружаем камеры
    cameras = load_cameras()
    print(f"📷 Камер с детектором: {len(cameras)}")
    
    detectors = []
    for cam in cameras:
        det = MotionDetector(cam, mqtt_client)
        if det.start():
            detectors.append(det)
    
    # Передаём список детекторов в userdata для callback
    mqtt_client.user_data_set({"detectors": detectors})
    mqtt_client.on_message = on_cmd
    mqtt_client.subscribe("spartan/+/cmd")
    mqtt_client.loop_start()
    print(f"🔍 Активных детекторов: {len(detectors)}")
    print(f"👂 Подписка: spartan/+/cmd")
    print("⏳ Работаю... (Ctrl+C для выхода)")
    
    try:
        while True:
            for det in detectors:
                if det.running:
                    det.loop()
            time.sleep(0.05)
    except KeyboardInterrupt:
        print("\n⏹️ Завершение...")
        for det in detectors:
            det.stop()
        mqtt_client.loop_stop()
        mqtt_client.disconnect()

if __name__ == '__main__':
    main()