"""Tiny, hand-rolled, idempotent schema migrations.

There's no Alembic in this project — for an app this size, `db.create_all()`
covers everything except changes to a table that already exists in a live
database with real rows in it. This module handles exactly those cases,
safely:

- Every check runs against SQLAlchemy's inspector first, so each migration
  is a no-op (and safe to run on every startup) once already applied.
- Additive-column changes are purely additive (a new nullable column) — no
  data is altered, dropped, or renamed. Existing rows simply get NULL.
- Column-widening changes only ever make a column *larger* — never smaller,
  never a type that could reject existing data. Postgres only (SQLite never
  enforced VARCHAR length limits in the first place, so there is nothing to
  widen there).
"""
import logging

from sqlalchemy import inspect, text

from app.extensions import db

logger = logging.getLogger(__name__)

# (table, column, SQL type) — add new entries here for future additive columns.
_ADDITIVE_COLUMNS = [
    ("tests", "target_batches_json", "TEXT"),
    ("tests", "chapter", "VARCHAR(150)"),
]

# (table, column, new_sql_type) — widen an existing column that turned out to
# be too narrow for real data (Postgres enforces VARCHAR(n); SQLite doesn't,
# which is exactly how this one first slipped past local testing).
_WIDENED_COLUMNS = [
    ("students", "mobile", "VARCHAR(255)"),
    ("submissions", "mobile", "VARCHAR(255)"),
]


def run_light_migrations(app):
    with app.app_context():
        dialect = db.engine.dialect.name
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

            if dialect == "postgresql":
                for table, column, new_type in _WIDENED_COLUMNS:
                    if table not in existing_tables:
                        continue
                    cols = {c["name"]: c for c in inspector.get_columns(table)}
                    col = cols.get(column)
                    if col is None:
                        continue
                    current_length = getattr(col["type"], "length", None)
                    target_length = int(new_type.split("(")[1].rstrip(")"))
                    if current_length is not None and current_length >= target_length:
                        continue
                    logger.info("Migration: widening %s.%s to %s", table, column, new_type)
                    with db.engine.begin() as conn:
                        conn.execute(text(f"ALTER TABLE {table} ALTER COLUMN {column} TYPE {new_type}"))
        except Exception:
            logger.exception("Light migration check failed — continuing startup regardless")
