import time
import threading
import logging
import os
import sys
from pathlib import Path
from datetime import datetime, timezone
from memall.core.db import pool_conn, init_db
from memall.pipeline import run_pipeline
from memall.pipeline.forget import forget_step
from memall.pipeline.security import audit_sensitive, security_score
from memall.scheduler.agent_round import agent_round
from memall.config import get_config

logger = logging.getLogger("memall.scheduler")
PID_FILE = Path.home() / ".memall" / "scheduler.pid"
HEARTBEAT_LOG = Path.home() / ".memall" / "heartbeat.log"

INTERVAL_HEARTBEAT = get_config("scheduler.heartbeat_interval", 300)
INTERVAL_PIPELINE = get_config("scheduler.pipeline_interval", 21600)
INTERVAL_DOCTOR = get_config("scheduler.doctor_interval", 3600)
INTERVAL_FORGET = get_config("scheduler.forget_interval", 86400)
INTERVAL_SECURITY = get_config("scheduler.security_interval", 86400)
INTERVAL_PROXY_SESSION = get_config("scheduler.proxy_session_interval", 300)
PROXY_AGENTS = get_config("scheduler.proxy_agents", ["claude", "opencode", "zcode"])
MISSED_HEARTBEAT_LIMIT = get_config("scheduler.missed_heartbeat_limit", 7)


class Scheduler:
    def __init__(self):
        self._running = False
        self._thread = None
        self._last_pipeline = 0
        self._last_doctor = 0
        self._last_forget = 0
        self._last_security = 0
        self._last_agent_round = 0

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info("scheduler started")

    def stop(self):
        self._running = False
        logger.info("scheduler stopped")

    def _loop(self):
        init_db()
        while self._running:
            try:
                self._tick()
            except Exception as e:
                logger.error(f"scheduler tick error: {e}")
            time.sleep(60)

    def _tick(self):
        now = time.time()

        self._heartbeat()

        if now - self._last_pipeline >= INTERVAL_PIPELINE:
            result = run_pipeline()
            logger.info(f"pipeline run: {result}")
            self._last_pipeline = now

        if now - self._last_doctor >= INTERVAL_DOCTOR:
            self._doctor_check()
            self._last_doctor = now

        if now - self._last_forget >= INTERVAL_FORGET:
            result = forget_step()
            logger.info(f"daily forget: {result}")
            self._last_forget = now

        if now - self._last_security >= INTERVAL_SECURITY:
            try:
                score = security_score()
                logger.info(f"daily security score: {score.get('score', '?')}")
            except Exception as e:
                logger.warning(f"daily security audit error: {e}")
            self._last_security = now

        if now - self._last_agent_round >= INTERVAL_PROXY_SESSION:
            try:
                stats = agent_round()
                logger.info(f"agent round: {stats}")
            except Exception as e:
                logger.warning(f"agent round error: {e}")
            self._last_agent_round = now

    def _heartbeat(self):
        with pool_conn() as conn:
            now_str = datetime.now(timezone.utc).isoformat()
            agents = conn.execute("SELECT agent_name FROM identities WHERE status = 'active'").fetchall()
            for ag in agents:
                name = ag["agent_name"]
                conn.execute(
                    "UPDATE identities SET last_heartbeat = ?, status = 'active' WHERE agent_name = ?",
                    (now_str, name),
                )

            # Fix 2026-06-10: 不再向 memories 表插入 category='heartbeat' 记录
            # 原因：每分钟一条纯垃圾 ("heartbeat" 字面)，3 天累积 3,016 条 + 3,888,140 关联 edges
            # 解决方案：identities.last_heartbeat 已经记录了 agent 在线状态，无需重复
            # 之前 3,016 条 heartbeat 已清理（memories -3,016, edges -3,888,140, 318MB 回收）

            conn.commit()

        # Log to heartbeat.log
        try:
            with open(str(HEARTBEAT_LOG), "a") as f:
                f.write(f"[{now_str[:19]}] heartbeat\n")
        except Exception:
            logger.warning("scheduler.py: silent error", exc_info=True)

    def _doctor_check(self):
        with pool_conn() as conn:
            cutoff = datetime.now(timezone.utc).timestamp() - (MISSED_HEARTBEAT_LIMIT * INTERVAL_HEARTBEAT)
            cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()

            offline = conn.execute(
                "SELECT agent_name, last_heartbeat FROM identities WHERE last_heartbeat < ? AND status = 'active'",
                (cutoff_iso,),
            ).fetchall()

            for ag in offline:
                logger.warning(f"agent offline detected: {ag['agent_name']}, last heartbeat: {ag['last_heartbeat']}")
                conn.execute(
                    "UPDATE identities SET status = 'offline' WHERE agent_name = ?",
                    (ag["agent_name"],),
                )
            conn.commit()

    def status(self) -> dict:
        with pool_conn() as conn:
            agents = conn.execute("SELECT agent_name, agent_type, status, last_heartbeat FROM identities").fetchall()
            mem_count = conn.execute("SELECT COUNT(*) as c FROM memories").fetchone()["c"]
            edge_count = conn.execute("SELECT COUNT(*) as c FROM edges").fetchone()["c"]
            return {
                "running": self._running,
                "memories": mem_count,
                "edges": edge_count,
                "agents": [dict(a) for a in agents],
                "last_pipeline": self._last_pipeline,
                "last_forget": self._last_forget,
                "last_security": self._last_security,
                "last_agent_round": self._last_agent_round,
                "agent_interval": INTERVAL_PROXY_SESSION,
                "proxy_agents": PROXY_AGENTS,
            }


def run_daemon():
    init_db()
    s = Scheduler()
    s._running = True
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))
    logger.info(f"scheduler daemon started (pid={os.getpid()})")
    try:
        while s._running:
            try:
                s._tick()
            except Exception as e:
                logger.error(f"scheduler tick error: {e}")
            time.sleep(60)
    finally:
        if PID_FILE.exists():
            PID_FILE.unlink()
        logger.info("scheduler daemon stopped")


def run_daemon_with_watchdog():
    """Run scheduler daemon with auto-restart if it crashes.
    
    This function is intended to be run as a subprocess by daemon_start().
    It writes its PID to the scheduler PID file, spawns the scheduler
    as a child subprocess, and restarts it if it exits unexpectedly.
    """
    import subprocess
    import time
    from datetime import datetime, timezone
    
    log_path = PID_FILE.parent / "scheduler_watchdog.log"
    stderr_path = PID_FILE.parent / "scheduler_err.log"
    
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))
    
    scheduler_script = "from memall.scheduler.scheduler import run_daemon_with_watchdog; run_daemon_with_watchdog()"
    restart_count = 0
    
    while True:
        stderr_handle = open(str(stderr_path), "a", encoding="utf-8")
        try:
            proc = subprocess.Popen(
                [sys.executable, "-c", scheduler_script],
                stdout=subprocess.DEVNULL,
                stderr=stderr_handle,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
            )
            
            with open(str(log_path), "a") as f:
                f.write("[{}] scheduler started (pid={})\n".format(datetime.now(timezone.utc).isoformat(), proc.pid))
            
            exit_code = proc.wait()
            
            with open(str(log_path), "a") as f:
                f.write("[{}] scheduler exited (pid={}, code={}), restarting in 5s\n".format(
                    datetime.now(timezone.utc).isoformat(), proc.pid, exit_code))
        finally:
            stderr_handle.close()
        
        restart_count += 1
        delay = min(30, 5 + restart_count // 3)
        time.sleep(delay)

def _validate_our_pid(pid: int) -> bool:
    """Check that *pid* is our scheduler process, not an injected foreign PID."""
    import subprocess
    try:
        result = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH", "/FI", f"PID eq {pid}"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return False
        # tasklist CSV output: "python.exe","1234","Console","1","...","Running"
        parts = result.stdout.strip().split(",")
        if len(parts) < 2:
            return False
        proc_name = parts[0].strip('"').lower() if parts else ""
        return "python" in proc_name or "memall" in proc_name
    except (ValueError, OSError, subprocess.TimeoutExpired):
        return False


def daemon_start():
    import subprocess
    if PID_FILE.exists():
        raw = PID_FILE.read_text().strip()
        try:
            pid = int(raw)
            if pid <= 0:
                raise ValueError
            if not _validate_our_pid(pid):
                logger.warning(f"stale PID file {PID_FILE}; removing")
                PID_FILE.unlink(missing_ok=True)
            else:
                print(f"scheduler already running (pid={pid})")
                return False
        except (ValueError, TypeError):
            logger.warning(f"invalid PID in {PID_FILE}; removing")
            PID_FILE.unlink(missing_ok=True)
    script = "from memall.scheduler.scheduler import run_daemon_with_watchdog; run_daemon_with_watchdog()"
    err_log = str(PID_FILE.parent / "scheduler_err.log")
    stderr_handle = open(err_log, "a", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.DEVNULL,
        stderr=stderr_handle,
        creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
    )
    print(f"scheduler started (pid={proc.pid})")
    return True


def daemon_stop():
    import subprocess
    if not PID_FILE.exists():
        print("scheduler not running")
        return False
    raw = PID_FILE.read_text().strip()
    try:
        pid = int(raw)
        if pid <= 0:
            raise ValueError
    except (ValueError, TypeError):
        print(f"invalid PID in {PID_FILE}")
        PID_FILE.unlink(missing_ok=True)
        return False
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], check=True, capture_output=True)
        print(f"scheduler stopped (pid={pid})")
    except subprocess.CalledProcessError:
        print(f"scheduler pid={pid} not found, cleaning up")
    PID_FILE.unlink(missing_ok=True)
    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run_daemon()
