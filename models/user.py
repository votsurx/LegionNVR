from werkzeug.security import generate_password_hash, check_password_hash
from models.database import get_db

class User:
    @staticmethod
    def create(username, password, role='viewer'):
        """Создаёт нового пользователя"""
        conn = get_db()
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
        finally:
            conn.close()
    
    @staticmethod
    def get_by_id(user_id):
        """Возвращает пользователя по ID"""
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        user = cursor.fetchone()
        conn.close()
        return dict(user) if user else None
    
    @staticmethod
    def get_by_username(username):
        """Возвращает пользователя по имени"""
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
        user = cursor.fetchone()
        conn.close()
        return dict(user) if user else None
    
    @staticmethod
    def check_password(user, password):
        """Проверяет пароль"""
        return check_password_hash(user['password_hash'], password)
    
    @staticmethod
    def get_all():
        """Список всех пользователей"""
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT id, username, role, created_at FROM users")
        users = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return users