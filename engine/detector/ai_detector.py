"""
AI детектор (YOLOv8)
"""
import os
import cv2
import numpy as np
import json
import time
import tempfile
from engine.shared.constants import *
from engine.shared.utils import ts


class AIDetector:
    def __init__(self, camera):
        self.camera = camera
        self.enabled = camera.get("ai_enabled", False)
        self.model = None
        self.classes = camera.get("ai_classes", [0])
        self.confidence = camera.get("ai_confidence", 0.5)
        self.frame_skip = camera.get("ai_frame_skip", 5)
        self._frame_counter = 0
        self._ai_frames_list = []

        if self.enabled:
            self._init_model()

    def _init_model(self):
        """Загружает модель YOLO"""
        try:
            from ultralytics import YOLO
            print(f"{ts()} 🤖 [{self.camera['name']}] Загружаю YOLOv8n...")
            self.model = YOLO('yolov8n.pt')

            if isinstance(self.classes, str):
                try:
                    self.classes = json.loads(self.classes)
                except:
                    self.classes = [0]

            print(f"{ts()} ✅ [{self.camera['name']}] YOLOv8n загружен! Классы: {self.classes}")
        except Exception as e:
            print(f"{ts()} ❌ [{self.camera['name']}] Ошибка загрузки YOLO: {e}")
            self.enabled = False

    def detect(self, frame):
        """
        Запускает YOLO на кадре.
        Возвращает (ai_result, boxes) или (None, None)
        """
        if not self.enabled or not self.model:
            return None, None

        try:
            results = self.model(frame, verbose=False, conf=self.confidence)
            boxes = results[0].boxes

            if boxes is None or len(boxes) == 0:
                return None, None

            detected = {'person': 0, 'car': 0, 'motorcycle': 0, 'dog': 0, 'cat': 0, 'total': 0}
            box_list = []

            ai_classes = self.classes
            if isinstance(ai_classes, str):
                ai_classes = json.loads(ai_classes)

            for box in boxes:
                cls = int(box.cls[0])
                conf = float(box.conf[0])

                if cls not in ai_classes:
                    continue

                x1, y1, x2, y2 = box.xyxy[0].tolist()
                box_list.append({
                    'class': cls,
                    'confidence': conf,
                    'x1': int(x1), 'y1': int(y1),
                    'x2': int(x2), 'y2': int(y2)
                })

                if cls == 0:
                    detected['person'] += 1
                elif cls == 2:
                    detected['car'] += 1
                elif cls == 3:
                    detected['motorcycle'] += 1
                elif cls == 16:
                    detected['dog'] += 1
                elif cls == 17:
                    detected['cat'] += 1

                detected['total'] += 1

            if detected['total'] > 0:
                return detected, box_list
            return None, None

        except Exception as e:
            print(f"⚠️ [{self.camera['name']}] Ошибка YOLO: {e}")
            return None, None

    def draw_boxes(self, frame, boxes):
        """Рисует рамки на кадре"""
        if not boxes:
            return frame

        colors = {
            0: (0, 255, 0),
            2: (0, 0, 255),
            3: (0, 255, 255),
        }
        names = {0: 'Person', 2: 'Car', 3: 'Moto'}

        for box in boxes:
            cls = box.get('class', 0)
            if cls not in self.classes:
                continue

            x1, y1, x2, y2 = box['x1'], box['y1'], box['x2'], box['y2']
            color = colors.get(cls, (255, 255, 255))
            name = names.get(cls, 'Obj')
            conf = box.get('confidence', 0)

            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            label = f'{name} {conf*100:.0f}%'
            cv2.putText(frame, label, (x1, y1-10), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)

        return frame

    def create_overlay_png(self, frame, boxes):
        """Создаёт PNG с прозрачным фоном и рамками для overlay"""
        try:
            h, w = frame.shape[:2]
            overlay = np.zeros((h, w, 4), dtype=np.uint8)

            colors = {
                0: (0, 255, 0, 255),
                2: (0, 0, 255, 255),
                3: (0, 255, 255, 255),
            }
            names = {0: 'Person', 2: 'Car', 3: 'Moto'}

            for box in boxes:
                cls = box.get('class', 0)
                if cls not in self.classes:
                    continue

                conf = box.get('confidence', 0)
                x1, y1, x2, y2 = box['x1'], box['y1'], box['x2'], box['y2']
                color = colors.get(cls, (255, 255, 255, 255))
                name = names.get(cls, 'Obj')

                cv2.rectangle(overlay, (x1, y1), (x2, y2), color, 3)
                label = f'{name} {conf*100:.0f}%'
                (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)
                cv2.rectangle(overlay, (x1, y1-th-10), (x1+tw+10, y1), color, -1)
                cv2.putText(overlay, label, (x1+5, y1-5), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0, 255), 2)

            timestamp = time.strftime("%Y%m%d_%H%M%S")
            ai_frames_dir = os.path.join(tempfile.gettempdir(), f"ai_overlay_{self.camera['id']}")
            os.makedirs(ai_frames_dir, exist_ok=True)

            self._frame_counter += 1
            filename = f"overlay_{timestamp}_{self._frame_counter:04d}.png"
            filepath = os.path.join(ai_frames_dir, filename)

            cv2.imwrite(filepath, overlay, [cv2.IMWRITE_PNG_COMPRESSION, 3])
            return filepath
        except Exception as e:
            print(f"{ts()} {C_RED}❌ Ошибка создания overlay: {e}{C_RESET}")
            return None