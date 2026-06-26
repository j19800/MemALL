import argparse
import sys
import logging

from memall import __version__
from memall.core.log_setup import configure as configure_logging

# Import command handlers from split modules
from memall.cli.commands.base import (
    cmd_init, cmd_capture, cmd_search, cmd_get,
    cmd_connect, cmd_traverse, cmd_timeline, cmd_update,
    cmd_knowledge, cmd_insights,
)
from memall.cli.commands.pipeline_commands import (
    cmd_pipeline, cmd_pipeline_status, cmd_forget, cmd_persona, cmd_cluster,
    cmd_cluster_show, cmd_narrative, cmd_suggest, cmd_bridge,
    cmd_ask, cmd_adaptive, cmd_security, cmd_ops, cmd_dream,
)
from memall.cli.commands.federation_commands import (
    cmd_publish, cmd_family, cmd_federation,
)
from memall.cli.commands.gateway_commands import (
    cmd_gateway,
)
from memall.cli.commands.management_commands import (
    cmd_start, cmd_stop, cmd_status, cmd_doctor, cmd_migrate,
    cmd_trust, cmd_identity_trust, cmd_identity_untrust,
    cmd_identity,
    cmd_graph_visualize, cmd_index, cmd_retrieve, cmd_onboarding,
    cmd_serve, cmd_db,
    cmd_setup, cmd_register, cmd_uninstall, cmd_backup,
    cmd_restore, cmd_export, cmd_import, cmd_sync, cmd_mcp_connect,
    cmd_arcs,
)

configure_logging()
logger = logging.getLogger("memall")


def app():
    parser = argparse.ArgumentParser(prog="memall", description="Multi-agent collaborative memory OS")
    parser.add_argument("--version", action="version", version=__version__)

    sub = parser.add_subparsers(title="commands", dest="command")

    p_init = sub.add_parser("init", help="Initialize database")
    p_init.set_defaults(func=cmd_init)

    p_cap = sub.add_parser("capture", help="Store a memory")
    p_cap.add_argument("content", nargs="?", help="Memory content (or stdin)")
    p_cap.add_argument("--owner", help="Owner name")
    p_cap.add_argument("--agent", help="Agent name")
    p_cap.add_argument("--subject", help="Subject")
    p_cap.add_argument("--project", help="Project")
    p_cap.add_argument("--category", help="Category")
    p_cap.add_argument("--level", choices=["P0", "P1", "P2", "L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8", "L9", "L10", "L11"], default="P2")
    p_cap.set_defaults(func=cmd_capture)

    p_search = sub.add_parser("search", help="Search memories")
    p_search.add_argument("query", nargs="?", default=None, help="Search query")
    p_search.add_argument("--owner")
    p_search.add_argument("--agent")
    p_search.add_argument("--category")
    p_search.add_argument("--project")
    p_search.add_argument("--level", help="Filter by level (e.g. L9, L10, L6)")
    p_search.add_argument("--limit", type=int, default=20)
    p_search.set_defaults(func=cmd_search)

    p_know = sub.add_parser("knowledge", help="Search L9 distilled knowledge")
    p_know.add_argument("query", nargs="?", default=None, help="Search distilled knowledge")
    p_know.add_argument("--limit", type=int, default=20)
    p_know.set_defaults(func=cmd_knowledge)

    p_ins = sub.add_parser("insights", help="Search L10 cross-domain insights")
    p_ins.add_argument("query", nargs="?", default=None, help="Search cross-domain insights")
    p_ins.add_argument("--limit", type=int, default=10)
    p_ins.set_defaults(func=cmd_insights)

    p_get = sub.add_parser("get", help="Get memory by ID")
    p_get.add_argument("id", type=int)
    p_get.set_defaults(func=cmd_get)

    p_con = sub.add_parser("connect", help="Create an edge between memories")
    p_con.add_argument("source", type=int)
    p_con.add_argument("target", type=int)
    p_con.add_argument("--relation", default="refines")
    p_con.add_argument("--weight", type=float, default=1.0)
    p_con.set_defaults(func=cmd_connect)

    p_trav = sub.add_parser("traverse", help="Traverse from a memory node")
    p_trav.add_argument("id", type=int)
    p_trav.add_argument("--depth", type=int, default=1)
    p_trav.add_argument("--relation")
    p_trav.set_defaults(func=cmd_traverse)

    p_time = sub.add_parser("timeline", help="Show memory timeline")
    p_time.add_argument("--query")
    p_time.add_argument("--hours", type=int, default=24)
    p_time.add_argument("--category")
    p_time.add_argument("--project")
    p_time.add_argument("--limit", type=int, default=50)
    p_time.add_argument("--start", help="Start date (ISO 8601)")
    p_time.add_argument("--end", help="End date (ISO 8601)")
    p_time.add_argument("--days", type=int, help="Number of days back")
    p_time.set_defaults(func=cmd_timeline)

    p_pl = sub.add_parser("pipeline", help="Run enrichment pipeline or view status")
    p_pl.add_argument("--dry-run", action="store_true")
    p_pl.set_defaults(func=cmd_pipeline)
    p_pl_sub = p_pl.add_subparsers(dest="pipeline_action")
    p_pl_status = p_pl_sub.add_parser("status", help="Show pipeline run history and quality gates")
    p_pl_status.set_defaults(func=cmd_pipeline_status)

    p_dream = sub.add_parser("dream", help="Dynamic Dreaming — active contradiction detection")
    p_dream_sub = p_dream.add_subparsers(dest="dream_action")
    p_dream_status = p_dream_sub.add_parser("status", help="Show active contradiction network")
    p_dream_status.set_defaults(func=cmd_dream)

    p_start = sub.add_parser("start", help="Start scheduler")
    p_start.set_defaults(func=cmd_start)

    p_stop = sub.add_parser("stop", help="Stop scheduler")
    p_stop.set_defaults(func=cmd_stop)

    p_stat = sub.add_parser("status", help="System status")
    p_stat.set_defaults(func=cmd_status)

    p_doc = sub.add_parser("doctor", help="Diagnose and fix database")
    p_doc.add_argument("--fix", action="store_true", help="Apply fixes")
    p_doc.add_argument("--deep", action="store_true", help="Deep health check with recommendations")
    p_doc.add_argument("--metrics", action="store_true", help="Show system metrics")
    p_doc.set_defaults(func=cmd_doctor)

    p_whois = sub.add_parser("whois", help="Show L1/L7 identity and preference profile for an agent")
    p_whois.add_argument("agent_name", help="Agent name to query")
    p_whois.set_defaults(func=cmd_identity)

    p_upd = sub.add_parser("update", help="Update a memory field")
    p_upd.add_argument("id", type=int, help="Memory ID")
    p_upd.add_argument("--field", required=True, help="Field name (level/category/project/summary/subject/confidence/visibility)")
    p_upd.add_argument("--value", required=True, help="New value")
    p_upd.set_defaults(func=cmd_update)

    p_mig = sub.add_parser("migrate", help="Migrate from legacy database or apply schema migrations")
    p_mig.add_argument("--source", help="Path to legacy db (data migration)")
    p_mig.add_argument("--apply", action="store_true", help="Apply pending schema migrations (GAP-7)")
    p_mig.add_argument("--status", action="store_true", help="Show migration status")
    p_mig.set_defaults(func=cmd_migrate)

    p_forget = sub.add_parser("forget", help="Automatic forgetting — TTL expiration + low-value decay (Phase 11)")
    p_forget.add_argument("--expired", action="store_true", help="Delete memories older than --days")
    p_forget.add_argument("--low-value", action="store_true", help="Delete isolated short memories (>7 days)")
    p_forget.add_argument("--review", action="store_true", help="Preview what would be deleted (no-op)")
    p_forget.add_argument("--stats", action="store_true", help="Show forgetting-related database statistics")
    p_forget.add_argument("--all", action="store_true", help="Run complete forget step (expired + low-value)")
    p_forget.add_argument("--days", type=int, default=90, help="TTL days for expired check (default: 90)")
    p_forget.add_argument("--agent", help="Filter by agent name")
    p_forget.set_defaults(func=cmd_forget)

    p_trust = sub.add_parser("trust", help="Change a memory's trust level")
    p_trust.add_argument("id", type=int, help="Memory ID")
    p_trust.add_argument("--level", required=True, choices=["private", "trusted", "family", "shared", "public"], help="New trust level")
    p_trust.set_defaults(func=cmd_trust)

    p_id_trust = sub.add_parser("identity", help="Manage identities")
    p_id_sub = p_id_trust.add_subparsers(title="identity commands", dest="identity_command")
    p_id_trust_cmd = p_id_sub.add_parser("trust", help="Add a user to your trusted circle")
    p_id_trust_cmd.add_argument("name", help="Identity name to trust")
    p_id_trust_cmd.set_defaults(func=cmd_identity_trust)
    p_id_untrust_cmd = p_id_sub.add_parser("untrust", help="Remove a user from your trusted circle")
    p_id_untrust_cmd.add_argument("name", help="Identity name to untrust")
    p_id_untrust_cmd.set_defaults(func=cmd_identity_untrust)

    p_per = sub.add_parser("persona", help="Generate or view agent persona")
    p_per.add_argument("agent", nargs="?", help="Agent name")
    p_per.add_argument("--compare", nargs="+", help="Compare multiple agents")
    p_per.add_argument("--evolution", action="store_true", help="Show persona evolution over time (GAP-9)")
    p_per.add_argument("--window", type=int, default=30, help="Window size in days for evolution (default: 30)")
    p_per.add_argument("--profile", action="store_true", help="Generate 3-layer profile (Phase 10)")
    p_per.add_argument("--layer", choices=["1", "2", "3", "all"], default="all", help="Profile layer to show")
    p_per.add_argument("--mode", choices=["static", "dynamic", "dual"], default="static",
                       help="Static (full history), dynamic (recent 7d), or dual (both + delta)")
    p_per.add_argument("--dynamic-days", type=int, default=7, help="Window in days for dynamic persona (default: 7)")
    p_per.set_defaults(func=cmd_persona)

    p_cl = sub.add_parser("cluster", help="Manage topic clusters")
    p_cl.add_argument("--show", action="store_true", help="Show all clusters after clustering")
    p_cl.add_argument("--method", default="tfidf", choices=["tfidf", "embedding"], help="Clustering method: tfidf (raw memory content) or embedding (narrative embeddings via all-MiniLM-L6-v2)")
    p_cl.set_defaults(func=cmd_cluster)

    p_cls = sub.add_parser("cluster-show", help="Show cluster details")
    p_cls.add_argument("id", type=int, help="Cluster ID")
    p_cls.set_defaults(func=cmd_cluster_show)

    p_nar = sub.add_parser("narrative", help="Generate agent narrative")
    p_nar.add_argument("agent", help="Agent name")
    p_nar.add_argument("--span", type=int, default=7, help="Days to span (default 7)")
    p_nar.add_argument("--type", default="weekly", choices=["weekly", "monthly", "phase"], help="Narrative type")
    p_nar.set_defaults(func=cmd_narrative)

    p_sug = sub.add_parser("suggest", help="Manage suggestions")
    p_sug.add_argument("--list", action="store_true", help="List suggestions")
    p_sug.add_argument("--status", help="Filter by status (pending/accepted/rejected/in_progress/implemented/wontfix)")
    p_sug.add_argument("--category", help="Filter by category")
    p_sug.add_argument("--accept", type=int, help="Accept suggestion by ID")
    p_sug.add_argument("--reject", type=int, help="Reject suggestion by ID")
    p_sug.add_argument("--reason", help="Reason for rejection")
    p_sug.add_argument("--start", type=int, help="Start working on suggestion by ID")
    p_sug.add_argument("--done", type=int, help="Mark suggestion as implemented by ID")
    p_sug.add_argument("--wontfix", type=int, help="Mark suggestion as wontfix by ID")
    p_sug.add_argument("--stats", action="store_true", help="Show suggestion statistics")
    p_sug.add_argument("--import", dest="import_file", help="Import suggestions from markdown file")
    p_sug.set_defaults(func=cmd_suggest)

    p_bri = sub.add_parser("bridge", help="Bridge edges / weak ties analysis (Phase 3.5)")
    p_bri.add_argument("--show", action="store_true", help="Show detailed bridge analysis")
    p_bri.add_argument("--agent", help="Filter by agent name")
    p_bri.set_defaults(func=cmd_bridge)

    p_ask = sub.add_parser("ask", help="Digital twin query (Phase 3.6)")
    p_ask.add_argument("query", help="Query text")
    p_ask.add_argument("--mode", default="stance", choices=["stance", "pattern", "predict"], help="Query mode")
    p_ask.add_argument("--subject", default="", help="Agent name to query about")
    p_ask.add_argument("--scope", default="local", choices=["local", "family", "all"], help="Search scope (Phase 4)")
    p_ask.set_defaults(func=cmd_ask)

    p_pub = sub.add_parser("publish", help="Publish memory to family library (Phase 4)")
    p_pub.add_argument("id", type=int, help="Memory ID to publish")
    p_pub.add_argument("--scope", default="family", choices=["family"], help="Publication scope")
    p_pub.set_defaults(func=cmd_publish)

    p_fed = sub.add_parser("federation", help="Federation management (Phase 4.1)")
    p_fed.add_argument("action", choices=["conflicts", "health", "visualize"], help="Action")
    p_fed.add_argument("--resolve", nargs=2, metavar=("CONFLICT_ID", "WINNER_ID"), help="Resolve conflict")
    p_fed.add_argument("--auto", action="store_true", help="Auto-resolve all open conflicts")
    p_fed.add_argument("--status", default="open", help="Filter by status (open/resolved)")
    p_fed.add_argument("--format", default="table", choices=["table", "json", "html", "png"], help="Output format")
    p_fed.add_argument("--detail", action="store_true", help="Show duplicates and orphans")
    p_fed.add_argument("--output", help="Output path for visualize report")
    p_fed.add_argument("--mode", default="all", choices=["all", "keyword", "semantic"], help="Conflict detection mode")
    p_fed.add_argument("--threshold", type=float, default=0.85, help="Semantic similarity threshold (default 0.85)")
    p_fed.set_defaults(func=cmd_federation)

    # ── family (GAP-8: multi-user / family circle management) ──
    p_fam = sub.add_parser("family", help="Family circle management and cross-member search")
    p_fam_sub = p_fam.add_subparsers(dest="action", help="Family action")
    p_fam_init = p_fam_sub.add_parser("init", help="Create a new family circle")
    p_fam_init.add_argument("--circle", required=True, help="Circle name")
    p_fam_init.add_argument("--owner", default="admin", help="Circle owner name")
    p_fam_invite = p_fam_sub.add_parser("invite", help="Invite a member to family circle")
    p_fam_invite.add_argument("member_name", help="Member name to invite")
    p_fam_invite.add_argument("--circle", required=True, help="Circle name")
    p_fam_invite.add_argument("--role", default="member", choices=["admin", "member"], help="Member role")
    p_fam_invite.add_argument("--invited-by", default="", help="Name of the inviter")
    p_fam_list = p_fam_sub.add_parser("list", help="List family circle members")
    p_fam_list.add_argument("--circle", help="Circle name (omit to list all)")
    p_fam_search = p_fam_sub.add_parser("search", help="Search across family shared memories")
    p_fam_search.add_argument("query", help="Search query")
    p_fam_search.add_argument("--trust-level", choices=["trusted", "family", "shared", "public"],
                              help="Filter by trust level")
    p_fam_search.add_argument("--member", help="Filter by member name")
    p_fam_search.add_argument("--limit", type=int, default=20, help="Max results (default: 20)")
    p_fam_stats = p_fam_sub.add_parser("stats", help="Show family library statistics")
    p_fam.set_defaults(func=cmd_family)

    p_gr = sub.add_parser("graph", help="Memory graph visualization (Phase 5)")
    p_gr.set_defaults(func=cmd_graph_visualize)
    p_gr_v = sub.add_parser("graph-visualize", help="Visualize memory graph")
    p_gr_v.add_argument("--format", default="html", choices=["html", "png"], help="Output format")
    p_gr_v.add_argument("--limit", type=int, help="Limit nodes (default: all)")
    p_gr_v.add_argument("--center", type=int, help="Center memory ID (2-hop subgraph)")
    p_gr_v.add_argument("--layout", default="spring", choices=["spring", "circular", "kamada"], help="Layout algorithm")
    p_gr_v.add_argument("--output", help="Output path")
    p_gr_v.set_defaults(func=cmd_graph_visualize)

    p_idx = sub.add_parser("index", help="Manage vector index (Phase 6)")
    p_idx.add_argument("action", choices=["build", "status"], help="Index action")
    p_idx.add_argument("--batch", type=int, default=64, help="Batch size (default 64)")
    p_idx.set_defaults(func=cmd_index)

    p_ret = sub.add_parser("retrieve", help="Semantic search (Phase 6)")
    p_ret.add_argument("query", help="Search query")
    p_ret.add_argument("--mode", default="hybrid", choices=["hybrid", "vector", "keyword"], help="Retrieval mode")
    p_ret.add_argument("--top", type=int, default=10, help="Top K results")
    p_ret.set_defaults(func=cmd_retrieve)

    p_ob = sub.add_parser("onboarding", help="New user onboarding (Phase 8)")
    p_ob.add_argument("action", choices=["start", "status", "reset", "complete"], help="Onboarding action")
    p_ob.add_argument("--step", type=int, help="Start from specific step")
    p_ob.set_defaults(func=cmd_onboarding)

    p_serve = sub.add_parser("serve", help="Start MCP STDIO Server or HTTP API")
    p_serve.add_argument("--http", action="store_true", help="Start HTTP API instead of MCP")
    p_serve.add_argument("--port", type=int, default=8199, help="HTTP port (default 8199)")
    p_serve.set_defaults(func=cmd_serve)

    p_setup = sub.add_parser("setup", help="Configure AI agents with MemALL MCP Server")
    p_setup.add_argument("--all", action="store_true", help="Configure all detected agents")
    p_setup.add_argument("--agent", help="Target agent: claude / cursor / opencode / solo")
    p_setup.add_argument("--fix", action="store_true", help="Repair existing config")
    p_setup.add_argument("--config", help="Custom config file path")
    p_setup.add_argument("--user", default="admin", help="User name for MCP config")
    p_setup.add_argument("--db", help="Database path for MCP config (default: ~/.memall/data.db)")
    p_setup.set_defaults(func=cmd_setup)

    p_backup = sub.add_parser("backup", help="Backup MemALL database")
    p_backup.add_argument("action", nargs="?", choices=["clean"], help="Subcommand (clean: delete old backups)")
    p_backup.add_argument("--list", action="store_true", help="List available backups")
    p_backup.add_argument("--keep-daily", type=int, default=7, help="Keep N recent daily backups (for clean)")
    p_backup.add_argument("--keep-weekly", type=int, default=4, help="Keep N recent weekly backups (for clean)")
    p_backup.set_defaults(func=cmd_backup)

    p_restore = sub.add_parser("restore", help="Restore database from backup")
    p_restore.add_argument("--from", dest="from_", help="Backup path (daily/YYYY-MM-DD.db or weekly/YYYY-MM-DD.db)")
    p_restore.add_argument("--auto", action="store_true", help="Restore from latest available backup")
    p_restore.set_defaults(func=cmd_restore)

    p_export = sub.add_parser("export", help="Export memories (json/jsonl/markdown/yaml)")
    p_export.add_argument("--format", default="json", choices=["json", "jsonl", "markdown", "yaml"],
                          help="Export format (default: json)")
    p_export.add_argument("--category", help="Filter by category (e.g. decision, code, meeting)")
    p_export.add_argument("--since", help="Export only memories updated after this ISO timestamp (e.g. 2026-06-01 or 2026-06-01T12:00:00)")
    p_export.add_argument("--output", help="Output file path (default: ~/.memall/exports/memall-export-DATE.ext)")
    p_export.set_defaults(func=cmd_export)

    p_import = sub.add_parser("import", help="Import memories from a JSON or JSONL file")
    p_import.add_argument("file", help="Path to export file (.json or .jsonl)")
    p_import.set_defaults(func=cmd_import)

    p_sync = sub.add_parser("sync", help="Incremental sync from an exported JSONL file")
    p_sync.add_argument("--from", dest="source", required=True,
                        help="Source JSONL file path (produced by memall export --format jsonl)")
    p_sync.add_argument("--since", help="Override last-sync timestamp (ISO format)")
    p_sync.set_defaults(func=cmd_sync)

    p_uninstall = sub.add_parser("uninstall", help="Remove MemALL MCP config from agents")
    p_uninstall.add_argument("--all", action="store_true", help="Remove from all agents")
    p_uninstall.add_argument("--agent", choices=["claude", "cursor", "opencode", "solo"],
                             help="Target a single agent")
    p_uninstall.add_argument("--purge", action="store_true",
                             help="Also delete ~/.memall/ data directory")
    p_uninstall.set_defaults(func=cmd_uninstall)

    # ── register (GAP-6) ──
    p_reg = sub.add_parser("register", help="Register a custom agent not detected by setup --all")
    p_reg.add_argument("--agent", help="Agent name to register")
    p_reg.add_argument("--type", choices=["mcp", "http", "stdio"], help="Agent connection type")
    p_reg.add_argument("--url", help="HTTP endpoint URL (for --type http)")
    p_reg.add_argument("--boot-cmd", "--command", dest="boot_cmd",
                       help="Boot command (for --type stdio / mcp)")
    p_reg.add_argument("--description", help="Optional description for the agent")
    p_reg.add_argument("--list", action="store_true", help="List all registered agents")
    p_reg.add_argument("--remove", help="Remove a registered agent by name")
    p_reg.set_defaults(func=cmd_register)

    # ── adaptive (Phase 12: AI Adaptive Subsystem) ──
    p_adapt = sub.add_parser("adaptive", help="AI adaptive subsystem — clean/index/distill/report")
    p_adapt.add_argument("--clean", action="store_true", help="Run adaptive cleaning")
    p_adapt.add_argument("--index", action="store_true", help="Run adaptive indexing")
    p_adapt.add_argument("--distill", action="store_true", help="Run adaptive distillation")
    p_adapt.add_argument("--all", action="store_true", help="Run all adaptive modules")
    p_adapt.add_argument("--report", action="store_true", help="Generate adaptive status report")
    p_adapt.add_argument("--agent", help="Filter by agent name")
    p_adapt.set_defaults(func=cmd_adaptive)

    # ── security (Phase 13: Security Governance) ──
    p_sec = sub.add_parser("security", help="Security governance — audit, permissions, scoring")
    p_sec_sub = p_sec.add_subparsers(dest="action", help="Security action")
    p_sec_audit = p_sec_sub.add_parser("audit", help="Scan memories for sensitive data")
    p_sec_audit.add_argument("--agent", help="Filter by agent name")
    p_sec_permit = p_sec_sub.add_parser("permit", help="Set agent permission level")
    p_sec_permit.add_argument("--agent", dest="agent_name", required=True, help="Agent name")
    p_sec_permit.add_argument("--level", required=True, choices=["public", "trusted", "private"], help="Permission level")
    p_sec_check = p_sec_sub.add_parser("check", help="Check access between two agents")
    p_sec_check.add_argument("--from", dest="requester", required=True, help="Requester agent name")
    p_sec_check.add_argument("--to", dest="target", required=True, help="Target agent name")
    p_sec_score = p_sec_sub.add_parser("score", help="Compute overall security score")
    p_sec_list = p_sec_sub.add_parser("list", help="List agents by permission level")
    p_sec_list.add_argument("--level", default="private", choices=["public", "trusted", "private"], help="Permission level filter")
    p_sec.set_defaults(func=cmd_security)

    # ── ops (Phase 14: Memory Operations) ──
    p_ops = sub.add_parser("ops", help="Memory operations — merge, split, tag, dedup")
    p_ops_sub = p_ops.add_subparsers(dest="action", help="Ops action")
    p_ops_merge = p_ops_sub.add_parser("merge", help="Merge two memories")
    p_ops_merge.add_argument("--from", dest="source_id", type=int, required=True, help="Source memory ID")
    p_ops_merge.add_argument("--to", dest="target_id", type=int, required=True, help="Target memory ID")
    p_ops_split = p_ops_sub.add_parser("split", help="Split a memory by delimiter")
    p_ops_split.add_argument("--id", dest="split_id", type=int, required=True, help="Memory ID to split")
    p_ops_split.add_argument("--delimiter", default="\\n\\n", help="Split delimiter (default: \\\\n\\\\n)")
    p_ops_tag = p_ops_sub.add_parser("tag", help="Tag a single memory")
    p_ops_tag.add_argument("--id", dest="tag_id", type=int, required=True, help="Memory ID")
    p_ops_tag.add_argument("--tags", required=True, help="Comma-separated tags")
    p_ops_tag.add_argument("--mode", default="add", choices=["add", "set", "remove"], help="Tag mode")
    p_ops_btag = p_ops_sub.add_parser("batch-tag", help="Batch tag memories")
    p_ops_btag.add_argument("--agent", required=True, help="Agent name")
    p_ops_btag.add_argument("--category", required=True, help="Category filter")
    p_ops_btag.add_argument("--tags", required=True, help="Comma-separated tags")
    p_ops_btag.add_argument("--mode", default="add", choices=["add", "set", "remove"], help="Tag mode")
    p_ops_arch = p_ops_sub.add_parser("archive", help="Archive old memories")
    p_ops_arch.add_argument("--agent", required=True, help="Agent name")
    p_ops_arch.add_argument("--days", type=int, default=30, help="Age threshold in days")
    p_ops_rest = p_ops_sub.add_parser("restore", help="Restore archived memories")
    p_ops_rest.add_argument("--agent", required=True, help="Agent name")
    p_ops_dedup = p_ops_sub.add_parser("dedup", help="Deduplicate similar memories")
    p_ops_dedup.add_argument("--agent", help="Agent name filter")
    p_ops_dedup.add_argument("--threshold", type=float, default=0.9, help="Similarity threshold")
    p_ops.set_defaults(func=cmd_ops)

    # ── gateway (Phase 15: Device Interconnection) ──
    p_gw = sub.add_parser("gateway", help="Gateway — device interconnection, sync, discovery, federated query")
    p_gw_sub = p_gw.add_subparsers(dest="action", help="Gateway action")
    p_gw_start = p_gw_sub.add_parser("start", help="Start local HTTP gateway")
    p_gw_start.add_argument("--port", type=int, default=9919, help="Gateway port (default 9919)")
    p_gw_stop = p_gw_sub.add_parser("stop", help="Stop local HTTP gateway")
    p_gw_export = p_gw_sub.add_parser("export", help="Export agent data bundle")
    p_gw_export.add_argument("--agent", required=True, help="Agent name")
    p_gw_export.add_argument("--format", default="json", help="Output format (default json)")
    p_gw_import = p_gw_sub.add_parser("import", help="Import a data bundle")
    p_gw_import.add_argument("--file", required=True, help="Bundle file path")
    p_gw_disc = p_gw_sub.add_parser("discover", help="Discover MemALL peers on LAN")
    p_gw_disc.add_argument("--port", type=int, default=9920, help="Discovery port (default 9920)")
    p_gw_disc.add_argument("--timeout", type=int, default=5, help="Scan timeout (default 5s)")
    p_gw_pair = p_gw_sub.add_parser("pair", help="Pair with a remote peer")
    p_gw_pair.add_argument("--address", required=True, help="Peer IP:PORT")
    p_gw_peers = p_gw_sub.add_parser("peers", help="List paired peers")
    p_gw_fed = p_gw_sub.add_parser("federated", help="Federated query across peers")
    p_gw_fed.add_argument("--query", required=True, help="Search query")
    p_gw_fed.add_argument("--max-peers", type=int, default=3, help="Max peers to query (default 3)")
    p_gw.set_defaults(func=cmd_gateway)

    # Database maintenance (Phase 21)
    p_db = sub.add_parser("db", help="Database maintenance — optimize, stats, vacuum")
    p_db_sub = p_db.add_subparsers(dest="action", help="DB action")
    p_db_opt = p_db_sub.add_parser("optimize", help="ANALYZE + VACUUM + PRAGMA optimize")
    p_db_opt.set_defaults(func=cmd_db)
    p_db_stats = p_db_sub.add_parser("stats", help="Show database statistics")
    p_db_stats.set_defaults(func=cmd_db)
    p_db_vac = p_db_sub.add_parser("vacuum", help="Reclaim disk space")
    p_db_vac.set_defaults(func=cmd_db)

    # mcp connect — 一键注册 MCP 服务器
    p_mcp = sub.add_parser("mcp", help="MCP server management")
    p_mcp_sub = p_mcp.add_subparsers(dest="mcp_action")
    p_mcp_con = p_mcp_sub.add_parser("connect", help="Auto-detect client and register memall MCP server")
    p_mcp_con.set_defaults(func=cmd_mcp_connect)

    # ── arcs (Decision Arcs) ──
    p_arcs = sub.add_parser("arcs", help="Query and manage decision arcs")
    p_arcs_sub = p_arcs.add_subparsers(dest="action")
    p_arcs_list = p_arcs_sub.add_parser("list", help="List decision arcs")
    p_arcs_list.add_argument("--status", choices=["open", "in_progress", "closed"], help="Filter by status")
    p_arcs_list.add_argument("--agent", help="Filter by agent name")
    p_arcs_list.add_argument("--limit", type=int, default=20, help="Max results")
    p_arcs_list.set_defaults(func=cmd_arcs)
    p_arcs_close = p_arcs_sub.add_parser("close", help="Manually close a decision arc")
    p_arcs_close.add_argument("id", type=int, help="Decision memory ID")
    p_arcs_close.set_defaults(func=cmd_arcs)
    p_arcs_stale = p_arcs_sub.add_parser("stale", help="List stale decisions (>21d no activity)")
    p_arcs_stale.set_defaults(func=cmd_arcs)

    # Compatibility aliases: 57 legacy actions -> redirect messages
    alias_map = {"add": "capture", "store": "capture", "link": "connect", "history": "timeline",
                 "show": "get", "delete": None}
    for alias_name, target in alias_map.items():
        p_alias = sub.add_parser(alias_name, help=f"[legacy alias] use: memall {target}" if target else "[legacy] not migrated")
        p_alias.set_defaults(func=lambda a, t=target: print(f"Use: memall {t}" if t else f"'{a.command}' not migrated, see memall update"))

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(1)
    args.func(args)


if __name__ == "__main__":
    app()