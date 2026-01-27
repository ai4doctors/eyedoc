"""
Database Models (Phase 1)

Key Models:
- Organization: Clinic/practice (billable entity)
- User: Belongs to organization
- Job: Analysis task (belongs to org + user) - now persisted to Postgres
- AuditLog: Compliance tracking
- PubMedCache: Cache for PubMed queries to reduce latency
"""
from datetime import datetime, timezone
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
import enum
import hashlib
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
    
    def increment_job_count(self):
        """Increment monthly job count"""
        self.monthly_job_count = (self.monthly_job_count or 0) + 1


class User(UserMixin, db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False)
    username = db.Column(db.String(100), unique=True, nullable=False)
    email = db.Column(db.String(255), nullable=True)
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
    """
    Analysis job - now persisted to Postgres for multi-worker support.
    
    Stores all job state including progress, analysis results, and error info.
    Survives worker restarts and supports horizontal scaling.
    """
    __tablename__ = 'jobs'
    
    # Primary key
    id = db.Column(db.String(100), primary_key=True)
    
    # Ownership (nullable for anonymous/legacy jobs)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    
    # Status tracking
    status = db.Column(db.String(20), default='waiting', index=True)
    stage = db.Column(db.String(50))  # extracting, analyzing, structuring, references, citations, complete
    stage_label = db.Column(db.String(100))  # Human-readable stage name
    progress = db.Column(db.Integer, default=0)  # 0-100
    error = db.Column(db.Text)  # Error message if failed
    
    # Input tracking
    input_filename = db.Column(db.String(255))
    upload_path = db.Column(db.String(500))  # Local file path
    upload_name = db.Column(db.String(255))  # Original filename
    force_ocr = db.Column(db.Boolean, default=False)
    
    # Results (JSON)
    analysis_data = db.Column(db.JSON)  # Full analysis result
    transcript = db.Column(db.Text)  # For audio transcription jobs
    
    # Timestamps
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime(timezone=True), onupdate=lambda: datetime.now(timezone.utc))
    heartbeat_at = db.Column(db.DateTime(timezone=True))  # For stale job detection
    
    # Resume tracking
    resume_started = db.Column(db.Boolean, default=False)
    
    # S3 keys for large files
    media_key = db.Column(db.String(500))  # S3 key for audio/media
    language = db.Column(db.String(20))  # For transcription
    mode = db.Column(db.String(20))  # dictation or live
    
    # Relationships
    organization = db.relationship('Organization', back_populates='jobs')
    user = db.relationship('User', back_populates='jobs')
    
    def to_dict(self):
        """Convert job to dictionary for API responses"""
        result = {
            'id': self.id,
            'status': self.status,
            'stage': self.stage,
            'stage_label': self.stage_label,
            'progress': self.progress,
            'error': self.error,
            'input_filename': self.input_filename,
            'upload_path': self.upload_path,
            'upload_name': self.upload_name,
            'force_ocr': self.force_ocr,
            'data': self.analysis_data,
            'transcript': self.transcript,
            'resume_started': self.resume_started,
            'media_key': self.media_key,
            'language': self.language,
            'mode': self.mode,
        }
        if self.created_at:
            result['created_at'] = self.created_at.isoformat()
        if self.updated_at:
            result['updated_at'] = self.updated_at.isoformat()
        if self.heartbeat_at:
            result['heartbeat_at'] = self.heartbeat_at.isoformat()
        return result
    
    def update_from_dict(self, data):
        """Update job from dictionary (used by set_job)"""
        field_mapping = {
            'status': 'status',
            'stage': 'stage',
            'stage_label': 'stage_label',
            'progress': 'progress',
            'error': 'error',
            'input_filename': 'input_filename',
            'upload_path': 'upload_path',
            'upload_name': 'upload_name',
            'force_ocr': 'force_ocr',
            'data': 'analysis_data',
            'transcript': 'transcript',
            'resume_started': 'resume_started',
            'media_key': 'media_key',
            'language': 'language',
            'mode': 'mode',
        }
        for key, attr in field_mapping.items():
            if key in data:
                setattr(self, attr, data[key])
        
        # Handle timestamps
        if 'updated_at' in data:
            self.updated_at = datetime.now(timezone.utc)
        if 'heartbeat_at' in data:
            self.heartbeat_at = datetime.now(timezone.utc)


class AuditLog(db.Model):
    __tablename__ = 'audit_logs'
    
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'))
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    event_type = db.Column(db.String(100), nullable=False)
    event_description = db.Column(db.Text)
    ip_address = db.Column(db.String(45))
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class PubMedCache(db.Model):
    """
    Cache for PubMed query results to reduce latency and API calls.
    
    Queries are hashed by normalized diagnosis terms.
    Cache entries expire after 7 days.
    """
    __tablename__ = 'pubmed_cache'
    
    id = db.Column(db.Integer, primary_key=True)
    query_hash = db.Column(db.String(64), unique=True, nullable=False, index=True)
    query_terms = db.Column(db.Text)  # Original terms for debugging
    results = db.Column(db.JSON)  # Cached PubMed results
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expires_at = db.Column(db.DateTime(timezone=True))
    hit_count = db.Column(db.Integer, default=0)  # Track cache hits
    
    @staticmethod
    def make_hash(terms):
        """Create consistent hash from list of diagnosis terms"""
        # Normalize: lowercase, sort, dedupe
        normalized = sorted(set(t.lower().strip() for t in terms if t and t.strip()))
        key = "|".join(normalized)
        return hashlib.sha256(key.encode()).hexdigest()
    
    @classmethod
    def get_cached(cls, terms, max_age_days=7):
        """Get cached results if fresh enough"""
        query_hash = cls.make_hash(terms)
        entry = cls.query.filter_by(query_hash=query_hash).first()
        if entry:
            # Check expiration
            age = datetime.now(timezone.utc) - entry.created_at
            if age.days < max_age_days:
                entry.hit_count = (entry.hit_count or 0) + 1
                db.session.commit()
                return entry.results
        return None
    
    @classmethod
    def set_cached(cls, terms, results, max_age_days=7):
        """Cache query results"""
        query_hash = cls.make_hash(terms)
        entry = cls.query.filter_by(query_hash=query_hash).first()
        if entry:
            entry.results = results
            entry.created_at = datetime.now(timezone.utc)
            entry.hit_count = 0
        else:
            entry = cls(
                query_hash=query_hash,
                query_terms="|".join(terms),
                results=results,
                expires_at=datetime.now(timezone.utc)
            )
            db.session.add(entry)
        db.session.commit()
