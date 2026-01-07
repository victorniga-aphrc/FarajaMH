from mailjet_rest import Client
import os

from dotenv import load_dotenv
load_dotenv()

api_key = os.getenv('MAILJET_API_KEY')
api_secret = os.getenv('MAILJET_API_SECRET')


mailjet = Client(auth=(api_key, api_secret), version='v3.1')
from flask import render_template

def send_mail_with_html_file(recipient_email, subject, html_file_name, placeholders: dict):
    sender_email = 'percy0.brown@gmail.com'
    sender_name = 'APHRC MHS'

    # Use Jinja to render HTML
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

    result = mailjet.send.create(data=data)
    return result.status_code, result.json()
