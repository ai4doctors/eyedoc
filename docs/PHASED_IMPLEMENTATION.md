# Revised Implementation Plan - Phased Approach

## Developer Feedback Summary

**Key Points:**
1. ✅ Right direction, but too much scope at once
2. ✅ User auth + PostgreSQL are must-haves early
3. ⚠️ Stripe should update DB state, not be the gatekeeper
4. ⚠️ Parameter Store adds complexity - start simpler
5. ❌ Missing multi-tenancy (clinics as buyers, not individuals)
6. ❌ Missing data retention policy

---

## Phase 1: Foundation (Ship in 2 Weeks) 🚀

### Goal
Get off password paywall, add persistence, enable multi-clinic deployments

### What to Build

#### 1. Database Models
```python
# Core models only
- Organization (clinic)
- User (belongs to org)
- Job (belongs to org + user)
- AuditLog (for compliance)
```

#### 2. Authentication
```python
- Registration (creates org + first admin user)
- Login/Logout
- Basic session management (no Redis yet)
- Password reset (email-based)
```

#### 3. Job Persistence
```python
- Store jobs in PostgreSQL (not memory)
- Track status: waiting → processing → complete/error
- Store analysis results as JSON
- Basic retry logic
```

#### 4. Usage Tracking
```python
# Simple tier system in database
class Organization:
    plan = Enum["trial", "paid"]
    monthly_job_count = Integer
    max_monthly_jobs = Integer  # 50 for trial, 500 for paid
    
    def can_create_job(self):
        return self.monthly_job_count < self.max_monthly_jobs
```

#### 5. Audit Logging
```python
# Track critical actions
- User login/logout
- Job created/completed
- Settings changed
- User added/removed
```

### What NOT to Build Yet
- ❌ Stripe integration
- ❌ Webhooks
- ❌ Redis (use default Flask sessions)
- ❌ Rate limiting (add in Phase 2)
- ❌ Parameter Store (use Render env vars)
- ❌ Multi-tier pricing (just trial vs paid)

### Migration Strategy

**Step 1: Database Setup**
```bash
# Create models
flask db init
flask db migrate -m "Add organizations and users"
flask db upgrade
```

**Step 2: Backward Compatibility**
```python
# Keep old password route temporarily
@app.route('/legacy-login', methods=['POST'])
def legacy_login():
    # Check old PAYWALL_PASSWORD
    # Auto-create account
    # Redirect to new system
```

**Step 3: Gradual Rollout**
```python
# Feature flag in code
USE_NEW_AUTH = os.getenv('USE_NEW_AUTH', 'false') == 'true'

@app.route('/some-route')
def some_route():
    if USE_NEW_AUTH:
        return new_implementation()
    else:
        return old_implementation()
```

### Configuration (Simplified)

**Use Render Environment Variables:**
```bash
# render.yaml
envVars:
  - key: DATABASE_URL
    fromDatabase: maneiro-db
  - key: SECRET_KEY
    value: generated_secret_here
  - key: OPENAI_API_KEY
    sync: false  # Add manually in dashboard
  - key: AWS_S3_BUCKET
    value: your-bucket
```

**No Parameter Store yet** - save that complexity for Phase 2

### Database Schema Phase 1

```sql
-- organizations table
CREATE TABLE organizations (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    slug VARCHAR(100) UNIQUE NOT NULL,
    email VARCHAR(255) NOT NULL,
    plan VARCHAR(20) DEFAULT 'trial',
    monthly_job_count INTEGER DEFAULT 0,
    max_monthly_jobs INTEGER DEFAULT 50,
    stripe_customer_id VARCHAR(255),  -- NULL for now
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- users table
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    organization_id INTEGER REFERENCES organizations(id),
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    first_name VARCHAR(100),
    last_name VARCHAR(100),
    role VARCHAR(20) DEFAULT 'doctor',
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- jobs table
CREATE TABLE jobs (
    id VARCHAR(100) PRIMARY KEY,
    organization_id INTEGER REFERENCES organizations(id),
    user_id INTEGER REFERENCES users(id),
    status VARCHAR(20) NOT NULL,
    input_filename VARCHAR(255),
    analysis_data JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- audit_logs table
CREATE TABLE audit_logs (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    organization_id INTEGER REFERENCES organizations(id),
    event_type VARCHAR(100) NOT NULL,
    event_description TEXT,
    ip_address VARCHAR(45),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

### Testing Checklist Phase 1

- [ ] User can register (creates org + admin user)
- [ ] User can login
- [ ] User can create jobs (stored in DB)
- [ ] Jobs persist across restarts
- [ ] Usage limits work (trial = 50 jobs/month)
- [ ] Audit log captures actions
- [ ] Old password still works (legacy route)

### Deployment Phase 1

```bash
# 1. Add PostgreSQL on Render
render.yaml:
  databases:
    - name: maneiro-db
      plan: starter

# 2. Run migrations on first deploy
Dockerfile CMD:
  flask db upgrade && gunicorn app:app

# 3. Create first organization manually
flask shell
>>> org = Organization(name="Test Clinic", slug="test-clinic", ...)
>>> user = User(organization_id=org.id, email="admin@test.com", ...)
>>> db.session.add_all([org, user])
>>> db.session.commit()
```

---

## Phase 2: Monetization (Ship in 4 Weeks) 💰

### Goal
Start charging customers, enforce limits, scale horizontally

### What to Build

#### 1. Stripe Integration
```python
# Subscription webhooks
- customer.subscription.created
- customer.subscription.updated
- customer.subscription.deleted
- invoice.payment_succeeded
- invoice.payment_failed

# Stripe updates DB state
def handle_subscription_created(subscription):
    org = Organization.query.filter_by(
        stripe_customer_id=subscription['customer']
    ).first()
    
    org.plan = map_price_to_plan(subscription['items'])
    org.stripe_subscription_id = subscription['id']
    db.session.commit()
```

#### 2. Multi-Tier Pricing
```python
class OrganizationPlan(enum.Enum):
    TRIAL = "trial"         # 50 jobs/month, 3 users
    STARTER = "starter"     # 200 jobs/month, 5 users, $99/mo
    PROFESSIONAL = "professional"  # 1000 jobs/month, 15 users, $299/mo
    ENTERPRISE = "enterprise"      # Unlimited, $999/mo
```

#### 3. Usage Enforcement
```python
@app.route('/api/analyze', methods=['POST'])
@login_required
def analyze():
    if not current_user.organization.can_create_job:
        return jsonify({
            'error': 'Monthly job limit reached',
            'limit': current_user.organization.max_monthly_jobs,
            'used': current_user.organization.monthly_job_count,
            'upgrade_url': url_for('billing.upgrade')
        }), 429
    
    # Process job...
```

#### 4. Redis for Sessions (if scaling)
```python
# Only add if running multiple instances
SESSION_TYPE = 'redis'
SESSION_REDIS = Redis.from_url(os.environ['REDIS_URL'])
```

#### 5. Rate Limiting
```python
from flask_limiter import Limiter

limiter = Limiter(
    app,
    key_func=lambda: current_user.organization_id,
    storage_uri=os.environ['REDIS_URL']
)

@app.route('/api/analyze')
@limiter.limit("100 per hour")
def analyze():
    pass
```

#### 6. Parameter Store (optional)
```python
# Only if you need it
# Most teams just use Render env vars + 1Password
```

### Testing Checklist Phase 2

- [ ] Stripe checkout creates subscription
- [ ] Webhooks update organization plan
- [ ] Usage limits enforced
- [ ] Rate limiting works
- [ ] Sessions persist with Redis
- [ ] Billing page shows usage

---

## Phase 3: Enterprise Features (Ship in 8-12 Weeks) 🏢

### Goal
Close enterprise deals, handle compliance requirements

### What to Build

1. **Team Management**
   - Invite users
   - Role-based access
   - SSO (optional)

2. **Data Retention**
   - Auto-delete files after 90 days
   - User-initiated deletion
   - Audit trail of deletions

3. **Advanced Features**
   - API access
   - Webhooks for customers
   - Custom branding
   - White-label

4. **Compliance**
   - HIPAA BAA (if needed)
   - SOC 2 prep
   - Data export

---

## Critical: Multi-Tenancy First

**Registration Flow:**
```python
@app.route('/register', methods=['POST'])
def register():
    # Step 1: Create organization
    org = Organization(
        name=request.form['clinic_name'],
        slug=slugify(request.form['clinic_name']),
        email=request.form['clinic_email'],
        plan=OrganizationPlan.TRIAL,
        max_users=3,
        max_monthly_jobs=50
    )
    db.session.add(org)
    db.session.flush()  # Get org.id
    
    # Step 2: Create admin user
    user = User(
        organization_id=org.id,
        email=request.form['user_email'],
        role=UserRole.ADMIN,
        first_name=request.form['first_name'],
        last_name=request.form['last_name']
    )
    user.set_password(request.form['password'])
    db.session.add(user)
    db.session.commit()
    
    # Step 3: Create Stripe customer (Phase 2)
    # stripe_customer = stripe.Customer.create(...)
    
    return redirect('/dashboard')
```

**Job Creation:**
```python
@app.route('/api/analyze', methods=['POST'])
@login_required
def analyze():
    # Check org-level limit
    if not current_user.organization.can_create_job:
        return error_response('Org limit reached')
    
    # Create job
    job = Job(
        organization_id=current_user.organization_id,
        user_id=current_user.id,
        ...
    )
    
    # Increment org counter
    current_user.organization.increment_job_count()
    
    db.session.add(job)
    db.session.commit()
```

---

## Data Retention Policy (Add to Phase 1)

```python
class Job(db.Model):
    # ... existing fields ...
    
    # Retention
    retention_days = db.Column(db.Integer, default=90)
    expires_at = db.Column(db.DateTime(timezone=True))
    deleted_at = db.Column(db.DateTime(timezone=True))
    deleted_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    
    def set_expiration(self):
        """Set expiration based on retention policy"""
        self.expires_at = datetime.now(timezone.utc) + timedelta(days=self.retention_days)
    
    def soft_delete(self, user_id):
        """Mark as deleted (don't actually delete for audit)"""
        self.deleted_at = datetime.now(timezone.utc)
        self.deleted_by_user_id = user_id


# Cleanup job (run daily)
@celery.task
def cleanup_expired_jobs():
    """Delete expired jobs and S3 files"""
    expired = Job.query.filter(
        Job.expires_at < datetime.now(timezone.utc),
        Job.deleted_at.is_(None)
    ).all()
    
    for job in expired:
        # Delete S3 file
        if job.input_s3_key:
            s3.delete_object(Bucket=bucket, Key=job.input_s3_key)
        
        # Soft delete
        job.deleted_at = datetime.now(timezone.utc)
    
    db.session.commit()
```

---

## Timeline

| Phase | Duration | Ship Date | Features |
|-------|----------|-----------|----------|
| Phase 1 | 2 weeks | Week 2 | Auth, DB, Jobs, Audit |
| Phase 2 | 2 weeks | Week 4 | Stripe, Limits, Redis |
| Phase 3 | 4-8 weeks | Week 12 | Enterprise |

---

## What Your Developer is Right About

1. **"Lock the product first, then harden it"**
   - ✅ Get Phase 1 working and shipping
   - ✅ Learn from real usage
   - ✅ Add Phase 2 when revenue demands it

2. **"Stripe as event source, not gatekeeper"**
   - ✅ Subscription logic in your DB
   - ✅ Stripe just updates state
   - ✅ Easy to swap providers later

3. **"Clinics are the buyer, not staff"**
   - ✅ Organization model is critical
   - ✅ Shared billing
   - ✅ Seat management

4. **"Audit logging is the real win"**
   - ✅ Answers "who accessed what"
   - ✅ Critical for enterprise deals
   - ✅ Build this in Phase 1

5. **"Rate limiting is a guardrail, not business model"**
   - ✅ Don't rely on it to prevent abuse
   - ✅ Pricing model should prevent abuse
   - ✅ Rate limiting prevents accidents

---

## Conclusion

**Ship Phase 1 in 2 weeks:**
- User auth
- PostgreSQL
- Multi-tenancy (orgs)
- Job persistence
- Audit logging
- Simple trial vs paid

**Ship Phase 2 when you have paying customers:**
- Stripe
- Usage enforcement
- Multi-tier pricing
- Redis
- Rate limiting

This gets you **shipping and learning** instead of **rewriting and stalling**.
