"""Stripe helper functions.

Stripe checkout creation lives in your UI layer. Webhook processing is in app/stripe_webhook.py.
"""
from __future__ import annotations

from typing import Any, Dict

def create_customer(email: str) -> Dict[str, Any]:
    raise NotImplementedError("Implement Stripe customer creation.")
