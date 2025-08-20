from celery import shared_task
from celery.exceptions import MaxRetriesExceededError
from django.core.mail import EmailMessage
from django.template import Template, Context
from django.utils import timezone
from datetime import timedelta
from icalendar import Calendar, Event

from .models import ScheduledNotification, NotificationLog
from .conf import ICS_DEFAULT_DURATION_MIN


def _build_ics(summary: str, starts_at, duration_min: int, description: str = "", location: str = "") -> bytes:
    """Create a very small .ics file (bytes) for calendar attachment."""
    cal = Calendar()
    cal.add("prodid", "-//Notifications App//")
    cal.add("version", "2.0")

    event = Event()
    event.add("summary", summary)
    event.add("dtstart", starts_at)  # aware datetime (UTC is fine)
    event.add("dtend", starts_at + timedelta(minutes=duration_min))
    if description:
        event.add("description", description)
    if location:
        event.add("location", location)

    cal.add_component(event)
    return cal.to_ical()


@shared_task(bind=True)
def send_notification(self, notification_id: int):
    """
    Send a ScheduledNotification email.
    - Respects cancel flag.
    - Creates a NotificationLog row per attempt.
    - Attaches .ics if requested.
    - Retries on error (simple backoff).
    """
    # 1) Load fresh copy
    try:
        sn = ScheduledNotification.objects.select_related("template").get(pk=notification_id)
    except ScheduledNotification.DoesNotExist:
        return "missing"

    # 2) Safety checks
    if sn.canceled or sn.state not in [
        ScheduledNotification.Status.PENDING,
        ScheduledNotification.Status.SCHEDULED,
        ScheduledNotification.Status.RETRYING,
        ScheduledNotification.Status.QUEUED,
    ]:
        return f"skip:{sn.state}"

    # 3) Mark attempt start
    sn.attempts += 1
    sn.state = ScheduledNotification.Status.QUEUED
    sn.save(update_fields=["attempts", "state", "updated_at"])

    log = NotificationLog.objects.create(
        notification=sn,
        attempt_no=sn.attempts,
        status="STARTED",
        to_email=sn.to_email,
        subject_snapshot="",  # fill after rendering
    )

    # 4) Render subject + body
    ctx = Context(sn.context or {})
    subject = Template(sn.template.subject).render(ctx)
    body = Template(sn.template.body).render(ctx)

    # update log with subject we tried
    log.subject_snapshot = subject
    log.save(update_fields=["subject_snapshot"])

    # 5) Build email
    email = EmailMessage(
        subject=subject,
        body=body,
        from_email=None,         # uses DEFAULT_FROM_EMAIL from settings
        to=[sn.to_email],
    )

    # Optional .ics attachment
    if sn.attach_ics:
        start_dt = sn.effective_send_at or timezone.now()
        ics_bytes = _build_ics(
            summary=subject,
            starts_at=start_dt,
            duration_min=ICS_DEFAULT_DURATION_MIN,
            description=body,
            location=(sn.context or {}).get("location", ""),
        )
        email.attach("invite.ics", ics_bytes, "text/calendar")

    # 6) Send
    try:
        # Build MIME to read generated Message-ID for tracing
        mime = email.message()
        message_id = mime.get("Message-ID") or ""

        email.send(fail_silently=False)

        sn.state = ScheduledNotification.Status.SENT
        sn.last_error = ""
        if message_id:
            sn.provider_message_id = message_id
        sn.save(update_fields=["state", "last_error", "provider_message_id", "updated_at"])

        log.status = "SENT"
        log.provider_message_id = message_id
        log.finished_at = timezone.now()
        log.save(update_fields=["status", "provider_message_id", "finished_at"])

        return "sent"

    except Exception as e:
        # Mark retrying and record error
        sn.state = ScheduledNotification.Status.RETRYING
        sn.last_error = str(e)
        sn.save(update_fields=["state", "last_error", "updated_at"])

        log.status = "RETRYING"
        log.error_message = str(e)
        log.finished_at = timezone.now()
        log.save(update_fields=["status", "error_message", "finished_at"])

        # Ask Celery to retry (60s). If we exceed retry limit, mark as FAILED.
        try:
            raise self.retry(exc=e, countdown=60, max_retries=3)
        except MaxRetriesExceededError:
            sn.state = ScheduledNotification.Status.FAILED
            sn.save(update_fields=["state", "updated_at"])

            log.status = "FAILED"
            log.save(update_fields=["status"])

            return "failed"
