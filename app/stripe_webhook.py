"""
Stripe webhook handler
"""
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify
from .models import db, User, SubscriptionEvent, SubscriptionStatus, SubscriptionTier
import stripe
import os

webhook_bp = Blueprint('webhook', __name__)


@webhook_bp.route('/stripe-webhook', methods=['POST'])
def stripe_webhook():
    """Handle Stripe webhook events"""
    payload = request.data
    sig_header = request.headers.get('Stripe-Signature')
    webhook_secret = os.environ.get('STRIPE_WEBHOOK_SECRET')
    
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, webhook_secret
        )
    except ValueError:
        return jsonify({'error': 'Invalid payload'}), 400
    except stripe.error.SignatureVerificationError:
        return jsonify({'error': 'Invalid signature'}), 400
    
    # Handle the event
    event_type = event['type']
    data = event['data']['object']
    
    try:
        if event_type == 'customer.subscription.created':
            handle_subscription_created(data)
        
        elif event_type == 'customer.subscription.updated':
            handle_subscription_updated(data)
        
        elif event_type == 'customer.subscription.deleted':
            handle_subscription_deleted(data)
        
        elif event_type == 'invoice.payment_succeeded':
            handle_payment_succeeded(data)
        
        elif event_type == 'invoice.payment_failed':
            handle_payment_failed(data)
        
        # Log the event
        log_subscription_event(event)
        
        return jsonify({'status': 'success'}), 200
        
    except Exception as e:
        print(f"Webhook error: {e}")
        return jsonify({'error': str(e)}), 500


def handle_subscription_created(subscription):
    """Handle subscription created"""
    customer_id = subscription['customer']
    user = User.query.filter_by(stripe_customer_id=customer_id).first()
    
    if user:
        user.stripe_subscription_id = subscription['id']
        user.subscription_status = SubscriptionStatus.ACTIVE
        
        # Map Stripe product to tier
        tier = get_tier_from_subscription(subscription)
        user.subscription_tier = tier
        
        # Set subscription end date
        if subscription.get('current_period_end'):
            user.subscription_end_date = datetime.fromtimestamp(
                subscription['current_period_end'],
                tz=timezone.utc
            )
        
        db.session.commit()


def handle_subscription_updated(subscription):
    """Handle subscription updated"""
    subscription_id = subscription['id']
    user = User.query.filter_by(stripe_subscription_id=subscription_id).first()
    
    if user:
        status_map = {
            'active': SubscriptionStatus.ACTIVE,
            'past_due': SubscriptionStatus.PAST_DUE,
            'canceled': SubscriptionStatus.CANCELED,
            'unpaid': SubscriptionStatus.EXPIRED,
        }
        
        stripe_status = subscription.get('status')
        if stripe_status in status_map:
            user.subscription_status = status_map[stripe_status]
        
        # Update tier if changed
        tier = get_tier_from_subscription(subscription)
        user.subscription_tier = tier
        
        # Update end date
        if subscription.get('current_period_end'):
            user.subscription_end_date = datetime.fromtimestamp(
                subscription['current_period_end'],
                tz=timezone.utc
            )
        
        db.session.commit()


def handle_subscription_deleted(subscription):
    """Handle subscription deleted"""
    subscription_id = subscription['id']
    user = User.query.filter_by(stripe_subscription_id=subscription_id).first()
    
    if user:
        user.subscription_status = SubscriptionStatus.CANCELED
        user.subscription_tier = SubscriptionTier.FREE
        db.session.commit()


def handle_payment_succeeded(invoice):
    """Handle successful payment"""
    customer_id = invoice['customer']
    user = User.query.filter_by(stripe_customer_id=customer_id).first()
    
    if user and user.subscription_status == SubscriptionStatus.PAST_DUE:
        user.subscription_status = SubscriptionStatus.ACTIVE
        db.session.commit()


def handle_payment_failed(invoice):
    """Handle failed payment"""
    customer_id = invoice['customer']
    user = User.query.filter_by(stripe_customer_id=customer_id).first()
    
    if user:
        user.subscription_status = SubscriptionStatus.PAST_DUE
        db.session.commit()


def get_tier_from_subscription(subscription):
    """Map Stripe subscription to tier"""
    # Get the price ID from the subscription
    items = subscription.get('items', {}).get('data', [])
    if not items:
        return SubscriptionTier.FREE
    
    price_id = items[0].get('price', {}).get('id', '')
    
    # Map price IDs to tiers (configure these in your settings)
    tier_map = {
        'price_basic_monthly': SubscriptionTier.BASIC,
        'price_basic_yearly': SubscriptionTier.BASIC,
        'price_professional_monthly': SubscriptionTier.PROFESSIONAL,
        'price_professional_yearly': SubscriptionTier.PROFESSIONAL,
        'price_enterprise_monthly': SubscriptionTier.ENTERPRISE,
        'price_enterprise_yearly': SubscriptionTier.ENTERPRISE,
    }
    
    return tier_map.get(price_id, SubscriptionTier.FREE)


def log_subscription_event(event):
    """Log subscription event to database"""
    event_data = event['data']['object']
    customer_id = event_data.get('customer')
    
    user = User.query.filter_by(stripe_customer_id=customer_id).first()
    
    if user:
        subscription_event = SubscriptionEvent(
            user_id=user.id,
            stripe_event_id=event['id'],
            stripe_subscription_id=event_data.get('id'),
            event_type=event['type'],
            event_data=event_data
        )
        db.session.add(subscription_event)
        db.session.commit()
