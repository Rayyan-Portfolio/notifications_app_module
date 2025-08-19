from django.db.models.signals import pre_save
from django.dispatch import receiver
from django.utils import timezone

from .models import ScheduledNotification
from .services import compute_idempotency_key
from .tasks import send_notification

@receiver(pre_save, sender=ScheduledNotification)
def scheduled_notification_pre_save(sender, instance: ScheduledNotification, **kwargs):
    """
    Runs just before a ScheduledNotification is saved.
    - Fills idempotency_key if it's empty.
    - Sets initial state on create (PENDING or SCHEDULED).
    """

    # 1) Fill idempotency_key (only if blank and we have enough info)
    if not instance.idempotency_key and instance.template_id and instance.to_email:
        instance.idempotency_key = compute_idempotency_key(
            template_key=instance.template.key,
            to_email=instance.to_email,
            scheduled_at=instance.scheduled_at,   # UTC or None
            context=instance.context,
            attach_ics=instance.attach_ics,
        )

    # 2) Set initial state on create
    if instance.pk is None:  # creating (not updating)
        if instance.scheduled_at and instance.scheduled_at > timezone.now():
            instance.state = ScheduledNotification.Status.SCHEDULED
        else:
            instance.state = ScheduledNotification.Status.PENDING


def scheduled_notification_post_save(sender, instance: ScheduledNotification, created: bool, **kwargs):
    """
    After a ScheduledNotification is created:
    - If it's canceled, do nothing.
    - If it has a future scheduled_at, enqueue Celery with ETA.
    - Otherwise, enqueue to send immediately.

    NOTE: Make sure your API view does NOT also enqueue,
    or you'll double-schedule. With this signal, creation is enough.
    """
    if not created:
        return

    if instance.canceled:
        return

    # Future time -> delay until then. Otherwise send now.
    if instance.scheduled_at and instance.scheduled_at > timezone.now():
        send_notification.apply_async(args=[instance.id], eta=instance.scheduled_at)
    else:
        send_notification.delay(instance.id)