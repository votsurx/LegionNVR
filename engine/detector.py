"""
Legion NVR - Motion Detector
Запуск: python engine/detector.py
Читает камеры из БД, детектит движение, публикует MQTT
"""
import os
os.system('')  # Включает ANSI-цвета в Windows

import sys
import io
import cv2
import numpy as np
import paho.mqtt.client as mqtt
import json
import time
import sys
import threading
import traceback

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.stdout.reconfigure(line_buffering=True)

from models.database import get_db


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


MQTT_BROKER = "127.0.0.1"
MQTT_PORT = 1883
# ════════════════════════════════════════════════════════════
# НАСТРОЙКИ ВЫВОДА MOG2
# ════════════════════════════════════════════════════════════
MOG2_LOG_MIN = 5.0          # Минимальный % для вывода (меньше — не показываем)
MOG2_LOG_COLORS = True      # Цветной вывод (False — если терминал не поддерживает)
MOG2_LOG_INTERVAL = 0.5     # Минимальный интервал между логами (сек), чтобы не спамить

# ════════════════════════════════════════════════════════════
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ (вне класса!)
# ════════════════════════════════════════════════════════════

def ts():
    """Возвращает цветную временную метку [HH:MM:SS]"""
    return f"{C_CYAN}[{time.strftime('%H:%M:%S')}]{C_RESET}"

def send_mqtt_command(camera_id, action, params=None):
    """Отправляет MQTT команду"""
    try:
        client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
        client.connect("127.0.0.1", 1883, 5)
        payload = {
            'action': action,
            'camera_id': camera_id,
            'timestamp': int(time.time())
        }
        if params:
            payload.update(params)
        client.publish(f"spartan/{camera_id}/cmd", json.dumps(payload))
        client.disconnect()
        return True
    except Exception as e:
        print(f"{ts()} ❌ MQTT ошибка: {e}")
        return False


def load_cameras():
    """Загружает камеры из БД с включённым детектором"""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM cameras WHERE enabled=1 AND motion_enabled=1"
        ).fetchall()
    return [dict(r) for r in rows]


def on_cmd(client, userdata, msg):
    """Обработчик MQTT команд"""
    try:
        data = json.loads(msg.payload.decode())
        action = data.get("action")
        cam_id = data.get("camera_id")

        if action == "reload_config":
            print(f"{ts()} 📡 [CMD] Перезагрузка конфига для камеры {cam_id}")
            with get_db() as conn:
                cam = conn.execute("SELECT * FROM cameras WHERE id=?", (cam_id,)).fetchone()

            if cam:
                cam_dict = dict(cam)

                # ✅ ИЩЕМ КАМЕРУ В СПИСКЕ АКТИВНЫХ ДЕТЕКТОРОВ
                found = False
                for det in userdata["detectors"]:
                    if str(det.camera["id"]) == str(cam_id):
                        found = True

                        # ✅ ОБНОВЛЯЕМ НАСТРОЙКИ
                        print(f"{ts()} ⏹️ [{det.camera['name']}] Перезагружаю настройки...")
                        det.stop()

                        det.camera = cam_dict
                        det.threshold = cam_dict.get("motion_threshold", 2.0)
                        det.cooldown = cam_dict.get("motion_cooldown", 5)
                        det.enabled = cam_dict.get("enabled", True) and cam_dict.get("motion_enabled", True)
                        try:
                            det._load_zones()
                        except Exception as e:
                            print(f"{ts()} {C_RED}❌ [{det.camera['name']}] Ошибка загрузки зон: {e}{C_RESET}")
                            import traceback
                            traceback.print_exc()
                        det.warmup_frames = 0

                        # ✅ ЗАПУСКАЕМ ЕСЛИ НУЖНО
                        if det.enabled:
                            print(f"{ts()} ▶️ [{det.camera['name']}] Запускаю детектор...")
                            try:
                                result = det.start()
                                if result:
                                    print(f"{ts()} ✅ [{det.camera['name']}] Детектор запущен")
                                    print(f"{ts()} ✅ [{det.camera['name']}] Порог: {det.threshold}%, Зон: {len(det.zones)}, Cooldown: {det.cooldown} сек")
                                else:
                                    print(f"{ts()} ❌ [{det.camera['name']}] Не удалось запустить детектор")
                            except Exception as e:
                                print(f"{ts()} ❌ [{det.camera['name']}] ОШИБКА запуска: {e}")
                                traceback.print_exc()
                        else:
                            print(f"{ts()} ⏸️ [{det.camera['name']}] Детектор отключён (motion_enabled=0)")

                # ✅ ЕСЛИ НЕ НАШЛИ — СОЗДАЁМ НОВЫЙ ДЕТЕКТОР
                if not found:
                    print(f"{ts()} 🆕 Камера {cam_id} не найдена в активных — создаю новый детектор")
                    if cam_dict.get("enabled") and cam_dict.get("motion_enabled"):
                        # Получаем MQTT клиент из первого детектора или создаём новый
                        mqtt_client = userdata.get("mqtt_client", client)
                        det = MotionDetector(cam_dict, mqtt_client)
                        if det.start():
                            userdata["detectors"].append(det)
                            print(f"{ts()} ✅ [{det.camera['name']}] Детектор создан и запущен")
                        else:
                            print(f"{ts()} ❌ [{cam_dict['name']}] Не удалось запустить детектор")
                    else:
                        print(f"{ts()} ⏸️ Камера {cam_id} отключена или детектор выключен — пропускаю")

        elif action == "ping":
            # Ответ на пинг от Health Monitor
            mqtt_client = userdata.get("mqtt_client", client)
            mqtt_client.publish("spartan/detector/pong", json.dumps({
                "status": "alive",
                "cameras": len(userdata.get("detectors", [])),
                "timestamp": int(time.time())
            }))

        elif action == "start_detector":
            print(f"{ts()} ▶️ [CMD] Запуск детектора для камеры {cam_id}")
            with get_db() as conn:
                cam = conn.execute("SELECT * FROM cameras WHERE id=?", (cam_id,)).fetchone()

            if cam:
                cam_dict = dict(cam)

                # Ищем или создаём
                found = False
                for det in userdata["detectors"]:
                    if str(det.camera["id"]) == str(cam_id):
                        found = True
                        det.camera = cam_dict
                        det.threshold = cam_dict.get("motion_threshold", 2.0)
                        det.cooldown = cam_dict.get("motion_cooldown", 5)
                        det._load_zones()
                        det.enabled = True
                        det.warmup_frames = 0
                        det.start()
                        print(f"{ts()} ✅ [{det.camera['name']}] Детектор запущен")
                        break

                if not found:
                    print(f"{ts()} 🆕 Создаю детектор для камеры {cam_id}")
                    mqtt_client = userdata.get("mqtt_client", client)
                    det = MotionDetector(cam_dict, mqtt_client)
                    if det.start():
                        userdata["detectors"].append(det)

        elif action == "stop_detector":
            print(f"{ts()} ⏹️ [CMD] Остановка детектора для камеры {cam_id}")
            for det in userdata["detectors"]:
                if str(det.camera["id"]) == str(cam_id):
                    det.enabled = False
                    det.stop()
                    print(f"{ts()} ⏹️ [{det.camera['name']}] Детектор остановлен")
                    break

    except Exception as e:
        print(f"{ts()} ⚠️ [CMD] Ошибка: {e}")


# ════════════════════════════════════════════════════════════
# КЛАСС ДЕТЕКТОРА
# ════════════════════════════════════════════════════════════

class MotionDetector:
    def __init__(self, camera, mqtt_client):
        self.camera = camera
        self.mqtt = mqtt_client
        self.cap = None
        self.fgbg = cv2.createBackgroundSubtractorMOG2(
            history=300,
            varThreshold=25,
            detectShadows=False
        )
        self.motion_active = False
        self.last_motion_time = 0
        self.running = False
        self.enabled = camera.get("enabled", True)

        self.threshold = camera.get("motion_threshold", 2.0)
        self.cooldown = camera.get("motion_cooldown", 5)
        self.log_min_threshold = 5.0

        self.zones = []
        self.motion_boxes = []
        self._load_zones()

        self.motion_end_delay = camera.get("motion_end_delay", 2.0)
        self.motion_end_time = None
        self.motion_end_timer = None

        self.warmup_frames = 0
        self.WARMUP_NEEDED = 25
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 5
        self._reconnect_delay = 2

        # 🤖 AI НАСТРОЙКИ
        self.ai_enabled = camera.get("ai_enabled", False)
        self.ai_model = None
        self.ai_classes = camera.get("ai_classes", [0])  # 0=человек, 2=машина
        self.ai_confidence = camera.get("ai_confidence", 0.5)
        self.ai_frame_skip = camera.get("ai_frame_skip", 5)  # каждый 5-й кадр
        self.frame_count = 0
        self._last_mog2_log = 0
        self._ai_found_streak = 0
        self._ai_miss_streak = 0
        self._ai_found_threshold = 2   # Сколько находок подряд для сброса таймера
        self._ai_miss_threshold = 4    # ← ВОТ ЭТО ДОБАВЬ!

        if self.ai_enabled:
            self._init_ai()

    def _init_ai(self):
        """Инициализация YOLO"""
        try:
            from ultralytics import YOLO
            print(f"{ts()} 🤖 [{self.camera['name']}] Загружаю YOLOv8n...")
            self.ai_model = YOLO('yolov8n.pt')

            # ✅ ПАРСИМ ai_classes (может быть строка из БД)
            if isinstance(self.ai_classes, str):
                import json
                try:
                    self.ai_classes = json.loads(self.ai_classes)
                except:
                    self.ai_classes = [0]  # По умолчанию — человек

            print(f"{ts()} ✅ [{self.camera['name']}] YOLOv8n загружен! Классы: {self.ai_classes}")
        except Exception as e:
            print(f"{ts()} ❌ [{self.camera['name']}] Ошибка загрузки YOLO: {e}")
            self.ai_enabled = False

    def _load_zones(self):
        """Загружает зоны детекции из БД"""
        try:
            with get_db() as conn:
                rows = conn.execute(
                    "SELECT * FROM detection_zones WHERE camera_id=? AND enabled=1",
                    (self.camera["id"],)
                ).fetchall()

            self.zones = []
            for row in rows:
                zone = dict(row)
                try:
                    zone["points"] = json.loads(zone["points_json"])
                    self.zones.append(zone)
                except Exception as e:
                    print(f"{ts()} {C_RED}⚠️ [{self.camera['name']}] Ошибка парсинга зоны {zone.get('id')}: {e}{C_RESET}")

            if self.zones:
                print(f"{ts()} 🎯 [{self.camera['name']}] Загружено зон: {len(self.zones)}")
            else:
                print(f"{ts()} 🎯 [{self.camera['name']}] Зоны не настроены")
        except Exception as e:
            print(f"{ts()} {C_RED}⚠️ [{self.camera['name']}] Ошибка загрузки зон: {e}{C_RESET}")
            import traceback
            traceback.print_exc()
            self.zones = []

    def enable(self):
        """Включает детектор"""
        if self.enabled:
            return
        self.enabled = True
        self._reconnect_attempts = 0
        print(f"{ts()} ✅ [{self.camera['name']}] Детектор ВКЛЮЧЕН")
        self.start()

    def disable(self):
        """Выключает детектор"""
        if not self.enabled:
            return
        self.enabled = False
        self.stop()
        print(f"{ts()} ⏹️ [{self.camera['name']}] Детектор ВЫКЛЮЧЕН")

    def start(self):
        """Запускает детектор"""
        if not self.enabled:
            print(f"{ts()} ⏸️ [{self.camera['name']}] Камера отключена, детектор не запущен")
            return False

        rtsp_url = self.camera.get("rtsp_sub") or self.camera.get("rtsp_main")

        # Закрываем старый кап если есть
        if self.cap:
            try:
                self.cap.release()
            except:
                pass
            self.cap = None

        # ✅ ЗАЩИТА ОТ ПАДЕНИЯ OPENCV
        try:
            for attempt in range(self._max_reconnect_attempts):
                try:
                    self.cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
                    if self.cap.isOpened():
                        break
                except Exception as e:
                    print(f"{ts()} {C_RED}⚠️ [{self.camera['name']}] Ошибка OpenCV (попытка {attempt+1}): {e}{C_RESET}")
                    time.sleep(self._reconnect_delay)

                print(f"{ts()} {C_YELLOW}⚠️ [{self.camera['name']}] Попытка {attempt+1}/{self._max_reconnect_attempts} подключиться...{C_RESET}")
                time.sleep(self._reconnect_delay)

            if not self.cap or not self.cap.isOpened():
                print(f"{ts()} {C_RED}❌ [{self.camera['name']}] Не могу открыть RTSP: {rtsp_url}{C_RESET}")
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
            print(f"{ts()} {C_RED}❌ [{self.camera['name']}] КРИТИЧЕСКАЯ ошибка запуска: {e}{C_RESET}")
            self.cap = None
            self.running = False
            return False

    def stop(self):
        """Останавливает детектор"""
        self.running = False
        if self.cap:
            self.cap.release()
            self.cap = None

    def restart(self):
        """Перезапускает детектор"""
        self.stop()
        time.sleep(1)
        return self.start()

    def restart_with_config(self):
        """Перезапускает детектор с новыми настройками"""
        print(f"{ts()} 🔄 [{self.camera['name']}] Перезапуск с новыми настройками...")
        self.stop()
        time.sleep(0.5)
        self.start()

    def loop(self):
        """Один цикл детекции — вызывается из main() для каждого кадра"""

        # ════════════════════════════════════════════════════
        # ПРОВЕРКА: ДЕТЕКТОР АКТИВЕН?
        # ════════════════════════════════════════════════════
        if not self.running or not self.enabled:
            # Детектор остановлен или камера выключена — выходим
            return

        # ════════════════════════════════════════════════════
        # ПРОВЕРКА: RTSP-ПОТОК ОТКРЫТ?
        # ════════════════════════════════════════════════════
        if self.cap is None:
            # cap = None значит поток не открыт (после ошибки)
            self._reconnect_attempts += 1              # Увеличиваем счётчик попыток
            if self._reconnect_attempts <= self._max_reconnect_attempts:  # 5 попыток
                self.start()                           # Пробуем переподключиться
            return                                     # Выходим — в этом цикле кадра нет

        # ════════════════════════════════════════════════════
        # ЧТЕНИЕ КАДРА ИЗ RTSP-ПОТОКА
        # ════════════════════════════════════════════════════
        try:
            ret, frame = self.cap.read()               # ret=True если кадр получен, frame — изображение
        except Exception as e:
            # Ошибка OpenCV (C++ exception, битый пакет)
            print(f"{ts()} ⚠️ [{self.camera['name']}] Ошибка чтения кадра: {e}")
            self._reconnect_attempts += 1
            if self._reconnect_attempts <= self._max_reconnect_attempts:
                time.sleep(2)                          # Ждём 2 секунды перед переподключением
                self.start()                           # Перезапускаем VideoCapture
            else:
                self.running = False                   # 5 ошибок подряд — останавливаем детектор
                self.cap.release()                     # Освобождаем RTSP
                self.cap = None
                print(f"{ts()} ❌ [{self.camera['name']}] Слишком много ошибок, детектор остановлен")
            return

        # ════════════════════════════════════════════════════
        # ПРОВЕРКА: КАДР ПУСТОЙ? (ret=False)
        # ════════════════════════════════════════════════════
        if not ret:
            # Кадр не получен (потеря потока, камера отключилась)
            self._reconnect_attempts += 1
            if self._reconnect_attempts <= self._max_reconnect_attempts:
                print(f"{ts()} ⚠️ [{self.camera['name']}] Потеря потока, переподключение...")
                time.sleep(2)
                self.start()
            else:
                self.running = False
                self.cap.release()
                self.cap = None
                print(f"{ts()} ❌ [{self.camera['name']}] Потеря потока, детектор остановлен")
            return

        # ════════════════════════════════════════════════════
        # КАДР УСПЕШНО ПОЛУЧЕН — СБРАСЫВАЕМ СЧЁТЧИК ОШИБОК
        # ════════════════════════════════════════════════════
        self._reconnect_attempts = 0

        # ════════════════════════════════════════════════════
        # ПРЕДОБРАБОТКА: СЖАТИЕ ДО 320×240
        # ════════════════════════════════════════════════════
        small = cv2.resize(frame, (320, 240))          # Уменьшаем для скорости обработки

        # ════════════════════════════════════════════════════
        # MOG2: ВЫЧИТАНИЕ ФОНА → МАСКА ДВИЖЕНИЯ
        # ════════════════════════════════════════════════════
        fgmask = self.fgbg.apply(small)                # Белые пиксели = движение, чёрные = фон

        # ════════════════════════════════════════════════════
        # ПРИМЕНЕНИЕ ЗОН ДЕТЕКЦИИ (если настроены)
        # ════════════════════════════════════════════════════
        if self.zones:
            mask = np.zeros((240, 320), dtype=np.uint8)  # Пустая маска

            # Проходим по всем зонам
            for zone in self.zones:
                # Масштабируем координаты зоны под 320×240
                scale_x = 320 / frame.shape[1]
                scale_y = 240 / frame.shape[0]
                pts = np.array([[(int(p["x"] * scale_x), int(p["y"] * scale_y)) for p in zone["points"]]], dtype=np.int32)

                if zone["zone_type"] == "include":
                    # Include-зона: закрашиваем белым (разрешаем детекцию)
                    cv2.fillPoly(mask, pts, 255)
                else:
                    # Exclude-зона: закрашиваем чёрным (запрещаем детекцию)
                    cv2.fillPoly(mask, pts, 0)

            # Проверяем, есть ли include-зоны
            has_include = any(z["zone_type"] == "include" for z in self.zones)

            if has_include:
                # Include-зоны есть → детектим ТОЛЬКО внутри них
                fgmask = cv2.bitwise_and(fgmask, mask)
            else:
                # Только exclude-зоны → инвертируем маску (всё кроме exclude)
                exclude_mask = cv2.bitwise_not(mask)
                fgmask = cv2.bitwise_and(fgmask, exclude_mask)

        # ════════════════════════════════════════════════════
        # ПРОГРЕВ MOG2 (первые 25 кадров)
        # ════════════════════════════════════════════════════
        if self.warmup_frames < self.WARMUP_NEEDED:    # WARMUP_NEEDED = 25
            self.warmup_frames += 1
            if self.warmup_frames % 5 == 0:            # Лог каждые 5 кадров
                print(f"{ts()} 🔥 [{self.camera['name']}] Прогрев: {self.warmup_frames}/{self.WARMUP_NEEDED}")
            return                                     # Выходим — во время прогрева движение не анализируем

        # ════════════════════════════════════════════════════
        # ПОДСЧЁТ ПРОЦЕНТА ДВИЖЕНИЯ
        # ════════════════════════════════════════════════════
        motion_pixels = np.count_nonzero(fgmask)        # Считаем белые пиксели (движение)
        motion_percent = motion_pixels / (320 * 240) * 100  # Процент от кадра

        # ✅ ЗАЩИТА ОТ СМЕНЫ РЕЖИМА ДЕНЬ/НОЧЬ (AI-контроль)
        if motion_percent > 80.0:
            if self.ai_enabled and self.ai_model:
                try:
                    ai_result, _ = self._ai_detect(frame)
                except:
                    ai_result = None

                if not ai_result:
                    # AI не нашёл объектов — это смена режима
                    print(f"{ts()} {C_GRAY}🌙 [{self.camera['name']}] Смена режима день/ночь (MOG2: {motion_percent:.1f}%, AI: пусто) — сброс MOG2{C_RESET}")
                    self.fgbg = cv2.createBackgroundSubtractorMOG2(history=300, varThreshold=25, detectShadows=False)
                    self.warmup_frames = 0
                    return
                else:
                    # AI нашёл объекты — это реальное движение!
                    print(f"{ts()} {C_YELLOW}⚠️ [{self.camera['name']}] Высокое движение + AI нашёл объекты — продолжаем{C_RESET}")
            else:
                # AI выключен — используем старый метод (просто сбрасываем MOG2)
                print(f"{ts()} {C_GRAY}🌙 [{self.camera['name']}] Смена режима (MOG2: {motion_percent:.1f}%) — сброс MOG2{C_RESET}")
                self.fgbg = cv2.createBackgroundSubtractorMOG2(history=300, varThreshold=25, detectShadows=False)
                self.warmup_frames = 0
                return

        # ════════════════════════════════════════════════════
        # ВЫВОД MOG2-ЛОГОВ В КОНСОЛЬ (с фильтром и интервалом)
        # ════════════════════════════════════════════════════
        if motion_percent >= MOG2_LOG_MIN:             # MOG2_LOG_MIN = 5.0% — отсекаем мусор
            now = time.time()
            if now - self._last_mog2_log >= MOG2_LOG_INTERVAL:  # Интервал 0.5 сек (не спамим)
                self._last_mog2_log = now

                if motion_percent < self.threshold:
                    # Движение ниже порога — серый лог
                    if MOG2_LOG_COLORS:
                        print(f"{ts()} {C_GRAY}👁️ [{self.camera['name']}] MOG2: {motion_percent:.1f}% (порог: {self.threshold:.1f}%){C_RESET}")
                    else:
                        print(f"{ts()} 👁️ [{self.camera['name']}] MOG2: {motion_percent:.1f}% (порог: {self.threshold:.1f}%)")
                else:
                    # Движение выше порога — жёлтый лог
                    if MOG2_LOG_COLORS:
                        print(f"{ts()} {C_YELLOW}📊 [{self.camera['name']}] MOG2: {motion_percent:.1f}% (ПРЕВЫШЕН! {self.threshold:.1f}%){C_RESET}")
                    else:
                        print(f"{ts()} 📊 [{self.camera['name']}] MOG2: {motion_percent:.1f}% (ПРЕВЫШЕН! {self.threshold:.1f}%)")

        # ════════════════════════════════════════════════════
        # ОСНОВНАЯ ЛОГИКА: ПРЕВЫШЕН ПОРОГ MOG2?
        # ════════════════════════════════════════════════════
        if motion_percent > self.threshold:

            # ════════════════════════════════════════════════
            # AI ВКЛЮЧЕН? → ЗАПУСКАЕМ YOLO ДЛЯ ПРОВЕРКИ
            # ════════════════════════════════════════════════
            if self.ai_enabled and self.ai_model:
                self.frame_count += 1

                # Во время тревоги проверяем AI чаще (skip меньше)
                # Без тревоги: каждый 5-й кадр, с тревогой: каждый 2-й
                skip = self.ai_frame_skip if not self.motion_active else max(1, self.ai_frame_skip // 2)

                if self.frame_count % skip == 0:
                    # Этот кадр проверяем AI
                    try:
                        ai_result, boxes = self._ai_detect(frame)  # YOLO ищет объекты
                    except Exception as e:
                        print(f"{ts()} {C_RED}❌ [{self.camera['name']}] Ошибка AI: {e}{C_RESET}")
                        ai_result, boxes = None, None

                    if ai_result and boxes:
                        # ✅ AI НАШЁЛ ОБЪЕКТЫ (человек/машина)
                        if not self.motion_active:
                            # Тревога ещё не активна → СТАРТ ЗАПИСИ
                            self._trigger_motion(motion_percent, ai_result, frame, boxes)
                        else:
                            # Тревога уже активна → СОХРАНЯЕМ КООРДИНАТЫ ДЛЯ РАМОК
                            self._save_motion_boxes(boxes, time.time())
                            # Логируем обновление
                            desc = []
                            if ai_result.get('person', 0) > 0:
                                desc.append(f"👤 x{ai_result['person']}")
                            if ai_result.get('car', 0) > 0:
                                desc.append(f"🚗 x{ai_result['car']}")
                            if desc:
                                print(f"{ts()} {C_YELLOW}🎯 [{self.camera['name']}] Обновление рамок: {', '.join(desc)}{C_RESET}")
                    else:
                        # ❌ AI НЕ НАШЁЛ ОБЪЕКТЫ
                        self._ai_found_streak = 0

                        # ✅ Увеличиваем счётчик пропусков ТОЛЬКО если MOG2 тоже считает, что движения нет
                        if motion_percent < self.threshold:
                            self._ai_miss_streak += 1
                        else:
                            # MOG2 видит движение — сбрасываем счётчик пропусков
                            self._ai_miss_streak = 0

                        if self.motion_active:
                            if self._ai_miss_streak >= self._ai_miss_threshold:
                                if self.motion_end_timer is None:
                                    total_delay = self.motion_end_delay + self.camera.get('record_post_sec', 5)
                                    print(f"{ts()} {C_CYAN}⏳ [{self.camera['name']}] Объекты не найдены и MOG2 успокоился ({self._ai_miss_streak}x). Жду {total_delay} сек...{C_RESET}")
                                    self.motion_end_timer = threading.Timer(total_delay, self._stop_motion)
                                    self.motion_end_timer.daemon = True
                                    self.motion_end_timer.start()
                        else:
                            if motion_percent > 0:
                                print(f"{ts()} {C_PURPLE}🤖 [{self.camera['name']}] Ложная тревога отфильтрована AI ({motion_percent:.1f}%){C_RESET}")
                else:
                    # Кадр пропущен (не проверяем AI для экономии CPU)
                    # НО если движение ОЧЕНЬ сильное (3× порог) — проверяем вне очереди
                    if motion_percent > self.threshold * 3:
                        try:
                            ai_result, boxes = self._ai_detect(frame)
                        except Exception as e:
                            ai_result, boxes = None, None

                        if ai_result and boxes:
                            if not self.motion_active:
                                self._trigger_motion(motion_percent, ai_result, frame, boxes)
                            else:
                                self._save_motion_boxes(boxes, time.time())
            else:
                # ════════════════════════════════════════════
                # AI ВЫКЛЮЧЕН — РАБОТАЕМ ТОЛЬКО ПО MOG2
                # ════════════════════════════════════════════
                try:
                    self._trigger_motion(motion_percent, None)  # Старт записи без AI
                except Exception as e:
                    print(f"{ts()} {C_RED}❌ [{self.camera['name']}] Ошибка trigger: {e}{C_RESET}")
        else:
            # ════════════════════════════════════════════════
            # MOG2 НИЖЕ ПОРОГА — ДВИЖЕНИЯ НЕТ
            # ════════════════════════════════════════════════
            if self.motion_active:
                # Тревога активна — ЗАПУСКАЕМ ТАЙМЕР ОСТАНОВКИ
                if self.motion_end_timer is None:
                    # Таймер ещё не запущен
                    total_delay = self.motion_end_delay + self.camera.get('record_post_sec', 5)
                    # total_delay = 2 сек (пауза) + 5 сек (постзапись) = 7 сек
                    print(f"{ts()} {C_CYAN}⏳ [{self.camera['name']}] Нет движения. Жду {total_delay} сек (пауза {self.motion_end_delay}с + пост {self.camera.get('record_post_sec', 5)}с)...{C_RESET}")

                    # Создаём таймер, который через total_delay вызовет _stop_motion
                    self.motion_end_timer = threading.Timer(total_delay, self._stop_motion)
                    self.motion_end_timer.daemon = True   # Таймер умрёт при завершении программы
                    self.motion_end_timer.start()         # ЗАПУСК ТАЙМЕРА

    def _ai_detect(self, frame):
        """
        Запускает YOLO на кадре.
        Возвращает (ai_result, boxes) — словарь с типами и список рамок.
        """
        try:
            # ════════════════════════════════════════════════
            # ЗАПУСК YOLO НА КАДРЕ
            # ════════════════════════════════════════════════
            results = self.ai_model(
                frame,              # Кадр в оригинальном разрешении (не 320×240!)
                verbose=False,      # Не печатать технический лог YOLO
                conf=self.ai_confidence  # Порог уверенности (0.4 = 40%)
            )

            # results[0] — первый (и единственный) результат для одного кадра
            # .boxes — найденные объекты (bounding boxes)
            boxes = results[0].boxes

            # ════════════════════════════════════════════════
            # ПРОВЕРКА: НАЙДЕНЫ ЛИ ОБЪЕКТЫ?
            # ════════════════════════════════════════════════
            if boxes is None or len(boxes) == 0:
                # YOLO ничего не нашёл — возвращаем два None
                return None, None

            # ════════════════════════════════════════════════
            # ИНИЦИАЛИЗАЦИЯ СЛОВАРЯ РЕЗУЛЬТАТОВ
            # ════════════════════════════════════════════════
            detected = {
                'person': 0,        # Количество людей
                'car': 0,           # Количество машин
                'motorcycle': 0,    # Количество мотоциклов
                'dog': 0,           # Количество собак
                'cat': 0,           # Количество кошек
                'total': 0          # Общее количество найденных объектов
            }
            box_list = []           # Список координат для рисования рамок

            # ════════════════════════════════════════════════
            # ПАРСИМ ai_classes (список разрешённых классов)
            # ════════════════════════════════════════════════
            ai_classes = self.ai_classes

            if isinstance(ai_classes, str):
                # Если ai_classes пришёл из БД как строка '[0, 2]' → парсим в список [0, 2]
                import json
                ai_classes = json.loads(ai_classes)

            # ════════════════════════════════════════════════
            # ОБРАБОТКА КАЖДОГО НАЙДЕННОГО ОБЪЕКТА
            # ════════════════════════════════════════════════
            for box in boxes:
                # ─── Извлекаем класс объекта ───
                cls = int(box.cls[0])       # 0=человек, 2=машина, 3=мотоцикл, 16=собака, 17=кошка

                # ─── Извлекаем уверенность ───
                conf = float(box.conf[0])   # 0.0 - 1.0 (например 0.85 = 85% уверенности)

                # ════════════════════════════════════════════
                # ФИЛЬТРАЦИЯ: ПРОПУСКАЕМ НЕРАЗРЕШЁННЫЕ КЛАССЫ
                # ════════════════════════════════════════════
                if cls not in ai_classes:
                    # Например: ai_classes=[0] (только люди), а YOLO нашёл машину (класс 2)
                    # → пропускаем этот объект
                    continue

                # ─── Извлекаем координаты рамки ───
                x1, y1, x2, y2 = box.xyxy[0].tolist()  # Левый верхний и правый нижний углы

                # ─── Сохраняем для рисования рамок ───
                box_list.append({
                    'class': cls,           # Класс объекта (0, 2, 3, 16, 17)
                    'confidence': conf,     # Уверенность (0.0 - 1.0)
                    'x1': int(x1),          # Левая граница рамки
                    'y1': int(y1),          # Верхняя граница рамки
                    'x2': int(x2),          # Правая граница рамки
                    'y2': int(y2)           # Нижняя граница рамки
                })

                # ════════════════════════════════════════════
                # ПОДСЧЁТ ПО ТИПАМ ОБЪЕКТОВ
                # ════════════════════════════════════════════
                if cls == 0:
                    detected['person'] += 1        # Человек
                elif cls == 2:
                    detected['car'] += 1           # Машина
                elif cls == 3:
                    detected['motorcycle'] += 1    # Мотоцикл
                elif cls == 16:
                    detected['dog'] += 1           # Собака
                elif cls == 17:
                    detected['cat'] += 1           # Кошка

                detected['total'] += 1             # Общий счётчик

            # ════════════════════════════════════════════════
            # ВОЗВРАЩАЕМ РЕЗУЛЬТАТ
            # ════════════════════════════════════════════════
            if detected['total'] > 0:
                # Нашли объекты → возвращаем словарь с типами и список координат
                return detected, box_list

            # Нашли объекты, но все отфильтрованы (не в ai_classes) → считаем что ничего нет
            return None, None

        except Exception as e:
            # Любая ошибка (YOLO не загружен, битый кадр, etc.)
            print(f"⚠️ [{self.camera['name']}] Ошибка YOLO: {e}")
            return None, None

    def _draw_boxes(self, frame, boxes):
        """Рисует рамки и подписи на кадре (только разрешённые классы)"""
        if not boxes:
            return frame

        # ✅ ПОЛУЧАЕМ РАЗРЕШЁННЫЕ КЛАССЫ
        ai_classes = self.ai_classes
        if isinstance(ai_classes, str):
            import json
            ai_classes = json.loads(ai_classes)

        # Цвета для разных классов
        colors = {
            0: (0, 255, 0),    # Человек — зелёный
            2: (255, 0, 0),    # Машина — синий
            3: (0, 255, 255),  # Мотоцикл — жёлтый
            16: (255, 0, 255), # Собака — фиолетовый
            17: (0, 165, 255), # Кошка — оранжевый
        }

        names = {
            0: 'Человек',
            2: 'Машина',
            3: 'Мотоцикл',
            16: 'Собака',
            17: 'Кошка',
        }

        for box in boxes:
            cls = box['class']

            # ✅ ПРОПУСКАЕМ НЕРАЗРЕШЁННЫЕ КЛАССЫ
            if cls not in ai_classes:
                continue

            conf = box['confidence']
            x1, y1, x2, y2 = box['x1'], box['y1'], box['x2'], box['y2']

            color = colors.get(cls, (255, 255, 255))
            name = names.get(cls, f'Объект {cls}')

            # Рамка (контур)
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)

            # Подпись
            label = f'{name} {conf*100:.0f}%'
            (label_w, label_h), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)

            # Фон подписи
            cv2.rectangle(frame, (x1, y1 - label_h - 10), (x1 + label_w + 10, y1), color, -1)

            # Текст
            cv2.putText(frame, label, (x1 + 5, y1 - 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2)

        return frame

    def _save_alert_snapshot(self, frame, boxes):
        """Сохраняет скриншот с рамками при тревоге"""
        try:
            timestamp = time.strftime("%Y-%m-%d_%H-%M-%S")
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            snap_dir = os.path.join(base_dir, "snapshots", str(self.camera["id"]))
            os.makedirs(snap_dir, exist_ok=True)

            filename = f"{timestamp}_alert.jpg"
            filepath = os.path.join(snap_dir, filename)

            # Рисуем рамки на копии кадра
            frame_with_boxes = self._draw_boxes(frame.copy(), boxes)
            cv2.imwrite(filepath, frame_with_boxes, [cv2.IMWRITE_JPEG_QUALITY, 85])

            print(f"{ts()} 📸 [{self.camera['name']}] Скриншот сохранён: {filename}")
            return filepath
        except Exception as e:
            print(f"{ts()} ❌ [{self.camera['name']}] Ошибка скриншота: {e}")
            return None

    def _save_boxes_data(self, boxes, motion_percent):
        """Сохраняет координаты рамок для AI-ролика"""
        try:
            timestamp = time.strftime("%Y-%m-%d_%H-%M-%S")
            boxes_dir = os.path.join("snapshots", str(self.camera["id"]), "boxes")
            os.makedirs(boxes_dir, exist_ok=True)

            filename = f"{timestamp}_boxes.json"
            filepath = os.path.join(boxes_dir, filename)

            data = {
                'camera_id': self.camera['id'],
                'camera_name': self.camera['name'],
                'timestamp': timestamp,
                'motion_percent': motion_percent,
                'boxes': boxes,
                'frame_width': 640,   # Будет масштабироваться при склейке
                'frame_height': 360
            }

            with open(filepath, 'w') as f:
                json.dump(data, f, indent=2)

            print(f"{ts()} 📦 [{self.camera['name']}] Координаты сохранены: {filename}")
            return filepath
        except Exception as e:
            print(f"{ts()} ❌ [{self.camera['name']}] Ошибка сохранения координат: {e}")
            return None

    def _trigger_motion(self, motion_percent, ai_result, frame=None, boxes=None):
        """Триггерит тревогу"""
        if self.motion_end_timer:
            self.motion_end_timer.cancel()
            self.motion_end_timer = None

        if not self.motion_active:
            self.motion_active = True
            self.motion_start_time = time.time()
            self.motion_boxes = []

            # Сохраняем скриншот и координаты
            if frame is not None and boxes:
                self._save_alert_snapshot(frame, boxes)
                self._save_motion_boxes(boxes, time.time())

            if ai_result:
                desc = []
                if ai_result['person'] > 0:
                    desc.append(f"👤 x{ai_result['person']}")
                if ai_result['car'] > 0:
                    desc.append(f"🚗 x{ai_result['car']}")
                print(f"{ts()} {C_RED}{C_BOLD}🤖 [{self.camera['name']}] AI ТРЕВОГА! {', '.join(desc)} ({motion_percent:.1f}%){C_RESET}")
            else:
                print(f"{ts()} 📊 [{self.camera['name']}] Движение: {motion_percent:.1f}%")

            self._publish("motion_start", motion_percent, ai_result)
            result = send_mqtt_command(self.camera['id'], 'start_recording')
            print(f"{ts()} {C_BLUE}🔴 [{self.camera['name']}] Старт записи! (MQTT: {'OK' if result else 'ОШИБКА'}){C_RESET}")

    def _save_motion_boxes(self, boxes, frame_time):
        """Сохраняет координаты с временной меткой"""
        if boxes:
            self.motion_boxes.append({
                'time': frame_time,
                'boxes': boxes
            })

    def _save_all_boxes(self):
        import json

        if not self.motion_boxes:
            return None

        timestamp = time.strftime("%Y-%m-%d_%H-%M-%S")
        boxes_dir = os.path.join("snapshots", str(self.camera["id"]), "boxes")
        os.makedirs(boxes_dir, exist_ok=True)

        filename = f"{timestamp}_boxes.json"
        filepath = os.path.join(boxes_dir, filename)

        ai_classes = self.ai_classes
        if isinstance(ai_classes, str):
            ai_classes = json.loads(ai_classes)

        data = {
            'camera_id': self.camera['id'],
            'camera_name': self.camera['name'],
            'timestamp': timestamp,
            'ai_classes': ai_classes,
            'frames': self.motion_boxes
        }

        # ✅ ЗАПИСЫВАЕМ И СРАЗУ СБРАСЫВАЕМ НА ДИСК
        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2)
            f.flush()  # ← ВАЖНО! Сбрасываем буфер на диск
            os.fsync(f.fileno())  # ← ЕЩЁ ВАЖНЕЕ! Ждём физической записи

        print(f"{ts()} 📦 [{self.camera['name']}] Координаты сохранены: {len(self.motion_boxes)} кадров")
        return filepath

    def _stop_motion(self):
        """Останавливает запись после задержки"""
        if self.motion_active:
            # ✅ СНАЧАЛА СОХРАНЯЕМ JSON С КООРДИНАТАМИ
            boxes_file = self._save_all_boxes()
            if boxes_file:
                print(f"{ts()} {C_BLUE}📦 [{self.camera['name']}] JSON сохранён: {len(self.motion_boxes)} кадров{C_RESET}")

            # ✅ ПОТОМ ОТПРАВЛЯЕМ stop_recording
            self.motion_active = False
            self.motion_boxes = []
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

        payload = json.dumps(payload_dict)
        self.mqtt.publish(topic, payload)

        # Логируем в БД
        try:
            with get_db() as conn:
                conn.execute(
                    "INSERT INTO events (camera_id, event_type, details) VALUES (?, ?, ?)",
                    (self.camera["id"], event_type, json.dumps(payload_dict))
                )
                conn.commit()
        except:
            pass

    def _publish_status(self, status):
        """Публикует статус камеры"""
        topic = f"spartan/{self.camera['id']}/status"
        payload = json.dumps({
            "camera_id": self.camera["id"],
            "camera_name": self.camera["name"],
            "status": status,
            "timestamp": int(time.time())
        })
        self.mqtt.publish(topic, payload)


# ════════════════════════════════════════════════════════════
# ГЛАВНАЯ ФУНКЦИЯ
# ════════════════════════════════════════════════════════════

def main():
    print("[Legion NVR] Motion Detector")
    print(f"{ts()} [MQTT] {MQTT_BROKER}:{MQTT_PORT}")

    # Подключаем MQTT
    mqtt_client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)

    # Загружаем камеры (если нет - не падаем)
    cameras = []
    try:
        cameras = load_cameras()
    except Exception as e:
        print(f"{ts()} ⚠️ Ошибка загрузки камер: {e}")

    print(f"{ts()} [Cameras] with detector: {len(cameras)}")

    # Создаём детекторы (если есть камеры)
    detectors = []
    for cam in cameras:
        try:
            det = MotionDetector(cam, mqtt_client)
            if det.start():
                detectors.append(det)
        except Exception as e:
            print(f"{ts()} ⚠️ Ошибка создания детектора для {cam.get('name', '?')}: {e}")

    # ✅ ВСЕГДА ПОДПИСЫВАЕМСЯ НА MQTT
    mqtt_client.user_data_set({
        "detectors": detectors,
        "mqtt_client": mqtt_client
    })
    mqtt_client.on_message = on_cmd
    mqtt_client.subscribe("spartan/+/cmd")
    mqtt_client.loop_start()

    print(f"{ts()} [Detectors] Active: {len(detectors)}")
    print(f"{ts()} [Subscriptions] spartan/+/cmd")
    print("[Running] Working... (Ctrl+C to exit)")

    # ✅ БЕСКОНЕЧНЫЙ ЦИКЛ
    try:
        while True:
            for det in detectors:
                try:
                    if det.running and det.enabled:
                        det.loop()
                except Exception as e:
                    print(f"{ts()} ⚠️ Ошибка цикла детекции: {e}")
                    # Пробуем перезапустить детектор
                    try:
                        det.start()
                    except:
                        pass
            time.sleep(0.05)
    except KeyboardInterrupt:
        print("\n[Stopping] Shutting down...")
    finally:
        for det in detectors:
            try:
                det.stop()
            except:
                pass
        try:
            mqtt_client.loop_stop()
            mqtt_client.disconnect()
        except:
            pass


if __name__ == '__main__':
    main()
