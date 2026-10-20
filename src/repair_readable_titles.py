"""One-time repair: machine-scaffolded subjects and raw-JSON bodies.

The readability audit (3804 live rows) found four violation families, all
written before the capture()/quality-gate sanitiser existed:

  A. subject carries [NOVEL:] [MODULE:] etc. writer template markers (171)
  B. subject contains embedded newlines (194)
  C. subject is a fixed English template (Auto-summary: / Global: memall /
     Domain pattern: / lesson:) (142)
  D. content is a whole raw JSON document — the observation step's old
     json.dumps report (115)

Repair rules are exactly the live-path rules now enforced by capture():
sanitize_subject() for A/B/C, and a prose re-render for D-family rows whose
JSON parses to the observation report schema (timestamp +
level_distribution), with the original dict kept in metadata.report.
Rows whose subject is left EMPTY by sanitisation are re-derived from their
own content via _make_subject.  Every change is logged to ops entries? No —
subjects rewrite in place; FTS triggers keep the index synced automatically.

  python repair_readable_titles.py          # dry-run
  python repair_readable_titles.py --apply  # write
"""
import json
import re
import sys

sys.path.insert(0, r"E:\MemALL\src")

from memall.core.thin_waist import (  # noqa: E402
    _make_subject, _TEMPLATE_MARKERS_RE, sanitize_subject,
)

APPLY = "--apply" in sys.argv

MARK = re.compile(r"\[(?:NOVEL|FILE|CHUNK|MODULE|PART|VOL|CHAPTER|SECTION)\s*:", re.I)
TEMPLATE = re.compile(
    r"^(Auto-summary: |Global: |Domain pattern: |lesson: |Pipeline step: )", re.I
)


def main() -> int:
    from memall.core.db import get_conn
    conn = get_conn()
    live = "COALESCE(memory_status,'') != 'archived'"

    rows = conn.execute(
        f"SELECT id, subject, content, level, category, agent_name, owner, metadata "
        f"FROM memories WHERE {live} "
        f"AND (subject LIKE '%[NOVEL:%' OR subject LIKE '%[MODULE:%' "
        f"     OR subject LIKE '%[FILE:%' OR subject LIKE '%[CHUNK:%' "
        f"     OR instr(subject, char(10)) > 0 "
        f"     OR subject LIKE 'Auto-summary:%' OR subject LIKE 'Global: %' "
        f"     OR subject LIKE 'Domain pattern:%' OR subject LIKE 'lesson:%' "
        f"     OR TRIM(content) LIKE '{{%')"
    ).fetchall()
    print(f"candidates: {len(rows)}")

    fixed_subject = fixed_json = skipped = 0
    for r in rows:
        rid = r["id"]
        old_subj = r["subject"] or ""
        content = r["content"] or ""
        new_content = None

        # ── D. raw JSON body of the observation report ──
        is_json_body = content.strip().startswith("{") and content.strip().endswith("}")
        if is_json_body:
            try:
                doc = json.loads(content)
            except (json.JSONDecodeError, TypeError):
                doc = None
            if isinstance(doc, dict) and "level_distribution" in doc:
                dist = doc.get("level_distribution", {})
                ts = (doc.get("timestamp") or doc.get("ts") or "")[:10]
                dist_line = "、".join(
                    f"{k}×{v}" for k, v in sorted(
                        dist.items(), key=lambda kv: -kv[1])[:8]
                ) or "无"
                l6 = doc.get("l6_reflections", {}) or {}
                warns = doc.get("warnings", [])
                warn_line = ("；告警：" + "；".join(warnings := warns)) if warns else ""
                new_content = (
                    f"管线观察（{ts}）：当前记忆分布 {dist_line}。"
                    f"L6 反思共 {l6.get('total', '?')} 条，其中高质量 "
                    f"{l6.get('high_quality', '?')} 条，近 24 小时新增 "
                    f"{l6.get('recent_24h', '?')} 条。{warn_line}"
                )[:900]
                meta = {}
                try:
                    meta = json.loads(r["metadata"]) if r["metadata"] else {}
                except (json.JSONDecodeError, TypeError):
                    meta = {}
                meta["report"] = doc
                meta["title_repair"] = {"reason": "raw-JSON body replaced by prose",
                                          "at": "2026-10-20"}
                if APPLY:
                    conn.execute(
                        "UPDATE memories SET content=?, summary=?, metadata=?, "
                        "updated_at=datetime('now') WHERE id=?",
                        (new_content, new_content[:180],
                         json.dumps(meta, ensure_ascii=False), rid))
                fixed_json += 1

        # ── A/B/C. subject repair ──
        dirty = (bool(MARK.search(old_subj)) or TEMPLATE.search(old_subj)
                 or "\n" in old_subj or old_subj != sanitize_subject(old_subj))
        if dirty:
            cleaned = sanitize_subject(old_subj)
            # English template -> rebuild from (possibly new) content
            if not cleaned or TEMPLATE.search(old_subj) or MARK.search(old_subj):
                base = new_content if new_content else content
                cleaned = _make_subject(base, r["category"], r["level"],
                                        r["agent_name"], r["owner"])
                # _make_subject may re-derive a marker from content head;
                # final safety pass
                cleaned = sanitize_subject(cleaned) or "记录"
            if APPLY and cleaned != old_subj:
                conn.execute(
                    "UPDATE memories SET subject=?, summary=CASE WHEN "
                    "TRIM(COALESCE(summary,''))='' THEN substr(?,1,180) "
                    "ELSE summary END, updated_at=datetime('now') WHERE id=?",
                    (cleaned, new_content or content, rid))
            fixed_subject += 1
        else:
            skipped += 1

    if APPLY:
        conn.commit()
    conn.close()
    print(f"{'APPLIED' if APPLY else 'DRY-RUN'}: subjects={fixed_subject} "
          f"json-bodies={fixed_json} untouched={skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
