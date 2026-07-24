"""MemALL 记忆系统接入桥接层（Bridge）

通过 stdin JSON 驱动，供外部进程（AI 助手）调用 MemALL 的记忆 API。

用法:
    python bridge.py < input.json

输入 JSON 格式（支持单条或数组）:
    {"action": "store",     "args": {...}}
    {"action": "retrieve",  "args": {...}}
    {"action": "search",    "args": {...}}
    {"action": "hybrid",    "args": {...}}
    {"action": "update",    "args": {...}}

输出: stdout JSON (数组).

环境变量:
    MEMALL_DB_PATH  覆盖 data.db 路径（建议设置）
    PYTHONPATH      需要包含 MemALL 的 src 目录
"""

import json
import os
import sys
from pathlib import Path

# Must set env var BEFORE any memall import
if "MEMALL_DB_PATH" not in os.environ:
    os.environ["MEMALL_DB_PATH"] = str(Path.home() / ".memall" / "data.db")


# ── Lazy imports (cached after first use) ──────────────────────────

_cache = {}


def _get_tw():
    if "tw" not in _cache:
        from memall.core import thin_waist as tw
        _cache["tw"] = tw
    return _cache["tw"]


# ── Actions ────────────────────────────────────────────────────────


def _action_store(args: dict) -> dict:
    """Store a memory via smart_store."""
    tw = _get_tw()
    result = tw.smart_store(
        content=args.get("content"),
        owner=args.get("owner", "nomi"),
        agent_name=args.get("agent_name", "nomi"),
        subject=args.get("subject", ""),
        project=args.get("project", "memall"),
        category=args.get("category", "general"),
        level=args.get("level", "P2"),
        dedup_threshold=args.get("dedup_threshold", 0.85),
    )
    return {"result": result}


def _action_retrieve(args: dict) -> dict:
    """Retrieve memories by query or filters."""
    tw = _get_tw()
    results = tw.retrieve(
        query=args.get("query"),
        viewer=args.get("viewer"),
        owner=args.get("owner"),
        agent_name=args.get("agent_name"),
        category=args.get("category"),
        project=args.get("project"),
        level=args.get("level"),
        subject=args.get("subject"),
        date_start=args.get("date_start"),
        date_end=args.get("date_end"),
        limit=args.get("limit", 20),
    )
    if isinstance(results, list):
        items = []
        for m in results:
            if hasattr(m, "to_dict"):
                items.append(m.to_dict())
            elif hasattr(m, "__dict__"):
                items.append(m.__dict__)
            else:
                items.append(str(m))
        return {"count": len(items), "results": items}
    return {"result": str(results)}


def _action_search(args: dict) -> dict:
    """Vector search."""
    tw = _get_tw()
    result = tw.vector_search(
        query=args.get("query", ""),
        top_k=args.get("top_k", 10),
        provider=args.get("provider"),
    )
    if isinstance(result, dict):
        return result
    return {"result": str(result)}


def _action_hybrid(args: dict) -> dict:
    """Hybrid FTS5 + vector search."""
    tw = _get_tw()
    result = tw.hybrid_search(
        query=args.get("query", ""),
        top_k=args.get("top_k", 10),
        rrf_k=args.get("rrf_k"),
        category=args.get("category"),
        level=args.get("level"),
        owner=args.get("owner"),
        rerank=args.get("rerank", False),
        viewer=args.get("viewer"),
    )
    return {"count": len(result.get("results", [])), **result} if isinstance(result, dict) else {"result": str(result)}


def _action_update(args: dict) -> dict:
    """Update a memory by id."""
    tw = _get_tw()
    mem_id = args.get("id")
    if mem_id is None:
        return {"error": "missing 'id'"}
    fields = {k: v for k, v in args.items() if k not in ("id",)}
    success = tw.update(mem_id, **fields)
    return {"success": success}


# ── Dispatcher ─────────────────────────────────────────────────────

_ACTIONS = {
    "store": _action_store,
    "retrieve": _action_retrieve,
    "search": _action_search,
    "hybrid": _action_hybrid,
    "update": _action_update,
}


def main():
    raw = sys.stdin.read()
    if not raw.strip():
        return

    try:
        inputs = json.loads(raw)
        if not isinstance(inputs, list):
            inputs = [inputs]
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"invalid JSON: {e}"}))
        sys.exit(1)

    results = []
    for inp in inputs:
        action = inp.get("action", "")
        args = inp.get("args", {})
        handler = _ACTIONS.get(action)
        if handler is None:
            results.append({"action": action, "error": f"unknown action: {action}"})
            continue
        try:
            ret = handler(args)
            results.append({"action": action, **ret})
        except Exception as e:
            import traceback
            results.append({"action": action, "error": str(e), "traceback": traceback.format_exc()})

    print(json.dumps(results, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
