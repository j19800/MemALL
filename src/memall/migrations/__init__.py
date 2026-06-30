"""
Schema migration system.

Migrations are applied automatically during `init_db()`. Each migration script is a
Python module with a `MIGRATION_ID` and an `apply(conn)` function. Applied migrations
are tracked in the `schema_version` table. Migrations are idempotent — running them
multiple times produces no damage.

Usage:
    from memall.migrations import run_migrations, get_pending_migrations, get_migration_status
"""

import importlib
import importlib.util
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("memall.migrations")

MIGRATIONS_DIR = Path(__file__).parent


def _ensure_schema_version_table(conn):
    """Create the schema_version table if it doesn't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            migration_id TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL,
            description TEXT DEFAULT '',
            checksum TEXT DEFAULT ''
        )
    """)


def _discover_migrations() -> list[tuple[str, Path]]:
    """Scan migrations/ directory for migration modules sorted by filename.

    Returns list of (migration_id, file_path) tuples.
    """
    migrations = []
    for f in sorted(MIGRATIONS_DIR.glob("*.py")):
        if f.name.startswith("_") or f.name == "__init__.py":
            continue
        # Migration ID is the filename minus extension
        migration_id = f.stem
        migrations.append((migration_id, f))
    return migrations


def _get_applied_migrations(conn) -> set:
    """Return set of already-applied migration IDs."""
    rows = conn.execute("SELECT migration_id FROM schema_version").fetchall()
    return {r["migration_id"] for r in rows}


def _backup_database(db_path: str) -> str:
    """Create a timestamped backup of the database before applying migrations."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = Path(db_path).parent / "backups" / "migration" / f"pre_migrate_{stamp}.db"
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(db_path, backup_path)
    return str(backup_path)


def _exec_migration_script(file_path: Path, conn) -> dict:
    """Execute a single migration script.

    Each script must define `MIGRATION_ID` (str) and an `apply(conn)` function.
    """

    module_name = f"memall_migration_{file_path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)

    try:
        spec.loader.exec_module(module)
    except Exception as e:
        return {"status": "error", "reason": f"Failed to load migration {file_path.name}: {e}"}

    if not hasattr(module, "apply"):
        return {"status": "error", "reason": f"Migration {file_path.name} missing apply(conn) function"}

    migration_id = getattr(module, "MIGRATION_ID", file_path.stem)
    description = getattr(module, "DESCRIPTION", "")

    # Check if already applied (idempotency guard)
    already = conn.execute(
        "SELECT 1 FROM schema_version WHERE migration_id = ?", (migration_id,)
    ).fetchone()
    if already:
        return {"status": "skipped", "migration_id": migration_id,
                "reason": "Already applied"}

    # Apply migration
    try:
        module.apply(conn)
    except Exception as e:
        return {"status": "error", "migration_id": migration_id,
                "reason": f"Migration script failed: {e}"}

    # Record as applied
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT OR REPLACE INTO schema_version (migration_id, applied_at, description) VALUES (?, ?, ?)",
        (migration_id, now, description),
    )
    conn.commit()
    return {"status": "ok", "migration_id": migration_id, "description": description}


def run_migrations(conn, dry_run: bool = False, db_path: str = "") -> dict:
    """Run all pending migrations. Returns summary dict.

    Args:
        conn: sqlite3 connection
        dry_run: If True, list pending migrations without applying
        db_path: Path to database file, required for backup
    """
    _ensure_schema_version_table(conn)

    discovered = _discover_migrations()
    applied = _get_applied_migrations(conn)

    pending = [(mid, fp) for mid, fp in discovered if mid not in applied]

    if not pending:
        return {"status": "ok", "applied": 0, "pending": 0, "message": "No pending migrations"}

    if dry_run:
        return {
            "status": "ok",
            "applied": 0,
            "pending": len(pending),
            "pending_list": [mid for mid, _ in pending],
            "message": f"{len(pending)} migration(s) pending (dry-run)",
        }

    # Auto-backup before migration
    backup_path = ""
    if db_path and Path(db_path).exists():
        try:
            backup_path = _backup_database(db_path)
        except Exception as e:
            logger.warning(f"Migration backup failed: {e}")

    results = []
    applied_count = 0
    error_count = 0
    skipped_count = 0

    for migration_id, file_path in pending:
        result = _exec_migration_script(file_path, conn)
        results.append(result)
        if result["status"] == "ok":
            applied_count += 1
        elif result["status"] == "skipped":
            skipped_count += 1
        else:
            error_count += 1
            logger.error(f"Migration {migration_id}: {result['reason']}")

    return {
        "status": "ok" if error_count == 0 else "partial",
        "applied": applied_count,
        "pending": len(pending),
        "skipped": skipped_count,
        "errors": error_count,
        "results": results,
        "backup": backup_path,
        "message": f"Applied {applied_count} migration(s), {skipped_count} skipped, {error_count} error(s)",
    }


def get_pending_migrations(conn) -> list[str]:
    """Return list of pending migration IDs. Used by `memall doctor`."""
    _ensure_schema_version_table(conn)
    discovered = _discover_migrations()
    applied = _get_applied_migrations(conn)
    return [mid for mid, _ in discovered if mid not in applied]


def get_migration_status(conn) -> dict:
    """Return full migration status for diagnostics."""
    _ensure_schema_version_table(conn)
    discovered = _discover_migrations()
    applied = _get_applied_migrations(conn)

    applied_details = conn.execute(
        "SELECT migration_id, applied_at, description FROM schema_version ORDER BY applied_at LIMIT 1000"
    ).fetchall()

    return {
        "total_discovered": len(discovered),
        "total_applied": len(applied),
        "pending": [mid for mid, _ in discovered if mid not in applied],
        "applied": [{"id": r["migration_id"], "at": r["applied_at"],
                      "desc": r["description"]} for r in applied_details],
    }
