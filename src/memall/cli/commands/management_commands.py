"""Management CLI commands: start, stop, status, doctor, migrate, trust, identity,
graph-visualize, index, retrieve, onboarding, serve, db, and re-exports from
other CLI modules (setup, register, uninstall, backup, restore, export)."""

import json
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from memall import __version__
from memall.core.db import init_db, get_conn, get_db_path, rebuild_fts
from memall.core.models import MemoryInput
from memall.core.thin_waist import retrieve, capture, connect

# Re-exports from sibling CLI modules (no code changes, just pass-through)
from memall.cli.setup import cmd_setup  # noqa: F401
from memall.cli.register import cmd_register  # noqa: F401
from memall.cli.uninstall import cmd_uninstall  # noqa: F401
from memall.cli.backup_restore import cmd_backup, cmd_restore  # noqa: F401
from memall.cli.export import cmd_export  # noqa: F401

logger = logging.getLogger("memall")


# ──────────────────────────────────────────────
# Helper
# ──────────────────────────────────────────────

def _rget(row, key, default=""):
    try:
        val = row[key]
        return val if val is not None else default
    except (IndexError, KeyError):
        return default


def rebuild_fts_cmd():
    conn = get_conn()
    try:
        rebuild_fts(conn)
    finally:
        conn.close()


# ──────────────────────────────────────────────
# cmd_start / cmd_stop / cmd_status
# ──────────────────────────────────────────────

def cmd_start(args):
    from memall.scheduler.scheduler import daemon_start
    daemon_start()


def cmd_stop(args):
    from memall.scheduler.scheduler import daemon_stop
    daemon_stop()


def cmd_status(args):
    init_db()
    conn = get_conn()
    try:
        db_path = get_db_path()
        mem_count = conn.execute("SELECT COUNT(*) as c FROM memories").fetchone()["c"]
        edge_count = conn.execute("SELECT COUNT(*) as c FROM edges").fetchone()["c"]
        agents = conn.execute("SELECT agent_name, agent_type, status, last_heartbeat FROM identities").fetchall()

        pid_alive = False
        pid_file = Path.home() / ".memall" / "scheduler.pid"
        if pid_file.exists():
            pid = int(pid_file.read_text().strip())
            try:
                subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], check=True, capture_output=True)
                pid_alive = True
            except Exception:
                pid_file.unlink(missing_ok=True)

        print(f"MemALL v{__version__}")
        print(f"Database: {db_path} ({db_path.stat().st_size / 1024:.0f} KB)" if db_path.exists() else f"Database: {db_path} (not found)")
        print(f"Scheduler: {'running' if pid_alive else 'stopped'}")
        print(f"Memories: {mem_count} | Edges: {edge_count}")
        marvis_unread = conn.execute("SELECT COUNT(*) as c FROM memories WHERE category='marvis_message'").fetchone()["c"]
        if marvis_unread > 0:
            print(f"Marvis: {marvis_unread} unread messages (memall search --category marvis_message)")
        pending_sug = conn.execute("SELECT COUNT(*) as c FROM suggestions WHERE status='pending'").fetchone()["c"]
        if pending_sug > 0:
            print(f"Suggestions: {pending_sug} pending (memall suggest --list --status pending)")
        print(f"Agents: {len(agents)}")
        for a in agents:
            hb = a['last_heartbeat'][:19] if a['last_heartbeat'] else 'never'
            print(f"  {a['agent_name']:20s} {a['agent_type']:6s} {a['status']:8s} last_hb={hb}")
    finally:
        conn.close()


# ──────────────────────────────────────────────
# cmd_doctor
# ──────────────────────────────────────────────

def cmd_doctor(args):
    """Enhanced 11-point health check with fix commands."""
    from memall.pipeline.metrics import show_metrics, read_history
    from memall.cli.setup import AGENT_PATHS
    from memall.cli.register import list_registered_agents, check_agent_connection

    # ── Metrics-only mode ──
    if args.metrics:
        m = show_metrics()
        print("System metrics:")
        for k, v in m.items():
            print(f"  {k:25s} {v}")
        print("\nHistory (last 5):")
        for h in read_history(5):
            print(f"  {h.get('timestamp','')[:19]} density={h.get('connection_density')} coverage={h.get('classification_coverage')}")
        return

    results = []  # list of (check_name, status, detail, fix_cmd)

    def _add(name, status, detail, fix=None):
        results.append((name, status, detail, fix))

    # ── Check 1: SQLite connection + stats ──
    init_db()
    conn = None
    try:
        conn = get_conn()
        mem_count = conn.execute("SELECT COUNT(*) as c FROM memories").fetchone()["c"]
        edge_count = conn.execute("SELECT COUNT(*) as c FROM edges").fetchone()["c"]
        table_check = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('memories','edges','identities')"
        ).fetchall()
        tables_found = [r["name"] for r in table_check]
        if len(tables_found) == 3:
            _add("SQLite DB", "PASS", f"{mem_count} memories, {edge_count} edges")
        else:
            missing = set(["memories", "edges", "identities"]) - set(tables_found)
            _add("SQLite DB", "FAIL", f"Missing tables: {missing}", "memall init")
    except Exception as e:
        _add("SQLite DB", "FAIL", f"Connection failed: {e}", "memall init")

    # ── Check 2: Directory permissions ──
    memall_dir = Path.home() / ".memall"
    if not memall_dir.exists():
        _add("Directory", "WARN", f"{memall_dir} does not exist", "memall init")
    else:
        can_read = os.access(memall_dir, os.R_OK)
        can_write = os.access(memall_dir, os.W_OK)
        if can_read and can_write:
            _add("Directory", "PASS", str(memall_dir))
        else:
            perms = []
            if not can_read:
                perms.append("not readable")
            if not can_write:
                perms.append("not writable")
            _add("Directory", "FAIL", f"{memall_dir} {', '.join(perms)}", "chmod 755 ~/.memall/")

    # ── Check 3: MCP Server executable + version ──
    mcp_exe = shutil.which("memall-mcp-server")
    if mcp_exe:
        try:
            result = subprocess.run([mcp_exe, "--version"], capture_output=True, text=True, timeout=5)
            ver = result.stdout.strip() or result.stderr.strip() or "unknown version"
            _add("MCP Server", "PASS", f"{mcp_exe} --- {ver[:80]}")
        except Exception as e:
            _add("MCP Server", "WARN", f"Found at {mcp_exe} but failed to get version: {e}")
    else:
        _add("MCP Server", "WARN", "memall-mcp-server not found in PATH", "pip install memall")

    # ── Check 4: Agent configs ──
    for agent_key, info in AGENT_PATHS.items():
        cfg_path = info["path"]
        if not cfg_path.exists():
            _add(f"Agent: {info['name']}", "WARN",
                 f"Config not found at {cfg_path}",
                 f"memall setup --agent {agent_key}")
            continue
        try:
            raw = cfg_path.read_text(encoding="utf-8")
            if info["format"] == "json":
                cfg = json.loads(raw)
                servers = cfg.get(info["config_key"], {})
            else:
                import yaml
                cfg = yaml.safe_load(raw) or {}
                servers = cfg.get(info["config_key"], {}) if isinstance(cfg.get(info["config_key"]), dict) else {}
            if "memall" in servers:
                _add(f"Agent: {info['name']}", "PASS", f"Configured at {cfg_path}")
            else:
                _add(f"Agent: {info['name']}", "WARN",
                     f"Config exists but MemALL not registered",
                     f"memall setup --agent {agent_key}")
        except Exception as e:
            _add(f"Agent: {info['name']}", "FAIL",
                 f"Cannot parse {cfg_path}: {e}",
                 f"memall setup --agent {agent_key} --fix")

    # ── Check 4b: Registered agents (GAP-6) ──
    registered = list_registered_agents()
    if registered:
        for a in registered:
            a_name = a.get("agent_name", "?")
            a_type = a.get("agent_type", "mcp")
            conn_result = check_agent_connection(
                a_name, a_type,
                url=a.get("url", ""),
                command=a.get("command", ""),
            )
            if conn_result["status"] == "PASS":
                _add(f"RegAgent: {a_name}", "PASS",
                     f"{a_type} --- {conn_result['detail']}")
            else:
                _add(f"RegAgent: {a_name}", conn_result["status"],
                     f"{a_type} --- {conn_result['detail']}",
                     "memall register --remove " + a_name if conn_result["status"] == "FAIL" else None)

    # ── Check 4c: Pending migrations (GAP-7) ──
    try:
        from memall.migrations import get_pending_migrations as get_pending_migs
        if conn:
            pending_migs = get_pending_migs(conn)
            if pending_migs:
                _add("Migrations", "WARN",
                     f"{len(pending_migs)} pending: {', '.join(pending_migs[:5])}",
                     "memall migrate --apply")
            else:
                _add("Migrations", "PASS", "All migrations applied")
    except ImportError:
        logger.warning("management_commands.py: silent error", exc_info=True)

    # ── Check 5: Disk space ──
    try:
        usage = shutil.disk_usage(memall_dir if memall_dir.exists() else Path.home())
        free_gb = usage.free / (1024 ** 3)
        if free_gb < 1.0:
            _add("Disk Space", "WARN",
                 f"Only {free_gb:.1f} GB free (threshold: 1 GB)",
                 "memall backup clean --keep-daily 3")
        else:
            _add("Disk Space", "PASS", f"{free_gb:.1f} GB free")
    except Exception as e:
        _add("Disk Space", "WARN", f"Could not check: {e}")

    # ── Check 6: Database file size + fragmentation ──
    db_path = get_db_path()
    if db_path.exists():
        size_mb = db_path.stat().st_size / (1024 * 1024)
        try:
            if conn:
                freelist = conn.execute("PRAGMA freelist_count").fetchone()[0]
                page_count = conn.execute("PRAGMA page_count").fetchone()[0]
                frag_pct = (freelist / max(page_count, 1)) * 100
                if frag_pct > 20:
                    _add("DB Fragmentation", "WARN",
                         f"{size_mb:.1f} MB, {frag_pct:.0f}% fragmented",
                         "memall backup && VACUUM INTO new.db")
                else:
                    _add("DB Fragmentation", "PASS", f"{size_mb:.1f} MB, {frag_pct:.0f}% free pages")
        except Exception:
            _add("DB File", "PASS", f"{size_mb:.1f} MB")
    else:
        _add("DB File", "WARN", "Database file not found", "memall init")

    # ── Check 7: Backup directory + last backup time ──
    backup_dir = memall_dir / "backups"
    daily_dir = backup_dir / "daily"
    if daily_dir.exists():
        dailies = sorted(daily_dir.glob("*.db"), reverse=True)
        if dailies:
            latest = dailies[0]
            age_hours = (datetime.now().timestamp() - latest.stat().st_mtime) / 3600
            if age_hours > 48:
                _add("Backups", "WARN",
                     f"{len(dailies)} daily backups, latest: {age_hours:.0f}h ago",
                     "memall backup")
            else:
                _add("Backups", "PASS", f"{len(dailies)} daily backups, latest: {latest.name}")
        else:
            _add("Backups", "WARN", "Backup directory exists but no backups", "memall backup")
    else:
        _add("Backups", "WARN", "No backup directory", "memall backup")

    # ── Check 8: Index status ──
    try:
        from memall.graph.embeddings import index_status
        idx = index_status()
        pending = idx.get("pending", 0)
        if pending > 0:
            _add("Index", "WARN",
                 f"{idx['embedded']}/{idx['total_memories']} indexed ({pending} pending), model={idx['model']}",
                 "memall index build")
        else:
            _add("Index", "PASS", f"{idx['embedded']}/{idx['total_memories']} indexed, model={idx['model']}")
    except Exception as e:
        _add("Index", "WARN", f"Could not check: {e}", "memall index build")

    # ── Check 9: Exports directory ──
    exports_dir = memall_dir / "exports"
    if exports_dir.exists():
        export_files = list(exports_dir.glob("*"))
        _add("Exports", "PASS", f"{len(export_files)} exports in {exports_dir}")
    else:
        _add("Exports", "PASS", "No exports yet")

    # ── Check 10: Orphan/integrity fixes ──
    if conn:
        try:
            orphan_edges = conn.execute(
                "SELECT COUNT(*) as c FROM edges WHERE source_id NOT IN (SELECT id FROM memories)"
            ).fetchone()["c"]
            # Fix: supersedes is now JSON array, parse in Python
            sup_rows = conn.execute(
                "SELECT id, supersedes FROM memories WHERE supersedes IS NOT NULL AND supersedes != '[]'"
            ).fetchall()
            orphan_supersedes = 0
            for r in sup_rows:
                try:
                    sup_list = json.loads(r["supersedes"]) if isinstance(r["supersedes"], str) else []
                    for sid in sup_list:
                        if not conn.execute("SELECT 1 FROM memories WHERE id = ?", (sid,)).fetchone():
                            orphan_supersedes += 1
                except (json.JSONDecodeError, TypeError):
                    logger.warning("management_commands.py: silent error", exc_info=True)
            if orphan_edges == 0 and orphan_supersedes == 0:
                _add("Integrity", "PASS", "No orphan records")
            else:
                detail_parts = []
                if orphan_edges:
                    detail_parts.append(f"{orphan_edges} orphan edges")
                if orphan_supersedes:
                    detail_parts.append(f"{orphan_supersedes} orphan supersedes")
                _add("Integrity", "WARN", ", ".join(detail_parts), "memall doctor --fix")
        except Exception:
            logger.warning("management_commands.py: silent error", exc_info=True)

    # ── Check 11: FTS index ──
    if conn:
        try:
            fts_size = conn.execute("SELECT COUNT(*) as c FROM memories_fts").fetchone()["c"]
            expected = conn.execute("SELECT COUNT(*) as c FROM memories").fetchone()["c"]
            if fts_size == expected:
                _add("FTS Index", "PASS", f"{fts_size} entries, matching")
            else:
                _add("FTS Index", "WARN",
                     f"FTS has {fts_size} entries, expected {expected}",
                     "memall doctor --fix")
        except Exception:
            _add("FTS Index", "WARN", "FTS index not accessible")

    if conn:
        conn.close()

    # ── Fix mode ──
    if args.fix:
        fix_conn = get_conn()
        try:
            fix_conn.execute("DELETE FROM edges WHERE source_id NOT IN (SELECT id FROM memories)")
            # Fix: supersedes is JSON array — clean orphan IDs in Python
            sup_rows = fix_conn.execute(
                "SELECT id, supersedes FROM memories WHERE supersedes IS NOT NULL AND supersedes != '[]'"
            ).fetchall()
            for r in sup_rows:
                try:
                    sup_list = json.loads(r["supersedes"]) if isinstance(r["supersedes"], str) else []
                    clean = [s for s in sup_list if fix_conn.execute("SELECT 1 FROM memories WHERE id = ?", (s,)).fetchone()]
                    if len(clean) != len(sup_list):
                        fix_conn.execute("UPDATE memories SET supersedes = ? WHERE id = ?",
                                         (json.dumps(clean, ensure_ascii=False), r["id"]))
                except (json.JSONDecodeError, TypeError):
                    fix_conn.execute("UPDATE memories SET supersedes = '[]' WHERE id = ?", (r["id"],))
            fix_conn.commit()
            rebuild_fts(fix_conn)
            print("Fix applied: orphan records cleaned, FTS rebuilt.")
        finally:
            fix_conn.close()
        return

    # ── Summary table ──
    print(f"\nMemALL Health Check --- v{__version__}")
    print("=" * 70)
    for name, status, detail, fix in results:
        icon = {"PASS": "✓", "FAIL": "✗", "WARN": "⚠"}.get(status, "?")
        line = f"  {icon} [{status:4s}] {name:24s} {detail}"
        if status in ("FAIL", "WARN") and fix:
            line += f"\n{' ' * 39}-> fix: {fix}"
        print(line)

    # ── Summary counts ──
    pass_count = sum(1 for _, s, _, _ in results if s == "PASS")
    fail_count = sum(1 for _, s, _, _ in results if s == "FAIL")
    warn_count = sum(1 for _, s, _, _ in results if s == "WARN")
    print("\n" + "=" * 70)
    print(f"Summary: {pass_count} PASS | {fail_count} FAIL | {warn_count} WARN")
    if fail_count + warn_count > 0:
        print("Use --fix to auto-repair orphan records and rebuild FTS.")

    # ── Deep health check ──
    if getattr(args, "deep", False):
        try:
            from memall.core.health import collect
            h = collect()

            print(f"\n{'─' * 70}")
            print(f"  Deep Health: score {h['score']}/100")
            print(f"{'─' * 70}")
            print(f"  记忆总数:      {h['total_memories']}")
            print(f"  知识图谱覆盖率: {h['graph_coverage_pct']}%")
            print(f"  反思覆盖率:    {h['reflection_pct']}%")
            print(f"  数据库大小:    {h['db_size_mb']} MB")
            print(f"  Pipeline:      {'正常' if h['pipeline_fresh'] else '⚠ 超过 2 天未运行'}")
            print(f"  孤立记忆:      {h['isolated_count']}")
            print(f"  零访问记忆:    {h['zero_access_count']}")
            print(f"  超时讨论:      {h['stale_discussions']}")
            print(f"  待索引嵌入:    {h['pending_embeddings']}")
            print(f"  FTS 索引:      {'正常' if h['fts_ok'] else '⚠ 不一致'}")
            print(f"  待应用迁移:    {h['pending_migrations']}")
            if h["issues"]:
                print(f"\n  ⚠ 发现 {len(h['issues'])} 个问题:")
                for issue in h["issues"]:
                    print(f"    · {issue}")
            if h["tips"]:
                print(f"\n  💡 建议:")
                for tip in h["tips"]:
                    print(f"    · {tip}")
        except Exception as e:
            print(f"\n  Deep health check failed: {e}")


# ──────────────────────────────────────────────
# cmd_migrate
# ──────────────────────────────────────────────

def cmd_migrate(args):
    if args.apply:
        # GAP-7: Apply pending schema migrations
        from memall.migrations import run_migrations as run_formal, get_pending_migrations
        conn = get_conn()
        pending = get_pending_migrations(conn)
        if not pending:
            print("All migrations already applied.")
            conn.close()
            return
        print(f"Applying {len(pending)} migration(s): {', '.join(pending)}")
        result = run_formal(conn, db_path=get_db_path())
        conn.close()
        applied_count = result.get("applied", 0)
        errors = result.get("errors", 0)
        for r in result.get("results", []):
            status_mark = "[OK]" if r["status"] == "ok" else f"[{r['status'].upper()}]"
            mid = r.get("migration_id", "?")
            print(f"  {status_mark} {mid}")
        if errors:
            print(f"  [ERR] {errors} migration(s) failed")
        print(f"Done --- {applied_count} applied, {errors} failed.")
        return

    if args.status:
        from memall.migrations import get_migration_status
        conn = get_conn()
        status = get_migration_status(conn)
        conn.close()
        print("Migration Status:")
        print(f"  Schema version: {status.get('total_applied', 0)}")
        applied = status.get('applied', [])
        if isinstance(applied, list):
            names = [a.get('id', a) if isinstance(a, dict) else str(a) for a in applied]
            print(f"  Applied: {', '.join(names) if names else '(none)'}")
        else:
            print(f"  Applied: {applied}")
        pending = status.get('pending', [])
        if pending:
            print(f"  Pending: {', '.join(pending)}  ->  memall migrate --apply")
        else:
            print(f"  Pending: (none)")
        return

    # Legacy data migration
    source = Path(args.source)
    if not source.exists():
        print(f"error: source db not found: {source}", file=sys.stderr)
        sys.exit(1)

    init_db()
    import sqlite3
    src = sqlite3.connect(str(source), timeout=10)
    src.row_factory = sqlite3.Row
    try:
        tables = [r["name"] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]

        mem_count = 0
        edge_count = 0
        agent_count = 0

        if "facts" in tables:
            rows = src.execute("SELECT * FROM facts ORDER BY id").fetchall()
            for row in rows:
                bm = _rget(row, "body_md")
                content = row["content"]
                if bm:
                    content += "\n" + bm
                inp = MemoryInput(
                    content=content,
                    level=_rget(row, "level", "P2"),
                    owner="admin",
                    agent_name=_rget(row, "agent_name") or _rget(row, "agent") or _rget(row, "source"),
                    subject=_rget(row, "title") or _rget(row, "subject"),
                    project=_rget(row, "project"),
                    category=_rget(row, "category", "general"),
                    summary=_rget(row, "summary"),
                    occurred_at=_rget(row, "created_at"),
                    confidence=float(_rget(row, "confidence", "1.0") or 1.0),
                    metadata=_rget(row, "metadata", "{}"),
                )
                if inp.content.strip():
                    try:
                        capture(inp)
                        mem_count += 1
                    except Exception:
                        logger.warning("management_commands.py: silent error", exc_info=True)

        if "memory_edges" in tables:
            rows = src.execute("SELECT * FROM memory_edges").fetchall()
            for row in rows:
                try:
                    connect(
                        source_id=row["source_id"],
                        target_id=row["target_id"],
                        relation_type=_rget(row, "relation_type", "related"),
                        weight=float(_rget(row, "weight", "1.0") or 1.0),
                    )
                    edge_count += 1
                except Exception:
                    logger.warning("management_commands.py: silent error", exc_info=True)

        if "agent_metadata" in tables:
            conn = get_conn()
            rows = src.execute("SELECT * FROM agent_metadata").fetchall()
            for row in rows:
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO identities (agent_name, agent_type, description, icon, status) VALUES (?,?,?,?,?)",
                        (
                            _rget(row, "agent_id", ""),
                            _rget(row, "agent_type", "ai"),
                            f"{_rget(row, 'display_name')} - {_rget(row, 'description')}",
                            _rget(row, "icon", "🤖"),
                            "active",
                        ),
                    )
                    agent_count += 1
                except Exception:
                    logger.warning("management_commands.py: silent error", exc_info=True)
            conn.commit()
            conn.close()

        rebuild_fts_cmd()

        print(f"migrated: {mem_count} memories, {edge_count} edges, {agent_count} agents from {source}")
    finally:
        src.close()


# ──────────────────────────────────────────────
# cmd_trust / cmd_identity_trust / cmd_identity_untrust
# ──────────────────────────────────────────────

def cmd_trust(args):
    conn = get_conn()
    try:
        mem = conn.execute("SELECT id, owner, visibility FROM memories WHERE id = ?", (args.id,)).fetchone()
        if not mem:
            print(f"memory {args.id} not found", file=sys.stderr)
            sys.exit(1)
        conn.execute("UPDATE memories SET visibility = ?, updated_at = datetime('now') WHERE id = ?", (args.level, args.id))
        conn.commit()
        print(f"memory {args.id} visibility: {mem['visibility']} -> {args.level}")
    finally:
        conn.close()


def cmd_identity_trust(args):
    conn = get_conn()
    try:
        target = conn.execute("SELECT agent_name, trusted_by FROM identities WHERE agent_name = ?", (args.name,)).fetchone()
        if not target:
            print(f"identity '{args.name}' not found", file=sys.stderr)
            sys.exit(1)
        trusted = json.loads(target["trusted_by"] or "[]")
        viewer = "admin"
        if viewer not in trusted:
            trusted.append(viewer)
            conn.execute("UPDATE identities SET trusted_by = ? WHERE agent_name = ?", (json.dumps(trusted, ensure_ascii=False), args.name))
            conn.commit()
            print(f"you now trust '{args.name}'")
        else:
            print(f"you already trust '{args.name}'")
    finally:
        conn.close()


def cmd_identity_untrust(args):
    conn = get_conn()
    try:
        target = conn.execute("SELECT agent_name, trusted_by FROM identities WHERE agent_name = ?", (args.name,)).fetchone()
        if not target:
            print(f"identity '{args.name}' not found", file=sys.stderr)
            sys.exit(1)
        trusted = json.loads(target["trusted_by"] or "[]")
        viewer = "admin"
        if viewer in trusted:
            trusted.remove(viewer)
            conn.execute("UPDATE identities SET trusted_by = ? WHERE agent_name = ?", (json.dumps(trusted, ensure_ascii=False), args.name))
            conn.commit()
            print(f"you no longer trust '{args.name}'")
        else:
            print(f"you don't trust '{args.name}'")
    finally:
        conn.close()


# ──────────────────────────────────────────────
# cmd_identity
# ──────────────────────────────────────────────

def cmd_identity(args):
    """Show L1/L7 identity and preference profile for an agent."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT identity_profile, profile_json, persona_updated_at, agent_type FROM identities WHERE LOWER(agent_name) = LOWER(?)",
            (args.agent_name,),
        ).fetchone()
        if not row:
            print(f"Agent '{args.agent_name}' not found in identities table.")
            print("Hint: run 'memall pipeline' first to extract identity traits.")
            return

        id_profile = json.loads(row["identity_profile"]) if isinstance(row["identity_profile"], str) and row["identity_profile"] else {}
        pj = json.loads(row["profile_json"]) if isinstance(row["profile_json"], str) and row["profile_json"] else {}
        updated = (row["persona_updated_at"] or "")[:19]

        l1_list = id_profile.get("l1_identity", []) if isinstance(id_profile, dict) else []
        l7_list = id_profile.get("l7_preferences", []) if isinstance(id_profile, dict) else []
        proto = pj.get("prototype", {}) if isinstance(pj, dict) else {}
        feats = pj.get("features", {}) if isinstance(pj, dict) else {}

        print(f"\n{'='*50}")
        print(f"  Agent: {args.agent_name} ({row['agent_type']})")
        print(f"  更新: {updated}")
        print(f"{'='*50}")

        # Persona header
        if proto:
            print(f"\n  认知类型: {proto.get('cn','?')} ({proto.get('en','?')})")

        # L1
        if l1_list:
            print(f"\n  ── L1 身份信息 ({len(l1_list)}条) ──")
            for t in l1_list:
                tp = t.get("type", "?")
                snippet = t.get("snippet", "")
                print(f"    [{tp}] {snippet}")
        else:
            print(f"\n  ── L1 身份信息: (暂无) ──")

        # L7
        if l7_list:
            print(f"\n  ── L7 偏好信息 ({len(l7_list)}条) ──")
            for t in l7_list:
                tp = t.get("type", "?")
                snippet = t.get("snippet", "")
                print(f"    [{tp}] {snippet}")
        else:
            print(f"\n  ── L7 偏好信息: (暂无) ──")

        # Stats
        if feats:
            print(f"\n  ── 认知特征 ──")
            for k, v in [("自信指数", "certainty_score"), ("决策密度", "decision_ratio"),
                          ("提问倾向", "question_ratio"), ("知识广度", "domain_breadth")]:
                val = feats.get(v, 0)
                if val:
                    fmt = f"{val*100:.0f}%" if v != "domain_breadth" else str(val)
                    print(f"    {k}: {fmt}")

        print()
    finally:
        conn.close()


# ──────────────────────────────────────────────
# cmd_graph_visualize
# ──────────────────────────────────────────────

def cmd_graph_visualize(args):
    from memall.graph.visualize import generate_graph
    result = generate_graph(center_id=args.center, limit=args.limit, format=args.format,
                            layout=args.layout, output_path=args.output or "")
    print(f"Graph generated: {result['path']}")
    print(f"  {result['nodes']} nodes, {result['edges']} edges")
    if result.get('center_id'):
        print(f"  Center: #{result['center_id']}")


# ──────────────────────────────────────────────
# cmd_index
# ──────────────────────────────────────────────

def cmd_index(args):
    if args.action == "build":
        from memall.graph.embeddings import build_index
        result = build_index(batch_size=args.batch)
        if "error" in result:
            print(f"Index build failed: {result['error']}", file=sys.stderr)
            sys.exit(1)
        print(f"Index build: {result.get('new', 0)} new embeddings ({result['embedded']}/{result['total']} total)")
    elif args.action == "status":
        from memall.graph.embeddings import index_status
        result = index_status()
        print(f"Total memories: {result['total_memories']}")
        print(f"Embedded: {result['embedded']}")
        print(f"Pending: {result['pending']}")
        print(f"Model: {result['model']} ({result['dims']}d)")


# ──────────────────────────────────────────────
# cmd_retrieve
# ──────────────────────────────────────────────

def cmd_retrieve(args):
    from memall.graph.retrieve import retrieve
    result = retrieve(args.query, mode=args.mode, top_k=args.top)
    if "error" in result:
        print(f"Retrieve failed: {result['error']}", file=sys.stderr)
        sys.exit(1)
    if not result.get("results"):
        print("No results found.")
        return
    print(f"Mode: {result['mode']} | Total candidates: {result.get('total', 0)}")
    for r in result["results"]:
        print(f"  #{r['memory_id']} [{r.get('source','?')}] score={r['score']:.4f}")
        print(f"    {r['content'][:120]}")
    if result.get("graph_expansions"):
        print(f"  Graph expansions: {result['graph_expansions']}")


# ──────────────────────────────────────────────
# cmd_onboarding
# ──────────────────────────────────────────────

def cmd_onboarding(args):
    from memall.onboarding import start, status, reset, complete

    if args.action == "start":
        result = start(step=args.step)
        if result.get("status") == "already_completed":
            print(result["message"])
    elif args.action == "status":
        s = status()
        if s["completed"]:
            print(f"新手引导：已完成（{s.get('completed_at','?')}）")
        else:
            print(f"新手引导：步骤 {s['current_step']}/5（未完成）")
            print(f"  Agent: {s.get('agent_name','未设置')}")
            print(f"  开始时间: {s.get('started_at','?')}")
    elif args.action == "reset":
        result = reset()
        print(result["message"])
    elif args.action == "complete":
        result = complete()
        print(result["message"])


# ──────────────────────────────────────────────
# cmd_serve
# ──────────────────────────────────────────────

def cmd_serve(args):
    if args.http:
        from memall.api.server import serve_http
        serve_http(port=args.port)
    else:
        from memall.mcp.server import serve
        serve()


# ──────────────────────────────────────────────
# cmd_db
# ──────────────────────────────────────────────

def cmd_db(args):
    """CLI handler for `memall db` — database maintenance (Phase 21)."""
    from memall.core.db import optimize_db, db_stats, vacuum_db

    action = getattr(args, "action", None)

    if action == "optimize":
        result = optimize_db()
        v = result["vacuumed"]
        print(f"ANALYZE:  done")
        print(f"VACUUM:   {v['before_mb']} MB -> {v['after_mb']} MB (reclaimed {v['reclaimed_mb']} MB)")
        print(f"OPTIMIZE: done")

    elif action == "stats":
        stats = db_stats()
        print(f"Database: {stats['db_path']}")
        print(f"File size: {stats['file_size_mb']} MB")
        print(f"WAL size:  {stats['wal_size_mb']} MB")
        print(f"Tables:")
        for name, cnt in stats["tables"].items():
            print(f"  {name}: {cnt} rows")

    elif action == "vacuum":
        result = vacuum_db()
        print(f"Before:  {result['before_mb']} MB")
        print(f"After:   {result['after_mb']} MB")
        print(f"Reclaimed: {result['reclaimed_mb']} MB")

    else:
        print("Usage: memall db {optimize|stats|vacuum}")


# ──────────────────────────────────────────────
# cmd_mcp_connect — 一键注册 MCP 服务器
# ──────────────────────────────────────────────

def _detect_claude_cli() -> str | None:
    """Find claude CLI binary. Checks PATH then common install locations."""
    claude = shutil.which("claude")
    if claude:
        return claude
    # Common install locations on Windows
    for p in [
        Path.home() / "AppData" / "Local" / "Claude-3p" / "claude-code" / "2.1.170" / "claude.exe",
        Path.home() / "AppData" / "Local" / "Claude-3p" / "claude-code" / "claude.exe",
    ]:
        if p.exists():
            return str(p)
    return None


def _detect_opencode_config() -> Path | None:
    """Detect OpenCode config file."""
    for p in [
        Path.home() / ".config" / "opencode" / "opencode.jsonc",
        Path.home() / ".config" / "opencode" / "opencode.json",
    ]:
        if p.exists():
            return p
    return None


def cmd_mcp_connect(args):
    """CLI handler for `memall mcp connect` — one-command MCP setup."""
    python_exe = sys.executable or "python"
    memall_module = "memall.mcp.server"

    print("🔍 检测可用客户端...")

    # ── Try Claude Code first ──
    claude_path = _detect_claude_cli()
    if claude_path:
        print(f"📝 检测到 Claude Code: {claude_path}")
        print(f"📝 注册 MCP 服务器 memall...")

        result = subprocess.run(
            [claude_path, "mcp", "add", "memall", "-s", "user",
             "--", python_exe, "-m", memall_module],
            capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace",
        )

        if result.returncode == 0 or (result.stderr and "already exists" in result.stderr):
            # Verify
            verify = subprocess.run(
                [claude_path, "mcp", "list"],
                capture_output=True, text=True, timeout=15, encoding="utf-8", errors="replace",
            )
            stdout = verify.stdout or ""
            if "memall" in stdout and "Connected" in stdout:
                print("✅ memall 已注册: python -m memall.mcp.server - ✔ Connected")
                print("🔄 请重启 Claude Code 使配置生效")
                return
            elif "memall" in stdout:
                print("✅ memall 已注册，但连接状态异常，请运行 claude mcp list 检查")
                return
            else:
                print(f"⚠️  注册完成但验证未通过，请手动运行: claude mcp list")
                return
        else:
            print(f"⚠️  Claude Code 注册失败: {(result.stderr or '')[:200]}")

    # ── No Claude Code, try OpenCode ──
    opencode_config = _detect_opencode_config()
    if opencode_config:
        print(f"📝 检测到 OpenCode: {opencode_config}")
        print(f"📝 写入 MCP 配置...")
        try:
            existing = json.loads(opencode_config.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
        mcp = existing.setdefault("mcp", {})
        mcp["memall"] = {
            "type": "local",
            "command": [python_exe, "-m", memall_module],
            "enabled": True,
            "timeout": 60000,
        }
        opencode_config.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"✅ 已写入 {opencode_config}")
        print(f"🔄 请重启 OpenCode 使配置生效")
        return

    # ── No supported client found ──
    print("❌ 未检测到支持的客户端（Claude Code / OpenCode）")
    print("   请确认已安装 Claude Code 并可通过命令行调用")
    print("   或手动将以下配置加入您的 MCP 客户端：")
    print()
    config_entry = {
        "memall": {
            "command": python_exe,
            "args": ["-m", "memall.mcp.server"],
        }
    }
    print(json.dumps({"mcpServers": config_entry}, indent=2, ensure_ascii=False))


# ── Decision Arcs ──

def cmd_arcs(args):
    """Query or manage decision arcs."""
    from memall.core.db import get_conn

    conn = get_conn()
    try:
        if args.action == "list":
            status_filter = args.status
            agent_filter = args.agent

            where = ["level = 'L4' AND arc_status IS NOT NULL"]
            params = []
            if status_filter:
                where.append("arc_status = ?")
                params.append(status_filter)
            if agent_filter:
                where.append("agent_name = ?")
                params.append(agent_filter)

            rows = conn.execute(
                f"SELECT id, subject, agent_name, arc_status, created_at "
                f"FROM memories WHERE {' AND '.join(where)} ORDER BY created_at DESC "
                f"LIMIT ?",
                (*params, args.limit),
            ).fetchall()

            if not rows:
                print("No decision arcs found.")
                return

            print(f"{'ID':>5} {'Status':<14} {'Agent':<12} {'Subject'}")
            print("-" * 80)
            for r in rows:
                badge = {"open": "open", "in_progress": "进行中", "closed": "已闭环"}.get(r["arc_status"], r["arc_status"])
                print(f"{r['id']:>5} {badge:<14} {(r['agent_name'] or '-'):<12} {(r['subject'] or '')[:50]}")

            print(f"\nTotal: {len(rows)}")

        elif args.action == "close":
            from memall.core.db import get_conn
            conn.execute(
                "UPDATE memories SET arc_status = 'closed' WHERE id = ? AND level = 'L4'",
                (args.id,),
            )
            conn.commit()
            if conn.changes > 0:
                print(f"Decision arc #{args.id} closed.")
            else:
                print(f"Memory #{args.id} not found or not a decision.")

        elif args.action == "stale":
            from datetime import datetime, timezone, timedelta
            cutoff = (datetime.now(timezone.utc) - timedelta(days=21)).isoformat()
            rows = conn.execute(
                "SELECT id, subject, agent_name, created_at FROM memories "
                "WHERE level = 'L4' AND arc_status = 'open' AND created_at < ? "
                "AND id NOT IN ("
                "  SELECT DISTINCT source_id FROM edges WHERE relation_type != 'deleted' "
                "  AND target_id IN (SELECT id FROM memories WHERE level = 'L5')"
                "  UNION "
                "  SELECT DISTINCT target_id FROM edges WHERE relation_type != 'deleted' "
                "  AND source_id IN (SELECT id FROM memories WHERE level = 'L5')"
                ") ORDER BY created_at",
                (cutoff,),
            ).fetchall()
            if not rows:
                print("No stale decisions (>21d with no activity).")
                return
            print(f"{'ID':>5} {'Agent':<12} {'Since':<12} Subject")
            print("-" * 80)
            for r in rows:
                since = (r["created_at"] or "")[:10]
                print(f"{r['id']:>5} {(r['agent_name'] or '-'):<12} {since:<12} {(r['subject'] or '')[:50]}")
            print(f"\nStale: {len(rows)}")

    finally:
        conn.close()


# ──────────────────────────────────────────────
# cmd_import — 导入记忆（JSON / JSONL）
# ──────────────────────────────────────────────

def cmd_import(args):
    """Import memories from a JSON or JSONL export file."""
    path = Path(args.file)
    if not path.exists():
        print(f"error: file not found: {path}", file=sys.stderr)
        sys.exit(1)

    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        memories = []
        edges = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                if obj.get("type") == "memory":
                    memories.append(obj)
                elif obj.get("type") == "edge":
                    edges.append(obj)

        if not memories and not edges:
            print("No memories or edges found in JSONL file.")
            return

        bundle = {"memories": memories, "edges": edges, "agent_name": "imported"}
        from memall.gateway import import_bundle
        result = import_bundle(bundle)

    elif suffix == ".json":
        bundle = json.loads(path.read_text(encoding="utf-8"))
        # Normalise: gateway expects "memories" key
        memories = bundle.get("memories", [])
        edges = bundle.get("edges", []) or []
        # Extract edges from relations inside each memory (export format)
        if not edges:
            edges = []
            for m in memories:
                for rel in m.pop("relations", []):
                    edges.append({
                        "source_id": m["id"],
                        "target_id": rel.get("target_id"),
                        "relation_type": rel.get("relation"),
                        "weight": rel.get("weight", 1.0),
                    })
        bundle.setdefault("edges", edges)
        bundle.setdefault("agent_name", "imported")
        from memall.gateway import import_bundle
        result = import_bundle(bundle)

    else:
        print(f"Unsupported file format: {suffix}. Use .json or .jsonl.", file=sys.stderr)
        sys.exit(1)

    mem_count = result.get("imported_memories", 0)
    edge_count = result.get("imported_edges", 0)
    identity = result.get("identity_updated", False)
    print(f"Imported: {mem_count} memories, {edge_count} edges" + (", identity updated" if identity else ""))


# ──────────────────────────────────────────────
# cmd_sync — 增量同步
# ──────────────────────────────────────────────

SYNC_STATE_PATH = Path.home() / ".memall" / "sync_state.json"


def _read_sync_state():
    if SYNC_STATE_PATH.exists():
        try:
            return json.loads(SYNC_STATE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _write_sync_state(state: dict):
    SYNC_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    SYNC_STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def cmd_sync(args):
    """Incremental sync from a JSONL export file.

    Two-phase flow:
        1. Export side: memall export --since <last_sync> --format jsonl
        2. Import side: memall sync --from <file>

    Uses content_hash dedup for idempotent re-import.
    Tracks last sync time in ~/.memall/sync_state.json.
    """
    source = Path(args.source)
    if not source.exists():
        print(f"error: source file not found: {source}", file=sys.stderr)
        sys.exit(1)

    state = _read_sync_state()

    memories = []
    edges = []
    with open(source, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if obj.get("type") == "memory":
                memories.append(obj)
            elif obj.get("type") == "edge":
                edges.append(obj)

    if not memories:
        print("No new memories to sync (empty or all previously synced).")
        return

    bundle = {"memories": memories, "edges": edges, "agent_name": "sync"}
    from memall.gateway import import_bundle
    result = import_bundle(bundle)

    mem_count = result.get("imported_memories", 0)
    edge_count = result.get("imported_edges", 0)

    timestamps = [m.get("updated_at", "") for m in memories if m.get("updated_at")]
    if timestamps:
        latest = max(timestamps)
        state["last_sync"] = latest
    _write_sync_state(state)

    print(f"Sync complete: {mem_count} new memories, {edge_count} new edges")
    print(f"  Last sync: {state.get('last_sync', '?')}")