"""Base CLI commands: init, capture, search, get, connect, traverse, timeline, update."""

import json
import sys

from memall.core.db import init_db, get_db_path
from memall.core.models import MemoryInput
from memall.core.thin_waist import capture, retrieve, connect, traverse, timeline, update as tw_update


def cmd_init(args):
    init_db()
    print(f"memall initialized at {get_db_path()}")


def cmd_capture(args):
    content = args.content or sys.stdin.read().strip()
    if not content:
        print("error: content required", file=sys.stderr)
        sys.exit(1)
    mid = capture(MemoryInput(
        content=content,
        owner=args.owner or "",
        agent_name=args.agent or "",
        subject=args.subject or "",
        project=args.project or "",
        category=args.category or "general",
        level=args.level or "P2",
    ))
    print(mid)


def cmd_search(args):
    results = retrieve(
        args.query,
        owner=args.owner,
        agent_name=args.agent,
        category=args.category,
        project=args.project,
        limit=args.limit or 20,
    )
    if isinstance(results, list):
        for r in results:
            print(f"[{r.id}] [{r.category}] {r.content[:120]}")
            print(f"       owner={r.owner} agent={r.agent_name} occurred={r.occurred_at[:19]}")
    elif results:
        r = results
        print(f"[{r.id}] [{r.category}] {r.content}")
        print(f"       owner={r.owner} agent={r.agent_name} occurred={r.occurred_at[:19]}")


def cmd_get(args):
    result = retrieve(args.id)
    if result:
        r = result
        print(f"ID: {r.id}")
        print(f"Content: {r.content}")
        print(f"Category: {r.category} | Level: {r.level}")
        print(f"Owner: {r.owner} | Agent: {r.agent_name}")
        print(f"Subject: {r.subject} | Project: {r.project}")
        print(f"Occurred: {r.occurred_at}")
        print(f"Confidence: {r.confidence} | Visibility: {r.visibility} | Score: {r.metadata}")
    else:
        print(f"memory {args.id} not found", file=sys.stderr)
        sys.exit(1)


def cmd_connect(args):
    try:
        eid = connect(args.source, args.target, args.relation, args.weight)
        print(eid)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_traverse(args):
    graph = traverse(args.id, args.depth or 1, args.relation)
    print(json.dumps(graph, ensure_ascii=False, indent=2))


def cmd_timeline(args):
    items = timeline(
        query=args.query,
        hours=args.hours or 24,
        category=args.category,
        project=args.project,
        limit=args.limit or 50,
        start=args.start,
        end=args.end,
        days=args.days,
    )
    for r in items:
        print(f"[{r.id}] [{r.category}] {r.content[:120]}")
        print(f"       occurred={r.occurred_at[:19]}")


def cmd_update(args):
    ok = tw_update(args.id, **{args.field: args.value})
    if ok:
        print(f"memory {args.id} updated: {args.field}={args.value}")
    else:
        print(f"memory {args.id} not found or invalid field", file=sys.stderr)
        sys.exit(1)