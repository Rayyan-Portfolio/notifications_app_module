from django.contrib import admin
from .models import NotificationTemplate, ScheduledNotification, NotificationLog

@admin.register(NotificationTemplate)
class NotificationTemplateAdmin(admin.ModelAdmin):
    list_display = ("subject", "key", "updated_at")
    search_fields = ("subject", "key")
    ordering = ("subject",)
    
@admin.register(ScheduledNotification)    
class ScheduledNotificationAdmin(admin.ModelAdmin):
    list_display = ("template", "to_email", "state", "scheduled_at", "attempts", "attach_ics")
    list_filter = ("state", "attach_ics")
    search_fields = ("to_email", "provider_message_id")
    ordering = ("-scheduled_at",)
    readonly_fields = ("attempts", "last_error", "provider_message_id", "created_at", "updated_at")
    
@admin.register(NotificationLog)
class NotificationLogAdmin(admin.ModelAdmin):
    list_display = ("notification", "attempt_no", "status", "to_email", "subject_snapshot", "started_at", "finished_at")
    list_filter = ("status",)
    search_fields = ("to_email", "provider_message_id", "subject_snapshot")
    ordering = ("-started_at",)   