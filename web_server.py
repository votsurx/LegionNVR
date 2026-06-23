"""
Legion NVR - Web Server
Запуск: python web_server.py
Только веб-интерфейс, без детектора и стримов
"""
from flask import Flask, send_file, jsonify, Response
from flask_login import login_required
from models.database import init_db, get_db
from models.user import User
from models.camera import Camera
from web.auth import auth_bp, login_manager
from web.routes import main_bp
from web.api import api_bp
import os
import subprocess
import shutil
import logging

# Отключаем логи HTTP-запросов
logging.getLogger('werkzeug').setLevel(logging.ERROR)

app = Flask(__name__)
app.secret_key = os.urandom(24).hex()

login_manager.init_app(app)
login_manager.login_view = 'auth.login'

app.register_blueprint(auth_bp)
app.register_blueprint(main_bp)
app.register_blueprint(api_bp)

HLS_DIR = "streams"

# ============================================================
# HLS-эндпоинты
# ============================================================
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

# ============================================================
# MJPEG-эндпоинт (живое видео без подвисаний)
# ============================================================
@app.route('/camera/<id>/mjpeg')
def mjpeg_stream(id):
    """MJPEG-поток для браузера — через временный файл"""
    cam = Camera.get_by_id(int(id))
    if not cam:
        return "Камера не найдена", 404
    
    ffmpeg = "ffmpeg"
    if shutil.which(ffmpeg) is None:
        for p in ["C:/ffmpeg/bin/ffmpeg.exe", "C:/ffmpeg/ffmpeg.exe"]:
            if os.path.exists(p):
                ffmpeg = p
                break
    
    import tempfile, time
    
    def generate():
        tmpfile = os.path.join(tempfile.gettempdir(), f"mjpeg_{id}_{int(time.time())}.mjpeg")
        
        cmd = [
            ffmpeg,
            "-loglevel", "error",
            "-rtsp_transport", "tcp",
            "-fflags", "nobuffer",        # ← ДОБАВЬ
            "-flags", "low_delay",        # ← ДОБАВЬ
            "-i", cam["rtsp_sub"] or cam["rtsp_main"],
            "-vf", "fps=8,scale=640:360", # ← 8 fps вместо 5
            "-f", "mjpeg",
            "-q:v", "5",
            "-avioflags", "direct",       # ← ДОБАВЬ (прямая запись)
            "-flush_packets", "1",        # ← ДОБАВЬ (сброс пакетов)
            "-y",
            tmpfile
        ]
        
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        try:
            # Ждём пока ffmpeg создаст файл и начнёт писать
            time.sleep(1)
            
            last_size = 0
            while proc.poll() is None:
                if os.path.exists(tmpfile) and os.path.getsize(tmpfile) > last_size:
                    with open(tmpfile, 'rb') as f:
                        f.seek(last_size)
                        data = f.read()
                        if data:
                            # Ищем JPEG-кадры (начинаются с FF D8, заканчиваются FF D9)
                            start = 0
                            while start < len(data):
                                # Ищем начало JPEG
                                soi = data.find(b'\xff\xd8', start)
                                if soi == -1:
                                    break
                                # Ищем конец JPEG
                                eoi = data.find(b'\xff\xd9', soi)
                                if eoi == -1:
                                    break
                                frame = data[soi:eoi+2]
                                yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
                                start = eoi + 2
                    last_size = os.path.getsize(tmpfile)
                else:
                    time.sleep(0.1)
        except GeneratorExit:
            pass
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except:
                proc.kill()
            try:
                os.remove(tmpfile)
            except:
                pass
    
    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')

# ============================================================
# Записи
# ============================================================
@app.route('/recordings/<int:recording_id>/play')
@login_required
def play_recording(recording_id):
    """Отдаёт mp4-файл записи для просмотра"""
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
    conn = get_db()
    row = conn.execute("SELECT * FROM recordings WHERE id = ?", (recording_id,)).fetchone()
    
    if not row:
        conn.close()
        return jsonify({'success': False, 'error': 'Запись не найдена'}), 404
    
    rec = dict(row)
    
    try:
        if os.path.exists(rec['filename']):
            os.remove(rec['filename'])
    except:
        pass
    
    conn.execute("DELETE FROM recordings WHERE id = ?", (recording_id,))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': 'Запись удалена'})

# ============================================================
# Health-check
# ============================================================
@app.route('/api/health')
def health():
    import time
    return jsonify({
        "status": "alive",
        "service": "web_server",
        "timestamp": int(time.time())
    })

# ============================================================
# ИНИЦИАЛИЗАЦИЯ
# ============================================================
with app.app_context():
    init_db()
    if not User.get_by_username('admin'):
        User.create('admin', 'admin123', 'admin')
        print("👤 Создан пользователь: admin / admin123")

if __name__ == '__main__':
    print("🛡️ Legion NVR - Web Server")
    print("🌐 http://localhost:8080")
    app.run(host='0.0.0.0', port=8080, threaded=True)