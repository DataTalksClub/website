"""Operator alert credentials are supplied only by the deployment environment."""

import os

OPERATOR_ALERT_TELEGRAM_BOT_TOKEN = os.getenv("OPERATOR_ALERT_TELEGRAM_BOT_TOKEN", "")
OPERATOR_ALERT_TELEGRAM_CHAT_ID = os.getenv("OPERATOR_ALERT_TELEGRAM_CHAT_ID", "")
