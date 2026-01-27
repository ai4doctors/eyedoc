# Database Migrations

Flask-Migrate (Alembic) manages database schema changes.

## Setup

Initialize migrations (already done if you cloned this repo):

```bash
flask db init
```

## Commands

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

For production deployments on Render, migrations run automatically via the Dockerfile CMD.

## Initial Schema

The initial schema includes:

- `organizations` - Clinics (billable entities)
- `users` - Belongs to organization
- `jobs` - Analysis tasks (belongs to org + user)
- `audit_logs` - Compliance tracking

## Notes

- Always review auto-generated migrations before applying
- Test migrations on a copy of production data
- Keep migrations in version control
- Use `flask db stamp head` to mark current state without running migrations
