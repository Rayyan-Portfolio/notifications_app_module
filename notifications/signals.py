from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver
from django.utils import timezone
from django.db import transaction

from .models import ScheduledNotification
from .services import compute_idempotency_key, enqueue_for_delivery

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
            effective_send_at=instance.effective_send_at,   # UTC or None
            context=instance.context,
            attach_ics=instance.attach_ics,
        )

    # 2) Set initial state on create
    if instance.pk is None:  # creating (not updating)
        if instance.effective_send_at and instance.effective_send_at > timezone.now():
            instance.state = ScheduledNotification.Status.SCHEDULED
        else:
            instance.state = ScheduledNotification.Status.PENDING

@receiver(post_save, sender=ScheduledNotification)
def scheduled_notification_post_save(sender, instance: ScheduledNotification, created: bool, **kwargs):
    if not created or instance.canceled:
        return
    # Delegate enqueue logic to services (single source of truth)
    transaction.on_commit(lambda: enqueue_for_delivery(instance))
