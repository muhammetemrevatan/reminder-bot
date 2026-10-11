# Reminder Bot

A personal Telegram bot that creates, edits, lists, and deletes CalDAV calendar events from Turkish text and voice messages. It supports recurring events, advance reminders, safe deletion, and recovery after deployments.

## Features

- Create single-day, multi-day, and all-day events using natural language.
- Add descriptions and notes to events.
- Validate AI actions, dates, field types, recurrence rules, and reminder offsets before writing.
- Schedule daily, weekly, and monthly recurring events, optionally with an end date or count.
- Configure up to five reminders per event, from its start time to seven days in advance.
- Find matching events, select one, and edit its title, notes, date, repetition, or reminders.
- Require explicit confirmation before deleting a selected event or recurring series.
- Summarize missed reminders from the last 24 hours after restarting.
- Run regression tests in GitHub Actions, with an optional deployment step that requires tests to pass.
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

The bot interprets dates in `Europe/Istanbul`. New timed events include the Istanbul timezone; older records without a timezone are interpreted as Istanbul local time. Blocking AI and calendar calls run in worker threads so they do not block the bot's event loop.

## Requirements

- A Python version supported by the installed dependencies. The application uses `asyncio.to_thread`, requiring Python 3.9 or newer; type annotations in the project require Python 3.10 or newer; current package versions may require a newer version.
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
| Weekly event | `Her pazartesi saat 09:00’da spor ekle, 10 dakika önce ve başladığında hatırlat.` |
| Daily event with an end date | `Her gün saat 20:00’de kitap oku, ay sonuna kadar tekrarla.` |
| Monthly event | `Her ayın 15’inde saat 10:00’da faturaları kontrol et.` |
| Move an event | `Sinema etkinliğini yarın saat 18:00’e taşı.` |
| Rename an event | `Sinema etkinliğinin adını Film gecesi yap.` |
| Change reminders | `Sinema için 30 dakika önce bildirim gönder.` |
| Delete an event | `Sinema etkinliğini sil.` Then select the event and confirm with the button. |

Voice messages follow the same flow after transcription. Check the date and title in the confirmation because interpretation depends on AI output. Stop the process with `Ctrl+C`.

## Editing and deletion

The bot presents up to ten matching events as Telegram buttons. Select the exact event; recurring entries are marked as an entire series. Deletion then requires a separate confirmation. Edits change only the fields requested, preserve the event UID, and rebuild its pending reminders. Both text and button actions enforce the owner and chat restriction.

Selections expire after five minutes and are invalidated by a new selection or restart. If the event changed after it was shown, the bot refuses the stale change. Editing and deleting recurring events affects the whole series; editing one occurrence is not supported. Series with externally edited exception instances must be edited in a calendar client.

## Reminder reliability and deployment

- The bot plans reminders within a rolling 30-day window, refreshed every 60 seconds. Recurrences are expanded over a bounded range rather than scheduling an infinite series.
- Each reminder has an identity based on event UID, occurrence time, and notification offset. Multiple alarms and repeated occurrences are scheduled separately.
- Removed or changed events cancel or replace their pending jobs. Before sending, the event and its alarm are checked again.
- Calendar verification or delivery failures retry up to three times at 30-second intervals. Failed syncs preserve existing jobs and retry on the next cycle.
- A private, transparent calendar entry dated January 1, 2000 stores the delivery checkpoint and recent receipts. It is excluded from the bot's event selection and listings. Keep it to preserve recovery history.
- After a restart, the bot sends a summary of undelivered notifications since the last checkpoint, limited to the last 24 hours. First installation creates a checkpoint without replaying historical events.
- Sent notifications and summarized missed notifications are recorded to prevent replay after a normal restart. A failure between Telegram accepting a message and saving its receipt can still cause a duplicate. Exactly-once delivery is not guaranteed.
- No local persistence file or Dokploy volume is needed. Run one instance per token to avoid polling conflicts and duplicate reminders.
- All-day events have no Telegram reminders. Notification times already in the past are skipped when creating an event.

For Dokploy, install `requirements.txt`, use `python main.py` as the start command, and supply the environment variables above. Startup and synchronization logs include `Reminder sync: ...`.

### Test-gated deployment

The workflow in `.github/workflows/ci.yml` runs tests for pull requests and pushes to `main`. Its deployment job runs only after successful tests and forwards the push payload to Dokploy. Older runs are skipped if a newer commit is already on `main`.

To enable deployment through this workflow:

1. In GitHub, open **Settings → Secrets and variables → Actions → New repository secret**.
2. Name it `DOKPLOY_DEPLOY_URL` and set its value to the application's complete Dokploy deployment webhook URL. Do not put the URL in source code.
3. Keep Dokploy **Autodeploy enabled**: the webhook endpoint requires it. Keep the repository, `main` branch, and `On Push` source settings.
4. Disable the direct repository webhook in **Settings → Webhooks** by clearing its **Active** checkbox. Otherwise it deploys immediately on push, bypassing CI and potentially causing a second deployment.
5. Push a change and check **Actions → Test and deploy**, then confirm that Dokploy finishes building the tested revision.

Without the secret, tests still run and the deployment step reports a notice and skips sending a request. The existing direct webhook continues to work until disabled. External setup is required before CI can gate production deployments; adding a workflow alone does not change Dokploy's current trigger.

If Cloudflare Access protects the dashboard, restrict its login exception to the exact deployment webhook path. The deployment script rejects Cloudflare sign-in HTML even if the HTTP status is `200`. Update both the secret and the Access path if the webhook token is regenerated. An accepted webhook means deployment was queued, not that its build or startup succeeded.

## Project structure

```text
.
├── main.py                  # Telegram handlers, AI clients, and calendar operations
├── actions.py               # AI action validation
├── calendar_ops.py          # Event creation, recurrence, and safe changes
├── reminders.py             # Scheduling, recovery, and delivery receipts
├── requirements.txt         # Python dependencies
├── tests/                   # Regression tests and mocked integrations
├── scripts/deploy.py        # Test-gated Dokploy webhook forwarding
├── .github/workflows/ci.yml  # GitHub Actions test and deployment workflow
├── .gitignore
└── README.md
```

`main.py` is the script entry point. Its `if __name__ == "__main__":` block starts the bot.

## Known limitations

- All-day events are stored without Telegram reminders. Timed events default to one hour in duration.
- Daily, weekly, and monthly recurrence is supported; advanced recurrence patterns and single-occurrence editing are not exposed through the bot. Monthly dates that do not exist in a month are skipped according to iCalendar recurrence rules.
- AI validation checks structure and bounds but cannot guarantee that every natural-language request was interpreted correctly. Review the bot's confirmation.
- More than ten matching entries require a more specific title.
- Missed notification recovery is limited to 24 hours and requires an existing checkpoint.
- Model identifiers are hardcoded in `main.py` and may need updating if provider availability changes.

## Data handling

Text messages are sent to OpenRouter. Voice messages are downloaded to a local `.ogg` file and uploaded to Gemini; the resulting transcript is sent to OpenRouter. After successful transcription, the local file is deleted and Gemini file deletion is attempted. Errors may leave files behind.

Event titles and descriptions are stored on the CalDAV server. Delivery receipt identifiers and checkpoint timestamps are also stored in CalDAV. Temporary voice files and legacy `bot_persistence.pkl` files are not explicitly excluded by `.gitignore`; inspect untracked files before committing.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Startup error involving `TELEGRAM_CHAT_ID` | Make sure the value is present and numeric. |
| `ZoneInfoNotFoundError` | Install timezone data with `python -m pip install tzdata`, particularly on Windows. |
| No response to messages | Check the token, permitted chat ID, network access, and running process. |
| Calendar operation fails | Check the CalDAV URL, credentials, and calendar creation/write permissions. |
| AI services do not respond | Check the OpenRouter key, quota, connectivity, and model availability. |
| Voice transcription fails | Check the Gemini key, model access, and application logs. |
| Reminder sync fails | Check CalDAV connectivity, credentials, and write access for the checkpoint. Existing jobs are retained and sync retries automatically. |
| Selection expired | Send the edit/delete request again and use the new buttons. |
| CI succeeds but no deployment is requested | Configure `DOKPLOY_DEPLOY_URL`; a missing secret deliberately skips deployment. |
| CI cannot trigger deployment | Keep Dokploy Autodeploy on, check the webhook URL and Cloudflare exception, and inspect the Actions logs. |
| Webhook reports success but no deploy appears | Inspect its response body for a Cloudflare login page, then check Autodeploy and branch settings. |

## Tests

After installing the dependencies, run:

```bash
python -m unittest discover -s tests -v
```

Tests cover AI validation, recurrence expansion, advance alarms, safe selection and deletion, editing, restart recovery, delivery receipts, missed summaries, retries, owner restrictions, and deployment webhook responses. They use mocked remote services and do not send Telegram messages or modify a live calendar.

For deployment verification, create a future reminder, restart the bot before its due time, and confirm delivery. Create another reminder and delete it before its due time; confirm that no message is sent.

## License

No license file is currently included. Usage and distribution terms must be established separately.
