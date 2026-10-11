import os
import json
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv
import caldav
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    JobQueue,
    PicklePersistence,
)
from openai import OpenAI
from zoneinfo import ZoneInfo
from google import genai
from google.genai import types

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
            deleted.append(summary)
    return deleted


async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    title = job.data["title"]
    chat_id = job.data["chat_id"]
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"⏰ Hatırlatma: {title}",
    )


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if str(chat_id) != str(TELEGRAM_CHAT_ID):
        return

    try:
        voice_file = await update.message.voice.get_file()
        file_path = f"voice_{update.message.message_id}.ogg"
        await voice_file.download_to_drive(file_path)

        uploaded_file = gemini_client.files.upload(file=file_path)

        interaction = gemini_client.interactions.create(
            model="gemini-3.5-transcribe",
            input=[
                types.Content(
                    parts=[
                        types.Part(
                            file_data=types.FileData(
                                mime_type="audio/ogg",
                                file_uri=uploaded_file.uri,
                            )
                        )
                    ]
                )
            ],
            generation_config=types.GenerationConfig(
                transcription_config=types.TranscriptionConfig(
                    language_codes=["tr-TR"],
                    mode="smart",
                )
            ),
        )
        transcript = interaction.output_text

        if not transcript:
            raise Exception("Transkript alınamadı.")

        try:
            gemini_client.files.delete(name=uploaded_file.name)
        except Exception:
            pass

        os.remove(file_path)

        update.message.text = transcript
        await update.message.reply_text(f"🎤 Anladım: {transcript}")
        await handle_message(update, context)

    except Exception as e:
        logger.error(f"Voice error: {e}")
        await update.message.reply_text(f"Sesli mesajı anlayamadım: {e}")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    chat_id = update.effective_chat.id

    if str(chat_id) != str(TELEGRAM_CHAT_ID):
        return

    try:
        parsed = parse_with_openrouter(user_message)
        action = parsed.get("action")

        if action == "add_event":
            title = parsed.get("title", "Hatırlatıcı")
            description = parsed.get("description", "")
            dt_str = parsed.get("datetime")
            all_day = parsed.get("all_day", False)

            if not dt_str:
                await update.message.reply_text("Tarih/saat anlayamadım. Tekrar yazar mısın?")
                return

            cal = get_calendar()

            if all_day:
                date_clean = dt_str.split(" ")[0].replace("-", "")
                next_day = (datetime.strptime(dt_str.split(" ")[0], "%Y-%m-%d") + timedelta(days=1)).strftime("%Y%m%d")
                event_ical = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//TelegramBot//TR
BEGIN:VEVENT
SUMMARY:{title}
DESCRIPTION:{description}
DTSTART;VALUE=DATE:{date_clean}
DTEND;VALUE=DATE:{next_day}
END:VEVENT
END:VCALENDAR"""
                cal.save_event(event_ical)
                await update.message.reply_text(f"✅ Tamam, {dt_str.split(' ')[0]} için '{title}' tüm gün etkinliği oluşturuldu.")
            else:
                event_date = datetime.strptime(dt_str, "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
                event_ical = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//TelegramBot//TR
BEGIN:VEVENT
SUMMARY:{title}
DESCRIPTION:{description}
DTSTART:{event_date.strftime('%Y%m%dT%H%M%S')}
DTEND:{(event_date + timedelta(hours=1)).strftime('%Y%m%dT%H%M%S')}
BEGIN:VALARM
ACTION:DISPLAY
DESCRIPTION:{title}
TRIGGER:-PT0M
END:VALARM
END:VEVENT
END:VCALENDAR"""
                cal.save_event(event_ical)
                context.job_queue.run_once(
                    send_reminder,
                    when=event_date,
                    data={"title": title, "chat_id": chat_id},
                )
                await update.message.reply_text(f"✅ Tamam, {dt_str} için '{title}' hatırlatıcısı oluşturuldu.")

        elif action == "add_multi_event":
            title = parsed.get("title", "Etkinlik")
            description = parsed.get("description", "")
            dates = parsed.get("dates", [])
            all_day = parsed.get("all_day", True)

            if not dates:
                await update.message.reply_text("Hangi günler olduğunu anlayamadım.")
                return

            cal = get_calendar()
            added = []

            for d in dates:
                if all_day:
                    date_clean = d.replace("-", "")
                    next_day = (datetime.strptime(d, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y%m%d")
                    event_ical = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//TelegramBot//TR
BEGIN:VEVENT
SUMMARY:{title}
DESCRIPTION:{description}
DTSTART;VALUE=DATE:{date_clean}
DTEND;VALUE=DATE:{next_day}
END:VEVENT
END:VCALENDAR"""
                    cal.save_event(event_ical)
                    added.append(d)
                else:
                    dt_str = f"{d} 09:00"
                    event_date = datetime.strptime(dt_str, "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
                    event_ical = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//TelegramBot//TR
BEGIN:VEVENT
SUMMARY:{title}
DESCRIPTION:{description}
DTSTART:{event_date.strftime('%Y%m%dT%H%M%S')}
DTEND:{(event_date + timedelta(hours=1)).strftime('%Y%m%dT%H%M%S')}
BEGIN:VALARM
ACTION:DISPLAY
DESCRIPTION:{title}
TRIGGER:-PT0M
END:VALARM
END:VEVENT
END:VCALENDAR"""
                    cal.save_event(event_ical)
                    context.job_queue.run_once(
                        send_reminder,
                        when=event_date,
                        data={"title": title, "chat_id": chat_id},
                    )
                    added.append(dt_str)

            await update.message.reply_text(
                f"✅ Tamam, şu günler için '{title}' etkinliği eklendi:\n" +
                "\n".join(f"• {d}" for d in added)
            )

        elif action == "delete_event":
            title = parsed.get("title")
            if not title:
                await update.message.reply_text("Hangi etkinliği sileceğimi anlayamadım.")
                return
            deleted = delete_event_by_title(title)
            if deleted:
                await update.message.reply_text(
                    "🗑️ Silindi:\n" + "\n".join(f"• {d}" for d in deleted)
                )
            else:
                await update.message.reply_text(f"'{title}' bulunamadı.")

        elif action == "list_events":
            days = parsed.get("days_ahead", 7)
            cal = get_calendar()

            start_date = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
            end_date = start_date + timedelta(days=days + 1)

            events = cal.search(start=start_date, end=end_date, event=True, expand=True)

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
    persistence = PicklePersistence(filepath="bot_persistence.pkl")
    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .persistence(persistence)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    logger.info("Bot started.")
    app.run_polling()


if __name__ == "__main__":
    main()