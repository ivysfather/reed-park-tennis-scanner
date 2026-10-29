#!/usr/bin/env python3
"""Reed Park tennis court scanner -> phone alerts (ntfy by default, or Twilio WhatsApp).

Modes:
  open   Run at ~8:14 PT. Waits for the 8:15 release of the date 3 days out,
         then sends what's open. Only for dates whose weekday is in OPEN_DAYS.
  watch  Run every ~15 min. Checks the next 3 days (WATCH_DAYS only) and
         alerts only on windows that weren't open last time (cancellations).

Flags:
  --dry-run       print the message instead of sending it
  --date YYYY-MM-DD   override the target date (open mode)

Stdlib only. Config lives in .env next to this file.
"""

import argparse
import base64
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
STATE_FILE = HERE / "state.json"
PT = ZoneInfo("America/Los_Angeles")

BASE = "https://anc.apm.activecommunities.com/santamonicarecreation"
AVAIL_URL = BASE + "/rest/reservation/resource/availability/daily/{id}"
DETAIL_URL = BASE + "/reservation/landing/search/detail/{id}"
SEARCH_URL = BASE + "/reservation/landing/search?resourceType=0&equipmentQty=0&facilityCenterIds=58"

# Ball machine courts are children of Ct #2 / #3: booking one blocks the other.
COURTS = [
    (601, "Ct #2 + ball machine", True),
    (765, "Ct #3 + ball machine", True),
    (342, "Ct #1", False),
    (343, "Ct #2", False),
    (364, "Ct #3", False),
    (365, "Ct #4", False),
    (366, "Ct #5", False),
    (367, "Ct #6", False),
]

# Status codes seen in the API (inferred, not documented).
STATUS_OPEN = 0
STATUS_FULL = 4
STATUS_PAST = 7
STATUS_NOT_YET_OPEN = 8

MIN_MINUTES = 60  # site's minimum reservation length
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def load_env():
    env_path = HERE / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def env_days(name, default):
    raw = os.environ.get(name, default)
    return {d.strip().title()[:3] for d in raw.split(",") if d.strip()}


def env_hour(name, default):
    return int(os.environ.get(name, default))


def log(msg):
    stamp = datetime.now(PT).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] {msg}", flush=True)


# ---------- ActiveNet ----------

def fetch_days(court_id, start, end):
    query = urllib.parse.urlencode({
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "customer_id": 0,
        "company_id": 0,
        "locale": "en-US",
    })
    req = urllib.request.Request(
        AVAIL_URL.format(id=court_id) + "?" + query,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.load(resp)
    return {d["date"]: d for d in data["body"]["details"]["daily_details"]}


def minutes(hhmmss):
    h, m, _ = hhmmss.split(":")
    return int(h) * 60 + int(m)


def windows_for(day, earliest_hour, latest_hour):
    """Open windows (start_min, end_min) of at least MIN_MINUTES, clipped to preferred hours."""
    if day["status"] != STATUS_OPEN:
        return []
    out = []
    for t in day["times"]:
        if not t.get("available"):
            continue
        start = max(minutes(t["start_time"]), earliest_hour * 60)
        end = min(minutes(t["end_time"]), latest_hour * 60)
        if end - start >= MIN_MINUTES:
            out.append((start, end))
    return out


def is_evening_day(d):
    """Days in EVENING_BM_DAYS only look for ball machine courts after EVENING_BM_HOUR."""
    return DAY_NAMES[d.weekday()] in env_days("EVENING_BM_DAYS", "")


def scan(dates, earliest_hour, latest_hour):
    """Returns {date_str: {"status": {court_id: status}, "open": {court_id: [windows]}}}."""
    start, end = min(dates), max(dates)
    result = {d.isoformat(): {"status": {}, "open": {}} for d in dates}
    evening_hour = env_hour("EVENING_BM_HOUR", 17)
    for court_id, _, ball_machine in COURTS:
        days = fetch_days(court_id, start, end)
        for d in dates:
            key = d.isoformat()
            day = days.get(key)
            if not day:
                continue
            result[key]["status"][court_id] = day["status"]
            day_earliest = earliest_hour
            if is_evening_day(d):
                if not ball_machine:
                    continue
                day_earliest = max(earliest_hour, evening_hour)
            wins = windows_for(day, day_earliest, latest_hour)
            if wins:
                result[key]["open"][court_id] = wins
    return result


# ---------- Formatting ----------

def fmt_time(mins):
    h, m = divmod(mins, 60)
    suffix = "am" if h < 12 else "pm"
    h12 = h % 12 or 12
    return f"{h12}{suffix}" if m == 0 else f"{h12}:{m:02d}{suffix}"


def fmt_windows(wins):
    return ", ".join(f"{fmt_time(s)}–{fmt_time(e)}" for s, e in wins)


def fmt_date(d):
    return d.strftime("%a %b %-d")


def build_message(day_results):
    """Returns (body, links). Links: ball machine courts first, then the all-courts search."""
    with_urls = os.environ.get("NOTIFY", "ntfy").lower() == "whatsapp"
    names = {cid: (name, bm) for cid, name, bm in COURTS}
    lines, links = [], []
    for key, info in day_results.items():
        d = date.fromisoformat(key)
        open_courts = info["open"]
        if lines:
            lines.append("")
        lines.append(fmt_date(d))
        if not open_courts:
            lines.append("Nothing open.")
            continue
        bm = [(cid, w) for cid, w in open_courts.items() if names[cid][1]]
        regular = [(cid, w) for cid, w in open_courts.items() if not names[cid][1]]
        if bm:
            lines.append("Ball machine:")
            for cid, w in bm:
                lines.append(f"• {names[cid][0]}: {fmt_windows(w)}")
                if with_urls:
                    lines.append(f"  {DETAIL_URL.format(id=cid)}")
                label = names[cid][0].replace(" + ball machine", " BM")
                if (label, DETAIL_URL.format(id=cid)) not in links:
                    links.append((label, DETAIL_URL.format(id=cid)))
        else:
            lines.append("Ball machine: none open")
        if regular:
            lines.append("Courts:")
            for cid, w in regular:
                lines.append(f"• {names[cid][0]}: {fmt_windows(w)}")
    if with_urls:
        lines.append("")
        lines.append(f"All courts: {SEARCH_URL}")
    lines.append("60–120 min bookings. Sign in first.")
    links = links[:2] + [("All courts", SEARCH_URL)]
    return "\n".join(lines), links


# ---------- Notifications ----------

def notify(title, body, links, dry_run):
    """links: [(label, url)] — first is the tap target, up to 3 become buttons (ntfy)."""
    if dry_run:
        print(f"----- DRY RUN: {title} -----")
        print(body)
        print("----------------------------")
        return
    if os.environ.get("NOTIFY", "ntfy").lower() == "whatsapp":
        send_whatsapp(f"🎾 {title}\n{body}")
    else:
        send_ntfy(title, body, links)


def send_ntfy(title, body, links):
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    topic = os.environ["NTFY_TOPIC"]
    headers = {"Title": title, "Priority": "high", "Tags": "tennis"}
    if links:
        headers["Click"] = links[0][1]
        headers["Actions"] = "; ".join(f"view, {label}, {url}" for label, url in links[:3])
    req = urllib.request.Request(f"{server}/{topic}", data=body.encode("utf-8"), headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        msg = json.load(resp)
    log(f"ntfy sent (id {msg.get('id')})")


def send_whatsapp(body):
    sid = os.environ["TWILIO_ACCOUNT_SID"]
    token = os.environ["TWILIO_AUTH_TOKEN"]
    from_ = os.environ.get("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")
    to = os.environ["WHATSAPP_TO"]
    if not to.startswith("whatsapp:"):
        to = "whatsapp:" + to
    if not from_.startswith("whatsapp:"):
        from_ = "whatsapp:" + from_
    payload = urllib.parse.urlencode({"From": from_, "To": to, "Body": body[:1600]}).encode()
    req = urllib.request.Request(
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
        data=payload,
        headers={"Authorization": "Basic " + base64.b64encode(f"{sid}:{token}".encode()).decode()},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        msg = json.load(resp)
    log(f"WhatsApp sent (sid {msg.get('sid')}, status {msg.get('status')})")


# ---------- Modes ----------

def run_open(args):
    # Schedulers start this early (GitHub cron can run late) or hourly (launchd on a
    # non-Pacific Mac). Act only between 7:20 and 8:30 PT; wait for the release.
    now = datetime.now(PT)
    window_start = now.replace(hour=7, minute=20, second=0, microsecond=0)
    release = now.replace(hour=8, minute=14, second=50, microsecond=0)
    if not args.date and not args.dry_run:
        if not (window_start <= now < now.replace(hour=8, minute=30, second=0)):
            log("Outside the 8:15 release window; skipping.")
            return
        if now < release:
            log(f"Waiting {int((release - now).total_seconds())}s for the 8:15 PT release.")
            time.sleep((release - now).total_seconds())
    today = datetime.now(PT).date()
    target = date.fromisoformat(args.date) if args.date else today + timedelta(days=3)
    open_days = env_days("OPEN_DAYS", "Mon")
    if not args.date and DAY_NAMES[target.weekday()] not in open_days:
        log("Target date not in OPEN_DAYS; skipping.")
        return
    earliest, latest = env_hour("EARLIEST_HOUR", 6), env_hour("LATEST_HOUR", 22)

    # Poll until the date flips from "not yet open" (or give up after ~10 min).
    deadline = time.time() + 600
    while True:
        result = scan([target], earliest, latest)
        statuses = set(result[target.isoformat()]["status"].values())
        if STATUS_NOT_YET_OPEN not in statuses or time.time() > deadline:
            break
        time.sleep(2)

    if STATUS_NOT_YET_OPEN in statuses:
        log("Still not open after waiting; sending nothing.")
        return
    title = f"Reed Park opened for {fmt_date(target)}"
    if is_evening_day(target):
        # Evening-only days alert only when something matches; no daily "nothing open".
        if not result[target.isoformat()]["open"]:
            log("No ball machine courts open after the evening cutoff; sending nothing.")
            save_state_for(result)
            return
        title += f" (ball machine after {fmt_time(env_hour('EVENING_BM_HOUR', 17) * 60)})"
    body, links = build_message(result)
    notify(title, body, links, args.dry_run)
    save_state_for(result)


def run_watch(args):
    now = datetime.now(PT)
    start_h, end_h = env_hour("WATCH_START_HOUR", 7), env_hour("WATCH_END_HOUR", 21)
    if not args.dry_run and not (start_h <= now.hour < end_h):
        log("Outside watch hours; skipping.")
        return
    watch_days = env_days("WATCH_DAYS", "Mon")
    dates = [now.date() + timedelta(days=i) for i in range(1, 4)]
    dates = [d for d in dates if DAY_NAMES[d.weekday()] in watch_days]
    if not dates:
        log("No upcoming dates in WATCH_DAYS; skipping.")
        return
    earliest, latest = env_hour("EARLIEST_HOUR", 6), env_hour("LATEST_HOUR", 22)
    result = scan(dates, earliest, latest)

    state = load_state()
    new = {}
    for key, info in result.items():
        seen = {tuple(x) for x in state.get(key, [])}
        fresh = {}
        for cid, wins in info["open"].items():
            wins_new = [w for w in wins if (cid, w[0], w[1]) not in seen]
            if wins_new:
                fresh[cid] = wins_new
        if fresh:
            new[key] = {"status": info["status"], "open": fresh}

    save_state_for(result)
    if not new:
        log("No new openings.")
        return
    body, links = build_message(new)
    notify("New Reed Park openings", body, links, args.dry_run)


# ---------- State ----------

def load_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state_for(result):
    state = load_state()
    today = datetime.now(PT).date().isoformat()
    state = {k: v for k, v in state.items() if k >= today}
    for key, info in result.items():
        state[key] = [[cid, s, e] for cid, wins in info["open"].items() for s, e in wins]
    STATE_FILE.write_text(json.dumps(state, indent=1))


def main():
    load_env()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", choices=["open", "watch"])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--date")
    args = parser.parse_args()
    try:
        run_open(args) if args.mode == "open" else run_watch(args)
    except Exception as exc:  # keep launchd logs readable
        log(f"ERROR: {exc!r}")
        sys.exit(1)


if __name__ == "__main__":
    main()
