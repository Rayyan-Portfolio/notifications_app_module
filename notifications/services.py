import hashlib
import json
from typing import Any, Dict, Optional, Tuple
from datetime import datetime, date, time, timedelta, timezone as dt_timezone
from django.utils import timezone
from django.conf import settings
from zoneinfo import ZoneInfo


from .tasks import send_notification

# Match your model's choice values
MODE_IMMEDIATE = "IMMEDIATE"
MODE_ALL_DAY_DATE = "ALL_DAY_DATE"
MODE_TODAY_AT_TIME = "TODAY_AT_TIME"
MODE_EXACT_DATETIME = "EXACT_DATETIME"


def compute_idempotency_key(
    *,
    template_key: str,
    to_email: str,
    effective_send_at: Optional[datetime],
    context: Optional[Dict[str, Any]] = None,
    attach_ics: bool = False,
    scheduling_mode: Optional[str] = None,
    user_timezone: Optional[str] = None,
) -> str:
    """
    Build a stable fingerprint for a scheduled email request (Approach B).
    Includes canonical instant + mode + tz, so different input shapes don't collide.
    """
    email_norm = (to_email or "").strip().lower()
    when_norm = effective_send_at.isoformat() if effective_send_at else "immediate"
    payload = json.dumps(context or {}, sort_keys=True, separators=(",", ":"))
    ics_flag = "ics" if attach_ics else "no-ics"
    mode = scheduling_mode or ""
    tzname = (user_timezone or "").strip()

    raw = f"{template_key}|{email_norm}|{when_norm}|{mode}|{tzname}|{payload}|{ics_flag}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
# # effective_send_at
# def compute_idempotency_key(
#     template_key: str,
#     to_email: str,
#     scheduled_at: Optional[datetime],
#     context: Optional[Dict[str, Any]] = None,
#     attach_ics: bool = False,
# ) -> str:
#     """
#     Build a stable fingerprint for a scheduled email request.
#     Users never set this; we compute it server-side.

#     What goes into the fingerprint:
#       - template_key: stable identifier of the template
#       - to_email: normalized to lowercase
#       - scheduled_at: ISO string in UTC or the literal "immediate" if None
#       - context: JSON with sorted keys (order-independent)
#       - attach_ics: whether an .ics will be attached (changes the final email)

#     Returns:
#       64-character hex string (SHA-256)
#     """
#     # Normalize
#     email_norm = (to_email or "").strip().lower()
#     when_norm = scheduled_at.isoformat() if scheduled_at else "immediate"
#     payload = json.dumps(context or {}, sort_keys=True, separators=(",", ":"))
#     ics_flag = "ics" if attach_ics else "no-ics"

#     # Join a simple, readable string then hash it
#     raw = f"{template_key}|{email_norm}|{when_norm}|{payload}|{ics_flag}"
#     return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def enqueue_for_delivery(notification):
    if notification.pk is None:
        raise ValueError("Notification must be saved before enqueuing.")
    if getattr(notification, "canceled", False):
        return None

    now = timezone.now()
    eta = notification.effective_send_at
    if eta and eta > now:
        return send_notification.apply_async(args=[notification.id], eta=eta)
    else:
        return send_notification.delay(notification.id)

# def enqueue_for_delivery(notification):
#     """
#     Put the notification into Celery based on when it should send.

#     - If 'scheduled_at' is in the future -> enqueue with ETA (delayed).
#     - Otherwise -> enqueue now (asynchronously).
#     - If it's canceled, do nothing.

#     Returns:
#         Celery AsyncResult (or None if nothing was enqueued).
#     """
#     # Safety: only enqueue saved rows
#     if notification.pk is None:
#         raise ValueError("Notification must be saved before enqueuing.")

#     # Respect cancel flag
#     if getattr(notification, "canceled", False):
#         return None

#     now = timezone.now()
#     if notification.effective_send_at and notification.effective_send_at > now:
#         return send_notification.apply_async(args=[notification.id], eta=notification.effective_send_at)
#     else:
#         return send_notification.delay(notification.id)
    
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

def pick_timezone(user_tz: Optional[str]) -> Tuple[ZoneInfo, str]:
    """
    Pick an IANA timezone to use.
    Priority:
      1) user-provided tz
      2) settings.USER_DEFAULT_TIMEZONE
      3) settings.TIME_ZONE
      4) 'UTC'

    Returns (tzinfo_object, tzname_string).
    """
    candidate = (
        user_tz
        or getattr(settings, "USER_DEFAULT_TIMEZONE", None)
        or getattr(settings, "TIME_ZONE", None)
        or "UTC"
    )
    try:
        return ZoneInfo(candidate), candidate
    except Exception:
        # Fallback to UTC if the name is invalid
        return ZoneInfo("UTC"), "UTC"


def to_local(dt_date: date, dt_time: time, tz: ZoneInfo) -> datetime:
    """
    Build a timezone-aware *local* datetime from a date and time.
    """
    return datetime(
        dt_date.year, dt_date.month, dt_date.day,
        dt_time.hour, dt_time.minute, dt_time.second, dt_time.microsecond,
        tzinfo=tz,
    )


def compute_schedule(
    *,
    scheduled_date: Optional[date],
    scheduled_time: Optional[time],
    user_timezone: Optional[str],
    all_day_hour: int = 9,
    all_day_minute: int = 0,
    now_utc: Optional[datetime] = None,
) -> Tuple[str, datetime, str]:
    """
    Turn user inputs into:
      (scheduling_mode, effective_send_at_utc, resolved_tzname)

    Rules:
      - No date & no time       -> IMMEDIATE (right now)
      - Date only               -> ALL_DAY_DATE (that date at all_day_hour:all_day_minute, local)
      - Time only               -> TODAY_AT_TIME (today at time if in future, else tomorrow)
      - Date + time             -> EXACT_DATETIME (exact local instant)

    We *always* return a UTC datetime for effective_send_at.
    """
    tz, tzname = pick_timezone(user_timezone)
    now_utc = now_utc or timezone.now()
    now_local = now_utc.astimezone(tz)

    # 1) Neither date nor time: send now
    if scheduled_date is None and scheduled_time is None:
        return MODE_IMMEDIATE, now_utc, tzname

    # 2) Date only: pick default hour/minute in local time
    if scheduled_date is not None and scheduled_time is None:
        local_dt = to_local(
            scheduled_date,
            time(hour=all_day_hour, minute=all_day_minute),
            tz,
        )
        return MODE_ALL_DAY_DATE, local_dt.astimezone(dt_timezone.utc), tzname

    # 3) Time only: today if still in future, else tomorrow
    if scheduled_date is None and scheduled_time is not None:
        local_dt = to_local(now_local.date(), scheduled_time, tz)
        if local_dt <= now_local:
            local_dt = local_dt + timedelta(days=1)
        return MODE_TODAY_AT_TIME, local_dt.astimezone(dt_timezone.utc), tzname

    # 4) Both date and time: exact instant
    local_dt = to_local(scheduled_date, scheduled_time, tz)
    return MODE_EXACT_DATETIME, local_dt.astimezone(dt_timezone.utc), tzname