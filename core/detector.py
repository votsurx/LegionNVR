import cv2
import numpy as np
import paho.mqtt.client as mqtt
import json
import time
import threading
from models.database import get_db

class MotionDetector:
    def __init__(self, camera_config, mqtt_config):
        self.camera = camera_config
        self.mqtt_client = None
        self.mqtt_available = False
        self.on_motion_callback = None  #    
        
        try:
            self.mqtt_client = mqtt.Client()
            if mqtt_config.get("username"):
                self.mqtt_client.username_pw_set(mqtt_config["username"], mqtt_config["password"])
            self.mqtt_client.connect(mqtt_config.get("broker", "127.0.0.1"), mqtt_config.get("port", 1883), 10)
            self.mqtt_available = True
            print(f" MQTT   {camera_config['name']}")
        except Exception as e:
            print(f" MQTT   {camera_config['name']}: {e}")
        
        self.cap = None
        self.fgbg = cv2.createBackgroundSubtractorMOG2(history=300, varThreshold=25, detectShadows=False)
        self.motion_active = False
        self.last_motion_time = 0
        self.running = False

    def start(self):
        self.running = True
        rtsp_url = self.camera.get("rtsp_sub") or self.camera.get("rtsp_main")
        
        self.cap = cv2.VideoCapture(rtsp_url)
        if not self.cap.isOpened():
            print(f"    RTSP: {rtsp_url}")
            return
        
        self.cap.set(cv2.CAP_PROP_FPS, 5)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        
        thread = threading.Thread(target=self._detect_loop)
        thread.daemon = True
        thread.start()
        print(f"   {self.camera['name']}  (: {self.camera.get('motion_threshold', 2.0)}%)")

    def _detect_loop(self):
        while self.running:
            ret, frame = self.cap.read()
            if not ret:
                time.sleep(1)
                continue

            small = cv2.resize(frame, (320, 240))
            fgmask = self.fgbg.apply(small)
            motion_pixels = np.count_nonzero(fgmask)
            motion_percent = motion_pixels / (320 * 240) * 100
            now = time.time()
            
            threshold = self.camera.get("motion_threshold", 2.0)
            cooldown = self.camera.get("cooldown_sec", 5)

            if motion_percent > threshold:
                if not self.motion_active and (now - self.last_motion_time > cooldown):
                    self.motion_active = True
                    self.last_motion_time = now
                    self._on_motion("start", motion_percent)
                    print(f" {self.camera['name']}: ! {motion_percent:.1f}%")
            else:
                if self.motion_active:
                    self.motion_active = False
                    self._on_motion("end", 0)
                    print(f" {self.camera['name']}:  ")

            time.sleep(0.1)

    def _on_motion(self, event_type, percent):
        # MQTT
        if self.mqtt_available and self.mqtt_client:
            topic = f"spartan/{self.camera['id']}/motion"
            payload = json.dumps({
                "camera_id": self.camera["id"],
                "camera_name": self.camera["name"],
                "event": f"motion_{event_type}",
                "percent": round(percent, 2),
                "timestamp": int(time.time())
            })
            self.mqtt_client.publish(topic, payload)
        
        # Callback  
        if self.on_motion_callback:
            try:
                self.on_motion_callback(event_type, self.camera["id"])
            except Exception as e:
                print(f"  callback: {e}")
        
        #   
        try:
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO events (camera_id, event_type, details) VALUES (?, ?, ?)",
                    (self.camera["id"], f"motion_{event_type}", json.dumps({"percent": round(percent, 2)}))
                )
                conn.commit()
        except:
            pass

    def stop(self):
        self.running = False
        if self.cap:
            self.cap.release()
        if self.mqtt_available and self.mqtt_client:
            self.mqtt_client.disconnect()