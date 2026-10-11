import asyncio
import os
import json
import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from dotenv import load_dotenv
import caldav
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from openai import OpenAI
from zoneinfo import ZoneInfo
from google import genai
from icalendar import Alarm, Calendar, Event

from reminders import Reminder, ReminderScheduler

TZ = ZoneInfo("Europe/Istanbul")

load_dotenv()

RADICALE_URL = os.getenv("RADICALE_URL", "https://cal.muhammetemrevatan.com")
RADICALE_USER = os.getenv("RADICALE_USER", "vatanemre123")
RADICALE_PASS = os.getenv("RADICALE_PASS")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID"))
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

openrouter_client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)

gemini_client = genai.Client(api_key=GEMINI_API_KEY)


def get_calendar():
    client = caldav.DAVClient(
        url=RADICALE_URL,
        username=RADICALE_USER,
        password=RADICALE_PASS,
        timeout=15,
    )
    principal = client.principal()
    calendars = principal.calendars()
    for cal in calendars:
        try:
            if "reminder" in cal.get_display_name().lower():
                return cal
        except Exception:
            continue

    logger.info("Reminder calendar not found, creating new one.")
    return principal.make_calendar(name="reminder")


reminder_scheduler = ReminderScheduler(get_calendar, TZ, TELEGRAM_CHAT_ID)


def save_calendar_event(title, description, start, all_day):
    uid = str(uuid4())
    calendar = Calendar()
    calendar.add("prodid", "-//TelegramBot//TR")
    calendar.add("version", "2.0")
    event = Event()
    event.add("uid", uid)
    event.add("dtstamp", datetime.now(timezone.utc))
    event.add("summary", title)
    event.add("description", description)
    if all_day:
        event.add("dtstart", start.date())
        event.add("dtend", (start + timedelta(days=1)).date())
    else:
        event.add("dtstart", start.astimezone(timezone.utc))
        event.add("dtend", (start + timedelta(hours=1)).astimezone(timezone.utc))
        alarm = Alarm()
        alarm.add("action", "DISPLAY")
        alarm.add("description", title)
        alarm.add("trigger", timedelta(0))
        event.add_component(alarm)
    calendar.add_component(event)
    get_calendar().save_event(calendar.to_ical().decode("utf-8"))
    return uid


async def add_calendar_event(context, title, description, start, all_day):
    async with reminder_scheduler.lock:
        uid = await asyncio.to_thread(
            save_calendar_event, title, description, start, all_day
        )
        if not all_day and start > datetime.now(TZ):
            reminder_scheduler.schedule(
                context.job_queue, Reminder(uid, title, start)
            )


def _clean_json(text: str):
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()
    return json.loads(text)


def parse_with_openrouter(message: str):
    now = datetime.now(TZ).strftime("%Y-%m-%d %A %H:%M")
    prompt = f"""
Şu anki tarih ve saat: {now}

Sen bir takvim asistanısın. Kullanıcının mesajını analiz et ve SADECE şu JSON formatında cevap ver:
{{
  "action": "add_event" | "add_multi_event" | "delete_event" | "list_events" | "chat",
  "title": "etkinlik başlığı (varsa)",
  "description": "etkinlik açıklaması (varsa)",
  "datetime": "YYYY-MM-DD HH:MM (tek etkinlik için, saat varsa)",
  "dates": ["YYYY-MM-DD", "YYYY-MM-DD"] (çoklu gün için),
  "all_day": true | false,
  "days_ahead": 0 | 1 | 7,
  "reply": "kullanıcıya verilecek kısa cevap"
}}

ÖNEMLİ KURALLAR:
1. Kullanıcı bir etkinlik eklemek istiyorsa ASLA soru sorma. Direkt action'ı seç.
2. "24 ve 25'inde", "2 gün boyunca", "3 gün", "hafta sonu" gibi ifadelerde action: "add_multi_event" kullan ve dates listesine her günü ayrı ayrı ekle.
3. Saat belirtilmemişse all_day: true yap.
4. "Şu an 00:00" gibi varsayım yapma. Saat yoksa all_day true.
5. Sadece etkinlik ekleme/silme/listeleme dışında bir şey sorulursa action: "chat" kullan.
6. Kısa cevap ver, gereksiz soru sorma.
7. Kullanıcı "açıklama", "not", "detay" gibi ifadeler kullanırsa bunları description alanına koy.

Kullanıcının mesajı: "{message}"
"""

    try:
        response = openrouter_client.chat.completions.create(
            model="nvidia/nemotron-3-super-120b-a12b:free",
            messages=[{"role": "user", "content": prompt}],
            timeout=5,
        )
        return _clean_json(response.choices[0].message.content.strip())
    except Exception as e:
        logger.warning(f"(OpenRouter) failed: {e}")

    try:
        response = openrouter_client.chat.completions.create(
            model="openrouter/free",
            messages=[{"role": "user", "content": prompt}],
            timeout=5,
        )
        return _clean_json(response.choices[0].message.content.strip())
    except Exception as e:
        logger.error(f"Free router failed: {e}")

    return {
        "action": "chat",
        "reply": "AI sunucuları cevap vermedi, lütfen tekrar deneyin.",
    }


def delete_event_by_title(title: str):
    cal = get_calendar()
    start = datetime.now(TZ) - timedelta(days=365)
    end = datetime.now(TZ) + timedelta(days=365)
    events = cal.search(start=start, end=end, event=True, expand=True)
    deleted = []
    for ev in events:
        comp = ev.icalendar_component
        summary = comp.get("SUMMARY", "")
        if title.lower() in summary.lower():
            ev.delete()
            deleted.append((str(comp.get("UID", "")), str(summary)))
    return deleted


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if str(chat_id) != str(TELEGRAM_CHAT_ID):
        return

    try:
        voice_file = await update.message.voice.get_file()
        file_path = f"voice_{update.message.message_id}.ogg"
        await voice_file.download_to_drive(file_path)

        uploaded_file = await asyncio.to_thread(gemini_client.files.upload, file=file_path)

        response = await asyncio.to_thread(
            gemini_client.models.generate_content,
            model="gemini-3.8-flash",
            contents=[
                uploaded_file,
                "Bu ses kaydını Türkçe olarak metne çevir. Sadece transkripti yaz, başka bir şey ekleme."
            ],
        )
        transcript = response.text.strip()

        if not transcript:
            raise Exception("Transkript alınamadı.")

        try:
            await asyncio.to_thread(gemini_client.files.delete, name=uploaded_file.name)
        except Exception:
            pass

        os.remove(file_path)

        await update.message.reply_text(f"🎤 Anladım: {transcript}")
        await handle_user_text(update, context, transcript)

    except Exception as e:
        logger.error(f"Voice error: {e}")
        await update.message.reply_text(f"Sesli mesajı anlayamadım: {e}")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await handle_user_text(update, context, update.message.text)


async def handle_user_text(update: Update, context: ContextTypes.DEFAULT_TYPE, user_message: str):
    chat_id = update.effective_chat.id

    if str(chat_id) != str(TELEGRAM_CHAT_ID):
        return

    try:
        parsed = await asyncio.to_thread(parse_with_openrouter, user_message)
        action = parsed.get("action")

        if action == "add_event":
            title = parsed.get("title", "Hatırlatıcı")
            description = parsed.get("description", "")
            dt_str = parsed.get("datetime")
            all_day = parsed.get("all_day", False)

            if not dt_str:
                await update.message.reply_text("Tarih/saat anlayamadım. Tekrar yazar mısın?")
                return

            if all_day:
                event_date = datetime.strptime(dt_str.split(" ")[0], "%Y-%m-%d").replace(tzinfo=TZ)
            else:
                event_date = datetime.strptime(dt_str, "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
            await add_calendar_event(context, title, description, event_date, all_day)
            if all_day:
                await update.message.reply_text(f"✅ Tamam, {dt_str.split(' ')[0]} için '{title}' tüm gün etkinliği oluşturuldu.")
            else:
                await update.message.reply_text(f"✅ Tamam, {dt_str} için '{title}' hatırlatıcısı oluşturuldu.")

        elif action == "add_multi_event":
            title = parsed.get("title", "Etkinlik")
            description = parsed.get("description", "")
            dates = parsed.get("dates", [])
            all_day = parsed.get("all_day", True)

            if not dates:
                await update.message.reply_text("Hangi günler olduğunu anlayamadım.")
                return

            added = []
            for d in dates:
                dt_str = f"{d} 09:00"
                event_date = datetime.strptime(dt_str, "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
                await add_calendar_event(context, title, description, event_date, all_day)
                added.append(d if all_day else dt_str)

            await update.message.reply_text(
                f"✅ Tamam, şu günler için '{title}' etkinliği eklendi:\n" +
                "\n".join(f"• {d}" for d in added)
            )

        elif action == "delete_event":
            title = parsed.get("title")
            if not title:
                await update.message.reply_text("Hangi etkinliği sileceğimi anlayamadım.")
                return
            async with reminder_scheduler.lock:
                deleted = await asyncio.to_thread(delete_event_by_title, title)
                for uid, _ in deleted:
                    reminder_scheduler.cancel(uid)
            if deleted:
                await update.message.reply_text(
                    "🗑️ Silindi:\n" + "\n".join(f"• {title}" for _, title in deleted)
                )
            else:
                await update.message.reply_text(f"'{title}' bulunamadı.")

        elif action == "list_events":
            days = parsed.get("days_ahead", 7)
            cal = await asyncio.to_thread(get_calendar)

            start_date = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
            end_date = start_date + timedelta(days=days + 1)

            events = await asyncio.to_thread(
                cal.search, start=start_date, end=end_date, event=True, expand=True
            )

            if not events:
                await update.message.reply_text(f"Önümüzdeki {days} günde etkinlik yok.")
                return

            if days == 0:
                header = "Bugünkü etkinlikler:\n\n"
            elif days == 1:
                header = "Yarınki etkinlikler:\n\n"
            else:
                header = f"Önümüzdeki {days} günün etkinlikleri:\n\n"

            msg = header
            for ev in events:
                comp = ev.icalendar_component
                summary = comp.get("SUMMARY", "Başlıksız")
                description = comp.get("DESCRIPTION", "")
                dtstart = comp.get("DTSTART").dt
                if isinstance(dtstart, datetime):
                    if dtstart.tzinfo is not None:
                        dtstart = dtstart.astimezone(TZ)
                    date_str = dtstart.strftime("%d.%m.%Y %H:%M")
                else:
                    date_str = dtstart.strftime("%d.%m.%Y")
                msg += f"• {date_str} — {summary}\n"
                if description:
                    msg += f"  📝 {description}\n"

            await update.message.reply_text(msg)

        else:
            reply = parsed.get("reply", "Anlayamadım, tekrar yazar mısın?")
            await update.message.reply_text(reply)

    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text(f"Bir hata oldu: {e}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Merhaba! 👋\n\n"
        "Bana doğal dille yaz veya sesli mesaj gönder, anlarım:\n"
        "• 'Yarın saat 15:00'a sinema hatırlatıcısı oluştur'\n"
        "• '24 ve 25'inde Çağla ile buluşacağız, takvime ekle'\n"
        "• 'Sinema etkinliğini sil'\n"
        "• 'Yaklaşan etkinlikleri göster'"
    )


def main():
    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .post_init(reminder_scheduler.initialize)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    logger.info("Bot started.")
    app.run_polling()


if __name__ == "__main__":
    main()
