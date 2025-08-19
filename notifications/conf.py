from django.conf import settings

DEFAULT_SEND_TIME = getattr(settings, "NOTIFY_DEFAULT_SEND_TIME", "09:00")
ALLOW_DATE_ONLY = getattr(settings, "NOTIFY_ALLOW_DATE_ONLY", True)
ICS_DEFAULT_DURATION_MIN = int(getattr(settings, "NOTIFY_ICS_DEFAULT_DURATION_MIN", 30))
