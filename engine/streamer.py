"""
Legion NVR - Stream Engine
Запуск: python engine/streamer.py
Подписывается на MQTT, управляет HLS-стримами и записью по тревоге
"""
import subprocess
import os
import shutil
import json
import time
import glob
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import paho.mqtt.client as mqtt
from models.database import get_db

MQTT_BROKER = "127.0.0.1"
MQTT_PORT = 1883
HLS_DIR = "streams"
RECORDINGS_DIR = "recordings"

stream_processes = {}

def find_ffmpeg():
    if shutil.which("ffmpeg"):
        return "ffmpeg"
    for p in ["C:/ffmpeg/bin/ffmpeg.exe", "C:/ffmpeg/ffmpeg.exe"]:
        if os.path.exists(p):
            return p
    return None

def start_hls_stream(camera):
    cam_id = str(camera["id"])
    
    # Останавливаем старый
    if cam_id in stream_processes:
        try:
            stream_processes[cam_id].terminate()
        except:
            pass
    
    # Чистим сегменты
    for f in glob.glob(os.path.join(HLS_DIR, f"camera{cam_id}*.ts")):
        os.remove(f)
    m3u8 = os.path.join(HLS_DIR, f"camera{cam_id}.m3u8")
    if os.path.exists(m3u8):
        os.remove(m3u8)
    
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
    
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    stream_processes[cam_id] = proc
    print(f"🎥 HLS стрим '{camera['name']}' запущен (ID={cam_id})")

def start_motion_recording(camera):
    cam_id = str(camera["id"])
    now = time.strftime("%Y-%m-%d_%H-%M-%S")
    date_dir = os.path.join(RECORDINGS_DIR, f"camera_{cam_id}", time.strftime("%Y-%m-%d"))
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
    
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
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

def on_motion(client, userdata, msg):
    """Callback при получении MQTT-сообщения о движении"""
    try:
        data = json.loads(msg.payload.decode())
        if data.get("event") == "motion_start":
            # Находим камеру в БД
            conn = get_db()
            cam = conn.execute("SELECT * FROM cameras WHERE id=?", (data["camera_id"],)).fetchone()
            conn.close()
            if cam and dict(cam).get("record_enabled"):
                start_motion_recording(dict(cam))
    except Exception as e:
        print(f"⚠️ Ошибка обработки MQTT: {e}")

def load_cameras():
    conn = get_db()
    rows = conn.execute("SELECT * FROM cameras WHERE enabled=1").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def main():
    print("🛡️ Legion NVR - Stream Engine")
    print(f"📡 MQTT: {MQTT_BROKER}:{MQTT_PORT}")
    
    os.makedirs(HLS_DIR, exist_ok=True)
    os.makedirs(RECORDINGS_DIR, exist_ok=True)
    
    # MQTT
    client = mqtt.Client()
    client.connect(MQTT_BROKER, MQTT_PORT, 60)
    client.subscribe("spartan/+/motion")
    client.on_message = on_motion
    client.loop_start()
    
    # Запускаем HLS для всех камер
    cameras = load_cameras()
    print(f"📷 Камер: {len(cameras)}")
    for cam in cameras:
        start_hls_stream(cam)
    
    print(f"🎥 HLS-стримов: {len(stream_processes)}")
    print("⏳ Работаю... (Ctrl+C для выхода)")
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n⏹️ Завершение...")
        for proc in stream_processes.values():
            proc.terminate()
        client.loop_stop()
        client.disconnect()

if __name__ == '__main__':
    main()