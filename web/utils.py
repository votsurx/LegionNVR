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

def check_service_mqtt(service_name):
    """Проверяет, отвечает ли сервис через MQTT"""
    result = {'alive': False}
    def on_message(client, userdata, msg):
        if msg.topic == f"spartan/{service_name}/pong":
            result['alive'] = True

    try:
        client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
        client.on_message = on_message
        client.connect("127.0.0.1", 1883, 3)
        client.subscribe(f"spartan/{service_name}/pong")
        client.loop_start()
        client.publish(f"spartan/{service_name}/cmd", json.dumps({"action": "ping"}))
        time.sleep(1)
        client.loop_stop()
        client.disconnect()
    except:
        pass
    return result['alive']

def restart_service_internal(service_name):
    if service_name not in ('detector', 'streamer'):
        return {'success': False, 'error': 'Неизвестный сервис'}

    script = f'engine/{service_name}/main.py'
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    killed_count = 0

    try:
        ps_cmd = f"Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | Where-Object {{ $_.CommandLine -like '*{service_name}*' -and $_.CommandLine -notlike '*web_server*' }} | Select-Object ProcessId, CommandLine | ConvertTo-Json"
        result = subprocess.run(['powershell', '-Command', ps_cmd], capture_output=True, text=True, timeout=10)

        try:
            processes = json.loads(result.stdout)
            if isinstance(processes, dict):
                processes = [processes]
        except:
            processes = []

        for proc in processes:
            pid = proc.get('ProcessId', 0)
            if not pid:
                continue
            print(f"   🔄 Убиваю {service_name}: PID {pid}")
            try:
                kill_result = subprocess.run(
                    ['taskkill', '/F', '/PID', str(pid)],
                    capture_output=True,
                    timeout=3
                )
                if kill_result.returncode == 0:
                    print(f"   ✅ PID {pid} убит")
                    killed_count += 1
                else:
                    print(f"   ⚠️ PID {pid} не убит (код {kill_result.returncode})")
            except subprocess.TimeoutExpired:
                print(f"   ⏱️ Таймаут при убийстве PID {pid}")
            except Exception as e:
                print(f"   ❌ Ошибка при убийстве PID {pid}: {e}")

        if killed_count == 0:
            print(f"   ℹ️ Старых процессов {service_name} не найдено")

        time.sleep(2)
        print(f"   🚀 Запуск нового {service_name}...")

        # ✅ ГАРАНТИРОВАННО РАБОТАЮЩИЙ СПОСОБ
        os.system(f'start powershell -Command cd "{project_root}"; python {script}')

        return {'success': True, 'killed': killed_count}

    except Exception as e:
        print(f"   ❌ Ошибка: {e}")
        return {'success': False, 'error': str(e)}