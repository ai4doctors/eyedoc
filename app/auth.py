"""
Authentication routes and utilities
"""
from functools import wraps
from flask import Blueprint, render_template, redirect, url_for, request, flash, jsonify
from flask_login import LoginManager, login_user, logout_user, current_user, login_required
from .models import db, User, AuditLog, SubscriptionStatus, SubscriptionTier
import stripe

auth_bp = Blueprint('auth', __name__)
login_manager = LoginManager()


def init_auth(app):
    """Initialize authentication"""
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Please log in to access this page.'
    

@login_manager.user_loader
def load_user(user_id):
    """Load user by ID"""
    return User.query.get(int(user_id))


def subscription_required(tier=None):
    """Decorator to require active subscription"""
    def decorator(f):
        @wraps(f)
        @login_required
        def decorated_function(*args, **kwargs):
            if not current_user.is_subscribed:
                flash('Please subscribe to access this feature.', 'warning')
                return redirect(url_for('auth.pricing'))
            
            if tier and current_user.subscription_tier.value < tier:
                flash(f'This feature requires {tier} subscription.', 'warning')
                return redirect(url_for('auth.pricing'))
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def usage_limit_check(f):
    """Decorator to check usage limits"""
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if not current_user.can_create_job:
            flash('Monthly job limit reached. Please upgrade your plan.', 'warning')
            return redirect(url_for('auth.pricing'))
        return f(*args, **kwargs)
    return decorated_function


def log_audit_event(event_type, description, metadata=None):
    """Log audit event"""
    if current_user.is_authenticated:
        audit_log = AuditLog(
            user_id=current_user.id,
            event_type=event_type,
            event_description=description,
            ip_address=request.remote_addr,
            user_agent=request.headers.get('User-Agent'),
            metadata=metadata
        )
        db.session.add(audit_log)
        db.session.commit()


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    """User registration"""
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        first_name = request.form.get('first_name', '').strip()
        last_name = request.form.get('last_name', '').strip()
        
        # Validation
        if not email or not password:
            flash('Email and password are required.', 'error')
            return render_template('auth/register.html')
        
        if User.query.filter_by(email=email).first():
            flash('Email already registered.', 'error')
            return render_template('auth/register.html')
        
        # Create user
        user = User(
            email=email,
            first_name=first_name,
            last_name=last_name,
            subscription_status=SubscriptionStatus.TRIAL,
            subscription_tier=SubscriptionTier.FREE
        )
        user.set_password(password)
        
        try:
            # Create Stripe customer
            stripe_customer = stripe.Customer.create(
                email=email,
                name=f"{first_name} {last_name}".strip() or email,
                metadata={'user_id': user.id}
            )
            user.stripe_customer_id = stripe_customer.id
            
            db.session.add(user)
            db.session.commit()
            
            log_audit_event('user_registered', f'User {email} registered')
            
            # Auto-login
            login_user(user)
            flash('Registration successful! Welcome to Maneiro.ai', 'success')
            return redirect(url_for('index'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Registration failed: {str(e)}', 'error')
            return render_template('auth/register.html')
    
    return render_template('auth/register.html')


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    """User login"""
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        remember = request.form.get('remember', False)
        
        user = User.query.filter_by(email=email).first()
        
        if user and user.check_password(password):
            if not user.is_active:
                flash('Account is disabled. Please contact support.', 'error')
                return render_template('auth/login.html')
            
            login_user(user, remember=remember)
            user.last_login_at = datetime.now(timezone.utc)
            db.session.commit()
            
            log_audit_event('user_login', f'User {email} logged in')
            
            next_page = request.args.get('next')
            if next_page:
                return redirect(next_page)
            return redirect(url_for('index'))
        
        flash('Invalid email or password.', 'error')
    
    return render_template('auth/login.html')


@auth_bp.route('/logout')
@login_required
def logout():
    """User logout"""
    log_audit_event('user_logout', f'User {current_user.email} logged out')
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('auth.login'))


@auth_bp.route('/pricing')
def pricing():
    """Pricing page"""
    return render_template('auth/pricing.html')


@auth_bp.route('/subscribe/<tier>', methods=['POST'])
@login_required
def subscribe(tier):
    """Create Stripe checkout session"""
    try:
        # Map tiers to Stripe price IDs (set these in your Stripe dashboard)
        price_ids = {
            'basic': 'price_basic_monthly',
            'professional': 'price_professional_monthly',
            'enterprise': 'price_enterprise_monthly'
        }
        
        if tier not in price_ids:
            return jsonify({'error': 'Invalid tier'}), 400
        
        # Create checkout session
        checkout_session = stripe.checkout.Session.create(
            customer=current_user.stripe_customer_id,
            mode='subscription',
            line_items=[{
                'price': price_ids[tier],
                'quantity': 1,
            }],
            success_url=url_for('auth.subscription_success', _external=True),
            cancel_url=url_for('auth.pricing', _external=True),
            metadata={
                'user_id': current_user.id,
                'tier': tier
            }
        )
        
        return jsonify({'checkout_url': checkout_session.url})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@auth_bp.route('/subscription/success')
@login_required
def subscription_success():
    """Subscription success page"""
    flash('Subscription activated! Thank you for subscribing.', 'success')
    log_audit_event('subscription_created', f'User subscribed')
    return redirect(url_for('index'))


@auth_bp.route('/account')
@login_required
def account():
    """User account page"""
    return render_template('auth/account.html', user=current_user)


@auth_bp.route('/account/update', methods=['POST'])
@login_required
def update_account():
    """Update account details"""
    try:
        current_user.first_name = request.form.get('first_name', '').strip()
        current_user.last_name = request.form.get('last_name', '').strip()
        current_user.license_number = request.form.get('license_number', '').strip()
        current_user.clinic_name = request.form.get('clinic_name', '').strip()
        
        db.session.commit()
        log_audit_event('account_updated', 'User updated account details')
        flash('Account updated successfully.', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Update failed: {str(e)}', 'error')
    
    return redirect(url_for('auth.account'))


@auth_bp.route('/account/cancel-subscription', methods=['POST'])
@login_required
def cancel_subscription():
    """Cancel subscription"""
    try:
        if current_user.stripe_subscription_id:
            stripe.Subscription.delete(current_user.stripe_subscription_id)
            
            current_user.subscription_status = SubscriptionStatus.CANCELED
            db.session.commit()
            
            log_audit_event('subscription_canceled', 'User canceled subscription')
            flash('Subscription canceled. You can continue using until the end of the billing period.', 'info')
        
    except Exception as e:
        flash(f'Cancellation failed: {str(e)}', 'error')
    
    return redirect(url_for('auth.account'))
