import logging
from flask import render_template, current_app
from flask_mail import Message
from app_pkg.extensions import mail


def send_mail_with_html_file(recipient_email: str, subject: str, html_file_name: str, placeholders: dict):
    """Send an HTML email using Flask-Mail and server SMTP settings.

    Returns (status_code, payload) similar to previous Mailjet helper.
    - On success: (200, {"ok": True})
    - On failure: (0, {"ok": False, "error": "..."})
    - If MAIL_SUPPRESS_SEND is True: (200, {"ok": True, "mock": True})
    """
    try:
        html_content = render_template(html_file_name, **placeholders)
    except Exception as e:
        logging.exception("Email template render failed: %s", e)
        return 0, {"ok": False, "error": f"template render: {e}"}

    suppress = bool(current_app.config.get("MAIL_SUPPRESS_SEND"))

    try:
        msg = Message(
            subject=subject,
            recipients=[recipient_email],
        )
        # Ensure a sender is always configured to satisfy Flask-Mail assertion
        default_sender = current_app.config.get("MAIL_DEFAULT_SENDER")
        if not default_sender or (isinstance(default_sender, str) and not default_sender.strip()):
            # Fallback to MAIL_USERNAME or a safe local address
            default_sender = current_app.config.get("MAIL_USERNAME") or "noreply@localhost"
        msg.sender = default_sender
        msg.html = html_content

        if suppress:
            logging.warning("MAIL_SUPPRESS_SEND=True; mocking email to %s", recipient_email)
            return 200, {"ok": True, "mock": True, "to": recipient_email, "subject": subject}

        mail.send(msg)
        return 200, {"ok": True}
    except Exception as e:
        logging.exception("Email send failed: %s", e)
        return 0, {"ok": False, "error": str(e)}
