"""
Authentication Blueprint (Phase 1)

Routes:
- GET/POST /register: Create organization + first admin user
- GET/POST /login: Email + password authentication
- GET /logout: End session
"""
import re
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, current_user, login_required
from app import db, login_manager
from app.models import Organization, User, OrganizationPlan, UserRole, AuditLog

auth_bp = Blueprint('auth', __name__)


@login_manager.user_loader
def load_user(user_id):
    """Load user by ID for Flask-Login"""
    return User.query.get(int(user_id))


def log_audit_event(event_type, description, user_id=None, org_id=None):
    """Log an audit event for compliance tracking"""
    try:
        log = AuditLog(
            organization_id=org_id or (current_user.organization_id if current_user.is_authenticated else None),
            user_id=user_id or (current_user.id if current_user.is_authenticated else None),
            event_type=event_type,
            event_description=description,
            ip_address=request.remote_addr
        )
        db.session.add(log)
        db.session.commit()
    except Exception:
        pass  # Don't fail if audit logging fails


def slugify(text):
    """Convert text to URL-friendly slug"""
    text = text.lower().strip()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[\s_-]+', '-', text)
    text = re.sub(r'^-+|-+$', '', text)
    return text


@auth_bp.route('/')
def index():
    """Main application page"""
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login'))
    return render_template('index.html', user=current_user)


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    """Register a new organization and admin user"""
    if current_user.is_authenticated:
        return redirect(url_for('auth.index'))
    
    if request.method == 'POST':
        # Get form data
        clinic_name = request.form.get('clinic_name', '').strip()
        first_name = request.form.get('first_name', '').strip()
        last_name = request.form.get('last_name', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        password_confirm = request.form.get('password_confirm', '')
        
        # Validation
        errors = []
        
        if not clinic_name:
            errors.append('Clinic name is required')
        
        if not first_name or not last_name:
            errors.append('First and last name are required')
        
        if not email:
            errors.append('Email is required')
        elif not re.match(r'^[^\s@]+@[^\s@]+\.[^\s@]+$', email):
            errors.append('Please enter a valid email address')
        elif User.query.filter_by(email=email).first():
            errors.append('An account with this email already exists')
        
        if len(password) < 8:
            errors.append('Password must be at least 8 characters')
        elif password != password_confirm:
            errors.append('Passwords do not match')
        
        if errors:
            for error in errors:
                flash(error, 'error')
            return render_template('auth/register.html')
        
        # Create organization
        slug = slugify(clinic_name)
        base_slug = slug
        counter = 1
        while Organization.query.filter_by(slug=slug).first():
            slug = f"{base_slug}-{counter}"
            counter += 1
        
        org = Organization(
            name=clinic_name,
            slug=slug,
            email=email,
            plan=OrganizationPlan.TRIAL,
            max_monthly_jobs=50  # Trial limit
        )
        db.session.add(org)
        db.session.flush()  # Get org.id
        
        # Create admin user
        user = User(
            organization_id=org.id,
            email=email,
            first_name=first_name,
            last_name=last_name,
            role=UserRole.ADMIN
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        
        # Log the registration
        log_audit_event(
            'user_registered',
            f'New organization "{clinic_name}" created with admin user {email}',
            user_id=user.id,
            org_id=org.id
        )
        
        # Log them in
        login_user(user)
        
        flash('Welcome to Maneiro! Your account has been created.', 'success')
        return redirect(url_for('auth.index'))
    
    return render_template('auth/register.html')


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    """Log in an existing user"""
    if current_user.is_authenticated:
        return redirect(url_for('auth.index'))
    
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        next_page = request.form.get('next', '')
        
        # Find user
        user = User.query.filter_by(email=email).first()
        
        if user is None or not user.check_password(password):
            flash('Invalid email or password', 'error')
            return render_template('auth/login.html')
        
        # Check if user is active
        if not user.organization:
            flash('Your organization has been deactivated', 'error')
            return render_template('auth/login.html')
        
        # Log them in
        login_user(user)
        
        # Log the event
        log_audit_event('user_login', f'User {email} logged in')
        
        # Redirect to next page or index
        if next_page and next_page.startswith('/'):
            return redirect(next_page)
        return redirect(url_for('auth.index'))
    
    return render_template('auth/login.html')


@auth_bp.route('/logout')
@login_required
def logout():
    """Log out the current user"""
    email = current_user.email
    log_audit_event('user_logout', f'User {email} logged out')
    logout_user()
    flash('You have been logged out', 'info')
    return redirect(url_for('auth.login'))


@auth_bp.route('/account')
@login_required
def account():
    """User account page (placeholder)"""
    org = current_user.organization
    return render_template('auth/account.html', user=current_user, org=org)


@auth_bp.route('/assistant')
@login_required
def assistant():
    """Assistant mode page for front desk staff"""
    return render_template('assistant.html', user=current_user)


# Password reset placeholder (Phase 2)
@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    """Password reset request (TODO: implement email sending)"""
    if request.method == 'POST':
        flash('Password reset is not yet implemented. Contact your administrator.', 'info')
    return render_template('auth/forgot_password.html')
