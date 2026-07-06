"""
Веб-модуль Legion NVR
Регистрация всех blueprints
"""
from flask import Blueprint
from .api_cameras import api_cameras_bp
from .api_recordings import api_recordings_bp
from .api_settings import api_settings_bp
from .api_health import api_health_bp
from .streams import streams_bp
from .player import player_bp
from .main_routes import main_bp
from .auth import auth_bp
from .api_users import api_users_bp


def register_blueprints(app):
    """Регистрирует все blueprints в приложении"""
    app.register_blueprint(api_cameras_bp)
    app.register_blueprint(api_recordings_bp)
    app.register_blueprint(api_settings_bp)
    app.register_blueprint(api_health_bp)
    app.register_blueprint(streams_bp)
    app.register_blueprint(player_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(api_users_bp)