#!/usr/bin/env python3
"""
Recreation.gov Permit Availability Checker — Railway Edition
=============================================================
Monitors Ruby Horsethief Canyon (permit 74466) for campsite cancellations
across multiple dates and site lists.

Uses Resend (https://resend.com) for email — works on Railway free tier.

Required environment variables:
  RESEND_API_KEY   — from resend.com dashboard
  EMAIL_SENDER     — verified sender in Resend (or onboarding@resend.dev for testing)
  EMAIL_RECEIVER   — where to send the alert

Optional environment variables (defaults shown):
  PERMIT_ID        — 74466
  CHECK_INTERVAL   — 300  (seconds)
  STOP_AFTER_FOUND — false
"""

import os
import requests
import time
import logging
from datetime import datetime

# ─────────────────────────────────────────────
#  CONFIG — read from environment variables
# ─────────────────────────────────────────────

PERMIT_ID = os.environ.get("PERMIT_ID", "74466")

RESEND_API_KEY = os.environ["RESEND_API_KEY"]
EMAIL_SENDER   = os.environ["EMAIL_SENDER"]
EMAIL_RECEIVER = os.environ["EMAIL_RECEIVER"]

CHECK_INTERVAL   = int(os.environ.get("CHECK_INTERVAL", "300"))
STOP_AFTER_FOUND = os.environ.get("STOP_AFTER_FOUND", "false").lower() == "true"

# Map of date -> set of site names to watch for cancellations
WATCH = {
    "2026-05-24": {
        "Knowles 1",
        "May Flats",
        "Black Rocks 1",
        "Black Rocks 2",
        "Black Rocks 3",
        "Black Rocks 4",
        "Black Rocks 5",
        "Black Rocks 6",
        "Black Rocks 7",
        "Black Rocks 8",
        "Black Rocks 9",
        "Dog Island",
    },
    "2026-05-23": {
        "Cottonwood 5",
        "Mee Canyon",
        "Mee Corner",
    },
}

# ─────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger(__name__)

CONTENT_URL = f"https://www.recreation.gov/api/permitcontent/{PERMIT_ID}"

REC_GOV_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": f"https://www.recreation.gov/permits/{PERMIT_ID}",
}

# Both dates are in the same month, so one API call covers both
MONTH_START = "2026-05-01T00:00:00.000Z"
AVAILABILITY_URL = (
    f"https://www.recreation.gov/api/permits/{PERMIT_ID}/availability/month"
    f"?start_date={MONTH_START}"
)


def fetch_division_names() -> dict:
    """
    Fetch human-readable names for each division ID from the permitcontent endpoint.
    Returns: {"74466000": "Beavertail 1", "74466001": "Black Rocks 1", ...}
    """
    try:
        r = requests.get(CONTENT_URL, headers=REC_GOV_HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()
        divisions = data.get("payload", {}).get("divisions", {})
        names = {str(div_id): div_data.get("name", str(div_id))
                 for div_id, div_data in divisions.items()
                 if isinstance(div_data, dict)}
        log.info(f"Loaded {len(names)} division name(s)")
        return names
    except Exception as e:
        log.warning(f"Could not fetch division names, will use IDs instead: {e}")
        return {}


def check_availability(division_names: dict) -> list[dict]:
    """
    Check availability for all watched dates/sites in a single API call.
    Returns a list of dicts for any available watched slots.
    """
    available = []
    try:
        r = requests.get(AVAILABILITY_URL, headers=REC_GOV_HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()

        payload   = data.get("payload", {})
        divisions = payload.get("availability", {})
        log.info(f"Found {len(divisions)} division(s) in availability block")

        for division_id, division_data in divisions.items():
            if not isinstance(division_data, dict):
                continue
            division_name = division_names.get(str(division_id), str(division_id))
            date_avail    = division_data.get("date_availability", {})

            # Check each watched date
            for target_date, watched_sites in WATCH.items():
                if division_name not in watched_sites:
                    continue
                date_key  = datetime.strptime(target_date, "%Y-%m-%d").strftime("%Y-%m-%dT00:00:00Z")
                slot      = date_avail.get(date_key, {})
                remaining = slot.get("remaining", 0)
                log.info(f"  [{target_date}] {division_name}: remaining={remaining}")
                if remaining and remaining > 0:
                    available.append({
                        "date": target_date,
                        "division_name": division_name,
                        "remaining": remaining,
                        "total": slot.get("total", "?"),
                    })

    except requests.HTTPError as e:
        log.error(f"HTTP error {e.response.status_code}: {e.response.text[:300]}")
    except Exception as e:
        log.exception(f"Error checking availability: {e}")

    return available


def send_email(available_slots: list[dict]):
    # Group by date for a clean email
    by_date: dict[str, list] = {}
    for s in available_slots:
        by_date.setdefault(s["date"], []).append(s)

    subject = f"🏕️ PERMIT AVAILABLE — Ruby Horsethief ({', '.join(sorted(by_date))})"

    lines = ["Permit cancellation(s) found at Ruby Horsethief Canyon!", ""]
    for date in sorted(by_date):
        lines.append(f"📅 {date}:")
        for s in by_date[date]:
            lines.append(f"  • {s['division_name']}: {s['remaining']} of {s['total']} remaining")
        booking_url = (
            f"https://www.recreation.gov/permits/{PERMIT_ID}"
            f"/registration/detailed-availability?date={date}"
        )
        lines.append(f"  Book: {booking_url}")
        lines.append("")

    lines.append(f"(Checked at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC)")

    try:
        r = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "from": EMAIL_SENDER,
                "to": [EMAIL_RECEIVER],
                "subject": subject,
                "text": "\n".join(lines),
            },
            timeout=15,
        )
        r.raise_for_status()
        log.info(f"✅ Alert email sent to {EMAIL_RECEIVER} (id: {r.json().get('id')})")
    except Exception as e:
        log.exception(f"Failed to send email: {e}")


def run():
    log.info("=" * 55)
    log.info("Ruby Horsethief Permit Checker started")
    log.info(f"  Permit ID   : {PERMIT_ID}")
    for date, sites in sorted(WATCH.items()):
        log.info(f"  Watching {date}: {', '.join(sorted(sites))}")
    log.info(f"  Check every : {CHECK_INTERVAL}s ({CHECK_INTERVAL // 60} min)")
    log.info(f"  Alert to    : {EMAIL_RECEIVER}")
    log.info("=" * 55)

    division_names = fetch_division_names()

    check_count = 0
    while True:
        check_count += 1
        log.info(f"Check #{check_count} — querying availability...")

        available = check_availability(division_names)

        if available:
            log.info(f"🎉 AVAILABILITY FOUND! {len(available)} slot(s):")
            for s in available:
                log.info(f"   [{s['date']}] {s['division_name']} — {s['remaining']} remaining")
            send_email(available)
            if STOP_AFTER_FOUND:
                log.info("STOP_AFTER_FOUND=true — exiting.")
                break
        else:
            log.info(f"No availability found. Next check in {CHECK_INTERVAL}s.")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    run()
