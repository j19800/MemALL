import re
from memall.core.db import pool_conn
from memall.core.nlp import tokenize

JACCARD_THRESHOLD = 0.6
MAX_EDGES_PER_MEMORY = 10


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0
    return len(a & b) / len(a | b)


RELATION_PATTERNS = [
    (r'(但是|然而|不过|contrary|however)', 'contradicts'),
    (r'(基于|根据|参考|from|according)', 'cites'),
    (r'(细化|具体|展开|detail|specific)', 'refines'),
    (r'(决定后面|基于上一条|延续|继续|continue|further)', 'extends'),
    # updates: version bumps, migration, technology transition
    (r'(v\d+\.\d+|版本\d+|升级到|更新到|改用|回退到)', 'updates'),
    (r'(从\s*\S+\s*(改为|变成|迁移到|切换到|升级到|降级到))', 'updates'),
    (r'(弃用|废弃|不再使用|停止支持)', 'updates'),
    # derives: conclusion drawn from prior reasoning
    (r'(由此|据此|综上|综上所述|归纳|推导|可以得出)', 'derives'),
    (r'(总结[:：]|结论[:：]|综上所述)', 'derives'),
    (r'(延伸|引申|推而广之)', 'derives'),
]

# Contradiction detection: opposite stance on same subject
CONTRADICT_PAIRS = [
    (r'用\s+\S+|采用\s+\S+|选择\s+\S+|替代\s+\S+|迁移到\s+\S+', r'不用|废弃|放弃|拒绝|回退|不推荐|反对'),
    (r'推荐|可靠|好方案|最优|首选', r'不推荐|不可靠|差方案|有问题|不好'),
    (r'应该|需要|必须|一定要', r'不应该|不需要|不必|不该|没必要'),
    (r'同意|支持|认可|赞同', r'反对|不同意|不认可|不赞同|质疑'),
    (r'简单|容易|方便|快速', r'复杂|困难|麻烦|缓慢'),
    (r'保留|继续用|维持', r'迁移|替换|改用|替代'),
    (r'好|优|有利|优势|优点', r'差|劣|不利|劣势|缺点|不足'),
]


def _infer_relation(text_a: str, text_b: str) -> str:
    for pattern, rel in RELATION_PATTERNS:
        if re.search(pattern, text_b, re.IGNORECASE):
            return rel
    # Check for contradiction: same subject mentioned with opposite stance
    for pos_pat, neg_pat in CONTRADICT_PAIRS:
        pos_a = re.search(pos_pat, text_a)
        pos_b = re.search(pos_pat, text_b)
        neg_a = re.search(neg_pat, text_a)
        neg_b = re.search(neg_pat, text_b)
        if (pos_a and neg_b) or (neg_a and pos_b):
            return 'contradicts'
    return 'refines'


def _prune_excess_edges(conn) -> int:
    """Remove weakest edges from memories that exceed MAX_EDGES_PER_MEMORY.
    
    For each memory, sorts its edges by weight descending and keeps only
    the top MAX_EDGES_PER_MEMORY.  Returns number of edges deleted.
    """
    deleted = 0
    for col in ("source_id", "target_id"):
        rows = conn.execute(
            f"SELECT {col} as mem_id, COUNT(*) as cnt FROM edges GROUP BY {col} HAVING cnt > ?",
            (MAX_EDGES_PER_MEMORY,),
        ).fetchall()
        for r in rows:
            mid = r["mem_id"]
            # Get IDs of edges to keep (top N by weight)
            keep = conn.execute(
                f"SELECT id FROM edges WHERE {col} = ? ORDER BY weight DESC LIMIT ?",
                (mid, MAX_EDGES_PER_MEMORY),
            ).fetchall()
            keep_ids = set(k[0] for k in keep)
            # Delete the rest
            all_edge_ids = conn.execute(
                f"SELECT id FROM edges WHERE {col} = ?", (mid,)
            ).fetchall()
            for ae in all_edge_ids:
                if ae[0] not in keep_ids:
                    conn.execute("DELETE FROM edges WHERE id = ?", (ae[0],))
                    deleted += 1
    return deleted


def link_step() -> int:
    with pool_conn() as conn:
        # Phase 1: prune existing excess edges before adding new ones
        pruned = _prune_excess_edges(conn)
        conn.commit()

        done_pairs = set()
        existing = conn.execute(
            "SELECT source_id, target_id, relation_type FROM edges"
        ).fetchall()
        for e in existing:
            done_pairs.add((e["source_id"], e["target_id"], e["relation_type"]))

        rows = conn.execute(
            "SELECT id, content, category FROM memories WHERE level != 'P0' ORDER BY id"
        ).fetchall()

        tokens_map = {r["id"]: set(tokenize(r["content"])) for r in rows}

        # Pre-compute current edge counts for quick cap check
        edge_count: dict[int, int] = {}
        for e in existing:
            edge_count[e["source_id"]] = edge_count.get(e["source_id"], 0) + 1

        count = 0
        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                a, b = rows[i], rows[j]
                # Skip if either node already at edge capacity
                if edge_count.get(a["id"], 0) >= MAX_EDGES_PER_MEMORY:
                    continue
                if edge_count.get(b["id"], 0) >= MAX_EDGES_PER_MEMORY:
                    continue

                sim = _jaccard(tokens_map[a["id"]], tokens_map[b["id"]])
                rel = None
                if sim >= JACCARD_THRESHOLD:
                    rel = _infer_relation(a["content"], b["content"])
                elif sim >= 0.2:
                    test_rel = _infer_relation(a["content"], b["content"])
                    if test_rel == 'contradicts':
                        rel = 'contradicts'
                if rel:
                    if (a["id"], b["id"], rel) not in done_pairs:
                        # Ontology upgrade: if a more specific relation is detected,
                        # upgrade any existing broader relation
                        upgraded = False
                        for broader in ('refines', 'cites'):
                            if rel != broader and (a["id"], b["id"], broader) in done_pairs:
                                conn.execute(
                                    "UPDATE edges SET relation_type = ? WHERE source_id = ? AND target_id = ? AND relation_type = ?",
                                    (rel, a["id"], b["id"], broader),
                                )
                                upgraded = True
                                break
                        if not upgraded:
                            conn.execute(
                                "INSERT OR IGNORE INTO edges (source_id, target_id, relation_type, weight, created_at, metadata) VALUES (?,?,?,?,datetime('now'),'{}')",
                                (a["id"], b["id"], rel, round(sim, 2)),
                            )
                            edge_count[a["id"]] = edge_count.get(a["id"], 0) + 1
                            edge_count[b["id"]] = edge_count.get(b["id"], 0) + 1
                        count += 1
                        done_pairs.add((a["id"], b["id"], rel))

        conn.commit()
        return count
