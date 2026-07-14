"""Telegram notifications (best-effort — a notify failure never blocks trading)."""
import requests

from . import config


def send(text):
    if not (config.TELEGRAM_TOKEN and config.TELEGRAM_CHAT_ID):
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": config.TELEGRAM_CHAT_ID, "text": text},
            timeout=15,
        )
        return r.status_code == 200
    except requests.RequestException:
        return False
