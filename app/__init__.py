# filename: app/__init__.py
from flask import Flask
from app.config import Config
from app.models import db


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)

    from app.routes import bp as routes_bp
    app.register_blueprint(routes_bp)

    return app
