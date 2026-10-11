import os
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv
import caldav
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, JobQueue
from google import genai

load_dotenv()

RADICALE_URL = os.getenv("RADICALE_URL", "https://cal.muhammetemrevatan.com")
RADICALE_USER = os.getenv("RADICALE_USER", "vatanemre123")
RADICALE_PASS = os.getenv("RADICALE_PASS")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID"))
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
        if "reminder" in str(cal.url).lower():
            return cal
    return calendars[0] if calendars else None


def parse_with_gemini(message: str):
    prompt = f"""
    Kullanıcının mesajını analiz et ve şu JSON formatında cevap ver:
    {{
      "action": "add_event" | "list_events" | "chat",
      "title": "etkinlik başlığı (varsa)",
      "datetime": "YYYY-MM-DD HH:MM (varsa)",
      "days_ahead": 0 | 1 | 7 (list_events için),
      "reply": "kullanıcıya verilecek doğal cevap"
    }}
    
    Kullanıcının mesajı: "{message}"
    """
    
    response = gemini_client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
    )
    
    text = response.text.strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()
    
    import json
    return json.loads(text)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    chat_id = update.effective_chat.id
    
    if str(chat_id) != str(TELEGRAM_CHAT_ID):
        return
    
    try:
        parsed = parse_with_gemini(user_message)
        action = parsed.get("action")
        
        if action == "add_event":
            title = parsed.get("title", "Hatırlatıcı")
            dt_str = parsed.get("datetime")
            
            if not dt_str:
                await update.message.reply_text("Tarih/saat anlayamadım. Tekrar yazar mısın?")
                return
            
            event_date = datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
            
            cal = get_calendar()
            if not cal:
                await update.message.reply_text("Takvim bulunamadı.")
                return
            
            event_ical = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//TelegramBot//TR
BEGIN:VEVENT
SUMMARY:{title}
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
        
        elif action == "list_events":
            days = parsed.get("days_ahead", 7)
            
            cal = get_calendar()
            if not cal:
                await update.message.reply_text("Takvim bulunamadı.")
                return
            
            start_date = datetime.now()
            end_date = start_date + timedelta(days=days)
            
            events = cal.search(start=start_date, end=end_date, event=True, expand=True)
            
            if not events:
                await update.message.reply_text(f"Önümüzdeki {days} günde etkinlik yok.")
                return
            
            msg = f"Önümüzdeki {days} gün:\n\n"
            for ev in events:
                comp = ev.icalendar_component
                summary = comp.get("SUMMARY", "Başlıksız")
                dtstart = comp.get("DTSTART").dt
                if isinstance(dtstart, datetime):
                    date_str = dtstart.strftime("%d.%m.%Y %H:%M")
                else:
                    date_str = dtstart.strftime("%d.%m.%Y")
                msg += f"• {date_str} — {summary}\n"
            
            await update.message.reply_text(msg)
        
        else:
            reply = parsed.get("reply", "Anlayamadım, tekrar yazar mısın?")
            await update.message.reply_text(reply)
    
    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text(f"Bir hata oldu: {e}")


async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    title = job.data["title"]
    chat_id = job.data["chat_id"]
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"⏰ Hatırlatma: {title}",
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Merhaba! 👋\n\n"
        "Bana doğal dille yaz, anlarım:\n"
        "• 'Yarın saat 15:00'a sinema hatırlatıcısı oluştur'\n"
        "• 'Yaklaşan etkinlikleri göster'\n"
        "• 'Bu hafta neler var?'"
    )


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Bot started.")
    app.run_polling()


if __name__ == "__main__":
    main()