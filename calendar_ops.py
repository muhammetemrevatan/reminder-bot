import hashlib
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

from icalendar import Alarm, Calendar, Event

STATE_UID = "reminder-bot-checkpoint-v1"


def serialize(event):
    calendar = Calendar()
    calendar.add("prodid", "-//TelegramBot//TR")
    calendar.add("version", "2.0")
    calendar.add_component(event)
    calendar.add_missing_timezones(last_date=date(2100, 1, 1))
    return calendar.to_ical().decode("utf-8")


def set_alarms(event, minutes):
    event.subcomponents = [c for c in event.subcomponents if c.name != "VALARM"]
    for offset in minutes:
        alarm = Alarm()
        alarm.add("action", "DISPLAY")
        alarm.add("description", str(event.get("SUMMARY", "Hatırlatıcı")))
        alarm.add("trigger", -timedelta(minutes=offset))
        event.add_component(alarm)


def set_recurrence(event, rule, tz):
    event.pop("RRULE", None)
    if rule:
        values = {k.upper(): v for k, v in rule.items() if k != "until"}
        if "until" in rule:
            end = datetime.strptime(rule["until"], "%Y-%m-%d").replace(
                hour=23, minute=59, second=59, tzinfo=tz
            )
            start = event["DTSTART"].dt
            values["UNTIL"] = end.astimezone(timezone.utc) if isinstance(start, datetime) else end.date()
            start_date = start.astimezone(tz).date() if isinstance(start, datetime) and start.tzinfo else start.date() if isinstance(start, datetime) else start
            if end.date() < start_date:
                raise ValueError("Tekrar bitişi başlangıçtan önce olamaz.")
        event.add("rrule", values)


def build_event(title, description, start, all_day, minutes=None, recurrence=None):
    event = Event()
    event.add("uid", str(uuid4()))
    event.add("dtstamp", datetime.now(timezone.utc))
    event.add("summary", title)
    event.add("description", description)
    event.add("dtstart", start.date() if all_day else start)
    event.add("dtend", (start + timedelta(days=1)).date() if all_day else start + timedelta(hours=1))
    set_alarms(event, [] if all_day else ([0] if minutes is None else minutes))
    set_recurrence(event, recurrence, start.tzinfo)
    return event


def fingerprint(component):
    return hashlib.sha256(component.to_ical()).hexdigest()


def find_matches(calendar, title):
    matches = []
    for resource in calendar.events():
        event = resource.icalendar_component
        if str(event.get("UID", "")) == STATE_UID:
            continue
        if title.casefold() in str(event.get("SUMMARY", "")).casefold():
            matches.append({
                "uid": str(event["UID"]), "title": str(event.get("SUMMARY", "")),
                "start": event["DTSTART"].dt, "version": fingerprint(event),
                "recurring": bool(event.get("RRULE")),
            })
    return matches


def apply_change(calendar, candidate, action, tz):
    resource = calendar.event_by_uid(candidate["uid"])
    event = resource.icalendar_component
    if fingerprint(event) != candidate["version"]:
        raise ValueError("Etkinlik seçimden sonra değişti. Lütfen yeniden seç.")
    if action["action"] == "delete_event":
        resource.delete()
        return
    instance = getattr(resource, "icalendar_instance", None)
    if isinstance(instance, Calendar) and len(instance.walk("VEVENT")) > 1:
        raise ValueError("Bu serinin özel gün değişiklikleri var; takvim uygulamasından düzenle.")
    if "new_title" in action:
        event["SUMMARY"] = action["new_title"]
    if "description" in action:
        event["DESCRIPTION"] = action["description"]
    if "datetime" in action:
        all_day = action.get("all_day", False)
        start = datetime.strptime(action["datetime"], "%Y-%m-%d" if all_day else "%Y-%m-%d %H:%M").replace(tzinfo=tz)
        if start <= datetime.now(tz) and not action.get("recurrence", event.get("RRULE")):
            raise ValueError("Yeni tarih gelecekte olmalı.")
        previous = event["DTSTART"].dt
        was_all_day = not isinstance(previous, datetime)
        duration = event["DTEND"].dt - previous if event.get("DTEND") else timedelta(hours=1)
        event.pop("DTSTART", None)
        event.pop("DTEND", None)
        event.pop("DURATION", None)
        event.add("dtstart", start.date() if all_day else start)
        event.add("dtend", (start + timedelta(days=1)).date() if all_day else start + (timedelta(hours=1) if was_all_day else duration))
        if all_day:
            set_alarms(event, [])
        elif was_all_day and "reminder_minutes" not in action:
            set_alarms(event, [0])
    if "recurrence" in action:
        set_recurrence(event, action["recurrence"], tz)
    if "reminder_minutes" in action:
        if not isinstance(event["DTSTART"].dt, datetime) and action["reminder_minutes"]:
            raise ValueError("Bildirim için etkinliğe bir saat eklemelisin.")
        set_alarms(event, action["reminder_minutes"])
    event.pop("DTSTAMP", None)
    event.add("dtstamp", datetime.now(timezone.utc))
    event["SEQUENCE"] = int(event.get("SEQUENCE", 0)) + 1
    resource.data = serialize(event)
    resource.save()
