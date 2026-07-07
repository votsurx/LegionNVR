"""
Health Monitor для Streamer
"""
import os
import time
import subprocess
import json
import threading
import paho.mqtt.client as mqtt

class StreamerHealer:
    def __init__(self):
        self.last_restart = time.time()
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
            print(f"⏳ Streamer стартует... ({int(time.time() - self.last_restart)}с)")
            return True

        self._pong_received = False
        try:
            client = mqtt.Client()
            client.on_message = self._on_pong
            client.connect("127.0.0.1", 1883, 3)
            client.subscribe("spartan/streamer/pong")
            client.publish("spartan/streamer/cmd", json.dumps({"action": "ping"}))
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
        print("🔪 Убиваю streamer (со всеми дочерними)...")
        try:
            ps_cmd = "Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | Where-Object { $_.CommandLine -like '*streamer*' -and $_.CommandLine -notlike '*web_server*' } | Select-Object ProcessId, CommandLine | ConvertTo-Json"
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
                    print(f"   ✅ PID {pid} и дочерние убиты")
            close_cmd = "Get-Process | Where-Object { $_.MainWindowTitle -like '*streamer*' } | ForEach-Object { $_.CloseMainWindow() }"
            subprocess.run(['powershell', '-Command', close_cmd], capture_output=True, timeout=3)
            print("   ✅ Окно терминала закрыто")
        except Exception as e:
            print(f"   ⚠️ Ошибка убийства: {e}")

    def start(self):
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        print("🚀 Запускаю streamer...")
        subprocess.Popen(
            f'start cmd /k "cd /d {project_root} && python engine/streamer/main.py && pause"',
            shell=True
        )
        self.last_restart = time.time()
        print("✅ streamer запущен, даём 60 сек на старт")

    def heal_loop(self):
        print("⏳ Даю 60 сек на старт streamer...")
        time.sleep(60)

        while True:
            # Сначала проверяем веб-сервер
            if not self.is_web_alive():
                print("❌ Web Server не отвечает!")
                print("🔄 Перезапуск Web Server...")
                # Ищем PID веб-сервера
                ps_cmd = "Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | Where-Object { $_.CommandLine -like '*web_server*' } | Select-Object ProcessId, CommandLine | ConvertTo-Json"
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
                        subprocess.run(['taskkill', '/F', '/T', '/PID', str(pid)], capture_output=True)
                        print(f"   ✅ Web Server (PID {pid}) убит")
                # Запускаем веб-сервер заново
                subprocess.Popen(
                    ['start', 'cmd', '/k', 'cd /d C:\\legionNVR && python web_server.py'],
                    shell=True
                )
                print("✅ Web Server перезапущен")

            # Потом проверяем себя
            if not self.is_alive():
                print(f"⚠️ streamer недоступен. Проверю через 5 сек...")
                time.sleep(5)
                if not self.is_alive():
                    print(f"❌ streamer не отвечает (подтверждено)")
                    print("🔄 Перезапуск streamer...")
                    self.kill()
                    self.start()
                    self.last_restart = time.time()

            time.sleep(30)