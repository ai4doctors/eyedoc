"""
Database models for Maneiro.ai
"""
from datetime import datetime, timezone
from typing import Optional
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from flask_bcrypt import Bcrypt
import enum

db = SQLAlchemy()
bcrypt = Bcrypt()


class SubscriptionStatus(enum.Enum):
    """Subscription status enum"""
    TRIAL = "trial"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    EXPIRED = "expired"


class SubscriptionTier(enum.Enum):
    """Subscription tier enum"""
    FREE = "free"
    BASIC = "basic"
    PROFESSIONAL = "professional"
    ENTERPRISE = "enterprise"


class User(UserMixin, db.Model):
    """User model"""
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    
    # Profile
    first_name = db.Column(db.String(100))
    last_name = db.Column(db.String(100))
    license_number = db.Column(db.String(100))
    clinic_name = db.Column(db.String(255))
    
    # Subscription
    subscription_status = db.Column(
        db.Enum(SubscriptionStatus),
        default=SubscriptionStatus.TRIAL,
        nullable=False
    )
    subscription_tier = db.Column(
        db.Enum(SubscriptionTier),
        default=SubscriptionTier.FREE,
        nullable=False
    )
    stripe_customer_id = db.Column(db.String(255), unique=True, index=True)
    stripe_subscription_id = db.Column(db.String(255), unique=True, index=True)
    subscription_end_date = db.Column(db.DateTime(timezone=True))
    
    # Usage tracking
    monthly_job_count = db.Column(db.Integer, default=0, nullable=False)
    monthly_job_reset_date = db.Column(db.DateTime(timezone=True))
    total_jobs = db.Column(db.Integer, default=0, nullable=False)
    
    # Timestamps
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False
    )
    last_login_at = db.Column(db.DateTime(timezone=True))
    
    # Status
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    is_verified = db.Column(db.Boolean, default=False, nullable=False)
    
    # Relationships
    jobs = db.relationship('Job', back_populates='user', lazy='dynamic')
    audit_logs = db.relationship('AuditLog', back_populates='user', lazy='dynamic')
    
    def set_password(self, password: str):
        """Hash and set password"""
        self.password_hash = bcrypt.generate_password_hash(password).decode('utf-8')
    
    def check_password(self, password: str) -> bool:
        """Verify password"""
        return bcrypt.check_password_hash(self.password_hash, password)
    
    @property
    def full_name(self) -> str:
        """Get full name"""
        if self.first_name and self.last_name:
            return f"{self.first_name} {self.last_name}"
        return self.email
    
    @property
    def is_subscribed(self) -> bool:
        """Check if user has active subscription"""
        return self.subscription_status in [
            SubscriptionStatus.TRIAL,
            SubscriptionStatus.ACTIVE
        ]
    
    @property
    def can_create_job(self) -> bool:
        """Check if user can create a new job based on tier limits"""
        if self.subscription_tier == SubscriptionTier.FREE:
            return self.monthly_job_count < 5
        elif self.subscription_tier == SubscriptionTier.BASIC:
            return self.monthly_job_count < 50
        elif self.subscription_tier == SubscriptionTier.PROFESSIONAL:
            return self.monthly_job_count < 500
        # Enterprise: unlimited
        return True
    
    def increment_job_count(self):
        """Increment monthly job count"""
        now = datetime.now(timezone.utc)
        
        # Reset counter if it's a new month
        if (not self.monthly_job_reset_date or 
            self.monthly_job_reset_date.month != now.month):
            self.monthly_job_count = 0
            self.monthly_job_reset_date = now
        
        self.monthly_job_count += 1
        self.total_jobs += 1
    
    def __repr__(self):
        return f'<User {self.email}>'


class JobStatus(enum.Enum):
    """Job status enum"""
    WAITING = "waiting"
    PROCESSING = "processing"
    COMPLETE = "complete"
    ERROR = "error"


class JobType(enum.Enum):
    """Job type enum"""
    ANALYSIS = "analysis"
    TRANSCRIPTION = "transcription"


class Job(db.Model):
    """Job model for tracking analysis and transcription jobs"""
    __tablename__ = 'jobs'
    
    id = db.Column(db.String(100), primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    
    # Job details
    job_type = db.Column(db.Enum(JobType), nullable=False)
    status = db.Column(db.Enum(JobStatus), default=JobStatus.WAITING, nullable=False)
    
    # Input/Output
    input_filename = db.Column(db.String(255))
    input_s3_key = db.Column(db.String(500))
    output_s3_key = db.Column(db.String(500))
    
    # Analysis data (stored as JSON)
    analysis_data = db.Column(db.JSON)
    
    # Transcription data
    transcript_text = db.Column(db.Text)
    aws_transcribe_job_id = db.Column(db.String(255))
    
    # Error handling
    error_message = db.Column(db.Text)
    retry_count = db.Column(db.Integer, default=0)
    
    # Timestamps
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False
    )
    completed_at = db.Column(db.DateTime(timezone=True))
    
    # Relationships
    user = db.relationship('User', back_populates='jobs')
    
    def __repr__(self):
        return f'<Job {self.id} ({self.status.value})>'


class AuditLog(db.Model):
    """Audit log for compliance tracking"""
    __tablename__ = 'audit_logs'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    
    # Event details
    event_type = db.Column(db.String(100), nullable=False, index=True)
    event_description = db.Column(db.Text)
    
    # Request details
    ip_address = db.Column(db.String(45))
    user_agent = db.Column(db.String(500))
    
    # Additional data (stored as JSON)
    metadata = db.Column(db.JSON)
    
    # Timestamp
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True
    )
    
    # Relationships
    user = db.relationship('User', back_populates='audit_logs')
    
    def __repr__(self):
        return f'<AuditLog {self.event_type} by User {self.user_id}>'


class SubscriptionEvent(db.Model):
    """Track Stripe subscription events"""
    __tablename__ = 'subscription_events'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    
    # Stripe details
    stripe_event_id = db.Column(db.String(255), unique=True, nullable=False)
    stripe_subscription_id = db.Column(db.String(255), index=True)
    
    # Event details
    event_type = db.Column(db.String(100), nullable=False)
    event_data = db.Column(db.JSON)
    
    # Timestamp
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )
    
    def __repr__(self):
        return f'<SubscriptionEvent {self.event_type}>'
