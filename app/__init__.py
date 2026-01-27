"""
Maneiro.ai Application Factory
"""
import os
from datetime import datetime, timezone
from flask import Flask, render_template, redirect, url_for, request
from flask_login import LoginManager, current_user, login_required
from flask_wtf.csrf import CSRFProtect

from config import get_config
from app.models import db, bcrypt, User, Organization, init_db

# Extensions
login_manager = LoginManager()
csrf = CSRFProtect()


def create_app(config_name=None):
    """Create and configure the Flask application"""
    app = Flask(__name__,
                static_folder="static",
                template_folder="templates")
    
    # Load configuration
    config = get_config(config_name or os.environ.get("FLASK_ENV", "development"))
    app.config.from_object(config)
    
    # Initialize extensions
    db.init_app(app)
    bcrypt.init_app(app)
    csrf.init_app(app)
    
    # Login manager
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Please log in to access this page."
    
    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))
    
    # Register blueprints
    from app.auth import auth_bp
    from app.api import api_bp
    
    app.register_blueprint(auth_bp)
    app.register_blueprint(api_bp)
    
    # Main routes
    @app.route("/")
    def index():
        if current_user.is_authenticated:
            return render_template("index.html", user=current_user)
        return redirect(url_for("auth.login"))
    
    @app.route("/healthz")
    def healthz():
        """Health check endpoint"""
        try:
            # Quick DB check
            db.session.execute(db.text("SELECT 1"))
            db_status = "ok"
        except Exception as e:
            db_status = f"error: {e}"
        
        return {
            "status": "ok" if db_status == "ok" else "degraded",
            "version": app.config.get("APP_VERSION", "unknown"),
            "database": db_status,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    
    @app.route("/version")
    def version():
        """Version endpoint"""
        return {
            "version": app.config.get("APP_VERSION", "unknown"),
            "build_time": app.config.get("BUILD_TIME", ""),
            "git_commit": app.config.get("GIT_COMMIT", ""),
            "features": {
                "strict_schema": app.config.get("FEATURE_STRICT_SCHEMA", True),
                "progress_stages": app.config.get("FEATURE_PROGRESS_STAGES", True),
                "multi_specialty": app.config.get("FEATURE_MULTI_SPECIALTY", True),
            }
        }
    
    # Error handlers
    @app.errorhandler(404)
    def not_found(error):
        return render_template("errors/404.html"), 404
    
    @app.errorhandler(500)
    def internal_error(error):
        db.session.rollback()
        return render_template("errors/500.html"), 500
    
    # Context processors
    @app.context_processor
    def inject_globals():
        return {
            "APP_VERSION": app.config.get("APP_VERSION", "unknown"),
            "current_year": datetime.now().year
        }
    
    # Initialize database
    with app.app_context():
        init_db(app)
    
    return app
