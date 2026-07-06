"""
API для управления записями
"""
import os
import time
from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from models.database import get_db
from models.recording import Recording
from web.utils import send_mqtt_command, check_rtsp_available

api_recordings_bp = Blueprint('api_recordings', __name__, url_prefix='/api/recordings')

@api_recordings_bp.route('', methods=['GET'])
@login_required
def get_recordings():
    camera_id = request.args.get('camera_id')
    date = request.args.get('date')

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

    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()
    
    recordings = [dict(row) for row in rows]
    return jsonify({'success': True, 'recordings': recordings})

@api_recordings_bp.route('', methods=['DELETE'])
@login_required
def delete_recordings_bulk():
    """Удаление записей"""
    if current_user.role != 'admin':
        return jsonify({'success': False, 'error': 'Только для админов'}), 403

    camera_id = request.args.get('camera_id')
    date = request.args.get('date')
    all_records = request.args.get('all')

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
    else:
        return jsonify({'success': False, 'error': 'Укажите параметры или all=true'}), 400

    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()
        deleted_files = 0

        for row in rows:
            try:
                if os.path.exists(row['filename']):
                    os.remove(row['filename'])
                    deleted_files += 1
            except:
                pass

        if all_records:
            conn.execute("DELETE FROM recordings")
        else:
            delete_query = query.replace("SELECT filename FROM recordings", "DELETE FROM recordings")
            conn.execute(delete_query, params)

        conn.commit()

    return jsonify({
        'success': True,
        'deleted_files': deleted_files,
        'message': f'Удалено файлов: {deleted_files}'
    })

@api_recordings_bp.route('/<int:recording_id>', methods=['DELETE'])
@login_required
def delete_recording(recording_id):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM recordings WHERE id = ?", (recording_id,)).fetchone()

        if not row:
            return jsonify({'success': False, 'error': 'Запись не найдена'}), 404

        rec = dict(row)

        try:
            if os.path.exists(rec['filename']):
                os.remove(rec['filename'])
        except:
            pass

        conn.execute("DELETE FROM recordings WHERE id = ?", (recording_id,))
        conn.commit()

    return jsonify({'success': True, 'message': 'Запись удалена'})
