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
    recent_events = []
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT e.*, c.name as camera_name 
        FROM events e 
        LEFT JOIN cameras c ON e.camera_id = c.id 
        ORDER BY e.timestamp DESC 
        LIMIT 20
    """)
    recent_events = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    today_recordings = Recording.count_today()
    
    return render_template('dashboard.html', 
                         user=current_user,
                         cameras=cameras,
                         recent_events=recent_events,
                         today_recordings=today_recordings)

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

@main_bp.route('/cameras/<int:camera_id>/edit')
@login_required
def edit_camera(camera_id):
    from models.camera import Camera
    cam = Camera.get_by_id(camera_id)
    if not cam:
        return "Камера не найдена", 404
    return render_template('camera_edit.html', user=current_user, camera=cam)

@main_bp.route('/cameras/<int:camera_id>/delete')
@login_required
def delete_camera(camera_id):
    from models.camera import Camera
    cam = Camera.get_by_id(camera_id)
    if not cam:
        return "Камера не найдена", 404
    Camera.delete(camera_id)
    return redirect(url_for('main.cameras'))