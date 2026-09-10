import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()  # load a local .env if present; a no-op in production
except ImportError:
    pass

BASE_DIR = Path(__file__).resolve().parent


def _normalize_db_url(url):
    # Some hosts (Render, Heroku) hand out bare "postgres://" URLs. Point
    # SQLAlchemy at the pure-Python pg8000 driver (no C build step, so it
    # installs cleanly everywhere) instead of the default psycopg2 dialect.
    if url and url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+pg8000://", 1)
    elif url and url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+pg8000://", 1)
    return url


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-in-production")
    SQLALCHEMY_DATABASE_URI = _normalize_db_url(os.environ.get("DATABASE_URL")) or (
        f"sqlite:///{BASE_DIR / 'instance' / 'econ_test.db'}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    UPLOAD_FOLDER = str(BASE_DIR / "uploads" / "question_papers")
    MAX_CONTENT_LENGTH = 15 * 1024 * 1024  # 15 MB upload cap
    ALLOWED_PAPER_EXTENSIONS = {"pdf", "png", "jpg", "jpeg"}
    ALLOWED_IMPORT_EXTENSIONS = {"xlsx", "xls", "csv"}
    WTF_CSRF_TIME_LIMIT = None

    # Web Push (optional). Unset = push disabled, app works normally.
    VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "")
    VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "")
    VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", "mailto:admin@example.com")
