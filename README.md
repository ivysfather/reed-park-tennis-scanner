# Reed Park tennis scanner

Sends a push notification (ntfy) when Reed Park courts open up. Twilio WhatsApp is still supported (`NOTIFY=whatsapp`). It doesn't book anything.

- **8:15 release alert** (`open`): every day at 8:14 PT it waits for the release of the date 3 days out, then sends what's open. It only alerts for weekdays in `OPEN_DAYS`. The default is `Mon`, so it fires on Fridays.
- **Cancellation watcher** (`watch`): every 15 min, 7am–9pm PT, it checks the next 3 days in `WATCH_DAYS` and alerts only on newly opened windows.

Each push has buttons that open the ball machine courts' booking pages and the all-courts search.

Data comes from the city's public ActiveNet feed. No login needed to check.

## Where it runs
**GitHub Actions** (no computer needs to be on):
- `.github/workflows/release.yml` starts ~7:25 PT daily (GitHub cron often runs late) and waits for the 8:15 release.
- `.github/workflows/watch.yml` runs every 15 min for cancellations. It remembers what it already sent using the Actions cache.
- Config lives in repo settings, not the code:
  - Secret `NTFY_TOPIC` (required)
  - Variables `OPEN_DAYS`, `WATCH_DAYS` (optional, default `Mon`)
  - Variables `EVENING_BM_DAYS`, `EVENING_BM_HOUR` (optional): days that only look for ball machine courts from that hour on (e.g. `Tue,Wed,Thu,Fri` and `17`). Those days alert only when something matches.
  - Variable `EVENING_ANY_DAYS` (optional): days that look for any court, not only ball machine, from `EVENING_BM_HOUR` on (e.g. `Wed,Fri`).
- To test, run either workflow from the Actions tab with **Run workflow**.

**Or locally on a Mac:** copy `.env.example` to `.env`, then `./install.sh` (launchd). The Mac must be awake at 8:14 PT. Don't run both, or you'll get double alerts.

## Get the alerts
In the ntfy app, tap **+** and subscribe to your `NTFY_TOPIC` (server: ntfy.sh).

**Privacy:** anyone who knows the topic name can read or post to it. The topic is random. Don't share it.

## Testing without sending
```bash
python3 scanner.py open --date 2026-10-26 --dry-run
python3 scanner.py watch --dry-run
```

## Things to know
- **If using WhatsApp:** WhatsApp only delivers within 24 hours of your last message to the Twilio number, and the sandbox join expires after a few days. ntfy has neither limit.
- **Ball machine courts share a court.** "Ct #2 + ball machine" is Ct #2. Booking either one blocks the other.
- **Booking rules:** 60–120 min per booking, at most 3 days ahead, releases at 8:15am. Sign in to ActiveNet on your phone ahead of time so the link goes straight to booking.
- **Status codes** in the feed are inferred: 0 open, 4 full, 7 past, 8 not open yet.

## Court IDs
| ID | Court |
|---|---|
| 601 | Ct #2 + ball machine |
| 765 | Ct #3 + ball machine |
| 342–343 | Ct #1, #2 |
| 364–367 | Ct #3–#6 |

Logs: `logs/open.log`, `logs/watch.log`.
