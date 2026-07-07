"""
Health Monitor для MQTT-брокера
"""
import os
import time
import subprocess
import threading
import paho.mqtt.client as mqtt

class MQTThealer:
    def __init__(self):
        self.last_restart = time.time()

    def is_alive(self):
        """Проверяет, жив ли MQTT-брокер"""
        try:
            client = mqtt.Client()
            client.connect("127.0.0.1", 1883, 2)
            client.disconnect()
            return True
        except:
            return False

    def kill(self):
        print("🔪 Убиваю MQTT-брокер...")
        subprocess.run(['taskkill', '/F', '/IM', 'mosquitto.exe'], capture_output=True, timeout=3)
        print("✅ MQTT-брокер убит")

    def start(self):
        print("🚀 Запускаю MQTT-брокер...")
        subprocess.Popen(['start', 'cmd', '/k', 'mosquitto'], shell=True)
        self.last_restart = time.time()
        print("✅ MQTT-брокер запущен")

    def heal_loop(self):
        print("⏳ Даю 30 сек на старт MQTT-брокеру...")
        time.sleep(30)
        print("✅ Стартовая пауза завершена")

        while True:
            if not self.is_alive():
                print("⚠️ MQTT-брокер недоступен. Проверю через 5 сек...")
                time.sleep(5)
                
                # Вторая проверка
                if not self.is_alive():
                    print("❌ MQTT-брокер не отвечает (подтверждено)")
                    print("🔄 Перезапуск MQTT...")
                    self.kill()
                    self.start()
                    print("⏳ Жду 30 сек, чтобы сервисы переподключились...")
                    time.sleep(30)
            
            time.sleep(30)