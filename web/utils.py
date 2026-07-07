"""
Вспомогательные функции для веб-модуля
"""
import os
import shutil
import subprocess
import sys
import socket
import json
import time
import paho.mqtt.client as mqtt

def find_ffmpeg():
    """Ищет ffmpeg в системе"""
    if shutil.which("ffmpeg"):
        return "ffmpeg"
    for p in ["C:/ffmpeg/bin/ffmpeg.exe", "C:/ffmpeg/ffmpeg.exe", "/usr/bin/ffmpeg"]:
        if os.path.exists(p):
            return p
    return None

def check_rtsp_available(rtsp_url):
    """Быстрая проверка доступности RTSP (по хосту и порту)"""
    import re
    try:
        match = re.search(r'rtsp://(?:[^@]+@)?([^:/]+)(?::(\d+))?', rtsp_url)
        if match:
            host = match.group(1)
            port = int(match.group(2)) if match.group(2) else 554
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex((host, port))
            sock.close()
            return result == 0
    except:
        pass
    return False

def send_mqtt_command(camera_id, action, params=None):
    """Отправляет MQTT команду"""
    try:
        client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
        client.connect("127.0.0.1", 1883, 5)
        payload = {
            'action': action,
            'camera_id': camera_id,
            'timestamp': int(time.time())
        }
        if params:
            payload.update(params)
        client.publish(f"spartan/{camera_id}/cmd", json.dumps(payload))
        client.disconnect()
        return True
    except Exception as e:
        print(f"❌ MQTT ошибка: {e}")
        return False

def mqtt_running():
    """Проверка MQTT брокера"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex(('127.0.0.1', 1883))
    sock.close()
    return result == 0
