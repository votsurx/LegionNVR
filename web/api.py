from flask import Blueprint, request, jsonify
from flask_login import login_required
from models.camera import Camera

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
    data = request.get_json()
    Camera.update(camera_id, **data)
    return jsonify({'success': True})