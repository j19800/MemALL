import logging
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from collections import Counter, defaultdict
from memall.core.db import get_conn
from memall.core.thin_waist import normalize_agent_name
from memall.pipeline.util import _smart_subject
logger = logging.getLogger(__name__)



def distill_step() -> dict:
    conn = get_conn()
    fk_was_on = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        rows = conn.execute(
            "SELECT id, content, category, agent_name, summary FROM memories WHERE category != '' AND category IS NOT NULL AND LENGTH(TRIM(content)) > 20 AND level NOT IN ('P0', 'P1', 'P2', 'P3', 'P4', 'L6', 'L9', 'L10', 'L11') ORDER BY id DESC LIMIT 5000"
        ).fetchall()

        groups = defaultdict(list)
        for r in rows:
            key = (r["agent_name"] or "unknown", r["category"] or "general")
            groups[key].append(r)

        now = datetime.now(timezone.utc).isoformat()
        distilled = 0

        for key, mems in groups.items():
            if len(mems) < 3:
                continue
            mem_ids = [m["id"] for m in mems]
            source_ids = mem_ids[:10]
            ph = ",".join("?" * len(source_ids))

            # Extract common topics and merge related sentences
            source_content = conn.execute(
                f"SELECT content, subject FROM memories WHERE id IN ({ph})", source_ids
            ).fetchall()
            all_text = " ".join(r["content"] for r in source_content if r["content"])
            distinctive_topics = ""
            if len(all_text) > 50:
                words = re.findall(r'[一-鿿]{2,4}|[a-zA-Z]\w{2,}', all_text.lower())
                if words:
                    wf = Counter(w for w in words)
                    top_words = [w for w, _ in wf.most_common(6) if wf[w] >= 2][:5]
                    if top_words:
                        distinctive_topics = "、".join(top_words)

            # Build merged content: common themes + key sentences
            content_lines = []
            subjects = [r["subject"] for r in source_content if r["subject"]]
            unique_subjects = list(dict.fromkeys(s for s in subjects if s.strip()))[:3]
            if unique_subjects:
                content_lines.append("主题：" + " | ".join(unique_subjects))

            # Extract key sentences (first meaningful sentence from each source)
            key_sentences = []
            for r in source_content:
                text = (r["content"] or "").strip()
                # Take first sentence that's not just a template header
                first_sentence = text.split("。")[0].split("\n")[0][:150]
                if first_sentence and len(first_sentence) > 15:
                    key_sentences.append(first_sentence)
            # Dedup by first 40 chars
            seen = set()
            unique_sentences = []
            for s in key_sentences:
                key = s[:40]
                if key not in seen:
                    seen.add(key)
                    unique_sentences.append(s)
            if unique_sentences:
                content_lines.append("要点：" + " | ".join(unique_sentences[:3]))
            else:
                # Fallback: sample texts from latest sources
                samples = conn.execute(
                    f"SELECT content FROM memories WHERE id IN ({ph}) ORDER BY id DESC LIMIT 2",
                    source_ids
                ).fetchall()
                for s in samples:
                    line = (s["content"] or "").strip()[:200]
                    if line:
                        content_lines.append(f"• {line}")

            header = f"[L9 蒸馏] {key[0]} 在 {key[1]} 领域共 {len(mems)} 条"
            if distinctive_topics:
                header += f"\n关键词：{distinctive_topics}"
            merged_content = header + "\n" + "\n".join(content_lines)

            merged_content = header
            l9_subject = _smart_subject(merged_content)
            l9_subject = f"[L9 蒸馏] {l9_subject}"

            # Majority project from source memories
            source_ids = mem_ids[:10]
            ph = ",".join("?" * len(source_ids))
            proj_row = conn.execute(f"SELECT project, COUNT(*) as cnt FROM memories WHERE id IN ({ph}) AND project IS NOT NULL AND project != '' GROUP BY project ORDER BY cnt DESC LIMIT 1", source_ids).fetchone()
            l9_project = proj_row["project"] if proj_row else ""

            ch = hashlib.sha256(merged_content.encode()).hexdigest()
            # Thread: L9 distillation inherits from first source memory
            l9_thread_id = source_ids[0] if source_ids else None
            cur = conn.execute(
                "INSERT OR IGNORE INTO memories (content, content_hash, level, owner, agent_name, category, summary, created_at, updated_at, occurred_at, subject, project, trust_level, access_count, metadata, thread_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (merged_content, ch, "L9", "", normalize_agent_name(key[0]), key[1], l9_subject, now, now, now, l9_subject, l9_project, 0, 0, "{}", l9_thread_id),
            )
            if cur.rowcount == 0:
                # Duplicate hash → record already exists, skip
                continue
            new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

            for mid in mem_ids:
                # Append to supersedes as JSON array of IDs
                cur = conn.execute("SELECT COALESCE(supersedes, '[]') FROM memories WHERE id = ?", (mid,))
                existing_sup = cur.fetchone()[0]
                try:
                    sup_list = json.loads(existing_sup) if isinstance(existing_sup, str) and existing_sup.startswith('[') else []
                except (json.JSONDecodeError, TypeError):
                    sup_list = []
                if new_id not in sup_list:
                    sup_list.append(new_id)
                try:
                    conn.execute(
                        "UPDATE memories SET supersedes = ? WHERE id = ?",
                        (json.dumps(sup_list, ensure_ascii=False), mid),
                    )
                except sqlite3.OperationalError as e:
                    logger.warning("distill: supersedes update failed for %d: %s", mid, e)
                try:
                    # Only create edge if both memories still exist (avoids FK constraint failures)
                    exists = conn.execute(
                        "SELECT COUNT(*) FROM memories WHERE id IN (?, ?)",
                        (new_id, mid)
                    ).fetchone()[0]
                    if exists == 2:
                        conn.execute(
                            "INSERT OR IGNORE INTO edges (source_id, target_id, relation_type, weight, created_at) VALUES (?, ?, ?, ?, ?)",
                            (new_id, mid, "refines", 1.0, now),
                        )
                except sqlite3.IntegrityError as e:
                    logger.warning("distill: edge INSERT FK violation %d->%d: %s", new_id, mid, e)

            distilled += 1

        conn.commit()
        return {"distilled": distilled, "groups_processed": len(groups)}
    finally:
        conn.execute(f"PRAGMA foreign_keys={'ON' if fk_was_on else 'OFF'}")
        conn.close()


def cleanup_l9() -> dict:
    """Clean up low-quality L9 records and re-distill old-format ones.

    Steps:
    1. Delete L9 records whose *only* source edges point to P0/P1/P2 memories
       (these won't be regenerated since P0/P1/P2 are now excluded from distill).
    2. Re-distill remaining L9 records with old single-line format (pre-extractive)
       by regenerating content from their source memories.

    Returns:
        ``{"deleted": int, "re_distilled": int, "skipped": int}``
    """
    conn = get_conn()
    try:
        now = datetime.now(timezone.utc).isoformat()
        deleted = 0
        re_distilled = 0
        skipped = 0

        # Step 1: Find L9 memories and check their source edges
        l9_rows = conn.execute(
            "SELECT id FROM memories WHERE level = 'L9' ORDER BY id DESC LIMIT 1000"
        ).fetchall()

        for row in l9_rows:
            l9_id = row["id"]

            # Find source levels for this L9
            sources = conn.execute(
                "SELECT DISTINCT t.level FROM edges e "
                "JOIN memories t ON e.target_id = t.id "
                "WHERE e.source_id = ? AND e.relation_type = 'refines'",
                (l9_id,),
            ).fetchall()
            source_levels = {r["level"] for r in sources if r["level"]}

            if not source_levels:
                skipped += 1
                continue

            # If ALL sources are P0/P1/P2 → delete (won't be regenerated)
            if source_levels.issubset({"P0", "P1", "P2"}):
                conn.execute("DELETE FROM edges WHERE source_id = ?", (l9_id,))
                conn.execute("DELETE FROM memories WHERE id = ?", (l9_id,))
                deleted += 1
                continue

            # Check if L9 content is old-format (short, single-line join style)
            l9_row = conn.execute("SELECT content FROM memories WHERE id = ?", (l9_id,)).fetchone()
            if not l9_row:
                skipped += 1
                continue
            content = l9_row["content"] or ""
            # Old format: short first line (< 30 chars before first newline) or no newlines
            first_newline = content.find("\n")
            is_old_format = first_newline < 0 or first_newline < 30

            if not is_old_format:
                skipped += 1
                continue

            # Step 2: Re-distill — regenerate content from source memories
            source_mems = conn.execute(
                "SELECT m.id, m.content, m.summary FROM memories m "
                "JOIN edges e ON e.target_id = m.id AND e.relation_type = 'refines' "
                "WHERE e.source_id = ? AND m.level NOT IN ('P0', 'P1', 'P2')",
                (l9_id,),
            ).fetchall()

            if len(source_mems) < 2:
                # Not enough valid sources — keep as-is but note it
                skipped += 1
                continue

            texts = [m["summary"] or m["content"][:500] for m in source_mems[:10]]
            merged = summarize_extractive(texts, top_n=5, max_chars=2000)

            # Preserve the original [L9 蒸馏] header structure
            header_end = content.find("\n")
            agent_cat_part = content[9:header_end] if header_end > 0 else content[9:]
            agent_cat_part = agent_cat_part.strip()

            new_content = f"[L9 蒸馏] {agent_cat_part}\n{merged}"
            conn.execute(
                "UPDATE memories SET content = ?, updated_at = ? WHERE id = ?",
                (new_content, now, l9_id),
            )
            re_distilled += 1

        conn.commit()
        return {"deleted": deleted, "re_distilled": re_distilled, "skipped": skipped}
    finally:
        conn.close()
