"""
Maneiro.ai Application Factory (Phase 1)
"""
import os
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect
from config import config

db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
csrf = CSRFProtect()

def create_app(config_name='default'):
    app = Flask(__name__)
    app.config.from_object(config[config_name])
    
    # Initialize extensions
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)
    
    login_manager.login_view = 'auth.login'
    
    # Register blueprints
    from app.auth import auth_bp
    from app.api import api_bp
    
    app.register_blueprint(auth_bp)
    app.register_blueprint(api_bp)  # No prefix - routes match original app.js
    
    # Exempt API routes from CSRF (JS doesn't send tokens)
    csrf.exempt(api_bp)
    
    # Auto-create tables on startup (useful for SQLite dev/staging)
    with app.app_context():
        # Reset DB if flag is set
        if os.getenv('RESET_DB', '').strip() in ('1', 'true', 'yes'):
            app.logger.warning('RESET_DB is set - dropping all tables...')
            db.drop_all()
        
        # Create tables if they don't exist
        db.create_all()
    
    return app
