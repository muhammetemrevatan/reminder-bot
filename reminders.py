import asyncio
import base64
import binascii
import copy
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import recurring_ical_events
from apscheduler.jobstores.base import JobLookupError
from caldav.lib.error import NotFoundError
from icalendar import Calendar, Event

from calendar_ops import STATE_UID, serialize

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Reminder:
    uid: str
    title: str
    when: datetime
    occurrence: datetime | None = None
    offset: int = 0
    recurring: bool = False

    @property
    def key(self):
        stamp = (self.occurrence or self.when).astimezone(timezone.utc).isoformat()
        return f"{self.uid}|{stamp}|{self.offset}"


def reminders_from_component(component, tz, *, recurring=False):
    start = component.get("DTSTART")
    uid = str(component.get("UID", ""))
    if not uid or uid == STATE_UID or start is None or not isinstance(start.dt, datetime):
        return []
    if str(component.get("STATUS", "")).upper() == "CANCELLED":
        return []
    when = start.dt
    if when.tzinfo is None:
        # Older bot records contain floating Istanbul local time.
        when = when.replace(tzinfo=tz)
    result = []
    for alarm in component.walk("VALARM"):
        trigger = alarm.get("TRIGGER")
        delta = getattr(trigger, "dt", None)
        if not isinstance(delta, timedelta) or delta > timedelta(0) or delta < -timedelta(days=7):
            continue
        base = when
        if str(alarm.get("RELATED", "START")) == "END" or str(getattr(trigger, "params", {}).get("RELATED", "START")) == "END":
            end = component.get("DTEND")
            if end is None or not isinstance(end.dt, datetime):
                continue
            base = end.dt.replace(tzinfo=tz) if end.dt.tzinfo is None else end.dt
        notify_at = base + delta
        offset = int((when - notify_at).total_seconds() // 60)
        result.append(Reminder(uid, str(component.get("SUMMARY", "Hatırlatıcı")), notify_at, when, offset, recurring))
    return result


def reminder_from_event(event, tz):
    component = event.icalendar_component
    if component.get("RRULE"):
        return None
    reminders = reminders_from_component(component, tz)
    return reminders[0] if reminders else None


def expand_reminders(resource, tz, start, end):
    component = resource.icalendar_component
    if str(component.get("UID", "")) == STATE_UID:
        return []
    if not component.get("RRULE") and not component.get("RDATE"):
        return reminders_from_component(component, tz)
    instance = getattr(resource, "icalendar_instance", None)
    if isinstance(instance, Calendar):
        calendar = copy.deepcopy(instance)
    else:
        calendar = Calendar()
        calendar.add_component(copy.deepcopy(component))
    for event in calendar.walk("VEVENT"):
        for field in ("DTSTART", "DTEND", "RECURRENCE-ID"):
            prop = event.get(field)
            if prop is not None and isinstance(prop.dt, datetime) and prop.dt.tzinfo is None:
                value = prop.dt.replace(tzinfo=tz)
                event.pop(field, None)
                event.add(field, value)
    occurrences = recurring_ical_events.of(calendar).between(start, end)
    result = []
    for occurrence in occurrences:
        result.extend(reminders_from_component(occurrence, tz, recurring=True))
    return result


class ReminderScheduler:
    def __init__(self, calendar_factory, timezone, chat_id):
        self.calendar_factory = calendar_factory
        self.timezone = timezone
        self.chat_id = chat_id
        self.lock = asyncio.Lock()
        self.jobs = {}
        self.delivered = {}
        self.last_seen = None
        self.recover_since = None
        self.state_loaded = False

    def _remove_key(self, key):
        entry = self.jobs.pop(key, None)
        if entry is not None:
            try:
                entry[1].schedule_removal()
            except JobLookupError:
                pass

    def cancel(self, uid):
        for key, (reminder, _) in list(self.jobs.items()):
            if reminder.uid == uid:
                self._remove_key(key)

    def schedule(self, queue, reminder, *, delay=None, attempts=0):
        self._remove_key(reminder.key)
        job = queue.run_once(
            self.send, when=reminder.when if delay is None else delay,
            name=f"reminder:{reminder.key}",
            data={"reminder": reminder, "attempts": attempts},
            chat_id=self.chat_id, job_kwargs={"misfire_grace_time": 300},
        )
        self.jobs[reminder.key] = (reminder, job)

    def _read_calendar(self):
        now = datetime.now(self.timezone)
        start = now - timedelta(days=1)
        end = now + timedelta(days=37)
        reminders = {}
        for event in self.calendar_factory().events():
            try:
                for reminder in expand_reminders(event, self.timezone, start, end):
                    if start <= reminder.when <= now + timedelta(days=30):
                        reminders[reminder.key] = reminder
            except (ValueError, TypeError, AttributeError):
                logger.exception("Skipping malformed calendar event")
        return reminders

    def _load_state(self):
        try:
            event = self.calendar_factory().event_by_uid(STATE_UID)
        except NotFoundError:
            return None
        raw = event.icalendar_component.get("X-REMINDER-BOT-STATE")
        if raw is None:
            return None
        try:
            value = str(raw)
            if value.startswith("base64:"):
                value = base64.b64decode(value[7:], validate=True).decode("utf-8")
            try:
                state = json.loads(value)
            except json.JSONDecodeError:
                # Older X-properties may be returned with iCalendar TEXT escaping.
                value = re.sub(r"\\([\\,;nN])", lambda match: "\n" if match[1] in ("n", "N") else match[1], value)
                state = json.loads(value)
            last_seen = datetime.fromisoformat(state["last_seen"])
            delivered = state["delivered"]
            if last_seen.tzinfo is None or not isinstance(delivered, dict) or any(
                not isinstance(key, str) or type(stamp) not in (int, float)
                for key, stamp in delivered.items()
            ):
                raise ValueError("Invalid reminder checkpoint")
        except (ValueError, TypeError, KeyError, binascii.Error):
            logger.warning("Invalid reminder checkpoint; restoring future reminders without historical replay")
            return None
        return last_seen, delivered

    def _write_state(self):
        event = Event()
        event.add("uid", STATE_UID)
        event.add("dtstamp", datetime.now(timezone.utc))
        event.add("dtstart", datetime(2000, 1, 1).date())
        event.add("dtend", datetime(2000, 1, 2).date())
        event.add("summary", "Reminder Bot delivery checkpoint")
        event.add("transp", "TRANSPARENT")
        event.add("class", "PRIVATE")
        state = json.dumps({
            "last_seen": self.last_seen.isoformat(), "delivered": self.delivered,
        }, separators=(",", ":"))
        event.add("X-REMINDER-BOT-STATE", "base64:" + base64.b64encode(state.encode("utf-8")).decode("ascii"))
        self.calendar_factory().save_event(serialize(event))

    async def _checkpoint(self, now):
        cutoff = (now - timedelta(days=30)).timestamp()
        self.delivered = {key: value for key, value in self.delivered.items() if value >= cutoff}
        self.last_seen = now
        await asyncio.to_thread(self._write_state)

    async def _digest(self, application, reminders, now):
        if self.recover_since is None:
            return
        start = max(self.recover_since - timedelta(minutes=2), now - timedelta(days=1))
        missed = [r for r in reminders.values() if start <= r.when <= now and r.key not in self.delivered]
        missed.sort(key=lambda r: r.when)
        if missed:
            rows = [f"• {r.when.astimezone(self.timezone):%d.%m %H:%M} — {r.title}" for r in missed]
            # Telegram messages have a size limit; split the one recovery digest into pages.
            page = "📋 Kaçırılan bildirimler (son 24 saat):\n"
            for row in rows:
                if len(page) + len(row) + 1 > 3500:
                    await application.bot.send_message(chat_id=self.chat_id, text=page)
                    page = "📋 Kaçırılan bildirimler (devam):\n"
                page += row[:500] + "\n"
            await application.bot.send_message(chat_id=self.chat_id, text=page)
            for reminder in missed:
                self.delivered[reminder.key] = now.timestamp()
        self.recover_since = None

    async def sync(self, application):
        async with self.lock:
            try:
                reminders = await asyncio.to_thread(self._read_calendar)
                now = datetime.now(self.timezone)
                if not self.state_loaded:
                    state = await asyncio.to_thread(self._load_state)
                    if state:
                        self.last_seen, self.delivered = state
                        self.recover_since = self.last_seen
                    self.state_loaded = True
                await self._digest(application, reminders, now)
                for key, (scheduled, _) in list(self.jobs.items()):
                    if reminders.get(key) != scheduled or key in self.delivered:
                        self._remove_key(key)
                restored = 0
                for key, reminder in reminders.items():
                    if reminder.when > now and key not in self.jobs and key not in self.delivered:
                        self.schedule(application.job_queue, reminder)
                        restored += 1
                await self._checkpoint(now)
                logger.info("Reminder sync: %d scheduled, %d active", restored, len(self.jobs))
            except Exception:
                logger.exception("Reminder sync failed; keeping existing jobs")

    async def sync_job(self, context):
        await self.sync(context.application)

    def _read_reminder(self, reminder):
        try:
            event = self.calendar_factory().event_by_uid(reminder.uid)
        except NotFoundError:
            return None
        start = (reminder.occurrence or reminder.when) - timedelta(minutes=1)
        end = start + timedelta(minutes=2)
        return next((r for r in expand_reminders(event, self.timezone, start, end) if r.key == reminder.key and r.when == reminder.when), None)

    async def send(self, context):
        async with self.lock:
            reminder = context.job.data["reminder"]
            entry = self.jobs.get(reminder.key)
            if entry is None or entry[1] is not context.job:
                return
            try:
                if reminder.key not in self.delivered:
                    current = await asyncio.to_thread(self._read_reminder, reminder)
                    if current is None:
                        self.jobs.pop(reminder.key, None)
                        return
                    suffix = f" ({current.offset} dakika önce)" if current.offset > 0 else ""
                    await context.bot.send_message(chat_id=self.chat_id, text=f"⏰ Hatırlatma: {current.title}{suffix}")
                    self.delivered[reminder.key] = datetime.now(self.timezone).timestamp()
                await self._checkpoint(datetime.now(self.timezone))
            except Exception:
                logger.exception("Reminder delivery/checkpoint failed for UID %s", reminder.uid)
                attempts = context.job.data["attempts"]
                if attempts < 3:
                    self.schedule(context.job_queue, reminder, delay=30, attempts=attempts + 1)
                else:
                    self.jobs.pop(reminder.key, None)
                return
            self.jobs.pop(reminder.key, None)

    async def initialize(self, application):
        await self.sync(application)
        application.job_queue.run_repeating(self.sync_job, interval=60, first=60, name="sync-reminders")
