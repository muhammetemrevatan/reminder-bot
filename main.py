import os
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv
import caldav
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

load_dotenv()

RADICALE_URL = os.getenv("RADICALE_URL")
RADICALE_USER = os.getenv("RADICALE_USER")
RADICALE_PASS = os.getenv("RADICALE_PASS")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def get_calendar():
    client = caldav.DAVClient(
        url=RADICALE_URL,
        username=RADICALE_USER,
        password=RADICALE_PASS,
    )
    principal = client.principal()
    calendars = principal.calendars()
    for cal in calendars:
        if "reminder" in str(cal.url).lower():
            return cal
    return calendars[0] if calendars else None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hello!\n\n"
        "/today - Today's events\n"
        "/tomorrow - Tomorrow's events\n"
        "/week - Next 7 days\n"
        "/add <title> | <YYYY-MM-DD HH:MM> - Add event"
    )


async def today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_events(update, days=0)


async def tomorrow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_events(update, days=1)


async def week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_events(update, days=7)


async def send_events(update: Update, days: int):
    try:
        cal = get_calendar()
        if not cal:
            await update.message.reply_text("Calendar not found.")
            return

        start_date = datetime.now()
        end_date = start_date + timedelta(days=days + 1)

        events = cal.search(
            start=start_date,
            end=end_date,
            event=True,
            expand=True,
        )

        if not events:
            await update.message.reply_text(f"No events in the next {days} day(s).")
            return

        message = f"Events in the next {days} day(s):\n\n"
        for ev in events:
            comp = ev.icalendar_component
            summary = comp.get("SUMMARY", "Untitled")
            dtstart = comp.get("DTSTART").dt
            if isinstance(dtstart, datetime):
                date_str = dtstart.strftime("%d.%m.%Y %H:%M")
            else:
                date_str = dtstart.strftime("%d.%m.%Y")
            message += f"- {date_str} - {summary}\n"

        await update.message.reply_text(message)

    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text(f"Error: {e}")


async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        text = " ".join(context.args)
        if "|" not in text:
            await update.message.reply_text(
                "Usage: /add <title> | <YYYY-MM-DD HH:MM>"
            )
            return

        title, date_str = [x.strip() for x in text.split("|", 1)]
        event_date = datetime.strptime(date_str, "%Y-%m-%d %H:%M")

        cal = get_calendar()
        if not cal:
            await update.message.reply_text("Calendar not found.")
            return

        event_ical = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//TelegramBot//EN
BEGIN:VEVENT
SUMMARY:{title}
DTSTART:{event_date.strftime('%Y%m%dT%H%M%S')}
DTEND:{(event_date + timedelta(hours=1)).strftime('%Y%m%dT%H%M%S')}
END:VEVENT
END:VCALENDAR"""

        cal.save_event(event_ical)
        await update.message.reply_text(f"Added: {title} - {date_str}")

    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text(f"Error: {e}")


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("today", today))
    app.add_handler(CommandHandler("tomorrow", tomorrow))
    app.add_handler(CommandHandler("week", week))
    app.add_handler(CommandHandler("add", add))

    logger.info("Bot started.")
    app.run_polling()


if __name__ == "__main__":
    main()