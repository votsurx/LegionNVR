from models.database import get_db

class Camera:
    @staticmethod
    def get_all():
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM cameras ORDER BY id")
        cameras = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return cameras
    
    @staticmethod
    def get_by_id(camera_id):
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM cameras WHERE id = ?", (camera_id,))
        camera = cursor.fetchone()
        conn.close()
        return dict(camera) if camera else None
    
    @staticmethod
    def create(name, rtsp_main, rtsp_sub=None):
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO cameras (name, rtsp_main, rtsp_sub) VALUES (?, ?, ?)",
            (name, rtsp_main, rtsp_sub)
        )
        conn.commit()
        camera_id = cursor.lastrowid
        conn.close()
        return camera_id
    
    @staticmethod
    def update(camera_id, **kwargs):
        allowed = ['name', 'rtsp_main', 'rtsp_sub', 'enabled', 'motion_enabled', 
                   'record_enabled', 'record_mode', 'record_retention_days']
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return
        
        conn = get_db()
        cursor = conn.cursor()
        set_clause = ', '.join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [camera_id]
        cursor.execute(f"UPDATE cameras SET {set_clause} WHERE id = ?", values)
        conn.commit()
        conn.close()
    
    @staticmethod
    def delete(camera_id):
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM cameras WHERE id = ?", (camera_id,))
        conn.commit()
        conn.close()