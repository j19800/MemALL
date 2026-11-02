"""Add bus_dlq table for message idempotency and retry tracking.

Revision ID: a1b2c3d4e5f6
Revises: 041_add_dedup_hits_column
Create Date: 2026-11-02 14:00:00.000000

"""
import sqlite3
from alembic import op


revision = "a1b2c3d4e5f6"
down_revision = "041_add_dedup_hits_column"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bus_dlq (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id TEXT NOT NULL,
            to_agent TEXT NOT NULL,
            payload TEXT NOT NULL,
            error TEXT,
            retry_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            resolved_at TEXT
        )
    """)
    conn.commit()


def downgrade() -> None:
    conn = op.get_connection()
    conn.execute("DROP TABLE IF EXISTS bus_dlq")
    conn.commit()
