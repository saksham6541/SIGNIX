# filename: app/__init__.py
from flask import Flask
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect

from app.config import Config
from app.models import db

csrf = CSRFProtect()
login_manager = LoginManager()
login_manager.login_view = "auth.login"


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)
    csrf.init_app(app)
    login_manager.init_app(app)

    from app.models import User

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    from app.auth import auth_bp
    from app.estimate import estimate_bp
    from app.locations import locations_bp
    from app.pages import pages_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(pages_bp)
    app.register_blueprint(estimate_bp)
    app.register_blueprint(locations_bp)

    csrf.exempt(estimate_bp)
    csrf.exempt(locations_bp)

    return app
