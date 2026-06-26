from werkzeug.security import generate_password_hash, check_password_hash
from models.database import get_db

class User:
    @staticmethod
    def create(username, password, role='viewer'):
        with get_db() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                    (username, generate_password_hash(password), role)
                )
                conn.commit()
                return cursor.lastrowid
            except:
                return None

    @staticmethod
    def get_by_id(user_id):
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
            user = cursor.fetchone()
        return dict(user) if user else None
    
    @staticmethod
    def get_by_username(username):
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
            user = cursor.fetchone()
        return dict(user) if user else None
    
    @staticmethod
    def check_password(user, password):
        """ """
        return check_password_hash(user['password_hash'], password)
    
    @staticmethod
    def get_all():
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, username, role, created_at FROM users ORDER BY id")
            users = [dict(row) for row in cursor.fetchall()]
        return users
    
    @staticmethod
    def change_password(user_id, new_password):
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (generate_password_hash(new_password), user_id)
            )
            conn.commit()
    
    @staticmethod
    def update_role(user_id, new_role):
        if new_role not in ('admin', 'viewer'):
            return False
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET role = ? WHERE id = ?", (new_role, user_id))
            conn.commit()
        return True
    
    @staticmethod
    def delete(user_id):
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
            conn.commit()
    
    @staticmethod
    def count():
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as cnt FROM users")
            result = cursor.fetchone()
        return result['cnt'] if result else 0