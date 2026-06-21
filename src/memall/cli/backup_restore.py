import logging
"""
memall backup / restore: manual backup and recovery operations.
logger = logging.getLogger(__name__)


Data-never-leaves-you principle:
- Every restore auto-backs up current db to .before-restore.db
- Backups stored in ~/.memall/backups/daily/ and weekly/
- Clean command with retention policy
"""

import shutil
import sys
from pathlib import Path
from datetime import datetime

from memall.core.db import DB_PATH


BACKUP_DIR = Path.home() / ".memall" / "backups"
DAILY_DIR = BACKUP_DIR / "daily"
WEEKLY_DIR = BACKUP_DIR / "weekly"


def _ensure_dirs():
    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    WEEKLY_DIR.mkdir(parents=True, exist_ok=True)


def _get_db_path() -> Path:
    """Get the current SQLite database path."""
    return DB_PATH


def _vacuum_backup(source: Path, dest: Path) -> bool:
    """Create a clean SQLite backup using VACUUM INTO."""
    import sqlite3
    try:
        conn = sqlite3.connect(str(source))
        conn.execute("VACUUM INTO ?", (str(dest),))
        conn.close()
        return True
    except sqlite3.OperationalError:
        # Fallback: file copy
        try:
            shutil.copy2(source, dest)
            return True
        except OSError:
            return False
    except Exception:
        return False


def backup_db(output_path: str = None) -> dict:
    """
    Manually backup the database to daily/YYYY-MM-DD.db.

    Returns:
        {"status": "ok", "path": str, "size": int}
        {"status": "no_db", "reason": str}
        {"status": "error", "reason": str}
    """
    db_path = _get_db_path()
    if not db_path.exists():
        return {"status": "no_db", "reason": f"Database not found: {db_path}"}

    _ensure_dirs()

    if output_path:
        dest = Path(output_path)
        if not dest.is_absolute():
            dest = DAILY_DIR / output_path
    else:
        today = datetime.now().strftime("%Y-%m-%d")
        dest = DAILY_DIR / f"{today}.db"

    dest.parent.mkdir(parents=True, exist_ok=True)

    if not _vacuum_backup(db_path, dest):
        return {"status": "error", "reason": f"Failed to backup to {dest}"}

    size = dest.stat().st_size
    return {"status": "ok", "path": str(dest), "size": size}


def restore_db(backup_path: str, auto: bool = False) -> dict:
    """
    Restore database from a backup file.

    Before restoring, the current database is backed up to .before-restore.db.

    Args:
        backup_path: Path to the backup file (relative to BACKUP_DIR or absolute).
        auto: If True, find the latest available backup.

    Returns:
        {"status": "ok", "from": str, "before_restore_backup": str, "memories": int}
        {"status": "error", "reason": str}
    """
    import sqlite3

    if auto:
        source = _find_latest_backup()
        if not source:
            return {"status": "error",
                    "reason": "No backups found. Run `memall backup` first."}
    else:
        source = Path(backup_path)
        if not source.is_absolute():
            # Handle "daily/YYYY-MM-DD.db" or "weekly/YYYY-MM-DD.db" prefix
            bp = str(backup_path).replace("\\", "/")
            if bp.startswith("daily/"):
                source = DAILY_DIR / bp[len("daily/"):]
            elif bp.startswith("weekly/"):
                source = WEEKLY_DIR / bp[len("weekly/"):]
            else:
                # Try daily then weekly with just the filename
                source_daily = DAILY_DIR / backup_path
                source_weekly = WEEKLY_DIR / backup_path
                if source_daily.exists():
                    source = source_daily
                elif source_weekly.exists():
                    source = source_weekly
                else:
                    return {"status": "error",
                            "reason": f"Backup not found: {backup_path}. "
                                      f"Tried: {source_daily}, {source_weekly}"}

    if not source.exists():
        return {"status": "error", "reason": f"Backup file not found: {source}"}

    # Verify it's a valid SQLite database
    try:
        test_conn = sqlite3.connect(str(source))
        test_conn.execute("SELECT count(*) FROM memories")
        test_conn.close()
    except sqlite3.Error as e:
        return {"status": "error", "reason": f"Invalid backup file: {e}"}

    db_path = _get_db_path()

    # Pre-restore backup
    pre_restore_bak = None
    if db_path.exists():
        pre_restore_bak = db_path.with_name("data.before-restore.db")
        shutil.copy2(db_path, pre_restore_bak)

    # Perform restore
    db_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, db_path)

    return {
        "status": "ok",
        "from": str(source),
        "before_restore_backup": str(pre_restore_bak) if pre_restore_bak else None,
        "size": source.stat().st_size,
    }


def _find_latest_backup() -> Path | None:
    """Find the latest backup file across daily and weekly directories."""
    candidates = []
    for d in [DAILY_DIR, WEEKLY_DIR]:
        if d.exists():
            for f in d.glob("*.db"):
                candidates.append((f.stat().st_mtime, f))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def clean_backups(keep_daily: int = 7, keep_weekly: int = 4) -> dict:
    """
    Clean old backups according to retention policy.

    Args:
        keep_daily: Number of daily backups to keep (most recent).
        keep_weekly: Number of weekly backups to keep (most recent).

    Returns:
        {"status": "ok", "deleted": [...], "kept_daily": int, "kept_weekly": int}
    """
    deleted = []

    # Clean daily
    if DAILY_DIR.exists():
        dailies = sorted(DAILY_DIR.glob("*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
        for f in dailies[keep_daily:]:
            try:
                f.unlink()
                deleted.append(str(f))
            except OSError:
                logger.warning("backup_restore.py: silent error", exc_info=True)

    # Clean weekly
    if WEEKLY_DIR.exists():
        weeklies = sorted(WEEKLY_DIR.glob("*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
        for f in weeklies[keep_weekly:]:
            try:
                f.unlink()
                deleted.append(str(f))
            except OSError:
                logger.warning("backup_restore.py: silent error", exc_info=True)

    # Count remaining
    kept_daily = len(list(DAILY_DIR.glob("*.db"))) if DAILY_DIR.exists() else 0
    kept_weekly = len(list(WEEKLY_DIR.glob("*.db"))) if WEEKLY_DIR.exists() else 0

    return {
        "status": "ok",
        "deleted": deleted,
        "kept_daily": kept_daily,
        "kept_weekly": kept_weekly,
    }


def list_backups() -> list[dict]:
    """List all available backups."""
    result = []
    for label, d in [("daily", DAILY_DIR), ("weekly", WEEKLY_DIR)]:
        if d.exists():
            for f in sorted(d.glob("*.db"), key=lambda p: p.stat().st_mtime, reverse=True):
                result.append({
                    "type": label,
                    "path": str(f),
                    "date": f.stem,
                    "size": f.stat().st_size,
                    "mtime": f.stat().st_mtime,
                })
    result.sort(key=lambda x: x["mtime"], reverse=True)
    return result


def cmd_backup(args):
    """CLI handler for `memall backup`."""
    action = getattr(args, "action", None)
    if action == "clean":
        keep_daily = getattr(args, "keep_daily", 7) or 7
        keep_weekly = getattr(args, "keep_weekly", 4) or 4
        result = clean_backups(keep_daily=keep_daily, keep_weekly=keep_weekly)
        if result["status"] == "ok":
            print(f"Cleanup complete.")
            print(f"  Kept daily:  {result['kept_daily']}")
            print(f"  Kept weekly: {result['kept_weekly']}")
            if result["deleted"]:
                print(f"  Deleted: {len(result['deleted'])} files")
                for d in result["deleted"][:10]:
                    print(f"    {d}")
                if len(result["deleted"]) > 10:
                    print(f"    ... and {len(result['deleted']) - 10} more")
            else:
                print(f"  Nothing to delete.")
        else:
            print(f"Error: {result.get('reason', 'unknown')}", file=sys.stderr)
            sys.exit(1)
        return

    if hasattr(args, "list") and args.list:
        backups = list_backups()
        if not backups:
            print("No backups found.")
            return
        print(f"Backups ({len(backups)}):")
        for b in backups:
            ts = datetime.fromtimestamp(b["mtime"]).strftime("%Y-%m-%d %H:%M")
            size_kb = b["size"] / 1024
            print(f"  [{b['type']:6s}] {b['date']:12s} {size_kb:8.1f} KB  {ts}")
        return

    # Default: manual backup
    result = backup_db()
    if result["status"] == "ok":
        size_kb = result["size"] / 1024
        print(f"[OK] Backed up to {result['path']} ({size_kb:.1f} KB)")
    elif result["status"] == "no_db":
        print(f"No database to backup: {result['reason']}", file=sys.stderr)
        sys.exit(1)
    else:
        print(f"Backup failed: {result.get('reason', 'unknown')}", file=sys.stderr)
        sys.exit(1)


def cmd_restore(args):
    """CLI handler for `memall restore`."""
    auto = getattr(args, "auto", False)
    from_path = getattr(args, "from_", None)  # 'from' is a Python keyword

    if auto:
        result = restore_db("", auto=True)
    elif from_path:
        result = restore_db(from_path)
    else:
        print("Usage:")
        print("  memall restore --from daily/YYYY-MM-DD.db   Restore from daily backup")
        print("  memall restore --from weekly/YYYY-MM-DD.db  Restore from weekly backup")
        print("  memall restore --auto                        Restore from latest backup")
        print()
        print("Tip: current database is auto-backed up to .before-restore.db before restore.")
        print("Available backups:")
        backups = list_backups()
        if not backups:
            print("  (none)")
        else:
            for b in backups[:10]:
                ts = datetime.fromtimestamp(b["mtime"]).strftime("%Y-%m-%d %H:%M")
                size_kb = b["size"] / 1024
                print(f"  [{b['type']:6s}] {b['date']:12s} {size_kb:8.1f} KB  {ts}")
            if len(backups) > 10:
                print(f"  ... and {len(backups) - 10} more")
        return

    if result["status"] == "ok":
        print(f"[OK] Database restored from {result['from']}")
        if result.get("before_restore_backup"):
            print(f"Pre-restore backup saved to {result['before_restore_backup']}")
        size_kb = result["size"] / 1024
        print(f"Size: {size_kb:.1f} KB")
        print(f"Tip: run `memall status` to verify.")
    else:
        print(f"Restore failed: {result.get('reason', 'unknown')}", file=sys.stderr)
        sys.exit(1)
