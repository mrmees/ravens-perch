"""
Ravens Perch - Flask Web Application
"""
import logging
import os
import traceback
from flask import Flask

from ..config import WEB_UI_HOST, WEB_UI_PORT

logger = logging.getLogger(__name__)

# Get absolute paths for templates and static files
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_TEMPLATE_DIR = os.path.join(_THIS_DIR, 'templates')
_STATIC_DIR = os.path.join(_THIS_DIR, 'static')


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
    app.config['SECRET_KEY'] = 'ravens-perch-secret-key-change-in-production'

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
