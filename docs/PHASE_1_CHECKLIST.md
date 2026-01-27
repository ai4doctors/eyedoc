# Phase 1 Implementation Checklist (2 Week Sprint)

## Week 1: Database + Auth

### Day 1-2: Database Setup
- [ ] Install PostgreSQL locally with Docker
  ```bash
  docker run -d -p 5432:5432 \
    -e POSTGRES_DB=maneiro \
    -e POSTGRES_USER=maneiro_user \
    -e POSTGRES_PASSWORD=dev_password \
    postgres:15-alpine
  ```

- [ ] Create base models
  ```python
  # models.py - simplified version
  - Organization (name, slug, email, plan, monthly_job_count, max_monthly_jobs)
  - User (organization_id, email, password_hash, first_name, last_name, role)
  - Job (organization_id, user_id, status, analysis_data)
  - AuditLog (organization_id, user_id, event_type, event_description)
  ```

- [ ] Set up Flask-Migrate
  ```bash
  flask db init
  flask db migrate -m "Initial schema"
  flask db upgrade
  ```

- [ ] Test locally
  ```bash
  flask shell
  >>> from models import Organization, User
  >>> org = Organization(name="Test Clinic", slug="test", email="test@test.com")
  >>> db.session.add(org)
  >>> db.session.commit()
  ```

### Day 3-4: Authentication
- [ ] Implement registration route
  - Creates organization + first admin user
  - Uses Flask-Login for session management
  - No email verification yet (add later)

- [ ] Implement login route
  - Check password with bcrypt
  - Set session
  - Redirect to dashboard

- [ ] Implement logout route

- [ ] Add `@login_required` decorator to protected routes

- [ ] Test the flow
  ```
  1. Visit /register
  2. Fill form (clinic name, user email, password)
  3. Submit â†’ creates org + user
  4. Redirect to /dashboard
  5. Logout
  6. Login again
  ```

### Day 5: Job Persistence
- [ ] Update analyze route to create Job in database
  ```python
  @app.route('/api/analyze', methods=['POST'])
  @login_required
  def analyze():
      # Create job record
      job = Job(
          id=f"job_{uuid.uuid4().hex}",
          organization_id=current_user.organization_id,
          user_id=current_user.id,
          status=JobStatus.WAITING
      )
      db.session.add(job)
      db.session.commit()
      
      # Process analysis...
      
      # Update job
      job.status = JobStatus.COMPLETE
      job.analysis_data = analysis_result
      db.session.commit()
  ```

- [ ] Update status route to query database
  ```python
  @app.route('/api/analyze/status/<job_id>')
  @login_required
  def analyze_status(job_id):
      job = Job.query.filter_by(
          id=job_id,
          organization_id=current_user.organization_id
      ).first_or_404()
      
      return jsonify({
          'status': job.status.value,
          'analysis': job.analysis_data
      })
  ```

- [ ] Test that jobs persist across restarts

---

## Week 2: Usage Limits + Audit Logging + Deploy

### Day 6-7: Usage Tracking
- [ ] Implement usage check
  ```python
  @app.route('/api/analyze', methods=['POST'])
  @login_required
  def analyze():
      # Check org limit
      if not current_user.organization.can_create_job:
          return jsonify({
              'error': 'Monthly limit reached',
              'current': current_user.organization.monthly_job_count,
              'max': current_user.organization.max_monthly_jobs
          }), 429
      
      # Create job...
      current_user.organization.increment_job_count()
      db.session.commit()
  ```

- [ ] Add usage display to dashboard
  ```html
  <div class="usage-meter">
    <p>Jobs this month: {{ current_user.organization.monthly_job_count }} / {{ current_user.organization.max_monthly_jobs }}</p>
    <progress value="{{ current_user.organization.monthly_job_count }}" max="{{ current_user.organization.max_monthly_jobs }}"></progress>
  </div>
  ```

- [ ] Test limit enforcement
  - Create 50 jobs (trial limit)
  - Verify 51st job fails with 429 error

### Day 8: Audit Logging
- [ ] Create helper function
  ```python
  def log_audit_event(event_type, description, metadata=None):
      log = AuditLog(
          organization_id=current_user.organization_id,
          user_id=current_user.id,
          event_type=event_type,
          event_description=description,
          ip_address=request.remote_addr,
          metadata=metadata
      )
      db.session.add(log)
      db.session.commit()
  ```

- [ ] Add to critical actions
  ```python
  # After successful login
  log_audit_event('user_login', f'User {current_user.email} logged in')
  
  # After job creation
  log_audit_event('job_created', f'Job {job.id} created', {'filename': job.input_filename})
  
  # After job completion
  log_audit_event('job_completed', f'Job {job.id} completed')
  ```

- [ ] Create audit log viewer (admin only)
  ```python
  @app.route('/admin/audit-logs')
  @login_required
  def audit_logs():
      if current_user.role != UserRole.ADMIN:
          abort(403)
      
      logs = AuditLog.query.filter_by(
          organization_id=current_user.organization_id
      ).order_by(AuditLog.created_at.desc()).limit(100).all()
      
      return render_template('audit_logs.html', logs=logs)
  ```

### Day 9: Legacy Migration
- [ ] Keep old password route as fallback
  ```python
  @app.route('/legacy-check', methods=['POST'])
  def legacy_check():
      """Temporary route for old clients"""
      password = request.form.get('password')
      if password == os.environ.get('PAYWALL_PASSWORD'):
          # Auto-create/login legacy user
          user = User.query.filter_by(email='legacy@maneiro.ai').first()
          if not user:
              # Create default org + user
              org = Organization(...)
              user = User(organization_id=org.id, ...)
              db.session.add_all([org, user])
              db.session.commit()
          
          login_user(user)
          return redirect('/')
      
      return redirect('/login')
  ```

- [ ] Update frontend to try new auth first, fall back to legacy

### Day 10: Deploy to Render
- [ ] Add PostgreSQL database in Render dashboard

- [ ] Update render.yaml
  ```yaml
  databases:
    - name: maneiro-db
      plan: starter
      databaseName: maneiro
  
  services:
    - type: web
      name: maneiro-web
      envVars:
        - key: DATABASE_URL
          fromDatabase:
            name: maneiro-db
            property: connectionString
  ```

- [ ] Update Dockerfile to run migrations
  ```dockerfile
  CMD flask db upgrade && gunicorn wsgi:app --bind 0.0.0.0:$PORT
  ```

- [ ] Deploy
  ```bash
  git add .
  git commit -m "Add Phase 1: Auth + DB + Usage tracking"
  git push origin main
  ```

- [ ] Create first organization in production
  ```bash
  # In Render shell
  flask shell
  >>> from models import Organization, User
  >>> org = Organization(name="Launch Clinic", slug="launch", ...)
  >>> user = User(organization_id=org.id, email="you@example.com", ...)
  >>> user.set_password("secure_password")
  >>> db.session.add_all([org, user])
  >>> db.session.commit()
  ```

- [ ] Test production
  - [ ] Visit your-app.onrender.com
  - [ ] Login with created account
  - [ ] Upload a file
  - [ ] Verify job is saved to database
  - [ ] Check audit logs

---

## Verification Checklist

Before considering Phase 1 complete:

- [ ] User can register (creates org + user)
- [ ] User can login/logout
- [ ] Jobs are stored in database (survive restarts)
- [ ] Usage limits work (50 jobs/month for trial)
- [ ] Audit log captures events
- [ ] Dashboard shows usage meter
- [ ] Old password still works (legacy route)
- [ ] Everything deployed to Render
- [ ] PostgreSQL connected and working
- [ ] Can create multiple organizations

---

## What You've Achieved

After Phase 1 (2 weeks), you have:
- âœ… Multi-tenant architecture (orgs + users)
- âœ… Persistent job storage
- âœ… Usage tracking and limits
- âœ… Audit trail for compliance
- âœ… Scalable foundation for Phase 2

**You can now:**
- Onboard multiple clinics
- Track who did what
- Enforce usage limits
- Prepare for billing (Phase 2)

---

## What to Skip (For Now)

Don't build these yet:
- âŒ Stripe integration (Phase 2)
- âŒ Email verification (Phase 2)
- âŒ Password reset (Phase 2)
- âŒ Team invites (Phase 2)
- âŒ Redis sessions (Phase 2)
- âŒ Rate limiting (Phase 2)
- âŒ Complex roles (Phase 2)

Just get the foundation working and shipping!

---

## Success Metrics for Phase 1

You'll know Phase 1 is successful when:
- [ ] You can demo to a clinic
- [ ] Multiple users can access separately
- [ ] Jobs don't disappear on restart
- [ ] You can show usage dashboard
- [ ] You can show audit log
- [ ] You sleep better knowing data persists

**Then move to Phase 2: Stripe + Billing**
