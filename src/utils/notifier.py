"""Seat/reservation alerts: macOS notification, terminal bell, and optional ntfy push to the phone."""
import subprocess
from typing import Optional

import requests

from config.settings import settings
from utils.logger import get_logger

logger = get_logger(__name__)

NTFY_TIMEOUT_SECONDS = 5


def notify(title: str, message: str, click_url: Optional[str] = None) -> None:
    """Alert on this Mac, and on the phone too when NTFY_TOPIC is set in .env."""
    print("\a", end="", flush=True)
    _notify_macos(title, message)
    if settings.NTFY_TOPIC:
        send_ntfy(title, message, click_url)


def _notify_macos(title: str, message: str) -> None:
    script = f'display notification {_applescript_str(message)} with title {_applescript_str(title)} sound name "Glass"'
    try:
        subprocess.run(["osascript", "-e", script], check=False, timeout=5)
    except (OSError, subprocess.SubprocessError):
        pass


def _applescript_str(text: str) -> str:
    # Train names and reservation text can contain quotes, which would otherwise end the AppleScript string.
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def send_ntfy(title: str, message: str, click_url: Optional[str] = None) -> bool:
    """Push to NTFY_TOPIC. Failures are logged, never raised, so the alert path can't crash."""
    # JSON publishing keeps the Korean title intact; HTTP headers would need extra encoding.
    payload = {
        "topic": settings.NTFY_TOPIC,
        "title": title,
        "message": message,
        "priority": 5,  # max: rings even when other notifications are quiet
        "tags": ["train"],
    }
    if click_url:
        payload["click"] = click_url
    try:
        response = requests.post(settings.NTFY_SERVER.rstrip("/"), json=payload, timeout=NTFY_TIMEOUT_SECONDS)
    except requests.RequestException as e:
        logger.warning(f"⚠️ ntfy 알림 전송 실패: {type(e).__name__}")
        return False
    if not response.ok:
        logger.warning(f"⚠️ ntfy 알림 전송 실패 (HTTP {response.status_code}): NTFY_TOPIC/NTFY_SERVER를 확인해주세요")
        return False
    return True
