"""Red/blue adversarial regression harness.

``src/check_fixes.py`` guards the bugs that were once fixed *silently* — one
named check per historical incident.  This module guards something else: it
checks that the **defences are still standing**.

Each entry is a red/blue pair:

  * **red** — an attack.  A caller who should not be able to do X tries to do
    X, on an isolated throwaway database.
  * **blue** — the defence that is supposed to stop it.

The check passes when the attack is repelled and fails when it lands.  A
failing entry is not a test regression to be silenced; it is a live hole with
a reproduction attached.

    python src/red_blue_checks.py              # run every duel
    python src/red_blue_checks.py --list       # show the manifest
    python src/red_blue_checks.py session_identity_is_authoritative

Adding a new duel: write ``def duel_<name>() -> Tuple[bool, str]`` returning
``(attack_repelled, detail)`` and register it in ``DUELS``.  The manifest is
also the documentation: attack -> defence -> why it matters.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from typing import Callable, Dict, Tuple

SRC = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SRC)


def _fresh_db() -> str:
    """Point MemALL at a throwaway DB and return its path.

    Mirrors ``check_fixes._fresh_db``: the session identity is process-global,
    so a leaked identity from a previous duel silently changes what the next
    duel can see.  The deny-list is neutralised too — these duels write under
    names like "duel" that the deployment may have banned.
    """
    try:
        from memall.mcp.session_identity import reset as _reset_identity

        _reset_identity()
    except Exception:
        pass

    tmp = tempfile.mkdtemp(prefix="memall_duel_")
    path = os.path.join(tmp, "duel.db")
    try:
        from memall.core.bg_registry import wait_for_background

        wait_for_background(timeout=20.0)
    except Exception:
        pass

    try:
        from memall.core import db as core_db

        core_db.DB_PATH = __import__("pathlib").Path(path)
        core_db._global_pool = None
    except Exception:
        pass

    try:
        import memall.config as mc

        if mc._config is not None:
            sec = mc._config.setdefault("security", {})
            sec["disabled_agents"] = []
            sec["disabled_projects"] = []
            mc._last_mtime_check = float("inf")
    except Exception:
        pass

    try:
        from memall.core.db import init_db

        init_db()
    except Exception as exc:  # pragma: no cover - setup failure
        return f"init failed: {exc}"

    return path


def _seed_identity(name: str, agent_type: str = "agent", trusted_by: str = "[]") -> None:
    from memall.core.db import get_conn

    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO identities (agent_name, agent_type, trusted_by, status) "
        "VALUES (?,?,?,?)",
        (name, agent_type, trusted_by, "active"),
    )
    conn.commit()
    conn.close()


def _read_private(owner: str = "writer") -> int:
    """Write one ``private`` memory owned by *owner* and return its id."""
    from memall.core.thin_waist import capture

    return capture(
        f"DUEL_SECRET 这条记忆由 {owner} 写成，visibility 设为 private，"
        "正文足够长以通过质量门检查。",
        level="L4",
        category="general",
        project="duel",
        agent_name=owner,
        owner="老陈",
        visibility="private",
    )


def _search_hits_secret(payload: str) -> bool:
    """True when the tool response contains the seeded private content."""
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return "DUEL_SECRET" in (payload or "")
    for row in data.get("results") or []:
        if "DUEL_SECRET" in (row.get("content") or ""):
            return True
    return False


# ── duels ────────────────────────────────────────────────────────────────


def duel_session_identity_is_authoritative() -> Tuple[bool, str]:
    """red: 握手为 alpha 的会话，显式传 agent_name=writer 冒充。

    blue: the handshake binding is the authority.  An explicit argument may
    not replace it, and — above all — it may not *mutate* it.

    Impact when it lands: the attacker reads another agent's private memory
    AND the whole session stays attributed to the victim for every later
    call that omits ``agent_name``.
    """
    _fresh_db()
    _seed_identity("alpha")
    _seed_identity("writer")
    _read_private("writer")

    from memall.mcp.adapter import handle_call
    from memall.mcp.session_identity import bind_handshake_agent, get_session_agent

    bind_handshake_agent("alpha")
    payload = handle_call(
        "memall_read",
        {"action": "search", "query": "DUEL_SECRET", "mode": "direct",
         "agent_name": "writer"},
    )
    leaked = _search_hits_secret(payload)
    rebound = get_session_agent()

    if leaked or rebound != "alpha":
        return False, (
            f"会话身份可被覆盖：alpha 的会话读到他人 private 记忆={leaked}，"
            f"会话绑定被改写为 {rebound!r}（期望仍为 'alpha'）"
        )
    return True, "握手身份保持权威，显式参数未改写会话绑定"


def duel_unregistered_identity_is_least_privilege() -> Tuple[bool, str]:
    """red: 用一个从未注册过的身份名去读 trusted 可见性的记忆。

    blue: an identity that is not in ``identities`` must get the *lowest*
    read level, not the highest.  ``_get_agent_read_level`` currently returns
    ``"private"`` for unknown agents, and ``VISIBILITY_RANK["private"]`` is
    the largest number, which makes ``mem_rank <= viewer_rank`` true for
    every row — the opposite of what the code comment promises.
    """
    _fresh_db()
    _seed_identity("writer")
    _seed_identity("alpha")

    from memall.core.thin_waist import capture

    mid = capture(
        "DUEL_TRUSTED 这条记忆由 writer 写成，visibility 设为 trusted，"
        "正文足够长以通过质量门检查。",
        level="L4", category="general", project="duel", agent_name="writer",
        owner="老陈", visibility="trusted",
    )

    from memall.core.db import get_conn
    from memall.core.thin_waist import (
        VISIBILITY_RANK,
        _filter_by_trust,
        _get_agent_read_level,
        _row_to_memory,
    )

    conn = get_conn()
    row = conn.execute("SELECT * FROM memories WHERE id=?", (mid,)).fetchone()
    mem = _row_to_memory(row)
    registered = bool(_filter_by_trust(conn, [mem], "alpha"))
    unregistered = bool(_filter_by_trust(conn, [mem], "phantom_unregistered"))
    lvl_registered = _get_agent_read_level(conn, "alpha")
    lvl_unregistered = _get_agent_read_level(conn, "phantom_unregistered")
    conn.close()

    if unregistered and not registered:
        return False, (
            f"未注册身份读到 trusted 记忆={unregistered}，在册身份 alpha "
            f"(read_level={lvl_registered}) 读到={registered} —— 信任等级方向反了"
        )
    if VISIBILITY_RANK.get(lvl_unregistered, 0) > VISIBILITY_RANK.get(lvl_registered, 0):
        return False, (
            f"未注册身份的读级别 {lvl_unregistered!r} "
            f"(rank {VISIBILITY_RANK.get(lvl_unregistered)}) 高于在册身份 "
            f"{lvl_registered!r} (rank {VISIBILITY_RANK.get(lvl_registered)})"
        )
    return True, "未注册身份拿到的读权限不高于在册身份"


def duel_every_read_path_applies_trust_filter() -> Tuple[bool, str]:
    """red: 无会话身份时，direct / fts5 / vector 三个检索分支读取他人记忆。

    blue: every read path must pass a viewer down to the trust filter.
    ``unified_search`` only does that in the hybrid branch, so the other
    three modes return rows no trust level would ever allow.
    """
    _fresh_db()
    _seed_identity("writer")
    _read_private("writer")

    from memall.core.thin_waist import unified_search

    leaked_modes = []
    for mode in ("direct", "fts5", "vector", "hybrid"):
        try:
            out = json.loads(unified_search({"query": "DUEL_SECRET", "mode": mode}))
        except Exception as exc:
            leaked_modes.append(f"{mode}(raised {exc!r})")
            continue
        for row in out.get("results") or []:
            if "DUEL_SECRET" in (row.get("content") or ""):
                leaked_modes.append(mode)
                break

    if leaked_modes:
        return False, f"以下检索分支未执行信任过滤，读到他人 private 记忆：{leaked_modes}"
    return True, "四个检索分支均执行了信任过滤"


def duel_graph_and_timeline_read_paths_filter() -> Tuple[bool, str]:
    """red: 陌生人用 traverse / timeline / trace 按 id 读他人 private 记忆.

    blue: 这些入口都返回记忆正文，必须和 search 一样过信任过滤。
    R3-11：traverse 返回节点全文、timeline 无 agent 过滤、trace 无 viewer。
    """
    _fresh_db()
    _seed_identity("writer")
    _seed_identity("eve")
    mid = _read_private("writer")

    from memall.core.thin_waist import traverse
    from memall.mcp.tools.retrieve import handle_trace
    from memall.mcp.tools.timeline import handle
    from memall.mcp.tools.graph import handle_traverse

    leaks = []

    try:
        g = traverse(mid, viewer="eve")
        if any("DUEL_SECRET" in (n.get("content") or "") for n in g.get("nodes", [])):
            leaks.append("traverse")
    except Exception as exc:
        leaks.append(f"traverse(raised {exc!r})")

    try:
        items = json.loads(handle({"limit": 20, "agent_name": "eve"}))
        if any("DUEL_SECRET" in (i.get("content") or "") for i in items):
            leaks.append("timeline")
    except Exception as exc:
        leaks.append(f"timeline(raised {exc!r})")

    try:
        out = json.loads(handle_trace({"memory_id": mid, "agent_name": "eve"}))
        if "DUEL_SECRET" in (json.dumps(out, ensure_ascii=False)):
            leaks.append("trace")
    except Exception as exc:
        leaks.append(f"trace(raised {exc!r})")

    try:
        out = json.loads(handle_traverse({"node_id": mid, "agent_name": "eve"}))
        if "DUEL_SECRET" in (json.dumps(out, ensure_ascii=False)):
            leaks.append("traverse-handler")
    except Exception as exc:
        leaks.append(f"traverse-handler(raised {exc!r})")

    if leaks:
        return False, f"以下读入口泄露他人 private 记忆：{leaks}"
    return True, "traverse / timeline / trace 均执行了信任过滤"


def duel_fed_publish_cannot_launder_or_forge() -> Tuple[bool, str]:
    """red: 把他人私有记忆发布到共享库，并伪造 source_agent.

    blue: fed_publish 必须校验 viewer 可见性，且 source_agent 只能取自调用者
    身份（R3-7）。写入共享库是不可撤销的，这一步必须先拦住。
    """
    import tempfile
    from pathlib import Path

    _fresh_db()
    _seed_identity("writer")
    _seed_identity("eve")
    mid = _read_private("writer")

    # Keep family.db inside the throwaway directory: it used to be hard-coded
    # to ~/.memall/family.db, which is how the audit polluted production.
    from memall.core import db as core_db

    fam = Path(str(core_db.get_db_path())).parent / "family.db"
    os.environ["MEMALL_DB_PATH"] = str(core_db.get_db_path())

    from memall.mcp.federation_tools import fed_publish

    problems = []

    out = fed_publish(mid, source_agent="dsh", viewer="eve")
    if out.get("published"):
        problems.append("陌生身份仍然发布成功")
    if out.get("error") and "source_agent" not in out["error"] and "not visible" not in out["error"]:
        problems.append(f"拒绝原因不明确: {out['error']}")

    # Forging attribution must be refused even for a legitimate viewer.
    out2 = fed_publish(mid, source_agent="dsh", viewer="老陈")
    if out2.get("published"):
        problems.append("伪造 source_agent 仍然发布成功")

    if problems:
        return False, "; ".join(problems)
    return True, "fed_publish 拒绝越权发布与伪造署名"


def duel_permission_grants_are_authorised() -> Tuple[bool, str]:
    """red: 一个普通 agent 把自己的可见性放宽到 public，或给别人设权限。

    blue: ``security permit`` 只允许 supervisor / 人类 owner 配置他人；本人
    只能收紧（往 private 方向）。R3-8：原实现里 agent_name 既是目标又是调用
    者，任意 agent 都能提权。
    """
    _fresh_db()
    for who, lv in (("eve", "private"), ("writer", "private"), ("老陈", "private")):
        from memall.core.db import get_conn

        conn = get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO identities (agent_name, agent_type, permission_level, status) "
            "VALUES (?,?,?,'active')", (who, "human" if who == "老陈" else "ai", lv))
        conn.commit()
        conn.close()

    from memall.mcp import session_identity as si
    from memall.mcp.adapter import handle_call

    def permit(caller, target, level):
        si.reset()
        si.bind_handshake_agent(caller)
        try:
            return json.loads(handle_call("memall_system", {
                "action": "security", "security_sub_action": "permit",
                "agent_name": caller, "target_agent": target, "level": level,
            }))
        except Exception as exc:
            return {"_exc": repr(exc)}

    def level_of(name):
        from memall.core.db import get_conn

        conn = get_conn()
        row = conn.execute("SELECT permission_level FROM identities WHERE agent_name=?",
                           (name,)).fetchone()
        conn.close()
        return row["permission_level"] if row else ""

    problems = []

    # 1. self-widening must be refused (private is the strictest level)
    permit("eve", "eve", "public")
    if level_of("eve") != "private":
        problems.append("自身放宽到 public 未被拒绝")

    # 2. configuring somebody else without authorisation must be refused
    r = permit("eve", "writer", "public")
    if level_of("writer") != "private":
        problems.append("越权给他人设权限未被拒绝")

    # 3. the human owner may configure anyone
    permit("老陈", "writer", "trusted")
    if level_of("writer") != "trusted":
        problems.append("人类 owner 无法配置 agent")

    if problems:
        return False, "; ".join(problems)
    return True, "permit 授权门：自放宽被拒、越权被拒、owner 可配置"


def duel_pinned_contract_fields_are_immutable() -> Tuple[bool, str]:
    """red: 把一条 decision 记忆改写成 category='goal'（伪造目标卡）。

    blue: ``level``/``category`` 是与下游消费者的契约，update() 必须拒绝改写
    契约类目。R3-9：管线各步都遵守 pinned 契约，唯独用户写入路径没有——于是
    可以凭空造出绕过双证删除保护的目标卡。
    """
    _fresh_db()
    from memall.core.thin_waist import capture

    mid = capture(
        "DUEL_PINNED 这条记忆的分类是 decision，正文足够长以通过质量门检查。",
        level="L4", category="decision", agent_name="writer", owner="老陈")

    from memall.mcp import session_identity as si
    from memall.mcp.adapter import handle_call

    si.reset()
    si.bind_handshake_agent("writer")
    try:
        out = json.loads(handle_call("memall_write", {
            "action": "update", "memory_id": mid,
            "category": "goal", "level": "P0",
        }))
    except Exception as exc:
        out = {"_exc": repr(exc)}

    from memall.core.db import get_conn

    conn = get_conn()
    row = conn.execute("SELECT category, level FROM memories WHERE id=?", (mid,)).fetchone()
    conn.close()
    now_cat, now_level = (row["category"], row["level"]) if row else ("", "")

    if now_cat == "goal" or now_level == "P0":
        return False, f"pinned 字段被改写：category={now_cat!r} level={now_level!r}"
    return True, f"契约类目不可改写（保持 category={now_cat!r} level={now_level!r}）"


# ── manifest ─────────────────────────────────────────────────────────────

DUELS: Dict[str, Callable[[], Tuple[bool, str]]] = {
    "session_identity_is_authoritative": duel_session_identity_is_authoritative,
    "unregistered_identity_is_least_privilege": duel_unregistered_identity_is_least_privilege,
    "every_read_path_applies_trust_filter": duel_every_read_path_applies_trust_filter,
    "graph_and_timeline_read_paths_filter": duel_graph_and_timeline_read_paths_filter,
    "fed_publish_cannot_launder_or_forge": duel_fed_publish_cannot_launder_or_forge,
    "permission_grants_are_authorised": duel_permission_grants_are_authorised,
    "pinned_contract_fields_are_immutable": duel_pinned_contract_fields_are_immutable,
}


def main(argv: list[str]) -> int:
    if "--list" in argv:
        for name in DUELS:
            print(name)
        return 0

    selected = [a for a in argv if not a.startswith("-")]
    names = selected or list(DUELS)
    passed = failed = 0
    for name in names:
        fn = DUELS.get(name)
        if fn is None:
            print(f"SKIP  {name} (unknown)")
            continue
        try:
            ok, detail = fn()
        except Exception as exc:
            ok, detail = False, f"duel raised {exc!r}"
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
        if ok:
            passed += 1
        else:
            failed += 1
    print(f"\n{passed} passed / {failed} failed / {len(names)} duels")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
