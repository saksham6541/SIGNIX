# filename: app/__init__.py
from flask import Flask, session
from flask_babel import Babel, get_locale
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect

from app.config import BASE_DIR, Config
from app.models import db

csrf = CSRFProtect()
login_manager = LoginManager()
login_manager.login_view = "auth.login"
babel = Babel()

SUPPORTED_LOCALES = ("en", "hi")
DEFAULT_LOCALE = "en"


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    app.extensions["assistant_message_counts"] = {}
    app.extensions["assistant_count_dates"] = {}

    db.init_app(app)
    csrf.init_app(app)
    login_manager.init_app(app)

    def select_locale():
        from flask_login import current_user

        try:
            authenticated = current_user.is_authenticated
        except (AttributeError, RuntimeError):
            authenticated = False
        if authenticated:
            return current_user.language_preference or DEFAULT_LOCALE
        try:
            return session.get("language", DEFAULT_LOCALE)
        except RuntimeError:
            return DEFAULT_LOCALE

    app.config.setdefault("BABEL_DEFAULT_LOCALE", DEFAULT_LOCALE)
    app.config.setdefault(
        "BABEL_TRANSLATION_DIRECTORIES",
        f"{BASE_DIR}/translations",
    )
    babel.init_app(app, locale_selector=select_locale)

    @app.context_processor
    def inject_locale():
        return {"get_locale": get_locale}

    from app.models import User

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    from app.auth import auth_bp
    from app.assistant import assistant_bp
    from app.estimate import estimate_bp
    from app.locations import locations_bp
    from app.pages import pages_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(assistant_bp)
    app.register_blueprint(pages_bp)
    app.register_blueprint(estimate_bp)
    app.register_blueprint(locations_bp)

    csrf.exempt(estimate_bp)
    csrf.exempt(locations_bp)

    return app
