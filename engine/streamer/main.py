"""
Главный модуль стримера
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import threading
import tempfile
import time
import signal
import threading
import paho.mqtt.client as mqtt

from engine.shared.constants import MQTT_BROKER, MQTT_PORT, HLS_DIR, HLS_TIME
from engine.shared.utils import ts, load_cameras
from engine.streamer.hls_streamer import start_hls_stream, stream_processes
from engine.streamer.recording import save_body_segments, motion_recordings
from engine.streamer.mqtt_handler import on_motion_and_cmd
from engine.health_monitor_str import StreamerHealer
from engine.shared.logger import get_logger

logger = get_logger("streamer")

logger.info("Streamer started")

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
    print("  🎥  LEGION NVR - STREAM ENGINE v6.0")
    print("=" * 50)
    print(f"{ts()}   📡 MQTT: {MQTT_BROKER}:{MQTT_PORT}")
    print()

    signal.signal(signal.SIGINT, signal_handler)
    os.makedirs(HLS_DIR, exist_ok=True)

    threading.Thread(target=cleanup_temp_files, daemon=True).start()

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
    logger.info(f"{ts()} [Cameras] {len(cameras)}")
    for cam in cameras:
        if cam.get("enabled") and cam.get("stream_enabled", True):
            start_hls_stream(cam)

    from engine.streamer.recording import start_continuous_recording
    for cam in cameras:
        mode = cam.get('record_mode', 'motion_ai')
        if cam.get("record_enabled") and mode in ('continuous_noai', 'schedule_noai', 'schedule_ai'):
            start_continuous_recording(cam)
            logger.info(f"{ts()} 📼 Непрерывная запись: {cam['name']} (режим: {mode})")


    healer = StreamerHealer()
    threading.Thread(target=healer.heal_loop, daemon=True).start()

    
    # Запускаем фоновое сохранение сегментов
    threading.Thread(target=save_body_segments, daemon=True).start()

    logger.info(f"{ts()} [HLS] Streams: {len(stream_processes)}")
    logger.info(f"{ts()} [Subscriptions] spartan/+/motion, spartan/+/cmd, spartan/streams/reload")
    logger.info("[Running] Working... (Ctrl+C to exit)")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        signal_handler(None, None)

def cleanup_temp_files():
    """Периодически чистит временные файлы"""
    import glob as glob_module
    import shutil
    import time as time_module

    while True:
        try:
            cutoff = time_module.time() - 3600  # Старше 1 часа
            for pattern in ['motion_*', 'ai_frames_*', 'ai_overlay_*', 'mjpeg_*', 'mjpeg_full_*']:
                for path in glob_module.glob(os.path.join(tempfile.gettempdir(), pattern)):
                    try:
                        if os.path.getmtime(path) < cutoff:
                            shutil.rmtree(path, ignore_errors=True)
                    except:
                        pass
        except:
            pass
        time_module.sleep(1800)  # Каждые 30 минут

if __name__ == '__main__':
    main()