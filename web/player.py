"""
Плеер и таймлайн
"""
from flask import Blueprint, request, jsonify, Response, send_file, render_template
from flask_login import login_required
from models.camera import Camera
from models.database import get_db
from flask import send_file
import os
import time
import glob
import re
from engine.shared.config import get_config

player_bp = Blueprint('player', __name__)

config = get_config()
HLS_RECORDINGS_PATH = config["hls_recordings_path"]
HLS_DIR = config["streams_path"]

@player_bp.route('/api/cameras/<int:camera_id>/timeline')
@login_required
def camera_timeline(camera_id):
    """Возвращает данные для таймлайна"""
    date = request.args.get('date', time.strftime('%Y-%m-%d'))
    
    recordings = []
    alarms = []

    archive_dir = os.path.join(HLS_RECORDINGS_PATH, f"camera_{camera_id}", date)
    if os.path.exists(archive_dir):
        import glob as glob_module
        
        for hls_dir in sorted(glob_module.glob(os.path.join(archive_dir, "hls_*"))):
            segments = sorted(glob_module.glob(os.path.join(hls_dir, "seg_*.ts")))
            if not segments:
                continue
            
            gaps = []
            current_start = None
            current_end = None
            
            for seg in segments:
                seg_name = os.path.basename(seg)
                try:
                    time_part = seg_name.replace("seg_", "").replace(".ts", "")
                    parts = time_part.split("-")
                    h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
                    seg_sec = h * 3600 + m * 60 + s
                except:
                    continue
                
                if current_start is None:
                    current_start = seg_sec
                    current_end = seg_sec
                elif seg_sec - current_end <= 2:
                    current_end = seg_sec
                else:
                    gaps.append({'start': current_start, 'end': current_end})
                    current_start = seg_sec
                    current_end = seg_sec
            
            if current_start is not None:
                gaps.append({'start': current_start, 'end': current_end})
            
            for gap in gaps:
                recordings.append({
                    'start': f"{gap['start']//3600:02d}:{(gap['start']%3600)//60:02d}:{gap['start']%60:02d}",
                    'end': f"{gap['end']//3600:02d}:{(gap['end']%3600)//60:02d}:{gap['end']%60:02d}",
                    'type': 'continuous'
                })
        
        if not recordings:
            merged_files = sorted(glob_module.glob(os.path.join(archive_dir, "*_merged.mp4")))
            for mf in merged_files:
                basename = os.path.basename(mf)
                try:
                    time_part = basename.split('_')[1]
                    if '-' in time_part:
                        h, m = time_part.split('-')
                        start_time = f"{h}:{m}:00"
                        end_min = int(m) + 1
                        end_hour = int(h)
                        if end_min >= 60:
                            end_min = 0
                            end_hour += 1
                        end_time = f"{str(end_hour).zfill(2)}:{str(end_min).zfill(2)}:00"
                    else:
                        h = time_part
                        start_time = f"{h}:00:00"
                        end_time = f"{int(h)+1}:00:00"
                    
                    recordings.append({
                        'start': start_time,
                        'end': end_time,
                        'type': 'continuous'
                    })
                except:
                    pass
    
    try:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE camera_id=? AND event_type LIKE '%start%' AND date(timestamp)=? ORDER BY timestamp",
                (camera_id, date)
            ).fetchall()
            
            for row in rows:
                alarms.append({
                    'time': row['timestamp'].strftime('%H:%M:%S') if row['timestamp'] else '--:--:--',
                    'event_type': row['event_type']
                })
    except:
        pass
    
    return jsonify({
        'success': True,
        'recordings': recordings,
        'alarms': alarms
    })

@player_bp.route('/player/<camera_id>/<date>/playlist.m3u8')
def player_hls(camera_id, date):
    """Отдаёт готовый HLS из часовой папки с правильным смещением"""
    start_sec = request.args.get('start', 0, type=int)
    
    start_hour = start_sec // 3600
    start_offset = start_sec % 3600

    hls_dir = os.path.join(HLS_RECORDINGS_PATH, f"camera_{camera_id}", date, f"hls_{start_hour:02d}")
    playlist_file = os.path.join(hls_dir, f"playlist_{start_hour:02d}.m3u8")

    # ✅ ПРОВЕРКА: есть ли сегменты в этой папке
    segments = glob.glob(os.path.join(hls_dir, "seg_*.ts"))
    if not segments:
        # Нет сегментов — возвращаем 404 с понятным сообщением
        return jsonify({"error": "no_segments", "message": "Нет записей в это время"}), 404

    # Если плейлист есть — отдаём его
    if os.path.exists(playlist_file):
        # Парсим время первого сегмента
        if segments:
            first_seg_time = os.path.getmtime(segments[0])
            date_struct = time.strptime(date + " 00:00:00", "%Y-%m-%d %H:%M:%S")
            hls_start_sec = first_seg_time - time.mktime(date_struct)
            hls_start_sec = max(0, int(hls_start_sec))
        else:
            hls_start_sec = start_hour * 3600
        
        offset_in_hls = start_sec - hls_start_sec
        if offset_in_hls < 0:
            offset_in_hls = 0

        with open(playlist_file, 'r') as f:
            content = f.read()

        content = re.sub(r'#EXT-X-START:TIME-OFFSET=\d+', '', content)
        content = content.replace('#EXTM3U\n', f'#EXTM3U\n#EXT-X-START:TIME-OFFSET={offset_in_hls}\n')
        
        return Response(content, mimetype='application/vnd.apple.mpegurl')

    return "Плейлист не найден", 404

@player_bp.route('/player/<camera_id>/<date>/<segment>')
def player_hls_segment(camera_id, date, segment):
    """Отдаёт HLS-сегмент из часовой папки"""
    for hls_dir in sorted(glob.glob(os.path.join("recordings", f"camera_{camera_id}", date, "hls_*"))):
        seg_path = os.path.join(hls_dir, segment)
        if os.path.exists(seg_path):
            return send_file(seg_path)

    import tempfile
    pattern = os.path.join(tempfile.gettempdir(), f"player_hls_{camera_id}_{date}_*", segment)
    matches = glob.glob(pattern)
    if matches:
        return send_file(matches[0])

    return "Сегмент не найден", 404

# ============================================================
# ПРОИГРЫВАНИЕ ЗАПИСЕЙ (из архива /recordings)
# ============================================================

@player_bp.route('/recordings/<int:recording_id>/play')
@login_required
def play_recording(recording_id):
    """Воспроизводит запись по ID"""
    with get_db() as conn:
        row = conn.execute("SELECT * FROM recordings WHERE id = ?", (recording_id,)).fetchone()

    if not row:
        return "Запись не найдена", 404

    rec = dict(row)
    filepath = rec['filename']

    if not os.path.exists(filepath):
        return "Файл не найден на диске", 404

    return send_file(filepath, mimetype='video/mp4')