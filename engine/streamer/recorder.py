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
from engine.shared.constants import HLS_DIR
from engine.shared.utils import ts, get_recordings_path
from engine.shared.utils import find_ffmpeg

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

    # Запускаем в фоновом потоке
    import threading
    thread = threading.Thread(
        target=_continuous_record_loop,
        args=(camera,),
        daemon=True
    )
    thread.start()


def _continuous_record_loop(camera):
    """Фоновый цикл непрерывной записи"""
    import subprocess
    import time
    import os
    import shutil

    cam_id = str(camera["id"])
    mode = camera.get('record_mode', 'motion_ai')
    retention_days = camera.get('record_retention_days', 7)
    recordings_path = "recordings"

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return

    proc = None
    current_date = time.strftime("%Y-%m-%d")
    current_hour = time.strftime("%H")

    while True:
        # Проверяем расписание
        if mode in ('schedule_noai', 'schedule_ai'):
            if not _is_in_schedule(camera):
                # Не время для записи — останавливаем ffmpeg
                if proc and proc.poll() is None:
                    proc.terminate()
                    proc = None
                    print(f"{ts()} ⏸️ Пауза непрерывной записи {camera['name']} (нерасписание)")
                time.sleep(30)
                continue

        # Время для записи — запускаем если не запущен
        if proc is None or proc.poll() is not None:
            now = time.strftime("%Y-%m-%d_%H-%M-%S")
            date_dir = os.path.join(recordings_path, f"camera_{cam_id}", time.strftime("%Y-%m-%d"))
            os.makedirs(date_dir, exist_ok=True)

            output_file = os.path.join(date_dir, f"{now}_continuous.mp4")

            # Пишем сегментами по 5 минут (300 сек)
            cmd = [
                ffmpeg,
                "-loglevel", "error",
                "-rtsp_transport", "tcp",
                "-i", camera["rtsp_main"],
                "-c:v", "copy",
                "-an",
                "-f", "segment",
                "-segment_time", "300",  # 5 минут
                "-reset_timestamps", "1",
                "-strftime", "1",
                os.path.join(date_dir, f"%Y-%m-%d_%H-%M-%S_continuous.mp4"),
                "-y"
            ]

            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print(f"{ts()} 🔴 Непрерывная запись {camera['name']} → {date_dir}")

        # Очистка старых файлов (каждые 60 минут)
        if int(time.strftime("%M")) == 0:  # В начале часа
            _cleanup_old_recordings(cam_id, retention_days, recordings_path)

        time.sleep(30)  # Проверяем каждые 30 секунд


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
        return True  # Всегда
    elif mode in ('schedule_noai', 'schedule_ai'):
        return _is_in_schedule(camera)  # По расписанию
    return False  # motion_ai — только по тревоге

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

    # ✅ ИСПОЛЬЗУЕМ РЕАЛЬНОЕ ВРЕМЯ НАЧАЛА ДВИЖЕНИЯ (от MOG2!)
    if motion_start_time:
        alarm_time = motion_start_time
        print(f"{ts()} {C_GREEN}📼 Тревога! Время MOG2: {time.strftime('%H:%M:%S', time.localtime(alarm_time))}{C_RESET}")
    else:
        alarm_time = time.time()
        print(f"{ts()} {C_YELLOW}📼 Тревога! Время (текущее): {time.strftime('%H:%M:%S', time.localtime(alarm_time))}{C_RESET}")

    # ✅ КОПИРУЕМ ПРЕДЗАПИСЬ (сегменты от alarm_time - pre_sec до alarm_time)
    all_segments = []
    for seg in glob.glob(os.path.join(HLS_DIR, f"camera{cam_id}*.ts")):
        try:
            mtime = os.path.getmtime(seg)
            all_segments.append((mtime, seg))
        except:
            pass

    all_segments.sort(key=lambda x: x[0])

    # Берём сегменты в диапазоне [alarm_time - pre_sec - 1, alarm_time + 1]
    start_time = alarm_time - record_pre_sec
    pre_segments = []
    for mtime, seg in all_segments:
        if start_time - 1 <= mtime <= alarm_time + 1:
            pre_segments.append(seg)

    if not pre_segments and all_segments:
        # Fallback: берём последние pre_sec сегментов
        pre_segments = [s[1] for s in all_segments[-record_pre_sec:]]

    # Копируем во временную папку
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

    # Запоминаем mtime последнего сегмента предзаписи
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

    # Логируем время первого и последнего сегмента
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

    # Собираем все сегменты
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

    # Сортировка
    # Убираем дубликаты
    all_saved = list(set(all_saved))

    # ✅ СОРТИРУЕМ ПО РЕАЛЬНОМУ ВРЕМЕНИ СОЗДАНИЯ ФАЙЛА
    all_saved_sorted = sorted(all_saved, key=lambda f: os.path.getmtime(f))

    # Логируем для проверки
    print(f"{ts()} 📊 Сегментов после сортировки по времени: {len(all_saved_sorted)}")
    for i, seg in enumerate(all_saved_sorted[:5]):
        seg_time = os.path.getmtime(seg)
        print(f"{ts()}   #{i}: {os.path.basename(seg)} → {time.strftime('%H:%M:%S', time.localtime(seg_time))}")
    for i, seg in enumerate(all_saved_sorted[:3]):
        seg_time = os.path.getmtime(seg)
        print(f"{ts()}   #{i}: {os.path.basename(seg)} → {time.strftime('%H:%M:%S', time.localtime(seg_time))}")
    if len(all_saved_sorted) > 6:
        print(f"{ts()}   ...")
        for i, seg in enumerate(all_saved_sorted[-3:]):
            seg_time = os.path.getmtime(seg)
            print(f"{ts()}   #{len(all_saved_sorted)-3+i}: {os.path.basename(seg)} → {time.strftime('%H:%M:%S', time.localtime(seg_time))}")

    # Склеиваем
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

    # Ищем JSON с рамками
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

    # Обычная склейка
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