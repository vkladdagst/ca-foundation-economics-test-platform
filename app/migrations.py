"""Tiny, hand-rolled, idempotent schema migrations.

There's no Alembic in this project — for an app this size, `db.create_all()`
covers everything except one situation it can't handle: adding a column to
a table that already exists in a live database with real rows in it. This
module does exactly that, safely:

- Checked with SQLAlchemy's inspector before touching anything, so it's a
  no-op (and safe to run on every startup) once the column is present.
- Every change here is purely additive (a new nullable column) — no data
  is altered, dropped, or renamed. Existing rows simply get NULL, which is
  the correct default for every column added this way so far.
- Works the same way against SQLite (local dev) and Postgres (production)
  since `ALTER TABLE ... ADD COLUMN ...` is valid on both.
"""
import logging

from sqlalchemy import inspect, text

from app.extensions import db

logger = logging.getLogger(__name__)

# (table, column, SQL type) — add new entries here for future additive columns.
_ADDITIVE_COLUMNS = [
    ("tests", "target_batches_json", "TEXT"),
]


def run_light_migrations(app):
    with app.app_context():
        try:
            inspector = inspect(db.engine)
            existing_tables = set(inspector.get_table_names())

            for table, column, sql_type in _ADDITIVE_COLUMNS:
                if table not in existing_tables:
                    continue  # db.create_all() will create it fresh, with the column already present
                cols = {c["name"] for c in inspector.get_columns(table)}
                if column in cols:
                    continue
                logger.info("Migration: adding column %s.%s", table, column)
                with db.engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"))
        except Exception:
            logger.exception("Light migration check failed — continuing startup regardless")
