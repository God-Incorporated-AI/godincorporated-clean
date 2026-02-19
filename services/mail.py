import os
import requests
import logging

# Set up logging
logger = logging.getLogger(__name__)

def send_email(to_email: str, subject: str, html: str):
    api_key = os.getenv("RESEND_API_KEY")
    if not api_key:
        logger.error("RESEND_API_KEY environment variable is required")
        raise RuntimeError("RESEND_API_KEY environment variable is required")
    
    mail_from = os.getenv("MAIL_FROM")
    if not mail_from:
        logger.error("MAIL_FROM environment variable is required")
        raise RuntimeError("MAIL_FROM environment variable is required")
    
    url = "https://api.resend.com/emails"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    data = {
        "from": mail_from,
        "to": [to_email],
        "subject": subject,
        "html": html
    }
    
    try:
        response = requests.post(url, json=data, headers=headers, timeout=30)
        
        if response.status_code >= 400:
            logger.error(f"Resend API error: {response.status_code} {response.text}")
            raise RuntimeError(f"Resend API error: {response.status_code} {response.text}")
        
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Network error sending email: {str(e)}")
        raise RuntimeError(f"Network error sending email: {str(e)}")