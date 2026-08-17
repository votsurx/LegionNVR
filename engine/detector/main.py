"""
Главный модуль детектора
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import threading
import time
import paho.mqtt.client as mqtt

from engine.shared.constants import MQTT_BROKER, MQTT_PORT
from engine.shared.utils import ts, load_detector_cameras
from engine.detector.motion_detector import MotionDetector
from engine.detector.mqtt_handler import on_cmd
from engine.health_monitor_det import DetectorHealer

def main():
    print("=" * 50)
    print("[Legion NVR] Motion Detector v6.0")
    print("=" * 50)
    print(f"{ts()} [MQTT] {MQTT_BROKER}:{MQTT_PORT}")

    mqtt_client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)

    # 2. Определяем и назначаем обработчики ДО подключения
    def on_connect(client, userdata, flags, reasonCode, properties=None):
        if reasonCode == 0:
            print(f"{ts()} ✅ MQTT подключён к брокеру")
        else:
            print(f"{ts()} ❌ Ошибка подключения MQTT: rc={reasonCode}")

    def on_disconnect(client, userdata, flags, reasonCode, properties=None):
        print(f"{ts()} ⚠️ MQTT отключён (rc={reasonCode})")
        if reasonCode != 0:
            print(f"{ts()} 🔄 Попытка переподключения...")
            try:
                client.reconnect()
            except:
                pass

    mqtt_client.on_connect = on_connect
    mqtt_client.on_disconnect = on_disconnect

    # 3. Теперь подключаемся
    mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)

    # Загружаем камеры с включённым детектором
    cameras = load_detector_cameras()
    print(f"{ts()} [Cameras] with detector: {len(cameras)}")

    # Создаём детекторы для каждой камеры
    detectors = []
    for cam in cameras:
        try:
            det = MotionDetector(cam, mqtt_client)
            if det.start():
                detectors.append(det)
                print(f"{ts()} ✅ [{cam['name']}] Детектор запущен")
            else:
                print(f"{ts()} ⏸️ [{cam['name']}] Детектор выключен (enabled=False)")
        except Exception as e:
            print(f"{ts()} ⚠️ Ошибка создания детектора: {e}")

    # Устанавливаем user_data с пустым списком
    mqtt_client.user_data_set({
        "detectors": detectors,
        "mqtt_client": mqtt_client
    })
    mqtt_client.on_message = on_cmd
    print(f"{ts()} ✅ on_cmd назначен для клиента")
    mqtt_client.subscribe("spartan/+/cmd")
    mqtt_client.subscribe("spartan/detector/cmd")
    print(f"{ts()} ✅ Подписка на spartan/+/cmd активна")
    mqtt_client.loop_start()
    print(f"{ts()} ✅ loop_start запущен, обработчик активен")
    time.sleep(2)

    healer = DetectorHealer()
    threading.Thread(target=healer.heal_loop, daemon=True).start()

    try:
        while True:
            for det in detectors:
                try:
                    if det.running and det.enabled:
                        det.loop()
                except Exception as e:
                    # ✅ Защищённый перезапуск
                    if det.enabled:
                        try:
                            if det.start():
                                print(f"{ts()} ✅ {det.camera['name']} перезапущен")
                            else:
                                print(f"{ts()} ⏸️ {det.camera['name']} не удалось перезапустить")
                        except Exception as start_e:
                            print(f"{ts()} ❌ КРИТИЧЕСКАЯ ОШИБКА при start(): {start_e}")
                            det.running = False
                    else:
                        print(f"{ts()} ⏸️ {det.camera['name']} выключена — не перезапускаю")
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