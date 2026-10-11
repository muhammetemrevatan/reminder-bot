import asyncio
import os
import threading
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo

from caldav.lib.error import NotFoundError
from icalendar import Alarm, Calendar, Event
from telegram.ext import Application

from reminders import ReminderScheduler, reminder_from_event

TZ = ZoneInfo("Europe/Istanbul")


def make_event(uid="test-event", when=None, title="Toplantı", alarm=True):
    component = Event()
    component.add("uid", uid)
    component.add("summary", title)
    component.add("dtstart", when or datetime.now(TZ) + timedelta(hours=1))
    if alarm:
        reminder_alarm = Alarm()
        reminder_alarm.add("action", "DISPLAY")
        reminder_alarm.add("description", title)
        reminder_alarm.add("trigger", timedelta(0))
        component.add_component(reminder_alarm)
    return SimpleNamespace(icalendar_component=component)


class FakeQueue:
    def __init__(self):
        self.created = []
        self.repeating = []

    def run_once(self, callback, when, **kwargs):
        job = SimpleNamespace(
            callback=callback, when=when, schedule_removal=Mock(), **kwargs
        )
        self.created.append(job)
        return job

    def run_repeating(self, callback, **kwargs):
        self.repeating.append((callback, kwargs))


class SchedulerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.calendar = Mock()
        self.event = make_event()
        self.calendar.events.return_value = [self.event]
        self.calendar.event_by_uid.return_value = self.event
        self.queue = FakeQueue()
        self.app = SimpleNamespace(job_queue=self.queue)
        self.scheduler = ReminderScheduler(lambda: self.calendar, TZ, 123)
        self.bot = SimpleNamespace(send_message=AsyncMock())

    def context(self, job):
        return SimpleNamespace(
            job=job, job_queue=self.queue, application=self.app, bot=self.bot
        )

    async def test_restart_restores_existing_reminder(self):
        await self.scheduler.initialize(self.app)
        restarted = ReminderScheduler(lambda: self.calendar, TZ, 123)
        new_queue = FakeQueue()
        await restarted.initialize(SimpleNamespace(job_queue=new_queue))
        self.assertEqual(len(new_queue.created), 1)
        self.assertEqual(new_queue.created[0].data["reminder"].uid, "test-event")
        self.assertEqual(new_queue.created[0].when, self.event.icalendar_component["DTSTART"].dt)
        self.assertEqual(new_queue.repeating[0][1]["interval"], 60)

    async def test_sync_does_not_duplicate_jobs(self):
        await self.scheduler.sync(self.app)
        await self.scheduler.sync(self.app)
        self.assertEqual(len(self.queue.created), 1)

    async def test_deleted_event_cancels_pending_job(self):
        await self.scheduler.sync(self.app)
        job = self.queue.created[0]
        self.calendar.events.return_value = []
        await self.scheduler.sync(self.app)
        job.schedule_removal.assert_called_once()
        self.assertEqual(self.scheduler.jobs, {})
        await self.scheduler.send(self.context(job))
        self.bot.send_message.assert_not_awaited()

    async def test_deletion_immediately_before_delivery_sends_nothing(self):
        await self.scheduler.sync(self.app)
        self.calendar.event_by_uid.side_effect = NotFoundError()
        await self.scheduler.send(self.context(self.queue.created[0]))
        self.bot.send_message.assert_not_awaited()
        self.assertEqual(self.scheduler.jobs, {})

    async def test_local_cancellation_blocks_already_dispatched_callback(self):
        await self.scheduler.sync(self.app)
        job = self.queue.created[0]
        self.scheduler.cancel("test-event")
        await self.scheduler.send(self.context(job))
        self.bot.send_message.assert_not_awaited()
        self.calendar.event_by_uid.assert_not_called()

    async def test_rescheduled_event_does_not_send_at_old_time(self):
        await self.scheduler.sync(self.app)
        original = self.queue.created[0]
        changed = make_event(when=datetime.now(TZ) + timedelta(hours=2))
        self.calendar.event_by_uid.return_value = changed
        await self.scheduler.send(self.context(original))
        self.bot.send_message.assert_not_awaited()
        self.calendar.events.return_value = [changed]
        await self.scheduler.sync(self.app)
        self.assertEqual(len(self.queue.created), 2)

    async def test_successful_delivery_uses_current_title(self):
        await self.scheduler.sync(self.app)
        self.event.icalendar_component["SUMMARY"] = "Yeni başlık"
        await self.scheduler.send(self.context(self.queue.created[0]))
        self.bot.send_message.assert_awaited_once_with(
            chat_id=123, text="⏰ Hatırlatma: Yeni başlık"
        )
        self.assertEqual(self.scheduler.jobs, {})

    async def test_calendar_outage_preserves_jobs(self):
        await self.scheduler.sync(self.app)
        existing = self.scheduler.jobs.copy()
        self.calendar.events.side_effect = OSError("unreachable")
        with self.assertLogs("reminders", level="ERROR"):
            await self.scheduler.sync(self.app)
        self.assertEqual(self.scheduler.jobs, existing)

    async def test_calendar_outage_retries_without_sending_unverified_reminder(self):
        await self.scheduler.sync(self.app)
        self.calendar.event_by_uid.side_effect = OSError("unreachable")
        with self.assertLogs("reminders", level="ERROR"):
            for _ in range(4):
                await self.scheduler.send(self.context(self.queue.created[-1]))
        self.bot.send_message.assert_not_awaited()
        self.assertEqual(len(self.queue.created), 4)
        self.assertEqual(self.scheduler.jobs, {})

    async def test_past_all_day_unalarmed_and_cancelled_events_are_not_restored(self):
        cancelled = make_event(uid="cancelled")
        cancelled.icalendar_component.add("status", "CANCELLED")
        self.calendar.events.return_value = [
            make_event(uid="past", when=datetime.now(TZ) - timedelta(hours=1)),
            make_event(uid="all-day", when=datetime.now(TZ).date()),
            make_event(uid="no-alarm", alarm=False),
            cancelled,
        ]
        await self.scheduler.sync(self.app)
        self.assertEqual(self.queue.created, [])

    async def test_legacy_floating_time_is_interpreted_as_istanbul(self):
        floating = datetime.now().replace(tzinfo=None) + timedelta(days=1)
        event = make_event(when=floating)
        reminder = reminder_from_event(event, TZ)
        self.assertEqual(reminder.when, floating.replace(tzinfo=TZ))

    async def test_calendar_read_does_not_block_event_loop(self):
        started = threading.Event()
        release = threading.Event()

        def slow_read():
            started.set()
            release.wait(timeout=5)
            return []

        self.calendar.events.side_effect = slow_read
        task = asyncio.create_task(self.scheduler.sync(self.app))
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 2))
            await asyncio.wait_for(asyncio.sleep(0), timeout=1)
            self.assertFalse(task.done())
        finally:
            release.set()
            await task

    async def test_real_telegram_job_queue_accepts_restored_jobs(self):
        app = Application.builder().token("123:TEST").build()
        await self.scheduler.initialize(app)
        jobs = app.job_queue.jobs()
        self.assertEqual(len(jobs), 2)
        self.assertEqual(len(app.job_queue.get_jobs_by_name("reminder:test-event")), 1)
        for job in jobs:
            job.schedule_removal()


class CalendarWriteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.dict(os.environ, {
            "TELEGRAM_CHAT_ID": "123", "TELEGRAM_USER_ID": "123", "OPENROUTER_API_KEY": "test",
            "GEMINI_API_KEY": "test", "TELEGRAM_TOKEN": "123:TEST",
        }):
            import main
            cls.main = main

    def test_saved_timed_event_round_trips_title_uid_and_timezone(self):
        calendar = Mock()
        start = datetime(2030, 1, 1, 15, tzinfo=TZ)
        title = "Toplantı, not; bir\\iki\nYeni satır"
        with patch.object(self.main, "get_calendar", return_value=calendar):
            uid = self.main.save_calendar_event(title, "Not\nDetay", start, False)
        content = Calendar.from_ical(calendar.save_event.call_args.args[0])
        event = content.walk("VEVENT")[0]
        self.assertEqual(str(event["UID"]), uid)
        self.assertEqual(str(event["SUMMARY"]), title)
        self.assertEqual(event["DTSTART"].dt, start.astimezone(timezone.utc))
        restored = reminder_from_event(SimpleNamespace(icalendar_component=event), TZ)
        self.assertEqual(restored.when, start)
        self.assertEqual(len(content.walk("VEVENT")), 1)

    def test_all_day_event_has_no_reminder(self):
        calendar = Mock()
        start = datetime(2030, 1, 1, tzinfo=TZ)
        with patch.object(self.main, "get_calendar", return_value=calendar):
            self.main.save_calendar_event("Fuar", "", start, True)
        event = Calendar.from_ical(calendar.save_event.call_args.args[0]).walk("VEVENT")[0]
        self.assertEqual(event["DTSTART"].dt, start.date())
        self.assertEqual(event["DTEND"].dt, (start + timedelta(days=1)).date())
        self.assertIsNone(reminder_from_event(SimpleNamespace(icalendar_component=event), TZ))


class HandlerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        with patch.dict(os.environ, {
            "TELEGRAM_CHAT_ID": "123", "TELEGRAM_USER_ID": "123", "OPENROUTER_API_KEY": "test",
            "GEMINI_API_KEY": "test", "TELEGRAM_TOKEN": "123:TEST",
        }):
            import main
            self.main = main
        self.calendar = Mock()
        self.scheduler = ReminderScheduler(lambda: self.calendar, TZ, 123)
        self.queue = FakeQueue()
        self.context = SimpleNamespace(job_queue=self.queue)
        self.reply = AsyncMock()
        self.update = SimpleNamespace(
            effective_chat=SimpleNamespace(id=123),
            effective_user=SimpleNamespace(id=123),
            message=SimpleNamespace(reply_text=self.reply),
        )

    async def test_handler_saves_and_schedules_same_event_uid(self):
        with (
            patch.object(self.main, "reminder_scheduler", self.scheduler),
            patch.object(self.main, "get_calendar", return_value=self.calendar),
            patch.object(self.main, "parse_with_openrouter", return_value={
                "action": "add_event", "title": "Test", "description": "",
                "datetime": "2030-01-01 15:00", "all_day": False,
            }),
        ):
            await self.main.handle_user_text(self.update, self.context, "Test")
        event = Calendar.from_ical(self.calendar.save_event.call_args.args[0]).walk("VEVENT")[0]
        self.assertEqual(self.queue.created[0].data["reminder"].uid, str(event["UID"]))
        self.reply.assert_awaited_once()

    async def test_delete_handler_cancels_matching_uid(self):
        event = make_event(title="Silinecek")
        event.delete = Mock()
        self.calendar.events.return_value = [event]
        self.calendar.search.return_value = [event]
        await self.scheduler.sync(SimpleNamespace(job_queue=self.queue))
        job = self.queue.created[0]
        with (
            patch.object(self.main, "reminder_scheduler", self.scheduler),
            patch.object(self.main, "get_calendar", return_value=self.calendar),
            patch.object(self.main, "parse_with_openrouter", return_value={
                "action": "delete_event", "title": "Silinecek",
            }),
        ):
            await self.main.handle_user_text(self.update, self.context, "Sil")
        event.delete.assert_called_once()
        job.schedule_removal.assert_called_once()
        self.assertEqual(self.scheduler.jobs, {})
        self.reply.assert_awaited_once_with("🗑️ Silindi:\n• Silinecek")

    async def test_other_user_in_allowed_chat_is_ignored_by_all_handlers(self):
        self.update.effective_user.id = 456
        with patch.object(self.main, "parse_with_openrouter") as parse:
            await self.main.start(self.update, self.context)
            await self.main.handle_user_text(self.update, self.context, "Sil")
            await self.main.handle_voice(self.update, self.context)
        parse.assert_not_called()
        self.reply.assert_not_awaited()

    async def test_allowed_user_in_other_chat_is_ignored_by_all_handlers(self):
        self.update.effective_chat.id = 456
        with patch.object(self.main, "parse_with_openrouter") as parse:
            await self.main.start(self.update, self.context)
            await self.main.handle_user_text(self.update, self.context, "Sil")
            await self.main.handle_voice(self.update, self.context)
        parse.assert_not_called()
        self.reply.assert_not_awaited()

    async def test_updates_without_sender_or_chat_are_not_authorized(self):
        self.update.effective_user = None
        self.assertFalse(self.main.is_authorized(self.update))
        self.update.effective_user = SimpleNamespace(id=123)
        self.update.effective_chat = None
        self.assertFalse(self.main.is_authorized(self.update))

    async def test_owner_can_use_start(self):
        await self.main.start(self.update, self.context)
        self.reply.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
