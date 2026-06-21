import json
import math
from datetime import datetime, timezone
from collections import defaultdict, Counter
from memall.core.db import get_conn


def bridge_analysis_step() -> dict:
    conn = get_conn()
    try:
        now = datetime.now(timezone.utc).isoformat()
        total_edges = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        if total_edges == 0:
            return {"error": "no edges to analyze", "total_edges": 0}

        mc_count = conn.execute("SELECT COUNT(*) FROM memory_clusters").fetchone()[0]
        nc_count = conn.execute("SELECT COUNT(*) FROM narrative_clusters").fetchone()[0]
        cluster_table = None
        if mc_count > 0:
            cluster_table = "memory_clusters"
        elif nc_count > 0:
            cluster_table = "narrative_clusters"
        else:
            return {"error": "no clusters found, run cluster step first", "total_edges": total_edges}

        ALLOWED_CLUSTER_TABLES = {"memory_clusters", "narrative_clusters"}
        if cluster_table not in ALLOWED_CLUSTER_TABLES:
            return {"error": f"unknown cluster table: {cluster_table}", "total_edges": total_edges}

        results = {"total_edges": total_edges, "cluster_source": cluster_table, "agents": {}}

        if cluster_table == "narrative_clusters":
            mem_to_cluster = _build_memory_narrative_map(conn)
            if not mem_to_cluster:
                return {"error": "narrative_clusters exist but no memory-to-narrative mapping could be built", "total_edges": total_edges}
        else:
            mem_to_cluster = None

        agents = conn.execute(
            "SELECT DISTINCT LOWER(agent_name) as aname FROM memories WHERE agent_name != '' AND agent_name IS NOT NULL"
        ).fetchall()

        bridge_summary = {}
        all_cross = 0
        all_within = 0

        for row in agents:
            agent = row["aname"]
            agent_mem_ids = conn.execute(
                "SELECT id FROM memories WHERE LOWER(agent_name) = LOWER(?)", (agent,)
            ).fetchall()
            mem_set = set(r["id"] for r in agent_mem_ids)
            if not mem_set:
                continue

            agent_edges = conn.execute(
                "SELECT source_id, target_id, relation_type FROM edges WHERE source_id IN ({})".format(
                    ",".join("?" * len(mem_set))
                ),
                tuple(mem_set),
            ).fetchall()

            total = len(agent_edges)
            if total == 0:
                continue

            cross = 0
            within = 0
            unknown = 0
            bridge_types = Counter()
            # Batch-load cluster membership: single query instead of N+1
            if cluster_table == "narrative_clusters" and mem_to_cluster:
                cls_map = mem_to_cluster
            elif cluster_table == "memory_clusters":
                cls_rows = conn.execute(
                    "SELECT memory_id, cluster_id FROM memory_clusters WHERE memory_id IN ({})".format(
                        ",".join("?" * len(mem_set))
                    ),
                    tuple(mem_set),
                ).fetchall()
                cls_map = {r["memory_id"]: r["cluster_id"] for r in cls_rows}
            else:
                cls_map = {}

            for e in agent_edges:
                src_cid = cls_map.get(e["source_id"])
                tgt_cid = cls_map.get(e["target_id"])
                if src_cid is not None and tgt_cid is not None:
                    if src_cid != tgt_cid:
                        cross += 1
                    else:
                        within += 1
                    bridge_types[e["relation_type"]] += 1
                else:
                    unknown += 1

            bridge_ratio = round(cross / max(1, cross + within), 4)
            all_cross += cross
            all_within += within

            ag_info = {
                "total_edges": total,
                "mapped_edges": cross + within,
                "unknown_edges": unknown,
                "cross_cluster": cross,
                "within_cluster": within,
                "bridge_ratio": bridge_ratio,
                "bridge_types": dict(bridge_types.most_common(10)),
            }
            results["agents"][agent] = ag_info
            bridge_summary[agent] = bridge_ratio

        results["total_cross_cluster"] = all_cross
        results["total_within_cluster"] = all_within
        results["overall_bridge_ratio"] = round(all_cross / max(1, all_cross + all_within), 4)

        results["analyzed_at"] = now

        calibrate_persona_weights(conn, bridge_summary)

        return results

    except Exception as e:
        return {"error": str(e)}

    finally:
        conn.close()


def _build_memory_narrative_map(conn) -> dict:
    """Build {memory_id: cluster_id} from narratives.events JSON + narrative_clusters."""
    mapping = {}
    narratives = conn.execute("SELECT id, events FROM narratives").fetchall()
    if not narratives:
        return mapping
    nc_rows = conn.execute("SELECT narrative_id, cluster_id FROM narrative_clusters").fetchall()
    nc_map = {r["narrative_id"]: r["cluster_id"] for r in nc_rows}
    for nr in narratives:
        nid = nr["id"]
        cid = nc_map.get(nid)
        if cid is None:
            continue
        try:
            events = json.loads(nr["events"]) if isinstance(nr["events"], str) else nr["events"]
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(events, list):
            continue
        for ev in events:
            mid = ev.get("id") if isinstance(ev, dict) else None
            if mid is not None:
                mapping[mid] = cid
    return mapping


def calibrate_persona_weights(conn, bridge_ratios: dict):
    agents = conn.execute("SELECT agent_name, profile_json FROM identities WHERE profile_json IS NOT NULL AND profile_json != ''").fetchall()
    if not bridge_ratios:
        return

    ratios = list(bridge_ratios.values())
    if not ratios:
        return
    low = min(ratios)
    high = max(ratios)
    span = high - low if high > low else 1.0

    for row in agents:
        agent = row["agent_name"].lower()
        raw = row["profile_json"]
        if not raw:
            continue
        try:
            profile = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        features = profile.get("features", {})
        if not features:
            continue
        colors = profile.get("color_ratios", {})
        if not colors:
            continue

        bridge_ratio = bridge_ratios.get(agent, 0.0)
        norm_bridge = (bridge_ratio - low) / span if span > 0 else 0.5

        old_green = colors.get("green", 0)
        old_red = colors.get("red", 0)

        boost = norm_bridge * 0.15
        penalty = norm_bridge * 0.10

        colors["green"] = max(0, old_green + boost)
        colors["red"] = max(0, old_red - penalty)

        total = sum(colors.values()) or 1
        for k in colors:
            colors[k] = round(colors[k] / total, 3)

        profile["bridge_ratio"] = bridge_ratio
        profile["color_ratios"] = colors

        from .persona import colors_to_prototype
        profile["prototype"] = colors_to_prototype(colors)

        conn.execute("UPDATE identities SET profile_json = ?, persona_updated_at = ? WHERE LOWER(agent_name) = LOWER(?)",
                     (json.dumps(profile, ensure_ascii=False), datetime.now(timezone.utc).isoformat(), agent))
    conn.commit()
