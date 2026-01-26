# Maneiro.ai Improved Architecture - Deployment Guide

## Overview

This guide covers migrating from the password-based paywall to a full user authentication system with Stripe subscriptions and AWS Parameter Store for secrets management.

## Prerequisites

- PostgreSQL database
- Redis instance
- AWS account with:
  - S3 bucket
  - Parameter Store access
  - Transcribe access (existing)
- Stripe account

## Step 1: Set Up AWS Parameter Store

### Create Parameters

Run these AWS CLI commands to create your parameters:

```bash
# Set your environment
export ENV="prod"  # or "dev", "staging"
export REGION="us-west-2"

# Database
aws ssm put-parameter \
    --name "/maneiro/${ENV}/database-url" \
    --value "postgresql://username:password@host:5432/maneiro" \
    --type "SecureString" \
    --region ${REGION}

# Redis
aws ssm put-parameter \
    --name "/maneiro/${ENV}/redis-url" \
    --value "redis://your-redis-host:6379/0" \
    --type "SecureString" \
    --region ${REGION}

# Flask Secret Key (generate with: python -c "import secrets; print(secrets.token_hex(32))")
aws ssm put-parameter \
    --name "/maneiro/${ENV}/secret-key" \
    --value "your-generated-secret-key" \
    --type "SecureString" \
    --region ${REGION}

# Stripe Keys
aws ssm put-parameter \
    --name "/maneiro/${ENV}/stripe-secret-key" \
    --value "sk_live_..." \
    --type "SecureString" \
    --region ${REGION}

aws ssm put-parameter \
    --name "/maneiro/${ENV}/stripe-publishable-key" \
    --value "pk_live_..." \
    --type "String" \
    --region ${REGION}

aws ssm put-parameter \
    --name "/maneiro/${ENV}/stripe-webhook-secret" \
    --value "whsec_..." \
    --type "SecureString" \
    --region ${REGION}

# OpenAI API Key
aws ssm put-parameter \
    --name "/maneiro/${ENV}/openai-api-key" \
    --value "sk-..." \
    --type "SecureString" \
    --region ${REGION}

# S3 Bucket
aws ssm put-parameter \
    --name "/maneiro/${ENV}/aws-s3-bucket" \
    --value "your-bucket-name" \
    --type "String" \
    --region ${REGION}

# Optional: Sentry DSN
aws ssm put-parameter \
    --name "/maneiro/${ENV}/sentry-dsn" \
    --value "https://...@sentry.io/..." \
    --type "SecureString" \
    --region ${REGION}
```

### IAM Permissions

Add this policy to your EC2/ECS task role:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ssm:GetParameter",
        "ssm:GetParameters",
        "ssm:GetParametersByPath"
      ],
      "Resource": "arn:aws:ssm:us-west-2:*:parameter/maneiro/*"
    }
  ]
}
```

## Step 2: Set Up Database

### Install PostgreSQL

```bash
# On Ubuntu/Debian
sudo apt-get install postgresql postgresql-contrib

# On macOS
brew install postgresql
```

### Create Database

```bash
sudo -u postgres psql
CREATE DATABASE maneiro;
CREATE USER maneiro_user WITH PASSWORD 'secure_password';
GRANT ALL PRIVILEGES ON DATABASE maneiro TO maneiro_user;
\q
```

### Environment Variables

For local development, create `.env`:

```bash
FLASK_ENV=development
FLASK_APP=app:create_app

# Database
DATABASE_URL=postgresql://maneiro_user:secure_password@localhost:5432/maneiro

# Redis
REDIS_URL=redis://localhost:6379/0

# AWS
AWS_REGION=us-west-2
AWS_S3_BUCKET=your-bucket-name
PARAMETER_STORE_PATH=/maneiro/dev/

# Stripe (for local testing)
STRIPE_PUBLISHABLE_KEY=pk_test_...
STRIPE_SECRET_KEY=sk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...

# OpenAI
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4.1

# Flask
SECRET_KEY=dev-secret-key-change-me
```

## Step 3: Database Migration

### Install Flask-Migrate

Already in requirements.txt:

```bash
pip install -r requirements.txt
```

### Initialize Migrations

```bash
flask db init
flask db migrate -m "Initial migration"
flask db upgrade
```

### Create First Admin User (Optional)

```python
from app import create_app
from models import db, User, SubscriptionTier, SubscriptionStatus

app = create_app()
with app.app_context():
    admin = User(
        email='admin@example.com',
        first_name='Admin',
        last_name='User',
        subscription_status=SubscriptionStatus.ACTIVE,
        subscription_tier=SubscriptionTier.ENTERPRISE,
        is_verified=True
    )
    admin.set_password('secure_password')
    db.session.add(admin)
    db.session.commit()
```

## Step 4: Set Up Stripe

### Create Products

In Stripe Dashboard:

1. Create 3 products:
   - **Basic** ($29/month)
   - **Professional** ($99/month)
   - **Enterprise** ($299/month)

2. Note the Price IDs (e.g., `price_1234...`)

3. Update `stripe_webhook.py` with your price IDs:

```python
tier_map = {
    'price_1234abc': SubscriptionTier.BASIC,
    'price_5678def': SubscriptionTier.PROFESSIONAL,
    'price_9012ghi': SubscriptionTier.ENTERPRISE,
}
```

### Set Up Webhook

1. In Stripe Dashboard → Developers → Webhooks
2. Add endpoint: `https://yourdomain.com/webhook/stripe-webhook`
3. Select events:
   - `customer.subscription.created`
   - `customer.subscription.updated`
   - `customer.subscription.deleted`
   - `invoice.payment_succeeded`
   - `invoice.payment_failed`
4. Copy webhook signing secret to Parameter Store

## Step 5: Update Application Code

### New App Structure

```
maneiro-ai/
├── app/
│   ├── __init__.py          # App factory
│   ├── models.py            # Database models
│   ├── auth.py              # Authentication blueprint
│   ├── api.py               # API blueprint
│   ├── stripe_webhook.py    # Stripe webhook handler
│   ├── services/
│   │   ├── __init__.py
│   │   ├── pdf_service.py
│   │   ├── openai_service.py
│   │   └── aws_service.py
│   ├── templates/
│   │   ├── base.html
│   │   ├── index.html
│   │   └── auth/
│   │       ├── login.html
│   │       ├── register.html
│   │       ├── pricing.html
│   │       └── account.html
│   └── static/
├── migrations/
├── tests/
├── config.py
├── requirements.txt
├── runtime.txt
└── wsgi.py
```

### Create wsgi.py

```python
from app import create_app

app = create_app()

if __name__ == '__main__':
    app.run()
```

### Create app/__init__.py (App Factory)

```python
from flask import Flask
from flask_login import LoginManager
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from config import get_config
from models import db, bcrypt
import stripe
import sentry_sdk
from sentry_sdk.integrations.flask import FlaskIntegration

def create_app(config_name=None):
    app = Flask(__name__)
    
    # Load config
    config = get_config(config_name)
    app.config.from_object(config)
    
    # Initialize extensions
    db.init_app(app)
    bcrypt.init_app(app)
    
    # Rate limiting
    limiter = Limiter(
        app=app,
        key_func=get_remote_address,
        storage_uri=config.RATELIMIT_STORAGE_URL
    )
    
    # Sentry monitoring
    if config.SENTRY_DSN:
        sentry_sdk.init(
            dsn=config.SENTRY_DSN,
            integrations=[FlaskIntegration()],
            traces_sample_rate=0.1
        )
    
    # Stripe
    stripe.api_key = config.STRIPE_SECRET_KEY
    
    # Register blueprints
    from auth import auth_bp, init_auth
    from api import api_bp
    from stripe_webhook import webhook_bp
    
    init_auth(app)
    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(api_bp, url_prefix='/api')
    app.register_blueprint(webhook_bp, url_prefix='/webhook')
    
    # Main routes
    from flask import render_template
    from flask_login import login_required
    
    @app.route('/')
    @login_required
    def index():
        return render_template('index.html')
    
    @app.route('/healthz')
    def healthz():
        return {'status': 'ok'}, 200
    
    return app
```

## Step 6: Render Deployment

### Update render.yaml

```yaml
services:
  - type: web
    name: maneiro
    env: docker
    plan: starter
    dockerfilePath: ./Dockerfile
    autoDeploy: true
    healthCheckPath: /healthz
    envVars:
      - key: FLASK_ENV
        value: production
      - key: PARAMETER_STORE_PATH
        value: /maneiro/prod/
      - key: AWS_REGION
        value: us-west-2
      - key: DATABASE_URL
        fromDatabase:
          name: maneiro-db
          property: connectionString
      - key: REDIS_URL
        fromService:
          name: maneiro-redis
          type: redis
          property: connectionString

databases:
  - name: maneiro-db
    plan: starter
    databaseName: maneiro
    user: maneiro_user

services:
  - type: redis
    name: maneiro-redis
    plan: starter
    maxmemoryPolicy: allkeys-lru
```

### Update Dockerfile

```dockerfile
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    poppler-utils \
    libgl1 \
    libglib2.0-0 \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

# Copy application
COPY . .

# Run migrations and start app
CMD flask db upgrade && gunicorn "app:create_app()" \
    --bind 0.0.0.0:${PORT:-10000} \
    --timeout 180 \
    --workers 4 \
    --worker-class gthread \
    --threads 2
```

## Step 7: Migration Checklist

### Pre-Migration

- [ ] Set up Parameter Store with all secrets
- [ ] Create PostgreSQL database
- [ ] Set up Redis instance
- [ ] Configure Stripe products and webhooks
- [ ] Update IAM roles with Parameter Store permissions
- [ ] Test locally with `.env` file

### Migration

- [ ] Deploy new code to Render
- [ ] Run database migrations
- [ ] Verify Parameter Store connection
- [ ] Test user registration
- [ ] Test Stripe checkout flow
- [ ] Verify webhook is receiving events

### Post-Migration

- [ ] Monitor error logs
- [ ] Test all major features
- [ ] Verify audit logging
- [ ] Check rate limiting
- [ ] Test subscription limits

## Step 8: Testing

### Local Testing

```bash
# Start services
docker-compose up -d postgres redis

# Run migrations
flask db upgrade

# Start development server
flask run
```

### Test Checklist

- [ ] User registration
- [ ] User login
- [ ] File upload and analysis
- [ ] Usage limits (free tier)
- [ ] Stripe checkout
- [ ] Webhook processing
- [ ] PDF generation
- [ ] Audit logging

## Security Best Practices

1. **Never commit secrets** - Use Parameter Store/env vars
2. **Enable HTTPS only** - Configure in Render
3. **Implement CSRF protection** - Enabled by default with Flask-WTF
4. **Add rate limiting** - Implemented with Flask-Limiter
5. **Use strong passwords** - Enforce in registration
6. **Enable audit logging** - Track all sensitive actions
7. **Sanitize file uploads** - Validate file types and sizes
8. **Secure session cookies** - Configure in production

## Monitoring

### Set Up Sentry

1. Create Sentry account
2. Create new project
3. Add DSN to Parameter Store
4. Test error reporting

### Key Metrics to Monitor

- User registration rate
- Subscription conversion rate
- API error rate
- Job processing time
- Usage limits hits
- Failed payments

## Support

For issues during migration, check:
- CloudWatch logs (if using AWS)
- Render logs
- Sentry error tracking
- Stripe webhook logs
