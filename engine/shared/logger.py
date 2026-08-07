"""
Единая система логгирования для всех сервисов
"""
import os
import time
import logging
from logging.handlers import RotatingFileHandler

# Папка для логов
LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# Настройки ротации: 10 МБ, 5 файлов
MAX_BYTES = 10 * 1024 * 1024
BACKUP_COUNT = 5

def get_logger(name, level=logging.INFO):
    """Возвращает логгер для конкретного сервиса"""
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Если обработчики уже есть, не добавляем повторно
    if logger.handlers:
        return logger

    # ✅ Формат лога: Дата Время | Сервис | Сообщение
    formatter = logging.Formatter(
        '%(asctime)s.%(msecs)03d | %(name)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Файловый обработчик с ротацией
    log_file = os.path.join(LOG_DIR, f"{name}.log")
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Консольный обработчик (дублирует логи в консоль)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)  # только WARNING и выше
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger