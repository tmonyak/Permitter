#!/usr/bin/env python3
"""
Recreation.gov Permit Availability Checker — Railway Edition
=============================================================
Monitors Ruby Horsethief Canyon (permit 74466) for campsite cancellations.
Uses the campground availability API: /api/camps/availability/campground/{id}/month

Required environment variables:
  EMAIL_SENDER    — Gmail address sending the alert
  EMAIL_PASSWORD  — Gmail App Password (16 chars, no spaces)
  EMAIL_RECEIVER  — Where to send the alert

Optional environment variables (defaults shown):
  PERMIT_ID        — 74466
  TARGET_DATE      — 2026-05-21
  CHECK_INTERVAL   — 300  (seconds)
  STOP_AFTER_FOUND — false
"""

import os
import requests
import smtplib
import time
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

# ─────────────────────────────────────────────
#  CONFIG — read from environment variables
# ─────────────────────────────────────────────

PERMIT_ID    = os.environ.get("PERMIT_ID", "74466")
TARGET_DATE  = os.environ.get("TARGET_DATE", "2026-05-24")   # YYYY-MM-DD

EMAIL_SENDER   = os.environ["EMAIL_SENDER"]
EMAIL_PASSWORD = os.environ["EMAIL_PASSWORD"]
EMAIL_RECEIVER = os.environ["EMAIL_RECEIVER"]
SMTP_HOST      = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT      = int(os.environ.get("SMTP_PORT", "587"))

CHECK_INTERVAL   = int(os.environ.get("CHECK_INTERVAL", "300"))
STOP_AFTER_FOUND = os.environ.get("STOP_AFTER_FOUND", "false").lower() == "true"

# ─────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger(__name__)

# Ruby Horsethief uses the campground API, not the permit/inyo API.
# The endpoint returns one month of per-campsite availability.
TARGET_DT   = datetime.strptime(TARGET_DATE, "%Y-%m-%d")
MONTH_START = TARGET_DT.replace(day=1).strftime("%Y-%m-%dT00:00:00.000Z")

AVAILABILITY_URL = (
    f"https://www.recreation.gov/api/camps/availability/campground"
    f"/{PERMIT_ID}/month?start_date={MONTH_START}"
)

BOOKING_URL = (
    f"https://www.recreation.gov/permits/{PERMIT_ID}"
    f"/registration/detailed-availability?date={TARGET_DATE}"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": f"https://www.recreation.gov/permits/{PERMIT_ID}",
}

# The API keys availability by this ISO format
TARGET_DATE_KEY = TARGET_DT.strftime("%Y-%m-%dT00:00:00Z")


def check_availability() -> list[dict]:
    """
    Query the campground availability API and return available campsites on TARGET_DATE.

    Response shape:
    {
      "campsites": {
        "<campsite_id>": {
          "site": "Beavertail 1",
          "availabilities": {
            "2026-05-24T00:00:00Z": "Available"   // or "Reserved", "Not Available"
          }
        }
      }
    }
    """
    available = []
    try:
        r = requests.get(AVAILABILITY_URL, headers=HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()

        campsites = data.get("campsites", {})
        log.debug(f"Got {len(campsites)} campsites from API")

        for site_id, site_data in campsites.items():
            availabilities = site_data.get("availabilities", {})
            status = availabilities.get(TARGET_DATE_KEY, "")
            site_name = site_data.get("site", site_id)

            log.debug(f"  {site_name}: {status!r}")

            if status == "Available":
                available.append({
                    "site_id": site_id,
                    "site_name": site_name,
                    "date": TARGET_DATE,
                })

    except requests.HTTPError as e:
        log.error(f"HTTP error {e.response.status_code}: {e}")
    except Exception as e:
        log.error(f"Error checking availability: {e}")

    return available


def send_email(available_sites: list[dict]):
    subject = f"🏕️ CAMPSITE AVAILABLE — Ruby Horsethief on {TARGET_DATE}"

    lines = [
        f"Good news! A campsite has opened up for {TARGET_DATE} at Ruby Horsethief Canyon.",
        "",
        "Available site(s):",
    ]
    for s in available_sites:
        lines.append(f"  • {s['site_name']}")

    lines += [
        "",
        "Book NOW before it's gone:",
        BOOKING_URL,
        "",
        f"(Checked at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC)",
    ]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = EMAIL_SENDER
    msg["To"] = EMAIL_RECEIVER
    msg.attach(MIMEText("\n".join(lines), "plain"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, msg.as_string())
        log.info(f"✅ Alert email sent to {EMAIL_RECEIVER}")
    except Exception as e:
        log.error(f"Failed to send email: {e}")


def run():
    log.info("=" * 55)
    log.info("Ruby Horsethief Permit Checker started")
    log.info(f"  Permit ID   : {PERMIT_ID}")
    log.info(f"  Target date : {TARGET_DATE}")
    log.info(f"  API URL     : {AVAILABILITY_URL}")
    log.info(f"  Check every : {CHECK_INTERVAL}s ({CHECK_INTERVAL // 60} min)")
    log.info(f"  Alert to    : {EMAIL_RECEIVER}")
    log.info("=" * 55)

    check_count = 0
    while True:
        check_count += 1
        log.info(f"Check #{check_count} — querying availability...")

        available = check_availability()

        if available:
            log.info(f"🎉 AVAILABILITY FOUND! {len(available)} site(s):")
            for s in available:
                log.info(f"   {s['site_name']}")
            send_email(available)
            if STOP_AFTER_FOUND:
                log.info("STOP_AFTER_FOUND=true — exiting.")
                break
        else:
            log.info(f"No availability on {TARGET_DATE}. Next check in {CHECK_INTERVAL}s.")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    run()
