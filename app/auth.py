"""
Maneiro.ai Authentication Routes
"""
from datetime import datetime, timezone
from functools import wraps
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, BooleanField
from wtforms.validators import DataRequired, Email, Length, EqualTo

from app.models import db, User, Organization, AuditLog, UserRole

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


class LoginForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired()])
    password = PasswordField("Password", validators=[DataRequired()])
    remember = BooleanField("Remember Me")


class RegisterForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(min=3, max=50)])
    email = StringField("Email", validators=[DataRequired(), Email()])
    password = PasswordField("Password", validators=[DataRequired(), Length(min=8)])
    confirm = PasswordField("Confirm Password", validators=[DataRequired(), EqualTo("password")])
    full_name = StringField("Full Name", validators=[Length(max=200)])


def log_audit(event_type: str, event_data: dict = None):
    """Log audit event"""
    try:
        log = AuditLog(
            event_type=event_type,
            event_data=event_data,
            user_id=current_user.id if current_user.is_authenticated else None,
            organization_id=current_user.organization_id if current_user.is_authenticated else None,
            ip_address=request.remote_addr,
            user_agent=request.user_agent.string[:500] if request.user_agent else None
        )
        db.session.add(log)
        db.session.commit()
    except Exception:
        pass  # Don't fail on audit log errors


def role_required(*roles):
    """Decorator to require specific roles"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for("auth.login"))
            if current_user.role not in [r.value if hasattr(r, 'value') else r for r in roles]:
                flash("You don't have permission to access this page.", "error")
                return redirect(url_for("index"))
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def usage_limit_check(f):
    """Decorator to check usage limits"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if current_user.is_authenticated and current_user.organization:
            if not current_user.organization.can_create_job():
                return {"ok": False, "error": "Monthly usage limit reached. Please upgrade your plan."}, 429
        return f(*args, **kwargs)
    return decorated_function


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter(
            (User.username == form.username.data) | (User.email == form.username.data)
        ).first()
        
        if user and user.check_password(form.password.data):
            if not user.is_active:
                flash("Your account is disabled.", "error")
                return render_template("auth/login.html", form=form)
            
            login_user(user, remember=form.remember.data)
            user.last_login = datetime.now(timezone.utc)
            db.session.commit()
            
            log_audit("user_login", {"username": user.username})
            
            next_page = request.args.get("next")
            return redirect(next_page if next_page else url_for("index"))
        
        flash("Invalid username or password.", "error")
    
    return render_template("auth/login.html", form=form)


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    
    form = RegisterForm()
    if form.validate_on_submit():
        # Check if user exists
        if User.query.filter_by(username=form.username.data).first():
            flash("Username already taken.", "error")
            return render_template("auth/register.html", form=form)
        
        if User.query.filter_by(email=form.email.data).first():
            flash("Email already registered.", "error")
            return render_template("auth/register.html", form=form)
        
        # Get or create default organization
        org = Organization.query.filter_by(slug="default").first()
        if not org:
            org = Organization(name="Default Clinic", slug="default")
            db.session.add(org)
            db.session.commit()
        
        # Create user
        user = User(
            username=form.username.data,
            email=form.email.data,
            full_name=form.full_name.data or form.username.data,
            organization_id=org.id,
            role=UserRole.DOCTOR.value
        )
        user.set_password(form.password.data)
        
        db.session.add(user)
        db.session.commit()
        
        log_audit("user_register", {"username": user.username})
        
        flash("Registration successful! Please log in.", "success")
        return redirect(url_for("auth.login"))
    
    return render_template("auth/register.html", form=form)


@auth_bp.route("/logout")
@login_required
def logout():
    log_audit("user_logout", {"username": current_user.username})
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))


@auth_bp.route("/account")
@login_required
def account():
    return render_template("auth/account.html", user=current_user)


@auth_bp.route("/team")
@login_required
@role_required(UserRole.OWNER, UserRole.ADMIN)
def team():
    if not current_user.organization:
        flash("No organization found.", "error")
        return redirect(url_for("index"))
    
    members = User.query.filter_by(organization_id=current_user.organization_id).all()
    return render_template("auth/team.html", members=members, org=current_user.organization)
