"""
Initialize database tables.
Run this on first deploy instead of flask db upgrade.
"""
import os
import sys

# Add app to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app, db

def init_db():
    """Create all database tables."""
    app = create_app(os.getenv('FLASK_ENV', 'production'))
    
    with app.app_context():
        print("Creating database tables...")
        db.create_all()
        print("✅ Database tables created successfully!")

if __name__ == '__main__':
    init_db()
