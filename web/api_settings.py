"""
API для настроек (settings)
"""
import os
import time
from flask import Blueprint, request, jsonify
from flask_login import login_required
from models.database import get_db

api_settings_bp = Blueprint('api_settings', __name__, url_prefix='/api/settings')

@api_settings_bp.route('', methods=['GET'])
@login_required
def get_settings():
    """Возвращает все настройки"""
    with get_db() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    settings = {row['key']: row['value'] for row in rows}
    return jsonify({'success': True, 'settings': settings})

@api_settings_bp.route('/<key>', methods=['PUT'])
@login_required
def update_setting(key):
    """Обновляет одну настройку"""
    data = request.get_json()
    value = data.get('value', '').strip()
    
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        conn.commit()
    
    return jsonify({'success': True, 'message': f'{key} обновлён'})

@api_settings_bp.route('/recordings_path', methods=['GET', 'POST'])
@login_required
def recordings_path():
    """Получить или сохранить путь к записям"""
    if request.method == 'GET':
        with get_db() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key='recordings_path'").fetchone()
        path = row[0] if row else "recordings"
        return jsonify({'success': True, 'path': path})

    data = request.get_json()
    new_path = data.get('path', '').strip()
    if not new_path:
        return jsonify({'success': False, 'error': 'Путь обязателен'}), 400

    os.makedirs(new_path, exist_ok=True)

    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('recordings_path', ?)", (new_path,))
        conn.commit()

    return jsonify({'success': True, 'path': new_path, 'message': 'Путь сохранен'})