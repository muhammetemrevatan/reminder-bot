# Reminder Bot

A personal Telegram bot that creates, lists, and deletes CalDAV calendar events from Turkish text and voice messages. Timed events produce Telegram reminders at their start time.

## Features

- Create single-day, multi-day, and all-day events using natural language.
- Add descriptions and notes to events.
- List upcoming events and delete events by title.
- Transcribe Turkish voice messages with Gemini and interpret messages with OpenRouter.
- Restrict text, voice, and `/start` processing to one configured Telegram chat and user.
- Restore future reminders from CalDAV after a restart or deployment.
- Cancel pending reminders when events are deleted and verify events before delivery.

## How it works

1. Telegram updates are received through polling.
2. Voice messages are uploaded to Gemini and transcribed into Turkish text.
3. OpenRouter returns a JSON action. If the first model fails, the bot tries `openrouter/free`.
4. Events are stored in the first CalDAV calendar whose display name contains `reminder`. If none exists, the bot creates a calendar named `reminder`.
5. Timed events are scheduled through Telegram's `JobQueue`. CalDAV is the source of truth: reminders are restored at startup and synchronized every 60 seconds.

The bot interprets dates in `Europe/Istanbul`. New timed events are stored in UTC; older records without a timezone are interpreted as Istanbul local time. Blocking AI and calendar calls run in worker threads so they do not block the bot's event loop.

## Requirements

- A Python version supported by the installed dependencies. The application uses `asyncio.to_thread`, requiring Python 3.9 or newer; current package versions may require a newer version.
- A Telegram bot token and the numeric ID of the permitted chat.
- A reachable CalDAV server, such as Radicale, and an account with permission to create calendars and write events.
- OpenRouter and Gemini API keys. Both clients are initialized at startup, even when only text messages are used.
- Network access to Telegram, CalDAV, OpenRouter, and Gemini.

## Installation

Open a terminal in the directory containing `main.py` and `requirements.txt`.

### Windows / PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

### Linux / macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

`requirements.txt` lists the Python packages needed for CalDAV, Telegram, environment variables, iCalendar serialization, OpenRouter, and Gemini. The `python-telegram-bot[job-queue]` extra installs scheduling support. Dependency versions are not currently pinned.

## Configuration

Create a `.env` file in the project directory, replacing these placeholders with your own values. On Dokploy, set the same variables in the application's Environment settings.

```dotenv
RADICALE_URL=https://calendar.example.com
RADICALE_USER=your_calendar_username
RADICALE_PASS=your_calendar_password
TELEGRAM_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=123456789
TELEGRAM_USER_ID=123456789
OPENROUTER_API_KEY=your_openrouter_api_key
GEMINI_API_KEY=your_gemini_api_key
```

| Variable | Description |
| --- | --- |
| `RADICALE_URL` | CalDAV server URL. Set this explicitly because the code contains a personal default. |
| `RADICALE_USER` | CalDAV username. Set this explicitly because the code contains a personal default. |
| `RADICALE_PASS` | CalDAV account password. |
| `TELEGRAM_TOKEN` | Bot token obtained through Telegram's BotFather. |
| `TELEGRAM_CHAT_ID` | Numeric ID of the permitted chat. Group IDs may be negative. |
| `TELEGRAM_USER_ID` | Numeric ID of the permitted user. Defaults to `TELEGRAM_CHAT_ID` for private chats; set it explicitly when using a group. |
| `OPENROUTER_API_KEY` | API key used to interpret messages. |
| `GEMINI_API_KEY` | API key used to transcribe voice messages. |

`TELEGRAM_CHAT_ID` is converted to an integer at startup. A missing or nonnumeric value prevents startup. `.env` is excluded by `.gitignore`; never commit credentials.

For personal use, use your private chat with the bot and set both Telegram IDs to your numeric user ID. Usernames and phone numbers are not valid IDs. Both the sender and chat must match; other users are silently ignored, including when they send `/start`. If using a group, set its ID as `TELEGRAM_CHAT_ID` and your own positive user ID as `TELEGRAM_USER_ID`. An unset user ID with a group chat fails closed: no sender is authorized.

## Running and usage

With the virtual environment active, run:

```bash
python main.py
```

Send `/start` to the bot from the configured Telegram chat. The interface and language prompts are Turkish. For example:

| Purpose | Example message |
| --- | --- |
| Timed event | `Yarın saat 15:00'a sinema hatırlatıcısı oluştur.` |
| All-day event | `Yarın kitap fuarını takvime ekle.` |
| Multiple days | `24 ve 25'inde toplantı var, takvime ekle.` |
| Description | `Yarın saat 14:00'a toplantı ekle, not: sunumu hazırla.` |
| Upcoming events | `Yaklaşan etkinlikleri göster.` |
| Today's events | `Bugünkü etkinlikleri göster.` |
| Delete an event | `Sinema etkinliğini sil.` |

Voice messages follow the same flow after transcription. Check the date and title in the confirmation because interpretation depends on AI output. Stop the process with `Ctrl+C`.

## Reminder reliability and deployment

- Future, nonrecurring timed events with a `VALARM` are restored from the reminder calendar at startup. Existing bot-created events are supported; no migration is required.
- Each reminder is tracked by the event's `UID`, preventing duplicate scheduling within one bot process.
- The calendar is synchronized every 60 seconds. Removed or changed events cancel or replace their pending jobs. Events deleted through the bot cancel their jobs immediately after successful deletion.
- Before sending, the bot looks up the event again. Deleted, cancelled, or rescheduled events do not produce the old reminder.
- If calendar verification or Telegram delivery fails, the bot retries up to three times at 30-second intervals. A failed calendar synchronization preserves existing jobs and is retried on the next cycle.
- Reminder recovery uses CalDAV, so no local persistence file or Dokploy volume is needed for scheduling.
- Run only one bot instance per token. Multiple instances can conflict over Telegram polling and send duplicate reminders.
- Events whose start time passed while the bot was offline are not replayed. A Telegram timeout after a message was accepted can also cause a retry to duplicate delivery; delivery is not guaranteed exactly once.

For Dokploy, install `requirements.txt`, use `python main.py` as the start command, and supply the environment variables above. Startup and synchronization logs include `Reminder sync: ...`.

For GitHub autodeploy, enable Autodeploy, select the correct repository and branch, and use the deployment webhook URL. If the dashboard is protected by Cloudflare Access, the webhook must reach Dokploy without an interactive login. A `200` response containing a Cloudflare sign-in HTML page does not mean deployment was triggered. Any Access exception should be restricted to the exact webhook path; update it and the GitHub URL if the webhook token is regenerated.

## Project structure

```text
.
├── main.py                  # Telegram handlers, AI clients, and calendar operations
├── reminders.py             # Reminder restoration, synchronization, and delivery
├── requirements.txt         # Python dependencies
├── tests/test_reminders.py  # Reminder regression tests
├── .gitignore
└── README.md
```

`main.py` is the script entry point. Its `if __name__ == "__main__":` block starts the bot.

## Known limitations

- All-day events are stored without Telegram reminders.
- Timed events last one hour. Multi-day timed events currently use `09:00` for each day rather than preserving a requested time.
- Title-based deletion removes all matching events within one year before and after the current date, without asking for confirmation.
- The search for tomorrow's events currently includes today as well.
- Recurring events are not restored by the reminder scheduler.
- Model identifiers are hardcoded in `main.py` and may need updating if provider availability changes.

## Data handling

Text messages are sent to OpenRouter. Voice messages are downloaded to a local `.ogg` file and uploaded to Gemini; the resulting transcript is sent to OpenRouter. After successful transcription, the local file is deleted and Gemini file deletion is attempted. Errors may leave files behind.

Event titles and descriptions are stored on the CalDAV server. Temporary voice files and legacy `bot_persistence.pkl` files are not explicitly excluded by `.gitignore`; inspect untracked files before committing.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Startup error involving `TELEGRAM_CHAT_ID` | Make sure the value is present and numeric. |
| `ZoneInfoNotFoundError` | Install timezone data with `python -m pip install tzdata`, particularly on Windows. |
| No response to messages | Check the token, permitted chat ID, network access, and running process. |
| Calendar operation fails | Check the CalDAV URL, credentials, and calendar creation/write permissions. |
| AI services do not respond | Check the OpenRouter key, quota, connectivity, and model availability. |
| Voice transcription fails | Check the Gemini key, model access, and application logs. |
| Reminder sync fails | Check CalDAV connectivity and credentials. Existing jobs are retained and sync retries automatically. |
| Webhook reports success but no deploy appears | Inspect its response body for a Cloudflare login page, then check Autodeploy and branch settings. |

## Tests

After installing the dependencies, run:

```bash
python -m unittest discover -s tests -v
```

Tests cover restart recovery, duplicate prevention, deletion, rescheduling, timezone compatibility, calendar failures, retries, event-loop responsiveness, and iCalendar serialization. They use mocked remote services and do not send Telegram messages or modify a live calendar.

For deployment verification, create a future reminder, restart the bot before its due time, and confirm delivery. Create another reminder and delete it before its due time; confirm that no message is sent.

## License

No license file is currently included. Usage and distribution terms must be established separately.
