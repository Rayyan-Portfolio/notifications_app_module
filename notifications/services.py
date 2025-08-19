import hashlib
import json
from typing import Any, Dict, Optional
from datetime import datetime
from django.utils import timezone

from .tasks import send_notification

def compute_idempotency_key(
    template_key: str,
    to_email: str,
    scheduled_at: Optional[datetime],
    context: Optional[Dict[str, Any]] = None,
    attach_ics: bool = False,
) -> str:
    """
    Build a stable fingerprint for a scheduled email request.
    Users never set this; we compute it server-side.

    What goes into the fingerprint:
      - template_key: stable identifier of the template
      - to_email: normalized to lowercase
      - scheduled_at: ISO string in UTC or the literal "immediate" if None
      - context: JSON with sorted keys (order-independent)
      - attach_ics: whether an .ics will be attached (changes the final email)

    Returns:
      64-character hex string (SHA-256)
    """
    # Normalize
    email_norm = (to_email or "").strip().lower()
    when_norm = scheduled_at.isoformat() if scheduled_at else "immediate"
    payload = json.dumps(context or {}, sort_keys=True, separators=(",", ":"))
    ics_flag = "ics" if attach_ics else "no-ics"

    # Join a simple, readable string then hash it
    raw = f"{template_key}|{email_norm}|{when_norm}|{payload}|{ics_flag}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def enqueue_for_delivery(notification):
    """
    Put the notification into Celery based on when it should send.

    - If 'scheduled_at' is in the future -> enqueue with ETA (delayed).
    - Otherwise -> enqueue now (asynchronously).
    - If it's canceled, do nothing.

    Returns:
        Celery AsyncResult (or None if nothing was enqueued).
    """
    # Safety: only enqueue saved rows
    if notification.pk is None:
        raise ValueError("Notification must be saved before enqueuing.")

    # Respect cancel flag
    if getattr(notification, "canceled", False):
        return None

    now = timezone.now()
    if notification.scheduled_at and notification.scheduled_at > now:
        return send_notification.apply_async(args=[notification.id], eta=notification.scheduled_at)
    else:
        return send_notification.delay(notification.id)
    
    
def cancel_notification(notification):
    """
    Mark a scheduled notification as canceled so the worker won't send it.

    Returns:
        True if we changed the state, False if no change was needed.

    Notes:
      - We don't revoke Celery tasks in v1.
      - The Celery task re-reads the row and exits if `canceled=True`.
    """
    if notification.pk is None:
        raise ValueError("Notification must be saved before canceling.")

    # If it's already final, do nothing
    if notification.state in (
        notification.Status.SENT,
        notification.Status.CANCELED,
        notification.Status.FAILED,
    ):
        return False

    notification.canceled = True
    notification.state = notification.Status.CANCELED
    notification.save(update_fields=["canceled", "state", "updated_at"])
    return True

