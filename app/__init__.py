# filename: app/__init__.py
from flask import Flask, session
from flask_babel import Babel, get_locale, get_translations, gettext as _
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect, CSRFError

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

    db.init_app(app)
    csrf.init_app(app)

    @app.errorhandler(CSRFError)
    def _handle_csrf_error(err):
        from flask import request, jsonify
        if request.path.startswith("/api/"):
            return jsonify(
                error=getattr(err, "description", None)
                or "CSRF token missing or invalid. Refresh the page and try again."
            ), 400
        return err

    login_manager.init_app(app)

    @login_manager.unauthorized_handler
    def _unauthorized():
        """API clients need JSON 401; browser pages keep the login redirect."""
        from flask import request, jsonify, redirect, url_for
        if request.path.startswith("/api/") or (
            request.accept_mimetypes.best == "application/json"
        ) or (
            request.headers.get("X-Requested-With") == "XMLHttpRequest"
        ) or (
            request.is_json
            or (request.content_type or "").startswith("application/json")
        ):
            return jsonify(
                error=_("Authentication required. Please log in and try again."),
                login_url=url_for("auth.login", _external=False),
            ), 401
        # A local path is sufficient to resume the requested page and avoids
        # carrying an absolute host URL through the authentication flow.
        return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))


    def select_locale():
        from flask_login import current_user

        try:
            authenticated = current_user.is_authenticated
        except (AttributeError, RuntimeError):
            authenticated = False
        try:
            selected = session.get("language")
            if selected in SUPPORTED_LOCALES:
                return selected
        except RuntimeError:
            pass
        if authenticated and current_user.language_preference in SUPPORTED_LOCALES:
            return current_user.language_preference
        return DEFAULT_LOCALE

    app.config.setdefault("BABEL_DEFAULT_LOCALE", DEFAULT_LOCALE)
    app.config.setdefault(
        "BABEL_TRANSLATION_DIRECTORIES",
        f"{BASE_DIR}/translations",
    )
    babel.init_app(app, locale_selector=select_locale)
    from app.localization import ui_label
    app.jinja_env.filters["ui_label"] = ui_label

    @app.context_processor
    def inject_locale():
        from app.assumptions import (
            MONETARY_DISCLAIMER,
            SUBSIDY_CAVEAT,
            SUBSIDY_ELIGIBILITY_ASSUMPTION,
        )

        return {
            "get_locale": get_locale,
            "client_translations": {
                key: value for key, value in getattr(get_translations(), "_catalog", {}).items()
                if isinstance(key, str) and key and isinstance(value, str) and value
            },
            "monetary_disclaimer": _(MONETARY_DISCLAIMER),
            "subsidy_caveat": _(SUBSIDY_CAVEAT),
            "subsidy_eligibility_assumption": _(SUBSIDY_ELIGIBILITY_ASSUMPTION),
        }

    from app.models import User

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    from app.auth import auth_bp
    from app.assistant import assistant_bp
    from app.estimate import estimate_bp
    from app.locations import locations_bp
    from app.roof_ai import roof_ai_bp
    from app.roof_model import roof_model_bp
    from app.rooftop_design import rooftop_design_bp
    from app.pages import pages_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(assistant_bp)
    app.register_blueprint(pages_bp)
    app.register_blueprint(estimate_bp)
    app.register_blueprint(locations_bp)
    app.register_blueprint(roof_ai_bp)
    app.register_blueprint(roof_model_bp)
    app.register_blueprint(rooftop_design_bp)

    # CSRF is enforced on unsafe methods (POST/PUT/PATCH/DELETE) app-wide.
    # estimate_bp and locations_bp were previously exempted; that exemption is
    # removed so POST /api/estimate requires a valid CSRF token in addition to
    # the existing @login_required session check. GET helpers on locations_bp
    # (geocode, reverse-geocode, parse-maps-url, list/detail) are unaffected
    # because CSRFProtect does not validate safe methods.
    #
    # Frontend callers must send the token, e.g.:
    #   headers: { "X-CSRFToken": <token from meta or hidden input> }

    
    @app.errorhandler(400)
    def _handle_bad_request(err):
        # CSRF failures and other 400s on /api/* should be JSON for fetch() clients
        from flask import request, jsonify
        from werkzeug.exceptions import HTTPException
        if request.path.startswith("/api/"):
            description = getattr(err, "description", None) or str(err)
            return jsonify({"error": description or "Bad request"}), 400
        if isinstance(err, HTTPException):
            return err
        return jsonify({"error": "Bad request"}), 400

    @app.errorhandler(500)
    def _handle_api_500(err):
        from flask import request, jsonify
        from werkzeug.exceptions import HTTPException
        if request.path.startswith("/api/"):
            return jsonify({"error": _("Internal server error. Check the Flask terminal.")}), 500
        if isinstance(err, HTTPException):
            return err
        return jsonify({"error": _("Internal server error")}), 500

    return app
