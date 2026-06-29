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

# ════════════════════════════════════════════════════════════
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ════════════════════════════════════════════════════════════

def ts():
    """Возвращает цветную временную метку [HH:MM:SS]"""
    return f"{C_CYAN}[{time.strftime('%H:%M:%S')}]{C_RESET}"

def _concat_with_boxes(selected_segments, boxes_file, final_output, ffmpeg):
    """
    Склеивает сегменты с наложением AI-рамок.
    Возвращает True если успешно.
    """
    print(f"{ts()} 🔧 _concat_with_boxes ВЫЗВАНА!")
    print(f"{ts()} 📁 boxes_file: {boxes_file}")
    print(f"{ts()} 📁 exists: {os.path.exists(boxes_file) if boxes_file else 'N/A'}")

    if not boxes_file or not os.path.exists(boxes_file):
        print(f"{ts()} ⚠️ Файл рамок не найден — склеиваю без рамок")
        return False

    # ✅ ПРОВЕРЯЕМ ЧТО ФАЙЛ НЕ ПУСТОЙ
    file_size = os.path.getsize(boxes_file)
    print(f"{ts()} 📁 Размер файла: {file_size} байт")

    if file_size == 0:
        print(f"{ts()} ⚠️ Файл координат пустой, пропускаю рамки")
        return False

    try:
        with open(boxes_file, 'r') as f:
            boxes_data = json.load(f)

        # ✅ ПРОВЕРЯЕМ ЧТО ДАННЫЕ КОРРЕКТНЫ
        if not boxes_data or not isinstance(boxes_data, dict):
            print(f"{ts()} ⚠️ Некорректный формат данных рамок")
            return False

        frames = boxes_data.get('frames', [])
        if not frames:
            print(f"{ts()} ⚠️ Нет данных о рамках (frames пуст)")
            return False

        # ✅ ОТЛАДКА ВРЕМЕНИ
        print(f"{ts()} 🕐 Кадров в JSON: {len(frames)}")
        print(f"{ts()} 🕐 Первый кадр: {time.strftime('%H:%M:%S', time.localtime(frames[0]['time']))}")
        print(f"{ts()} 🕐 Последний кадр: {time.strftime('%H:%M:%S', time.localtime(frames[-1]['time']))}")
        print(f"{ts()} 🕐 Сегментов: {len(selected_segments)}")
        print(f"{ts()} 🕐 Первый сегмент: {time.strftime('%H:%M:%S', time.localtime(os.path.getmtime(selected_segments[0])))}")
        print(f"{ts()} 🕐 Последний сегмент: {time.strftime('%H:%M:%S', time.localtime(os.path.getmtime(selected_segments[-1])))}")

        # ✅ ПОЛУЧАЕМ РАЗРЕШЁННЫЕ КЛАССЫ
        ai_classes = boxes_data.get('ai_classes', [0, 2])
        print(f"{ts()} 🔧 Накладываю рамки на {len(selected_segments)} сегментов (классы: {ai_classes})...")

        # Создаём временную папку для обработанных сегментов
        temp_dir = tempfile.mkdtemp(prefix="ai_boxes_")
        processed_segments = []

        for i, seg in enumerate(selected_segments):
            seg_time = os.path.getmtime(seg)

            # ✅ НАХОДИМ БЛИЖАЙШИЙ КАДР ПО ВРЕМЕНИ
            closest_frame = None
            min_diff = float('inf')
            for frame_data in frames:
                diff = abs(frame_data['time'] - seg_time)
                if diff < min_diff:
                    min_diff = diff
                    closest_frame = frame_data

            seg_name = os.path.basename(seg)
            processed_seg = os.path.join(temp_dir, seg_name)

            # ✅ ЛОГ ДЛЯ ПЕРВЫХ 5 СЕГМЕНТОВ
            if i < 5:
                has_boxes = 'YES' if (closest_frame and closest_frame.get('boxes')) else 'NO'
                print(f"{ts()} 🔍 Сегмент {i}: {seg_name} время={time.strftime('%H:%M:%S', time.localtime(seg_time))}, рамки={has_boxes}, diff={min_diff:.2f}с")

            if closest_frame and closest_frame.get('boxes'):
                boxes = closest_frame['boxes']

                # ✅ ФИЛЬТРУЕМ ТОЛЬКО РАЗРЕШЁННЫЕ КЛАССЫ
                filtered_boxes = [b for b in boxes if b['class'] in ai_classes]

                if filtered_boxes:
                    # Строим drawbox фильтры
                    draw_filters = []
                    for box in filtered_boxes:
                        x1, y1, x2, y2 = box['x1'], box['y1'], box['x2'], box['y2']
                        w = x2 - x1
                        h = y2 - y1

                        # Цвет и подпись в зависимости от класса
                        if box['class'] == 0:       # Человек
                            color = 'green'
                            label = f"Person {box['confidence']*100:.0f}%"
                        elif box['class'] == 2:     # Машина
                            color = 'red'
                            label = f"Car {box['confidence']*100:.0f}%"
                        elif box['class'] == 3:     # Мотоцикл
                            color = 'yellow'
                            label = f"Moto {box['confidence']*100:.0f}%"
                        elif box['class'] == 5:     # Автобус
                            color = 'blue'
                            label = f"Bus {box['confidence']*100:.0f}%"
                        elif box['class'] == 7:     # Грузовик
                            color = 'orange'
                            label = f"Truck {box['confidence']*100:.0f}%"
                        elif box['class'] == 16:    # Собака
                            color = 'purple'
                            label = f"Dog {box['confidence']*100:.0f}%"
                        elif box['class'] == 17:    # Кошка
                            color = 'pink'
                            label = f"Cat {box['confidence']*100:.0f}%"
                        else:
                            color = 'white'
                            label = f"Obj {box['confidence']*100:.0f}%"

                        # ✅ КОНТУР (t=3) вместо заливки (t=fill)
                        draw_filters.append(f"drawbox=x={x1}:y={y1}:w={w}:h={h}:color={color}:t=3")
                        # Подпись
                        draw_filters.append(f"drawtext=text='{label}':x={x1+5}:y={y1-25}:fontsize=18:fontcolor=white:box=1:boxcolor=black@0.5")

                    if draw_filters:
                        filter_chain = ','.join(draw_filters)

                        cmd = [
                            ffmpeg,
                            "-loglevel", "error",
                            "-i", seg,
                            "-vf", filter_chain,
                            "-c:v", "libx264",
                            "-preset", "ultrafast",
                            "-crf", "23",
                            "-an",
                            "-y",
                            processed_seg
                        ]

                        try:
                            subprocess.run(cmd, timeout=30, capture_output=True)
                            if os.path.exists(processed_seg) and os.path.getsize(processed_seg) > 0:
                                processed_segments.append(processed_seg)
                            else:
                                # Если не получилось — копируем оригинал
                                shutil.copy2(seg, processed_seg)
                                processed_segments.append(processed_seg)
                        except subprocess.TimeoutExpired:
                            print(f"{ts()} ⚠️ Таймаут обработки сегмента {seg_name}")
                            shutil.copy2(seg, processed_seg)
                            processed_segments.append(processed_seg)
                        except Exception as e:
                            print(f"{ts()} ⚠️ Ошибка обработки сегмента {seg_name}: {e}")
                            shutil.copy2(seg, processed_seg)
                            processed_segments.append(processed_seg)
                    else:
                        # Нет фильтров — копируем как есть
                        shutil.copy2(seg, processed_seg)
                        processed_segments.append(processed_seg)
                else:
                    # Нет разрешённых классов — копируем как есть
                    shutil.copy2(seg, processed_seg)
                    processed_segments.append(processed_seg)
            else:
                # Нет рамок для этого сегмента — копируем как есть
                shutil.copy2(seg, processed_seg)
                processed_segments.append(processed_seg)

        # ✅ СКЛЕИВАЕМ ОБРАБОТАННЫЕ СЕГМЕНТЫ
        if processed_segments:
            print(f"{ts()} 🔧 Склеиваю {len(processed_segments)} обработанных сегментов...")

            concat_file = os.path.join(temp_dir, "concat.txt")
            with open(concat_file, "w", encoding='utf-8') as f:
                for seg in processed_segments:
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

                # Чистим временную папку
                try:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                except:
                    pass

                if result.returncode == 0 and os.path.exists(final_output) and os.path.getsize(final_output) > 0:
                    print(f"{ts()} ✅ AI-ролик с рамками готов!")
                    return True
                else:
                    error_msg = result.stderr.decode('utf-8', errors='ignore')[:200] if result.stderr else 'Unknown'
                    print(f"{ts()} ❌ Ошибка склейки: {error_msg}")
                    return False

            except subprocess.TimeoutExpired:
                print(f"{ts()} ❌ Таймаут склейки (60 сек)")
                try:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                except:
                    pass
                return False
            except Exception as e:
                print(f"{ts()} ❌ Ошибка склейки: {e}")
                try:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                except:
                    pass
                return False
        else:
            print(f"{ts()} ❌ Нет обработанных сегментов")
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except:
                pass
            return False

    except Exception as e:
        print(f"{ts()} ⚠️ Ошибка наложения рамок: {e}")
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
    """
    Запускает HLS стрим с буфером для предзаписи.
    hls_time = 1 сек (идеально для точного буфера).
    hls_list_size = record_pre_sec + 3 (буфер + запас).
    """
    cam_id = str(camera["id"])

    if not camera.get("enabled", True):
        print(f"{ts()} ⏸️ Камера {cam_id} отключена, стрим не запущен")
        return

    if not camera.get("stream_enabled", True):
        print(f"{ts()} ⏸️ Стрим для камеры {cam_id} отключен")
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

    print(f"{ts()} 🎥 [{camera['name']}] Буфер HLS: {hls_list_size} сегментов по {HLS_TIME} сек = {hls_list_size} сек")

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
        print(f"{ts()} 🎥 HLS стрим '{camera['name']}' запущен (буфер {hls_list_size} сек)")
    except Exception as e:
        print(f"{ts()} ❌ Ошибка запуска стрима для {camera['name']}: {e}")


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
    """Запускает запись по тревоге (собирает HLS-сегменты по времени)"""
    cam_id = str(camera["id"])

    if not camera.get("enabled", True):
        return

    if not camera.get("record_enabled", False):
        print(f"{ts()} ⏸️ Запись отключена для камеры {cam_id}")
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
        print(f"{ts()} ❌ Нет HLS-сегментов для камеры {cam_id}")
        return

    all_segments.sort(key=lambda x: x[0])

    print(f"{ts()} {C_RED}📼 Тревога! Время: {time.strftime('%H:%M:%S', time.localtime(alarm_time))}{C_RESET}")
    print(f"{ts()} {C_BLUE}🔴 Запись: буфер {record_pre_sec} сек + пост {record_post_sec} сек{C_RESET}")

    # ✅ СОХРАНЯЕМ ИНФОРМАЦИЮ О ЗАПИСИ (БЕЗ ТАЙМЕРА!)
    motion_recordings[cam_id] = {
        'start_time': time.time(),
        'alarm_time': alarm_time,
        'pre_sec': record_pre_sec,
        'post_sec': record_post_sec,
        'camera': camera
    }

    print(f"{ts()} {C_CYAN}⏱️ Ожидаю stop_recording для склейки...{C_RESET}")

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
        print(f"{ts()} 📦 Найдено JSON-файлов: {len(box_files)}")

        if box_files:
            # ✅ ИЩЕМ ФАЙЛ, БЛИЖАЙШИЙ ПО ВРЕМЕНИ К alarm_time
            best_file = None
            best_diff = float('inf')
            for bf in box_files:
                bf_time = os.path.getmtime(bf)
                diff = abs(bf_time - alarm_time)
                if diff < best_diff:
                    best_diff = diff
                    best_file = bf

            if best_file and best_diff < 10:  # Не старше 10 секунд
                boxes_file = best_file
                print(f"{ts()} 📦 Выбран файл: {os.path.basename(boxes_file)} (разница: {best_diff:.1f}с)")
            else:
                print(f"{ts()} ⚠️ Нет подходящего JSON (разница: {best_diff:.1f}с)")

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
    """Принудительно завершает запись и склеивает ролик"""
    cam_id = str(camera_id)

    if cam_id not in motion_recordings:
        print(f"{ts()} ⚠️ Нет активной записи для камеры {cam_id}")
        return

    print(f"{ts()} {C_GREEN}⏹️ Завершение записи для камеры {cam_id}{C_RESET}")

    # ✅ СРАЗУ СОБИРАЕМ СЕГМЕНТЫ (JSON от детектора уже готов!)
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
        print(f"{ts()} ⚠️ Ошибка обработки motion: {e}")


def on_cmd(client, userdata, msg):
    """Обработчик команд для стримера"""
    try:
        data = json.loads(msg.payload.decode())
        action = data.get("action")
        cam_id = data.get("camera_id")

        # ✅ СТАРТ ЗАПИСИ
        if action == "start_recording" and cam_id:
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

    # Запускаем очистку старых сегментов
    threading.Thread(target=cleanup_old_segments, daemon=True).start()

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
