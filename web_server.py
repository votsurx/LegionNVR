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
import psutil
import glob
from models.database import get_db

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

def check_process_running(script_name):
    """Проверяет, запущен ли Python-процесс с указанным скриптом"""
    try:
        result = subprocess.run(
            ['wmic', 'process', 'where', f'name="python.exe"', 'get', 'commandline'],
            capture_output=True, text=True, timeout=5
        )
        return script_name in result.stdout
    except:
        return False

def check_service_mqtt(service_name):
    """Проверяет, отвечает ли сервис через MQTT"""
    import paho.mqtt.client as mqtt
    import threading

    result = {'alive': False}

    def on_message(client, userdata, msg):
        if msg.topic == f"spartan/{service_name}/pong":
            result['alive'] = True

    try:
        client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
        client.on_message = on_message
        client.connect("127.0.0.1", 1883, 3)
        client.subscribe(f"spartan/{service_name}/pong")
        client.loop_start()

        # ✅ Отправляем ping в spartan/{name}/cmd (куда подписан сервис)
        client.publish(f"spartan/{service_name}/cmd", json.dumps({"action": "ping"}))
        time.sleep(1)  # Ждём ответ

        client.loop_stop()
        client.disconnect()
    except:
        pass

    return result['alive']

def mqtt_running():
    """Проверка MQTT брокера"""
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex(('127.0.0.1', 1883))
    sock.close()
    return result == 0

def send_mqtt_command(camera_id, action, params=None):
    """Отправляет MQTT команду"""
    try:
        import paho.mqtt.client as mqtt
        client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
        client.connect("127.0.0.1", 1883, 5)
        payload = {
            'action': action,
            'camera_id': camera_id,
            'timestamp': int(time.time())
        }
        if params:
            payload.update(params)
        client.publish(f"spartan/{camera_id}/cmd", json.dumps(payload))
        client.disconnect()
        return True
    except Exception as e:
        print(f"❌ MQTT ошибка: {e}")
        return False

def send_mqtt_status(camera_id, status_type, value):
    """Отправляет статус камеры в MQTT"""
    try:
        client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
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
    """Быстрая проверка доступности RTSP (по хосту и порту)"""
    import socket
    import re

    try:
        # Парсим хост и порт из RTSP URL
        # rtsp://admin:pass@192.168.1.100:554/stream
        match = re.search(r'rtsp://(?:[^@]+@)?([^:/]+)(?::(\d+))?', rtsp_url)
        if match:
            host = match.group(1)
            port = int(match.group(2)) if match.group(2) else 554

            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex((host, port))
            sock.close()
            return result == 0
    except:
        pass
    return False


# ============================================================
# API HEALTH
# ============================================================

@app.route('/api/health/full')
def health_full():
    import psutil
    import os
    import time as time_module

    # Системные метрики
    cpu_percent = psutil.cpu_percent(interval=0.5)
    ram = psutil.virtual_memory()
    disk = psutil.disk_usage('C:\\')
    uptime_seconds = time_module.time() - psutil.boot_time()
    uptime_str = f"{int(uptime_seconds // 86400)}д {int((uptime_seconds % 86400) // 3600)}ч {int((uptime_seconds % 3600) // 60)}м"

    # ✅ ПУТЬ К ЗАПИСЯМ ИЗ НАСТРОЕК
    recordings_path = 'recordings'
    try:
        with get_db() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key='recordings_path'").fetchone()
        if row and row[0]:
            recordings_path = row[0]
    except:
        pass

    # Если путь относительный — делаем абсолютным
    if not os.path.isabs(recordings_path):
        recordings_path = os.path.join(os.path.dirname(__file__), recordings_path)

    # ✅ РЕКУРСИВНЫЙ ОБХОД ВСЕХ ПОДПАПОК
    recordings_size = 0
    recordings_count = 0
    today_str = time.strftime("%Y-%m-%d")
    today_count = 0

    if os.path.exists(recordings_path):
        for dirpath, dirnames, filenames in os.walk(recordings_path):
            for f in filenames:
                try:
                    fp = os.path.join(dirpath, f)
                    size = os.path.getsize(fp)
                    recordings_size += size
                    recordings_count += 1

                    # ✅ ПРОВЕРЯЕМ ДАТУ ФАЙЛА (для "сегодня")
                    try:
                        file_date = time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(fp)))
                        if file_date == today_str:
                            today_count += 1
                    except:
                        pass
                except:
                    pass

    # ✅ HLS СЕГМЕНТЫ
    streams_count = len(glob.glob(os.path.join('streams', '*.ts'))) if os.path.exists('streams') else 0

    # ✅ КАМЕРЫ (быстрая проверка)
    cameras = Camera.get_all()
    cameras_online = 0
    cameras_list = []
    for cam in cameras:
        try:
            online = check_rtsp_available(cam['rtsp_main'])
        except:
            online = False
        if online:
            cameras_online += 1
        cameras_list.append({
            'id': cam['id'],
            'name': cam['name'],
            'online': online,
            'enabled': cam.get('enabled', 0),
            'motion_enabled': cam.get('motion_enabled', 0),
            'ai_enabled': cam.get('ai_enabled', 0)
        })

    # ✅ СЕРВИСЫ
    services = {
        'web_server': {'status': 'running', 'port': 8080, 'pid': os.getpid()},
        'mqtt': {'status': 'running' if mqtt_running() else 'stopped', 'port': 1883},
        'detector': {'status': 'running' if check_service_mqtt('detector') else 'stopped', 'port': None},
        'streamer': {'status': 'running' if check_service_mqtt('streamer') else 'stopped', 'port': None}
    }

    # ✅ AI СТАТИСТИКА + ЗАПИСИ ИЗ БД
    with get_db() as conn:
        total_events = conn.execute(
            "SELECT COUNT(*) FROM events WHERE event_type='motion_start'"
        ).fetchone()[0]

        ai_events = conn.execute("""
            SELECT COUNT(*) FROM events
            WHERE event_type='motion_start'
            AND (details LIKE '%\"ai\"%' OR details LIKE '%person%' OR details LIKE '%car%')
        """).fetchone()[0]

        # ✅ ЗАПИСИ ЗА СЕГОДНЯ (ИЗ БД — ТОЧНЕЕ)
        today_recordings_db = conn.execute(
            "SELECT COUNT(*) FROM recordings WHERE date(start_time) = date('now','localtime')"
        ).fetchone()[0]

        # Последние события
        last_events = conn.execute(
            "SELECT e.*, c.name as camera_name FROM events e LEFT JOIN cameras c ON e.camera_id=c.id ORDER BY e.timestamp DESC LIMIT 10"
        ).fetchall()
        events_list = [dict(r) for r in last_events]

    # ✅ ИСПОЛЬЗУЕМ БД ДЛЯ "СЕГОДНЯ" (точнее), или файлы (если БД пустая)
    final_today = today_recordings_db if today_recordings_db > 0 else today_count

    events_list = []
    for r in last_events:
        event = dict(r)
        try:
            details = json.loads(event.get('details', '{}'))
            ts = details.get('timestamp', 0)
            if ts:
                event['timestamp'] = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts))
        except:
            pass
        events_list.append(event)

    return jsonify({
        'success': True,
        'system': {
            'cpu': cpu_percent,
            'ram_used_mb': round(ram.used / (1024*1024), 1),
            'ram_total_mb': round(ram.total / (1024*1024), 1),
            'ram_percent': ram.percent,
            'disk_free_gb': round(disk.free / (1024*1024*1024), 1),
            'disk_total_gb': round(disk.total / (1024*1024*1024), 1),
            'disk_percent': disk.percent,
            'uptime': uptime_str,
            'python_version': sys.version.split()[0]
        },
        'services': services,
        'cameras': {
            'total': len(cameras),
            'online': cameras_online,
            'offline': len(cameras) - cameras_online,
            'list': cameras_list
        },
        'ai': {
            'total_events': total_events,
            'ai_events': ai_events,
            'filtered': total_events - ai_events,
            'filter_rate': round((total_events - ai_events) / total_events * 100, 1) if total_events > 0 else 0
        },
        'recordings': {
            'total': recordings_count,
            'today': final_today,
            'size_mb': round(recordings_size / (1024*1024), 1)
        },
        'streams': {
            'active_segments': streams_count
        },
        'events': events_list
    })

@app.route('/api/health/reset', methods=['POST'])
def reset_health_stats():
    """Сбрасывает статистику (события, AI-статистику)"""
    try:
        with get_db() as conn:
            conn.execute("DELETE FROM events")
            conn.commit()
        return jsonify({'success': True, 'message': f'Статистика сброшена'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/service/restart/<service_name>', methods=['POST'])
def restart_service(service_name):
    """Жёсткий перезапуск сервиса (выборочное убийство)"""
    import subprocess

    if service_name not in ('detector', 'streamer', 'web_server'):
        return jsonify({'success': False, 'error': 'Неизвестный сервис'}), 400

    if service_name == 'web_server':
        return restart_web_server()

    script = f'engine/{service_name}/main.py'
    project_root = os.path.dirname(os.path.abspath(__file__))
    current_pid = os.getpid()
    killed_count = 0

    try:
        # ✅ Используем PowerShell Get-CimInstance (работает!)
        ps_cmd = f'Get-CimInstance Win32_Process -Filter "name=\'python.exe\'" | Select-Object ProcessId, CommandLine | ConvertTo-Json'
        result = subprocess.run(
            ['powershell', '-Command', ps_cmd],
            capture_output=True, text=True, timeout=10
        )

        print(f"🔄 Поиск процессов {service_name}...")
        print(f"🔄 Вывод PowerShell:\n{result.stdout[:500]}")

        # Парсим JSON
        import json
        try:
            processes = json.loads(result.stdout)
            # Если один процесс — оборачиваем в список
            if isinstance(processes, dict):
                processes = [processes]
        except:
            print(f"⚠️ Не удалось распарсить JSON")
            processes = []

        for proc in processes:
            pid = proc.get('ProcessId', 0)
            cmd = proc.get('CommandLine', '')

            if not pid:
                continue

            print(f"🔄 Найден: PID={pid}, CMD={cmd[:150]}")

            # Не убиваем web_server (себя)
            if pid == current_pid or 'web_server' in cmd:
                print(f"🔄 Пропускаю web_server")
                continue

            # Убиваем по ключевым словам
            if service_name in cmd:
                print(f"🔄 Убиваю {service_name} (PID {pid})...")
                kill_result = subprocess.run(
                    ['taskkill', '/F', '/T', '/PID', str(pid)],  # ← ДОБАВИЛ /T
                    capture_output=True, text=True, timeout=5
                )
                if kill_result.returncode == 0:
                    print(f"🔄 PID {pid} убит")
                    killed_count += 1

        time.sleep(2)

        # ✅ Запускаем новый процесс (CMD окно, не PowerShell)
        subprocess.Popen(
            ['cmd', '/c', 'start', 'python', script],
            cwd=project_root
        )

        message = f'{service_name} перезапущен (убито {killed_count})'
        print(f"🔄 {message}")
        return jsonify({'success': True, 'message': message, 'killed': killed_count})

    except Exception as e:
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

def restart_web_server():
    """Перезапуск веб-сервера через внешний скрипт"""
    script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'restart_web.ps1')

    try:
        subprocess.Popen(
            ['powershell', '-ExecutionPolicy', 'Bypass', '-File', script_path, '-PIDtoKill', str(os.getpid())],
            creationflags=subprocess.CREATE_NEW_CONSOLE if sys.platform == 'win32' else 0
        )
        return jsonify({'success': True, 'message': 'Web Server перезапускается...'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
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

    # 1. КАМЕРА ON/OFF
    if new_enabled != old_enabled:
        if new_enabled == 1:
            print(f"🟢 Камера {camera_id} ВКЛЮЧЕНА")
            send_mqtt_command(camera_id, 'start_stream')
        else:
            print(f"🔴 Камера {camera_id} ВЫКЛЮЧЕНА → стоп всё")
            # ✅ ОБНОВЛЯЕМ БД
            Camera.update_full(camera_id, {
                'motion_enabled': 0,
                'record_enabled': 0
            })
            send_mqtt_command(camera_id, 'stop_stream')
            send_mqtt_command(camera_id, 'stop_detector')
            send_mqtt_command(camera_id, 'stop_recording')

    # 2. ДЕТЕКТОР ON/OFF
    elif new_motion != old_motion:
        if new_motion == 1:
            print(f"🔍 Детектор камеры {camera_id}: ВКЛ")
            send_mqtt_command(camera_id, 'reload_config')
        else:
            print(f"🔍 Детектор камеры {camera_id}: ВЫКЛ")
            # ✅ ОСТАНАВЛИВАЕМ ЗАПИСЬ ПРИ ВЫКЛЮЧЕНИИ ДЕТЕКТОРА
            Camera.update_full(camera_id, {'record_enabled': 0})
            send_mqtt_command(camera_id, 'stop_detector')
            send_mqtt_command(camera_id, 'stop_recording')

    # 3. ЗАПИСЬ ON/OFF
    elif new_record != old_record:
        if new_record == 0:
            print(f"📼 Запись камеры {camera_id}: ВЫКЛ")
            send_mqtt_command(camera_id, 'stop_recording')

    # 4. ДРУГИЕ НАСТРОЙКИ
    else:
        if any(k in data for k in ['motion_threshold', 'motion_cooldown', 'motion_fps']):
            send_mqtt_command(camera_id, 'reload_config')

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

@app.route('/api/cameras/<int:camera_id>/snapshots/latest')
def latest_snapshot(camera_id):
    snap_dir = os.path.join("snapshots", str(camera_id))
    if os.path.exists(snap_dir):
        files = sorted(glob.glob(os.path.join(snap_dir, "*_alert.jpg")))
        if files:
            return send_file(files[-1], mimetype='image/jpeg')
    return "Нет скриншотов", 404

@app.route('/api/cameras/<int:camera_id>/snapshots')
def list_snapshots(camera_id):
    snap_dir = os.path.join("snapshots", str(camera_id))
    if os.path.exists(snap_dir):
        files = sorted(glob.glob(os.path.join(snap_dir, "*_alert.jpg")), reverse=True)
        return jsonify({
            'success': True,
            'snapshots': [os.path.basename(f) for f in files[:20]]
        })
    return jsonify({'success': True, 'snapshots': []})


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
    with get_db() as conn:
        row = conn.execute("SELECT * FROM recordings WHERE id = ?", (recording_id,)).fetchone()

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