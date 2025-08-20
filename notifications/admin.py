from django.contrib import admin, messages
from .models import NotificationTemplate, ScheduledNotification, NotificationLog
from .services import cancel_notification, compute_schedule

@admin.register(NotificationTemplate)
class NotificationTemplateAdmin(admin.ModelAdmin):
    list_display = ("subject", "key", "updated_at")
    search_fields = ("subject", "key")
    ordering = ("subject",)

@admin.register(ScheduledNotification)
class ScheduledNotificationAdmin(admin.ModelAdmin):
    list_display = ("template", "to_email", "state", "scheduling_mode", "effective_send_at", "attempts", "attach_ics")
    list_filter = ("state", "scheduling_mode", "attach_ics")
    search_fields = ("to_email", "provider_message_id")
    ordering = ("-effective_send_at",)
    readonly_fields = ("state","attempts", "last_error", "provider_message_id", "created_at", "updated_at")
    actions = ["cancel_selected"]

    def cancel_selected(self, request, queryset):
        count = 0
        for notif in queryset:
            if cancel_notification(notif):
                count += 1
        self.message_user(request, f"{count} notifications successfully canceled.", level=messages.SUCCESS)
    cancel_selected.short_description = "Cancel selected notifications"
        
    def save_model(self, request, obj, form, change):
        mode, send_at_utc, resolved_tz = compute_schedule(
            scheduled_date=obj.scheduled_date,
            scheduled_time=obj.scheduled_time,
            user_timezone=obj.user_timezone,
        )
        obj.scheduling_mode = mode
        obj.effective_send_at = send_at_utc
        obj.user_timezone = resolved_tz
        super().save_model(request, obj, form, change)

  
# @admin.register(ScheduledNotification)    
# class ScheduledNotificationAdmin(admin.ModelAdmin):
#     list_display = ("template", "to_email", "state", "scheduled_at", "attempts", "attach_ics")
#     list_filter = ("state", "attach_ics")
#     search_fields = ("to_email", "provider_message_id")
#     ordering = ("-scheduled_at",)
#     readonly_fields = ("state","state","attempts", "last_error", "provider_message_id", "created_at", "updated_at")
#     actions = ["cancel_selected"]

#     def cancel_selected(self, request, queryset):
#         count = 0
#         for notif in queryset:
#             if cancel_notification(notif):   # <- calls your service function
#                 count += 1
#         self.message_user(
#             request, f"{count} notifications successfully canceled.",
#             level=messages.SUCCESS
#         )
#     cancel_selected.short_description = "Cancel selected notifications"
    
#     def save_model(self, request, obj, form, change):
#         mode, send_at_utc, resolved_tz = compute_schedule(
#             scheduled_date=obj.scheduled_date,
#             scheduled_time=obj.scheduled_time,
#             user_timezone=obj.user_timezone,
#         )
#         obj.scheduling_mode = mode
#         obj.effective_send_at = send_at_utc
#         obj.user_timezone = resolved_tz
#         super().save_model(request, obj, form, change)
    
@admin.register(NotificationLog)
class NotificationLogAdmin(admin.ModelAdmin):
    list_display = ("notification", "attempt_no", "status", "to_email", "subject_snapshot", "started_at", "finished_at")
    list_filter = ("status",)
    search_fields = ("to_email", "provider_message_id", "subject_snapshot")
    ordering = ("-started_at",)   