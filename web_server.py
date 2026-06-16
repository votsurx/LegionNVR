"""
Legion NVR - Web Server
Запуск: python web_server.py
Только веб-интерфейс, без детектора и стримов
"""
from flask import Flask, send_file, jsonify
from flask_login import login_required
from models.database import init_db, get_db
from models.user import User
from models.camera import Camera
from web.auth import auth_bp, login_manager
from web.routes import main_bp
from web.api import api_bp
import os

app = Flask(__name__)
app.secret_key = os.urandom(24).hex()

login_manager.init_app(app)
login_manager.login_view = 'auth.login'

app.register_blueprint(auth_bp)
app.register_blueprint(main_bp)
app.register_blueprint(api_bp)

HLS_DIR = "streams"

@app.route('/camera/<id>/stream.m3u8')
def stream(id):
    m3u8_path = os.path.join(HLS_DIR, f"camera{id}.m3u8")
    if os.path.exists(m3u8_path):
        return send_file(m3u8_path, mimetype='application/vnd.apple.mpegurl')
    return "Стрим не запущен", 404

@app.route('/camera/<id>/<segment>')
def segment(id, segment):
    seg_path = os.path.join(HLS_DIR, segment)
    if os.path.exists(seg_path):
        return send_file(seg_path)
    return "Сегмент не найден", 404

with app.app_context():
    init_db()
    if not User.get_by_username('admin'):
        User.create('admin', 'admin123', 'admin')
        print("👤 Создан пользователь: admin / admin123")

@app.route('/recordings/<int:recording_id>/play')
@login_required
def play_recording(recording_id):
    """Отдаёт mp4-файл записи для просмотра"""
    from models.database import get_db
    conn = get_db()
    row = conn.execute("SELECT * FROM recordings WHERE id = ?", (recording_id,)).fetchone()
    conn.close()
    
    if not row:
        return "Запись не найдена", 404
    
    rec = dict(row)
    filepath = rec['filename']
    
    if not os.path.exists(filepath):
        return "Файл не найден на диске", 404
    
    return send_file(filepath, mimetype='video/mp4')

@app.route('/api/recordings/<int:recording_id>', methods=['DELETE'])
@login_required
def delete_recording(recording_id):
    """Удаляет запись из БД и с диска"""
    from models.database import get_db
    conn = get_db()
    row = conn.execute("SELECT * FROM recordings WHERE id = ?", (recording_id,)).fetchone()
    
    if not row:
        conn.close()
        return jsonify({'success': False, 'error': 'Запись не найдена'}), 404
    
    rec = dict(row)
    
    # Удаляем файл
    try:
        if os.path.exists(rec['filename']):
            os.remove(rec['filename'])
    except:
        pass
    
    # Удаляем из БД
    conn.execute("DELETE FROM recordings WHERE id = ?", (recording_id,))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': 'Запись удалена'})

if __name__ == '__main__':
    print("🛡️ Legion NVR - Web Server")
    print("🌐 http://localhost:8080")
    app.run(host='0.0.0.0', port=8080, threaded=True)