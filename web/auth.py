from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from models.user import User

auth_bp = Blueprint('auth', __name__)
login_manager = LoginManager()

class UserSession(UserMixin):
    """  Flask-Login"""
    def __init__(self, user_dict):
        self.id = str(user_dict['id'])
        self.username = user_dict['username']
        self.role = user_dict['role']

@login_manager.user_loader
def load_user(user_id):
    user = User.get_by_id(int(user_id))
    if user:
        return UserSession(user)
    return None

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
    
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        
        user = User.get_by_username(username)
        if user and User.check_password(user, password):
            login_user(UserSession(user), remember=True)
            flash(' !', 'success')
            next_page = request.args.get('next')
            return redirect(next_page or url_for('main.dashboard'))
        else:
            flash('   ', 'error')
    
    return render_template('login.html')

@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('   ', 'info')
    return redirect(url_for('auth.login'))