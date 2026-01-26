"""
Database Models (Phase 1)

See docs/reference_multi_tenant_models.py for complete implementation
This is a simplified version for Phase 1

Key Models:
- Organization: Clinic/practice (billable entity)
- User: Belongs to organization
- Job: Analysis task (belongs to org + user)
- AuditLog: Compliance tracking
"""
from datetime import datetime, timezone
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
import enum
from app import db

class OrganizationPlan(enum.Enum):
    TRIAL = "trial"
    PAID = "paid"

class UserRole(enum.Enum):
    ADMIN = "admin"
    DOCTOR = "doctor"
    STAFF = "staff"

class JobStatus(enum.Enum):
    WAITING = "waiting"
    PROCESSING = "processing"
    COMPLETE = "complete"
    ERROR = "error"

class Organization(db.Model):
    __tablename__ = 'organizations'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    slug = db.Column(db.String(100), unique=True, nullable=False)
    email = db.Column(db.String(255), nullable=False)
    plan = db.Column(db.Enum(OrganizationPlan), default=OrganizationPlan.TRIAL)
    monthly_job_count = db.Column(db.Integer, default=0)
    max_monthly_jobs = db.Column(db.Integer, default=50)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    
    users = db.relationship('User', back_populates='organization', lazy='dynamic')
    jobs = db.relationship('Job', back_populates='organization', lazy='dynamic')
    
    @property
    def can_create_job(self):
        return self.monthly_job_count < self.max_monthly_jobs

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    first_name = db.Column(db.String(100))
    last_name = db.Column(db.String(100))
    role = db.Column(db.Enum(UserRole), default=UserRole.DOCTOR)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    
    organization = db.relationship('Organization', back_populates='users')
    jobs = db.relationship('Job', back_populates='user', lazy='dynamic')
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class Job(db.Model):
    __tablename__ = 'jobs'
    
    id = db.Column(db.String(100), primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    status = db.Column(db.Enum(JobStatus), default=JobStatus.WAITING)
    input_filename = db.Column(db.String(255))
    analysis_data = db.Column(db.JSON)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    
    organization = db.relationship('Organization', back_populates='jobs')
    user = db.relationship('User', back_populates='jobs')

class AuditLog(db.Model):
    __tablename__ = 'audit_logs'
    
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'))
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    event_type = db.Column(db.String(100), nullable=False)
    event_description = db.Column(db.Text)
    ip_address = db.Column(db.String(45))
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
