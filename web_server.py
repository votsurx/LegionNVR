"""
Legion NVR - Web Server
Запуск: python web_server.py
Только веб-интерфейс, без детектора и стримов
"""
from flask import Flask, send_file, jsonify, Response, request
from flask_login import login_required, LoginManager
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
import sys
import time
import json
import paho.mqtt.client as mqtt

# Отключаем логи HTTP-запросов
logging.getLogger('werkzeug').setLevel(logging.ERROR)

# ============================================================
# СОЗДАЁМ ПРИЛОЖЕНИЕ
# ============================================================
app = Flask(__name__)
app.secret_key = os.urandom(24).hex()

# ✅ Отключаем авторизацию для API
app.config['LOGIN_DISABLED'] = True

# Инициализируем Flask-Login
login_manager.init_app(app)
login_manager.login_view = 'auth.login'

# Регистрируем Blueprints
app.register_blueprint(auth_bp)
app.register_blueprint(main_bp)
app.register_blueprint(api_bp)

HLS_DIR = "streams"


# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================

def send_mqtt_status(camera_id, status_type, value):
    """Отправляет статус камеры в MQTT"""
    try:
        client = mqtt.Client()
        client.connect("127.0.0.1", 1883, 5)
        payload = json.dumps({
            'camera_id': camera_id,
            status_type: value,
            'timestamp': int(time.time())
        })
        topic = f"spartan/{camera_id}/{status_type}"
        client.publish(topic, payload)
        client.disconnect()
        print(f"📡 MQTT: {topic} -> {payload}")
        return True
    except Exception as e:
        print(f"❌ MQTT ошибка: {e}")
        return False


def check_rtsp_available(rtsp_url):
    """Проверяет доступность RTSP потока"""
    ffmpeg = "ffmpeg"
    if shutil.which(ffmpeg) is None:
        for p in ["C:/ffmpeg/bin/ffmpeg.exe", "C:/ffmpeg/ffmpeg.exe"]:
            if os.path.exists(p):
                ffmpeg = p
                break

    if not os.path.exists(ffmpeg):
        return False

    try:
        cmd = [
            ffmpeg, "-loglevel", "error",
            "-rtsp_transport", "tcp",
            "-i", rtsp_url,
            "-frames:v", "1",
            "-f", "null", "NUL"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        return result.returncode == 0
    except:
        return False


# ============================================================
# API КАМЕРЫ
# ============================================================

@app.route('/api/cameras', methods=['GET'])
def api_get_cameras():
    """Возвращает список всех камер с проверкой доступности"""
    from models.camera import Camera
    cameras = Camera.get_all()
    result = []
    for cam in cameras:
        cam_dict = dict(cam)
        # Проверяем доступность RTSP
        cam_dict['online'] = check_rtsp_available(cam_dict['rtsp_main'])
        result.append(cam_dict)
    return jsonify({'success': True, 'cameras': result})


@app.route('/api/cameras/<int:camera_id>', methods=['PUT'])
def api_update_camera(camera_id):
    """Обновляет камеру и управляет компонентами"""
    data = request.get_json()

    cam = Camera.get_by_id(camera_id)
    if not cam:
        return jsonify({'success': False, 'error': 'Камера не найдена'}), 404

    # Проверяем изменения
    old_enabled = cam.get('enabled', 1)
    new_enabled = data.get('enabled', old_enabled)

    old_motion = cam.get('motion_enabled', 0)
    new_motion = data.get('motion_enabled', old_motion)

    old_record = cam.get('record_enabled', 0)
    new_record = data.get('record_enabled', old_record)

    # Обновляем камеру в БД
    Camera.update_full(camera_id, data)

    # ════════════════════════════════════════════════════════════
    # 1. ON/OFF камеры
    # ════════════════════════════════════════════════════════════
    if new_enabled != old_enabled:
        if new_enabled == 1:
            print(f"🟢 Камера {camera_id} ВКЛЮЧЕНА")
            send_mqtt_command(camera_id, 'start_stream')
            send_mqtt_command(camera_id, 'start_detector')
            send_mqtt_command(camera_id, 'reload_config')
        else:
            print(f"🔴 Камера {camera_id} ВЫКЛЮЧЕНА")
            send_mqtt_command(camera_id, 'stop_stream')
            send_mqtt_command(camera_id, 'stop_detector')

        # ✅ ОТПРАВЛЯЕМ СТАТУС
        send_mqtt_status(camera_id, 'status', new_enabled)

    # ════════════════════════════════════════════════════════════
    # 2. ДЕТЕКТОР
    # ════════════════════════════════════════════════════════════
    if new_motion != old_motion:
        print(f"🔍 Детектор камеры {camera_id}: {'ВКЛ' if new_motion else 'ВЫКЛ'}")
        # ✅ ОТПРАВЛЯЕМ СТАТУС
        send_mqtt_status(camera_id, 'motion_status', new_motion)
        if new_motion == 1:
            send_mqtt_command(camera_id, 'reload_config')

    # ════════════════════════════════════════════════════════════
    # 3. ЗАПИСЬ
    # ════════════════════════════════════════════════════════════
    if new_record != old_record:
        print(f"📼 Запись камеры {camera_id}: {'ВКЛ' if new_record else 'ВЫКЛ'}")
        # ✅ ОТПРАВЛЯЕМ СТАТУС
        send_mqtt_status(camera_id, 'record_status', new_record)

    return jsonify({'success': True})
@app.route('/api/cameras/<int:camera_id>/apply', methods=['POST'])
def api_apply_camera(camera_id):
    """Применяет настройки камеры (перезагружает конфиг)"""
    cam = Camera.get_by_id(camera_id)
    if not cam:
        return jsonify({'success': False, 'error': 'Камера не найдена'}), 404

    send_mqtt_command(camera_id, 'reload_config')
    return jsonify({'success': True})


@app.route('/api/cameras/<int:camera_id>/test', methods=['POST'])
def api_test_camera(camera_id):
    """Тест RTSP соединения"""
    cam = Camera.get_by_id(camera_id)
    if not cam:
        return jsonify({'success': False, 'error': 'Камера не найдена'}), 404

    is_available = check_rtsp_available(cam['rtsp_main'])
    return jsonify({
        'success': is_available,
        'message': 'RTSP доступен' if is_available else 'RTSP недоступен'
    })


# ============================================================
# HLS-ЭНДПОИНТЫ
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
# MJPEG-ЭНДПОИНТ
# ============================================================

@app.route('/camera/<id>/mjpeg')
def mjpeg_stream(id):
    """MJPEG-поток для браузера"""
    cam = Camera.get_by_id(int(id))
    if not cam:
        return "Камера не найдена", 404

    # ✅ Проверяем, включена ли камера
    if not cam.get('enabled', True):
        return "Камера отключена", 403

    ffmpeg = "ffmpeg"
    if shutil.which(ffmpeg) is None:
        for p in ["C:/ffmpeg/bin/ffmpeg.exe", "C:/ffmpeg/ffmpeg.exe"]:
            if os.path.exists(p):
                ffmpeg = p
                break

    import tempfile

    def generate():
        tmpfile = os.path.join(tempfile.gettempdir(), f"mjpeg_{id}_{int(time.time())}.mjpeg")

        cmd = [
            ffmpeg,
            "-loglevel", "error",
            "-rtsp_transport", "tcp",
            "-fflags", "nobuffer",
            "-flags", "low_delay",
            "-i", cam["rtsp_sub"] or cam["rtsp_main"],
            "-vf", "fps=8,scale=640:360",
            "-f", "mjpeg",
            "-q:v", "5",
            "-avioflags", "direct",
            "-flush_packets", "1",
            "-y",
            tmpfile
        ]

        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        try:
            time.sleep(1)

            last_size = 0
            while proc.poll() is None:
                if os.path.exists(tmpfile) and os.path.getsize(tmpfile) > last_size:
                    with open(tmpfile, 'rb') as f:
                        f.seek(last_size)
                        data = f.read()
                        if data:
                            start = 0
                            while start < len(data):
                                soi = data.find(b'\xff\xd8', start)
                                if soi == -1:
                                    break
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


@app.route('/camera/<int:camera_id>/snapshot')
def camera_snapshot(camera_id):
    """Возвращает один кадр с камеры"""
    cam = Camera.get_by_id(camera_id)
    if not cam:
        return "Камера не найдена", 404

    ffmpeg = "ffmpeg"
    if shutil.which(ffmpeg) is None:
        for p in ["C:/ffmpeg/bin/ffmpeg.exe", "C:/ffmpeg/ffmpeg.exe"]:
            if os.path.exists(p):
                ffmpeg = p
                break

    import tempfile
    import subprocess
    import os

    tmpfile = tempfile.NamedTemporaryFile(suffix='.jpg', delete=False)
    tmpfile.close()

    cmd = [
        ffmpeg,
        "-loglevel", "error",
        "-rtsp_transport", "tcp",
        "-i", cam["rtsp_sub"] or cam["rtsp_main"],
        "-frames:v", "1",
        "-q:v", "2",
        "-y",
        tmpfile.name
    ]

    try:
        subprocess.run(cmd, timeout=5, capture_output=True)
        if os.path.exists(tmpfile.name) and os.path.getsize(tmpfile.name) > 0:
            return send_file(tmpfile.name, mimetype='image/jpeg')
        return "Не удалось получить кадр", 404
    except Exception as e:
        return f"Ошибка: {e}", 500
    finally:
        try:
            os.remove(tmpfile.name)
        except:
            pass


# ============================================================
# ЗАПИСИ
# ============================================================

@app.route('/recordings/<int:recording_id>/play')
@login_required
def play_recording(recording_id):
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
    print("[Legion NVR] Web Server")
    print("[Web] http://localhost:8080")
    app.run(host='0.0.0.0', port=8080, threaded=True)