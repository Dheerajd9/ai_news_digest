"""Email a short alert when a scheduled World Brief run fails (no secrets printed)."""

import os
import smtplib
from email.mime.text import MIMEText

user, pwd, to = (os.getenv(k, "") for k in ("GMAIL_USER", "GMAIL_APP_PASSWORD", "TO_EMAIL"))
edition = os.getenv("EDITION", "?")
url = os.getenv("RUN_URL", "")
if user and pwd and to:
    body = (f"The {edition} World Brief run failed.\n\n"
            "Nothing to do: the next scheduled slot will retry automatically "
            "(morning until about 1 PM, evening until about 11 PM Dallas time).\n\n"
            f"Run details: {url}\n")
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = f"⚠️ World Brief ({edition}) run failed — retrying automatically"
    msg["From"], msg["To"] = user, to
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(user, pwd)
            s.sendmail(user, [a.strip() for a in to.split(",") if a.strip()], msg.as_string())
        print("failure alert sent")
    except Exception as exc:
        print(f"could not send failure alert: {type(exc).__name__}")
