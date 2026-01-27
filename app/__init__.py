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
    
    # Handle database initialization
    with app.app_context():
        from sqlalchemy import text, inspect
        
        # Reset DB if flag is set (works for both SQLite and PostgreSQL)
        if os.getenv('RESET_DB', '').strip() in ('1', 'true', 'yes'):
            app.logger.warning('RESET_DB is set - dropping all tables...')
            try:
                # For PostgreSQL, drop and recreate schema
                db.session.execute(text('DROP SCHEMA public CASCADE'))
                db.session.execute(text('CREATE SCHEMA public'))
                db.session.execute(text('GRANT ALL ON SCHEMA public TO public'))
                db.session.commit()
                app.logger.warning('PostgreSQL schema reset complete')
            except Exception as e:
                db.session.rollback()
                app.logger.warning(f'PostgreSQL reset failed, trying SQLAlchemy drop_all: {e}')
                try:
                    db.drop_all()
                except Exception:
                    pass
            
            # Now create fresh tables
            db.create_all()
            app.logger.warning('Fresh tables created')
        else:
            # Only create tables if they don't exist (safe for existing DB)
            inspector = inspect(db.engine)
            existing_tables = inspector.get_table_names()
            if not existing_tables:
                app.logger.info('No tables found, creating...')
                db.create_all()
    
    return app
