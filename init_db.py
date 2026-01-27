"""
Initialize database tables.
Run this on first deploy instead of flask db upgrade.

Set RESET_DB=1 environment variable to drop and recreate all tables.
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
        # Check if we should reset
        if os.getenv('RESET_DB', '').strip() in ('1', 'true', 'yes'):
            print("⚠️  RESET_DB is set - dropping all tables...")
            db.drop_all()
            print("Tables dropped.")
        
        print("Creating database tables...")
        db.create_all()
        print("✅ Database tables created successfully!")
        
        # Run migration for existing databases (adds new columns if missing)
        try:
            from sqlalchemy import text
            # Check if pubmed_cache table exists
            result = db.session.execute(text("SELECT to_regclass('pubmed_cache')"))
            if result.scalar() is None:
                print("Running job persistence migration...")
                # Import and run migration
                migration_path = os.path.join(os.path.dirname(__file__), 'migrations', '002_job_persistence.py')
                if os.path.exists(migration_path):
                    import importlib.util
                    spec = importlib.util.spec_from_file_location("migration", migration_path)
                    migration = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(migration)
                    migration.upgrade()
                    print("✅ Migration complete!")
                else:
                    print("Migration file not found, skipping...")
            else:
                print("Migration already applied (pubmed_cache table exists)")
        except Exception as e:
            print(f"Migration skipped or failed (may not be needed): {e}")

if __name__ == '__main__':
    init_db()
