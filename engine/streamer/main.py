"""
Главный модуль стримера
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import time
import signal
import threading
import paho.mqtt.client as mqtt

from engine.shared.constants import MQTT_BROKER, MQTT_PORT, HLS_DIR, HLS_TIME
from engine.shared.utils import ts, load_cameras
from engine.streamer.hls_streamer import start_hls_stream, stream_processes
from engine.streamer.recorder import save_body_segments, motion_recordings
from engine.streamer.mqtt_handler import on_motion_and_cmd


def signal_handler(sig, frame):
    """Обработчик Ctrl+C"""
    print("\n⏹️ Завершение...")
    for proc in stream_processes.values():
        try:
            proc.terminate()
        except:
            pass
    sys.exit(0)


def main():
    print("=" * 50)
    print("  🎥  LEGION NVR - STREAM ENGINE v3.0")
    print("=" * 50)
    print(f"{ts()}   📡 MQTT: {MQTT_BROKER}:{MQTT_PORT}")
    print(f"{ts()}   🎬 HLS сегменты: {HLS_TIME} сек")
    print()

    signal.signal(signal.SIGINT, signal_handler)
    os.makedirs(HLS_DIR, exist_ok=True)

    # MQTT
    client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    client.connect(MQTT_BROKER, MQTT_PORT, 60)
    client.subscribe("spartan/+/motion")
    client.subscribe("spartan/+/cmd")
    client.subscribe("spartan/streams/reload")
    client.on_message = on_motion_and_cmd
    client.loop_start()

    # Запускаем HLS для всех камер
    cameras = load_cameras()
    print(f"{ts()} [Cameras] {len(cameras)}")
    for cam in cameras:
        if cam.get("enabled") and cam.get("stream_enabled", True):
            start_hls_stream(cam)

    # Запускаем фоновое сохранение сегментов
    threading.Thread(target=save_body_segments, daemon=True).start()

    print(f"{ts()} [HLS] Streams: {len(stream_processes)}")
    print(f"{ts()} [Subscriptions] spartan/+/motion, spartan/+/cmd, spartan/streams/reload")
    print("[Running] Working... (Ctrl+C to exit)")
    print()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        signal_handler(None, None)


if __name__ == '__main__':
    main()