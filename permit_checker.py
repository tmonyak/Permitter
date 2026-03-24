#!/usr/bin/env python3
"""
Recreation.gov Permit Availability Checker — Railway Edition
=============================================================
All secrets are read from environment variables (set in Railway dashboard).

Required environment variables:
  EMAIL_SENDER    — Gmail address sending the alert
  EMAIL_PASSWORD  — Gmail App Password (16 chars, no spaces)
  EMAIL_RECEIVER  — Where to send the alert

Optional environment variables (defaults shown):
  PERMIT_ID       — 74466
  TARGET_DATE     — 2026-05-24
  CHECK_INTERVAL  — 300  (seconds)
  STOP_AFTER_FOUND— false
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
TARGET_DATE  = os.environ.get("TARGET_DATE", "2026-05-24")

EMAIL_SENDER   = os.environ["EMAIL_SENDER"]    # Required — will crash loudly if missing
EMAIL_PASSWORD = os.environ["EMAIL_PASSWORD"]  # Required
EMAIL_RECEIVER = os.environ["EMAIL_RECEIVER"]  # Required
SMTP_HOST      = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT      = int(os.environ.get("SMTP_PORT", "587"))

CHECK_INTERVAL   = int(os.environ.get("CHECK_INTERVAL", "300"))
STOP_AFTER_FOUND = os.environ.get("STOP_AFTER_FOUND", "false").lower() == "true"

# ─────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler()],  # Railway captures stdout/stderr as logs
)
log = logging.getLogger(__name__)

AVAILABILITY_URL = (
    f"https://www.recreation.gov/api/permitinyo/{PERMIT_ID}/availability"
    f"?start_date={TARGET_DATE}T00:00:00.000Z"
    f"&end_date={TARGET_DATE}T00:00:00.000Z"
    f"&commercial_acct=false"
)

AVAILABILITY_URL_V2 = (
    f"https://www.recreation.gov/api/permits/{PERMIT_ID}/divisions/availability"
    f"?start_date={TARGET_DATE}&end_date={TARGET_DATE}"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": f"https://www.recreation.gov/permits/{PERMIT_ID}/registration/detailed-availability",
}


def check_availability() -> list[dict]:
    available = []

    # ── Try the Inyo-style endpoint first ──
    try:
        r = requests.get(AVAILABILITY_URL, headers=HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()

        payload = data.get("payload", data)
        avail_block = payload.get("availability", {})

        for division_id, div_data in avail_block.items():
            date_avail = div_data.get("date_availability", {})
            for date_key, slot in date_avail.items():
                if date_key.startswith(TARGET_DATE):
                    remaining = slot.get("remaining", 0)
                    if remaining and remaining > 0:
                        available.append({
                            "date": TARGET_DATE,
                            "division": div_data.get("name", division_id),
                            "remaining": remaining,
                        })
        if avail_block:
            return available
    except Exception as e:
        log.debug(f"Primary endpoint error: {e}")

    # ── Fallback endpoint ──
    try:
        r = requests.get(AVAILABILITY_URL_V2, headers=HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()

        def find_available(obj, path=""):
            found = []
            if isinstance(obj, dict):
                remaining = obj.get("remaining", obj.get("available", 0))
                if isinstance(remaining, (int, float)) and remaining > 0:
                    found.append({
                        "date": TARGET_DATE,
                        "division": obj.get("name", obj.get("division_name", path)),
                        "remaining": remaining,
                    })
                for k, v in obj.items():
                    found.extend(find_available(v, k))
            elif isinstance(obj, list):
                for item in obj:
                    found.extend(find_available(item, path))
            return found

        available = find_available(data)
    except Exception as e:
        log.debug(f"Fallback endpoint error: {e}")

    return available


def send_email(available_slots: list[dict]):
    subject = f"🏕️ PERMIT AVAILABLE — Recreation.gov #{PERMIT_ID} on {TARGET_DATE}"

    lines = [
        f"Good news! A permit has opened up for {TARGET_DATE}.",
        "",
        "Available slots:",
    ]
    for slot in available_slots:
        lines.append(f"  • {slot['division']}: {slot['remaining']} remaining")

    lines += [
        "",
        "Book NOW before it's gone:",
        f"https://www.recreation.gov/permits/{PERMIT_ID}/registration/detailed-availability?date={TARGET_DATE}",
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
    log.info("Permit Checker started")
    log.info(f"  Permit ID   : {PERMIT_ID}")
    log.info(f"  Target date : {TARGET_DATE}")
    log.info(f"  Check every : {CHECK_INTERVAL}s ({CHECK_INTERVAL // 60} min)")
    log.info(f"  Alert to    : {EMAIL_RECEIVER}")
    log.info("=" * 55)

    check_count = 0
    while True:
        check_count += 1
        log.info(f"Check #{check_count} — querying availability...")

        try:
            available = check_availability()
        except Exception as e:
            log.warning(f"Unexpected error during check: {e}")
            available = []

        if available:
            log.info(f"🎉 AVAILABILITY FOUND! {len(available)} slot(s):")
            for slot in available:
                log.info(f"   {slot['division']} — {slot['remaining']} remaining")
            send_email(available)
            if STOP_AFTER_FOUND:
                log.info("STOP_AFTER_FOUND=true — exiting.")
                break
        else:
            log.info(f"No availability. Next check in {CHECK_INTERVAL}s.")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    run()
