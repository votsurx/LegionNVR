"""
MQTT утилиты
"""
import json
import time
import paho.mqtt.client as mqtt


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