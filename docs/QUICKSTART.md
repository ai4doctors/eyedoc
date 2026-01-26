# Quick Start Guide

## Prerequisites
- Python 3.11+
- Docker & Docker Compose
- AWS CLI configured
- Stripe account (for testing)

## Local Development Setup

### 1. Clone and Setup

```bash
# Navigate to your project
cd maneiro-ai

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Start Services

```bash
# Start PostgreSQL and Redis
docker-compose up -d postgres redis

# Wait for services to be healthy
docker-compose ps
```

### 3. Configure Environment

Create `.env` file:

```bash
# Flask
FLASK_ENV=development
FLASK_APP=app:create_app
SECRET_KEY=dev-secret-key-replace-me

# Database
DATABASE_URL=postgresql://maneiro_user:dev_password@localhost:5432/maneiro

# Redis
REDIS_URL=redis://localhost:6379/0

# AWS
AWS_REGION=us-west-2
AWS_S3_BUCKET=your-dev-bucket
# Don't set PARAMETER_STORE_PATH for local dev

# Stripe (use test keys)
STRIPE_PUBLISHABLE_KEY=pk_test_...
STRIPE_SECRET_KEY=sk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...

# OpenAI
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4.1

# Optional: Sentry
SENTRY_DSN=https://...@sentry.io/...
```

### 4. Initialize Database

```bash
# Run migrations
flask db upgrade

# Create test user (optional)
python -c "
from app import create_app
from models import db, User, SubscriptionStatus, SubscriptionTier

app = create_app()
with app.app_context():
    user = User(
        email='test@example.com',
        first_name='Test',
        last_name='User',
        subscription_status=SubscriptionStatus.ACTIVE,
        subscription_tier=SubscriptionTier.PROFESSIONAL
    )
    user.set_password('password123')
    db.session.add(user)
    db.session.commit()
    print('Created test user: test@example.com / password123')
"
```

### 5. Run Application

```bash
# Start Flask development server
flask run

# Or use gunicorn (production-like)
gunicorn "app:create_app()" --bind 0.0.0.0:5000 --reload
```

Visit: http://localhost:5000

### 6. Test Stripe Webhooks Locally

Install Stripe CLI:
```bash
# macOS
brew install stripe/stripe-cli/stripe

# Login
stripe login
```

Forward webhooks:
```bash
# This will give you a webhook signing secret
stripe listen --forward-to localhost:5000/webhook/stripe-webhook

# In another terminal, trigger test events
stripe trigger customer.subscription.created
```

## Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=app tests/

# Run specific test file
pytest tests/test_auth.py -v
```

## Database Management

```bash
# Create a new migration
flask db migrate -m "Description of changes"

# Apply migrations
flask db upgrade

# Rollback last migration
flask db downgrade

# View migration history
flask db history

# Reset database (WARNING: deletes all data)
flask db downgrade base
flask db upgrade
```

## Common Development Tasks

### Add a New Route

```python
# app/api.py
@api_bp.route('/new-endpoint', methods=['POST'])
@login_required
@limiter.limit("10 per hour")
def new_endpoint():
    # Your code here
    return jsonify({'ok': True})
```

### Add a New Database Model

```python
# models.py
class NewModel(db.Model):
    __tablename__ = 'new_models'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )

# Then create migration
flask db migrate -m "Add NewModel"
flask db upgrade
```

### Add a New Service

```python
# app/services/new_service.py
class NewService:
    def __init__(self, config):
        self.config = config
    
    def do_something(self, data):
        # Implementation
        pass
```

## Debugging

### Enable Debug Mode

```bash
export FLASK_DEBUG=1
flask run
```

### View Logs

```bash
# Flask logs
tail -f logs/app.log

# Database queries
export SQLALCHEMY_ECHO=True

# Celery worker logs
docker-compose logs -f celery
```

### Database Connection Issues

```bash
# Check if PostgreSQL is running
docker-compose ps postgres

# Connect to database directly
psql postgresql://maneiro_user:dev_password@localhost:5432/maneiro

# View tables
\dt

# View specific table
SELECT * FROM users LIMIT 10;
```

## Troubleshooting

### Port Already in Use
```bash
# Find process using port 5000
lsof -ti:5000

# Kill process
kill -9 $(lsof -ti:5000)
```

### Database Connection Refused
```bash
# Restart PostgreSQL
docker-compose restart postgres

# Check logs
docker-compose logs postgres
```

### Redis Connection Issues
```bash
# Test Redis connection
redis-cli ping

# Restart Redis
docker-compose restart redis
```

### Migration Conflicts
```bash
# If migrations are out of sync
flask db stamp head
flask db migrate -m "Sync migrations"
flask db upgrade
```

## Production Deployment

See `DEPLOYMENT.md` for detailed production deployment instructions.

Quick checklist:
- [ ] Set all secrets in AWS Parameter Store
- [ ] Configure production database
- [ ] Set up Redis instance
- [ ] Configure Stripe webhook
- [ ] Enable HTTPS
- [ ] Set rate limits appropriately
- [ ] Configure monitoring (Sentry)
- [ ] Run database migrations
- [ ] Test subscription flow

## Useful Commands

```bash
# Shell access to running container
docker-compose exec app bash

# View all users
flask shell
>>> from models import User
>>> User.query.all()

# Reset user password
flask shell
>>> user = User.query.filter_by(email='test@example.com').first()
>>> user.set_password('newpassword')
>>> db.session.commit()

# Check subscription status
flask shell
>>> from models import User
>>> user = User.query.first()
>>> print(f"Tier: {user.subscription_tier.value}")
>>> print(f"Status: {user.subscription_status.value}")
>>> print(f"Jobs: {user.monthly_job_count}")
```

## Next Steps

1. Review architecture in `IMPLEMENTATION_SUMMARY.md`
2. Follow deployment guide in `DEPLOYMENT.md`
3. Customize subscription tiers in Stripe
4. Update branding and styling
5. Add custom features

## Support

- 📖 Documentation: See `DEPLOYMENT.md`
- 🐛 Issues: Check logs in `logs/` directory
- 💬 Questions: Review code comments and docstrings
