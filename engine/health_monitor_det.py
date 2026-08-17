"""
Health Monitor для Detector
"""
import os
import time
import subprocess
import json
import threading
import paho.mqtt.client as mqtt
from engine.shared.utils import ts

class DetectorHealer:
    def __init__(self):
        self.last_restart = time.time()  # время старта
        self._pong_received = False

    def is_web_alive(self):
        try:
            import requests
            requests.get("http://localhost:8080/health", timeout=2)
            return True
        except:
            return False
    
    def is_alive(self):
        if time.time() - self.last_restart < 60:
            return True

        self._pong_received = False
        try:
            client = mqtt.Client()
            client.on_message = self._on_pong
            client.connect("127.0.0.1", 1883, 3)
            client.subscribe("spartan/detector/pong")
            client.publish("spartan/detector/cmd", json.dumps({"action": "ping"}))
            client.loop_start()
            time.sleep(2)
            client.loop_stop()
        except:
            pass
        result = self._pong_received
        return result

    def _on_pong(self, client, userdata, msg):
        self._pong_received = True

    def kill(self):
        print(f"{ts()}🔪 Убиваю detector (со всеми дочерними)...")
        try:
            ps_cmd = "Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | Where-Object { $_.CommandLine -like '*detector*' -and $_.CommandLine -notlike '*web_server*' } | Select-Object ProcessId, CommandLine | ConvertTo-Json"
            result = subprocess.run(['powershell', '-Command', ps_cmd], capture_output=True, text=True, timeout=5)
            try:
                processes = json.loads(result.stdout)
                if isinstance(processes, dict):
                    processes = [processes]
            except:
                processes = []
            for proc in processes:
                pid = proc.get('ProcessId', 0)
                if pid:
                    subprocess.run(['taskkill', '/F', '/T', '/PID', str(pid)], capture_output=True, timeout=3)
                    print(f"{ts()}   ✅ PID {pid} и дочерние убиты")
            close_cmd = "Get-Process | Where-Object { $_.MainWindowTitle -like '*detector*' } | ForEach-Object { $_.CloseMainWindow() }"
            subprocess.run(['powershell', '-Command', close_cmd], capture_output=True, timeout=3)
            print(f"{ts()}   ✅ Окно терминала закрыто")
        except Exception as e:
            print(f"{ts()}   ⚠️ Ошибка убийства: {e}")

    def start(self):
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        print(f"{ts()}🚀 Запускаю detector...")
        subprocess.Popen(
            f'start cmd /k "cd /d {project_root} && python engine/detector/main.py && pause"',
            shell=True
        )
        #self.last_restart = time.time()
        print(f"{ts()}✅ detector запущен, даём 60 сек на старт")

    def heal_loop(self):
        while True:
            # Только проверка детектора через MQTT ping/pong
            if not self.is_alive():
                print(f"{ts()}⚠️ detector недоступен. Проверю через 5 сек...")
                time.sleep(5)
                if not self.is_alive():
                    print(f"{ts()}❌ detector не отвечает (подтверждено)")
                    print(f"{ts()}🔄 Перезапуск detector...")
                    self.kill()
                    self.start()
                    self.last_restart = time.time()
            time.sleep(30)