"""
API для управления камерами
"""
from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from models.camera import Camera
from models.database import get_db
from web.utils import send_mqtt_command, check_rtsp_available
import json
import time

api_cameras_bp = Blueprint('api_cameras', __name__, url_prefix='/api/cameras')

@api_cameras_bp.route('', methods=['GET'])
@login_required
def get_cameras():
    """Возвращает список всех камер с проверкой доступности"""
    cameras = Camera.get_all()
    result = []
    for cam in cameras:
        cam_dict = dict(cam)
        cam_dict['online'] = check_rtsp_available(cam_dict['rtsp_main'])
        result.append(cam_dict)
    return jsonify({'success': True, 'cameras': result})

@api_cameras_bp.route('', methods=['POST'])
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

@api_cameras_bp.route('/<int:camera_id>', methods=['PUT'])
@login_required
def update_camera(camera_id):
    data = request.get_json()
    cam = Camera.get_by_id(camera_id)
    if not cam:
        return jsonify({'success': False, 'error': 'Камера не найдена'}), 404

    old_enabled = cam.get('enabled', 1)
    old_motion = cam.get('motion_enabled', 0)
    old_record = cam.get('record_enabled', 0)

    Camera.update_full(camera_id, data)

    new_enabled = data.get('enabled', old_enabled)
    new_motion = data.get('motion_enabled', old_motion)
    new_record = data.get('record_enabled', old_record)

    if new_enabled != old_enabled:
        if new_enabled == 1:
            print(f"🟢 Камера {camera_id} ВКЛЮЧЕНА")
            send_mqtt_command(camera_id, 'start_stream')
        else:
            print(f"🔴 Камера {camera_id} ВЫКЛЮЧЕНА → стоп всё")
            Camera.update_full(camera_id, {'motion_enabled': 0, 'record_enabled': 0})
            send_mqtt_command(camera_id, 'stop_stream')
            send_mqtt_command(camera_id, 'stop_detector')
            send_mqtt_command(camera_id, 'stop_recording')
    elif new_motion != old_motion:
        if new_motion == 1:
            print(f"🔍 Детектор камеры {camera_id}: ВКЛ")
            send_mqtt_command(camera_id, 'reload_config')
        else:
            print(f"🔍 Детектор камеры {camera_id}: ВЫКЛ")
            Camera.update_full(camera_id, {'record_enabled': 0})
            send_mqtt_command(camera_id, 'stop_detector')
            send_mqtt_command(camera_id, 'stop_recording')
    elif new_record != old_record:
        if new_record == 0:
            print(f"📼 Запись камеры {camera_id}: ВЫКЛ")
            send_mqtt_command(camera_id, 'stop_recording')
    else:
        if any(k in data for k in ['motion_threshold', 'motion_cooldown', 'motion_fps']):
            send_mqtt_command(camera_id, 'reload_config')

    return jsonify({'success': True})

@api_cameras_bp.route('/<int:camera_id>', methods=['DELETE'])
@login_required
def delete_camera(camera_id):
    Camera.delete(camera_id)
    return jsonify({'success': True})

@api_cameras_bp.route('/<int:camera_id>/test', methods=['POST'])
@login_required
def test_camera(camera_id):
    """Тест RTSP-соединения"""
    cam = Camera.get_by_id(camera_id)
    if not cam:
        return jsonify({'success': False, 'error': 'Камера не найдена'}), 404

    is_available = check_rtsp_available(cam['rtsp_main'])
    return jsonify({'success': is_available, 'message': 'RTSP доступен' if is_available else 'RTSP недоступен'})

@api_cameras_bp.route('/<int:camera_id>/status', methods=['GET'])
@login_required
def camera_status(camera_id):
    """Возвращает статус камеры для оверлея"""
    cam = Camera.get_by_id(camera_id)
    if not cam:
        return jsonify({'success': False}), 404

    online = check_rtsp_available(cam['rtsp_main'])

    with get_db() as conn:
        last_event = conn.execute(
            "SELECT * FROM events WHERE camera_id=? ORDER BY timestamp DESC LIMIT 1",
            (camera_id,)
        ).fetchone()

    return jsonify({
        'success': True,
        'online': online,
        'name': cam['name'],
        'enabled': cam.get('enabled', 0),
        'motion_enabled': cam.get('motion_enabled', 0),
        'record_enabled': cam.get('record_enabled', 0),
        'ai_enabled': cam.get('ai_enabled', 0),
        'last_event': dict(last_event) if last_event else None,
        'server_time': time.strftime('%H:%M:%S')
    })

@api_cameras_bp.route('/<int:camera_id>/apply', methods=['POST'])
@login_required
def apply_camera_config(camera_id):
    """Применяет настройки камеры (перезагружает конфиг)"""
    cam = Camera.get_by_id(camera_id)
    if not cam:
        return jsonify({'success': False, 'error': 'Камера не найдена'}), 404

    send_mqtt_command(camera_id, 'reload_config')
    return jsonify({'success': True})

@api_cameras_bp.route('/<int:camera_id>/zones', methods=['GET'])
@login_required
def get_zones(camera_id):
    zones = Camera.get_zones(camera_id)
    return jsonify({'success': True, 'zones': zones})

@api_cameras_bp.route('/<int:camera_id>/zones', methods=['POST'])
@login_required
def save_zone(camera_id):
    data = request.get_json()
    Camera.save_zone(camera_id, data)
    return jsonify({'success': True})

@api_cameras_bp.route('/<int:camera_id>/zones/<int:zone_id>', methods=['DELETE'])
@login_required
def delete_zone(camera_id, zone_id):
    Camera.delete_zone(zone_id)
    return jsonify({'success': True})