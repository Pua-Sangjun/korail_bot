"""
Application configuration management.

This module centralizes all configuration variables from environment
and provides type-safe access to settings throughout the application.
"""
import os
from typing import Optional


class Settings:
    """Application settings loaded from environment variables."""

    # Korail Configuration
    KORAIL_USER_ID: Optional[str] = os.environ.get('USERID')
    KORAIL_PASSWORD: Optional[str] = os.environ.get('USERPW')
    KORAIL_SEARCH_INTERVAL: int = int(os.environ.get('SEARCH_INTERVAL', '1'))  # seconds
    KORAIL_PAYMENT_URL: str = "https://www.letskorail.com/ebizprd/EbizPrdTicketpr13500W_pr13510.do?"

    # Payment deadline shown after a successful reservation
    PAYMENT_TIMEOUT_MINUTES: int = int(os.environ.get('PAYMENT_TIMEOUT_MINUTES', '10'))

    # Logging Configuration
    LOG_LEVEL: str = os.environ.get('LOG_LEVEL', 'INFO')
    LOG_FORMAT: str = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'


# Singleton instance
settings = Settings()
