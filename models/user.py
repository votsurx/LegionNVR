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
            user_id = cursor.lastrowid
            return user_id
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
        """Список всех пользователей (без хэшей паролей)"""
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT id, username, role, created_at FROM users ORDER BY id")
        users = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return users
    
    @staticmethod
    def change_password(user_id, new_password):
        """Меняет пароль пользователя"""
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (generate_password_hash(new_password), user_id)
        )
        conn.commit()
        conn.close()
    
    @staticmethod
    def update_role(user_id, new_role):
        """Меняет роль пользователя"""
        if new_role not in ('admin', 'viewer'):
            return False
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET role = ? WHERE id = ?", (new_role, user_id))
        conn.commit()
        conn.close()
        return True
    
    @staticmethod
    def delete(user_id):
        """Удаляет пользователя"""
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
        conn.close()
    
    @staticmethod
    def count():
        """Количество пользователей"""
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as cnt FROM users")
        result = cursor.fetchone()
        conn.close()
        return result['cnt'] if result else 0