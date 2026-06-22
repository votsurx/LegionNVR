import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'database.db')

def get_db():
    """Возвращает соединение с БД"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # чтобы обращаться к полям по имени
    return conn

def init_db():
    """Создаёт таблицы при первом запуске"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS locations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            icon TEXT DEFAULT '📍',
            sort_order INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT DEFAULT 'viewer',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        
        CREATE TABLE IF NOT EXISTS cameras (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            rtsp_main TEXT NOT NULL,
            rtsp_sub TEXT,
            -- Статусы
            enabled INTEGER DEFAULT 1,
            stream_enabled INTEGER DEFAULT 1,
            motion_enabled INTEGER DEFAULT 1,
            record_enabled INTEGER DEFAULT 0,
            -- Детектор
            motion_threshold REAL DEFAULT 2.0,
            motion_cooldown INTEGER DEFAULT 5,
            motion_fps INTEGER DEFAULT 5,
            -- Запись
            record_mode TEXT DEFAULT 'motion',
            record_pre_sec INTEGER DEFAULT 5,
            record_post_sec INTEGER DEFAULT 10,
            record_retention_days INTEGER DEFAULT 7,
            -- Стриминг
            stream_quality TEXT DEFAULT 'copy',
            stream_hls_time INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        
        CREATE TABLE IF NOT EXISTS recordings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            camera_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            start_time TIMESTAMP NOT NULL,
            end_time TIMESTAMP,
            type TEXT DEFAULT 'motion',
            size_mb REAL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (camera_id) REFERENCES cameras(id)
        );
        
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            camera_id INTEGER,
            event_type TEXT NOT NULL,
            details TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (camera_id) REFERENCES cameras(id)
        );
        
        CREATE TABLE IF NOT EXISTS detection_zones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            camera_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            zone_type TEXT DEFAULT 'exclude',
            points_json TEXT DEFAULT '[]',
            enabled INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (camera_id) REFERENCES cameras(id)
        );
        
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
    """)
    
    # Добавляем новые колонки если их нет (миграция старой БД)
    try:
        cursor.execute("ALTER TABLE cameras ADD COLUMN location_id INTEGER DEFAULT NULL REFERENCES locations(id)")
    except:
        pass
    try:
        cursor.execute("ALTER TABLE cameras ADD COLUMN stream_enabled INTEGER DEFAULT 1")
    except:
        pass
    try:
        cursor.execute("ALTER TABLE cameras ADD COLUMN motion_threshold REAL DEFAULT 2.0")
    except:
        pass
    try:
        cursor.execute("ALTER TABLE cameras ADD COLUMN motion_cooldown INTEGER DEFAULT 5")
    except:
        pass
    try:
        cursor.execute("ALTER TABLE cameras ADD COLUMN motion_fps INTEGER DEFAULT 5")
    except:
        pass
    try:
        cursor.execute("ALTER TABLE cameras ADD COLUMN record_pre_sec INTEGER DEFAULT 5")
    except:
        pass
    try:
        cursor.execute("ALTER TABLE cameras ADD COLUMN record_post_sec INTEGER DEFAULT 10")
    except:
        pass
    try:
        cursor.execute("ALTER TABLE cameras ADD COLUMN stream_quality TEXT DEFAULT 'copy'")
    except:
        pass
    try:
        cursor.execute("ALTER TABLE cameras ADD COLUMN stream_hls_time INTEGER DEFAULT 1")
    except:
        pass
    try:
        cursor.execute("ALTER TABLE cameras ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    except:
        pass
    
    conn.commit()
    conn.close()
    print("✅ База данных готова")

def get_mqtt_config():
    """Читает MQTT-настройки из БД"""
    conn = get_db()
    rows = conn.execute("SELECT key, value FROM settings WHERE key LIKE 'mqtt_%'").fetchall()
    conn.close()
    
    config = {
        "broker": "127.0.0.1",
        "port": 1883,
        "username": "",
        "password": ""
    }
    
    for row in rows:
        key = row["key"].replace("mqtt_", "")
        if key == "port":
            config[key] = int(row["value"])
        else:
            config[key] = row["value"]
    
    return config

if __name__ == '__main__':
    init_db()