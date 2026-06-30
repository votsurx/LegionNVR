"""
Основной класс MotionDetector
"""
import os
import cv2
import numpy as np
import json
import time
import threading
import tempfile
import traceback

from engine.shared.constants import *
from engine.shared.utils import ts
from engine.shared.mqtt_utils import send_mqtt_command
from engine.detector.zones import load_zones
from engine.detector.ai_detector import AIDetector
from engine.detector.recording import RecordingManager


class MotionDetector:
    def __init__(self, camera, mqtt_client):
        self.camera = camera
        self.mqtt = mqtt_client
        self.cap = None
        self.fgbg = cv2.createBackgroundSubtractorMOG2(
            history=300, varThreshold=25, detectShadows=False
        )
        self.motion_active = False
        self.last_motion_time = 0
        self.running = False
        self.enabled = camera.get("enabled", True)

        self.threshold = camera.get("motion_threshold", 2.0)
        self.cooldown = camera.get("motion_cooldown", 5)

        self.zones = []
        self.motion_boxes = []
        load_zones(self)

        self.motion_end_delay = camera.get("motion_end_delay", 2.0)
        self.motion_end_timer = None

        self.warmup_frames = 0
        self.WARMUP_NEEDED = 25
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 5
        self._reconnect_delay = 2
        self._last_mog2_log = 0

        # AI детектор
        self.ai_detector = AIDetector(camera)

        # Запись (скриншоты, JSON, координаты)
        self.recording = RecordingManager(camera)

        self.recording = RecordingManager(camera, self.ai_detector)

        # Счётчики для дебаунса
        self._ai_found_streak = 0
        self._ai_miss_streak = 0
        self._ai_found_threshold = 2
        self._ai_miss_threshold = 4

        self._last_ai_frame_time = 0
        self.frame_count = 0

    @property
    def ai_enabled(self):
        return self.ai_detector.enabled

    @property
    def ai_model(self):
        return self.ai_detector.model

    @property
    def ai_frame_skip(self):
        return self.ai_detector.frame_skip

    def enable(self):
        if self.enabled:
            return
        self.enabled = True
        self._reconnect_attempts = 0
        print(f"{ts()} ✅ [{self.camera['name']}] Детектор ВКЛЮЧЕН")
        self.start()

    def disable(self):
        if not self.enabled:
            return
        self.enabled = False
        self.stop()
        print(f"{ts()} ⏹️ [{self.camera['name']}] Детектор ВЫКЛЮЧЕН")

    def start(self):
        if not self.enabled:
            return False

        rtsp_url = self.camera.get("rtsp_sub") or self.camera.get("rtsp_main")

        if self.cap:
            try:
                self.cap.release()
            except:
                pass
            self.cap = None

        try:
            for attempt in range(self._max_reconnect_attempts):
                try:
                    self.cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
                    if self.cap.isOpened():
                        break
                except Exception as e:
                    print(f"{ts()} {C_RED}⚠️ [{self.camera['name']}] Ошибка OpenCV: {e}{C_RESET}")
                    time.sleep(self._reconnect_delay)

            if not self.cap or not self.cap.isOpened():
                self.cap = None
                return False

            self.cap.set(cv2.CAP_PROP_FPS, 5)
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            self.running = True
            self.warmup_frames = 0
            self._reconnect_attempts = 0

            mode = "🤖 AI + MOG2" if self.ai_enabled else "🔍 MOG2"
            print(f"{ts()} {mode} [{self.camera['name']}] Детектор запущен (порог: {self.threshold}%)")
            return True
        except Exception as e:
            print(f"{ts()} {C_RED}❌ [{self.camera['name']}] КРИТИЧЕСКАЯ ошибка: {e}{C_RESET}")
            self.cap = None
            self.running = False
            return False

    def stop(self):
        self.running = False
        if self.cap:
            self.cap.release()
            self.cap = None

    def restart(self):
        self.stop()
        time.sleep(1)
        return self.start()

    def restart_with_config(self):
        self.stop()
        time.sleep(0.5)
        return self.start()

    def loop(self):
        """Один цикл детекции"""
        if not self.running or not self.enabled:
            return

        if self.cap is None:
            self._reconnect_attempts += 1
            if self._reconnect_attempts <= self._max_reconnect_attempts:
                self.start()
            return

        try:
            ret, frame = self.cap.read()
        except Exception as e:
            print(f"{ts()} ⚠️ [{self.camera['name']}] Ошибка чтения кадра: {e}")
            self._reconnect_attempts += 1
            if self._reconnect_attempts <= self._max_reconnect_attempts:
                time.sleep(2)
                self.start()
            else:
                self.running = False
                self.cap = None
            return

        if not ret:
            self._reconnect_attempts += 1
            if self._reconnect_attempts <= self._max_reconnect_attempts:
                time.sleep(2)
                self.start()
            else:
                self.running = False
                self.cap = None
            return

        self._reconnect_attempts = 0

        small = cv2.resize(frame, (320, 240))
        fgmask = self.fgbg.apply(small)

        # Зоны
        if self.zones:
            self._apply_zones(fgmask, frame)

        # Прогрев
        if self.warmup_frames < self.WARMUP_NEEDED:
            self.warmup_frames += 1
            if self.warmup_frames % 5 == 0:
                print(f"{ts()} 🔥 [{self.camera['name']}] Прогрев: {self.warmup_frames}/{self.WARMUP_NEEDED}")
            return

        motion_pixels = np.count_nonzero(fgmask)
        motion_percent = motion_pixels / (320 * 240) * 100

        # Защита от смены день/ночь
        if motion_percent > 80.0:
            self._handle_day_night_switch(frame, motion_percent)
            return

        # Лог MOG2
        self._log_mog2(motion_percent)

        # Основная логика
        if motion_percent > self.threshold:
            self._on_motion_detected(frame, motion_percent)
        else:
            self._on_no_motion()

    def _apply_zones(self, fgmask, frame):
        """Применяет зоны детекции"""
        mask = np.zeros((240, 320), dtype=np.uint8)
        for zone in self.zones:
            scale_x = 320 / frame.shape[1]
            scale_y = 240 / frame.shape[0]
            pts = np.array([[(int(p["x"] * scale_x), int(p["y"] * scale_y)) for p in zone["points"]]], dtype=np.int32)
            if zone["zone_type"] == "include":
                cv2.fillPoly(mask, pts, 255)
            else:
                cv2.fillPoly(mask, pts, 0)
        has_include = any(z["zone_type"] == "include" for z in self.zones)
        if has_include:
            fgmask[:] = cv2.bitwise_and(fgmask, mask)
        else:
            fgmask[:] = cv2.bitwise_and(fgmask, cv2.bitwise_not(mask))

    def _log_mog2(self, motion_percent):
        """Выводит MOG2 логи"""
        if motion_percent >= MOG2_LOG_MIN:
            now = time.time()
            if now - self._last_mog2_log >= MOG2_LOG_INTERVAL:
                self._last_mog2_log = now
                if motion_percent < self.threshold:
                    if MOG2_LOG_COLORS:
                        print(f"{ts()} {C_GRAY}👁️ [{self.camera['name']}] MOG2: {motion_percent:.1f}% (порог: {self.threshold:.1f}%){C_RESET}")
                    else:
                        print(f"{ts()} 👁️ [{self.camera['name']}] MOG2: {motion_percent:.1f}% (порог: {self.threshold:.1f}%)")
                else:
                    if MOG2_LOG_COLORS:
                        print(f"{ts()} {C_YELLOW}📊 [{self.camera['name']}] MOG2: {motion_percent:.1f}% (ПРЕВЫШЕН! {self.threshold:.1f}%){C_RESET}")
                    else:
                        print(f"{ts()} 📊 [{self.camera['name']}] MOG2: {motion_percent:.1f}% (ПРЕВЫШЕН! {self.threshold:.1f}%)")

    def _handle_day_night_switch(self, frame, motion_percent):
        """Обрабатывает смену режима день/ночь"""
        if self.ai_enabled and self.ai_model:
            try:
                ai_result, _ = self.ai_detector.detect(frame)
            except:
                ai_result = None
            if not ai_result:
                print(f"{ts()} {C_GRAY}🌙 [{self.camera['name']}] Смена режима — сброс MOG2{C_RESET}")
                self.fgbg = cv2.createBackgroundSubtractorMOG2(history=300, varThreshold=25, detectShadows=False)
                self.warmup_frames = 0
                return
        else:
            print(f"{ts()} {C_GRAY}🌙 [{self.camera['name']}] Смена режима — сброс MOG2{C_RESET}")
            self.fgbg = cv2.createBackgroundSubtractorMOG2(history=300, varThreshold=25, detectShadows=False)
            self.warmup_frames = 0

    def _on_motion_detected(self, frame, motion_percent):
        """Вызывается когда MOG2 обнаружил движение"""
        if self.ai_enabled and self.ai_model:
            self.frame_count += 1
            skip = self.ai_frame_skip if not self.motion_active else max(1, self.ai_frame_skip // 2)

            if self.frame_count % skip == 0:
                try:
                    ai_result, boxes = self.ai_detector.detect(frame)
                except:
                    ai_result, boxes = None, None

                if ai_result and boxes:
                    self._ai_found_streak += 1
                    self._ai_miss_streak = 0

                    if not self.motion_active:
                        if self._ai_found_streak >= self._ai_found_threshold:
                            self._trigger_motion(motion_percent, ai_result, frame, boxes)
                    else:
                        # Тревога активна — сбрасываем таймер остановки
                        if self.motion_end_timer:
                            self.motion_end_timer.cancel()
                            self.motion_end_timer = None
                            send_mqtt_command(self.camera['id'], 'extend_recording')

                        # ✅ Сохраняем AI-кадр не чаще 1 раза в секунду
                        now = time.time()
                        if now - self._last_ai_frame_time >= 1.0:
                            self._last_ai_frame_time = now
                            frame_time = now - 5.0  # ← СДВИГ НА 2 СЕК НАЗАД

                            # Просто сохраняем с текущим временем (без смещений!)
                            self.recording.save_ai_frame(frame, boxes)
                            self.recording.save_motion_boxes(boxes, now)

                            # Логируем обновление
                            desc = []
                            if ai_result.get('person', 0) > 0:
                                desc.append(f"👤 x{ai_result['person']}")
                            if ai_result.get('car', 0) > 0:
                                desc.append(f"🚗 x{ai_result['car']}")
                            if desc:
                                print(f"{ts()} {C_YELLOW}🎯 [{self.camera['name']}] Обновление рамок: {', '.join(desc)}{C_RESET}")
                else:
                    # AI не нашёл объекты
                    self._ai_found_streak = 0

                    if motion_percent < self.threshold:
                        self._ai_miss_streak += 1
                    else:
                        self._ai_miss_streak = 0

                    if self.motion_active:
                        if self._ai_miss_streak >= self._ai_miss_threshold:
                            if self.motion_end_timer is None:
                                total_delay = self.motion_end_delay + self.camera.get('record_post_sec', 5)
                                print(f"{ts()} {C_CYAN}⏳ [{self.camera['name']}] Объекты не найдены. Жду {total_delay} сек...{C_RESET}")
                                self.motion_end_timer = threading.Timer(total_delay, self._stop_motion)
                                self.motion_end_timer.daemon = True
                                self.motion_end_timer.start()
                    else:
                        if motion_percent > 0:
                            print(f"{ts()} {C_PURPLE}🤖 [{self.camera['name']}] Ложная тревога отфильтрована AI ({motion_percent:.1f}%){C_RESET}")
            else:
                # Пропущенный кадр — если движение очень сильное, проверяем AI
                if motion_percent > self.threshold * 3:
                    try:
                        ai_result, boxes = self.ai_detector.detect(frame)
                    except:
                        ai_result, boxes = None, None

                    if ai_result and boxes:
                        if not self.motion_active:
                            self._trigger_motion(motion_percent, ai_result, frame, boxes)
                        else:
                            now = time.time()
                            if now - self._last_ai_frame_time >= 1.0:
                                self._last_ai_frame_time = now
                                frame_time = now - 5.0  # ← СДВИГ НА 2 СЕК НАЗАД
                                self.recording.save_ai_frame(frame, boxes)
                                self.recording.save_motion_boxes(boxes, now)
        else:
            # AI выключен — просто MOG2
            try:
                self._trigger_motion(motion_percent, None)
            except:
                pass

    def _on_no_motion(self):
        """Вызывается когда MOG2 не видит движения"""
        if self.motion_active:
            if self.motion_end_timer is None:
                total_delay = self.motion_end_delay + self.camera.get('record_post_sec', 5)
                print(f"{ts()} {C_CYAN}⏳ [{self.camera['name']}] Нет движения. Жду {total_delay} сек...{C_RESET}")
                self.motion_end_timer = threading.Timer(total_delay, self._stop_motion)
                self.motion_end_timer.daemon = True
                self.motion_end_timer.start()

    def _trigger_motion(self, motion_percent, ai_result, frame=None, boxes=None):
            if self.motion_end_timer:
                self.motion_end_timer.cancel()
                self.motion_end_timer = None

            if not self.motion_active:
                self.motion_active = True
                self.motion_start_time = time.time()
                self._last_ai_frame_time = time.time()
                self.motion_boxes = []
                self.recording.reset()

                if frame is not None and boxes:
                    # ✅ СДВИГ НА 2 СЕК НАЗАД
                    frame_time = time.time() - 5.0

                    self.recording.save_alert_snapshot(frame, boxes)
                    self.recording.save_ai_frame(frame, boxes)
                    self.recording.save_motion_boxes(boxes, frame_time)

            if ai_result:
                desc = []
                if ai_result.get('person', 0) > 0:
                    desc.append(f"👤 x{ai_result['person']}")
                if ai_result.get('car', 0) > 0:
                    desc.append(f"🚗 x{ai_result['car']}")
                print(f"{ts()} {C_RED}{C_BOLD}🤖 [{self.camera['name']}] AI ТРЕВОГА! {', '.join(desc)} ({motion_percent:.1f}%){C_RESET}")
            else:
                print(f"{ts()} 📊 [{self.camera['name']}] Движение: {motion_percent:.1f}%")

            self._publish("motion_start", motion_percent, ai_result)
            result = send_mqtt_command(self.camera['id'], 'start_recording')
            print(f"{ts()} {C_BLUE}🔴 [{self.camera['name']}] Старт записи! (MQTT: {'OK' if result else 'ОШИБКА'}){C_RESET}")

    def _stop_motion(self):
        """Останавливает тревогу"""
        if self.motion_active:
            boxes_file = self.recording.save_ai_frames_json()
            if boxes_file:
                print(f"{ts()} {C_BLUE}📦 [{self.camera['name']}] JSON сохранён{C_RESET}")

            self.motion_active = False
            self._publish("motion_end", 0)
            send_mqtt_command(self.camera['id'], 'stop_recording')
            print(f"{ts()} {C_GREEN}🟢 [{self.camera['name']}] Запись остановлена{C_RESET}")
        self.motion_end_timer = None

    def _publish(self, event_type, percent, ai_result=None):
        """Публикует MQTT событие"""
        topic = f"spartan/{self.camera['id']}/motion"
        payload_dict = {
            "camera_id": self.camera["id"],
            "camera_name": self.camera["name"],
            "event": event_type,
            "percent": round(percent, 2),
            "timestamp": int(time.time())
        }
        if ai_result:
            payload_dict["ai"] = ai_result

        self.mqtt.publish(topic, json.dumps(payload_dict))

        try:
            from models.database import get_db
            with get_db() as conn:
                conn.execute(
                    "INSERT INTO events (camera_id, event_type, details) VALUES (?, ?, ?)",
                    (self.camera["id"], event_type, json.dumps(payload_dict))
                )
                conn.commit()
        except:
            pass