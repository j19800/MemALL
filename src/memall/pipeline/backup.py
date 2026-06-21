from memall.core.db import DB_PATH, get_conn
from pathlib import Path
from datetime import datetime


def backup_step() -> dict:
    if not DB_PATH.exists():
        return {"status": "no_db", "path": None}

    backup_dir = DB_PATH.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"data_{stamp}.db"

    conn = get_conn()
    try:
        conn.execute("VACUUM INTO ?", (str(backup_path),))
    finally:
        conn.close()

    rotation(backup_dir, keep_daily=7, keep_weekly=4)

    return {"status": "ok", "path": str(backup_path)}


def rotation(backup_dir: Path, keep_daily: int = 7, keep_weekly: int = 4):
    backups = sorted(backup_dir.glob("data_*.db"), reverse=True)
    if not backups:
        return

    daily_kept = set(backups[:keep_daily])
    weekly_seen = set()
    for b in backups[keep_daily:]:
        if b in daily_kept:
            continue
        if len(weekly_seen) < keep_weekly:
            weekly_seen.add(b)
            continue
        b.unlink(missing_ok=True)
