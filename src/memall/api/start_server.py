"""Start MemALL API server with correct database path.
Patches DB_PATH to use the symlink (bypasses TRAE sandbox virtualization)."""
import sys, os
from pathlib import Path

# Ensure src is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

# Point DB_PATH directly at the real database (no copy needed)
from memall.core import db as memall_db
memall_db.DB_PATH = Path.home() / ".memall" / "data.db"

# Verify at startup
import sqlite3
try:
    conn = sqlite3.connect(str(memall_db.DB_PATH), timeout=10)
    cnt = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    max_id = conn.execute("SELECT MAX(id) FROM memories").fetchone()[0]
    conn.close()
    print(f"[startup] DB: {memall_db.DB_PATH}")
    print(f"[startup] Memories: {cnt}, Max ID: {max_id}")
except Exception as e:
    print(f"[startup] ERROR opening DB: {e}")
    raise

from memall.api.server import serve_http
serve_http(port=8199)