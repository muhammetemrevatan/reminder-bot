import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime

from apscheduler.jobstores.base import JobLookupError
from caldav.lib.error import NotFoundError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Reminder:
    uid: str
    title: str
    when: datetime


def reminder_from_event(event, timezone):
    component = event.icalendar_component
    start = component.get("DTSTART")
    uid = str(component.get("UID", ""))
    if (
        not uid
        or start is None
        or not isinstance(start.dt, datetime)
        or component.get("RRULE")
        or str(component.get("STATUS", "")).upper() == "CANCELLED"
        or not component.walk("VALARM")
    ):
        return None
    when = start.dt
    if when.tzinfo is None:
        # Older bot records store floating local time without TZID.
        when = when.replace(tzinfo=timezone)
    return Reminder(uid, str(component.get("SUMMARY", "Hatırlatıcı")), when)


class ReminderScheduler:
    def __init__(self, calendar_factory, timezone, chat_id):
        self.calendar_factory = calendar_factory
        self.timezone = timezone
        self.chat_id = chat_id
        self.lock = asyncio.Lock()
        self.jobs = {}

    def cancel(self, uid):
        entry = self.jobs.pop(uid, None)
        if entry is not None:
            try:
                entry[1].schedule_removal()
            except JobLookupError:
                pass

    def schedule(self, queue, reminder, *, delay=None, attempts=0):
        self.cancel(reminder.uid)
        job = queue.run_once(
            self.send,
            when=reminder.when if delay is None else delay,
            name=f"reminder:{reminder.uid}",
            data={"reminder": reminder, "attempts": attempts},
            chat_id=self.chat_id,
            job_kwargs={"misfire_grace_time": 300},
        )
        self.jobs[reminder.uid] = (reminder, job)

    def _read_calendar(self):
        reminders = {}
        for event in self.calendar_factory().events():
            try:
                reminder = reminder_from_event(event, self.timezone)
            except (ValueError, TypeError, AttributeError):
                logger.exception("Skipping malformed calendar event")
                continue
            if reminder is not None:
                reminders[reminder.uid] = reminder
        return reminders

    async def sync(self, application):
        async with self.lock:
            try:
                reminders = await asyncio.to_thread(self._read_calendar)
            except Exception:
                logger.exception("Reminder sync failed; keeping existing jobs")
                return

            for uid, (scheduled, _) in list(self.jobs.items()):
                if reminders.get(uid) != scheduled:
                    self.cancel(uid)

            now = datetime.now(self.timezone)
            restored = 0
            for uid, reminder in reminders.items():
                if reminder.when > now and uid not in self.jobs:
                    self.schedule(application.job_queue, reminder)
                    restored += 1
            logger.info("Reminder sync: %d scheduled, %d active", restored, len(self.jobs))

    async def sync_job(self, context):
        await self.sync(context.application)

    def _read_reminder(self, uid):
        try:
            event = self.calendar_factory().event_by_uid(uid)
        except NotFoundError:
            return None
        return reminder_from_event(event, self.timezone)

    async def send(self, context):
        async with self.lock:
            reminder = context.job.data["reminder"]
            entry = self.jobs.get(reminder.uid)
            if entry is None or entry[1] is not context.job:
                return
            try:
                current = await asyncio.to_thread(self._read_reminder, reminder.uid)
                if current is None or current.when != reminder.when:
                    self.jobs.pop(reminder.uid, None)
                    return
                await context.bot.send_message(
                    chat_id=self.chat_id,
                    text=f"⏰ Hatırlatma: {current.title}",
                )
            except Exception:
                logger.exception("Reminder delivery failed for UID %s", reminder.uid)
                attempts = context.job.data["attempts"]
                if attempts < 3:
                    self.schedule(context.job_queue, reminder, delay=30, attempts=attempts + 1)
                else:
                    self.jobs.pop(reminder.uid, None)
                return
            self.jobs.pop(reminder.uid, None)

    async def initialize(self, application):
        await self.sync(application)
        application.job_queue.run_repeating(
            self.sync_job, interval=60, first=60, name="sync-reminders"
        )
