"""
Legion NVR - Stream Engine v2.0
Запуск: python engine/streamer.py
Подписывается на MQTT, управляет HLS-стримами и записью по тревоге
"""

import subprocess
import os
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

# Глобальные переменные для управления процессами
stream_processes = {}
recording_processes = {}
recording_locks = {}  # Блокировка повторного запуска записи
camera_status = {}
motion_recordings = {}

# ════════════════════════════════════════════════════════════
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ════════════════════════════════════════════════════════════

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
        print(f"⏹️ HLS стрим для камеры {cam_id} остановлен")


def start_hls_stream(camera):
    """
    Запускает HLS стрим с буфером для предзаписи.
    hls_time = 1 сек (идеально для точного буфера).
    hls_list_size = record_pre_sec + 3 (буфер + запас).
    """
    cam_id = str(camera["id"])

    if not camera.get("enabled", True):
        print(f"⏸️ Камера {cam_id} отключена, стрим не запущен")
        return

    if not camera.get("stream_enabled", True):
        print(f"⏸️ Стрим для камеры {cam_id} отключен")
        return

    # Останавливаем старый стрим
    stop_hls_stream(cam_id)

    # Чистим старые сегменты
    for f in glob.glob(os.path.join(HLS_DIR, f"camera{cam_id}*.ts")):
        try:
            os.remove(f)
        except:
            pass

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        print("❌ ffmpeg не найден!")
        return

    # ✅ РАССЧИТЫВАЕМ БУФЕР
    record_pre_sec = camera.get('record_pre_sec', 5)
    hls_list_size = record_pre_sec + 3  # Буфер + запас (3 сегмента на лаги)

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
        "-hls_time", str(HLS_TIME),               # 1 секунда
        "-hls_list_size", str(hls_list_size),      # Буфер для предзаписи
        "-hls_flags", "omit_endlist+delete_segments",
        os.path.join(HLS_DIR, f"camera{cam_id}.m3u8")
    ]

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        stream_processes[cam_id] = proc
        print(f"🎥 HLS стрим '{camera['name']}' запущен (буфер {hls_list_size} сек)")
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

    print(f"📼 Буфер: захвачено {len(pre_segments)} из {segments_needed} сегментов")

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
            print(f"⚠️ Ошибка копирования сегмента {seg_name}: {e}")

    return saved_segments, temp_dir


def start_motion_recording(camera):
    """Запускает запись по тревоге (собирает HLS-сегменты по времени)"""
    cam_id = str(camera["id"])

    if not camera.get("enabled", True):
        return

    if not camera.get("record_enabled", False):
        print(f"⏸️ Запись отключена для камеры {cam_id}")
        return

    record_pre_sec = camera.get('record_pre_sec', 5)
    record_post_sec = camera.get('record_post_sec', 10)

    # ✅ ЗАПОМИНАЕМ ВРЕМЯ СТАРТА ТРЕВОГИ
    alarm_time = time.time()

    # ✅ НАХОДИМ ВСЕ СЕГМЕНТЫ С ВРЕМЕНЕМ
    all_segments = []
    for seg in glob.glob(os.path.join(HLS_DIR, f"camera{cam_id}*.ts")):
        try:
            mtime = os.path.getmtime(seg)
            all_segments.append((mtime, seg))
        except:
            pass

    if not all_segments:
        print(f"❌ Нет HLS-сегментов для камеры {cam_id}")
        return

    all_segments.sort(key=lambda x: x[0])  # Сортируем по времени

    print(f"📼 Тревога! Время: {time.strftime('%H:%M:%S', time.localtime(alarm_time))}")
    print(f"🔴 Запись: буфер {record_pre_sec} сек + пост {record_post_sec} сек")

    # ✅ СОХРАНЯЕМ ИНФОРМАЦИЮ О ЗАПИСИ
    motion_recordings[cam_id] = {
        'start_time': time.time(),
        'alarm_time': alarm_time,  # ← ВРЕМЯ ТРЕВОГИ (вместо имени сегмента)
        'pre_sec': record_pre_sec,
        'post_sec': record_post_sec,
        'camera': camera
    }

    # ✅ ЗАПУСКАЕМ ТАЙМЕР НА СБОР СЕГМЕНТОВ
    timer = threading.Timer(record_post_sec + 2, _collect_motion_segments, args=[cam_id])
    timer.daemon = True
    motion_recordings[cam_id]['timer'] = timer
    timer.start()

    print(f"⏱️ Сбор сегментов через {record_post_sec} сек...")

def _collect_motion_segments(cam_id):
    """Собирает HLS-сегменты по времени и склеивает в ролик"""
    if cam_id not in motion_recordings:
        return

    data = motion_recordings.pop(cam_id)
    alarm_time = data['alarm_time']  # ← ВРЕМЯ ТРЕВОГИ
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
        print(f"❌ Нет сегментов для склейки")
        return

    all_segments.sort(key=lambda x: x[0])  # Сортируем по времени

    # ✅ ВЫЧИСЛЯЕМ ВРЕМЕННЫЕ ГРАНИЦЫ
    start_time = alarm_time - pre_sec     # Начало предзаписи
    end_time = time.time()                 # Текущее время (конец постзаписи)

    # ✅ ОТБИРАЕМ СЕГМЕНТЫ ПО ВРЕМЕНИ
    selected_segments = []
    for mtime, seg_path in all_segments:
        if start_time - 1 <= mtime <= end_time + 1:  # +-1 сек на погрешность
            selected_segments.append(seg_path)

    if len(selected_segments) < 2:
        print(f"❌ Слишком мало сегментов: {len(selected_segments)}")
        # Пробуем взять последние 10 как fallback
        if len(all_segments) >= 2:
            selected_segments = [s[1] for s in all_segments[-10:]]
            print(f"⚠️ Беру последние {len(selected_segments)} сегментов (fallback)")
        else:
            return

    # ✅ ЛОГИРУЕМ ВРЕМЕНА
    print(f"📼 Предзапись с: {time.strftime('%H:%M:%S', time.localtime(start_time))}")
    print(f"📼 Тревога в:    {time.strftime('%H:%M:%S', time.localtime(alarm_time))}")
    print(f"📼 Конец записи: {time.strftime('%H:%M:%S', time.localtime(end_time))}")
    print(f"📼 Длительность: {len(selected_segments)} сек (пред {pre_sec}с + пост {post_sec}с)")

    # ✅ СОЗДАЁМ ФИНАЛЬНЫЙ ФАЙЛ
    now = time.strftime("%Y-%m-%d_%H-%M-%S")
    recordings_path = get_recordings_path()
    date_dir = os.path.join(recordings_path, f"camera_{cam_id}", time.strftime("%Y-%m-%d"))
    os.makedirs(date_dir, exist_ok=True)

    final_output = os.path.join(date_dir, f"{now}_motion.mp4")

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        print("❌ ffmpeg не найден!")
        return

    # ✅ СОЗДАЁМ ФАЙЛ КОНКАТЕНАЦИИ
    temp_dir = tempfile.gettempdir()
    concat_file = os.path.join(temp_dir, f"concat_{cam_id}_{int(time.time())}.txt")

    with open(concat_file, "w", encoding='utf-8') as f:
        for seg in selected_segments:
            # Экранируем путь для ffmpeg
            escaped_path = os.path.abspath(seg).replace('\\', '/')
            f.write(f"file '{escaped_path}'\n")

    # ✅ СКЛЕИВАЕМ БЕЗ ПЕРЕКОДИРОВАНИЯ
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

        if result.returncode == 0 and os.path.exists(final_output) and os.path.getsize(final_output) > 0:
            file_size = os.path.getsize(final_output)
            print(f"✅ Запись сохранена: {os.path.basename(final_output)} ({file_size:,} байт)")

            # ✅ ЛОГИРУЕМ В БД
            try:
                with get_db() as conn:
                    conn.execute(
                        "INSERT INTO recordings (camera_id, filename, start_time, type) VALUES (?, ?, datetime('now','localtime'), 'motion')",
                        (int(cam_id), final_output)
                    )
                    conn.commit()
                print(f"📝 Запись добавлена в БД")
            except Exception as e:
                print(f"❌ Ошибка записи в БД: {e}")
        else:
            error_msg = result.stderr.decode('utf-8', errors='ignore') if result.stderr else 'Неизвестная ошибка'
            print(f"❌ Ошибка склейки (код {result.returncode}): {error_msg[:200]}")

            # Fallback: копируем первый и последний сегмент для диагностики
            if selected_segments:
                debug_dir = os.path.join(temp_dir, f"debug_{cam_id}")
                os.makedirs(debug_dir, exist_ok=True)
                for i, seg in enumerate(selected_segments[:3] + selected_segments[-3:]):
                    shutil.copy2(seg, os.path.join(debug_dir, f"seg_{i}_{os.path.basename(seg)}"))
                print(f"🔍 Отладочные сегменты сохранены в {debug_dir}")

    except subprocess.TimeoutExpired:
        print(f"❌ Таймаут склейки (60 сек)")
    except Exception as e:
        print(f"❌ Ошибка склейки: {e}")

    # ✅ ЧИСТИМ ВРЕМЕННЫЕ ФАЙЛЫ
    try:
        os.remove(concat_file)
    except:
        pass

def extend_recording(cam_id):
    """Продлевает запись (сбрасывает таймер сбора)"""
    if cam_id not in motion_recordings:
        return

    data = motion_recordings[cam_id]

    # ✅ ОТМЕНЯЕМ СТАРЫЙ ТАЙМЕР
    if 'timer' in data:
        data['timer'].cancel()

    # ✅ ЗАПУСКАЕМ НОВЫЙ ТАЙМЕР
    post_sec = data['post_sec']
    timer = threading.Timer(post_sec + 2, _collect_motion_segments, args=[cam_id])
    timer.daemon = True
    data['timer'] = timer
    timer.start()

    print(f"⏱️ Запись продлена ещё на {post_sec} сек")


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
        print(f"❌ Постзапись пустая или не существует: {temp_post}")
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
    print(f"🔧 Склейка: {len(saved_segments)} сегментов буфера + постзапись")

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
        print(f"✅ Запись сохранена: {os.path.basename(final_output)} ({file_size:,} байт)")

        # ✅ ЛОГИРУЕМ В БД
        try:
            with get_db() as conn:
                conn.execute(
                    "INSERT INTO recordings (camera_id, filename, start_time, type) VALUES (?, ?, datetime('now','localtime'), 'motion')",
                    (int(cam_id), final_output)
                )
                conn.commit()
            print(f"📝 Запись добавлена в БД")
        except Exception as e:
            print(f"❌ Ошибка записи в БД: {e}")
    else:
        print(f"❌ Ошибка склейки (код {result.returncode})")
        # Fallback: сохраняем только постзапись
        if os.path.exists(temp_post) and os.path.getsize(temp_post) > 0:
            try:
                os.rename(temp_post, final_output)
                print(f"⚠️ Сохранена только постзапись (fallback)")
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

    print(f"🧹 Временные файлы очищены")


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
    """Принудительно завершает запись"""
    cam_id = str(camera_id)

    if cam_id not in motion_recordings:
        return

    # ✅ ОТМЕНЯЕМ ТАЙМЕР И СРАЗУ СКЛЕИВАЕМ
    data = motion_recordings[cam_id]
    if 'timer' in data:
        data['timer'].cancel()

    print(f"⏹️ Принудительное завершение записи для камеры {cam_id}")
    _collect_motion_segments(cam_id)


# ════════════════════════════════════════════════════════════
# MQTT-ОБРАБОТЧИКИ
# ════════════════════════════════════════════════════════════

def on_motion(client, userdata, msg):
    """Callback при получении MQTT-сообщения о движении"""
    try:
        data = json.loads(msg.payload.decode())
        if data.get("event") == "motion_start":
            with get_db() as conn:
                cam = conn.execute("SELECT * FROM cameras WHERE id=?", (data["camera_id"],)).fetchone()
            if cam:
                start_motion_recording(dict(cam))
    except Exception as e:
        print(f"⚠️ Ошибка обработки motion: {e}")


def on_cmd(client, userdata, msg):
    """Обработчик команд для стримера"""
    try:
        data = json.loads(msg.payload.decode())
        action = data.get("action")
        cam_id = data.get("camera_id")

        # ✅ СТАРТ ЗАПИСИ
        if action == "start_recording" and cam_id:
            print(f"📡 [CMD] Старт записи для камеры {cam_id}")
            with get_db() as conn:
                cam = conn.execute("SELECT * FROM cameras WHERE id=?", (cam_id,)).fetchone()
            if cam:
                start_motion_recording(dict(cam))

        # ✅ ПРОДЛЕНИЕ ЗАПИСИ
        elif action == "extend_recording" and cam_id:
            print(f"📡 [CMD] Продление записи для камеры {cam_id}")
            extend_recording(cam_id)

        # ✅ ОСТАНОВКА ЗАПИСИ
        elif action == "stop_recording" and cam_id:
            print(f"📡 [CMD] Остановка записи для камеры {cam_id}")
            stop_motion_recording(cam_id)

        # ✅ ЗАПУСК СТРИМА
        elif action == "start_stream" and cam_id:
            print(f"▶️ [CMD] Запуск стрима для камеры {cam_id}")
            with get_db() as conn:
                cam = conn.execute("SELECT * FROM cameras WHERE id=?", (cam_id,)).fetchone()
            if cam:
                start_hls_stream(dict(cam))

        # ✅ ОСТАНОВКА СТРИМА
        elif action == "stop_stream" and cam_id:
            print(f"⏹️ [CMD] Остановка стрима для камеры {cam_id}")
            stop_hls_stream(cam_id)

        # ✅ ОСТАНОВКА ДЕТЕКТОРА → останавливаем запись
        elif action == "stop_detector" and cam_id:
            print(f"⏹️ [CMD] Остановка детектора для камеры {cam_id} → стоп записи")
            stop_motion_recording(cam_id)

        # ✅ ПЕРЕЗАГРУЗКА КОНФИГА
        elif action == "reload_config" and cam_id:
            print(f"📡 [CMD] Перезагрузка конфига для камеры {cam_id}")
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
            print(f"🔄 Перезапущено стримов: {len(stream_processes)}")

        elif action == "ping":
            # Ответ на пинг от Health Monitor
            client.publish("spartan/streamer/pong", json.dumps({
                "status": "alive",
                "streams": len(stream_processes),
                "recordings": len(recording_processes),
                "timestamp": int(time.time())
            }))

    except Exception as e:
        print(f"⚠️ [CMD] Ошибка: {e}")


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
    print(f"  📡 MQTT: {MQTT_BROKER}:{MQTT_PORT}")
    print(f"  🎬 HLS сегменты: {HLS_TIME} сек")
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
    print(f"[Cameras] {len(cameras)}")
    for cam in cameras:
        if cam.get("enabled") and cam.get("stream_enabled", True):
            start_hls_stream(cam)

    # Запускаем очистку старых сегментов
    threading.Thread(target=cleanup_old_segments, daemon=True).start()

    print(f"[HLS] Streams: {len(stream_processes)}")
    print(f"[Subscriptions] spartan/+/motion, spartan/+/cmd, spartan/streams/reload")
    print("[Running] Working... (Ctrl+C to exit)")
    print()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        signal_handler(None, None)


if __name__ == '__main__':
    main()