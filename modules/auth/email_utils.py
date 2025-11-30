# modules/auth/email_utils.py

import smtplib
from email.mime.text import MIMEText
from flask import current_app


def send_email(to_email: str, subject: str, body: str):
    host = current_app.config.get("SMTP_HOST")
    port = int(current_app.config.get("SMTP_PORT", 587))
    user = current_app.config.get("SMTP_USER")
    password = current_app.config.get("SMTP_PASSWORD")
    sender = current_app.config.get("SMTP_FROM", user or "no-reply@example.com")

    # Dev fallback → log to console
    if not host or not user or not password:
        print(f"[DEV EMAIL] To: {to_email}\nSubject: {subject}\n\n{body}")
        current_app.logger.warning(
            f"[DEV EMAIL] To: {to_email}\nSubject: {subject}\n{body}"
        )
        return

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to_email

    with smtplib.SMTP(host, port) as smtp:
        try:
            smtp.starttls()
        except Exception:
            pass
        if user and password:
            smtp.login(user, password)
        smtp.sendmail(sender, [to_email], msg.as_string())


def send_otp_email(to_email: str, otp_code: str):
    subject = "Your Login Code"
    body = f"Your OTP is: {otp_code}\n\nThis code expires in 10 minutes."
    send_email(to_email, subject, body)
