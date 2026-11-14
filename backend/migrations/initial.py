from sqlalchemy import text
from src.config.database import Base, engine
from src import models


# Columns added after the first release.  ``create_all`` only creates missing
# tables, so existing databases need explicit, idempotent ALTERs.
_ADDED_COLUMNS = {
    'jobs': [('approved_at', 'DOUBLE NULL')],
    'reports': [('period', 'INT NOT NULL DEFAULT 0')],
    'deliveries': [
        ('period', 'INT NULL'),
        ('due_at', 'DOUBLE NULL'),
        ('status', "VARCHAR(20) NOT NULL DEFAULT 'complete'"),
        ('report_name', "VARCHAR(160) NOT NULL DEFAULT ''"),
        ('job_id', 'INT NULL'),
        ('job_name', "VARCHAR(160) NOT NULL DEFAULT ''"),
        ('params', 'JSON NULL'),
        ('result', 'JSON NULL'),
        ('snapshot_checksum', "VARCHAR(64) NOT NULL DEFAULT ''"),
        ('approved_at', 'DOUBLE NULL'),
        ('completed_at', 'DOUBLE NULL'),
        ('csv_checksum', "VARCHAR(64) NOT NULL DEFAULT ''"),
        ('json_checksum', "VARCHAR(64) NOT NULL DEFAULT ''"),
        ('csv_size', 'INT NOT NULL DEFAULT 0'),
        ('json_size', 'INT NOT NULL DEFAULT 0'),
        ('repair_count', 'INT NOT NULL DEFAULT 0'),
        ('repaired_at', 'DOUBLE NULL'),
        ('last_error', "VARCHAR(1000) NOT NULL DEFAULT ''"),
    ],
}


def _columns(conn, table):
    if conn.dialect.name == 'sqlite':
        return {row[1] for row in conn.execute(text(f'PRAGMA table_info({table})'))}
    rows = conn.execute(text(
        'SELECT column_name FROM information_schema.columns '
        'WHERE table_schema = DATABASE() AND table_name = :table'), {'table': table})
    return {row[0] for row in rows}


def _add_column(conn, table, column, ddl):
    # The DDL subset below is accepted by both MySQL 8 and SQLite.
    conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {column} {ddl}'))


def _ensure_unique(conn):
    # Legacy archives have no period.  Backfill them with a negative sentinel so
    # they stay uniquely addressable without ever colliding with real periods
    # (which start at 1).  They are marked ``legacy`` below and never rewritten.
    conn.execute(text('UPDATE deliveries SET period = -id WHERE period IS NULL'))
    if conn.dialect.name == 'sqlite':
        # Old local SQLite files may predate the constraint; create_all only
        # builds it for fresh databases.
        conn.execute(text(
            'CREATE UNIQUE INDEX IF NOT EXISTS uq_delivery_report_period '
            'ON deliveries (report_id, period)'))
        return
    exists = conn.execute(text(
        "SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema = DATABASE() "
        "AND table_name = 'deliveries' AND index_name = 'uq_delivery_report_period'")).scalar()
    if not exists:
        conn.execute(text(
            'ALTER TABLE deliveries ADD UNIQUE KEY uq_delivery_report_period (report_id, period)'))


def migrate():
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        for table, columns in _ADDED_COLUMNS.items():
            existing = _columns(conn, table)
            for column, ddl in columns:
                if column not in existing:
                    _add_column(conn, table, column, ddl)
        _ensure_unique(conn)
        # Pre-snapshot archives can never be rebuilt; keep them downloadable but
        # make their state explicit instead of pretending they are complete.
        conn.execute(text(
            "UPDATE deliveries SET status = 'legacy' "
            "WHERE period < 0 AND status <> 'legacy'"))


if __name__ == '__main__':
    migrate()
