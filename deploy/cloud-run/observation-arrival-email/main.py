import hashlib
import json
import os
import smtplib
import ssl
from contextlib import suppress
from datetime import datetime, timezone
from email.message import EmailMessage
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import functions_framework


def utc(value):
    stamp = datetime.fromisoformat(value)
    if stamp.utcoffset() is None:
        raise ValueError("timestamp requires a UTC offset")
    return stamp.astimezone(timezone.utc)


def visibility_link(station, observed_at):
    frontend = os.environ["WAIMEA_FRONTEND_URL"].rstrip("/")
    api = os.environ["WAIMEA_API_V1_URL"].rstrip("/")
    link = f"{frontend}/?{urlencode({'observation': observed_at})}"
    parts = urlsplit(frontend)
    origin = f"{parts.scheme}://{parts.netloc}"
    params = urlencode({"layer": "bronze", "start": observed_at, "end": observed_at, "limit": 1})
    headers = {"Origin": origin, "Cache-Control": "no-cache"}
    with urlopen(Request(f"{api}/observations?{params}", headers=headers), timeout=6) as response:
        if response.headers.get("Access-Control-Allow-Origin") not in (origin, "*"):
            raise ValueError("frontend origin rejected")
        rows = json.load(response)["data"]
    if not any(row["station_id"] == station and utc(row["observed_at"]) == utc(observed_at)
               and row.get("raw_line") for row in rows):
        raise ValueError("Bronze observation is not visible")
    with urlopen(Request(link, headers={"Cache-Control": "no-cache"}), timeout=6) as response:
        if b'name="waimea-observation-link" content="bronze-v1"' not in response.read(128_000):
            raise ValueError("frontend does not support observation links")
    return link


def send_email(station, observed, received, link):
    sender = os.environ["SMTP_USER"]
    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "587"))
    event_key = hashlib.sha256(f"{station}/{observed.isoformat()}".encode()).hexdigest()
    message = EmailMessage()
    message["From"] = sender
    message["To"] = os.environ["EMAIL_TO"]
    message["Subject"] = f"Waimea 51201 đã nhận quan trắc {observed:%d/%m %H:%M} UTC"
    message["Message-ID"] = f"<waimea-{event_key}@{sender.rsplit('@', 1)[-1]}>"
    lines = ["Waimea đã ghi nhận một cụm NOAA mới.", ""]
    for label, zone in (("Việt Nam", "Asia/Ho_Chi_Minh"), ("UTC", "UTC"), ("Hawaii", "Pacific/Honolulu")):
        measured = observed.astimezone(ZoneInfo(zone))
        stored = received.astimezone(ZoneInfo(zone))
        lines.append(f"{label} — NOAA đo {measured:%d/%m/%Y %H:%M:%S}, DB ghi nhận {stored:%d/%m/%Y %H:%M:%S}")
    delay = (received - observed).total_seconds() / 60
    lines.extend(["", f"Độ trễ: {delay:.1f} phút.", "", f"Mở đúng bản ghi Bronze: {link}"])
    message.set_content("\n".join(lines))
    tls = ssl.create_default_context()
    smtp = (smtplib.SMTP_SSL(host, port, timeout=7, context=tls) if port == 465
            else smtplib.SMTP(host, port, timeout=7))
    try:
        if port != 465:
            smtp.starttls(context=tls)
        smtp.login(sender, os.environ["SMTP_PASSWORD"])
        smtp.send_message(message)
    finally:
        # A failure closing the socket after SMTP accepted DATA must not retry it.
        with suppress(OSError):
            smtp.close()


@functions_framework.http
def send_noaa_arrival_email(request):
    if request.method != "POST":
        return {"status": "method_not_allowed"}, 405
    try:
        event = request.get_json(silent=True)
        if event["station_id"] != "51201":
            raise ValueError("unsupported station")
        observed = utc(event["observed_at"])
        received = utc(event["first_seen_at"])
    except (AttributeError, TypeError, ValueError, KeyError):
        return {"status": "invalid_observation"}, 400

    try:
        timestamp = observed.isoformat().replace("+00:00", "Z")
        link = visibility_link(event["station_id"], timestamp)
        send_email(event["station_id"], observed, received, link)
    except (OSError, TypeError, ValueError, KeyError) as exc:
        print(json.dumps({"severity": "WARNING", "event": "noaa_arrival_email_deferred",
                          "error_type": type(exc).__name__}), flush=True)
        return {"status": "deferred"}, 503

    print(json.dumps({"severity": "INFO", "event": "noaa_arrival_email_accepted",
                      "station_id": event["station_id"], "observed_at": timestamp}), flush=True)
    return "", 204
