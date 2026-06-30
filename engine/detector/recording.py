"""
Сохранение скриншотов, JSON, координат
"""
import os
import cv2
import json
import time
from engine.shared.constants import *
from engine.shared.utils import ts


class RecordingManager:
    def __init__(self, camera, ai_detector=None):
        self.camera = camera
        self.ai_detector = ai_detector
        self.motion_boxes = []
        self.ai_frames_list = []
        self.ai_frame_counter = 0

    def reset(self):
        """Сбрасывает счётчики для новой тревоги"""
        self.motion_boxes = []
        self.ai_frames_list = []
        self.ai_frame_counter = 0

    def save_alert_snapshot(self, frame, boxes):
        """Сохраняет скриншот с рамками"""
        try:
            from engine.detector.ai_detector import AIDetector
            timestamp = time.strftime("%Y-%m-%d_%H-%M-%S")
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            snap_dir = os.path.join(base_dir, "snapshots", str(self.camera["id"]))
            os.makedirs(snap_dir, exist_ok=True)

            filename = f"{timestamp}_alert.jpg"
            filepath = os.path.join(snap_dir, filename)

            ai = AIDetector(self.camera)
            frame_with_boxes = ai.draw_boxes(frame.copy(), boxes)
            cv2.imwrite(filepath, frame_with_boxes, [cv2.IMWRITE_JPEG_QUALITY, 85])

            print(f"{ts()} 📸 [{self.camera['name']}] Скриншот сохранён: {filename}")
            return filepath
        except Exception as e:
            print(f"{ts()} ❌ [{self.camera['name']}] Ошибка скриншота: {e}")
            return None

    def save_motion_boxes(self, boxes, frame_time):
        """Сохраняет координаты с временной меткой"""
        if boxes:
            self.motion_boxes.append({
                'time': frame_time,
                'boxes': boxes
            })

    def save_ai_frame(self, frame, boxes):
            """Сохраняет PNG с прозрачным фоном для overlay"""
            try:
                if self.ai_detector:
                    filepath = self.ai_detector.create_overlay_png(frame, boxes)
                    if filepath:
                        meta = {
                            'time': time.time(),
                            'file': filepath,
                            'boxes': boxes
                        }
                        self.ai_frames_list.append(meta)
                    return filepath
            except Exception as e:
                print(f"{ts()} {C_RED}❌ Ошибка сохранения AI-кадра: {e}{C_RESET}")
            return None

    def save_ai_frames_json(self):
        """Сохраняет JSON со списком AI-кадров"""
        if not self.ai_frames_list:
            return None

        timestamp = time.strftime("%Y-%m-%d_%H-%M-%S")
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        boxes_dir = os.path.join(base_dir, "snapshots", str(self.camera["id"]), "boxes")
        os.makedirs(boxes_dir, exist_ok=True)

        filepath = os.path.join(boxes_dir, f"{timestamp}_boxes.json")

        data = {
            'camera_id': self.camera['id'],
            'camera_name': self.camera['name'],
            'timestamp': timestamp,
            'frames': [{'time': f['time'], 'file': f['file'], 'boxes': f['boxes']} for f in self.ai_frames_list]
        }

        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())

        self.ai_frames_list = []
        print(f"{ts()} 📦 [{self.camera['name']}] AI-кадры сохранены: {len(data['frames'])} шт.")
        return filepath