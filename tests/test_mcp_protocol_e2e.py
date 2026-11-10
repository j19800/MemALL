"""Real MCP protocol end-to-end test (stdio JSON-RPC).

Unlike the other suites (which call `handle_call` / tool functions / the CLI),
this starts the actual server process (`python -m memall.mcp.server`) and
speaks MCP over stdin/stdout: initialize -> notifications/initialized ->
tools/list -> tools/call.

That layer is where `agent_name` is bound to the SESSION identity during the
handshake, which is invisible to adapter-level tests — a real MCP run is what
surfaced the rollback CAS bug (raw-string metadata comparison refused valid
rollbacks after a benign JSON key reorder).
"""
import json
import os
import queue
import subprocess
import sys
import threading
import time

import pytest
from pathlib import Path

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _MCP:
    def __init__(self, db_path, agent, home):
        env = dict(os.environ)
        env.update({
            "PYTHONPATH": f"{REPO}:{REPO}/src",
            "MEMALL_DB_PATH": db_path,
            "MEMALL_AGENT_NAME": agent,
            "HOME": home,
        })
        self.p = subprocess.Popen(
            [sys.executable, "-m", "memall.mcp.server"],
            cwd=REPO, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1,
        )
        self._id = 0
        self._q: "queue.Queue[dict]" = queue.Queue()
        self._err: list[str] = []
        threading.Thread(target=self._pump, daemon=True).start()
        threading.Thread(target=self._pump_err, daemon=True).start()

    def _pump(self):
        for line in self.p.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                self._q.put(json.loads(line))
            except Exception:
                pass

    def _pump_err(self):
        for line in self.p.stderr:
            self._err.append(line.rstrip())

    def send(self, method, params=None, notify=False):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if not notify:
            self._id += 1
            msg["id"] = self._id
        self.p.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self.p.stdin.flush()
        return msg.get("id")

    def wait(self, rid, timeout=180):
        end = time.time() + timeout
        while time.time() < end:
            try:
                m = self._q.get(timeout=max(0.1, end - time.time()))
            except queue.Empty:
                break
            if m.get("id") == rid:
                return m
        raise AssertionError(f"no MCP response for id={rid}; stderr tail={self._err[-5:]}")

    def call(self, method, params=None, timeout=180):
        return self.wait(self.send(method, params), timeout)

    def tool(self, name, arguments):
        r = self.call("tools/call", {"name": name, "arguments": arguments})
        res = r.get("result") or {}
        txt = (res.get("content") or [{}])[0].get("text", "")
        try:
            return json.loads(txt)
        except Exception:
            return {"_raw": txt[:300], "_isError": res.get("isError")}

    def close(self):
        try:
            self.p.stdin.close()
        except Exception:
            pass
        try:
            self.p.wait(timeout=10)
        except Exception:
            self.p.kill()


@pytest.fixture()
def mcp_env(tmp_path):
    """Isolated DB + HOME so the child server reads a controlled config."""
    from memall.core import db as core_db
    db_path = str(tmp_path / "data.db")
    home = tmp_path / "home"
    (home / ".memall").mkdir(parents=True)
    (home / ".memall" / "memall.yaml").write_text(json.dumps({
        "quality": {"utility": {"tracking_enabled": True}},
        "security": {"supervisor_agents": ["mcp_sup"]},
        "identity": {"human_owner": "mcp_sup"},
        "maintenance": {"enabled": True, "conflict_review": True, "snapshot_before_write": True},
    }), encoding="utf-8")

    # Create the schema in the SAME file the child will open.
    orig = core_db.DB_PATH
    core_db.DB_PATH = Path(db_path)
    core_db._global_pool = None
    from memall.core.db import init_db
    init_db(migrate=True)
    core_db.DB_PATH = orig
    core_db._global_pool = None
    return {"db": db_path, "home": str(home)}


def test_mcp_stdio_handshake_and_tools_list(mcp_env):
    c = _MCP(mcp_env["db"], "mcp_e2e_agent", mcp_env["home"])
    try:
        res = c.call("initialize", {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "pytest-mcp", "version": "1"},
            "agent_name": "mcp_e2e_agent",
        })["result"]
        assert res.get("serverInfo", {}).get("name") == "memall"
        assert res.get("protocolVersion") == "2025-03-26"
        assert "tools" in (res.get("capabilities") or {})
        c.send("notifications/initialized", {}, notify=True)

        tools = c.call("tools/list")["result"]["tools"]
        names = {t["name"] for t in tools}
        for required in ("memall_read", "memall_write", "memall_system"):
            assert required in names, sorted(names)
        print("PASS: test_mcp_stdio_handshake_and_tools_list")
    finally:
        c.close()


def test_mcp_stdio_capture_search_and_new_capabilities(mcp_env):
    c = _MCP(mcp_env["db"], "mcp_e2e_agent", mcp_env["home"])
    try:
        c.call("initialize", {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "pytest-mcp", "version": "1"},
            "agent_name": "mcp_e2e_agent",
        })
        c.send("notifications/initialized", {}, notify=True)

        cap = c.tool("memall_write", {
            "action": "capture",
            "content": "MCP 协议测试：项目决定使用 PostgreSQL 主库，事务稳定可靠已确认。",
            "level": "L4", "category": "decision",
            "agent_name": "mcp_e2e_agent", "project": "memall",
        })
        assert isinstance(cap.get("id"), int), cap
        mid = cap["id"]

        s = c.tool("memall_read", {
            "action": "search", "query": "PostgreSQL", "mode": "fts5",
            "agent_name": "mcp_e2e_agent"})
        assert len(s.get("results") or []) >= 1, s

        cp = c.tool("memall_read", {
            "action": "continuity_pack", "agent_name": "mcp_e2e_agent", "project": "memall"})
        for k in ("active_state", "conflicts", "pending_decisions",
                  "recent_supersedes", "why_selected"):
            assert k in cp, k
        assert cp.get("kb_excluded") is True

        kb = c.tool("memall_write", {
            "action": "kb_ingest",
            "content": "MCP 协议 KB 文档正文需要足够长以便通过分块与摄取校验流程。" * 8,
            "doc_id": "mcp-kb", "title": "MCP KB", "path": "/kb/mcp.md",
            "project": "memall", "chunk_size": 240})
        assert kb.get("doc_id") == "mcp-kb" or kb.get("chunks"), kb
        cp2 = c.tool("memall_read", {
            "action": "continuity_pack", "agent_name": "mcp_e2e_agent", "project": "memall"})
        assert "mcp-kb" not in json.dumps(cp2, ensure_ascii=False)

        # P3: config-only (tracking_enabled preset in the child's HOME config)
        for _ in range(3):
            c.tool("memall_write", {
                "action": "record_interaction", "query": "PostgreSQL", "memory_id": mid,
                "interaction_type": "click", "agent_name": "mcp_e2e_agent", "accepted": True})
        on = c.tool("memall_read", {
            "action": "search", "query": "PostgreSQL", "mode": "fts5",
            "agent_name": "mcp_e2e_agent"})
        assert any("utility_rerank_signal" in r for r in (on.get("results") or [])), on

        aud = c.tool("memall_read", {
            "action": "retrieve", "mode": "audit", "agent_name": "mcp_e2e_agent", "limit": 5})
        for k in ("conflicts", "history", "evidence", "maintenance"):
            assert k in aud, k
        print("PASS: test_mcp_stdio_capture_search_and_new_capabilities")
    finally:
        c.close()


def test_mcp_stdio_maintenance_authorization_and_rollback(mcp_env):
    # Low-privilege session: writes refused, reads open.
    low = _MCP(mcp_env["db"], "mcp_e2e_agent", mcp_env["home"])
    try:
        low.call("initialize", {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "pytest-mcp", "version": "1"},
            "agent_name": "mcp_e2e_agent"})
        low.send("notifications/initialized", {}, notify=True)
        d = low.tool("memall_system", {"action": "maintenance", "sub_action": "run"})
        assert d.get("error") == "forbidden", d
        st = low.tool("memall_system", {"action": "maintenance", "sub_action": "status"})
        assert st.get("enabled") is True, st
        au = low.tool("memall_system", {"action": "maintenance", "sub_action": "audit", "limit": 5})
        assert au.get("ok") is True, au
    finally:
        low.close()

    # Supervisor session (handshake identity is what authorize uses).
    sup = _MCP(mcp_env["db"], "mcp_sup", mcp_env["home"])
    try:
        sup.call("initialize", {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "pytest-mcp", "version": "1"},
            "agent_name": "mcp_sup"})
        sup.send("notifications/initialized", {}, notify=True)

        cap = sup.tool("memall_write", {
            "action": "capture",
            "content": "MCP 协议维护测试：项目采用 Valkey 主库，配置已确认且需审计留痕。",
            "level": "L4", "category": "decision", "project": "memall"})
        mid = cap["id"]

        # Force a CONFLICT row (the update tool does not expose memory_status).
        from memall.core import db as core_db
        orig = core_db.DB_PATH
        core_db.DB_PATH = Path(mcp_env["db"])
        core_db._global_pool = None
        from memall.core.db import get_conn
        conn = get_conn()
        conn.execute("UPDATE memories SET memory_status='CONFLICT' WHERE id=?", (mid,))
        conn.commit()
        conn.close()
        core_db.DB_PATH = orig
        core_db._global_pool = None

        run = sup.tool("memall_system", {"action": "maintenance", "sub_action": "run"})
        assert run.get("ok") is True and run.get("ran") is True, run

        au = sup.tool("memall_system", {"action": "maintenance", "sub_action": "audit", "limit": 50})
        blob = json.dumps(au, ensure_ascii=False)
        assert (au.get("entries") or [{}])[0].get("redacted") is True, au
        assert "Valkey 主库" not in blob, "audit must not leak memory content"
        mine = [e for e in (au.get("entries") or [])
                if e.get("action") == "conflict_review" and e.get("memory_id") == mid]
        assert mine, au

        rb = sup.tool("memall_system", {
            "action": "maintenance", "sub_action": "rollback",
            "entry": {k: mine[0][k] for k in ("ts", "memory_id", "action")}})
        assert rb.get("ok") is True, rb
        assert rb.get("source") == "server-side-audit-log", rb

        # A forged payload must be refused and must not touch the row.
        fg = sup.tool("memall_system", {
            "action": "maintenance", "sub_action": "rollback",
            "entry": {"before": {"id": mid,
                                 "metadata": json.dumps({"pwned": True}),
                                 "memory_status": "archived"}}})
        assert fg.get("ok") is False, fg

        core_db.DB_PATH = Path(mcp_env["db"])
        core_db._global_pool = None
        conn = get_conn()
        meta = json.loads(conn.execute(
            "SELECT metadata FROM memories WHERE id=?", (mid,)).fetchone()[0])
        conn.close()
        core_db.DB_PATH = orig
        core_db._global_pool = None
        assert "pwned" not in meta
        print("PASS: test_mcp_stdio_maintenance_authorization_and_rollback")
    finally:
        sup.close()
