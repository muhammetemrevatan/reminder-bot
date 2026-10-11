import asyncio
import json
import os
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo

from caldav.lib.error import NotFoundError
from icalendar import Calendar, Event, vText

from actions import InvalidAction, validate_action
from calendar_ops import STATE_UID, apply_change, build_event, find_matches, serialize
from reminders import ReminderScheduler, expand_reminders
from test_reminders import FakeQueue

TZ = ZoneInfo("Europe/Istanbul")


class MemoryResource:
    def __init__(self, calendar, data):
        self.calendar = calendar
        self.data = data

    @property
    def icalendar_instance(self):
        return Calendar.from_ical(self.data)

    @property
    def icalendar_component(self):
        return self.icalendar_instance.walk("VEVENT")[0]

    def save(self):
        self.calendar.save_event(self.data)

    def delete(self):
        self.calendar.records.pop(str(self.icalendar_component["UID"]))


class MemoryCalendar:
    def __init__(self):
        self.records = {}

    def save_event(self, data):
        event = Calendar.from_ical(data).walk("VEVENT")[0]
        resource = MemoryResource(self, data)
        self.records[str(event["UID"])] = resource
        return resource

    def events(self):
        return list(self.records.values())

    def event_by_uid(self, uid):
        if uid not in self.records:
            raise NotFoundError()
        return self.records[uid]


class ValidationTests(unittest.TestCase):
    def test_string_boolean_is_rejected(self):
        with self.assertRaises(InvalidAction):
            validate_action({"action": "add_event", "title": "Test", "datetime": "2030-01-01 15:00", "all_day": "false"})

    def test_invalid_date_is_rejected(self):
        with self.assertRaises(InvalidAction):
            validate_action({"action": "add_event", "title": "Test", "datetime": "2030-02-30 15:00", "all_day": False})

    def test_missing_multi_day_time_is_rejected(self):
        with self.assertRaises(InvalidAction):
            validate_action({"action": "add_multi_event", "title": "Test", "dates": ["2030-01-01"], "all_day": False})

    def test_non_object_and_unsupported_action_are_rejected(self):
        for value in ([], None, {"action": "run_shell"}, {"action": []}):
            with self.subTest(value=value), self.assertRaises(InvalidAction):
                validate_action(value)

    def test_reminder_bounds_and_types(self):
        base = {"action": "add_event", "title": "Test", "datetime": "2030-01-01 15:00", "all_day": False}
        for minutes in ([True], [-1], [10081], "10", [0, 1, 2, 3, 4, 5]):
            with self.subTest(minutes=minutes), self.assertRaises(InvalidAction):
                validate_action({**base, "reminder_minutes": minutes})
        self.assertEqual(validate_action({**base, "reminder_minutes": [10, 0, 10]})["reminder_minutes"], [0, 10])

    def test_recurrence_is_validated(self):
        base = {"action": "add_event", "title": "Test", "datetime": "2030-01-01 15:00", "all_day": False}
        for rule in ({"freq": []}, {"freq": "SECONDLY"}, {"freq": "WEEKLY", "byday": ["XX"]}, {"freq": "DAILY", "interval": 0}, {"freq": "DAILY", "count": 0}, {"freq": "DAILY", "count": 2, "until": "2030-02-01"}):
            with self.subTest(rule=rule), self.assertRaises(InvalidAction):
                validate_action({**base, "recurrence": rule})

    def test_update_preserves_unspecified_fields(self):
        action = validate_action({"action": "update_event", "title": "Test", "new_title": "Yeni"})
        self.assertNotIn("description", action)
        self.assertNotIn("recurrence", action)
        self.assertNotIn("reminder_minutes", action)


class CalendarFeaturesTests(unittest.TestCase):
    def setUp(self):
        self.calendar = MemoryCalendar()
        self.start = datetime.now(TZ).replace(second=0, microsecond=0) + timedelta(days=1)

    def save(self, **kwargs):
        return self.calendar.save_event(serialize(build_event("Test", "Not", self.start, False, **kwargs)))

    def test_weekly_series_with_two_alarms_expands_per_occurrence(self):
        resource = self.save(minutes=[10, 0], recurrence={"freq": "WEEKLY", "count": 3})
        reminders = expand_reminders(resource, TZ, self.start - timedelta(days=1), self.start + timedelta(days=22))
        self.assertEqual(len(reminders), 6)
        self.assertEqual(len({r.key for r in reminders}), 6)
        self.assertEqual(sorted({r.offset for r in reminders}), [0, 10])
        self.assertEqual(sorted({r.occurrence for r in reminders}), [self.start + timedelta(weeks=n) for n in range(3)])

    def test_until_is_inclusive_and_excludes_later_occurrences(self):
        until = (self.start + timedelta(days=2)).strftime("%Y-%m-%d")
        resource = self.save(recurrence={"freq": "DAILY", "until": until})
        reminders = expand_reminders(resource, TZ, self.start - timedelta(days=1), self.start + timedelta(days=10))
        self.assertEqual(len(reminders), 3)

    def test_monthly_31st_skips_months_without_that_date(self):
        start = datetime(2030, 1, 31, 9, tzinfo=TZ)
        resource = self.calendar.save_event(serialize(build_event("Fatura", "", start, False, recurrence={"freq": "MONTHLY", "count": 3})))
        reminders = expand_reminders(resource, TZ, start - timedelta(days=1), datetime(2030, 6, 1, tzinfo=TZ))
        self.assertEqual([r.occurrence.month for r in reminders], [1, 3, 5])

    def test_weekday_at_midnight_keeps_istanbul_weekday(self):
        start = datetime(2030, 1, 7, 0, 30, tzinfo=TZ)
        resource = self.calendar.save_event(serialize(build_event("Test", "", start, False, recurrence={"freq": "WEEKLY", "byday": ["MO"], "count": 2})))
        reminders = expand_reminders(resource, TZ, start - timedelta(days=1), start + timedelta(days=15))
        self.assertEqual([r.occurrence.astimezone(TZ).weekday() for r in reminders], [0, 0])
        self.assertEqual(reminders[0].occurrence, start)
        self.assertEqual(resource.icalendar_instance.get_missing_tzids(), set())

    def test_past_until_rejected_before_save(self):
        with self.assertRaises(ValueError):
            self.save(recurrence={"freq": "DAILY", "until": "2000-01-01"})
        self.assertEqual(self.calendar.records, {})

    def test_edit_keeps_uid_and_unspecified_notes(self):
        resource = self.save(minutes=[10, 0], recurrence={"freq": "DAILY", "count": 3})
        uid = str(resource.icalendar_component["UID"])
        candidate = find_matches(self.calendar, "Test")[0]
        action = validate_action({"action": "update_event", "title": "Test", "new_title": "Yeni", "datetime": (self.start + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M"), "all_day": False})
        apply_change(self.calendar, candidate, action, TZ)
        event = self.calendar.event_by_uid(uid).icalendar_component
        self.assertEqual(str(event["UID"]), uid)
        self.assertEqual(str(event["SUMMARY"]), "Yeni")
        self.assertEqual(str(event["DESCRIPTION"]), "Not")
        self.assertEqual(len(event.walk("VALARM")), 2)
        self.assertIn("RRULE", event)
        self.assertEqual(event["DTSTART"].dt, self.start + timedelta(hours=2))

    def test_stale_selection_cannot_delete_changed_event(self):
        resource = self.save()
        candidate = find_matches(self.calendar, "Test")[0]
        event = resource.icalendar_component
        event["SUMMARY"] = "Değişti"
        self.calendar.save_event(serialize(event))
        with self.assertRaises(ValueError):
            apply_change(self.calendar, candidate, {"action": "delete_event"}, TZ)
        self.assertEqual(len(self.calendar.records), 1)

    def test_delete_affects_only_selected_uid(self):
        self.save()
        self.save()
        candidates = find_matches(self.calendar, "Test")
        apply_change(self.calendar, candidates[0], {"action": "delete_event"}, TZ)
        self.assertEqual(len(self.calendar.records), 1)
        self.assertIn(candidates[1]["uid"], self.calendar.records)


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.calendar = MemoryCalendar()
        self.queue = FakeQueue()
        self.bot = SimpleNamespace(send_message=AsyncMock())
        self.app = SimpleNamespace(job_queue=self.queue, bot=self.bot)
        self.scheduler = ReminderScheduler(lambda: self.calendar, TZ, 123)

    def add(self, start, **kwargs):
        return self.calendar.save_event(serialize(build_event("Test", "", start, False, **kwargs)))

    def context(self, job):
        return SimpleNamespace(job=job, job_queue=self.queue, application=self.app, bot=self.bot)

    async def test_legacy_escaped_checkpoint_preserves_delivery_receipts(self):
        last_seen = datetime.now(TZ) - timedelta(minutes=15)
        delivered = {'uid|time|0': last_seen.timestamp()}
        event = Event()
        event.add('uid', STATE_UID)
        state = json.dumps({'last_seen': last_seen.isoformat(), 'delivered': delivered})
        event.add('X-REMINDER-BOT-STATE', vText(state).to_ical().decode())
        self.calendar.save_event(serialize(event))
        self.assertEqual(self.scheduler._load_state(), (last_seen, delivered))
        self.scheduler.last_seen, self.scheduler.delivered = last_seen, delivered
        self.scheduler._write_state()
        self.assertEqual(self.scheduler._load_state(), (last_seen, delivered))

    async def test_corrupt_checkpoint_does_not_block_future_reminders(self):
        event = Event()
        event.add('uid', STATE_UID)
        event.add('X-REMINDER-BOT-STATE', 'broken checkpoint')
        self.calendar.save_event(serialize(event))
        self.add(datetime.now(TZ) + timedelta(hours=1))
        await self.scheduler.initialize(self.app)
        self.assertEqual(len(self.scheduler.jobs), 1)
        self.bot.send_message.assert_not_awaited()
        self.assertIsNotNone(self.scheduler._load_state())

    async def test_restart_does_not_repeat_sent_early_alarm(self):
        self.add(datetime.now(TZ) + timedelta(minutes=5), minutes=[10, 0])
        await self.scheduler.initialize(self.app)
        # Seed a delivered receipt for an early alarm, then simulate a restart.
        reminders = self.scheduler._read_calendar()
        early = next(r for r in reminders.values() if r.offset == 10)
        self.scheduler.delivered[early.key] = datetime.now(TZ).timestamp()
        await self.scheduler._checkpoint(datetime.now(TZ) - timedelta(minutes=15))
        restarted = ReminderScheduler(lambda: self.calendar, TZ, 123)
        new_queue = FakeQueue()
        await restarted.initialize(SimpleNamespace(job_queue=new_queue, bot=self.bot))
        self.bot.send_message.assert_not_awaited()
        self.assertEqual(len(new_queue.created), 1)
        self.assertEqual(new_queue.created[0].data["reminder"].offset, 0)

    async def test_restart_sends_one_missed_digest_and_persists_receipts(self):
        await self.scheduler.initialize(self.app)
        await self.scheduler._checkpoint(datetime.now(TZ) - timedelta(hours=2))
        self.add(datetime.now(TZ) - timedelta(hours=1))
        restarted = ReminderScheduler(lambda: self.calendar, TZ, 123)
        await restarted.initialize(self.app)
        self.bot.send_message.assert_awaited_once()
        self.assertIn("Kaçırılan", self.bot.send_message.call_args.kwargs["text"])
        again = ReminderScheduler(lambda: self.calendar, TZ, 123)
        await again.initialize(self.app)
        self.bot.send_message.assert_awaited_once()

    async def test_first_install_does_not_replay_old_events(self):
        self.add(datetime.now(TZ) - timedelta(hours=1))
        await self.scheduler.initialize(self.app)
        self.bot.send_message.assert_not_awaited()
        self.assertIn(STATE_UID, self.calendar.records)

    async def test_older_than_24_hours_is_not_in_digest(self):
        await self.scheduler.initialize(self.app)
        await self.scheduler._checkpoint(datetime.now(TZ) - timedelta(days=3))
        self.add(datetime.now(TZ) - timedelta(days=2))
        restarted = ReminderScheduler(lambda: self.calendar, TZ, 123)
        await restarted.initialize(self.app)
        self.bot.send_message.assert_not_awaited()

    async def test_delivery_receipt_prevents_repeat_even_after_restart(self):
        self.add(datetime.now(TZ) + timedelta(hours=1))
        await self.scheduler.sync(self.app)
        job = self.queue.created[0]
        await self.scheduler.send(self.context(job))
        restarted = ReminderScheduler(lambda: self.calendar, TZ, 123)
        queue = FakeQueue()
        await restarted.initialize(SimpleNamespace(job_queue=queue, bot=self.bot))
        self.assertEqual(queue.created, [])
        self.bot.send_message.assert_awaited_once()

    async def test_cancel_series_removes_every_occurrence_and_alarm(self):
        resource = self.add(datetime.now(TZ) + timedelta(hours=1), minutes=[10, 0], recurrence={"freq": "DAILY", "count": 3})
        await self.scheduler.sync(self.app)
        self.assertEqual(len(self.scheduler.jobs), 6)
        self.scheduler.cancel(str(resource.icalendar_component["UID"]))
        self.assertEqual(self.scheduler.jobs, {})


class SelectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        with patch.dict(os.environ, {"TELEGRAM_CHAT_ID": "123", "TELEGRAM_USER_ID": "123", "OPENROUTER_API_KEY": "test", "GEMINI_API_KEY": "test"}):
            import main
            self.main = main
        self.calendar = MemoryCalendar()
        self.calendar.save_event(serialize(build_event("Test", "", datetime.now(TZ) + timedelta(days=1), False)))
        self.scheduler = ReminderScheduler(lambda: self.calendar, TZ, 123)
        self.app = SimpleNamespace(job_queue=FakeQueue(), bot=SimpleNamespace(send_message=AsyncMock()))
        self.context = SimpleNamespace(user_data={}, application=self.app)
        self.query = SimpleNamespace(answer=AsyncMock(), edit_message_text=AsyncMock(), data="")
        self.update = SimpleNamespace(effective_chat=SimpleNamespace(id=123), effective_user=SimpleNamespace(id=123), callback_query=self.query, message=SimpleNamespace(reply_text=AsyncMock()))

    async def prepare(self):
        with patch.object(self.main, "get_calendar", return_value=self.calendar):
            await self.main.offer_event_selection(self.update, self.context, {"action": "delete_event", "title": "Test"})
        return self.context.user_data["pending_event"]["nonce"]

    async def click(self, data):
        self.query.data = data
        with patch.object(self.main, "get_calendar", return_value=self.calendar), patch.object(self.main, "reminder_scheduler", self.scheduler):
            await self.main.handle_event_selection(self.update, self.context)

    async def test_deletion_requires_selection_then_explicit_confirmation(self):
        nonce = await self.prepare()
        await self.click(f"event:{nonce}:confirm:0")
        self.assertEqual(len(self.calendar.records), 1)
        await self.click(f"event:{nonce}:pick:0")
        self.assertEqual(len(self.calendar.records), 1)
        await self.click(f"event:{nonce}:confirm:0")
        self.assertNotIn("pending_event", self.context.user_data)
        self.assertEqual(find_matches(self.calendar, "Test"), [])

    async def test_other_user_cannot_confirm(self):
        nonce = await self.prepare()
        self.update.effective_user.id = 456
        await self.click(f"event:{nonce}:pick:0")
        self.query.answer.assert_not_awaited()
        self.assertEqual(len(self.calendar.records), 1)

    async def test_expired_selection_does_not_modify_calendar(self):
        nonce = await self.prepare()
        self.context.user_data["pending_event"]["expires"] = datetime.now(TZ) - timedelta(minutes=1)
        await self.click(f"event:{nonce}:confirm:0")
        self.assertEqual(len(self.calendar.records), 1)

    async def test_cancel_and_stale_nonce_cannot_delete(self):
        nonce = await self.prepare()
        await self.click("event:oldnonce:pick:0")
        self.assertEqual(len(self.calendar.records), 1)
        await self.click(f"event:{nonce}:cancel")
        await self.click(f"event:{nonce}:confirm:0")
        self.assertEqual(len(self.calendar.records), 1)


if __name__ == "__main__":
    unittest.main()
