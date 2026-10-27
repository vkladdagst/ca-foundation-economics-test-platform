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
    ("questions", "ai_explanation", "TEXT"),
]

# (table, column, new_sql_type) — widen an existing column that turned out to
# be too narrow for real data (Postgres enforces VARCHAR(n); SQLite doesn't,
# which is exactly how this one first slipped past local testing).
_WIDENED_COLUMNS = [
    ("students", "mobile", "VARCHAR(255)"),
    ("submissions", "mobile", "VARCHAR(255)"),
]


# Chapters 8-10 were first imported with titles/chapter labels in a different
# style from chapters 1-7 ("Ch8 U1 - Money Demand - Test 1" under the chapter
# "Chapter 8 Unit 1 - Money Demand"). Rename them in place so students see one
# consistent scheme. Renaming keeps each test's id, access code and every
# student attempt. (old title prefix, new title prefix, chapter label)
_TEST_RENAMES = [
    ("Ch8 U1 - Money Demand", "Ch 8 Unit 1 - Money Demand", "Ch 8 - Money Market"),
    ("Ch8 U2 - Money Supply", "Ch 8 Unit 2 - Money Supply", "Ch 8 - Money Market"),
    ("Ch8 U3 - Monetary Policy", "Ch 8 Unit 3 - Monetary Policy", "Ch 8 - Money Market"),
    ("Ch9 U1 - Theories of Trade", "Ch 9 Unit 1 - Theories of International Trade", "Ch 9 - International Trade"),
    ("Ch9 U2 - Instruments of Trade Policy", "Ch 9 Unit 2 - Instruments of Trade Policy", "Ch 9 - International Trade"),
    ("Ch9 U3 - Trade Negotiations", "Ch 9 Unit 3 - Trade Negotiations", "Ch 9 - International Trade"),
    ("Ch9 U4 - Exchange Rate", "Ch 9 Unit 4 - Exchange Rate", "Ch 9 - International Trade"),
    ("Ch9 U5 - International Capital Movements", "Ch 9 Unit 5 - International Capital Movements", "Ch 9 - International Trade"),
    ("Ch10 - Indian Economy", "Ch 10 - Indian Economy", "Ch 10 - Indian Economy"),
]


def _rename_bank_tests():
    from app.models import Test

    candidates = Test.query.filter(db.or_(
        Test.title.like("Ch8 U%"), Test.title.like("Ch9 U%"), Test.title.like("Ch10 - %"),
    )).all()
    changed = 0
    for test in candidates:
        for old, new, chapter in _TEST_RENAMES:
            if not test.title.startswith(old + " - Test "):
                continue
            new_title = new + test.title[len(old):]
            clash = Test.query.filter_by(teacher_id=test.teacher_id, title=new_title).first()
            if clash is None:
                test.title = new_title
                test.chapter = chapter
                changed += 1
            break
    if changed:
        db.session.commit()
        logger.info("Migration: renamed %s chapter 8-10 tests to the standard naming", changed)


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

            _rename_bank_tests()
        except Exception:
            db.session.rollback()
            logger.exception("Light migration check failed — continuing startup regardless")
