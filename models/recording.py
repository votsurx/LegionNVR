from models.database import get_db
from datetime import datetime

class Recording:
    @staticmethod
    def get_all(limit=50):
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT r.*, c.name as camera_name
                FROM recordings r
                LEFT JOIN cameras c ON r.camera_id = c.id
                ORDER BY r.start_time DESC
                LIMIT ?
            """, (limit,))
            recordings = [dict(row) for row in cursor.fetchall()]
        return recordings
    
    @staticmethod
    def count_today():
        today = datetime.now().strftime('%Y-%m-%d')
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) as cnt FROM recordings WHERE date(start_time) = ?",
                (today,)
            )
            result = cursor.fetchone()
        return result['cnt'] if result else 0

    @staticmethod
    def add(camera_id, filename, start_time, record_type='motion'):
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO recordings (camera_id, filename, start_time, type) VALUES (?, ?, ?, ?)",
                (camera_id, filename, start_time, record_type)
            )
            conn.commit()