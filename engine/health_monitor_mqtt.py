"""
Health Monitor для MQTT-брокера
"""
import os
import time
import subprocess
import threading
import paho.mqtt.client as mqtt
from engine.shared.logger import get_logger

logger = get_logger("web_server")

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
        logger.info("🔪 Убиваю MQTT-брокер...")
        subprocess.run(['taskkill', '/F', '/IM', 'mosquitto.exe'], capture_output=True, timeout=3)
        logger.info("✅ MQTT-брокер убит")

    def start(self):
        logger.info("🚀 Запускаю MQTT-брокер...")
        subprocess.Popen(['start', 'cmd', '/k', 'mosquitto'], shell=True)
        self.last_restart = time.time()
        logger.info("✅ MQTT-брокер запущен")

    def heal_loop(self):
        logger.info("⏳ Даю 30 сек на старт MQTT-брокеру...")
        time.sleep(30)

        while True:
            if not self.is_alive():
                logger.info("⚠️ MQTT-брокер недоступен. Проверю через 5 сек...")
                time.sleep(5)
                
                # Вторая проверка
                if not self.is_alive():
                    logger.warning("❌ MQTT-брокер не отвечает (подтверждено)")
                    logger.info("🔄 Перезапуск MQTT...")
                    self.kill()
                    self.start()
                    logger.info("⏳ Жду 30 сек, чтобы сервисы переподключились...")
                    time.sleep(30)
            
            time.sleep(30)