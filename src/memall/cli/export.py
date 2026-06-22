"""
memall export: export memories in multiple formats (JSON / JSONL / Markdown / YAML).

Design principle: data never leaves you — exports go to ~/.memall/exports/ by default.
"""

import json
import sys
from pathlib import Path
from datetime import datetime, timezone

from memall.core.db import get_conn, DB_PATH

EXPORT_DIR = Path.home() / ".memall" / "exports"


def _ensure_export_dir():
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)


def _get_all_memories(category=None, since=None):
    conn = get_conn()
    try:
        if category and since:
            rows = conn.execute(
                "SELECT * FROM memories WHERE category = ? AND updated_at >= ? ORDER BY occurred_at DESC",
                (category, since),
            ).fetchall()
        elif category:
            rows = conn.execute(
                "SELECT * FROM memories WHERE category = ? ORDER BY occurred_at DESC",
                (category,),
            ).fetchall()
        elif since:
            rows = conn.execute(
                "SELECT * FROM memories WHERE updated_at >= ? ORDER BY occurred_at DESC",
                (since,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM memories ORDER BY occurred_at DESC"
            ).fetchall()

        results = []
        for r in rows:
            edges = conn.execute(
                "SELECT e.target_id, e.relation_type, e.weight, m.content "
                "FROM edges e JOIN memories m ON e.target_id = m.id "
                "WHERE e.source_id = ?",
                (r["id"],),
            ).fetchall()

            mem = dict(r)
            mem["_edges"] = [
                {
                    "target_id": e["target_id"],
                    "relation": e["relation_type"],
                    "weight": e["weight"],
                    "target_content": e["content"][:80] if e["content"] else "",
                }
                for e in edges
            ]
            results.append(mem)
        return results
    finally:
        conn.close()


def _format_jsonl(memories, output_path):
    """Export as JSONL (one JSON object per line), including content_hash for dedup."""
    output_path = Path(output_path)
    with open(output_path, "w", encoding="utf-8") as f:
        for m in memories:
            edges = m.pop("_edges", [])
            entry = {
                "type": "memory",
                "id": m["id"],
                "content": m["content"],
                "content_hash": m["content_hash"],
                "category": m["category"],
                "level": m["level"],
                "owner": m["owner"],
                "agent_name": m["agent_name"],
                "subject": m["subject"],
                "project": m["project"],
                "summary": m["summary"],
                "occurred_at": m["occurred_at"],
                "created_at": m["created_at"],
                "updated_at": m["updated_at"],
                "confidence": m["confidence"],
                "visibility": m["visibility"],
                "metadata": m.get("metadata", "{}"),
            }
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
            for e in edges:
                f.write(json.dumps({
                    "type": "edge",
                    "source_id": m["id"],
                    "target_id": e["target_id"],
                    "relation_type": e["relation"],
                    "weight": e["weight"],
                }, ensure_ascii=False) + "\n")
    return output_path


def _format_json(memories, output_path):
    payload = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "source_db": str(DB_PATH),
        "total_memories": len(memories),
        "memories": [],
    }
    for m in memories:
        edges = m.pop("_edges", [])
        entry = {
            "id": m["id"],
            "content": m["content"],
            "category": m["category"],
            "level": m["level"],
            "owner": m["owner"],
            "agent_name": m["agent_name"],
            "subject": m["subject"],
            "project": m["project"],
            "summary": m["summary"],
            "occurred_at": m["occurred_at"],
            "created_at": m["created_at"],
            "updated_at": m["updated_at"],
            "content_hash": m["content_hash"],
            "confidence": m["confidence"],
            "visibility": m["visibility"],
            "metadata": m.get("metadata", "{}"),
            "relations": edges,
        }
        payload["memories"].append(entry)

    output_path = Path(output_path)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def _format_markdown(memories, output_path):
    lines = []
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines.append(f"# MemALL Export")
    lines.append(f"")
    lines.append(f"**Exported**: {now_str}")
    lines.append(f"**Source**: `{DB_PATH}`")
    lines.append(f"**Total Memories**: {len(memories)}")
    lines.append(f"")
    lines.append(f"---")
    lines.append(f"")

    for m in memories:
        edges = m.pop("_edges", [])
        cat = m["category"] or "general"
        lines.append(f"## [{m['id']}] [{cat}] {m.get('subject') or m['content'][:60]}")
        lines.append(f"")
        lines.append(f"- **Category**: {cat}")
        lines.append(f"- **Level**: {m['level']}")
        lines.append(f"- **Owner**: {m['owner']}")
        lines.append(f"- **Agent**: {m['agent_name']}")
        lines.append(f"- **Occurred**: {m['occurred_at'][:19] if m['occurred_at'] else 'N/A'}")
        lines.append(f"- **Created**: {m['created_at'][:19] if m['created_at'] else 'N/A'}")
        lines.append(f"- **Visibility**: {m.get('visibility', 'private')}")
        lines.append(f"- **Confidence**: {m.get('confidence', 0.5)}")
        if m.get("project"):
            lines.append(f"- **Project**: {m['project']}")
        if m.get("summary"):
            lines.append(f"- **Summary**: {m['summary']}")
        lines.append(f"")

        content = m["content"]
        # Quote the content
        for line in content.split("\n"):
            lines.append(f"> {line}")
        lines.append(f"")

        if edges:
            lines.append(f"**Relations ({len(edges)}):**")
            for e in edges:
                lines.append(f"- `{e['relation']}` → #{e['target_id']} (w={e['weight']})")
            lines.append(f"")

        lines.append(f"---")
        lines.append(f"")

    output_path = Path(output_path)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path


def _format_yaml(memories, output_path):
    try:
        import yaml
    except ImportError:
        print("PyYAML not installed. Install with: pip install pyyaml", file=sys.stderr)
        sys.exit(1)

    payload = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "source_db": str(DB_PATH),
        "total_memories": len(memories),
        "memories": [],
    }
    for m in memories:
        edges = m.pop("_edges", [])
        entry = {
            "id": m["id"],
            "content": m["content"],
            "category": m["category"],
            "level": m["level"],
            "owner": m["owner"],
            "agent_name": m["agent_name"],
            "subject": m["subject"] or "",
            "project": m["project"] or "",
            "summary": m["summary"] or "",
            "occurred_at": m["occurred_at"],
            "created_at": m["created_at"],
            "updated_at": m["updated_at"],
            "content_hash": m["content_hash"],
            "confidence": m["confidence"],
            "visibility": m.get("visibility", "private"),
            "relations": edges,
        }
        payload["memories"].append(entry)

    output_path = Path(output_path)
    output_path.write_text(yaml.dump(payload, allow_unicode=True, default_flow_style=False, sort_keys=False), encoding="utf-8")
    return output_path


def cmd_export(args):
    _ensure_export_dir()

    category = getattr(args, "category", None)
    since = getattr(args, "since", None)
    memories = _get_all_memories(category=category, since=since)

    if not memories:
        print("No memories to export.")
        return

    fmt = args.format
    date_str = datetime.now().strftime("%Y-%m-%d")

    if args.output:
        output_path = Path(args.output)
    else:
        ext = {"json": ".json", "jsonl": ".jsonl", "markdown": ".md", "yaml": ".yaml"}.get(fmt, ".json")
        filename = f"memall-export-{date_str}{ext}"
        output_path = EXPORT_DIR / filename

    if fmt == "jsonl":
        out = _format_jsonl(memories, output_path)
    elif fmt == "json":
        out = _format_json(memories, output_path)
    elif fmt == "markdown":
        out = _format_markdown(memories, output_path)
    elif fmt == "yaml":
        out = _format_yaml(memories, output_path)
    else:
        print(f"Unsupported format: {fmt}. Use json, jsonl, markdown, or yaml.", file=sys.stderr)
        sys.exit(1)

    size_kb = out.stat().st_size / 1024
    print(f"Exported {len(memories)} memories ({size_kb:.1f} KB) to {out}")
