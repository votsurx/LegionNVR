"""
Стриминг: MJPEG и HLS
"""
import time

from flask import Blueprint, request, Response, send_file
from flask_login import login_required
from models.camera import Camera
from web.utils import find_ffmpeg
import subprocess
import os
from flask import url_for, redirect
import glob
from engine.shared.config import get_config

streams_bp = Blueprint('streams', __name__)

config = get_config()
HLS_RECORDINGS_PATH = config["hls_recordings_path"]

@streams_bp.route('/camera/<int:camera_id>/mjpeg')
def mjpeg_stream(camera_id):
    cam = Camera.get_by_id(camera_id)
    if not cam or not cam.get('enabled', True):
        return "Камера отключена", 403

    # Читаем настройки MJPEG из БД
    fps = cam.get('mjpeg_fps', 5)
    resolution = cam.get('mjpeg_resolution', '640x360')

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return "ffmpeg не найден", 500

    def generate():
        cmd = [
            ffmpeg,
            "-loglevel", "error",
            "-rtsp_transport", "tcp",
            "-re",
            "-fflags", "nobuffer",
            "-flags", "low_delay",
            "-avioflags", "direct",
            "-i", cam["rtsp_sub"] or cam["rtsp_main"],
            "-vf", f"fps={fps},scale={resolution}",
            "-f", "mjpeg",
            "-q:v", "5",
            "-flush_packets", "1",
            "-movflags", "+frag_keyframe+empty_moov",
            "-"
        ]

        proc = subprocess.Popen(
            cmd, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.DEVNULL, 
            bufsize=2**20,  # 1 МБ
        )

        try:
            buf = b''
            while True:
                chunk = proc.stdout.read(65536)
                if not chunk:
                    break
                buf += chunk
                pos = 0
                while True:
                    start = buf.find(b'\xff\xd8', pos)
                    if start == -1:
                        break
                    end = buf.find(b'\xff\xd9', start)
                    if end == -1:
                        break
                    frame = buf[start:end + 2]
                    yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
                    pos = end + 2
                if pos > 0:
                    buf = buf[pos:]
                    pos = 0
        except GeneratorExit:
            pass
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except:
                proc.kill()

    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')

@streams_bp.route('/camera/<int:camera_id>/mjpeg-full')
def mjpeg_full_stream(camera_id):
    """MJPEG-поток высокого качества для полноэкранного режима"""
    cam = Camera.get_by_id(camera_id)
    if not cam or not cam.get('enabled', True):
        return "Камера отключена", 403

    quality = request.args.get('quality', 'med')
    quality_settings = {
        'low': {'scale': '320:240', 'fps': '8', 'q': '8'},
        'med': {'scale': '640:360', 'fps': '15', 'q': '5'},
        'high': {'scale': '1280:720', 'fps': '25', 'q': '3'},
        'ultra': {'scale': '1920:1080', 'fps': '25', 'q': '2'},
    }
    settings = quality_settings.get(quality, quality_settings['med'])

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return "ffmpeg не найден", 500

    def generate():
        cmd = [
            ffmpeg,
            "-loglevel", "error",
            "-rtsp_transport", "tcp",
            "-re",
            "-fflags", "nobuffer",
            "-flags", "low_delay",
            "-avioflags", "direct",
            "-i", cam["rtsp_sub"] or cam["rtsp_main"],
            "-vf", f"fps={settings['fps']},scale={settings['scale']}",
            "-f", "mjpeg",
            "-q:v", settings['q'],
            "-flush_packets", "1",
            "-movflags", "+frag_keyframe+empty_moov",
            "-"
        ]

        proc = subprocess.Popen(
            cmd, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.DEVNULL, 
            bufsize=2**20,
        )

        try:
            buf = b''
            while True:
                chunk = proc.stdout.read(65536)
                if not chunk:
                    break
                buf += chunk
                pos = 0
                while True:
                    start = buf.find(b'\xff\xd8', pos)
                    if start == -1:
                        break
                    end = buf.find(b'\xff\xd9', start)
                    if end == -1:
                        break
                    frame = buf[start:end + 2]
                    yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
                    pos = end + 2
                if pos > 0:
                    buf = buf[pos:]
                    pos = 0
        except GeneratorExit:
            pass
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except:
                proc.kill()

    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')

@streams_bp.route('/camera/<int:camera_id>/snapshot')
def camera_snapshot(camera_id):
    """Возвращает один кадр с камеры"""
    cam = Camera.get_by_id(camera_id)
    if not cam:
        return "Камера не найдена", 404

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return "ffmpeg не найден", 500

    import tempfile
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

@streams_bp.route('/camera/<int:camera_id>/hls/playlist.m3u8')
def camera_hls_playlist(camera_id):
    cam = Camera.get_by_id(camera_id)
    if not cam or not cam.get('enabled', True):
        return "Камера отключена", 403

    # Если выбран MJPEG — редирект на MJPEG
    if cam.get('stream_protocol', 'hls') == 'mjpeg':
        return redirect(url_for('streams.mjpeg_stream', camera_id=camera_id))

    hour_str = time.strftime("%H")
    date_str = time.strftime("%Y-%m-%d")
    hls_dir = os.path.join(HLS_RECORDINGS_PATH, f"camera_{camera_id}", date_str, f"hls_{hour_str}")
    segments = glob.glob(os.path.join(hls_dir, "seg_*.ts"))
    
    if not segments:
        return redirect(url_for('streams.mjpeg_stream', camera_id=camera_id))

    playlist_path = os.path.join(hls_dir, f"playlist_{hour_str}.m3u8")
    if not os.path.exists(playlist_path):
        return "Плейлист не готов", 404

    # ✅ ЧИТАЕМ ПЛЕЙЛИСТ И ЗАМЕНЯЕМ ПУТИ НА АБСОЛЮТНЫЕ
    with open(playlist_path, 'r') as f:
        content = f.read()

    # Заменяем относительные пути на абсолютные
    base_url = f"/player/{camera_id}/{date_str}/"
    lines = content.split('\n')
    new_lines = []
    for line in lines:
        if line.endswith('.ts'):
            new_lines.append(base_url + line)
        else:
            new_lines.append(line)

    new_content = '\n'.join(new_lines)

    return Response(new_content, mimetype='application/vnd.apple.mpegurl')