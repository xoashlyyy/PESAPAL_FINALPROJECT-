"""Sends the sign-in codes. With no SMTP settings the code is printed in the server console."""
import os
import smtplib
import ssl
from email.message import EmailMessage

HOST = os.getenv("SMTP_HOST", "")
PORT = int(os.getenv("SMTP_PORT", "587"))
USER = os.getenv("SMTP_USER", "")
PASSWORD = os.getenv("SMTP_PASSWORD", "")
SENDER = os.getenv("SMTP_FROM", USER or "no-reply@pesapal.local")
CONFIGURED = bool(HOST)


def send(to: str, subject: str, body: str) -> bool:
    if not CONFIGURED:
        print(f"[email to {to}] {subject}: {body}")
        return False
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = SENDER, to, subject
    message.set_content(body)
    try:
        with smtplib.SMTP(HOST, PORT, timeout=15) as server:
            server.starttls(context=ssl.create_default_context())
            if USER:
                server.login(USER, PASSWORD)
            server.send_message(message)
        return True
    except Exception as error:
        print("Email failed:", error)
        return False
