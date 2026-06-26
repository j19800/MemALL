import json
import re
from datetime import datetime, timezone
from memall.core.db import get_conn
from memall.pipeline.behavior import annotate_text

_FROM_TO_TOPIC_RE = re.compile(
    r'from[：:]\s*(?P<from>[^\s]+)\s+to[：:]\s*(?P<to>[^\s]+)'
    r'(?:\s+topic[：:]\s*(?P<topic>[^\n]+?))?',
    re.I,
)
_MODULE_REF_RE = re.compile(r'\[MODULE[^\]]*\]\s*(?P<module_path>[^\s]+)', re.I)


def _find_memory_refs(text: str) -> list:
    refs = re.findall(r'(?:ID|#|id|Id)\s*(\d{3,5})', text)
    refs += re.findall(r'(?:综合|参考|基于|来自|源自|关联)\s*(\d{3,5})', text)
    return [int(r) for r in set(refs) if 1 <= int(r) <= 1800]


def _is_summary_like(text: str) -> bool:
    keywords = ['总结', '摘要', '提炼', '融合', '汇总', '综合', '融合了', '提炼自', '基于以上', '综合以上']
    return any(k in text for k in keywords)


def _parse_from_to_topic(text: str) -> list:
    """Extract from/to/topic structured communication records and return edge dicts."""
    edges = []
    for m in _FROM_TO_TOPIC_RE.finditer(text):
        d = m.groupdict()
        fr = (d.get("from") or "").strip()
        to = (d.get("to") or "").strip()
        topic = (d.get("topic") or "").strip()
        if fr and to:
            edges.append({
                "source_agent": fr,
                "target_agent": to,
                "relation": "delegates",
                "topic": topic,
            })
    return edges


def _parse_module_refs(text: str) -> list:
    """Extract [MODULE:layer_core/xxx] references and return module paths."""
    return [m.group("module_path") for m in _MODULE_REF_RE.finditer(text)]


def _ensure_edges_table(conn) -> None:
    conn.execute("""
    CREATE TABLE IF NOT EXISTS edges (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      source_id INTEGER NOT NULL,
      target_id INTEGER NOT NULL,
      relation_type TEXT NOT NULL DEFAULT 'related',
      weight REAL DEFAULT 1.0,
      created_by TEXT DEFAULT 'pipeline',
      created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """)
    rows = conn.execute(
      "SELECT source_id, target_id, relation_type, COUNT(*) as cnt "
      "FROM edges GROUP BY source_id, target_id, relation_type HAVING cnt > 1"
    ).fetchall()
    if rows:
      for r in rows:
        conn.execute(
          "DELETE FROM edges WHERE rowid NOT IN ("
          "SELECT MIN(rowid) FROM edges "
          "WHERE source_id=? AND target_id=? AND relation_type=?"
          ")", (r["source_id"], r["target_id"], r["relation_type"])
        )
    conn.execute(
      "CREATE UNIQUE INDEX IF NOT EXISTS idx_edges_unique "
      "ON edges(source_id, target_id, relation_type)"
    )


def enrich_step() -> int:
    conn = get_conn()
    try:
        _ensure_edges_table(conn)
        rows = conn.execute(
            "SELECT id, content, metadata FROM memories WHERE level != 'P0' ORDER BY id DESC LIMIT 2000"
        ).fetchall()
        count = 0
        for row in rows:
            existing_meta = json.loads(row["metadata"] or "{}")
            if existing_meta.get("enrich"):
                continue  # Already enriched by Agent, skip

            meta: dict = {}
            text = row["content"]

            # Basic enrichment: entities / times / problems / decisions
            entities = re.findall(r'[A-Z][a-zA-Z]*(?:\s[A-Z][a-zA-Z]*)*', text)
            if entities:
                meta["entities"] = list(set(entities))

            time_refs = re.findall(
                r'(\d{4}-\d{2}-\d{2}|\d{1,2}月\s?\d{1,2}日|上周|这周|下个月|昨天|今天|明天)',
                text,
            )
            if time_refs:
                meta["time_refs"] = time_refs

            problems = re.findall(r'(问题|瓶颈|不足|太慢|太复杂|不够|没法)[^。]*', text)
            if problems:
                meta["problems"] = [p.strip() for p in problems]

            decisions = re.findall(r'(决定|选择|采用|改用|替换|用\s+\w+\s+替代)[^。]*', text)
            if decisions:
                meta["decisions"] = [d.strip() for d in decisions]

            # L8: from/to/topic structured communication edges
            comm_edges = _parse_from_to_topic(text)
            if comm_edges:
                meta["communication_edges"] = comm_edges

            # L8: MODULE references → semantic links
            mod_refs = _parse_module_refs(text)
            if mod_refs:
                meta["module_refs"] = mod_refs

            # Phase 1: behavioral stage annotation (observe→model→predict→deviate→correct)
            behavior = annotate_text(text)
            if behavior.stages:
                meta["behavior"] = behavior.__dict__

            if meta:
                existing = json.loads(row["metadata"] or "{}")
                existing["enrich"] = {
                    "value": meta,
                    "_meta": {"version": 1, "written_at": datetime.now(timezone.utc).isoformat()},
                }
                conn.execute(
                    "UPDATE memories SET metadata = ? WHERE id = ?",
                    (json.dumps(existing, ensure_ascii=False), row["id"]),
                )
                count += 1

            # Entity/cross-reference edges (existing behavior, kept intact)
            refs = _find_memory_refs(text)
            if refs:
                for ref_id in refs:
                    exists = conn.execute(
                        "SELECT 1 FROM memories WHERE id = ?", (ref_id,)
                    ).fetchone()
                    if exists:
                        dup = conn.execute(
                            "SELECT 1 FROM edges WHERE source_id = ? AND target_id = ? "
                            "AND relation_type = 'derived_from'",
                            (row["id"], ref_id),
                        ).fetchone()
                        if not dup:
                            conn.execute(
                                "INSERT OR IGNORE INTO edges "
                                "(source_id, target_id, relation_type, weight, created_at) "
                                "VALUES (?,?,'derived_from',1.0,datetime('now'))",
                                (row["id"], ref_id),
                            )
                            count += 1

            # L8: build delegates/replies_to edges from from/to/topic records
            for edge in comm_edges:
                _fr = edge["source_agent"]
                to = edge["target_agent"]
                _topic = edge.get("topic", "")
                target_row = conn.execute(
                  "SELECT id FROM memories WHERE agent_name = ? ORDER BY id DESC LIMIT 1",
                  (to,),
                ).fetchone()
                if target_row:
                  rel = "delegates"
                  dup = conn.execute(
                    "SELECT 1 FROM edges WHERE source_id = ? AND target_id = ? "
                    "AND relation_type = ?",
                    (row["id"], target_row["id"], rel),
                  ).fetchone()
                  if not dup:
                    conn.execute(
                      "INSERT OR IGNORE INTO edges "
                      "(source_id, target_id, relation_type, weight, created_at) "
                      "VALUES (?,?,?,?,datetime('now'))",
                      (row["id"], target_row["id"], rel, 1.0),
                    )
                  count += 1

            conn.commit()
        return count
    finally:
        conn.close()
