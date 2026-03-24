#!/usr/bin/env python3
"""
Recreation.gov Permit Availability Checker — Railway Edition
=============================================================
Monitors Ruby Horsethief Canyon (permit 74466) for campsite cancellations.

Uses Resend (https://resend.com) for email — works on Railway free tier.
SMTP is blocked by Railway; Resend sends over HTTPS instead.

Required environment variables:
  RESEND_API_KEY  — from resend.com dashboard
  EMAIL_SENDER    — must be a verified address/domain in Resend
                    (use onboarding@resend.dev to test before verifying your own)
  EMAIL_RECEIVER  — where to send the alert

Optional environment variables (defaults shown):
  PERMIT_ID        — 74466
  TARGET_DATE      — 2026-05-24
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

PERMIT_ID    = os.environ.get("PERMIT_ID", "74466")
TARGET_DATE  = os.environ.get("TARGET_DATE", "2026-05-24")

RESEND_API_KEY = os.environ["RESEND_API_KEY"]
EMAIL_SENDER   = os.environ["EMAIL_SENDER"]
EMAIL_RECEIVER = os.environ["EMAIL_RECEIVER"]

CHECK_INTERVAL   = int(os.environ.get("CHECK_INTERVAL", "120"))
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

AVAILABILITY_URL = (
    f"https://www.recreation.gov/api/permits/{PERMIT_ID}/availability/month"
    f"?start_date={MONTH_START}"
)

BOOKING_URL = (
    f"https://www.recreation.gov/permits/{PERMIT_ID}"
    f"/registration/detailed-availability?date={TARGET_DATE}"
)

REC_GOV_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": f"https://www.recreation.gov/permits/{PERMIT_ID}",
}

TARGET_DATE_KEY = TARGET_DT.strftime("%Y-%m-%dT00:00:00Z")


def check_availability() -> list[dict]:
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
            division_name = division_data.get("name", division_id)
            date_avail    = division_data.get("date_availability", {})
            slot          = date_avail.get(TARGET_DATE_KEY, {})
            remaining     = slot.get("remaining", 0)
            log.info(f"  {division_name}: remaining={remaining}")
            if remaining and remaining > 0:
                available.append({
                    "division_name": division_name,
                    "remaining": remaining,
                    "total": slot.get("total", "?"),
                    "date": TARGET_DATE,
                })

    except requests.HTTPError as e:
        log.error(f"HTTP error {e.response.status_code}: {e.response.text[:300]}")
    except Exception as e:
        log.exception(f"Error checking availability: {e}")

    return available


def send_email(available_slots: list[dict]):
    subject = f"🏕️ PERMIT AVAILABLE — Ruby Horsethief on {TARGET_DATE}"

    lines = [
        f"A permit has opened up for {TARGET_DATE} at Ruby Horsethief Canyon.",
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
    body = "\n".join(lines)

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
                "text": body,
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
    log.info(f"  Target date : {TARGET_DATE}")
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
