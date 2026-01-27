# Database Migrations

Flask-Migrate (Alembic) manages database schema changes.

## Initial Setup

```bash
# Initialize migrations (first time only)
flask db init

# Create initial migration
flask db migrate -m "Initial schema"

# Apply migrations
flask db upgrade
```

## Common Commands

```bash
# Create a new migration
flask db migrate -m "Description of changes"

# Apply migrations
flask db upgrade

# Rollback one migration
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

Migrations run automatically on Render deployment via the Dockerfile CMD.

## Troubleshooting

If migrations fail:
```bash
# Reset migrations (DESTROYS DATA)
flask db stamp head
flask db migrate -m "Reset"
flask db upgrade
```
