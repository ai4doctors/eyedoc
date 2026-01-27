"""
Authentication Blueprint (Phase 1)

Routes:
- GET/POST /register: Create organization + first admin user
- GET/POST /login: Email + password authentication
- GET /logout: End session
- GET/POST /team: Manage team members (admin only)

Role-based access:
- ADMIN: sees both doctor + assistant pages, can manage team
- DOCTOR: sees doctor page only
- STAFF: sees assistant page only
"""
import re
from functools import wraps
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, current_user, login_required
from app import db, login_manager
from app.models import Organization, User, OrganizationPlan, UserRole, AuditLog

auth_bp = Blueprint('auth', __name__)


@login_manager.user_loader
def load_user(user_id):
    """Load user by ID for Flask-Login"""
    return User.query.get(int(user_id))


def role_required(*roles):
    """Decorator to require specific roles"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for('auth.login'))
            if current_user.role not in roles:
                flash('You do not have permission to access this page', 'error')
                # Redirect to appropriate page for their role
                if current_user.role == UserRole.STAFF:
                    return redirect(url_for('auth.assistant'))
                elif current_user.role == UserRole.DOCTOR:
                    return redirect(url_for('auth.doctor'))
                else:
                    return redirect(url_for('auth.index'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator


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
    """Main application page - redirect based on role"""
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login'))
    
    # Redirect to appropriate page based on role
    if current_user.role == UserRole.STAFF:
        return redirect(url_for('auth.assistant'))
    elif current_user.role == UserRole.DOCTOR:
        return redirect(url_for('auth.doctor'))
    else:  # ADMIN
        return redirect(url_for('auth.doctor'))


@auth_bp.route('/doctor')
@login_required
@role_required(UserRole.ADMIN, UserRole.DOCTOR)
def doctor():
    """Doctor view - for doctors and admins only"""
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
        username = request.form.get('username', '').strip().lower()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        password_confirm = request.form.get('password_confirm', '')
        
        # Validation
        errors = []
        
        if not clinic_name:
            errors.append('Clinic name is required')
        
        if not first_name or not last_name:
            errors.append('First and last name are required')
        
        if not username:
            errors.append('Username is required')
        elif not re.match(r'^[a-zA-Z0-9_]+$', username):
            errors.append('Username can only contain letters, numbers, and underscores')
        elif User.query.filter_by(username=username).first():
            errors.append('This username is already taken')
        
        if email and not re.match(r'^[^\s@]+@[^\s@]+\.[^\s@]+$', email):
            errors.append('Please enter a valid email address')
        
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
            email=email or f'{username}@{slug}.local',
            plan=OrganizationPlan.TRIAL,
            max_monthly_jobs=50  # Trial limit
        )
        db.session.add(org)
        db.session.flush()  # Get org.id
        
        # Create admin user
        user = User(
            organization_id=org.id,
            username=username,
            email=email if email else None,
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
            f'New organization "{clinic_name}" created with admin user {username}',
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
        username = request.form.get('username', '').strip().lower()
        password = request.form.get('password', '')
        next_page = request.form.get('next', '')
        
        # Find user by username
        user = User.query.filter_by(username=username).first()
        
        if user is None or not user.check_password(password):
            flash('Invalid username or password', 'error')
            return render_template('auth/login.html')
        
        # Check if user is active
        if not user.organization:
            flash('Your organization has been deactivated', 'error')
            return render_template('auth/login.html')
        
        # Log them in
        login_user(user)
        
        # Log the event
        log_audit_event('user_login', f'User {username} logged in')
        
        # Redirect based on role
        if next_page and next_page.startswith('/'):
            return redirect(next_page)
        
        # Staff goes to assistant, others go to doctor view
        if user.role == UserRole.STAFF:
            return redirect(url_for('auth.assistant'))
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
    """User account page"""
    org = current_user.organization
    return render_template('auth/account.html', user=current_user, org=org)


@auth_bp.route('/assistant')
@login_required
@role_required(UserRole.ADMIN, UserRole.STAFF)
def assistant():
    """Assistant mode page - for staff and admins only"""
    return render_template('assistant.html', user=current_user)


@auth_bp.route('/team', methods=['GET', 'POST'])
@login_required
@role_required(UserRole.ADMIN)
def team():
    """Team management page - admin only"""
    org = current_user.organization
    
    if request.method == 'POST':
        action = request.form.get('action', '')
        
        if action == 'add':
            first_name = request.form.get('first_name', '').strip()
            last_name = request.form.get('last_name', '').strip()
            username = request.form.get('username', '').strip().lower()
            role = request.form.get('role', 'staff')
            password = request.form.get('password', '')
            
            # Validation
            if not first_name or not last_name:
                flash('First and last name are required', 'error')
            elif not username:
                flash('Username is required', 'error')
            elif not re.match(r'^[a-zA-Z0-9_]+$', username):
                flash('Username can only contain letters, numbers, and underscores', 'error')
            elif User.query.filter_by(username=username).first():
                flash('This username is already taken', 'error')
            elif len(password) < 8:
                flash('Password must be at least 8 characters', 'error')
            else:
                # Map role string to enum
                user_role = UserRole.STAFF if role == 'staff' else UserRole.DOCTOR
                
                new_user = User(
                    organization_id=org.id,
                    username=username,
                    first_name=first_name,
                    last_name=last_name,
                    role=user_role
                )
                new_user.set_password(password)
                db.session.add(new_user)
                db.session.commit()
                
                log_audit_event('user_created', f'Admin created new {role} user: {username}')
                flash(f'{first_name} {last_name} has been added as {role}', 'success')
        
        elif action == 'delete':
            user_id = request.form.get('user_id', type=int)
            user = User.query.get(user_id)
            
            if user and user.organization_id == org.id and user.id != current_user.id:
                username = user.username
                db.session.delete(user)
                db.session.commit()
                log_audit_event('user_deleted', f'Admin deleted user: {username}')
                flash(f'User has been removed', 'success')
            else:
                flash('Cannot delete this user', 'error')
        
        return redirect(url_for('auth.team'))
    
    # Get all users in org
    users = User.query.filter_by(organization_id=org.id).order_by(User.created_at).all()
    return render_template('auth/team.html', user=current_user, org=org, users=users, UserRole=UserRole)


# Password reset placeholder (Phase 2)
@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    """Password reset request (TODO: implement email sending)"""
    if request.method == 'POST':
        flash('Password reset is not yet implemented. Contact your administrator.', 'info')
    return render_template('auth/forgot_password.html')
