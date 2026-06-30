"""
Главный модуль детектора
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import time
import paho.mqtt.client as mqtt

from engine.shared.constants import MQTT_BROKER, MQTT_PORT
from engine.shared.utils import ts, load_detector_cameras
from engine.detector.motion_detector import MotionDetector
from engine.detector.mqtt_handler import on_cmd


def main():
    print("[Legion NVR] Motion Detector")
    print(f"{ts()} [MQTT] {MQTT_BROKER}:{MQTT_PORT}")

    mqtt_client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)

    cameras = []
    try:
        cameras = load_detector_cameras()
    except Exception as e:
        print(f"{ts()} ⚠️ Ошибка загрузки камер: {e}")

    print(f"{ts()} [Cameras] with detector: {len(cameras)}")

    detectors = []
    for cam in cameras:
        try:
            det = MotionDetector(cam, mqtt_client)
            if det.start():
                detectors.append(det)
        except Exception as e:
            print(f"{ts()} ⚠️ Ошибка создания детектора: {e}")

    mqtt_client.user_data_set({
        "detectors": detectors,
        "mqtt_client": mqtt_client
    })
    mqtt_client.on_message = on_cmd
    mqtt_client.subscribe("spartan/+/cmd")
    mqtt_client.loop_start()

    print(f"{ts()} [Detectors] Active: {len(detectors)}")
    print("[Running] Working... (Ctrl+C to exit)")

    try:
        while True:
            for det in detectors:
                try:
                    if det.running and det.enabled:
                        det.loop()
                except Exception as e:
                    print(f"{ts()} ⚠️ Ошибка цикла: {e}")
                    try:
                        det.start()
                    except:
                        pass
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