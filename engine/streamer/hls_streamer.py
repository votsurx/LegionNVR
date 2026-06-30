"""
HLS стриминг
"""
import os
import subprocess
import glob
import time
from engine.shared.constants import HLS_DIR, HLS_TIME
from engine.shared.utils import ts, find_ffmpeg

# Глобальные переменные для управления процессами
stream_processes = {}


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
    """Запускает HLS стрим с буфером для предзаписи"""
    cam_id = str(camera["id"])

    if not camera.get("enabled", True):
        print(f"⏸️ Камера {cam_id} отключена, стрим не запущен")
        return

    if not camera.get("stream_enabled", True):
        print(f"⏸️ Стрим для камеры {cam_id} отключен")
        return

    stop_hls_stream(cam_id)

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
        "-hls_segment_filename", segment_pattern,
        "-hls_flags", "omit_endlist+delete_segments+split_by_time",
        "-strftime", "1",
        playlist_file
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