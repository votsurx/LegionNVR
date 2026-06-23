"""
Legion NVR - Stream Engine
Запуск: python engine/streamer.py
Подписывается на MQTT, управляет HLS-стримами и записью по тревоге
"""
import subprocess
import os
import shutil
import json
import threading
import time
import glob
import sys
import signal

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import paho.mqtt.client as mqtt
from models.database import get_db

MQTT_BROKER = "127.0.0.1"
MQTT_PORT = 1883
HLS_DIR = "streams"
RECORDINGS_DIR = "recordings"

# Глобальные переменные для управления процессами
stream_processes = {}
recording_processes = {}
camera_status = {}  # {camera_id: {'enabled': bool, 'online': bool}}


def get_recordings_path():
    """Получает путь к папке записей из настроек"""
    try:
        conn = get_db()
        row = conn.execute("SELECT value FROM settings WHERE key='recordings_path'").fetchone()
        conn.close()
        if row:
            return row[0]
    except:
        pass
    return "recordings"


def find_ffmpeg():
    if shutil.which("ffmpeg"):
        return "ffmpeg"
    for p in ["C:/ffmpeg/bin/ffmpeg.exe", "C:/ffmpeg/ffmpeg.exe"]:
        if os.path.exists(p):
            return p
    return None


def stop_hls_stream(camera_id):
    """Останавливает HLS стрим"""
    cam_id = str(camera_id)
    if cam_id in stream_processes:
        try:
            stream_processes[cam_id].terminate()
            stream_processes[cam_id].wait(timeout=3)
        except:
            stream_processes[cam_id].kill()
        del stream_processes[cam_id]

        # Удаляем файлы стрима
        for f in glob.glob(os.path.join(HLS_DIR, f"camera{cam_id}*.ts")):
            try:
                os.remove(f)
            except:
                pass
        m3u8 = os.path.join(HLS_DIR, f"camera{cam_id}.m3u8")
        if os.path.exists(m3u8):
            try:
                os.remove(m3u8)
            except:
                pass
        print(f"⏹️ HLS стрим для камеры {cam_id} остановлен")


def start_hls_stream(camera):
    """Запускает HLS стрим"""
    cam_id = str(camera["id"])

    # Проверяем, включена ли камера
    if not camera.get("stream_enabled", True):
        print(f"⏸️ Камера {cam_id} отключена, стрим не запущен")
        return

    if not camera.get("enabled", True):
        print(f"⏸️ Камера {cam_id} выключена, стрим не запущен")
        return

    # Останавливаем старый стрим
    stop_hls_stream(cam_id)

    # Чистим старые сегменты
    for f in glob.glob(os.path.join(HLS_DIR, f"camera{cam_id}*.ts")):
        try:
            os.remove(f)
        except:
            pass
    m3u8 = os.path.join(HLS_DIR, f"camera{cam_id}.m3u8")
    if os.path.exists(m3u8):
        try:
            os.remove(m3u8)
        except:
            pass

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        print("❌ ffmpeg не найден!")
        return

    cmd = [
        ffmpeg, "-loglevel", "error",
        "-rtsp_transport", "tcp",
        "-i", camera["rtsp_main"],
        "-c:v", "copy", "-c:a", "aac",
        "-hls_time", "1", "-hls_list_size", "3",
        "-hls_flags", "delete_segments+omit_endlist",
        os.path.join(HLS_DIR, f"camera{cam_id}.m3u8")
    ]

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        stream_processes[cam_id] = proc
        print(f"🎥 HLS стрим '{camera['name']}' запущен (ID={cam_id})")
    except Exception as e:
        print(f"❌ Ошибка запуска стрима для {camera['name']}: {e}")


def restart_hls_stream(camera):
    """Перезапускает HLS стрим"""
    stop_hls_stream(camera["id"])
    time.sleep(0.5)
    start_hls_stream(camera)


def start_motion_recording(camera):
    """Запускает запись по тревоге"""
    cam_id = str(camera["id"])

    # Проверяем, включена ли камера и запись
    if not camera.get("enabled", True):
        print(f"⏸️ Камера {cam_id} выключена, запись не запущена")
        return

    if not camera.get("record_enabled", False):
        print(f"⏸️ Запись для камеры {cam_id} отключена")
        return

    now = time.strftime("%Y-%m-%d_%H-%M-%S")

    recordings_path = get_recordings_path()
    date_dir = os.path.join(recordings_path, f"camera_{cam_id}", time.strftime("%Y-%m-%d"))
    os.makedirs(date_dir, exist_ok=True)

    output = os.path.join(date_dir, f"{now}_motion.mp4")

    ffmpeg = find_ffmpeg()
    cmd = [
        ffmpeg, "-loglevel", "error",
        "-rtsp_transport", "tcp",
        "-i", camera["rtsp_main"],
        "-c:v", "copy", "-c:a", "aac",
        "-t", "15", "-y", output
    ]

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        recording_processes[cam_id] = proc

        # Логируем в БД
        try:
            conn = get_db()
            conn.execute(
                "INSERT INTO recordings (camera_id, filename, start_time, type) VALUES (?, ?, datetime('now','localtime'), 'motion')",
                (camera["id"], output)
            )
            conn.commit()
            conn.close()
        except:
            pass

        print(f"🔴 Запись тревоги: {camera['name']} → {now}_motion.mp4")

        # Ждём окончания в фоне
        def wait_and_log():
            proc.wait()
            print(f"💾 Запись завершена: {now}_motion.mp4")
            if cam_id in recording_processes:
                del recording_processes[cam_id]

        threading.Thread(target=wait_and_log, daemon=True).start()
    except Exception as e:
        print(f"❌ Ошибка записи для {camera['name']}: {e}")


def on_motion(client, userdata, msg):
    """Callback при получении MQTT-сообщения о движении"""
    try:
        data = json.loads(msg.payload.decode())
        if data.get("event") == "motion_start":
            conn = get_db()
            cam = conn.execute("SELECT * FROM cameras WHERE id=?", (data["camera_id"],)).fetchone()
            conn.close()
            if cam:
                cam_dict = dict(cam)
                start_motion_recording(cam_dict)
    except Exception as e:
        print(f"⚠️ Ошибка обработки MQTT: {e}")


def on_cmd(client, userdata, msg):
    """Обработчик команд для стримера"""
    try:
        data = json.loads(msg.payload.decode())
        action = data.get("action")
        cam_id = data.get("camera_id")

        if action == "reload_config" and cam_id:
            print(f"📡 [CMD] Перезагрузка конфига для камеры {cam_id}")
            conn = get_db()
            cam = conn.execute("SELECT * FROM cameras WHERE id=?", (cam_id,)).fetchone()
            conn.close()

            if cam:
                cam_dict = dict(cam)
                # Перезапускаем HLS-стрим
                if cam_dict.get("enabled") and cam_dict.get("stream_enabled"):
                    start_hls_stream(cam_dict)
                    print(f"🔄 Стрим для '{cam_dict['name']}' перезапущен")
                else:
                    stop_hls_stream(cam_id)
                    print(f"⏹️ Стрим '{cam_dict['name']}' остановлен")

        elif action == "reload_all":
            print("📡 [CMD] Перезагрузка ВСЕХ стримов")
            cameras = load_cameras()
            for cam in cameras:
                if cam.get("enabled") and cam.get("stream_enabled"):
                    start_hls_stream(cam)
            print(f"🔄 Перезапущено стримов: {len(stream_processes)}")

        elif action == "stop_stream":
            print(f"⏹️ [CMD] Остановка стрима для камеры {cam_id}")
            stop_hls_stream(cam_id)

        elif action == "start_stream":
            print(f"▶️ [CMD] Запуск стрима для камеры {cam_id}")
            conn = get_db()
            cam = conn.execute("SELECT * FROM cameras WHERE id=?", (cam_id,)).fetchone()
            conn.close()
            if cam:
                start_hls_stream(dict(cam))

    except Exception as e:
        print(f"⚠️ [CMD] Ошибка: {e}")


def load_cameras():
    """Загружает все включённые камеры"""
    conn = get_db()
    rows = conn.execute("SELECT * FROM cameras WHERE enabled=1").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def on_motion_and_cmd(client, userdata, msg):
    """Обрабатывает и motion, и cmd"""
    if msg.topic.endswith("/motion"):
        on_motion(client, userdata, msg)
    else:
        on_cmd(client, userdata, msg)


def signal_handler(sig, frame):
    """Обработчик Ctrl+C"""
    print("\n⏹️ Завершение...")
    for proc in stream_processes.values():
        try:
            proc.terminate()
        except:
            pass
    for proc in recording_processes.values():
        try:
            proc.terminate()
        except:
            pass
    sys.exit(0)


def main():
    print("[Legion NVR] Stream Engine")
    print(f"[MQTT] {MQTT_BROKER}:{MQTT_PORT}")

    # Обработчик сигналов
    signal.signal(signal.SIGINT, signal_handler)

    os.makedirs(HLS_DIR, exist_ok=True)
    os.makedirs(RECORDINGS_DIR, exist_ok=True)

    # MQTT
    client = mqtt.Client()
    client.connect(MQTT_BROKER, MQTT_PORT, 60)
    client.subscribe("spartan/+/motion")
    client.subscribe("spartan/+/cmd")
    client.subscribe("spartan/streams/reload")
    client.on_message = on_motion_and_cmd
    client.loop_start()

    # Запускаем HLS для всех камер
    cameras = load_cameras()
    print(f"[Cameras] {len(cameras)}")
    for cam in cameras:
        start_hls_stream(cam)

    print(f"[HLS] Streams: {len(stream_processes)}")
    print(f"[Subscriptions] spartan/+/motion, spartan/+/cmd, spartan/streams/reload")
    print("[Running] Working... (Ctrl+C to exit)")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        signal_handler(None, None)


if __name__ == '__main__':
    main()