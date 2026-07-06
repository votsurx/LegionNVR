"""
Запись по тревоге
"""
import os
import time
import glob
import shutil
import tempfile
import threading
from engine.shared.constants import *
from engine.shared.utils import ts, get_recordings_path
from engine.shared.utils import find_ffmpeg
from engine.shared.config import get_config

# Глобальные переменные
motion_recordings = {}
recording_lock = threading.Lock()

def start_continuous_recording(camera):
    """Запускает непрерывную запись (24/7 или по расписанию)"""
    cam_id = str(camera["id"])

    if not camera.get("record_enabled", False):
        return

    mode = camera.get('record_mode', 'motion_ai')
    if mode not in ('continuous_noai', 'schedule_noai', 'schedule_ai'):
        return

    retention_days = camera.get('record_retention_days', 7)

    print(f"{ts()} 📼 Запуск непрерывной записи для {camera['name']} (хранение {retention_days} дн)")

    import threading
    thread = threading.Thread(
        target=_continuous_record_loop,
        args=(camera,),
        daemon=True
    )
    thread.start()


def _continuous_record_loop(camera):
    """Фоновый цикл непрерывной записи (сегменты сразу в hls_XX/)"""
    import subprocess
    import time
    import os
    import glob as glob_module
    import shutil
    
    cam_id = str(camera["id"])
    mode = camera.get('record_mode', 'motion_ai')
    retention_days = camera.get('record_retention_days', 7)
    
    config = get_config()
    HLS_RECORDINGS_PATH = config["hls_recordings_path"]

    
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return
    
    proc = None
    last_hour = None
    current_hls_dir = None
    
    while True:
        # Проверяем расписание
        if mode in ('schedule_noai', 'schedule_ai'):
            if not _is_in_schedule(camera):
                if proc and proc.poll() is None:
                    proc.terminate()
                    proc = None
                time.sleep(30)
                continue
        
        date_str = time.strftime("%Y-%m-%d")
        hour_str = time.strftime("%H")
        current_hour_key = f"{date_str}_{hour_str}"
        
        # ✅ При смене часа — завершаем старый плейлист и создаём новую папку
        if current_hour_key != last_hour:
            # Завершаем старый плейлист (добавляем ENDLIST)
            if last_hour is not None and current_hls_dir is not None:
                _finalize_playlist(current_hls_dir, last_hour.split('_')[1])
            
            date_dir = os.path.join(HLS_RECORDINGS_PATH, f"camera_{cam_id}", date_str)
            current_hls_dir = os.path.join(date_dir, f"hls_{hour_str}")
            os.makedirs(current_hls_dir, exist_ok=True)
            
            if proc and proc.poll() is None:
                proc.terminate()
                proc = None
            
            print(f"{ts()} 📁 Новая HLS папка: {current_hls_dir}")
            last_hour = current_hour_key
        
        # ✅ Запускаем запись сразу в HLS папку
        if proc is None or proc.poll() is not None:
            cmd = [
                ffmpeg,
                "-loglevel", "error",
                "-rtsp_transport", "tcp",
                "-i", camera["rtsp_main"],
                "-c:v", "copy",
                "-an",
                "-f", "hls",
                "-hls_time", "1",
                "-hls_list_size", "0",
                "-hls_segment_filename", os.path.join(current_hls_dir, "seg_%H-%M-%S.ts"),
                "-strftime", "1",
                "-hls_flags", "omit_endlist",  # ✅ Убрали delete_segments!
                "-y", os.path.join(current_hls_dir, f"playlist_{hour_str}.m3u8")
            ]
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print(f"{ts()} 🔴 Запись {camera['name']} → {current_hls_dir}")
        
        # ✅ Принудительно обновляем плейлист из сегментов каждые 30 секунд
        if int(time.strftime("%S")) % 30 == 0:
            _update_playlist_from_segments(current_hls_dir, hour_str)
        
        # ✅ Добавляем #EXT-X-START в playlist (каждые 30 сек)
        playlist_file = os.path.join(current_hls_dir, f"playlist_{hour_str}.m3u8")
        if os.path.exists(playlist_file) and int(time.strftime("%S")) < 15:
            try:
                with open(playlist_file, 'r') as f:
                    content = f.read()
                if '#EXT-X-START' not in content:
                    content = content.replace('#EXTM3U\n', '#EXTM3U\n#EXT-X-START:TIME-OFFSET=0\n')
                    with open(playlist_file, 'w') as f:
                        f.write(content)
            except:
                pass
        
        # ✅ Очистка старых HLS папок
        if int(time.strftime("%M")) == 0:
            _cleanup_old_hls(cam_id, retention_days, HLS_RECORDINGS_PATH)
        
        time.sleep(15)


def _update_playlist_from_segments(hls_dir, hour_str):
    """Пересоздаёт плейлист из всех сегментов в папке"""
    segments = sorted(glob.glob(os.path.join(hls_dir, "seg_*.ts")))
    if not segments:
        return

    playlist = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:1", "#EXT-X-MEDIA-SEQUENCE:0"]

    for seg in segments:
        seg_name = os.path.basename(seg)
        playlist.append(f"#EXTINF:1.000,")
        playlist.append(seg_name)

    # Для live-режима НЕ добавляем ENDLIST
    playlist_file = os.path.join(hls_dir, f"playlist_{hour_str}.m3u8")
    with open(playlist_file, 'w') as f:
        f.write("\n".join(playlist))


def _finalize_playlist(hls_dir, hour_str):
    """Добавляет #EXT-X-ENDLIST в плейлист"""
    playlist_file = os.path.join(hls_dir, f"playlist_{hour_str}.m3u8")
    if not os.path.exists(playlist_file):
        return

    with open(playlist_file, 'r') as f:
        content = f.read()

    if "#EXT-X-ENDLIST" not in content:
        content += "\n#EXT-X-ENDLIST"

    with open(playlist_file, 'w') as f:
        f.write(content)


def _cleanup_old_hls(cam_id, retention_days, recordings_path):
    """Удаляет HLS-папки старше N дней"""
    import glob as glob_module
    import os
    import shutil

    cam_dir = os.path.join(recordings_path, f"camera_{cam_id}")
    if not os.path.exists(cam_dir):
        return

    cutoff_time = time.time() - (retention_days * 86400)

    for date_dir in os.listdir(cam_dir):
        date_path = os.path.join(cam_dir, date_dir)
        if not os.path.isdir(date_path):
            continue

        for hls_dir in glob_module.glob(os.path.join(date_path, "hls_*")):
            if os.path.getmtime(hls_dir) < cutoff_time:
                shutil.rmtree(hls_dir, ignore_errors=True)
                print(f"{ts()} 🗑️ Удалена старая HLS: {hls_dir}")

        try:
            if not os.listdir(date_path):
                os.rmdir(date_path)
        except:
            pass


def _cleanup_old_recordings(cam_id, retention_days, recordings_path):
    """Удаляет файлы старше N дней"""
    import glob
    import os

    cam_dir = os.path.join(recordings_path, f"camera_{cam_id}")
    if not os.path.exists(cam_dir):
        return

    cutoff_time = time.time() - (retention_days * 86400)
    deleted_count = 0

    for root, dirs, files in os.walk(cam_dir):
        for f in files:
            if '_continuous.mp4' in f or '_motion.mp4' in f:
                filepath = os.path.join(root, f)
                if os.path.getmtime(filepath) < cutoff_time:
                    try:
                        os.remove(filepath)
                        deleted_count += 1
                    except:
                        pass

    if deleted_count > 0:
        print(f"{ts()} 🗑️ Удалено {deleted_count} старых записей (>{retention_days} дн) для камеры {cam_id}")


def _should_record_continuous(camera):
    """Нужно ли писать непрерывно?"""
    mode = camera.get('record_mode', 'motion_ai')

    if mode == 'continuous_noai':
        return True
    elif mode in ('schedule_noai', 'schedule_ai'):
        return _is_in_schedule(camera)
    return False


def _is_in_schedule(camera):
    """Проверяет расписание"""
    schedule = camera.get('record_schedule', {})
    if isinstance(schedule, str):
        import json
        try:
            schedule = json.loads(schedule)
        except:
            return False

    if not schedule:
        return False

    now = time.localtime()
    day_names = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
    today = day_names[now.tm_wday]

    day_schedule = schedule.get(today, {})
    if not day_schedule.get('enabled', False):
        return False

    current_time = time.strftime('%H:%M')
    start = day_schedule.get('start', '00:00')
    end = day_schedule.get('end', '00:00')

    return start <= current_time <= end


def start_motion_recording(camera, motion_start_time=None):
    """Запускает запись по тревоге"""
    cam_id = str(camera["id"])

    with recording_lock:
        if cam_id in motion_recordings:
            print(f"{ts()} ⚠️ Запись уже создана для {cam_id}, продлеваю")
            extend_recording(cam_id)
            return

    if not camera.get("enabled", True):
        return
    if not camera.get("record_enabled", False):
        print(f"{ts()} ⏸️ Запись отключена для камеры {cam_id}")
        return

    record_pre_sec = camera.get('record_pre_sec', 5)
    record_post_sec = camera.get('record_post_sec', 10)

    if motion_start_time:
        alarm_time = motion_start_time
        print(f"{ts()} {C_GREEN}📼 Тревога! Время MOG2: {time.strftime('%H:%M:%S', time.localtime(alarm_time))}{C_RESET}")
    else:
        alarm_time = time.time()
        print(f"{ts()} {C_YELLOW}📼 Тревога! Время (текущее): {time.strftime('%H:%M:%S', time.localtime(alarm_time))}{C_RESET}")

    all_segments = []
    for seg in glob.glob(os.path.join(HLS_DIR, f"camera{cam_id}*.ts")):
        try:
            mtime = os.path.getmtime(seg)
            all_segments.append((mtime, seg))
        except:
            pass

    all_segments.sort(key=lambda x: x[0])

    start_time = alarm_time - record_pre_sec
    pre_segments = []
    for mtime, seg in all_segments:
        if start_time - 1 <= mtime <= alarm_time + 1:
            pre_segments.append(seg)

    if not pre_segments and all_segments:
        pre_segments = [s[1] for s in all_segments[-record_pre_sec:]]

    temp_dir = os.path.join(tempfile.gettempdir(), f"motion_{cam_id}_{int(time.time())}")
    os.makedirs(temp_dir, exist_ok=True)

    saved_pre = []
    for seg in pre_segments:
        seg_copy = os.path.join(temp_dir, os.path.basename(seg))
        try:
            shutil.copy2(seg, seg_copy)
            saved_pre.append(seg_copy)
        except:
            pass

    last_mtime = os.path.getmtime(pre_segments[-1]) if pre_segments else 0

    motion_recordings[cam_id] = {
        'alarm_time': alarm_time,
        'pre_sec': record_pre_sec,
        'post_sec': record_post_sec,
        'camera': camera,
        'recording': True,
        'temp_dir': temp_dir,
        'saved_pre': saved_pre,
        'saved_body': [],
        'last_mtime': last_mtime
    }

    print(f"{ts()} {C_BLUE}🔴 Запись: буфер {record_pre_sec} сек + пост {record_post_sec} сек{C_RESET}")
    print(f"{ts()} {C_GREEN}📁 Сохранено {len(saved_pre)} сегментов предзаписи{C_RESET}")

    if saved_pre:
        first_time = os.path.getmtime(saved_pre[0])
        last_time = os.path.getmtime(saved_pre[-1])
        print(f"{ts()} 📁 Предзапись: {time.strftime('%H:%M:%S', time.localtime(first_time))} → {time.strftime('%H:%M:%S', time.localtime(last_time))}")


def extend_recording(cam_id):
    """Продлевает запись"""
    if cam_id not in motion_recordings:
        return
    motion_recordings[cam_id]['alarm_time'] = time.time()
    print(f"{ts()} ⏱️ Запись продлена")


def stop_motion_recording(camera_id):
    """Завершает запись и склеивает ролик"""
    cam_id = str(camera_id)

    if cam_id not in motion_recordings:
        return

    data = motion_recordings.pop(cam_id)
    data['recording'] = False

    print(f"{ts()} ⏹️ Завершение записи для камеры {cam_id}")

    post_sec = data.get('post_sec', 10)
    print(f"{ts()} ⏱️ Постзапись {post_sec} сек...")
    time.sleep(post_sec)

    all_saved = data['saved_pre'] + data['saved_body']

    all_hls = sorted(glob.glob(os.path.join(HLS_DIR, f"camera{cam_id}*.ts")))
    post_segments = []
    if all_hls:
        for seg in all_hls[-post_sec:]:
            if seg not in all_saved:
                seg_copy = os.path.join(data['temp_dir'], os.path.basename(seg))
                try:
                    shutil.copy2(seg, seg_copy)
                    post_segments.append(seg_copy)
                except:
                    pass

    all_saved.extend(post_segments)

    if len(all_saved) < 2:
        print(f"{ts()} ❌ Слишком мало сегментов: {len(all_saved)}")
        return

    all_saved = list(set(all_saved))
    all_saved_sorted = sorted(all_saved, key=lambda f: os.path.getmtime(f))

    print(f"{ts()} 📊 Сегментов после сортировки по времени: {len(all_saved_sorted)}")
    for i, seg in enumerate(all_saved_sorted[:5]):
        seg_time = os.path.getmtime(seg)
        print(f"{ts()}   #{i}: {os.path.basename(seg)} → {time.strftime('%H:%M:%S', time.localtime(seg_time))}")

    from engine.streamer.concat import concat_with_ai_frames
    from engine.shared.utils import find_ffmpeg

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return

    now = time.strftime("%Y-%m-%d_%H-%M-%S")
    recordings_path = get_recordings_path()
    date_dir = os.path.join(recordings_path, f"camera_{cam_id}", time.strftime("%Y-%m-%d"))
    os.makedirs(date_dir, exist_ok=True)
    final_output = os.path.join(date_dir, f"{now}_motion.mp4")

    boxes_file = _find_boxes_file(cam_id, data['alarm_time'])

    if boxes_file:
        success = concat_with_ai_frames(all_saved_sorted, boxes_file, final_output, ffmpeg)
        if success:
            _save_to_db(cam_id, final_output)
            try:
                os.remove(boxes_file)
            except:
                pass
            try:
                shutil.rmtree(data['temp_dir'], ignore_errors=True)
            except:
                pass
            return

    concat_file = os.path.join(data['temp_dir'], "concat.txt")
    with open(concat_file, "w") as f:
        for seg in all_saved_sorted:
            f.write(f"file '{os.path.abspath(seg).replace(chr(92), '/')}'\n")

    cmd = ["ffmpeg", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", concat_file, "-c", "copy", "-y", final_output]
    import subprocess
    result = subprocess.run(cmd, timeout=120, capture_output=True)

    if result.returncode == 0 and os.path.exists(final_output):
        _save_to_db(cam_id, final_output)

    try:
        shutil.rmtree(data['temp_dir'], ignore_errors=True)
    except:
        pass


def save_body_segments():
    """Фоновая задача: сохраняет HLS-сегменты во время тревоги"""
    while True:
        try:
            for cam_id, data in list(motion_recordings.items()):
                if not data.get('recording'):
                    continue

                all_segments = sorted(glob.glob(os.path.join(HLS_DIR, f"camera{cam_id}*.ts")))
                if not all_segments:
                    continue

                for seg in all_segments:
                    try:
                        seg_mtime = os.path.getmtime(seg)
                    except:
                        continue

                    if seg_mtime <= data['last_mtime']:
                        continue

                    seg_copy = os.path.join(data['temp_dir'], os.path.basename(seg))
                    try:
                        shutil.copy2(seg, seg_copy)
                        data['saved_body'].append(seg_copy)
                        data['last_mtime'] = seg_mtime
                    except:
                        pass
        except:
            pass
        time.sleep(0.5)


def _find_boxes_file(cam_id, alarm_time):
    """Ищет JSON с координатами рамок"""
    import glob as glob_module
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    boxes_dir = os.path.join(base_dir, "snapshots", cam_id, "boxes")

    if not os.path.exists(boxes_dir):
        return None

    box_files = sorted(glob_module.glob(os.path.join(boxes_dir, "*_boxes.json")))
    if not box_files:
        return None

    box_files.sort(key=lambda f: os.path.getmtime(f))
    newest = box_files[-1]
    age = time.time() - os.path.getmtime(newest)

    if age < 300:
        print(f"{ts()} 📦 Выбран JSON: {os.path.basename(newest)} (возраст: {age:.1f}с)")
        return newest
    return None


def _save_to_db(cam_id, filepath):
    """Сохраняет запись в БД"""
    try:
        from models.database import get_db
        with get_db() as conn:
            conn.execute(
                "INSERT INTO recordings (camera_id, filename, start_time, type) VALUES (?, ?, datetime('now','localtime'), 'motion')",
                (int(cam_id), filepath)
            )
            conn.commit()
        print(f"{ts()} 📝 Запись добавлена в БД")
    except:
        pass