"""Federation CLI commands: publish, family, federation."""

import sys


# ──────────────────────────────────────────────
# cmd_publish
# ──────────────────────────────────────────────

def cmd_publish(args):
    from memall.federation.family import publish_memory
    result = publish_memory(args.id, scope=args.scope)
    if "error" in result:
        print(f"Publish failed: {result['error']}", file=sys.stderr)
        sys.exit(1)
    print(f"Published memory #{result['memory_id']} to {result['target_db']}")


# ──────────────────────────────────────────────
# cmd_family
# ──────────────────────────────────────────────

def cmd_family(args):
    """GAP-8: Family circle management and cross-member search."""
    action = getattr(args, "action", None)
    circle = getattr(args, "circle", None)

    if action == "init":
        if not circle:
            print("Usage: memall family init --circle <circle_name> [--owner NAME]")
            print("Hint: use '--circle myfamily --owner NAME'")
            sys.exit(1)
        from memall.federation.family import family_init
        result = family_init(circle, owner_name=args.owner)
        if result["status"] == "ok":
            print(f"✓ {result['message']}")
        else:
            print(f"✗ {result['reason']}", file=sys.stderr)
            sys.exit(1)

    elif action == "invite":
        if not circle or not args.member_name:
            print("Usage: memall family invite <member_name> --circle <circle_name> [--role admin|member] [--invited-by NAME]")
            sys.exit(1)
        from memall.federation.family import family_invite
        role = getattr(args, "role", "member")
        invited_by = getattr(args, "invited_by", "") or ""
        result = family_invite(circle, args.member_name, role=role, invited_by=invited_by)
        if result["status"] in ("ok", "already_member"):
            print(f"✓ {result['message']}")
        else:
            print(f"✗ {result['reason']}", file=sys.stderr)
            sys.exit(1)

    elif action == "list":
        from memall.federation.family import family_list
        results = family_list(circle_name=circle or "")
        if not results:
            print("No family circles found. Create one with: memall family init --circle <name>")
            return
        for r in results:
            role_tag = f"[{r['role']}]" if r["role"] == "admin" else ""
            print(f"  {r['circle_name']:20s} {r['member']:16s} {role_tag:8s} {r['joined_at'][:10]}")

    elif action == "search":
        if not args.query:
            print("Usage: memall family search <query> [--trust-level X] [--member NAME]")
            sys.exit(1)
        from memall.federation.family import search_family
        results = search_family(
            args.query,
            limit=args.limit,
            trust_level=args.trust_level or "",
            member_filter=args.member or "",
        )
        if not results:
            print("No family memories found.")
            return
        print(f"Found {len(results)} family memory(ies):")
        for r in results:
            print(f"  [{r['family_id']}] {r['content'][:80]}... ({r['source_agent']}, {r['trust_level']})")

    elif action == "stats":
        from memall.federation.family import get_family_stats
        stats = get_family_stats()
        print(f"Family Library:")
        print(f"  Total shared: {stats['total']} memories across {stats['circles']} circle(s)")
        print(f"  Active members: {stats['members']}")
        for agent, count in stats.get("agents", {}).items():
            print(f"  {agent}: {count}")
        trust_dist = stats.get("trust_distribution", {})
        if trust_dist:
            print(f"  Trust distribution: {trust_dist}")

    else:
        print("Usage: memall family {init|invite|list|search|stats}")
        print("  init --circle NAME [--owner NAME]")
        print("  invite MEMBER --circle NAME")
        print("  list [--circle NAME]")
        print("  search QUERY [--trust-level X] [--member X]")
        print("  stats")
        sys.exit(1)


# ──────────────────────────────────────────────
# cmd_federation
# ──────────────────────────────────────────────

def cmd_federation(args):
    from memall.federation.conflict import detect_conflicts, list_conflicts, resolve_conflict, auto_resolve
    from memall.federation.health import federation_health
    from memall.federation.visualize import generate_report as fed_generate

    if args.action == "visualize":
        result = fed_generate(output_path=args.output or "", format=args.format, detail=args.detail)
        print(f"Report generated: {result['path']} ({result['total']} memories)")
        return

    if args.action == "health":
        result = federation_health(detail=args.detail)
        if args.format == "json":
            import json
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"Federation Health — {result['total']} memories")
            print(f"  Agents:")
            for agent, cnt in sorted(result['agents'].items()):
                print(f"    {agent}: {cnt}")
            print(f"  Conflict Status: {result['conflict_status']}")
            print(f"  Open conflicts: {result['open_conflicts']}")
            print(f"  Resolved: {result['resolved_conflicts']}")
            if result.get('last_snapshot'):
                print(f"  Last snapshot: {result['last_snapshot']}")
            if result.get('trend'):
                print(f"  Trend (last {len(result['trend'])} days):")
                for t in result['trend'][-3:]:
                    print(f"    {t['date']}: {t['total']} mems, {t['open_conflicts']} open")
            if args.detail:
                dups = result.get('duplicates', [])
                if dups:
                    print(f"\n  Near-duplicates ({len(dups)}):")
                    for d in dups[:5]:
                        print(f"    #{d['id']} (sim={d['similarity']}) <-> #{d['most_similar_id']}")
                        print(f"      {d['content']}")
                orphans = result.get('orphans', [])
                if orphans:
                    print(f"\n  Orphans ({len(orphans)}):")
                    for o in orphans[:5]:
                        print(f"    #{o['id']}: {o['content']}")
        return

    if args.action == "conflicts":
        if args.resolve:
            cid, wid = int(args.resolve[0]), int(args.resolve[1])
            result = resolve_conflict(cid, wid)
            if "error" in result:
                print(f"Resolve failed: {result['error']}", file=sys.stderr)
                sys.exit(1)
            print(f"Resolved conflict #{result['conflict_id']}: winner #{result['winner']}, loser #{result['loser']}")
        elif args.auto:
            result = auto_resolve()
            print(f"Auto-resolved {result['auto_resolved']}/{result['total_processed']} conflicts")
        else:
            _ = detect_conflicts(threshold=args.threshold, mode=args.mode)
            conflicts = list_conflicts(status=args.status)
            if not conflicts:
                print(f"No {args.status} conflicts found.")
            for c in conflicts:
                print(f"#{c['id']}: #{c['memory_id_a']} ({c['agent_a']}) vs #{c['memory_id_b']} ({c['agent_b']}) [{c['status']}] src={c.get('conflict_type','?')}")
                print(f"  A: {c['content_a'][:80]}...")
                print(f"  B: {c['content_b'][:80]}...")
                if c['winner_id']:
                    print(f"  Winner: #{c['winner_id']}")
                print()