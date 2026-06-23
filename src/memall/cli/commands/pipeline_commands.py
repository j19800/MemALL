"""Pipeline CLI commands: pipeline, forget, persona, cluster, cluster-show, narrative,
suggest, bridge, ask, adaptive, security, ops."""

import json
import sys
from pathlib import Path

from memall.core.db import get_conn
from memall.core.thin_waist import retrieve
from memall.pipeline import run_pipeline
from memall.pipeline.adaptive import adaptive_clean, adaptive_index, adaptive_distill, adaptive_step, adaptive_report
from memall.pipeline.forget import forget_stats, forget_review, forget_expired, forget_low_value, forget_step
from memall.pipeline.ops import merge_memories, split_memory, tag_memory, batch_tag, batch_archive, batch_restore, deduplicate
from memall.pipeline.persona import generate_persona, persona_step, generate_profile_3layer
from memall.pipeline.security import audit_sensitive, set_permission, check_access, security_score, list_agents_by_permission


# ──────────────────────────────────────────────
# Helper functions
# ──────────────────────────────────────────────

def print_persona(agent: str, profile: dict, compact: bool = False):
    proto = profile.get("prototype", {})
    features = profile.get("features", {})
    colors = profile.get("color_ratios", {})

    if compact:
        pc = proto.get("primary_color", {})
        sc = proto.get("secondary_color")
        color_str = f"{pc.get('name','?')}" + (f"+{sc.get('name','?')}" if sc else "")
        print(f"  {proto.get('cn', '?')} ({proto.get('en', '?')}) | {color_str} | n={features.get('sample_size',0)}")
        sorted_c = sorted(colors.items(), key=lambda x: -x[1])
        bar = " ".join(f"{k}={v:.0%}" for k, v in sorted_c[:3])
        if bar:
            print(f"  Colors: {bar}")
        time_range = profile.get("time_range", "")
        if time_range:
            print(f"  Time range: {time_range}")
        return

    print(f"=== {agent} Persona ===")
    print(f"Prototype: {proto.get('cn', '?')} ({proto.get('en', '?')})")

    pc = proto.get("primary_color", {})
    sc = proto.get("secondary_color")
    if sc:
        print(f"Colors: {pc.get('name','?')}·{pc.get('meaning','?')} (main) + {sc.get('name','?')}·{sc.get('meaning','?')}")
    else:
        print(f"Colors: {pc.get('name','?')}·{pc.get('meaning','?')} (single)")

    print(f"Sample size: {features.get('sample_size', 0)} memories")

    sorted_c = sorted(colors.items(), key=lambda x: -x[1])
    print("Color ratios:")
    for k, v in sorted_c:
        bar = "█" * int(v * 20) + "░" * (20 - int(v * 20))
        print(f"  {k:6s} {bar} {v:.1%}")

    print("Key features:")
    for k, v in sorted(features.items(), key=lambda x: -abs(x[1]) if isinstance(x[1], (int, float)) else 0)[:8]:
        if k != "sample_size":
            print(f"  {k}: {v}")
    print()


def print_profile_3layer(agent: str, profile: dict, layer: str = "all"):
    """Print 3-layer Agent Profile (Phase 10)."""
    l1 = profile.get("layer_1_cognitive", {})
    l2 = profile.get("layer_2_topology", {})
    l3 = profile.get("layer_3_behavioral", {})

    print(f"=== {agent} 3-Layer Profile ===")
    print(f"Generated at: {profile.get('generated_at', '?')}\n")

    # Layer 1
    if layer in ("1", "all"):
        proto = l1.get("prototype", {})
        feat = l1.get("features", {})
        colors = l1.get("color_ratios", {})
        print(f"--- Layer 1: Cognitive Features ---")
        print(f"  Prototype: {proto.get('cn', '?')} ({proto.get('en', '?')})")
        pc = proto.get("primary_color", {})
        sc = proto.get("secondary_color")
        if sc:
            print(f"  Colors: {pc.get('name','?')}·{pc.get('meaning','?')} + {sc.get('name','?')}·{sc.get('meaning','?')}")
        else:
            print(f"  Colors: {pc.get('name','?')}·{pc.get('meaning','?')}")
        print(f"  Sample size: {feat.get('sample_size', 0)} memories")
        print(f"  Certainty: {feat.get('certainty_score', 0):.3f}  Decision ratio: {feat.get('decision_ratio', 0):.3f}")
        print(f"  Domain breadth: {feat.get('domain_breadth', 0)}  Depth: {feat.get('domain_depth', 0):.3f}")
        print(f"  Contradiction resolution: {feat.get('contradiction_resolution', 0):.3f}")
        if colors:
            print("  Color ratios:", ", ".join(f"{k}={v:.1%}" for k, v in sorted(colors.items(), key=lambda x: -x[1])))
        print()

    # Layer 2
    if layer in ("2", "all"):
        print(f"--- Layer 2: Network Topology ---")
        if "error" in l2:
            print(f"  Error: {l2['error']}")
        else:
            deg = l2.get("degree", {})
            print(f"  Memory count: {l2.get('memory_count', 0)}")
            print(f"  Out-degree: avg={deg.get('avg_out', 0)}, max={deg.get('max_out', 0)}, total={deg.get('total_out_edges', 0)}")
            print(f"  In-degree:  avg={deg.get('avg_in', 0)}, max={deg.get('max_in', 0)}, total={deg.get('total_in_edges', 0)}")
            print(f"  Internal edges: {l2.get('internal_edges', 0)}  External edges: {l2.get('external_edges', 0)}")
            print(f"  Network leverage: {l2.get('network_leverage', 0):.3f}")
            print(f"  Clustering coefficient: {l2.get('clustering_coefficient', 0):.4f}")
            print(f"  Contradiction self-index: {l2.get('contradiction_self_index', 0)}")
            print(f"  Global edge share: {l2.get('global_edge_share', 0):.4%}")
            bridges = l2.get("bridge_nodes", [])
            print(f"  Bridge nodes: {l2.get('bridge_count', 0)}")
            for b in bridges[:3]:
                print(f"    memory_id={b['memory_id']}, rel_types={b['relation_types']}, out_targets={b['out_targets']}")
        print()

    # Layer 3
    if layer in ("3", "all"):
        print(f"--- Layer 3: Behavioral Patterns ---")
        if "error" in l3:
            print(f"  Error: {l3['error']}")
        else:
            rhythm = l3.get("time_rhythm", {})
            flow = l3.get("domain_flow", {})
            bursts = l3.get("bursts", {})
            sess = l3.get("sessions", {})
            print(f"  Total interactions: {l3.get('total_interactions', 0)}  Span: {l3.get('span_days', 0)} days")
            print(f"  Peak hour: {rhythm.get('peak_hour', '?')}:00  Active hours/day: {rhythm.get('active_hours', 0)}")
            print(f"  Peak day: {rhythm.get('peak_day', '?')}")
            print(f"  Category entropy: {flow.get('category_entropy', 0):.3f}  Stickiness: {flow.get('stickiness', 0):.3f}")
            top_t = flow.get("top_transitions", [])
            if top_t:
                tt_parts = [f"{t['from_to']}({t['count']})" for t in top_t[:3]]
                print(f"  Top transitions: {', '.join(tt_parts)}")
            print(f"  Avg interval: {bursts.get('avg_interval_seconds', 0):.0f}s  Burst count: {bursts.get('burst_count', 0)}")
            if bursts.get('burst_sizes'):
                print(f"  Burst sizes: {bursts['burst_sizes'][:5]}")
            print(f"  Sessions: {sess.get('total_sessions', 0)} total, "
                  f"{sess.get('avg_memories_per_session', 0)} mems/session avg, "
                  f"{sess.get('avg_duration_min', 0)}min/session avg")
        print()


# ──────────────────────────────────────────────
# cmd_pipeline
# ──────────────────────────────────────────────

def cmd_pipeline(args):
    result = run_pipeline(dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))


# ──────────────────────────────────────────────
# cmd_forget
# ──────────────────────────────────────────────

def cmd_forget(args):
    """CLI handler for `memall forget` — Phase 11 automatic forgetting."""
    if args.expired:
        result = forget_expired(days=args.days or 90, agent_name=args.agent or None)
        print(f"Expired cleanup: {result['deleted_memories']} memories, {result['deleted_edges']} edges deleted")

    elif args.low_value:
        result = forget_low_value(agent_name=args.agent or None)
        print(f"Low-value cleanup: {result['deleted_memories']}/{result['candidate_count']} candidates deleted")

    elif args.review:
        result = forget_review(days=args.days or 90, agent_name=args.agent or None)
        print(f"Review — expired: {result['expired_candidates']}, low-value: {result['low_value_candidates']}")
        if result["details"]:
            print("Top candidates:")
            for d in result["details"]:
                print(f"  [{d['type']:10s}] #{d['id']} | {d['content_preview'][:50]} | {d['created_at']} | {d['agent_name']}")

    elif args.stats:
        result = forget_stats()
        print(f"Database stats:")
        print(f"  Total:        {result['total_memories']} memories, {result['total_edges']} edges")
        print(f"  Expired:      {result['expired_count']} (>{args.days or 90}d)")
        print(f"  Low-value:    {result['low_value_count']}")
        print(f"  Orphan edges: {result['orphaned_edge_count']}")
        if result["oldest_memory_date"]:
            print(f"  Span:         {result['oldest_memory_date']} .. {result['newest_memory_date']}")
        print(f"  Avg length:   {result['avg_content_length']} chars")
        print(f"  Est size:     {result['size_estimate_mb']} MB")

    elif args.all:
        result = forget_step(days=args.days or 90, agent_name=args.agent or None)
        exp = result["expired"]
        low = result["low_value"]
        print(f"Forget step complete:")
        print(f"  Expired:   {exp['deleted_memories']} memories, {exp['deleted_edges']} edges")
        print(f"  Low-value: {low['deleted_memories']}/{low['candidate_count']} candidates")
        print(f"  Total:     {result['total_deleted_memories']} memories, {result['total_deleted_edges']} edges")

    else:
        # Default: show stats
        result = forget_stats()
        print(f"memall forget — Phase 11")
        print(f"  {result['total_memories']} total | {result['expired_count']} expired | {result['low_value_count']} low-value")
        print(f"  Use --review to preview, --expired / --low-value / --all to execute")


# ──────────────────────────────────────────────
# cmd_persona
# ──────────────────────────────────────────────

def cmd_persona(args):
    # Phase 10: 3-layer profile
    if args.profile and args.agent:
        profile = generate_profile_3layer(args.agent)
        if "error" in profile:
            print(f"Error: {profile['error']}", file=sys.stderr)
            sys.exit(1)
        print_profile_3layer(args.agent, profile, layer=args.layer)
        return

    # GAP-9: Evolution tracking
    if args.evolution and args.agent:
        from memall.pipeline.persona import get_evolution
        result = get_evolution(args.agent, window_days=args.window)
        if "error" in result:
            print(f"Error: {result['error']}", file=sys.stderr)
            sys.exit(1)
        print(f"=== {args.agent} Persona Evolution ({result['total_windows']} windows, {result['span_days']}d span) ===")
        print(f"Trend: activity={result['trend']['activity']}, "
              f"certainty={result['trend']['certainty']}, "
              f"decision_making={result['trend']['decision_making']}")
        for w in result["windows"]:
            bar = "█" * min(w["count"], 40)
            print(f"  {w['label']} | {bar:40s} | {w['count']:4d} entries "
                  f"(certain:{w['certain']}, uncertain:{w['uncertain']}, decisions:{w['decisions']})")
        if result.get("current_persona"):
            print(f"\nCurrent persona: {result['current_persona'].get('cn', '?')}")
        return

    # GAP-9: Multi-agent comparison
    if args.compare:
        from memall.pipeline.persona import compare_personas
        result = compare_personas(args.compare)
        agents_data = result.get("agents", {})
        similarities = result.get("similarities", {})
        print(f"=== Agent Persona Comparison ===")
        for agent_name, data in agents_data.items():
            if "error" in data:
                print(f"{agent_name}: ERROR — {data['error']}")
            else:
                print(f"\n  {agent_name}: {data['prototype_cn']} ({data['prototype_en']}) "
                      f"[primary: {data['primary_color']}], n={data['sample_size']}")
                print(f"    certainty={data['certainty_score']:.2f}, "
                      f"decision_ratio={data['decision_ratio']:.2f}, "
                      f"domain_breadth={data['domain_breadth']}, depth={data['domain_depth']}")
        if similarities:
            print(f"\n  Similarity Matrix:")
            for pair, sim in sorted(similarities.items(), key=lambda x: -x[1]):
                print(f"    {pair}: {sim:.3f}")
        return

    if args.agent:
        mode = getattr(args, "mode", "static")
        dynamic_days = getattr(args, "dynamic_days", 7)

        if mode == "dual":
            from memall.pipeline.persona import generate_dual_persona
            profile = generate_dual_persona(args.agent, dynamic_days=dynamic_days)
            if "error" in profile:
                print(profile["error"], file=sys.stderr)
                sys.exit(1)
            print(f"=== {args.agent} 双画像 (static + dynamic) ===")
            print()
            s = profile.get("static", {})
            d = profile.get("dynamic", {})
            print("【静态画像 — 全量历史】")
            print_persona(args.agent, s, compact=True)
            print()
            print("【动态画像 — 最近 " + str(dynamic_days) + " 天】")
            if d.get("note"):
                print(f"  ⚠ {d['note']}")
            else:
                print_persona(args.agent, d, compact=True)
            print()
            delta = profile.get("delta", {})
            print("【变化趋势】")
            print(f"  原型变化:     {delta.get('prototype_shift', '?')}")
            print(f"  活跃比例:     {delta.get('activity_ratio', 0):.1%}")
            color_deltas = delta.get("color_deltas", {})
            if color_deltas:
                print(f"  色彩偏移:")
                for c, dv in color_deltas.items():
                    arrow = "↑" if dv > 0 else "↓"
                    print(f"    {c}: {arrow} {abs(dv):.3f}")
            return

        if mode == "dynamic":
            profile = generate_persona(args.agent, time_range=f"recent_{dynamic_days}d")
        else:
            profile = generate_persona(args.agent, time_range="all")

        if "error" in profile:
            print(profile["error"], file=sys.stderr)
            sys.exit(1)
        print_persona(args.agent, profile)
    else:
        result = persona_step()
        print(f"Generated personas for {result['agents_processed']} agents:")
        for name, info in sorted(result.get("agents", {}).items()):
            print(f"  {name}: {info['prototype_cn']} ({info['prototype_en']}) [{info['sample_size']} memories]")


# ──────────────────────────────────────────────
# cmd_cluster
# ──────────────────────────────────────────────

def cmd_cluster(args):
    from memall.pipeline.cluster import cluster_step
    result = cluster_step(method=args.method)
    status = "PASS" if result.get("coherence_pass") else "FAIL"
    print(f"Method: {result.get('method', 'tfidf')}")
    print(f"Created {result['clusters_created']} clusters from {result['memories_clustered']} memories")
    print(f"Coherence: {result['coherence']} (threshold: {result['threshold']}) [{status}]")
    if result.get("clusters_created", 0) and args.show:
        from memall.core.db import get_conn
        conn = get_conn()
        clusters = conn.execute("SELECT id, label, member_count, coherence_score FROM clusters ORDER BY id").fetchall()
        for c in clusters:
            members = conn.execute("SELECT COUNT(*) FROM memory_clusters WHERE cluster_id = ?", (c["id"],)).fetchone()[0]
            print(f"  #{c['id']} {c['label']} — {c['member_count']} mems, coherence={c['coherence_score']}")
        conn.close()


# ──────────────────────────────────────────────
# cmd_cluster_show
# ──────────────────────────────────────────────

def cmd_cluster_show(args):
    from memall.core.db import get_conn
    from memall.core.thin_waist import retrieve
    conn = get_conn()
    c = conn.execute("SELECT * FROM clusters WHERE id = ?", (args.id,)).fetchone()
    if not c:
        print(f"cluster {args.id} not found", file=sys.stderr)
        sys.exit(1)
    print(f"Cluster #{c['id']}: {c['label']}")
    print(f"Members: {c['member_count']} | Coherence: {c['coherence_score']}")
    centroid = retrieve(c["centroid_memory_id"])
    if centroid:
        print(f"Centroid: {centroid.content[:200]}")
    members = conn.execute(
        "SELECT m.id, m.content, m.category, mc.distance FROM memory_clusters mc JOIN memories m ON mc.memory_id = m.id WHERE mc.cluster_id = ? ORDER BY mc.distance LIMIT 10",
        (args.id,),
    ).fetchall()
    print("Top members:")
    for m in members:
        print(f"  #{m['id']} [{m['category']}] dist={m['distance']:.3f} {m['content'][:100]}")
    conn.close()


# ──────────────────────────────────────────────
# cmd_narrative
# ──────────────────────────────────────────────

def cmd_narrative(args):
    from memall.pipeline.narrative import generate_agent_narrative
    result = generate_agent_narrative(args.agent, args.span, narrative_type=args.type)
    label = {"weekly": "一周", "monthly": "一个月", "phase": "整个阶段"}
    print(f"=== {result['agent']} — 过去 {label.get(result['narrative_type'], result['narrative_type'])} ===")
    print(f"记忆数: {result['events']}")
    print()
    print(result["narrative"])


# ──────────────────────────────────────────────
# cmd_suggest
# ──────────────────────────────────────────────

def cmd_suggest(args):
    from memall.core.db import get_conn
    from datetime import datetime, timezone
    conn = get_conn()
    try:
        now = datetime.now(timezone.utc).isoformat()

        if args.stats:
            rows = conn.execute("SELECT status, COUNT(*) as cnt FROM suggestions GROUP BY status").fetchall()
            total = conn.execute("SELECT COUNT(*) FROM suggestions").fetchone()[0]
            pending = conn.execute("SELECT COUNT(*) FROM suggestions WHERE status='pending'").fetchone()[0]
            accepted = conn.execute("SELECT COUNT(*) FROM suggestions WHERE status='accepted'").fetchone()[0]
            in_prog = conn.execute("SELECT COUNT(*) FROM suggestions WHERE status='in_progress'").fetchone()[0]
            done = conn.execute("SELECT COUNT(*) FROM suggestions WHERE status='implemented'").fetchone()[0]
            rejected = conn.execute("SELECT COUNT(*) FROM suggestions WHERE status='rejected'").fetchone()[0]
            wontfix = conn.execute("SELECT COUNT(*) FROM suggestions WHERE status='wontfix'").fetchone()[0]
            rate = round(done / (total - rejected - wontfix) * 100, 1) if (total - rejected - wontfix) > 0 else 0
            print(f"Total: {total}")
            print(f"  pending:     {pending}")
            print(f"  accepted:    {accepted}")
            print(f"  in_progress: {in_prog}")
            print(f"  implemented: {done}")
            print(f"  rejected:    {rejected}")
            print(f"  wontfix:    {wontfix}")
            print(f"Adoption rate: {rate}%")

            if args.category:
                cat_rows = conn.execute("SELECT category, COUNT(*) as cnt FROM suggestions GROUP BY category ORDER BY cnt DESC").fetchall()
                print(f"\nBy category:")
                for r in cat_rows:
                    print(f"  {r['category']}: {r['cnt']}")

        elif args.list is not False:
            query = "SELECT * FROM suggestions"
            params = []
            conditions = []
            if args.status:
                conditions.append("status = ?")
                params.append(args.status)
            if args.category:
                conditions.append("category = ?")
                params.append(args.category)
            if conditions:
                query += " WHERE " + " AND ".join(conditions)
            query += " ORDER BY id DESC LIMIT 100"
            rows = conn.execute(query, params).fetchall()
            if not rows:
                print("No suggestions found.")
            for r in rows:
                status_icon = {"pending": "⏳", "accepted": "✅", "rejected": "❌", "in_progress": "🔨", "implemented": "🎉", "wontfix": "🚫"}
                icon = status_icon.get(r["status"], "?")
                print(f"{icon} #{r['id']} [{r['status']}] {r['content'][:120]}")
                print(f"   cat={r['category']} pri={r['priority']} by={r['created_by']} src={r['source_type']}#{r['source_id']}")
                print()

        elif args.accept:
            cur = conn.execute("UPDATE suggestions SET status='accepted', accepted_at=? WHERE id=? AND status='pending'", (now, args.accept))
            if cur.rowcount:
                print(f"Suggestion #{args.accept} accepted.")
            else:
                print(f"Suggestion #{args.accept} not found or not in pending status.")

        elif args.reject:
            reason = args.reason or "No reason given"
            cur = conn.execute("UPDATE suggestions SET status='rejected', rejection_reason=? WHERE id=? AND status='pending'", (reason, args.reject))
            if cur.rowcount:
                print(f"Suggestion #{args.reject} rejected: {reason}")
            else:
                print(f"Suggestion #{args.reject} not found or not in pending status.")

        elif args.start:
            cur = conn.execute("UPDATE suggestions SET status='in_progress' WHERE id=? AND status='accepted'", (args.start,))
            if cur.rowcount:
                print(f"Suggestion #{args.start} started.")
            else:
                print(f"Suggestion #{args.start} not found or not in accepted status.")

        elif args.done:
            cur = conn.execute("UPDATE suggestions SET status='implemented', implemented_at=? WHERE id=? AND status='in_progress'", (now, args.done))
            if cur.rowcount:
                print(f"Suggestion #{args.done} implemented.")
            else:
                print(f"Suggestion #{args.done} not found or not in in_progress status.")

        elif args.wontfix:
            reason = args.reason or "No reason given"
            cur = conn.execute("UPDATE suggestions SET status='wontfix', rejection_reason=? WHERE id=? AND (status='pending' OR status='accepted')", (reason, args.wontfix))
            if cur.rowcount:
                print(f"Suggestion #{args.wontfix} marked wontfix: {reason}")
            else:
                print(f"Suggestion #{args.wontfix} not found or not in pending/accepted status.")

        elif args.import_file:
            path = Path(args.import_file)
            if not path.exists():
                print(f"File not found: {args.import_file}", file=sys.stderr)
                sys.exit(1)
            text = path.read_text(encoding="utf-8")
            from memall.pipeline.suggest import _extract_from_content, _detect_category
            items = _extract_from_content(text)
            count = 0
            for item in items:
                if conn.execute("SELECT COUNT(*) FROM suggestions WHERE substr(content,1,80)=substr(?,1,80)", (item,)).fetchone()[0] == 0:
                    cat = _detect_category(item)
                    conn.execute(
                        "INSERT INTO suggestions (source_type, source_id, content, category, priority, status, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        ("design_doc", None, item, cat, "P2", "pending", "marvis", now),
                    )
                    count += 1
            print(f"Imported {count} suggestions from {args.import_file}")

        else:
            print("Use --list, --stats, --accept, --reject, --start, --done, or --import. See memall suggest --help")

        conn.commit()
    finally:
        conn.close()


# ──────────────────────────────────────────────
# cmd_bridge
# ──────────────────────────────────────────────

def cmd_bridge(args):
    from memall.pipeline.bridge import bridge_analysis_step
    result = bridge_analysis_step()
    if "error" in result:
        print(f"Bridge analysis failed: {result['error']}", file=sys.stderr)
        sys.exit(1)
    print(f"Total edges: {result['total_edges']}")
    print(f"Cross-cluster edges: {result.get('total_cross_cluster', 0)}")
    print(f"Within-cluster edges: {result.get('total_within_cluster', 0)}")
    print(f"Overall bridge ratio: {result.get('overall_bridge_ratio', 0)}")
    if args.show:
        agents = result.get("agents", {})
        if args.agent:
            agents = {k: v for k, v in agents.items() if k == args.agent.lower()}
        for agent, info in sorted(agents.items()):
            print(f"\n  {agent}:")
            print(f"    bridge_ratio={info['bridge_ratio']} ({info['cross_cluster']}/{info['mapped_edges']} mapped)")
            print(f"    top types: {dict(list(info['bridge_types'].items())[:5])}")


# ──────────────────────────────────────────────
# cmd_ask
# ──────────────────────────────────────────────

def cmd_ask(args):
    from memall.pipeline.ask import ContextAssembler
    result = ContextAssembler.ask(args.query, subject=args.subject, mode=args.mode, scope=args.scope)
    if "error" in result:
        print(f"Ask failed: {result['error']}", file=sys.stderr)
        sys.exit(1)
    print(result["answer"])
    if result.get("citations"):
        print(f"\n引用: {result['citations']}")
    print(f"\n-- {result.get('disclaimer', '')}")


# ──────────────────────────────────────────────
# cmd_adaptive
# ──────────────────────────────────────────────

def cmd_adaptive(args):
    """CLI handler for `memall adaptive` — Phase 12 AI Adaptive Subsystem."""
    agent = args.agent or None

    if args.report:
        result = adaptive_report()
        print("=== Adaptive Subsystem Report ===")
        print(f"  Total memories:       {result['total_memories']}")
        print(f"  Recent 7d memories:   {result['recent_7d_memories']}")
        print(f"  Growth rate (7d):     {result['growth_rate_7d']:.2%}")
        print(f"  Mode suggestion:      {result['mode_suggestion']}")
        print(f"  Query log entries:    {result['query_log_total']}")
        print(f"  Acceleration tables:  {result['accel_table_count']}")
        print(f"  Distill history (last 5):")
        for h in result.get("distill_history_recent", []):
            print(f"    #{h['id']} {h['agent_name']:12s} {h['mode']:10s} "
                  f"{h['memory_before']}->{h['memory_after']} mems  {h['triggered_at'][:19]}")
        return

    if args.all:
        result = adaptive_step(agent_name=agent)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.clean:
        result = adaptive_clean(agent_name=agent)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.index:
        result = adaptive_index(agent_name=agent)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.distill:
        result = adaptive_distill(agent_name=agent)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    # Default: show report
    result = adaptive_report()
    print(json.dumps(result, ensure_ascii=False, indent=2))


# ──────────────────────────────────────────────
# cmd_security
# ──────────────────────────────────────────────

def cmd_security(args):
    """CLI handler for `memall security` — Phase 13 security governance."""
    action = getattr(args, "action", None)

    if action == "audit":
        result = audit_sensitive(agent_name=args.agent or None)
        print(f"Security Audit — scanned {result['total_scanned']} memories")
        print(f"  Findings: {result['findings']} | Risk: {result['risk_level']}")
        print(f"  By type: {json.dumps(result['by_type'], ensure_ascii=False)}")
        if result["details"]:
            print(f"\n  Top findings:")
            for d in result["details"][:10]:
                print(f"    #{d['memory_id']} [{d['match_type']:7s}] {d['agent_name']:12s}  {d['match_preview'][:80]}")

    elif action == "permit":
        if not args.agent_name or not args.level:
            print("error: --agent and --level are required", file=sys.stderr)
            sys.exit(1)
        result = set_permission(args.agent_name, args.level)
        if "error" in result:
            print(f"error: {result['error']}", file=sys.stderr)
            sys.exit(1)
        print(f"Permission set: {result['agent_name']} -> {result['level']}")

    elif action == "check":
        if not args.requester or not args.target:
            print("error: --from and --to are required", file=sys.stderr)
            sys.exit(1)
        result = check_access(args.requester, args.target)
        status = "ALLOWED" if result["allowed"] else "DENIED"
        print(f"Access check: {status}")
        print(f"  {result['reason']}")

    elif action == "score":
        result = security_score()
        print(f"Security Score: {result['score']:.1f} / 100  Grade: {result['grade']}")
        print(f"  Breakdown:")
        for k, v in result["breakdown"].items():
            print(f"    {k}: {v}")
        if result["recommendations"]:
            print(f"\n  Recommendations:")
            for r in result["recommendations"]:
                print(f"    - {r}")

    elif action == "list":
        level = getattr(args, "level", "private")
        agents = list_agents_by_permission(level)
        if not agents:
            print(f"No agents with permission level '{level}'.")
        else:
            print(f"Agents with permission '{level}' ({len(agents)}):")
            for a in agents:
                print(f"  {a['agent_name']:20s} type={a['agent_type']}")

    else:
        print("Usage: memall security {audit|permit|check|score|list}")
        print("  audit [--agent X]")
        print("  permit --agent X --level public|trusted|private")
        print("  check --from AGENT --to AGENT")
        print("  score")
        print("  list --level public|trusted|private")


# ──────────────────────────────────────────────
# cmd_ops
# ──────────────────────────────────────────────

def cmd_ops(args):
    """CLI handler for `memall ops` — Phase 14 memory operations."""
    action = getattr(args, "action", None)

    if action == "merge":
        if not args.source_id or not args.target_id:
            print("error: --from and --to are required", file=sys.stderr)
            sys.exit(1)
        result = merge_memories(args.source_id, args.target_id)
        print(f"Merged memory #{args.source_id} into #{args.target_id}")
        print(f"  Edges redirected: {result['edges_redirected']}")

    elif action == "split":
        if not args.split_id:
            print("error: --id is required", file=sys.stderr)
            sys.exit(1)
        delim = args.delimiter or "\n\n"
        # Support literal "\n" in CLI args
        if delim.startswith("\\"):
            delim = delim.encode().decode("unicode_escape")
        result = split_memory(args.split_id, delimiter=delim)
        print(f"Split memory #{result['original_id']} -> {result['split_count']} new memories")
        if result.get("new_ids"):
            print(f"  New IDs: {result['new_ids']}")

    elif action == "tag":
        if not args.tag_id:
            print("error: --id is required", file=sys.stderr)
            sys.exit(1)
        tag_list = [t.strip() for t in (args.tags or "").split(",") if t.strip()]
        if not tag_list:
            print("error: --tags is required (comma-separated)", file=sys.stderr)
            sys.exit(1)
        mode = args.mode or "add"
        result = tag_memory(args.tag_id, tag_list, mode=mode)
        print(f"Tagged #{result['memory_id']} ({result['mode']}): {result['tags']}")

    elif action == "batch-tag":
        if not args.agent or not args.category:
            print("error: --agent and --category are required", file=sys.stderr)
            sys.exit(1)
        tag_list = [t.strip() for t in (args.tags or "").split(",") if t.strip()]
        if not tag_list:
            print("error: --tags is required", file=sys.stderr)
            sys.exit(1)
        mode = args.mode or "add"
        result = batch_tag(args.agent, args.category, tag_list, mode=mode)
        print(f"Batch tagged: {result['updated']}/{result['matched']} memories updated")

    elif action == "archive":
        if not args.agent:
            print("error: --agent is required", file=sys.stderr)
            sys.exit(1)
        days = args.days or 30
        result = batch_archive(args.agent, days=days)
        print(f"Archived {result['archived']} memories for agent '{args.agent}' (>{days} days old)")

    elif action == "restore":
        if not args.agent:
            print("error: --agent is required", file=sys.stderr)
            sys.exit(1)
        result = batch_restore(args.agent)
        print(f"Restored {result['restored']} archived memories for agent '{args.agent}'")

    elif action == "dedup":
        agent = args.agent or None
        threshold = args.threshold or 0.9
        result = deduplicate(agent_name=agent, threshold=threshold)
        print(f"Dedup: {result['duplicates_found']} duplicates found, {result['merged']} merged")
        for pair in result.get("pairs", [])[:10]:
            print(f"  #{pair['removed']} -> #{pair['kept']}  (sim={pair['similarity']:.3f})")

    else:
        print("Usage: memall ops {merge|split|tag|batch-tag|archive|restore|dedup}")
        print("  merge --from ID --to ID")
        print("  split --id ID [--delimiter '...']")
        print("  tag --id ID --tags t1,t2 [--mode add|set|remove]")
        print("  batch-tag --agent X --category Y --tags t1,t2 [--mode add]")
        print("  archive --agent X [--days 30]")
        print("  restore --agent X")
        print("  dedup [--agent X] [--threshold 0.9]")


# ──────────────────────────────────────────────
# cmd_dream — Dynamic Dreaming status
# ──────────────────────────────────────────────

def cmd_dream(args):
    """CLI handler for ``memall dream status`` — active contradiction network."""
    action = getattr(args, "dream_action", "status")

    if action == "status":
        conn = get_conn()
        try:
            total = conn.execute(
                "SELECT COUNT(*) as c FROM edges WHERE relation_type = 'contradicts'"
            ).fetchone()["c"]
            resolved = conn.execute(
                "SELECT COUNT(*) as c FROM edges WHERE relation_type = 'contradicts' AND json_extract(metadata, '$.resolved_by') IS NOT NULL"
            ).fetchone()["c"]
            recent = conn.execute(
                "SELECT e.id, e.source_id, e.target_id, e.weight, e.created_at, "
                "m1.content as src_content, m2.content as tgt_content, "
                "m1.agent_name as src_agent, m2.agent_name as tgt_agent, "
                "e.metadata "
                "FROM edges e "
                "JOIN memories m1 ON e.source_id = m1.id "
                "JOIN memories m2 ON e.target_id = m2.id "
                "WHERE e.relation_type = 'contradicts' "
                "ORDER BY e.created_at DESC LIMIT 15"
            ).fetchall()

            print(f"=== Dynamic Dreaming ===")
            print(f"  Total contradiction edges: {total}")
            print(f"  Resolved by timestamp:     {resolved}")
            print(f"  Resolution rate:           {resolved/max(total,1)*100:.1f}%")
            print()
            if recent:
                print(f"  Recent contradictions (last {len(recent)}):")
                for r in recent:
                    meta = {}
                    try:
                        raw = r["metadata"]
                        meta = json.loads(raw) if raw and raw.strip() else {}
                    except Exception:
                        pass
                    verdict = meta.get("verdict", "undecided")
                    badge = {"newer_wins": "✓", "older_wins": "←", "undecided": "?"}.get(verdict, "?")
                    src_snip = (r["src_content"] or "")[:60].replace("\n", " ")
                    tgt_snip = (r["tgt_content"] or "")[:60].replace("\n", " ")
                    created = (r["created_at"] or "")[:19]
                    print(f"    {badge} [#{r['source_id']}] {src_snip}")
                    print(f"      ↔ [#{r['target_id']}] {tgt_snip}")
                    print(f"      at {created}  wt={r['weight']:.2f}  verdict={verdict}")
                    print()
            else:
                print("  No contradiction edges found.")
        finally:
            conn.close()