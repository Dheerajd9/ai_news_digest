"""Email a short alert when a scheduled World Brief run fails (no secrets printed)."""

import os
import smtplib
from email.mime.text import MIMEText

user, pwd, to = (os.getenv(k, "") for k in ("GMAIL_USER", "GMAIL_APP_PASSWORD", "TO_EMAIL"))
edition = os.getenv("EDITION", "?")
url = os.getenv("RUN_URL", "")
if user and pwd and to:
    if edition == "test":
        body = ("A code change just failed the automatic end-to-end test (fake news, fake AI). "
                "Tell Claude about this email so it is fixed before the next edition.\n\n"
                f"Run details: {url}\n")
    else:
        body = (f"The {edition} World Brief run failed.\n\n"
                "Nothing to do: the next scheduled slot will retry automatically "
                "(morning until about 1 PM, evening until about 11 PM US Eastern time).\n\n"
                f"Run details: {url}\n")
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = ("⚠️ World Brief code test failed" if edition == "test" else
                      f"⚠️ World Brief ({edition}) run failed — retrying automatically")
    msg["From"], msg["To"] = user, to
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(user, pwd)
            s.sendmail(user, [a.strip() for a in to.split(",") if a.strip()], msg.as_string())
        print("failure alert sent")
    except Exception as exc:
        print(f"could not send failure alert: {type(exc).__name__}")
