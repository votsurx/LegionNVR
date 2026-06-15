"""
Legion NVR - Web Server
Запуск: python web_server.py
Только веб-интерфейс, без детектора и стримов
"""
from flask import Flask, send_file, jsonify
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

if __name__ == '__main__':
    print("🛡️ Legion NVR - Web Server")
    print("🌐 http://localhost:8080")
    app.run(host='0.0.0.0', port=8080, threaded=True)