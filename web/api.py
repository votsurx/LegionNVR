from flask import Blueprint, request, jsonify
from flask_login import login_required
from models.camera import Camera
import paho.mqtt.client as mqtt
import json
import time
import os

api_bp = Blueprint('api', __name__)

@api_bp.route('/api/cameras', methods=['POST'])
@login_required
def add_camera():
    data = request.get_json()
    name = data.get('name', '').strip()
    rtsp_main = data.get('rtsp_main', '').strip()
    rtsp_sub = data.get('rtsp_sub', '').strip()
    
    if not name or not rtsp_main:
        return jsonify({'success': False, 'error': 'Название и RTSP обязательны'}), 400
    
    camera_id = Camera.create(name, rtsp_main, rtsp_sub or None)
    return jsonify({'success': True, 'id': camera_id})

@api_bp.route('/api/cameras/<int:camera_id>', methods=['DELETE'])
@login_required
def delete_camera(camera_id):
    Camera.delete(camera_id)
    return jsonify({'success': True})

@api_bp.route('/api/cameras/<int:camera_id>', methods=['PUT'])
@login_required
def update_camera(camera_id):
    """Полное обновление камеры (все поля из формы)"""
    data = request.get_json()
    Camera.update_full(camera_id, data)
    return jsonify({'success': True})

@api_bp.route('/api/cameras/<int:camera_id>/test', methods=['POST'])
@login_required
def test_camera(camera_id):
    """Тест RTSP-соединения"""
    cam = Camera.get_by_id(camera_id)
    if not cam:
        return jsonify({'success': False, 'error': 'Камера не найдена'}), 404
    
    import subprocess, shutil
    ffmpeg = "ffmpeg"
    if shutil.which(ffmpeg) is None:
        for p in ["C:/ffmpeg/bin/ffmpeg.exe", "C:/ffmpeg/ffmpeg.exe"]:
            import os
            if os.path.exists(p):
                ffmpeg = p
                break
    
    try:
        result = subprocess.run(
            [ffmpeg, "-loglevel", "error", "-rtsp_transport", "tcp", 
             "-i", cam["rtsp_main"], "-t", "3", "-f", "null", "NUL"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            return jsonify({'success': True, 'message': 'RTSP работает!'})
        else:
            return jsonify({'success': False, 'error': result.stderr[:200]})
    except subprocess.TimeoutExpired:
        return jsonify({'success': False, 'error': 'Таймаут подключения'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# Зоны детекции
@api_bp.route('/api/cameras/<int:camera_id>/zones', methods=['GET'])
@login_required
def get_zones(camera_id):
    zones = Camera.get_zones(camera_id)
    return jsonify({'success': True, 'zones': zones})

@api_bp.route('/api/cameras/<int:camera_id>/zones', methods=['POST'])
@login_required
def save_zone(camera_id):
    data = request.get_json()
    Camera.save_zone(camera_id, data)
    return jsonify({'success': True})

@api_bp.route('/api/settings/recordings_path', methods=['GET', 'POST'])
@login_required
def recordings_path():
    """Получить или изменить путь к папке записей"""
    from models.database import get_db
    
    if request.method == 'GET':
        conn = get_db()
        row = conn.execute("SELECT value FROM settings WHERE key='recordings_path'").fetchone()
        conn.close()
        path = row[0] if row else "recordings"
        return jsonify({'success': True, 'path': path})
    
    # POST — изменить путь
    data = request.get_json()
    new_path = data.get('path', '').strip()
    if not new_path:
        return jsonify({'success': False, 'error': 'Путь не может быть пустым'}), 400
    
    # Создаём папку если нет
    os.makedirs(new_path, exist_ok=True)
    
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('recordings_path', ?)", (new_path,))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'path': new_path, 'message': 'Путь обновлён. Перезапустите Stream Engine.'})

@api_bp.route('/api/cameras/<int:camera_id>/apply', methods=['POST'])
@login_required
def apply_camera_config(camera_id):
    """Отправляет MQTT-команду на перезагрузку конфига камеры"""
    try:
        import paho.mqtt.client as mqtt
        import json
        import time
        
        client = mqtt.Client()
        client.connect("127.0.0.1", 1883, 5)
        
        payload = json.dumps({
            "action": "reload_config",
            "camera_id": camera_id,
            "timestamp": int(time.time())
        })
        client.publish(f"spartan/{camera_id}/cmd", payload)
        client.disconnect()
        
        return jsonify({'success': True, 'message': 'Команда отправлена'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@api_bp.route('/api/streams/restart', methods=['POST'])
@login_required
def restart_streams():
    """Перезапуск всех стримов через MQTT"""
    try:
        import paho.mqtt.client as mqtt
        import json
        
        client = mqtt.Client()
        client.connect("127.0.0.1", 1883, 5)
        client.publish("spartan/streams/reload", json.dumps({"action": "reload_all"}))
        client.disconnect()
        
        return jsonify({'success': True, 'message': 'Команда отправлена'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@api_bp.route('/api/cameras/<int:camera_id>/zones/<int:zone_id>', methods=['DELETE'])
@login_required
def delete_zone(camera_id, zone_id):
    Camera.delete_zone(zone_id)
    return jsonify({'success': True})