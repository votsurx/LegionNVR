"""
Основные HTML-маршруты
"""
from flask import Blueprint, render_template, redirect, url_for
from flask_login import login_required, current_user
from models.camera import Camera
from models.recording import Recording
from models.database import get_db

main_bp = Blueprint('main', __name__)

@main_bp.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
    return redirect(url_for('auth.login'))

@main_bp.route('/dashboard')
@login_required
def dashboard():
    cameras = Camera.get_all()
    with get_db() as conn:
        locations = [dict(r) for r in conn.execute("SELECT * FROM locations ORDER BY sort_order, id").fetchall()]
    return render_template('dashboard.html', user=current_user, cameras=cameras, locations=locations)

@main_bp.route('/cameras')
@login_required
def cameras():
    cameras = Camera.get_all()
    return render_template('cameras.html', user=current_user, cameras=cameras)

@main_bp.route('/recordings')
@login_required
def recordings():
    recordings = Recording.get_all()
    return render_template('recordings.html', user=current_user, recordings=recordings)

@main_bp.route('/settings')
@login_required
def settings():
    return render_template('settings.html', user=current_user)

@main_bp.route('/settings/users')
@login_required
def settings_users():
    return render_template('settings_users.html', user=current_user)

@main_bp.route('/settings/mqtt')
@login_required
def settings_mqtt():
    return render_template('settings_mqtt.html', user=current_user)

@main_bp.route('/settings/storage')
@login_required
def settings_storage():
    return render_template('settings_storage.html', user=current_user)

@main_bp.route('/settings/locations')
@login_required
def settings_locations():
    return render_template('settings_locations.html', user=current_user)

@main_bp.route('/cameras/<int:camera_id>/zones')
@login_required
def camera_zones(camera_id):
    cam = Camera.get_by_id(camera_id)
    if not cam:
        return "Камера не найдена", 404
    return render_template('camera_zones.html', user=current_user, camera=cam)

@main_bp.route('/cameras/<int:camera_id>/edit')
@login_required
def edit_camera(camera_id):
    cam = Camera.get_by_id(camera_id)
    if not cam:
        return "Камера не найдена", 404
    return render_template('camera_edit.html', user=current_user, camera=cam)

@main_bp.route('/health')
@login_required
def health_page():
    return render_template('health.html', user=current_user)

@main_bp.route('/player')
@login_required
def player():
    cameras = Camera.get_all()
    return render_template('player.html', user=current_user, cameras=cameras)