"""
API для Health Monitor
"""
from flask import Blueprint, jsonify, request
from flask_login import login_required
from models.database import get_db
from models.camera import Camera
from web.utils import check_rtsp_available, mqtt_running, check_service_mqtt
import psutil
import os
import sys
import time
import json
import glob
from engine.shared.config import get_config

api_health_bp = Blueprint('api_health', __name__, url_prefix='/api/health')

last_restart_time = {}

config = get_config()
HLS_RECORDINGS_PATH = config["hls_recordings_path"]
HLS_DIR = config["streams_path"]


@api_health_bp.route('/service/restart/<service_name>', methods=['POST'])
@login_required
def restart_service(service_name):
    """Жёсткий ручной перезапуск сервиса"""
    from web.utils import restart_service_internal

    if service_name not in ('detector', 'streamer'):
        return jsonify({'success': False, 'error': 'Неизвестный сервис'}), 400

    result = restart_service_internal(service_name)
    if result['success']:
        return jsonify({
            'success': True,
            'message': f'{service_name} перезапущен (убито {result.get("killed", 0)})',
            'killed': result.get('killed', 0)
        })
    else:
        return jsonify({'success': False, 'error': result.get('error', 'Ошибка')}), 500

@api_health_bp.route('/full', methods=['GET'])
@login_required
def health_full():
    import psutil
    import time as time_module

    cpu_percent = psutil.cpu_percent(interval=0.5)
    ram = psutil.virtual_memory()
    disk = psutil.disk_usage('C:\\')
    uptime_seconds = time_module.time() - psutil.boot_time()
    uptime_str = f"{int(uptime_seconds // 86400)}д {int((uptime_seconds % 86400) // 3600)}ч {int((uptime_seconds % 3600) // 60)}м"

    recordings_path = HLS_RECORDINGS_PATH

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
                    try:
                        file_date = time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(fp)))
                        if file_date == today_str:
                            today_count += 1
                    except:
                        pass
                except:
                    pass

    streams_count = len(glob.glob(os.path.join(HLS_DIR, '*.ts'))) if os.path.exists(HLS_DIR) else 0

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

    services = {
        'web_server': {'status': 'running', 'port': 8080, 'pid': os.getpid()},
        'mqtt': {'status': 'running' if mqtt_running() else 'stopped', 'port': 1883},
        'detector': {'status': 'running' if check_service_mqtt('detector') else 'stopped', 'port': None},
        'streamer': {'status': 'running' if check_service_mqtt('streamer') else 'stopped', 'port': None}
    }

    with get_db() as conn:
        total_events = conn.execute(
            "SELECT COUNT(*) FROM events WHERE event_type LIKE '%motion_start%' OR event_type='motion_filtered'"
        ).fetchone()[0]

        ai_events = conn.execute(
            "SELECT COUNT(*) FROM events WHERE event_type LIKE '%motion_start%' AND details LIKE '%\"ai\"%'"
        ).fetchone()[0]

        filtered_events = conn.execute(
            "SELECT COUNT(*) FROM events WHERE event_type='motion_filtered'"
        ).fetchone()[0]

        today_recordings_db = conn.execute(
            "SELECT COUNT(*) FROM recordings WHERE date(start_time) = date('now','localtime')"
        ).fetchone()[0]

        last_events = conn.execute(
            "SELECT e.*, c.name as camera_name FROM events e LEFT JOIN cameras c ON e.camera_id=c.id ORDER BY e.timestamp DESC LIMIT 10"
        ).fetchall()

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
            'filtered': filtered_events,
            'filter_rate': round(ai_events / total_events * 100, 1) if total_events > 0 else 0
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

@api_health_bp.route('/reset', methods=['POST'])
@login_required
def reset_health_stats():
    """Сбрасывает статистику (события, AI-статистику)"""
    if current_user.role != 'admin':
        return jsonify({'success': False, 'error': 'Только для админов'}), 403
    try:
        with get_db() as conn:
            conn.execute("DELETE FROM events")
            conn.commit()
        return jsonify({'success': True, 'message': f'Статистика сброшена'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@api_health_bp.route('/auto-heal', methods=['POST'])
@login_required
def auto_heal_services():
    """Автоматический перезапуск упавших сервисов"""
    healed = []
    from web.utils import restart_service_internal

    for service_name in ['detector', 'streamer']:
        cooldown = 60
        if service_name in last_restart_time:
            elapsed = time.time() - last_restart_time[service_name]
            if elapsed < cooldown:
                continue

        alive = check_service_mqtt(service_name)

        if not alive:
            print(f"🔄 Авто-перезапуск {service_name}")
            result = restart_service_internal(service_name)
            if result['success']:
                healed.append(service_name)
                last_restart_time[service_name] = time.time()
                try:
                    with get_db() as conn:
                        conn.execute(
                            "INSERT INTO events (camera_id, event_type, details) VALUES (?, ?, ?)",
                            (0, "auto_heal", json.dumps({
                                "service": service_name,
                                "action": "restart",
                                "status": "pending",
                                "killed": result.get('killed', 0),
                                "timestamp": int(time.time())
                            }))
                        )
                        conn.commit()
                except:
                    pass
        else:
            if service_name in last_restart_time:
                elapsed = time.time() - last_restart_time[service_name]
                if elapsed >= cooldown:
                    print(f"✅ {service_name} отвечает после перезапуска!")
                    last_restart_time.pop(service_name, None)

    with get_db() as conn:
        today_heals = conn.execute(
            "SELECT COUNT(*) FROM events WHERE event_type='auto_heal' AND date(timestamp)=date('now','localtime')"
        ).fetchone()[0]

    return jsonify({
        'success': True,
        'healed': healed,
        'today_heals': today_heals
    })

@api_health_bp.route('', methods=['GET'])
def health():
    return jsonify({
        "status": "alive",
        "service": "web_server",
        "timestamp": int(time.time())
    })