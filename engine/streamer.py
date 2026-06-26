"""
Legion NVR - Stream Engine
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

MQTT_BROKER = "127.0.0.1"
MQTT_PORT = 1883
HLS_DIR = "streams"
RECORDINGS_DIR = "recordings"

# Глобальные переменные для управления процессами
stream_processes = {}
recording_processes = {}
camera_status = {}  # {camera_id: {'enabled': bool, 'online': bool}}


def get_recordings_path():
    try:
        with get_db() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key='recordings_path'").fetchone()
        if row:
            return row[0]
    except:
        pass
    return "recordings"


def find_ffmpeg():
    if shutil.which("ffmpeg"):
        return "ffmpeg"
    for p in ["C:/ffmpeg/bin/ffmpeg.exe", "C:/ffmpeg/ffmpeg.exe"]:
        if os.path.exists(p):
            return p
    return None


def stop_hls_stream(camera_id):
    """Останавливает HLS стрим"""
    cam_id = str(camera_id)
    if cam_id in stream_processes:
        try:
            stream_processes[cam_id].terminate()
            stream_processes[cam_id].wait(timeout=3)
        except:
            stream_processes[cam_id].kill()
        del stream_processes[cam_id]

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
    """Запускает HLS стрим с большим буфером для предзаписи"""
    cam_id = str(camera["id"])

    if not camera.get("stream_enabled", True):
        print(f"⏸️ Камера {cam_id} отключена, стрим не запущен")
        return

    if not camera.get("enabled", True):
        print(f"⏸️ Камера {cam_id} выключена, стрим не запущен")
        return

    # Останавливаем старый стрим
    stop_hls_stream(cam_id)

    # Чистим старые сегменты (НО НЕ ВСЕ, ОСТАВЛЯЕМ ПОСЛЕДНИЕ!)
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

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        print("❌ ffmpeg не найден!")
        return

    cmd = [
        ffmpeg, "-loglevel", "error",
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
        "-hls_time", "0.2",
        "-hls_list_size", "10",
        "-hls_flags", "omit_endlist",
        os.path.join(HLS_DIR, f"camera{cam_id}.m3u8")
    ]

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        stream_processes[cam_id] = proc
        print(f"🎥 HLS стрим '{camera['name']}' запущен (ID={cam_id}")
    except Exception as e:
        print(f"❌ Ошибка запуска стрима для {camera['name']}: {e}")


def restart_hls_stream(camera):
    """Перезапускает HLS стрим"""
    stop_hls_stream(camera["id"])
    time.sleep(0.5)
    start_hls_stream(camera)


def _simple_motion_recording(camera, output, pre_sec, post_sec):
    """Обычная запись (без буфера) — fallback"""
    ffmpeg = find_ffmpeg()
    total_sec = pre_sec + post_sec

    cmd = [
        ffmpeg,
        "-loglevel", "error",
        "-rtsp_transport", "tcp",
        "-i", camera["rtsp_main"],
        # ✅ ДОБАВЛЯЕМ ВРЕМЯ НА ВИДЕО
        "-vf", "drawtext=text='%Y-%m-%d %H:%M:%S':fontcolor=white:fontsize=24:x=10:y=10:box=1:boxcolor=black@0.5",
        "-c:v", "copy",
        "-c:a", "aac",
        "-t", str(total_sec),
        "-y",
        output
    ]

    try:
        subprocess.run(cmd, timeout=total_sec + 10, capture_output=True)
        print(f"🔴 Запись тревоги (обычная): {camera['name']} → {os.path.basename(output)}")
    except subprocess.TimeoutExpired:
        print(f"❌ Таймаут записи для {camera['name']}")
    except Exception as e:
        print(f"❌ Ошибка записи для {camera['name']}: {e}")


def start_motion_recording(camera):
    """Запись по тревоге с перекодированием для плавного видео"""
    cam_id = str(camera["id"])

    if not camera.get("enabled", True):
        return

    if not camera.get("record_enabled", False):
        return

    pre_sec = camera.get('record_pre_sec', 4)
    post_sec = camera.get('record_post_sec', 10)

    if cam_id in recording_processes:
        return

    now = time.strftime("%Y-%m-%d_%H-%M-%S")
    recordings_path = get_recordings_path()
    date_dir = os.path.join(recordings_path, f"camera_{cam_id}", time.strftime("%Y-%m-%d"))
    os.makedirs(date_dir, exist_ok=True)

    output = os.path.join(date_dir, f"{now}_motion.mp4")
    temp_output = os.path.join(date_dir, f"{now}_temp.mp4")

    hls_dir = HLS_DIR
    segments = sorted(glob.glob(os.path.join(hls_dir, f"camera{cam_id}*.ts")))

    print(f"📼 HLS сегментов: {len(segments)}")

    ffmpeg = find_ffmpeg()

    # ✅ ЗАПИСЫВАЕМ ПОСТ-ЗАПИСЬ
    cmd_record = [
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
        "-an",                          # ← БЕЗ АУДИО
        "-t", str(post_sec),
        "-y",
        temp_output
    ]

    try:
        proc = subprocess.Popen(cmd_record, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        proc.wait(timeout=post_sec + 5)
        print(f"✅ Постзапись завершена")
    except Exception as e:
        print(f"❌ Ошибка постзаписи: {e}")
        return

    # ✅ ОБЪЕДИНЯЕМ С ПЕРЕКОДИРОВАНИЕМ
    if len(segments) >= 2 and os.path.exists(temp_output):
        segments_needed = max(2, pre_sec)
        pre_segments = segments[-segments_needed:]

        print(f"📼 Берём {len(pre_segments)} сегментов для предзаписи")

        concat_file = os.path.join(tempfile.gettempdir(), f"concat_{cam_id}_{int(time.time())}.txt")
        with open(concat_file, "w") as f:
            for seg in pre_segments:
                f.write(f"file '{os.path.abspath(seg)}'\n")
            f.write(f"file '{os.path.abspath(temp_output)}'\n")

        # ✅ С ПЕРЕКОДИРОВАНИЕМ (ЧТОБЫ НЕ БЫЛО РВАНЫХ РОЛИКОВ)
        cmd_concat = [
            ffmpeg,
            "-loglevel", "error",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_file,
            "-c:v", "libx264",        # ← ПЕРЕКОДИРУЕМ
            "-preset", "veryfast",    # ← БЫСТРО
            "-crf", "23",             # ← КАЧЕСТВО
            "-pix_fmt", "yuv420p",    # ← СОВМЕСТИМОСТЬ
            "-an",                    # ← БЕЗ АУДИО
            "-y",
            output
        ]

        result = subprocess.run(cmd_concat, timeout=60, capture_output=True)

        if result.returncode == 0 and os.path.exists(output):
            print(f"✅ Запись сохранена: {os.path.basename(output)} (предзапись {len(pre_segments)} сегментов + {post_sec} сек)")

            try:
                conn = get_db()
                conn.execute(
                    "INSERT INTO recordings (camera_id, filename, start_time, type) VALUES (?, ?, datetime('now','localtime'), 'motion')",
                    (int(cam_id), output)
                )
                conn.commit()
                conn.close()
            except Exception as e:
                print(f"❌ Ошибка записи в БД: {e}")
        else:
            print(f"❌ Ошибка объединения: {result.stderr.decode() if result.stderr else 'Unknown'}")
            # Fallback: просто сохраняем постзапись
            if os.path.exists(temp_output) and os.path.getsize(temp_output) > 0:
                os.rename(temp_output, output)
                print(f"✅ Сохранена только постзапись (fallback)")

        try:
            os.remove(concat_file)
        except:
            pass
    else:
        if os.path.exists(temp_output) and os.path.getsize(temp_output) > 0:
            os.rename(temp_output, output)
            print(f"✅ Сохранена только постзапись")

    try:
        if os.path.exists(temp_output) and temp_output != output:
            os.remove(temp_output)
    except:
        pass

def stop_motion_recording(camera_id):
    """Останавливает запись и собирает финальный ролик с буфером"""
    cam_id = str(camera_id)

    if cam_id not in recording_processes:
        print(f"⚠️ Запись для камеры {cam_id} не найдена")
        return

    data = recording_processes[cam_id]
    proc = data['proc']
    temp_output = data['temp_output']
    final_output = data['final_output']
    max_duration = data['max_duration']
    start_time = data['start_time']
    segments = data.get('segments', [])

    # ✅ ОСТАНАВЛИВАЕМ FFMPEG
    try:
        proc.terminate()
        proc.wait(timeout=3)
        print(f"⏹️ FFmpeg остановлен для камеры {cam_id}")
    except subprocess.TimeoutExpired:
        proc.kill()
        print(f"💀 FFmpeg принудительно убит для камеры {cam_id}")

    # ✅ ДАЁМ ВРЕМЯ НА ЗАПИСЬ НА ДИСК
    time.sleep(1)

    # ✅ ОТМЕНЯЕМ ТАЙМЕР
    if 'timer' in data:
        data['timer'].cancel()

    # ✅ ВЫЧИСЛЯЕМ РЕАЛЬНУЮ ДЛИТЕЛЬНОСТЬ
    duration = time.time() - start_time
    print(f"⏹️ Запись остановлена для камеры {cam_id} (длительность: {duration:.1f} сек)")

    # ✅ ЕСЛИ ЗАПИСЬ КОРОТКАЯ — НЕ СОХРАНЯЕМ
    if duration < 2.0:
        print(f"🗑️ Запись слишком короткая ({duration:.1f} сек), удаляем")
        try:
            os.remove(temp_output)
        except:
            pass
        del recording_processes[cam_id]
        return

    # ✅ ОБРЕЗАЕМ ПО МАКСИМАЛЬНОЙ ДЛИТЕЛЬНОСТИ (post_sec)
    if duration > max_duration:
        print(f"✂️ Обрезаем ролик до {max_duration} сек (было {duration:.1f} сек)")
        duration = max_duration

    # ✅ СОБИРАЕМ ФИНАЛЬНЫЙ РОЛИК (буфер + запись)
    ffmpeg = find_ffmpeg()

    if segments and len(segments) >= 3:
        # ✅ ЕСТЬ СЕГМЕНТЫ — ДОБАВЛЯЕМ ПРЕДЗАПИСЬ
        print(f"📼 Добавляем предзапись из {len(segments)} сегментов")

        pre_sec = 5  # или из настроек
        segments_needed = max(3, pre_sec // 2 + 1)
        pre_segments = segments[-segments_needed:]

        # Создаём файл конкатенации
        concat_file = os.path.join(tempfile.gettempdir(), f"concat_final_{cam_id}_{int(time.time())}.txt")
        with open(concat_file, "w") as f:
            for seg in pre_segments:
                f.write(f"file '{os.path.abspath(seg)}'\n")
            f.write(f"file '{os.path.abspath(temp_output)}'\n")

        cmd_concat = [
            ffmpeg,
            "-loglevel", "error",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_file,
            "-c:v", "copy",
            "-an",
            "-y",
            final_output
        ]
        print(f"📡 Команда FFmpeg: {' '.join(cmd_concat)}")

        subprocess.run(cmd_concat, timeout=duration + 20, capture_output=True)
        os.remove(concat_file)
    else:
        # ❌ НЕТ СЕГМЕНТОВ — ПРОСТО ПЕРЕИМЕНОВЫВАЕМ
        os.rename(temp_output, final_output)

    # ✅ УДАЛЯЕМ ВРЕМЕННЫЙ ФАЙЛ
    try:
        if os.path.exists(temp_output) and temp_output != final_output:
            os.remove(temp_output)
    except:
        pass

    print(f"💾 Запись сохранена: {os.path.basename(final_output)} (длительность: {duration:.1f} сек)")

    # ✅ ПРОВЕРЯЕМ, ЧТО ФАЙЛ СОЗДАЛСЯ
    if os.path.exists(final_output):
        file_size = os.path.getsize(final_output)
        print(f"✅ Файл создан: {os.path.basename(final_output)} (размер: {file_size} байт)")
    else:
        print(f"❌ ФАЙЛ НЕ СОЗДАН: {final_output}")

    # ✅ ЛОГИРУЕМ В БД
    if os.path.exists(final_output) and os.path.getsize(final_output) > 0:
        try:
            conn = get_db()
            conn.execute(
                "INSERT INTO recordings (camera_id, filename, start_time, type) VALUES (?, ?, datetime('now','localtime'), 'motion')",
                (int(cam_id), final_output)
            )
            conn.commit()
            conn.close()
            print(f"📝 Запись добавлена в БД")
        except Exception as e:
            print(f"❌ Ошибка записи в БД: {e}")

    # ✅ УДАЛЯЕМ ИЗ ПРОЦЕССОВ
    del recording_processes[cam_id]

def on_motion(client, userdata, msg):
    try:
        data = json.loads(msg.payload.decode())
        if data.get("event") == "motion_start":
            with get_db() as conn:
                cam = conn.execute("SELECT * FROM cameras WHERE id=?", (data["camera_id"],)).fetchone()
            if cam:
                cam_dict = dict(cam)
                start_motion_recording(cam_dict)
    except Exception as e:
        print(f"⚠️ Ошибка обработки MQTT: {e}")


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

        # ✅ ОСТАНОВКА ЗАПИСИ
        elif action == "stop_recording" and cam_id:
            print(f"📡 [CMD] Остановка записи для камеры {cam_id}")
            stop_motion_recording(cam_id)

        # ✅ ЗАПУСК СТРИМА (НЕ reload_config!)
        elif action == "start_stream" and cam_id:
            print(f"▶️ [CMD] Запуск стрима для камеры {cam_id}")
            with get_db() as conn:
                cam = conn.execute("SELECT * FROM cameras WHERE id=?", (cam_id,)).fetchone()
            if cam:
                cam_dict = dict(cam)
                if cam_dict.get("enabled") and cam_dict.get("stream_enabled", True):
                    start_hls_stream(cam_dict)

        # ✅ ОСТАНОВКА СТРИМА
        elif action == "stop_stream" and cam_id:
            print(f"⏹️ [CMD] Остановка стрима для камеры {cam_id}")
            stop_hls_stream(cam_id)

        # ✅ ПЕРЕЗАГРУЗКА КОНФИГА (только при изменении настроек!)
        elif action == "reload_config" and cam_id:
            print(f"📡 [CMD] Перезагрузка конфига для камеры {cam_id}")
            with get_db() as conn:
                cam = conn.execute("SELECT * FROM cameras WHERE id=?", (cam_id,)).fetchone()

            if cam:
                cam_dict = dict(cam)
                if cam_dict.get("enabled") and cam_dict.get("stream_enabled", True):
                    restart_hls_stream(cam_dict)  # ← ПЕРЕЗАПУСКАЕТ, а не останавливает
                else:
                    stop_hls_stream(cam_id)

        elif action == "reload_all":
            print("📡 [CMD] Перезагрузка ВСЕХ стримов")
            cameras = load_cameras()
            for cam in cameras:
                if cam.get("enabled") and cam.get("stream_enabled", True):
                    start_hls_stream(cam)
            print(f"🔄 Перезапущено стримов: {len(stream_processes)}")

    except Exception as e:
        print(f"⚠️ [CMD] Ошибка: {e}")


def load_cameras():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM cameras WHERE enabled=1").fetchall()
    return [dict(r) for r in rows]


def on_motion_and_cmd(client, userdata, msg):
    """Обрабатывает и motion, и cmd"""
    if msg.topic.endswith("/motion"):
        on_motion(client, userdata, msg)
    else:
        on_cmd(client, userdata, msg)


def signal_handler(sig, frame):
    """Обработчик Ctrl+C"""
    print("\n⏹️ Завершение...")
    for proc in stream_processes.values():
        try:
            proc.terminate()
        except:
            pass
    for proc in recording_processes.values():
        try:
            proc.terminate()
        except:
            pass
    sys.exit(0)


def main():
    print("[Legion NVR] Stream Engine")
    print(f"[MQTT] {MQTT_BROKER}:{MQTT_PORT}")

    signal.signal(signal.SIGINT, signal_handler)

    os.makedirs(HLS_DIR, exist_ok=True)
    os.makedirs(RECORDINGS_DIR, exist_ok=True)

    # MQTT
    client = mqtt.Client()
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
        start_hls_stream(cam)

    print(f"[HLS] Streams: {len(stream_processes)}")
    print(f"[Subscriptions] spartan/+/motion, spartan/+/cmd, spartan/streams/reload")
    print("[Running] Working... (Ctrl+C to exit)")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        signal_handler(None, None)


if __name__ == '__main__':
    main()