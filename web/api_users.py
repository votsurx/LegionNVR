"""
API для управления пользователями
"""
from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from models.user import User

api_users_bp = Blueprint('api_users', __name__, url_prefix='/api/users')

@api_users_bp.route('', methods=['GET'])
@login_required
def get_users():
    """Возвращает список пользователей (только для админов)"""
    if current_user.role != 'admin':
        return jsonify({'success': False, 'error': 'Только для админов'}), 403
    users = User.get_all()
    return jsonify({'success': True, 'users': users})

@api_users_bp.route('', methods=['POST'])
@login_required
def create_user():
    """Создаёт нового пользователя (только для админов)"""
    if current_user.role != 'admin':
        return jsonify({'success': False, 'error': 'Только для админов'}), 403
    
    data = request.get_json()
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()
    role = data.get('role', 'viewer')
    
    if not username or len(username) < 2:
        return jsonify({'success': False, 'error': 'Имя пользователя минимум 2 символа'}), 400
    if not password or len(password) < 4:
        return jsonify({'success': False, 'error': 'Пароль минимум 4 символа'}), 400
    if role not in ('admin', 'viewer'):
        return jsonify({'success': False, 'error': 'Недопустимая роль'}), 400
    
    if User.get_by_username(username):
        return jsonify({'success': False, 'error': 'Пользователь уже существует'}), 400
    
    user_id = User.create(username, password, role)
    if user_id:
        return jsonify({'success': True, 'id': user_id, 'message': f'Пользователь {username} создан'})
    return jsonify({'success': False, 'error': 'Ошибка создания'}), 500

@api_users_bp.route('/<int:user_id>', methods=['PUT'])
@login_required
def update_user(user_id):
    """Обновляет пользователя (пароль или роль)"""
    if current_user.role != 'admin' and current_user.id != user_id:
        return jsonify({'success': False, 'error': 'Нет прав'}), 403
    
    data = request.get_json()
    
    if 'password' in data:
        new_pass = data['password'].strip()
        if len(new_pass) < 4:
            return jsonify({'success': False, 'error': 'Пароль минимум 4 символа'}), 400
        User.change_password(user_id, new_pass)
    
    if 'role' in data and current_user.role == 'admin':
        User.update_role(user_id, data['role'])
    
    return jsonify({'success': True, 'message': 'Пользователь обновлён'})

@api_users_bp.route('/<int:user_id>', methods=['DELETE'])
@login_required
def delete_user(user_id):
    """Удаляет пользователя (только для админов, нельзя удалить себя)"""
    if current_user.role != 'admin':
        return jsonify({'success': False, 'error': 'Только для админов'}), 403
    
    if str(current_user.id) == str(user_id):
        return jsonify({'success': False, 'error': 'Нельзя удалить себя'}), 400
    
    User.delete(user_id)
    return jsonify({'success': True, 'message': 'Пользователь удалён'})