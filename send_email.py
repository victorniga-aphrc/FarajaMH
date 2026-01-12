import os
import logging
from dotenv import load_dotenv
from flask import render_template

load_dotenv()

api_key = os.getenv('MAILJET_API_KEY')
api_secret = os.getenv('MAILJET_API_SECRET')
sender_email = os.getenv('MAILJET_SENDER_EMAIL')
sender_name = os.getenv('MAILJET_SENDER_NAME', 'APHRC Faraja MH')

MAILJET_ENABLED = bool(api_key and api_secret)
mailjet = None

if MAILJET_ENABLED:
    try:
        from mailjet_rest import Client
        mailjet = Client(auth=(api_key, api_secret), version='v3.1')
    except Exception:
        MAILJET_ENABLED = False


def send_mail_with_html_file(recipient_email, subject, html_file_name, placeholders: dict):
    html_content = render_template(html_file_name, **placeholders)

    data = {
        'Messages': [
            {
                "From": {
                    "Email": sender_email,
                    "Name": sender_name
                },
                "To": [{"Email": recipient_email}],
                "Subject": subject,
                "HTMLPart": html_content
            }
        ]
    }

    if not MAILJET_ENABLED or mailjet is None:
        logging.warning("Mailjet disabled or unavailable; mocking email send to %s", recipient_email)
        return 200, {"ok": True, "mock": True, "to": recipient_email, "subject": subject}

    try:
        result = mailjet.send.create(data=data)
        return result.status_code, result.json()
    except Exception as e:
        logging.exception("Email send failed: %s", e)
        return 0, {"ok": False, "error": str(e)}
