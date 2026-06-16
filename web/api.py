from flask import Blueprint, request, jsonify
from flask_login import login_required
from models.camera import Camera
from flask_login import login_required, current_user
import paho.mqtt.client as mqtt
from models.database import get_db
from models.user import User
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

# ============================================================
# API: Пользователи
# ============================================================
@api_bp.route('/api/users', methods=['GET'])
@login_required
def get_users():
    """Список пользователей (только для admin)"""
    if current_user.role != 'admin':
        return jsonify({'success': False, 'error': 'Доступ запрещён'}), 403
    users = User.get_all()
    return jsonify({'success': True, 'users': users})

@api_bp.route('/api/users', methods=['POST'])
@login_required
def create_user():
    """Создать пользователя (только admin)"""
    if current_user.role != 'admin':
        return jsonify({'success': False, 'error': 'Доступ запрещён'}), 403
    
    data = request.get_json()
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()
    role = data.get('role', 'viewer')
    
    if not username or len(username) < 2:
        return jsonify({'success': False, 'error': 'Логин должен быть не менее 2 символов'}), 400
    if not password or len(password) < 4:
        return jsonify({'success': False, 'error': 'Пароль должен быть не менее 4 символов'}), 400
    if role not in ('admin', 'viewer'):
        return jsonify({'success': False, 'error': 'Неверная роль'}), 400
    
    if User.get_by_username(username):
        return jsonify({'success': False, 'error': 'Пользователь уже существует'}), 400
    
    user_id = User.create(username, password, role)
    if user_id:
        return jsonify({'success': True, 'id': user_id, 'message': f'Пользователь {username} создан'})
    return jsonify({'success': False, 'error': 'Ошибка создания'}), 500

@api_bp.route('/api/users/<int:user_id>', methods=['PUT'])
@login_required
def update_user(user_id):
    """Изменить роль или пароль пользователя"""
    if current_user.role != 'admin' and current_user.id != user_id:
        return jsonify({'success': False, 'error': 'Доступ запрещён'}), 403
    
    data = request.get_json()
    
    # Смена пароля
    if 'password' in data:
        new_pass = data['password'].strip()
        if len(new_pass) < 4:
            return jsonify({'success': False, 'error': 'Пароль должен быть не менее 4 символов'}), 400
        User.change_password(user_id, new_pass)
    
    # Смена роли (только admin)
    if 'role' in data and current_user.role == 'admin':
        User.update_role(user_id, data['role'])
    
    return jsonify({'success': True, 'message': 'Пользователь обновлён'})

@api_bp.route('/api/users/<int:user_id>', methods=['DELETE'])
@login_required
def delete_user(user_id):
    """Удалить пользователя (только admin, нельзя удалить себя)"""
    if current_user.role != 'admin':
        return jsonify({'success': False, 'error': 'Доступ запрещён'}), 403
    
    if str(current_user.id) == str(user_id):
        return jsonify({'success': False, 'error': 'Нельзя удалить самого себя'}), 400
    
    User.delete(user_id)
    return jsonify({'success': True, 'message': 'Пользователь удалён'})

@api_bp.route('/api/recordings', methods=['DELETE'])
@login_required
def delete_recordings_bulk():
    """Массовое удаление записей по фильтру"""
    if current_user.role != 'admin':
        return jsonify({'success': False, 'error': 'Только для администратора'}), 403
    
    from models.database import get_db
    
    camera_id = request.args.get('camera_id')
    date = request.args.get('date')
    date_before = request.args.get('date_before')
    all_records = request.args.get('all')
    
    conn = get_db()
    
    # Сначала получаем список файлов для удаления с диска
    query = "SELECT filename FROM recordings WHERE 1=1"
    params = []
    
    if all_records:
        pass
    elif camera_id:
        query += " AND camera_id = ?"
        params.append(camera_id)
    elif date:
        query += " AND date(start_time) = ?"
        params.append(date)
    elif date_before:
        query += " AND date(start_time) < ?"
        params.append(date_before)
    else:
        conn.close()
        return jsonify({'success': False, 'error': 'Укажите фильтр или all=true'}), 400
    
    # Получаем файлы
    rows = conn.execute(query, params).fetchall()
    deleted_files = 0
    
    for row in rows:
        try:
            if os.path.exists(row['filename']):
                os.remove(row['filename'])
                deleted_files += 1
        except:
            pass
    
    # Удаляем из БД
    if all_records:
        cursor = conn.execute("DELETE FROM recordings")
    else:
        delete_query = "DELETE FROM recordings" + query.replace("SELECT filename FROM recordings", "")
        cursor = conn.execute(delete_query, params)
    
    deleted_rows = cursor.rowcount
    conn.commit()
    conn.close()
    
    return jsonify({
        'success': True, 
        'deleted_rows': deleted_rows,
        'deleted_files': deleted_files,
        'message': f'Удалено записей: {deleted_rows}, файлов: {deleted_files}'
    })

@api_bp.route('/api/cameras', methods=['GET'])
@login_required
def get_cameras_list():
    """Список камер для фильтров (без авторизации admin)"""
    cameras = Camera.get_all()
    return jsonify({'success': True, 'cameras': cameras})

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

@api_bp.route('/api/recordings', methods=['GET'])
@login_required
def get_recordings():
    """Список записей с фильтрацией"""
    camera_id = request.args.get('camera_id')
    date = request.args.get('date')
    
    conn = get_db()
    query = """
        SELECT r.*, c.name as camera_name 
        FROM recordings r 
        LEFT JOIN cameras c ON r.camera_id = c.id 
        WHERE 1=1
    """
    params = []
    
    if camera_id:
        query += " AND r.camera_id = ?"
        params.append(camera_id)
    if date:
        query += " AND date(r.start_time) = ?"
        params.append(date)
    
    query += " ORDER BY r.start_time DESC LIMIT 100"
    
    rows = conn.execute(query, params).fetchall()
    conn.close()
    
    recordings = [dict(row) for row in rows]
    return jsonify({'success': True, 'recordings': recordings})