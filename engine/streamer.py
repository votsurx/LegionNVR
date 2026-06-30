"""
Legion NVR - Stream Engine v2.0
Запуск: python engine/streamer.py
Подписывается на MQTT, управляет HLS-стримами и записью по тревоге
"""
import os
os.system('')  # Включает ANSI-цвета в Windows

import subprocess
import shutil
import json
import threading
import time
import glob
import sys
import signal
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import paho.mqtt.client as mqtt
from models.database import get_db

# ════════════════════════════════════════════════════════════
# КОНФИГУРАЦИЯ
# ════════════════════════════════════════════════════════════
MQTT_BROKER = "127.0.0.1"
MQTT_PORT = 1883
HLS_DIR = "streams"
HLS_TIME = 1  # ✅ ФИКСИРУЕМ 1 СЕКУНДУ — ИДЕАЛЬНО ДЛЯ ПРЕДЗАПИСИ

# ════════════════════════════════════════════════════════════
# ЦВЕТА ДЛЯ ЛОГОВ
# ════════════════════════════════════════════════════════════
C_RED = '\033[91m'
C_GREEN = '\033[92m'
C_YELLOW = '\033[93m'
C_BLUE = '\033[94m'
C_PURPLE = '\033[95m'
C_CYAN = '\033[96m'
C_GRAY = '\033[90m'
C_WHITE = '\033[97m'
C_RESET = '\033[0m'
C_BOLD = '\033[1m'

# Глобальные переменные для управления процессами
stream_processes = {}
recording_processes = {}
recording_locks = {}  # Блокировка повторного запуска записи
camera_status = {}
motion_recordings = {}
recording_lock = threading.Lock()

# ════════════════════════════════════════════════════════════
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ════════════════════════════════════════════════════════════

def ts():
    """Возвращает цветную временную метку [HH:MM:SS]"""
    return f"{C_CYAN}[{time.strftime('%H:%M:%S')}]{C_RESET}"

def _concat_with_ai_frames(selected_segments, boxes_file, final_output, ffmpeg):
    """
    Быстрая склейка + пост-обработка рамками.
    1. Склеиваем все сегменты через -c copy (быстро)
    2. Одним ffmpeg накладываем рамки по таймкодам из JSON
    """
    print(f"{ts()} 🔧 _concat_with_ai_frames ВЫЗВАНА!")

    if not boxes_file or not os.path.exists(boxes_file):
        print(f"{ts()} ⚠️ Файл AI-кадров не найден")
        return False

    try:
        with open(boxes_file, 'r') as f:
            data = json.load(f)

        ai_frames = data.get('frames', [])
        if not ai_frames:
            print(f"{ts()} ⚠️ Нет AI-кадров в JSON")
            return False

        print(f"{ts()} 🕐 AI-кадров: {len(ai_frames)}")

        temp_dir = tempfile.mkdtemp(prefix="ai_post_")
        temp_video = os.path.join(temp_dir, "temp_concat.mp4")

        # ════════════════════════════════════════════════
        # ШАГ 1: БЫСТРАЯ СКЛЕЙКА ВСЕХ СЕГМЕНТОВ (-c copy)
        # ════════════════════════════════════════════════
        print(f"{ts()} 🔧 Шаг 1: Быстрая склейка {len(selected_segments)} сегментов...")

        concat_file = os.path.join(temp_dir, "concat.txt")
        with open(concat_file, "w", encoding='utf-8') as f:
            for seg in selected_segments:
                escaped_path = os.path.abspath(seg).replace('\\', '/')
                f.write(f"file '{escaped_path}'\n")

        cmd_concat = [
            ffmpeg, "-loglevel", "error",
            "-f", "concat", "-safe", "0",
            "-i", concat_file,
            "-c", "copy",
            "-y", temp_video
        ]

        result = subprocess.run(cmd_concat, timeout=60, capture_output=True)
        if result.returncode != 0 or not os.path.exists(temp_video):
            print(f"{ts()} {C_RED}❌ Ошибка быстрой склейки{C_RESET}")
            return False

        print(f"{ts()} ✅ Склейка готова: {os.path.getsize(temp_video):,} байт")

        # ════════════════════════════════════════════════
        # ШАГ 2: ПОСТ-ОБРАБОТКА — НАКЛАДЫВАЕМ РАМКИ
        # ════════════════════════════════════════════════
        print(f"{ts()} 🔧 Шаг 2: Накладываю рамки по таймкодам...")

        # Получаем длительность видео
        probe_cmd = [ffmpeg, "-i", temp_video, "-f", "null", "-"]
        probe_result = subprocess.run(probe_cmd, capture_output=True, text=True)

        # Строим drawbox фильтры с enable (по времени)
        draw_filters = []
        first_segment_time = os.path.getmtime(selected_segments[0])

        for ai in ai_frames:
            # Время от начала видео в секундах
            offset = ai['time'] - first_segment_time
            if offset < 0:
                offset = 0

            boxes = ai.get('boxes', [])
            for box in boxes:
                cls = box.get('class', 0)
                conf = box.get('confidence', 0)
                x1, y1, x2, y2 = box['x1'], box['y1'], box['x2'], box['y2']
                w = x2 - x1
                h = y2 - y1

                # Цвет
                if cls == 0:
                    color = 'green'
                    label = f"Person {conf*100:.0f}%"
                elif cls == 2:
                    color = 'red'
                    label = f"Car {conf*100:.0f}%"
                else:
                    color = 'yellow'
                    label = f"Obj {conf*100:.0f}%"

                # Рамка с enable (показываем 1 секунду)
                draw_filters.append(
                    f"drawbox=x={x1}:y={y1}:w={w}:h={h}:color={color}:t=3:enable='between(t,{offset:.1f},{offset+1:.1f})'"
                )
                # Подпись
                draw_filters.append(
                    f"drawtext=text='{label}':x={x1+5}:y={y1-25}:fontsize=18:fontcolor=white:box=1:boxcolor=black@0.5:enable='between(t,{offset:.1f},{offset+1:.1f})'"
                )

        if draw_filters:
            filter_chain = ','.join(draw_filters)

            cmd_boxes = [
                ffmpeg, "-loglevel", "error",
                "-i", temp_video,
                "-vf", filter_chain,
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-crf", "23",
                "-c:a", "copy",
                "-y", final_output
            ]

            print(f"{ts()} 🔧 Применяю {len(draw_filters)} фильтров...")
            result = subprocess.run(cmd_boxes, timeout=300, capture_output=True)

            if result.returncode == 0 and os.path.exists(final_output):
                file_size = os.path.getsize(final_output)
                print(f"{ts()} {C_GREEN}✅ AI-ролик готов! ({file_size:,} байт){C_RESET}")
            else:
                # Fallback: копируем склеенное видео без рамок
                shutil.copy2(temp_video, final_output)
                print(f"{ts_} {C_YELLOW}⚠️ Рамки не наложились, сохраняю без рамок{C_RESET}")
        else:
            # Нет рамок — просто копируем
            shutil.copy2(temp_video, final_output)
            print(f"{ts_} ⚠️ Нет рамок для наложения")

        # Чистим
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except:
            pass

        return os.path.exists(final_output) and os.path.getsize(final_output) > 0

    except Exception as e:
        print(f"{ts()} {C_RED}❌ Ошибка AI-склейки: {e}{C_RESET}")
        import traceback
        traceback.print_exc()
        return False

def get_recordings_path():
    """Получает путь к папке записей из настроек"""
    try:
        with get_db() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key='recordings_path'").fetchone()
        if row:
            return row[0]
    except:
        pass
    return "recordings"


def find_ffmpeg():
    """Ищет ffmpeg в системе"""
    if shutil.which("ffmpeg"):
        return "ffmpeg"
    for p in ["C:/ffmpeg/bin/ffmpeg.exe", "C:/ffmpeg/ffmpeg.exe", "/usr/bin/ffmpeg"]:
        if os.path.exists(p):
            return p
    return None


def load_cameras():
    """Загружает все включённые камеры"""
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM cameras WHERE enabled=1").fetchall()
    return [dict(r) for r in rows]


# ════════════════════════════════════════════════════════════
# УПРАВЛЕНИЕ HLS-СТРИМАМИ
# ════════════════════════════════════════════════════════════

def stop_hls_stream(camera_id):
    """Останавливает HLS стрим"""
    cam_id = str(camera_id)
    if cam_id in stream_processes:
        try:
            stream_processes[cam_id].terminate()
            stream_processes[cam_id].wait(timeout=3)
        except:
            try:
                stream_processes[cam_id].kill()
            except:
                pass
        del stream_processes[cam_id]

        # Чистим сегменты
        for f in glob.glob(os.path.join(HLS_DIR, f"camera{cam_id}*.ts")):
            try:
                os.remove(f)
            except:
                pass
        m3u8 = os.path.join(HLS_DIR, f"camera{cam_id}.m3u8")
        if os.path.exists(m3u8):
            try:
                os.remove(m3u8)
            except:
                pass
        print(f"{ts()} ⏹️ HLS стрим для камеры {cam_id} остановлен")


def start_hls_stream(camera):
    cam_id = str(camera["id"])

    if not camera.get("enabled", True):
        print(f"⏸️ Камера {cam_id} отключена, стрим не запущен")
        return

    if not camera.get("stream_enabled", True):
        print(f"⏸️ Стрим для камеры {cam_id} отключен")
        return

    stop_hls_stream(cam_id)

    # Чистим старые сегменты (старый формат camera{cam_id}*.ts)
    for f in glob.glob(os.path.join(HLS_DIR, f"camera{cam_id}*.ts")):
        try:
            os.remove(f)
        except:
            pass

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        print("❌ ffmpeg не найден!")
        return

    record_pre_sec = camera.get('record_pre_sec', 5)
    hls_list_size = max(10, record_pre_sec + 5)

    # ✅ НОВЫЙ ФОРМАТ СЕГМЕНТОВ С ВРЕМЕНЕМ
    segment_pattern = os.path.join(HLS_DIR, f"camera{cam_id}_%Y%m%d_%H%M%S.ts")
    playlist_file = os.path.join(HLS_DIR, f"camera{cam_id}.m3u8")

    print(f"🎥 [{camera['name']}] Буфер HLS: {hls_list_size} сегментов по {HLS_TIME} сек = {hls_list_size} сек")

    cmd = [
        ffmpeg,
        "-loglevel", "error",
        "-rtsp_transport", "tcp",
        "-rtsp_flags", "prefer_tcp",
        "-max_delay", "5000000",
        "-analyzeduration", "10000000",
        "-probesize", "10000000",
        "-fflags", "nobuffer",
        "-flags", "low_delay",
        "-i", camera["rtsp_main"],
        "-c:v", "copy",
        "-an",
        "-hls_time", str(HLS_TIME),
        "-hls_list_size", str(hls_list_size),
        "-hls_segment_filename", segment_pattern,  # ← ИМЕНА С ДАТОЙ!
        "-hls_flags", "omit_endlist+delete_segments+split_by_time",
        "-strftime", "1",  # ← ВКЛЮЧАЕМ!
        playlist_file
    ]

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        stream_processes[cam_id] = proc
        print(f"🎥 HLS стрим '{camera['name']}' запущен (буфер {hls_list_size} сек, сегменты с временными метками)")
    except Exception as e:
        print(f"❌ Ошибка запуска стрима для {camera['name']}: {e}")


def restart_hls_stream(camera):
    """Перезапускает HLS стрим"""
    stop_hls_stream(camera["id"])
    time.sleep(0.5)
    start_hls_stream(camera)


# ════════════════════════════════════════════════════════════
# ЗАПИСЬ ПО ТРЕВОГЕ (ПЕРЕРАБОТАННАЯ ЛОГИКА)
# ════════════════════════════════════════════════════════════

def capture_buffer(cam_id, pre_sec):
    """
    Захватывает буфер предзаписи из HLS-сегментов.
    Копирует последние pre_sec сегментов во временную папку.
    Возвращает список путей к скопированным сегментам и путь к временной папке.
    """
    all_segments = sorted(glob.glob(os.path.join(HLS_DIR, f"camera{cam_id}*.ts")))

    # ✅ РОВНО pre_sec СЕГМЕНТОВ (т.к. hls_time=1)
    segments_needed = pre_sec
    if len(all_segments) >= segments_needed:
        pre_segments = all_segments[-segments_needed:]
    else:
        pre_segments = all_segments

    print(f"{ts()} 📼 Буфер: захвачено {len(pre_segments)} из {segments_needed} сегментов")

    # Копируем во временную папку (чтобы HLS не удалил сегменты)
    temp_dir = os.path.join(tempfile.gettempdir(), f"motion_buffer_{cam_id}_{int(time.time())}")
    os.makedirs(temp_dir, exist_ok=True)

    saved_segments = []
    for seg in pre_segments:
        seg_name = os.path.basename(seg)
        seg_copy = os.path.join(temp_dir, seg_name)
        try:
            shutil.copy2(seg, seg_copy)
            saved_segments.append(seg_copy)
        except Exception as e:
            print(f"{ts()} ⚠️ Ошибка копирования сегмента {seg_name}: {e}")

    return saved_segments, temp_dir


def start_motion_recording(camera):
    cam_id = str(camera["id"])

    # ✅ БЛОКИРОВКА ОТ ОДНОВРЕМЕННЫХ ВЫЗОВОВ
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

    alarm_time = time.time()

    # ✅ 1. КОПИРУЕМ ПРЕДЗАПИСЬ
    all_segments = []
    for seg in glob.glob(os.path.join(HLS_DIR, f"camera{cam_id}*.ts")):
        try:
            mtime = os.path.getmtime(seg)
            all_segments.append((mtime, seg))
        except:
            pass

    all_segments.sort(key=lambda x: x[0])

    # Берём последние record_pre_sec сегментов
    pre_segments = [s[1] for s in all_segments[-record_pre_sec:]] if len(all_segments) >= record_pre_sec else [s[1] for s in all_segments]

    # Копируем во временную папку
    temp_dir = os.path.join(tempfile.gettempdir(), f"motion_{cam_id}_{int(time.time())}")
    os.makedirs(temp_dir, exist_ok=True)

    saved_pre = []
    for seg in pre_segments:
        seg_copy = os.path.join(temp_dir, os.path.basename(seg))
        shutil.copy2(seg, seg_copy)
        saved_pre.append(seg_copy)

    # ✅ ЗАПОМИНАЕМ mtime ПОСЛЕДНЕГО СЕГМЕНТА ПРЕДЗАПИСИ
        last_mtime = 0
        if pre_segments:
            last_mtime = os.path.getmtime(pre_segments[-1])

        motion_recordings[cam_id] = {
            'alarm_time': alarm_time,
            'pre_sec': record_pre_sec,
            'post_sec': record_post_sec,
            'camera': camera,
            'recording': True,
            'temp_dir': temp_dir,
            'saved_pre': saved_pre,
            'saved_body': [],
            'last_mtime': last_mtime  # ← mtime вместо имени!
        }

    print(f"{ts()} {C_RED}📼 Тревога! Время: {time.strftime('%H:%M:%S', time.localtime(alarm_time))}{C_RESET}")
    print(f"{ts()} {C_BLUE}🔴 Запись: буфер {record_pre_sec} сек + пост {record_post_sec} сек{C_RESET}")
    print(f"{ts()} {C_GREEN}📁 Сохранено {len(saved_pre)} сегментов предзаписи{C_RESET}")

def _collect_motion_segments(cam_id):
    """Собирает HLS-сегменты по времени и склеивает в ролик (с AI-рамками если есть)"""
    if cam_id not in motion_recordings:
        return

    data = motion_recordings.pop(cam_id)
    alarm_time = data['alarm_time']
    pre_sec = data['pre_sec']
    post_sec = data['post_sec']
    camera = data['camera']

    # ✅ СОБИРАЕМ ВСЕ СЕГМЕНТЫ С ВРЕМЕНЕМ
    all_segments = []
    for seg in glob.glob(os.path.join(HLS_DIR, f"camera{cam_id}*.ts")):
        try:
            mtime = os.path.getmtime(seg)
            all_segments.append((mtime, seg))
        except:
            pass

    if not all_segments:
        print(f"{ts()} ❌ Нет сегментов для склейки")
        return

    all_segments.sort(key=lambda x: x[0])

    # ✅ ВЫЧИСЛЯЕМ ВРЕМЕННЫЕ ГРАНИЦЫ
    start_time = alarm_time - pre_sec
    end_time = time.time()

    # ✅ ОТБИРАЕМ СЕГМЕНТЫ ПО ВРЕМЕНИ
    selected_segments = []
    for mtime, seg_path in all_segments:
        if start_time - 1 <= mtime <= end_time + 1:
            selected_segments.append(seg_path)

    if len(selected_segments) < 2:
        print(f"{ts()} ❌ Слишком мало сегментов: {len(selected_segments)}")
        if len(all_segments) >= 2:
            selected_segments = [s[1] for s in all_segments[-10:]]
            print(f"{ts()} ⚠️ Беру последние {len(selected_segments)} сегментов (fallback)")
        else:
            return

    # ✅ ЛОГИРУЕМ ВРЕМЕНА
    print(f"{ts()} 📼 Предзапись с: {time.strftime('%H:%M:%S', time.localtime(start_time))}")
    print(f"{ts()} 📼 Тревога в:    {time.strftime('%H:%M:%S', time.localtime(alarm_time))}")
    print(f"{ts()} 📼 Конец записи: {time.strftime('%H:%M:%S', time.localtime(end_time))}")
    print(f"{ts()} 📼 Длительность: {len(selected_segments)} сек (пред {pre_sec}с + пост {post_sec}с)")

    # ✅ СОЗДАЁМ ФИНАЛЬНЫЙ ФАЙЛ
    now = time.strftime("%Y-%m-%d_%H-%M-%S")
    recordings_path = get_recordings_path()
    date_dir = os.path.join(recordings_path, f"camera_{cam_id}", time.strftime("%Y-%m-%d"))
    os.makedirs(date_dir, exist_ok=True)

    final_output = os.path.join(date_dir, f"{now}_motion.mp4")

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        print(f"{ts()} ❌ ffmpeg не найден!")
        return

    # ✅ ИЩЕМ ФАЙЛ С КООРДИНАТАМИ РАМОК
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    boxes_dir = os.path.join(base_dir, "snapshots", cam_id, "boxes")
    print(f"{ts()} 🔍 Ищу рамки в: {boxes_dir}")
    print(f"{ts()} 📁 Папка существует: {os.path.exists(boxes_dir)}")

    # ✅ ИЩЕМ JSON ПО ВРЕМЕНИ ТРЕВОГИ (а не последний)
    boxes_file = None
    if os.path.exists(boxes_dir):
        box_files = sorted(glob.glob(os.path.join(boxes_dir, "*_boxes.json")))
        print(f"{ts()} 📦 Найдено JSON: {len(box_files)}")
        if box_files:
            # Сортируем по времени изменения — самый новый в конце
            box_files.sort(key=lambda f: os.path.getmtime(f))
            newest = box_files[-1]
            age = time.time() - os.path.getmtime(newest)

            if age < 300:  # Не старше 300 секунд
                boxes_file = newest
                print(f"{ts()} 📦 Выбран новейший: {os.path.basename(boxes_file)} (возраст: {age:.1f}с)")
            else:
                print(f"{ts()} ⚠️ Новейший JSON старше 300 сек (возраст: {age:.1f}с)")

    # ✅ ПРОБУЕМ СКЛЕЙКУ С РАМКАМИ
    success = False
    if boxes_file:
        success = _concat_with_boxes(selected_segments, boxes_file, final_output, ffmpeg)

    # ✅ ЕСЛИ НЕ ПОЛУЧИЛОСЬ — ОБЫЧНАЯ СКЛЕЙКА
    if not success:
        # Создаём файл конкатенации
        temp_dir = tempfile.gettempdir()
        concat_file = os.path.join(temp_dir, f"concat_{cam_id}_{int(time.time())}.txt")

        with open(concat_file, "w", encoding='utf-8') as f:
            for seg in selected_segments:
                escaped_path = os.path.abspath(seg).replace('\\', '/')
                f.write(f"file '{escaped_path}'\n")

        cmd_concat = [
            ffmpeg,
            "-loglevel", "error",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_file,
            "-c", "copy",
            "-y",
            final_output
        ]

        try:
            result = subprocess.run(cmd_concat, timeout=60, capture_output=True)
            success = result.returncode == 0 and os.path.exists(final_output) and os.path.getsize(final_output) > 0
        except subprocess.TimeoutExpired:
            print(f"{ts()} ❌ Таймаут склейки (60 сек)")
        except Exception as e:
            print(f"{ts()} ❌ Ошибка склейки: {e}")

        try:
            os.remove(concat_file)
        except:
            pass

    # ✅ ПРОВЕРЯЕМ РЕЗУЛЬТАТ
    if success and os.path.exists(final_output) and os.path.getsize(final_output) > 0:
        file_size = os.path.getsize(final_output)
        print(f"{ts()} {C_GREEN}✅ Запись сохранена: {os.path.basename(final_output)} ({file_size:,} байт){C_RESET}")

        # ✅ ЛОГИРУЕМ В БД
        try:
            with get_db() as conn:
                conn.execute(
                    "INSERT INTO recordings (camera_id, filename, start_time, type) VALUES (?, ?, datetime('now','localtime'), 'motion')",
                    (int(cam_id), final_output)
                )
                conn.commit()
            print(f"{ts()} 📝 Запись добавлена в БД")
        except Exception as e:
            print(f"{ts()} ❌ Ошибка записи в БД: {e}")
    else:
        print(f"{ts()} ❌ Не удалось сохранить запись")

        # Сохраняем сегменты для отладки
        if selected_segments:
            debug_dir = os.path.join(tempfile.gettempdir(), f"debug_{cam_id}_{int(time.time())}")
            os.makedirs(debug_dir, exist_ok=True)
            for i, seg in enumerate(selected_segments[:3] + selected_segments[-3:]):
                try:
                    shutil.copy2(seg, os.path.join(debug_dir, f"seg_{i}_{os.path.basename(seg)}"))
                except:
                    pass
            print(f"{ts()} 🔍 Отладочные сегменты: {debug_dir}")

    # ✅ ЧИСТИМ ФАЙЛ КООРДИНАТ (после использования)
    if boxes_file and os.path.exists(boxes_file):
        try:
            os.remove(boxes_file)
            print(f"{ts()} 🧹 Файл координат удалён")
        except:
            pass

def extend_recording(cam_id):
    """Продлевает запись (обновляет время для захвата больше сегментов)"""
    if cam_id not in motion_recordings:
        return

    data = motion_recordings[cam_id]
    data['alarm_time'] = time.time()  # Обновляем время тревоги
    data['extended'] = True

    print(f"{ts()} {C_YELLOW}⏱️ Запись продлена{C_RESET}")


def _finalize_recording(cam_id):
    """
    Склеивает буфер предзаписи и постзапись.
    Сохраняет итоговый файл и логирует в БД.
    """
    if cam_id not in recording_processes:
        return

    data = recording_processes.pop(cam_id)

    saved_segments = data['saved_segments']
    temp_post = data['temp_post']
    final_output = data['final_output']
    temp_dir = data['temp_dir']
    camera = data['camera']

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        print("❌ ffmpeg не найден!")
        return

    # Проверяем, что есть что склеивать
    if not os.path.exists(temp_post) or os.path.getsize(temp_post) == 0:
        print(f"{ts()} ❌ Постзапись пустая или не существует: {temp_post}")
        if saved_segments and os.path.exists(final_output) == False:
            # Сохраняем хотя бы буфер
            _save_segments_as_video(saved_segments, final_output, ffmpeg)
        return

    # ✅ СОЗДАЁМ СПИСОК КОНКАТЕНАЦИИ
    concat_file = os.path.join(temp_dir, "concat.txt")
    with open(concat_file, "w") as f:
        for seg in sorted(saved_segments):
            if os.path.exists(seg):
                f.write(f"file '{os.path.abspath(seg)}'\n")
        f.write(f"file '{os.path.abspath(temp_post)}'\n")

    # ✅ СКЛЕИВАЕМ БЕЗ ПЕРЕКОДИРОВАНИЯ
    print(f"{ts()} 🔧 Склейка: {len(saved_segments)} сегментов буфера + постзапись")

    cmd_concat = [
        ffmpeg,
        "-loglevel", "error",
        "-f", "concat",
        "-safe", "0",
        "-i", concat_file,
        "-c", "copy",  # ✅ КОПИРУЕМ, НЕ ПЕРЕКОДИРУЕМ!
        "-y",
        final_output
    ]

    result = subprocess.run(cmd_concat, timeout=60, capture_output=True)

    if result.returncode == 0 and os.path.exists(final_output):
        file_size = os.path.getsize(final_output)
        print(f"{ts()} ✅ Запись сохранена: {os.path.basename(final_output)} ({file_size:,} байт)")

        # ✅ ЛОГИРУЕМ В БД
        try:
            with get_db() as conn:
                conn.execute(
                    "INSERT INTO recordings (camera_id, filename, start_time, type) VALUES (?, ?, datetime('now','localtime'), 'motion')",
                    (int(cam_id), final_output)
                )
                conn.commit()
            print(f"{ts()} 📝 Запись добавлена в БД")
        except Exception as e:
            print(f"{ts()} ❌ Ошибка записи в БД: {e}")
    else:
        print(f"{ts()} ❌ Ошибка склейки (код {result.returncode})")
        # Fallback: сохраняем только постзапись
        if os.path.exists(temp_post) and os.path.getsize(temp_post) > 0:
            try:
                os.rename(temp_post, final_output)
                print(f"{ts()} ⚠️ Сохранена только постзапись (fallback)")
            except:
                pass

    # ✅ ЧИСТИМ ВРЕМЕННЫЕ ФАЙЛЫ
    try:
        shutil.rmtree(temp_dir, ignore_errors=True)
    except:
        pass

    try:
        os.remove(concat_file)
    except:
        pass

    try:
        if os.path.exists(temp_post) and temp_post != final_output:
            os.remove(temp_post)
    except:
        pass

    print(f"{ts()} 🧹 Временные файлы очищены")


def _save_segments_as_video(segments, output, ffmpeg):
    """Сохраняет сегменты как видео (без постзаписи)"""
    if not segments:
        return

    temp_dir = os.path.dirname(segments[0]) if segments else tempfile.gettempdir()
    concat_file = os.path.join(temp_dir, "concat_segments.txt")

    with open(concat_file, "w") as f:
        for seg in sorted(segments):
            if os.path.exists(seg):
                f.write(f"file '{os.path.abspath(seg)}'\n")

    cmd = [
        ffmpeg,
        "-loglevel", "error",
        "-f", "concat",
        "-safe", "0",
        "-i", concat_file,
        "-c", "copy",
        "-y",
        output
    ]

    subprocess.run(cmd, timeout=30, capture_output=True)

    try:
        os.remove(concat_file)
    except:
        pass


def stop_motion_recording(camera_id):
    """Завершает запись и склеивает: предзапись + тело + постзапись (+ AI-рамки)"""
    cam_id = str(camera_id)

    if cam_id not in motion_recordings:
        return

    data = motion_recordings.pop(cam_id)
    data['recording'] = False

    print(f"{ts()} {C_GREEN}⏹️ Завершение записи для камеры {cam_id}{C_RESET}")

    # Ждём постзапись
    post_sec = data.get('post_sec', 10)
    print(f"{ts()} {C_CYAN}⏱️ Постзапись {post_sec} сек...{C_RESET}")
    time.sleep(post_sec)

    # ✅ ИЩЕМ ФАЙЛ С КООРДИНАТАМИ РАМОК
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    boxes_dir = os.path.join(base_dir, "snapshots", cam_id, "boxes")
    boxes_file = None

    print(f"{ts()} 🔍 Ищу рамки в: {boxes_dir}")
    if os.path.exists(boxes_dir):
        box_files = sorted(glob.glob(os.path.join(boxes_dir, "*_boxes.json")))
        print(f"{ts()} 📦 Найдено JSON: {len(box_files)}")
        if box_files:
            best_file = None
            best_diff = float('inf')
            for bf in box_files:
                bf_time = os.path.getmtime(bf)
                diff = abs(bf_time - data['alarm_time'])
                if diff < best_diff:
                    best_diff = diff
                    best_file = bf
            if best_file and best_diff < 300:
                boxes_file = best_file
                print(f"{ts()} 📦 Выбран: {os.path.basename(boxes_file)} (diff={best_diff:.1f}с)")
            else:
                print(f"{ts()} ⚠️ Нет подходящего JSON (diff={best_diff:.1f}с)")
    else:
        print(f"{ts()} ⚠️ Папка не существует")

    # ✅ СОБИРАЕМ ВСЕ СЕГМЕНТЫ
    all_saved = data['saved_pre'] + data['saved_body']

    # Добавляем постзапись
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
        print(f"{ts()} {C_RED}❌ Слишком мало сегментов: {len(all_saved)}{C_RESET}")
        return

    # ✅ СОРТИРОВКА (время в имени = правильный порядок)
    all_saved = list(set(all_saved))  # Убираем дубликаты
    all_saved_sorted = sorted(all_saved)  # Сортируем по имени (время в имени!)

    # Логируем первые 3 и последние 3
    print(f"{ts()} 📊 Сегментов после сортировки: {len(all_saved_sorted)}")
    for i, seg in enumerate(all_saved_sorted[:3]):
        seg_time = os.path.getmtime(seg)
        print(f"{ts()}   #{i}: {os.path.basename(seg)} → {time.strftime('%H:%M:%S', time.localtime(seg_time))}")
    if len(all_saved_sorted) > 6:
        print(f"{ts()}   ...")
        for i, seg in enumerate(all_saved_sorted[-3:]):
            seg_time = os.path.getmtime(seg)
            print(f"{ts()}   #{len(all_saved_sorted)-3+i}: {os.path.basename(seg)} → {time.strftime('%H:%M:%S', time.localtime(seg_time))}")

    # ✅ СОЗДАЁМ ФИНАЛЬНЫЙ ФАЙЛ
    now = time.strftime("%Y-%m-%d_%H-%M-%S")
    recordings_path = get_recordings_path()
    date_dir = os.path.join(recordings_path, f"camera_{cam_id}", time.strftime("%Y-%m-%d"))
    os.makedirs(date_dir, exist_ok=True)
    final_output = os.path.join(date_dir, f"{now}_motion.mp4")

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return

    # ✅ ПРОБУЕМ СКЛЕЙКУ С РАМКАМИ
    if boxes_file:
        success = _concat_with_ai_frames(all_saved_sorted, boxes_file, final_output, ffmpeg)
        if success:
            file_size = os.path.getsize(final_output)
            duration = int(os.path.getmtime(all_saved_sorted[-1]) - os.path.getmtime(all_saved_sorted[0])) + 1
            print(f"{ts()} {C_GREEN}✅ AI-ролик сохранён: {os.path.basename(final_output)} ({file_size:,} байт, ~{duration} сек){C_RESET}")

            # БД
            try:
                with get_db() as conn:
                    conn.execute(
                        "INSERT INTO recordings (camera_id, filename, start_time, type) VALUES (?, ?, datetime('now','localtime'), 'motion')",
                        (int(cam_id), final_output)
                    )
                    conn.commit()
            except:
                pass

            # Чистим JSON
            try:
                if os.path.exists(boxes_file):
                    os.remove(boxes_file)
            except:
                pass
            # Чистим временную папку
            try:
                shutil.rmtree(data['temp_dir'], ignore_errors=True)
            except:
                pass
            return  # ВЫХОДИМ — рамки готовы!

    # ✅ ОБЫЧНАЯ СКЛЕЙКА (без рамок или если рамки не получились)
    concat_file = os.path.join(data['temp_dir'], "concat.txt")
    with open(concat_file, "w") as f:
        for seg in all_saved_sorted:
            escaped_path = os.path.abspath(seg).replace('\\', '/')
            f.write(f"file '{escaped_path}'\n")

    cmd = [
        ffmpeg, "-loglevel", "error",
        "-f", "concat", "-safe", "0",
        "-i", concat_file,
        "-c", "copy", "-y",
        final_output
    ]

    result = subprocess.run(cmd, timeout=120, capture_output=True)

    if result.returncode == 0 and os.path.exists(final_output):
        file_size = os.path.getsize(final_output)
        first_time = os.path.getmtime(all_saved_sorted[0])
        last_time = os.path.getmtime(all_saved_sorted[-1])
        duration = int(last_time - first_time) + 1
        print(f"{ts()} {C_GREEN}✅ Запись сохранена: {os.path.basename(final_output)} ({file_size:,} байт, ~{duration} сек){C_RESET}")

        # БД
        try:
            with get_db() as conn:
                conn.execute(
                    "INSERT INTO recordings (camera_id, filename, start_time, type) VALUES (?, ?, datetime('now','localtime'), 'motion')",
                    (int(cam_id), final_output)
                )
                conn.commit()
        except:
            pass
    else:
        print(f"{ts()} {C_RED}❌ Ошибка склейки{C_RESET}")

    # Чистим
    try:
        if os.path.exists(boxes_file):
            os.remove(boxes_file)
    except:
        pass
    try:
        shutil.rmtree(data['temp_dir'], ignore_errors=True)
    except:
        pass

def save_body_segments():
    while True:
        try:
            for cam_id, data in list(motion_recordings.items()):
                if not data.get('recording'):
                    continue

                all_segments = sorted(glob.glob(os.path.join(HLS_DIR, f"camera{cam_id}*.ts")))

                if not all_segments:
                    continue

                # ✅ СОХРАНЯЕМ ТОЛЬКО СЕГМЕНТЫ НОВЕЕ last_mtime
                for seg in all_segments:
                    try:
                        seg_mtime = os.path.getmtime(seg)
                    except:
                        continue

                    # ПРОПУСКАЕМ СТАРЫЕ (включая предзапись!)
                    if seg_mtime <= data['last_mtime']:
                        continue

                    # Копируем
                    seg_copy = os.path.join(data['temp_dir'], os.path.basename(seg))
                    try:
                        shutil.copy2(seg, seg_copy)
                        data['saved_body'].append(seg_copy)
                        data['last_mtime'] = seg_mtime  # Обновляем
                    except:
                        pass
        except:
            pass

        time.sleep(0.5)


# ════════════════════════════════════════════════════════════
# MQTT-ОБРАБОТЧИКИ
# ════════════════════════════════════════════════════════════

def on_motion(client, userdata, msg):
    """Callback при получении MQTT-сообщения о движении"""
    try:
        data = json.loads(msg.payload.decode())
        cam_id = str(data.get("camera_id"))

        if data.get("event") == "motion_start":
            # ✅ ЗАЩИТА ОТ ДВОЙНОГО ВЫЗОВА
            if cam_id in motion_recordings:
                print(f"{ts()} {C_YELLOW}📡 [MOTION] Запись уже активна для камеры {cam_id}, продлеваю{C_RESET}")
                extend_recording(cam_id)
            else:
                print(f"{ts()} {C_BLUE}📡 [MOTION] Старт записи для камеры {cam_id}{C_RESET}")
                with get_db() as conn:
                    cam = conn.execute("SELECT * FROM cameras WHERE id=?", (cam_id,)).fetchone()
                if cam:
                    start_motion_recording(dict(cam))
    except Exception as e:
        print(f"{ts()} ⚠️ Ошибка обработки motion: {e}")


def on_cmd(client, userdata, msg):
    """Обработчик команд для стримера"""
    try:
        data = json.loads(msg.payload.decode())
        action = data.get("action")
        cam_id = data.get("camera_id")

        # ✅ СТАРТ ЗАПИСИ (с защитой от двойного вызова)
        if action == "start_recording" and cam_id:
            if cam_id in motion_recordings:
                # Запись уже активна — продлеваем вместо создания новой
                print(f"{ts()} {C_YELLOW}📡 [CMD] Запись уже активна для камеры {cam_id}, продлеваю{C_RESET}")
                extend_recording(cam_id)
            else:
                print(f"{ts()} {C_BLUE}📡 [CMD] Старт записи для камеры {cam_id}{C_RESET}")
                with get_db() as conn:
                    cam = conn.execute("SELECT * FROM cameras WHERE id=?", (cam_id,)).fetchone()
                if cam:
                    start_motion_recording(dict(cam))

        # ✅ ПРОДЛЕНИЕ ЗАПИСИ
        elif action == "extend_recording" and cam_id:
            print(f"{ts()} 📡 [CMD] Продление записи для камеры {cam_id}")
            extend_recording(cam_id)

        # ✅ ОСТАНОВКА ЗАПИСИ
        elif action == "stop_recording" and cam_id:
            print(f"{ts()} 📡 [CMD] Остановка записи для камеры {cam_id}")
            stop_motion_recording(cam_id)

        # ✅ ЗАПУСК СТРИМА
        elif action == "start_stream" and cam_id:
            print(f"{ts()} ▶️ [CMD] Запуск стрима для камеры {cam_id}")
            with get_db() as conn:
                cam = conn.execute("SELECT * FROM cameras WHERE id=?", (cam_id,)).fetchone()
            if cam:
                start_hls_stream(dict(cam))

        # ✅ ОСТАНОВКА СТРИМА
        elif action == "stop_stream" and cam_id:
            print(f"{ts()} ⏹️ [CMD] Остановка стрима для камеры {cam_id}")
            stop_hls_stream(cam_id)

        # ✅ ОСТАНОВКА ДЕТЕКТОРА → останавливаем запись
        elif action == "stop_detector" and cam_id:
            print(f"{ts()} ⏹️ [CMD] Остановка детектора для камеры {cam_id} → стоп записи")
            stop_motion_recording(cam_id)

        # ✅ ПЕРЕЗАГРУЗКА КОНФИГА
        elif action == "reload_config" and cam_id:
            print(f"{ts()} 📡 [CMD] Перезагрузка конфига для камеры {cam_id}")
            with get_db() as conn:
                cam = conn.execute("SELECT * FROM cameras WHERE id=?", (cam_id,)).fetchone()

            if cam:
                cam_dict = dict(cam)
                if cam_dict.get("enabled") and cam_dict.get("stream_enabled", True):
                    restart_hls_stream(cam_dict)
                else:
                    stop_hls_stream(cam_id)

        # ✅ ПЕРЕЗАГРУЗКА ВСЕХ
        elif action == "reload_all":
            print("📡 [CMD] Перезагрузка ВСЕХ стримов")
            cameras = load_cameras()
            for cam in cameras:
                if cam.get("enabled") and cam.get("stream_enabled", True):
                    start_hls_stream(cam)
            print(f"{ts()} 🔄 Перезапущено стримов: {len(stream_processes)}")

        elif action == "ping":
            # Ответ на пинг от Health Monitor
            client.publish("spartan/streamer/pong", json.dumps({
                "status": "alive",
                "streams": len(stream_processes),
                "recordings": len(recording_processes),
                "timestamp": int(time.time())
            }))

    except Exception as e:
        print(f"{ts()} ⚠️ [CMD] Ошибка: {e}")


def on_motion_and_cmd(client, userdata, msg):
    """Обрабатывает и motion, и cmd"""
    if msg.topic.endswith("/motion"):
        on_motion(client, userdata, msg)
    else:
        on_cmd(client, userdata, msg)


# ════════════════════════════════════════════════════════════
# ОЧИСТКА СТАРЫХ СЕГМЕНТОВ
# ════════════════════════════════════════════════════════════

def cleanup_old_segments():
    """Периодически чистит осиротевшие HLS-сегменты"""
    while True:
        try:
            now = time.time()
            for f in glob.glob(os.path.join(HLS_DIR, "*.ts")):
                if os.path.getmtime(f) < now - 60:  # Старше 60 секунд
                    try:
                        os.remove(f)
                    except:
                        pass
        except:
            pass
        time.sleep(30)


# ════════════════════════════════════════════════════════════
# ГЛАВНАЯ ФУНКЦИЯ
# ════════════════════════════════════════════════════════════

def signal_handler(sig, frame):
    """Обработчик Ctrl+C"""
    print("\n⏹️ Завершение...")
    for proc in stream_processes.values():
        try:
            proc.terminate()
        except:
            pass
    for data in recording_processes.values():
        try:
            data['proc'].terminate()
        except:
            pass
    sys.exit(0)


def main():
    print("=" * 50)
    print("  🎥  LEGION NVR - STREAM ENGINE v2.0")
    print("=" * 50)
    print(f"{ts()}   📡 MQTT: {MQTT_BROKER}:{MQTT_PORT}")
    print(f"{ts()}   🎬 HLS сегменты: {HLS_TIME} сек")
    print()

    signal.signal(signal.SIGINT, signal_handler)

    os.makedirs(HLS_DIR, exist_ok=True)

    # MQTT
    client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    client.connect(MQTT_BROKER, MQTT_PORT, 60)
    client.subscribe("spartan/+/motion")
    client.subscribe("spartan/+/cmd")
    client.subscribe("spartan/streams/reload")
    client.on_message = on_motion_and_cmd
    client.loop_start()

    # Запускаем HLS для всех камер
    cameras = load_cameras()
    print(f"{ts()} [Cameras] {len(cameras)}")
    for cam in cameras:
        if cam.get("enabled") and cam.get("stream_enabled", True):
            start_hls_stream(cam)
    threading.Thread(target=save_body_segments, daemon=True).start()
    # Запускаем очистку старых сегментов
    # threading.Thread(target=cleanup_old_segments, daemon=True).start()

    print(f"{ts()} [HLS] Streams: {len(stream_processes)}")
    print(f"{ts()} [Subscriptions] spartan/+/motion, spartan/+/cmd, spartan/streams/reload")
    print("[Running] Working... (Ctrl+C to exit)")
    print()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        signal_handler(None, None)


if __name__ == '__main__':
    main()
