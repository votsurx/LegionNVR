import cv2
import numpy as np
import paho.mqtt.client as mqtt
import json
import time
import threading

class MotionDetector:
    def __init__(self, camera_config, mqtt_config):
        self.camera = camera_config
        self.mqtt_client = mqtt.Client()
        if mqtt_config.get("username"):
            self.mqtt_client.username_pw_set(mqtt_config["username"], mqtt_config["password"])
        self.mqtt_client.connect(mqtt_config["broker"], mqtt_config["port"], 60)
        
        self.cap = None
        self.fgbg = cv2.createBackgroundSubtractorMOG2(history=300, varThreshold=25, detectShadows=False)
        self.motion_active = False
        self.last_motion_time = 0
        self.running = False

    def start(self):
        self.running = True
        self.cap = cv2.VideoCapture(self.camera["rtsp_sub"])
        self.cap.set(cv2.CAP_PROP_FPS, 5)  # 5fps для детектора
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        
        thread = threading.Thread(target=self._detect_loop)
        thread.daemon = True
        thread.start()
        print(f"🔍 Детектор для {self.camera['name']} запущен")

    def _detect_loop(self):
        while self.running:
            ret, frame = self.cap.read()
            if not ret:
                time.sleep(1)
                continue

            # Мега-лёгкая обработка
            small = cv2.resize(frame, (320, 240))
            fgmask = self.fgbg.apply(small)
            motion_pixels = np.count_nonzero(fgmask)
            motion_percent = motion_pixels / (320 * 240) * 100
            now = time.time()

            if motion_percent > self.camera["motion_threshold"]:
                if not self.motion_active and (now - self.last_motion_time > self.camera["cooldown_sec"]):
                    self.motion_active = True
                    self.last_motion_time = now
                    self._publish_motion("start", motion_percent)
            else:
                if self.motion_active:
                    self.motion_active = False
                    self._publish_motion("end", 0)

            time.sleep(0.1)  # щадим процессор

    def _publish_motion(self, event_type, percent):
        topic = f"spartan/{self.camera['id']}/motion"
        payload = json.dumps({
            "camera_id": self.camera["id"],
            "camera_name": self.camera["name"],
            "event": f"motion_{event_type}",
            "percent": round(percent, 2),
            "timestamp": int(time.time())
        })
        self.mqtt_client.publish(topic, payload)
        print(f"📡 MQTT → {topic}: {event_type} ({percent:.1f}%)")

    def stop(self):
        self.running = False
        if self.cap:
            self.cap.release()
        self.mqtt_client.disconnect()