"""
Maneiro.ai Database Models
Multi-tenant architecture with Organizations and Users
"""
from datetime import datetime, timezone
from enum import Enum
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from flask_login import UserMixin

db = SQLAlchemy()
bcrypt = Bcrypt()


class SubscriptionTier(str, Enum):
    FREE = "free"
    BASIC = "basic"
    PROFESSIONAL = "professional"
    ENTERPRISE = "enterprise"


class SubscriptionStatus(str, Enum):
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    TRIALING = "trialing"


class JobStatus(str, Enum):
    WAITING = "waiting"
    PROCESSING = "processing"
    COMPLETE = "complete"
    ERROR = "error"


class JobType(str, Enum):
    ANALYSIS = "analysis"
    LETTER = "letter"
    TRANSCRIPTION = "transcription"


class UserRole(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    DOCTOR = "doctor"
    ASSISTANT = "assistant"


# Tier limits for usage tracking
TIER_LIMITS = {
    SubscriptionTier.FREE: {"jobs_per_month": 5, "team_members": 1},
    SubscriptionTier.BASIC: {"jobs_per_month": 50, "team_members": 3},
    SubscriptionTier.PROFESSIONAL: {"jobs_per_month": 500, "team_members": 10},
    SubscriptionTier.ENTERPRISE: {"jobs_per_month": 999999, "team_members": 999},
}


class Organization(db.Model):
    """Multi-tenant organization"""
    __tablename__ = "organizations"
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    slug = db.Column(db.String(100), unique=True, nullable=False)
    
    # Subscription
    subscription_tier = db.Column(db.String(50), default=SubscriptionTier.FREE.value)
    subscription_status = db.Column(db.String(50), default=SubscriptionStatus.ACTIVE.value)
    stripe_customer_id = db.Column(db.String(100), nullable=True)
    stripe_subscription_id = db.Column(db.String(100), nullable=True)
    
    # Usage tracking
    jobs_this_month = db.Column(db.Integer, default=0)
    usage_reset_date = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    # Relationships
    users = db.relationship("User", backref="organization", lazy="dynamic")
    jobs = db.relationship("Job", backref="organization", lazy="dynamic")
    
    def get_tier_limits(self):
        tier = SubscriptionTier(self.subscription_tier)
        return TIER_LIMITS.get(tier, TIER_LIMITS[SubscriptionTier.FREE])
    
    def can_create_job(self):
        limits = self.get_tier_limits()
        return self.jobs_this_month < limits["jobs_per_month"]
    
    def increment_usage(self):
        self.jobs_this_month += 1
        db.session.commit()


class User(db.Model, UserMixin):
    """User account"""
    __tablename__ = "users"
    
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    username = db.Column(db.String(100), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    
    # Profile
    full_name = db.Column(db.String(200), nullable=True)
    title = db.Column(db.String(100), nullable=True)  # Dr., etc.
    specialty = db.Column(db.String(100), nullable=True)
    
    # Organization
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=True)
    role = db.Column(db.String(50), default=UserRole.DOCTOR.value)
    
    # Status
    is_active = db.Column(db.Boolean, default=True)
    email_verified = db.Column(db.Boolean, default=False)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    last_login = db.Column(db.DateTime, nullable=True)
    
    # Relationships
    jobs = db.relationship("Job", backref="user", lazy="dynamic")
    audit_logs = db.relationship("AuditLog", backref="user", lazy="dynamic")
    
    def set_password(self, password: str):
        self.password_hash = bcrypt.generate_password_hash(password).decode("utf-8")
    
    def check_password(self, password: str) -> bool:
        return bcrypt.check_password_hash(self.password_hash, password)
    
    def is_admin(self) -> bool:
        return self.role in [UserRole.OWNER.value, UserRole.ADMIN.value]
    
    def is_doctor(self) -> bool:
        return self.role in [UserRole.OWNER.value, UserRole.ADMIN.value, UserRole.DOCTOR.value]
    
    def get_initials(self) -> str:
        if self.full_name:
            parts = self.full_name.split()
            if len(parts) >= 2:
                return (parts[0][0] + parts[-1][0]).upper()
            return self.full_name[:2].upper()
        return self.username[:2].upper()


class Job(db.Model):
    """Analysis/Letter job"""
    __tablename__ = "jobs"
    
    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.String(100), unique=True, nullable=False, index=True)
    
    # Type and status
    job_type = db.Column(db.String(50), default=JobType.ANALYSIS.value)
    status = db.Column(db.String(50), default=JobStatus.WAITING.value)
    
    # Progress tracking (v2026.6+)
    stage = db.Column(db.String(50), default="received")
    progress = db.Column(db.Integer, default=0)
    stage_label = db.Column(db.String(200), default="Received")
    
    # Input
    input_text = db.Column(db.Text, nullable=True)
    input_filename = db.Column(db.String(255), nullable=True)
    specialty = db.Column(db.String(50), default="auto")
    template = db.Column(db.String(50), default="standard")
    
    # Output
    result = db.Column(db.JSON, nullable=True)
    error = db.Column(db.Text, nullable=True)
    
    # Metadata
    schema_valid = db.Column(db.Boolean, default=True)
    repair_attempts = db.Column(db.Integer, default=0)
    processing_time_ms = db.Column(db.Integer, nullable=True)
    
    # Relationships
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=True)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    started_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    
    def set_stage(self, stage: str, label: str, progress: int):
        """Update job stage with progress"""
        self.stage = stage
        self.stage_label = label
        self.progress = progress
        db.session.commit()
    
    def complete(self, result: dict):
        """Mark job as complete"""
        self.status = JobStatus.COMPLETE.value
        self.result = result
        self.completed_at = datetime.now(timezone.utc)
        self.stage = "complete"
        self.stage_label = "Complete"
        self.progress = 100
        if self.started_at:
            self.processing_time_ms = int((self.completed_at - self.started_at).total_seconds() * 1000)
        db.session.commit()
    
    def fail(self, error: str):
        """Mark job as failed"""
        self.status = JobStatus.ERROR.value
        self.error = error
        self.completed_at = datetime.now(timezone.utc)
        db.session.commit()


class AuditLog(db.Model):
    """Audit log for compliance"""
    __tablename__ = "audit_logs"
    
    id = db.Column(db.Integer, primary_key=True)
    
    # Event
    event_type = db.Column(db.String(100), nullable=False)
    event_data = db.Column(db.JSON, nullable=True)
    
    # Context
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    organization_id = db.Column(db.Integer, nullable=True)
    ip_address = db.Column(db.String(50), nullable=True)
    user_agent = db.Column(db.String(500), nullable=True)
    
    # Timestamp
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class SubscriptionEvent(db.Model):
    """Stripe subscription events"""
    __tablename__ = "subscription_events"
    
    id = db.Column(db.Integer, primary_key=True)
    stripe_event_id = db.Column(db.String(100), unique=True, nullable=False)
    event_type = db.Column(db.String(100), nullable=False)
    event_data = db.Column(db.JSON, nullable=True)
    processed = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


def init_db(app):
    """Initialize database"""
    with app.app_context():
        # Check if we need to reset
        reset_db = app.config.get("RESET_DB") or os.environ.get("RESET_DB") == "1"
        
        if reset_db:
            db.drop_all()
        
        db.create_all()
        
        # Create default organization if none exists
        if not Organization.query.first():
            org = Organization(
                name="Default Clinic",
                slug="default",
                subscription_tier=SubscriptionTier.PROFESSIONAL.value
            )
            db.session.add(org)
            db.session.commit()


import os  # For init_db
