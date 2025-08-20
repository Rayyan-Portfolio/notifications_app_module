# # notifications/tests.py
# from datetime import date, time, datetime, timedelta, timezone as dt_timezone
# from zoneinfo import ZoneInfo
# from unittest.mock import patch

# from django.test import TestCase
# from django.db import IntegrityError
# from django.utils import timezone

# from notifications.models import ScheduledNotification, NotificationTemplate
# from notifications.services import compute_schedule


# class SchedulingPersistenceTests(TestCase):
#     def setUp(self):
#         self.template = NotificationTemplate.objects.create(
#             key="welcome_email",
#             subject="Welcome {{name}}",
#             body="Hello {{name}}!",
#         )
#         self.tzname = "Asia/Karachi"
#         self.tz = ZoneInfo(self.tzname)

#     def _create_row(self, *, sd, st, now_utc=None):
#         """
#         Helper: resolve schedule, set intent fields consistently with mode,
#         save the row, and return the instance + the resolved tuple.
#         """
#         mode, send_at_utc, tzname = compute_schedule(
#             scheduled_date=sd, scheduled_time=st, user_timezone=self.tzname, now_utc=now_utc
#         )
#         sn = ScheduledNotification(
#             template=self.template,
#             to_email="user@example.com",
#             user_timezone=tzname,
#             scheduling_mode=mode,
#             effective_send_at=send_at_utc,
#             # intent fields must match mode for DB constraints:
#             scheduled_date=sd if mode in ("ALL_DAY_DATE", "EXACT_DATETIME") else None,
#             scheduled_time=st if mode in ("TODAY_AT_TIME", "EXACT_DATETIME") else None,
#         )
#         sn.save()
#         return sn, (mode, send_at_utc, tzname)

#     @patch("notifications.services.enqueue_for_delivery")  # avoid broker calls
#     def test_persist_all_modes_minimal(self, _enqueue):
#         """
#         Saves one row for each mode and asserts persisted fields/state.
#         Uses a fixed 'now' for determinism on time-only cases.
#         """
#         fixed_now = datetime(2025, 8, 20, 8, 0, tzinfo=dt_timezone.utc)  # 13:00 PKT

#         # 1) IMMEDIATE (no date/time)
#         sn1, (m1, at1, tz1) = self._create_row(sd=None, st=None, now_utc=fixed_now)
#         self.assertEqual(m1, "IMMEDIATE")
#         self.assertEqual(sn1.state, ScheduledNotification.Status.PENDING)
#         self.assertIsNone(sn1.scheduled_date)
#         self.assertIsNone(sn1.scheduled_time)
#         self.assertEqual(sn1.effective_send_at, fixed_now)
#         self.assertTrue(sn1.idempotency_key)

#         # 2) ALL_DAY_DATE (date only; pick a future date)
#         future_date = (fixed_now.astimezone(self.tz).date() + timedelta(days=2))
#         sn2, (m2, at2, tz2) = self._create_row(sd=future_date, st=None, now_utc=fixed_now)
#         self.assertEqual(m2, "ALL_DAY_DATE")
#         self.assertEqual(sn2.state, ScheduledNotification.Status.SCHEDULED)
#         self.assertEqual(sn2.scheduled_date, future_date)
#         self.assertIsNone(sn2.scheduled_time)
#         self.assertEqual(sn2.effective_send_at, at2)

#         # 3) TODAY_AT_TIME (time only; choose future local time)
#         sn3, (m3, at3, tz3) = self._create_row(sd=None, st=time(23, 45), now_utc=fixed_now)
#         self.assertEqual(m3, "TODAY_AT_TIME")
#         self.assertEqual(sn3.state, ScheduledNotification.Status.SCHEDULED)
#         self.assertIsNone(sn3.scheduled_date)
#         self.assertEqual(sn3.scheduled_time, time(23, 45))
#         self.assertEqual(sn3.effective_send_at, at3)

#         # 4) EXACT_DATETIME (both; choose future local datetime)
#         local_future = fixed_now.astimezone(self.tz) + timedelta(days=1, hours=5)
#         sn4, (m4, at4, tz4) = self._create_row(
#             sd=local_future.date(), st=local_future.timetz().replace(tzinfo=None), now_utc=fixed_now
#         )
#         self.assertEqual(m4, "EXACT_DATETIME")
#         self.assertEqual(sn4.state, ScheduledNotification.Status.SCHEDULED)
#         self.assertEqual(sn4.scheduled_date, local_future.date())
#         self.assertEqual(sn4.scheduled_time, local_future.timetz().replace(tzinfo=None))
#         self.assertEqual(sn4.effective_send_at, at4)

#         # Sanity: 4 rows persisted
#         self.assertEqual(ScheduledNotification.objects.count(), 4)

#     @patch("notifications.services.enqueue_for_delivery")
#     def test_exact_past_becomes_pending(self, _enqueue):
#         """
#         If the resolved instant is in the past, state should be PENDING.
#         """
#         # Yesterday 10:00 local -> definitely in the past
#         yesterday_local = timezone.now().astimezone(self.tz).date() - timedelta(days=1)
#         sn, (_, at, _) = self._create_row(sd=yesterday_local, st=time(10, 0))
#         self.assertEqual(sn.state, ScheduledNotification.Status.PENDING)
#         self.assertEqual(sn.effective_send_at, at)

#     @patch("notifications.services.enqueue_for_delivery")
#     def test_idempotency_uniqueness_active_rows(self, _enqueue):
#         """
#         Creating the *same* request twice should fail the partial unique constraint.
#         """
#         fixed_now = datetime(2025, 8, 20, 8, 0, tzinfo=dt_timezone.utc)
#         sn1, (m1, at1, tz1) = self._create_row(sd=None, st=None, now_utc=fixed_now)
#         with self.assertRaises(IntegrityError):
#             # identical intent → identical idempotency key → IntegrityError
#             self._create_row(sd=None, st=None, now_utc=fixed_now)

#     @patch("notifications.services.enqueue_for_delivery")
#     def test_constraints_enforced_for_time_only(self, _enqueue):
#         """
#         If scheduling_mode is TODAY_AT_TIME but scheduled_time is NULL,
#         the DB check constraint should fail. (Simulate a buggy save.)
#         """
#         mode, send_at, tzname = compute_schedule(
#             scheduled_date=None, scheduled_time=time(14, 30), user_timezone=self.tzname
#         )
#         sn = ScheduledNotification(
#             template=self.template,
#             to_email="bad@example.com",
#             user_timezone=tzname,
#             scheduling_mode="TODAY_AT_TIME",  # force this
#             effective_send_at=send_at,
#             scheduled_date=None,
#             scheduled_time=None,  # BUG: violates constraint
#         )
#         with self.assertRaises(IntegrityError):
#             sn.save()
