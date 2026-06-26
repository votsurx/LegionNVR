from models.database import get_db
from models.database import get_db
from datetime import datetime

class Camera:
    @staticmethod
    def get_all():
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM cameras ORDER BY id")
            cameras = [dict(row) for row in cursor.fetchall()]
        return cameras

    @staticmethod
    def get_by_id(camera_id):
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM cameras WHERE id = ?", (camera_id,))
            camera = cursor.fetchone()
        return dict(camera) if camera else None
    
    @staticmethod
    def create(name, rtsp_main, rtsp_sub=None):
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO cameras (name, rtsp_main, rtsp_sub) VALUES (?, ?, ?)",
                (name, rtsp_main, rtsp_sub)
            )
            conn.commit()
            camera_id = cursor.lastrowid
        return camera_id

    @staticmethod
    def update(camera_id, **kwargs):
        allowed = ['name', 'rtsp_main', 'rtsp_sub', 'enabled', 'motion_enabled',
                   'record_enabled', 'record_mode', 'record_retention_days']
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return

        with get_db() as conn:
            cursor = conn.cursor()
            set_clause = ', '.join(f"{k} = ?" for k in updates)
            values = list(updates.values()) + [camera_id]
            cursor.execute(f"UPDATE cameras SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE id = ?", values)
            conn.commit()

    @staticmethod
    def update_full(camera_id, data):
        allowed = [
            'name', 'rtsp_main', 'rtsp_sub',
            'enabled', 'stream_enabled', 'motion_enabled', 'record_enabled',
            'motion_threshold', 'motion_cooldown', 'motion_fps',
            'record_mode', 'record_pre_sec', 'record_post_sec', 'record_retention_days',
            'stream_quality', 'stream_hls_time',
            'location_id',
            # 🤖 AI
            'ai_enabled', 'ai_classes', 'ai_confidence', 'ai_frame_skip'
        ]
        updates = {k: data[k] for k in allowed if k in data}
        if not updates:
            return

        with get_db() as conn:
            cursor = conn.cursor()
            set_clause = ', '.join(f"{k} = ?" for k in updates)
            values = list(updates.values()) + [camera_id]
            cursor.execute(f"UPDATE cameras SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE id = ?", values)
            conn.commit()
    
    @staticmethod
    def delete(camera_id):
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM cameras WHERE id = ?", (camera_id,))
            conn.commit()
    
    @staticmethod
    def get_zones(camera_id):
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM detection_zones WHERE camera_id = ? ORDER BY id", (camera_id,))
            zones = [dict(row) for row in cursor.fetchall()]
        return zones
    
    @staticmethod
    def save_zone(camera_id, zone_data):
        with get_db() as conn:
            cursor = conn.cursor()
            if 'id' in zone_data and zone_data['id']:
                cursor.execute(
                    "UPDATE detection_zones SET name=?, zone_type=?, points_json=?, enabled=? WHERE id=? AND camera_id=?",
                    (zone_data['name'], zone_data['zone_type'], zone_data['points_json'],
                     zone_data.get('enabled', 1), zone_data['id'], camera_id)
                )
            else:
                cursor.execute(
                    "INSERT INTO detection_zones (camera_id, name, zone_type, points_json) VALUES (?, ?, ?, ?)",
                    (camera_id, zone_data['name'], zone_data['zone_type'], zone_data['points_json'])
                )
            conn.commit()

    @staticmethod
    def delete_zone(zone_id):
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM detection_zones WHERE id = ?", (zone_id,))
            conn.commit()