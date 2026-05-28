import os
from datetime import datetime, timezone

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


def _db_url():
    url = os.environ.get("DATABASE_URL", f"sqlite:///{os.path.join(BASE_DIR, 'wkpoule.db')}")
    # Railway (and Render) geeft soms postgres://, SQLAlchemy wil postgresql://
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-in-production")
    SQLALCHEMY_DATABASE_URI = _db_url()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    ADMIN_PIN = os.environ.get("ADMIN_PIN", "1337")
    # Special predictions lock when the tournament kicks off
    TOURNAMENT_LOCK_UTC = datetime(2026, 6, 11, 19, 0, 0, tzinfo=timezone.utc)
    # Kiosk auto-logout timeout in seconds (used in JS)
    KIOSK_TIMEOUT_SECONDS = 15
