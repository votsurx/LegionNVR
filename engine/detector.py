"""
Legion NVR - Motion Detector
Запуск: python engine/detector.py
Читает камеры из БД, детектит движение, публикует MQTT
"""
import cv2
import numpy as np
import paho.mqtt.client as mqtt
import json
import time
import os
import sys

# Добавляем родительскую папку в path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

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
    
    def start(self):
        rtsp_url = self.camera.get("rtsp_sub") or self.camera.get("rtsp_main")
        
        self.cap = cv2.VideoCapture(rtsp_url)
        if not self.cap.isOpened():
            print(f"❌ [{self.camera['name']}] Не могу открыть RTSP: {rtsp_url}")
            return False
        
        self.cap.set(cv2.CAP_PROP_FPS, 5)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.running = True
        print(f"🔍 [{self.camera['name']}] Детектор запущен (порог: {self.threshold}%)")
        return True
    
    def loop(self):
        """Один цикл детекции (вызывается из внешнего цикла)"""
        if not self.running:
            return
        
        ret, frame = self.cap.read()
        if not ret:
            return
        
        small = cv2.resize(frame, (320, 240))
        fgmask = self.fgbg.apply(small)
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

def main():
    print("🛡️ Legion NVR - Motion Detector")
    print(f"📡 MQTT: {MQTT_BROKER}:{MQTT_PORT}")
    
    # Подключаем MQTT
    mqtt_client = mqtt.Client()
    mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
    mqtt_client.loop_start()
    
    # Загружаем камеры
    cameras = load_cameras()
    print(f"📷 Камер с детектором: {len(cameras)}")
    
    detectors = []
    for cam in cameras:
        det = MotionDetector(cam, mqtt_client)
        if det.start():
            detectors.append(det)
    
    print(f"🔍 Активных детекторов: {len(detectors)}")
    print("⏳ Работаю... (Ctrl+C для выхода)")
    
    try:
        while True:
            # Периодически обновляем список камер (раз в 30 сек)
            for det in detectors:
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