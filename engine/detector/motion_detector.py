"""
Основной класс MotionDetector
"""
import os
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|timeout;5000000|threads;1"
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
        self._max_reconnect_attempts = 10
        self._reconnect_delay = 3
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

        self.camera_health = True
        self.camera_check_interval = 30  # проверка каждые 30 секунд
        self.last_camera_check = time.time()
        self.camera_health_timer = None

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
        try:
            if not self.enabled:
                return False

            rtsp_url = self.camera.get("rtsp_sub") or self.camera.get("rtsp_main")

            if self.cap:
                try:
                    self.cap.release()
                except:
                    pass
                self.cap = None

            # ✅ Вместо блокирующего цикла — запускаем подключение в фоне
            self._rtsp_url = rtsp_url
            self._connecting = True
            threading.Thread(target=self._connect_rtsp, daemon=True).start()

            # Возвращаем True сразу, чтобы MQTT не блокировался
            self.running = True
            self.warmup_frames = 0
            self._reconnect_attempts = 0
            print(f"{ts()} ⏳ [{self.camera['name']}] Подключение к RTSP в фоне...")
            return True

        except Exception as e:
            print(f"{ts()} ❌ КРИТИЧЕСКАЯ ОШИБКА В start(): {e}")
            self.cap = None
            self.running = False
            return False

        def _start_camera_monitor(self):
            if self.camera_health_timer:
                self.camera_health_timer.cancel()
            self.camera_health_timer = threading.Timer(self.camera_check_interval, self._check_camera_health)
            self.camera_health_timer.daemon = True
            self.camera_health_timer.start()

        def _check_camera_health(self):
            """Проверяет здоровье камеры и перезапускает только её"""
            if not self.enabled or not self.running:
                return

            # Проверяем, читаются ли кадры
            try:
                ret, frame = self.cap.read()
                if not ret:
                    self.camera_health = False
                    print(f"{ts()} ⚠️ [{self.camera['name']}] Камера не отвечает")
                    # Если камера включена — перезапускаем только эту камеру
                    if self.enabled:
                        print(f"{ts()} 🔄 [{self.camera['name']}] Перезапуск камеры")
                        self.stop()
                        time.sleep(2)
                        self.start()
                    return
                else:
                    self.camera_health = True
            except Exception as e:
                self.camera_health = False
                print(f"{ts()} ⚠️ [{self.camera['name']}] Ошибка проверки камеры: {e}")
                if self.enabled:
                    print(f"{ts()} 🔄 [{self.camera['name']}] Перезапуск камеры")
                    self.stop()
                    time.sleep(2)
                    self.start()
            finally:
                # Перезапускаем таймер
                self._start_camera_monitor()

    def _connect_rtsp(self):
            """Подключается к RTSP в фоновом потоке"""
            for attempt in range(self._max_reconnect_attempts):
                try:
                    self.cap = cv2.VideoCapture(self._rtsp_url, cv2.CAP_FFMPEG)
                    self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    if self.cap.isOpened():
                        self.running = True
                        self._connecting = False
                        self.warmup_frames = 0
                        self._reconnect_attempts = 0
                        print(f"{ts()} ✅ [{self.camera['name']}] Детектор запущен (RTSP подключён)")
                        self._start_camera_monitor()
                        return
                except Exception as e:
                    print(f"{ts()} ⚠️ [{self.camera['name']}] Ошибка OpenCV: {e}")
                    time.sleep(self._reconnect_delay)

            print(f"{ts()} ❌ [{self.camera['name']}] Не удалось подключиться к RTSP")
            self.cap = None
            self.running = False
            self._connecting = False

    def stop(self):
        self.running = False
        self.enabled = False  # ← добавляем, чтобы отключить обработку

        if self.cap:
            try:
                self.cap.release()
            except:
                pass
            self.cap = None

        if self.camera_health_timer:
            self.camera_health_timer.cancel()
            self.camera_health_timer = None

    def restart(self):
        self.stop()
        time.sleep(1)
        return self.start()

    def restart_with_config(self):
        self.stop()
        time.sleep(0.5)
        return self.start()

    def loop(self):
        if not self.running or not self.enabled or self._connecting:
            return
        try:
            if not self.running or not self.enabled:
                return

            if self.cap is None:
                if not self.enabled:
                    return
                self._reconnect_attempts += 1
                if self._reconnect_attempts <= self._max_reconnect_attempts:
                    try:
                        self.start()  # ← ЗАЩИТИТЬ!
                    except Exception as e:
                        print(f"{ts()} ⚠️ [{self.camera['name']}] Ошибка при start(): {e}")
                        self.running = False
                else:
                    self.running = False
                return

            ret, frame = self.cap.read()
            if not ret:
                self._reconnect_attempts += 1
                if self._reconnect_attempts <= self._max_reconnect_attempts:
                    time.sleep(2)
                    try:
                        self.start()  # ← ЗАЩИТИТЬ!
                    except Exception as e:
                        print(f"{ts()} ⚠️ [{self.camera['name']}] Ошибка при start(): {e}")
                        self.running = False
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

        except Exception as e:
            print(f"{ts()} 🔴 НЕОБРАБОТАННОЕ ИСКЛЮЧЕНИЕ В loop(): {e}")
            import traceback
            traceback.print_exc()
            if self.enabled:
                try:
                    self.start()
                except:
                    pass
            else:
                print(f"{ts()} ⏸️ Детектор выключен — не перезапускаю")

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

                            # ✅ ПРИМЕНЯЕМ СДВИГ И ПРОВЕРЯЕМ ГАЛОЧКУ
                            if self.ai_detector.boxes_enabled:
                                shift = self.ai_detector.boxes_shift
                                frame_time = now + shift
                                self.recording.save_ai_frame(frame, boxes)
                            else:
                                frame_time = now

                            self.recording.save_motion_boxes(boxes, frame_time)

                            # Логируем обновление (только если рамки включены)
                            if self.ai_detector.boxes_enabled:
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
                            try:
                                from models.database import get_db
                                with get_db() as conn:
                                    conn.execute(
                                        "INSERT INTO events (camera_id, event_type, details) VALUES (?, ?, ?)",
                                        (self.camera["id"], "motion_filtered", json.dumps({
                                            "percent": round(motion_percent, 2),
                                            "filtered": True,
                                            "timestamp": int(time.time())
                                        }))
                                    )
                                    conn.commit()
                            except:
                                pass
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

                                if self.ai_detector.boxes_enabled:
                                    shift = self.ai_detector.boxes_shift
                                    frame_time = now + shift
                                    self.recording.save_ai_frame(frame, boxes)
                                else:
                                    frame_time = now

                                self.recording.save_motion_boxes(boxes, frame_time)
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
            self.motion_start_time = time.time()  # ← Время когда MOG2 сработал!
            self._last_ai_frame_time = time.time()
            self.motion_boxes = []
            self.recording.reset()

            # ✅ ПЕРВЫЙ AI-КАДР СРАЗУ ПРИ СТАРТЕ (с учётом сдвига и галочки)
            if frame is not None and boxes:
                # Проверяем, включены ли рамки
                if self.ai_detector.boxes_enabled:
                    # Применяем сдвиг из настроек
                    shift = self.ai_detector.boxes_shift
                    frame_time = time.time() + shift
                else:
                    frame_time = time.time()

                self.recording.save_alert_snapshot(frame, boxes)

                # AI-кадр сохраняем только если рамки включены
                if self.ai_detector.boxes_enabled:
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
            result = send_mqtt_command(self.camera['id'], 'start_recording', {
                                 'motion_start_time': self.motion_start_time
                             })
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

    def _should_detect(self):
        """Нужно ли сейчас детектировать?"""
        mode = self.camera.get('record_mode', 'motion_ai')

        if mode in ('motion_ai', 'continuous_noai'):
            return True  # Всегда детектим

        elif mode in ('schedule_noai', 'schedule_ai'):
            return self._is_in_schedule()  # Только по расписанию

        return True

    def _should_use_ai(self):
        """Нужно ли использовать AI?"""
        mode = self.camera.get('record_mode', 'motion_ai')
        return mode in ('motion_ai', 'schedule_ai')  # AI только в этих режимах

    def _is_in_schedule(self):
        """Проверяет, находимся ли мы в периоде расписания"""
        schedule = self.camera.get('record_schedule', {})
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