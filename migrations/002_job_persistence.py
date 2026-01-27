"""
Migration: Add expanded Job fields and PubMedCache table

This migration adds:
1. New columns to jobs table for full state persistence
2. New pubmed_cache table for caching PubMed queries

Run manually or via Flask-Migrate:
    flask db upgrade

For manual migration on existing database:
    psql $DATABASE_URL < migrations/versions/002_job_persistence.sql
"""

# SQL for PostgreSQL
UPGRADE_SQL = """
-- Add new columns to jobs table (if they don't exist)
DO $$ 
BEGIN
    -- Status tracking columns
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='jobs' AND column_name='stage') THEN
        ALTER TABLE jobs ADD COLUMN stage VARCHAR(50);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='jobs' AND column_name='stage_label') THEN
        ALTER TABLE jobs ADD COLUMN stage_label VARCHAR(100);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='jobs' AND column_name='progress') THEN
        ALTER TABLE jobs ADD COLUMN progress INTEGER DEFAULT 0;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='jobs' AND column_name='error') THEN
        ALTER TABLE jobs ADD COLUMN error TEXT;
    END IF;
    
    -- Input tracking columns
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='jobs' AND column_name='upload_path') THEN
        ALTER TABLE jobs ADD COLUMN upload_path VARCHAR(500);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='jobs' AND column_name='upload_name') THEN
        ALTER TABLE jobs ADD COLUMN upload_name VARCHAR(255);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='jobs' AND column_name='force_ocr') THEN
        ALTER TABLE jobs ADD COLUMN force_ocr BOOLEAN DEFAULT FALSE;
    END IF;
    
    -- Results columns
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='jobs' AND column_name='transcript') THEN
        ALTER TABLE jobs ADD COLUMN transcript TEXT;
    END IF;
    
    -- Timestamp columns
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='jobs' AND column_name='updated_at') THEN
        ALTER TABLE jobs ADD COLUMN updated_at TIMESTAMP WITH TIME ZONE;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='jobs' AND column_name='heartbeat_at') THEN
        ALTER TABLE jobs ADD COLUMN heartbeat_at TIMESTAMP WITH TIME ZONE;
    END IF;
    
    -- Resume tracking
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='jobs' AND column_name='resume_started') THEN
        ALTER TABLE jobs ADD COLUMN resume_started BOOLEAN DEFAULT FALSE;
    END IF;
    
    -- S3/transcription columns
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='jobs' AND column_name='media_key') THEN
        ALTER TABLE jobs ADD COLUMN media_key VARCHAR(500);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='jobs' AND column_name='language') THEN
        ALTER TABLE jobs ADD COLUMN language VARCHAR(20);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='jobs' AND column_name='mode') THEN
        ALTER TABLE jobs ADD COLUMN mode VARCHAR(20);
    END IF;
    
    -- Make organization_id and user_id nullable for anonymous jobs
    ALTER TABLE jobs ALTER COLUMN organization_id DROP NOT NULL;
    ALTER TABLE jobs ALTER COLUMN user_id DROP NOT NULL;
    
    -- Change status from enum to varchar for flexibility
    ALTER TABLE jobs ALTER COLUMN status TYPE VARCHAR(20) USING status::text;
END $$;

-- Create index on status for faster queries
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);

-- Create pubmed_cache table
CREATE TABLE IF NOT EXISTS pubmed_cache (
    id SERIAL PRIMARY KEY,
    query_hash VARCHAR(64) UNIQUE NOT NULL,
    query_terms TEXT,
    results JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    expires_at TIMESTAMP WITH TIME ZONE,
    hit_count INTEGER DEFAULT 0
);

-- Create index on query_hash for fast lookups
CREATE INDEX IF NOT EXISTS idx_pubmed_cache_hash ON pubmed_cache(query_hash);
"""

DOWNGRADE_SQL = """
-- Drop pubmed_cache table
DROP TABLE IF EXISTS pubmed_cache;

-- Note: We don't remove the new columns from jobs table 
-- as that could cause data loss. They're harmless if unused.
"""

def upgrade():
    """Run upgrade migration"""
    from app import db
    from sqlalchemy import text
    db.session.execute(text(UPGRADE_SQL))
    db.session.commit()

def downgrade():
    """Run downgrade migration"""
    from app import db
    from sqlalchemy import text
    db.session.execute(text(DOWNGRADE_SQL))
    db.session.commit()

if __name__ == "__main__":
    import sys
    sys.path.insert(0, '.')
    from app import create_app
    import os
    
    app = create_app(os.getenv('FLASK_ENV', 'production'))
    with app.app_context():
        if len(sys.argv) > 1 and sys.argv[1] == 'downgrade':
            print("Running downgrade...")
            downgrade()
            print("Downgrade complete.")
        else:
            print("Running upgrade...")
            upgrade()
            print("Upgrade complete.")
