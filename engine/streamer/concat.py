"""
Склейка сегментов с AI-рамками
"""
import os
import json
import time
import shutil
import tempfile
import subprocess
from engine.shared.constants import *
from engine.shared.utils import ts


def concat_with_ai_frames(selected_segments, boxes_file, final_output, ffmpeg):
    """
    Быстрая склейка + пост-обработка рамками.
    1. Склеиваем все сегменты через -c copy (быстро)
    2. Одним ffmpeg накладываем рамки по таймкодам из JSON
    """
    print(f"{ts()} 🔧 concat_with_ai_frames ВЫЗВАНА!")

    if not boxes_file or not os.path.exists(boxes_file):
        return False

    try:
        with open(boxes_file, 'r') as f:
            data = json.load(f)

        ai_frames = data.get('frames', [])
        if not ai_frames:
            return False

        print(f"{ts()} 🕐 AI-кадров: {len(ai_frames)}")

        temp_dir = tempfile.mkdtemp(prefix="ai_post_")
        temp_video = os.path.join(temp_dir, "temp_concat.mp4")

        # Шаг 1: Быстрая склейка
        print(f"{ts()} 🔧 Шаг 1: Быстрая склейка {len(selected_segments)} сегментов...")

        concat_file = os.path.join(temp_dir, "concat.txt")
        with open(concat_file, "w", encoding='utf-8') as f:
            for seg in selected_segments:
                escaped_path = os.path.abspath(seg).replace('\\', '/')
                f.write(f"file '{escaped_path}'\n")

        cmd_concat = [ffmpeg, "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", concat_file, "-c", "copy", "-y", temp_video]
        result = subprocess.run(cmd_concat, timeout=60, capture_output=True)

        if result.returncode != 0 or not os.path.exists(temp_video):
            print(f"{ts()} {C_RED}❌ Ошибка быстрой склейки{C_RESET}")
            return False

        print(f"{ts()} ✅ Склейка готова: {os.path.getsize(temp_video):,} байт")

        # Шаг 2: Накладываем рамки
        print(f"{ts()} 🔧 Шаг 2: Накладываю рамки...")

        draw_filters = []
        first_segment_time = os.path.getmtime(selected_segments[0])

        for ai in ai_frames:
            offset = ai['time'] - first_segment_time
            if offset < 0:
                offset = 0

            for box in ai.get('boxes', []):
                cls = box.get('class', 0)
                conf = box.get('confidence', 0)
                x1, y1, x2, y2 = box['x1'], box['y1'], box['x2'], box['y2']
                w = x2 - x1
                h = y2 - y1

                if cls == 0:
                    color = 'green'
                    label = f"Person {conf*100:.0f}%"
                elif cls == 2:
                    color = 'red'
                    label = f"Car {conf*100:.0f}%"
                else:
                    color = 'yellow'
                    label = f"Obj {conf*100:.0f}%"

                draw_filters.append(f"drawbox=x={x1}:y={y1}:w={w}:h={h}:color={color}:t=3:enable='between(t,{offset:.1f},{offset+1:.1f})'")
                draw_filters.append(f"drawtext=text='{label}':x={x1+5}:y={y1-25}:fontsize=18:fontcolor=white:box=1:boxcolor=black@0.5:enable='between(t,{offset:.1f},{offset+1:.1f})'")

        if draw_filters:
            filter_chain = ','.join(draw_filters)
            cmd_boxes = [ffmpeg, "-loglevel", "error", "-i", temp_video, "-vf", filter_chain, "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23", "-c:a", "copy", "-y", final_output]
            result = subprocess.run(cmd_boxes, timeout=300, capture_output=True)

            if result.returncode == 0 and os.path.exists(final_output):
                print(f"{ts()} {C_GREEN}✅ AI-ролик готов! ({os.path.getsize(final_output):,} байт){C_RESET}")
            else:
                shutil.copy2(temp_video, final_output)
                print(f"{ts()} {C_YELLOW}⚠️ Рамки не наложились{C_RESET}")
        else:
            shutil.copy2(temp_video, final_output)

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