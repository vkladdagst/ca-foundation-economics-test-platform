import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

try:
    from dotenv import load_dotenv
    load_dotenv()  # load a local .env if present; a no-op in production
except ImportError:
    pass

BASE_DIR = Path(__file__).resolve().parent


def _normalize_db_url(url):
    if not url:
        return url

    # Some hosts (Render, Heroku, Neon) hand out "postgres://" or
    # "postgresql://" URLs written for the default psycopg2 driver. Point
    # SQLAlchemy at the pure-Python pg8000 driver instead (no C build step,
    # so it installs cleanly everywhere psycopg2's wheels might not).
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+pg8000://", 1)
    elif url.startswith("postgresql://") and "+pg8000" not in url:
        url = url.replace("postgresql://", "postgresql+pg8000://", 1)

    # Connection strings from Postgres providers (Neon, Supabase, Render...)
    # carry query parameters written for psycopg2/libpq -- sslmode,
    # channel_binding, and others. pg8000's connect() doesn't recognize any
    # of them and raises "TypeError: unexpected keyword argument '...'" for
    # whichever one is present, one at a time, as different providers
    # include different sets. Rather than chase each one individually, drop
    # every query parameter for pg8000 URLs: this app needs none of them --
    # TLS is configured explicitly instead, via connect_args, below.
    if "+pg8000" in url:
        parts = urlsplit(url)
        url = urlunsplit(parts._replace(query=""))

    return url


_DB_URL = _normalize_db_url(os.environ.get("DATABASE_URL")) or None
_IS_POSTGRES = bool(_DB_URL and "+pg8000" in _DB_URL)


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-in-production")
    SQLALCHEMY_DATABASE_URI = _DB_URL or f"sqlite:///{BASE_DIR / 'instance' / 'econ_test.db'}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # Managed free-tier Postgres (and a web service that itself sleeps and
    # wakes on the free tier) can silently drop idle connections. Without
    # this, SQLAlchemy tries to reuse a dead pooled connection and the
    # request fails with a raw "network error" / broken pipe. pool_pre_ping
    # cheaply tests each connection before handing it to a request and
    # transparently reconnects if it's gone; pool_recycle retires
    # connections proactively before they get that old. Harmless no-op on
    # SQLite (which doesn't pool connections the same way).
    #
    # connect_args enables TLS for pg8000 (required by Neon and most
    # external Postgres providers) -- ssl_context=True tells pg8000 to use
    # Python's default SSL context, equivalent to psycopg2's sslmode=require.
    # Only set for Postgres: SQLite's driver has no such argument and would
    # error if passed one.
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 280,
        **({"connect_args": {"ssl_context": True}} if _IS_POSTGRES else {}),
    }
    UPLOAD_FOLDER = str(BASE_DIR / "uploads" / "question_papers")
    MAX_CONTENT_LENGTH = 15 * 1024 * 1024  # 15 MB upload cap
    ALLOWED_PAPER_EXTENSIONS = {"pdf", "png", "jpg", "jpeg"}
    ALLOWED_IMPORT_EXTENSIONS = {"xlsx", "xls", "csv"}
    WTF_CSRF_TIME_LIMIT = None

    # Web Push (optional). Unset = push disabled, app works normally.
    VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "")
    VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "")
    VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", "mailto:admin@example.com")
