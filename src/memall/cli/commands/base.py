"""Base CLI commands: init, capture, search, get, connect, traverse, timeline, update."""

import json
import sys

from memall.cli.handle_call import mcp_call
from memall.core.db import init_db, get_db_path


def cmd_init(args):
    init_db()
    print(f"memall initialized at {get_db_path()}")


def cmd_capture(args):
    content = args.content or sys.stdin.read().strip()
    if not content:
        print("error: content required", file=sys.stderr)
        sys.exit(1)
    result = mcp_call("memall_write", action="capture",
        content=content,
        owner=args.owner or "",
        agent_name=args.agent or "",
        subject=args.subject or "",
        project=args.project or "",
        category=args.category or "general",
        level=args.level or "P2",
    )
    if not result.ok:
        print(f"error: {result.error}", file=sys.stderr)
        sys.exit(1)
    print(result.data.get("id", "ok"))


def cmd_search(args):
    level_filter = getattr(args, "level", None)
    result = mcp_call("memall_read", action="retrieve",
        query=args.query,
        owner=args.owner,
        agent_name=args.agent,
        category=args.category,
        project=args.project,
        level=level_filter,
        limit=args.limit or 20,
    )
    if not result.ok:
        print(f"error: {result.error}", file=sys.stderr)
        sys.exit(1)
    results = result.data
    if isinstance(results, list):
        for r in results:
            print(f"[{r['id']}] [{r['level']}/{r['category']}] {r['content'][:120]}")
            print(f"       owner={r['owner']} agent={r['agent_name']} occurred={r['occurred_at'][:19]}")
    elif isinstance(results, dict) and results.get("id"):
        r = results
        print(f"[{r['id']}] [{r['level']}/{r['category']}] {r['content']}")
        print(f"       owner={r['owner']} agent={r['agent_name']} occurred={r['occurred_at'][:19]}")


def cmd_knowledge(args):
    """Search only L9 distilled knowledge."""
    result = mcp_call("memall_read", action="retrieve",
        query=args.query,
        level="L9",
        limit=args.limit or 20,
    )
    if not result.ok:
        print(f"error: {result.error}", file=sys.stderr)
        return
    results = result.data
    if not isinstance(results, list) or not results:
        print("No distilled knowledge found for this query.")
        return
    print(f"=== L9 蒸馏知识 ({len(results)}条) ===")
    for r in results:
        print(f"  [{r['id']}] [{r['category']}] {r.get('subject') or '(无主题)'}")
        print(f"      {r['content'][:150]}")
        print()


def cmd_insights(args):
    """Search only L10 cross-domain insights."""
    result = mcp_call("memall_read", action="retrieve",
        query=args.query,
        level="L10",
        limit=args.limit or 10,
    )
    if not result.ok:
        print(f"error: {result.error}", file=sys.stderr)
        return
    results = result.data
    if not isinstance(results, list) or not results:
        print("No cross-domain insights found for this query.")
        return
    print(f"=== L10 跨领域洞察 ({len(results)}条) ===")
    for r in results:
        print(f"  [{r['id']}] [{r['category']}] {r.get('subject') or '(无主题)'}")
        print(f"      {r['content'][:200]}")
        print()


def cmd_get(args):
    result = mcp_call("memall_read", action="retrieve", query=args.id)
    if not result.ok or result.data is None:
        print(f"memory {args.id} not found", file=sys.stderr)
        sys.exit(1)
    r = result.data
    if isinstance(result.data, list) and len(result.data) == 1:
        r = result.data[0]
    print(f"ID: {r['id']}")
    print(f"Content: {r['content']}")
    print(f"Category: {r['category']} | Level: {r['level']}")
    print(f"Owner: {r['owner']} | Agent: {r['agent_name']}")
    print(f"Subject: {r.get('subject', '')} | Project: {args.project if hasattr(args, 'project') else ''}")
    print(f"Occurred: {r['occurred_at']}")


def cmd_connect(args):
    result = mcp_call("memall_write", action="connect",
        source_id=args.source,
        target_id=args.target,
        relation_type=args.relation,
        weight=args.weight,
    )
    if not result.ok:
        print(f"error: {result.error}", file=sys.stderr)
        sys.exit(1)
    print(result.data.get("id", "ok"))


def cmd_traverse(args):
    result = mcp_call("memall_read", action="traverse",
        node_id=args.id,
        depth=args.depth or 1,
        relation_filter=args.relation,
    )
    if not result.ok:
        print(f"error: {result.error}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(result.data, ensure_ascii=False, indent=2))


def cmd_timeline(args):
    result = mcp_call("memall_read", action="timeline",
        query=args.query,
        hours=args.hours or 24,
        category=args.category,
        project=args.project,
        limit=args.limit or 50,
        start=getattr(args, "start", None),
        end=getattr(args, "end", None),
        days=getattr(args, "days", None),
    )
    if not result.ok:
        print(f"error: {result.error}", file=sys.stderr)
        sys.exit(1)
    items = result.data if isinstance(result.data, list) else []
    for r in items:
        print(f"[{r['id']}] [{r['category']}] {r['content'][:120]}")
        print(f"       occurred={r['occurred_at'][:19]}")


def cmd_update(args):
    result = mcp_call("memall_write", action="update", memory_id=args.id, **{args.field: args.value})
    if result.ok:
        print(f"memory {args.id} updated: {args.field}={args.value}")
    else:
        print(f"memory {args.id} not found or invalid field", file=sys.stderr)
        sys.exit(1)