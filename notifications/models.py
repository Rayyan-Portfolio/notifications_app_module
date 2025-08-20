from django.db import models
from django.conf import settings

class NotificationTemplate(models.Model):

    key = models.SlugField(unique=True, db_index=True)
    # users on the frontend will pick by subject (so make it unique)
    subject = models.CharField(max_length=200, unique=True, db_index=True)
    body = models.TextField()

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["subject"]

    def __str__(self):
        return self.subject

# class ScheduledNotification(models.Model):
#     class Status(models.TextChoices):
#         PENDING = "PENDING", "Pending"       # created; send now (no datetime) or waiting for worker
#         SCHEDULED = "SCHEDULED", "Scheduled" # has a future datetime (ETA)
#         QUEUED = "QUEUED", "Queued"          # task picked up by worker
#         SENT = "SENT", "Sent"                # successfully emailed
#         FAILED = "FAILED", "Failed"          # gave up after retries
#         CANCELED = "CANCELED", "Canceled"    # user/admin canceled
#         RETRYING = "RETRYING", "Retrying"    # temporary failure; Celery will retry

#     # who to send to
#     to_email = models.EmailField()

#     # which template to use
#     template = models.ForeignKey("notifications.NotificationTemplate", on_delete=models.PROTECT)

#     # optional data for {{ placeholders }} in the template
#     context = models.JSONField(default=dict, blank=True)

#     # should we attach an .ics calendar file? (optional)
#     attach_ics = models.BooleanField(default=False)

#     # when to send (UTC). If left empty => send immediately.
#     scheduled_at = models.DateTimeField(null=True, blank=True)

#     # the timezone name the user picked in the UI (just for reference)
#     user_timezone = models.CharField(max_length=64, default="UTC")

#     # true if user gave a DATE only and we filled the default time (e.g., 09:00)
#     date_only = models.BooleanField(default=False)

#     # state machine + attempts info
#     state = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
#     attempts = models.PositiveIntegerField(default=0)
#     last_error = models.TextField(null=True, blank=True)

#     # used to avoid duplicate sends (we’ll compute this when creating)
#     idempotency_key = models.CharField(max_length=128, db_index=True, blank=True)

#     # allows a quick “cancel” before it’s sent
#     canceled = models.BooleanField(default=False)

#     # audit fields
#     created_by = models.ForeignKey(
#         settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
#     )
#     provider_message_id = models.CharField(max_length=128, null=True, blank=True)

#     created_at = models.DateTimeField(auto_now_add=True)
#     updated_at = models.DateTimeField(auto_now=True)

#     class Meta:
#         indexes = [
#             models.Index(fields=["state"]),
#             models.Index(fields=["scheduled_at"]),
#             models.Index(fields=["idempotency_key"]),
#         ]
#         ordering = ["-created_at"]

#     def __str__(self):
#         return f"{self.template.subject} -> {self.to_email} [{self.state}]"

class ScheduledNotification(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"        # created; ready to send now or awaiting worker
        SCHEDULED = "SCHEDULED", "Scheduled"  # future effective_send_at (ETA)
        RETRYING = "RETRYING", "Retrying"     # temporary failure; Celery will retry
        QUEUED = "QUEUED", "Queued"           # (optional) worker picked up
        SENT = "SENT", "Sent"                 # successfully emailed
        FAILED = "FAILED", "Failed"           # gave up after retries
        CANCELED = "CANCELED", "Canceled"     # user/admin canceled

    class SchedulingMode(models.TextChoices):
        IMMEDIATE = "IMMEDIATE", "Immediate"           # no date/time provided → send now
        ALL_DAY_DATE = "ALL_DAY_DATE", "All-day Date"  # date only → use configured hour (e.g., 09:00 local)
        TODAY_AT_TIME = "TODAY_AT_TIME", "Today at Time"  # time only → today if future else tomorrow
        EXACT_DATETIME = "EXACT_DATETIME", "Exact Date+Time"  # both provided

    # who to send to
    to_email = models.EmailField()

    # which template to use
    template = models.ForeignKey(
        "notifications.NotificationTemplate",
        on_delete=models.PROTECT,
        related_name="scheduled_notifications",
    )

    # optional data for {{ placeholders }} in the template
    context = models.JSONField(default=dict, blank=True)

    # should we attach an .ics calendar file? (optional)
    attach_ics = models.BooleanField(default=False)

    # USER INPUTS (intent)
    scheduled_date = models.DateField(null=True, blank=True)
    scheduled_time = models.TimeField(null=True, blank=True)
    user_timezone = models.CharField(
        max_length=64,
        default="UTC",
        help_text="IANA timezone used to resolve inputs (e.g., 'Asia/Karachi').",
    )
    scheduling_mode = models.CharField(
        max_length=20,
        choices=SchedulingMode.choices,
        help_text="How the schedule was expressed by the user.",
    )

    # CANONICAL RUNTIME INSTANT (what Celery/queries use) — UTC
    # Tip: make null=False after backfilling via a data migration.
    effective_send_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Resolved UTC instant when this should be sent (single source of truth).",
    )

    # state machine + attempts info
    state = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(null=True, blank=True)

    # used to avoid duplicate sends (compute on create)
    # keep nullable; enforce uniqueness only when not null
    idempotency_key = models.CharField(max_length=128, null=True, blank=True, db_index=True)

    # allows a quick “cancel” before it’s sent
    canceled = models.BooleanField(default=False)

    # provider + audit fields
    provider_message_id = models.CharField(max_length=128, null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["state", "effective_send_at"]),
            models.Index(fields=["effective_send_at"]),
            models.Index(fields=["state"]),
            models.Index(fields=["idempotency_key"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["idempotency_key"],
                name="uniq_nonnull_idempotency_key",
                condition=models.Q(idempotency_key__isnull=False),
            ),
            # IMMEDIATE -> no date/time
            models.CheckConstraint(
                name="sched_mode_immediate_no_inputs",
                check=(
                    ~models.Q(scheduling_mode="IMMEDIATE")
                    | (models.Q(scheduled_date__isnull=True) & models.Q(scheduled_time__isnull=True))
                ),
            ),
            # ALL_DAY_DATE -> date set, time null
            models.CheckConstraint(
                name="sched_mode_all_day_requires_date_only",
                check=(
                    ~models.Q(scheduling_mode="ALL_DAY_DATE")
                    | (models.Q(scheduled_date__isnull=False) & models.Q(scheduled_time__isnull=True))
                ),
            ),
            # TODAY_AT_TIME -> time set, date null
            models.CheckConstraint(
                name="sched_mode_today_at_time_requires_time_only",
                check=(
                    ~models.Q(scheduling_mode="TODAY_AT_TIME")
                    | (models.Q(scheduled_time__isnull=False) & models.Q(scheduled_date__isnull=True))
                ),
            ),
            # EXACT_DATETIME -> both set
            models.CheckConstraint(
                name="sched_mode_exact_requires_both",
                check=(
                    ~models.Q(scheduling_mode="EXACT_DATETIME")
                    | (models.Q(scheduled_date__isnull=False) & models.Q(scheduled_time__isnull=False))
                ),
            ),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.template.subject} -> {self.to_email} [{self.state}]"

class NotificationLog(models.Model):
    """
    One row = one attempt to send a ScheduledNotification.
    We create it when the task starts, and update it on success/failure.
    """
    # link back to the scheduled item
    notification = models.ForeignKey(
        "notifications.ScheduledNotification",
        on_delete=models.CASCADE,
        related_name="logs",
    )

    # attempt number (1, 2, 3, ...) copied from notification.attempts
    attempt_no = models.PositiveIntegerField(default=1)

    # simple status for this attempt
    STATUS_CHOICES = [
        ("STARTED", "Started"),
        ("SENT", "Sent"),
        ("FAILED", "Failed"),
        ("RETRYING", "Retrying"),
        ("CANCELED", "Canceled"),
    ]
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="Started")

    # quick snapshot info (helps debugging without opening the parent)
    to_email = models.EmailField()                 # denormalized for quick search
    subject_snapshot = models.CharField(max_length=200, blank=True)  # what we tried to send

    # provider identifiers / errors
    provider_message_id = models.CharField(max_length=128, blank=True)
    error_message = models.TextField(blank=True)

    # timing
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["started_at"]),
        ]

    def __str__(self):
        return f"Attempt {self.attempt_no} for #{self.notification_id} [{self.status}]"
