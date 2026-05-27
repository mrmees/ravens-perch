"""
Ravens Perch - Flask Web Application
"""
import logging
import os
import secrets
import traceback
import hmac
from pathlib import Path
from typing import Optional

from flask import Flask, Response, abort, request, session
from markupsafe import Markup, escape

from ..config import DATA_DIR, WEB_UI_HOST, WEB_UI_PORT

logger = logging.getLogger(__name__)

# Get absolute paths for templates and static files
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_TEMPLATE_DIR = os.path.join(_THIS_DIR, 'templates')
_STATIC_DIR = os.path.join(_THIS_DIR, 'static')
_SECRET_KEY_FILE = DATA_DIR / "web-ui-secret"


def _read_secret_file(path: Path) -> Optional[str]:
    try:
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    except OSError as e:
        logger.warning(f"Unable to read web UI secret file {path}: {e}")
    return None


def _write_secret_file(path: Path, value: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value + "\n", encoding="utf-8")
        path.chmod(0o600)
    except OSError as e:
        logger.warning(f"Unable to write web UI secret file {path}: {e}")


def _get_secret_key() -> str:
    """Return a persistent Flask secret key for session-backed CSRF tokens."""
    env_secret = os.environ.get("RAVENS_PERCH_SECRET_KEY")
    if env_secret:
        return env_secret

    file_secret = _read_secret_file(_SECRET_KEY_FILE)
    if file_secret:
        return file_secret

    generated = secrets.token_urlsafe(48)
    _write_secret_file(_SECRET_KEY_FILE, generated)
    return generated


def _configured_password() -> Optional[str]:
    """Return configured Basic auth password, if web auth is enabled."""
    if os.environ.get("RAVENS_PERCH_WEB_AUTH_DISABLED", "").lower() in {"1", "true", "yes"}:
        return None

    password = os.environ.get("RAVENS_PERCH_WEB_AUTH_PASSWORD")
    if password:
        return password

    password_file = os.environ.get("RAVENS_PERCH_WEB_AUTH_PASSWORD_FILE")
    if password_file:
        return _read_secret_file(Path(password_file))

    return None


def _is_authenticated() -> bool:
    password = _configured_password()
    if not password:
        return True

    username = os.environ.get("RAVENS_PERCH_WEB_AUTH_USERNAME", "ravens")
    auth = request.authorization
    if not auth:
        return False

    return (
        hmac.compare_digest(auth.username or "", username)
        and hmac.compare_digest(auth.password or "", password)
    )


def _auth_required_response() -> Response:
    return Response(
        "Authentication required\n",
        401,
        {"WWW-Authenticate": 'Basic realm="Ravens Perch"'}
    )


def _csrf_token() -> str:
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def _csrf_field() -> Markup:
    return Markup(f'<input type="hidden" name="_csrf_token" value="{escape(_csrf_token())}">')


def _request_csrf_token() -> Optional[str]:
    return (
        request.headers.get("X-CSRFToken")
        or request.headers.get("X-CSRF-Token")
        or request.form.get("_csrf_token")
    )


def create_app():
    """Create and configure the Flask application."""
    app = Flask(
        __name__,
        template_folder=_TEMPLATE_DIR,
        static_folder=_STATIC_DIR,
        static_url_path='/cameras/static'
    )

    # Configuration
    app.config['APPLICATION_ROOT'] = '/cameras'
    app.config['SECRET_KEY'] = _get_secret_key()
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

    @app.before_request
    def require_authentication():
        if request.endpoint in {"static", "cameras.api_health"}:
            return None
        if not _is_authenticated():
            return _auth_required_response()
        return None

    @app.before_request
    def require_csrf_token():
        if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return None

        expected = session.get("_csrf_token")
        provided = _request_csrf_token()
        if not expected or not provided or not hmac.compare_digest(provided, expected):
            abort(400, "Invalid CSRF token")
        return None

    @app.context_processor
    def inject_security_helpers():
        return {
            "csrf_token": _csrf_token,
            "csrf_field": _csrf_field,
        }

    # Register blueprints
    from .routes import bp
    app.register_blueprint(bp, url_prefix='/cameras')

    # Error handlers
    @app.errorhandler(404)
    def not_found(e):
        return {"error": "Not found"}, 404

    @app.errorhandler(500)
    def server_error(e):
        logger.error(f"Server error: {e}")
        logger.error(traceback.format_exc())
        return {"error": "Internal server error"}, 500

    return app


def run_app(host: str = WEB_UI_HOST, port: int = WEB_UI_PORT, debug: bool = False):
    """Run the Flask application."""
    app = create_app()
    app.run(host=host, port=port, debug=debug, threaded=True)
