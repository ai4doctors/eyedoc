# Database Migrations

Flask-Migrate (Alembic) manages database schema changes.

## Commands

```bash
# Initialize migrations (first time only)
flask db init

# Create migration after model changes
flask db migrate -m "Description of changes"

# Apply migrations
flask db upgrade

# Rollback one revision
flask db downgrade

# Show current revision
flask db current

# Show migration history
flask db history
```

## Workflow

1. Modify models in `app/models.py`
2. Generate migration: `flask db migrate -m "Add field X"`
3. Review generated file in `migrations/versions/`
4. Apply migration: `flask db upgrade`

## Production

Migrations run automatically on deployment via the Dockerfile CMD.

For fresh deployments without migrations, use `python init_db.py` which calls `db.create_all()`.
