#!/usr/bin/env python3
"""
Recreation.gov Permit Availability Checker — Railway Edition
=============================================================
Monitors Ruby Horsethief Canyon (permit 74466) for campsite cancellations.

Uses the standard permit availability endpoint:
  /api/permits/{id}/availability/month?start_date=YYYY-MM-01T00:00:00.000Z

Response shape:
{
  "payload": {
    "<division_id>": {
      "date_availability": {
        "2026-05-24T00:00:00Z": {
          "remaining": 2,
          "total": 4,
          "is_walkup": false,
          ...
        }
      },
      "name": "Beavertail 1"
    }
  }
}

Required environment variables:
  EMAIL_SENDER    — Gmail address sending the alert
  EMAIL_PASSWORD  — Gmail App Password (16 chars, no spaces)
  EMAIL_RECEIVER  — Where to send the alert

Optional environment variables (defaults shown):
  PERMIT_ID        — 74466
  TARGET_DATE      — 2026-05-24
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
TARGET_DATE  = os.environ.get("TARGET_DATE", "2026-05-21")   # YYYY-MM-DD

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

TARGET_DT   = datetime.strptime(TARGET_DATE, "%Y-%m-%d")
MONTH_START = TARGET_DT.replace(day=1).strftime("%Y-%m-%dT00:00:00.000Z")

# Correct endpoint for permit-type facilities (not the campground or inyo endpoint)
AVAILABILITY_URL = (
    f"https://www.recreation.gov/api/permits/{PERMIT_ID}/availability/month"
    f"?start_date={MONTH_START}"
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
    Query the permit availability API and return available permit slots on TARGET_DATE.

    Response shape:
    {
      "payload": {
        "<division_id>": {
          "name": "Beavertail 1",
          "date_availability": {
            "2026-05-24T00:00:00Z": {
              "remaining": 2,
              "total": 4,
              "is_walkup": false
            }
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

        payload = data.get("payload", {})
        log.debug(f"Got {len(payload)} divisions from API")

        for division_id, division_data in payload.items():
            division_name = division_data.get("name", division_id)
            date_avail = division_data.get("date_availability", {})
            slot = date_avail.get(TARGET_DATE_KEY, {})
            remaining = slot.get("remaining", 0)

            log.debug(f"  {division_name}: remaining={remaining}")

            if remaining and remaining > 0:
                available.append({
                    "division_id": division_id,
                    "division_name": division_name,
                    "remaining": remaining,
                    "total": slot.get("total", "?"),
                    "date": TARGET_DATE,
                })

    except requests.HTTPError as e:
        log.error(f"HTTP error {e.response.status_code}: {e}")
    except Exception as e:
        log.error(f"Error checking availability: {e}")

    return available


def send_email(available_slots: list[dict]):
    subject = f"🏕️ PERMIT AVAILABLE — Ruby Horsethief on {TARGET_DATE}"

    lines = [
        f"Good news! A permit has opened up for {TARGET_DATE} at Ruby Horsethief Canyon.",
        "",
        "Available slots:",
    ]
    for s in available_slots:
        lines.append(f"  • {s['division_name']}: {s['remaining']} of {s['total']} remaining")

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
    log.info(f"  Date key    : {TARGET_DATE_KEY}")
    log.info(f"  Check every : {CHECK_INTERVAL}s ({CHECK_INTERVAL // 60} min)")
    log.info(f"  Alert to    : {EMAIL_RECEIVER}")
    log.info("=" * 55)

    check_count = 0
    while True:
        check_count += 1
        log.info(f"Check #{check_count} — querying availability...")

        available = check_availability()

        if available:
            log.info(f"🎉 AVAILABILITY FOUND! {len(available)} slot(s):")
            for s in available:
                log.info(f"   {s['division_name']} — {s['remaining']} remaining")
            send_email(available)
            if STOP_AFTER_FOUND:
                log.info("STOP_AFTER_FOUND=true — exiting.")
                break
        else:
            log.info(f"No availability on {TARGET_DATE}. Next check in {CHECK_INTERVAL}s.")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    run()
