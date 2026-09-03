# filename: app/__init__.py
from flask import Flask
from app.config import Config
from app.models import db


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)

    from app.estimate import estimate_bp
    from app.locations import locations_bp
    from app.pages import pages_bp

    app.register_blueprint(pages_bp)
    app.register_blueprint(estimate_bp)
    app.register_blueprint(locations_bp)

    return app
