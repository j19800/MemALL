import logging
import time
from datetime import datetime, timezone
from .enrich import enrich_step
from .classify import classify_step
from .link import link_step
from .decay import decay_step
from .backup import backup_step
from .metrics import collect_metrics, append_metrics
from .narrative import narrative_step
from .cluster import cluster_step
from .suggest import suggest_step
from .bridge import bridge_analysis_step
from .distill import distill_step
from .reflect import reflect_step
from .embed_index import embed_index_step
from .integrate import integrate_step
from .procedure import procedure_step
from .cleanup import cleanup_step
from .observe import observation_step
from .identity import identity_step
from .time_slice import time_slice_step
from .arc_status import arc_status_step
from .echo import echo_step
from .epoch import epoch_step
from .convergence import convergence_step, resolve_pending_deliberations
from .improve import improve_step
from .distill_l7 import distill_l7_step
from memall.core.db import get_conn
from memall.mcp.hooks import HookRegistry, HOOK_STOP

logger = logging.getLogger(__name__)


def check_level_discipline() -> dict:
    """Validate level hierarchy. Warn on violations, return summary."""
    conn = get_conn()
    try:
        violations = []
        counts = {}

        # 1. Level distribution
        rows = conn.execute(
            "SELECT level, COUNT(*) as cnt FROM memories GROUP BY level ORDER BY cnt DESC"
        ).fetchall()
        counts = dict(rows)

        # 2. L10 with no L9 source edge
        l10_no_l9 = conn.execute("""
            SELECT COUNT(*) FROM memories m
            WHERE m.level = 'L10' AND NOT EXISTS (
                SELECT 1 FROM edges e
                JOIN memories t ON e.target_id = t.id
                WHERE e.source_id = m.id AND t.level = 'L9'
            )
        """).fetchone()[0]
        if l10_no_l9:
            msg = f"{l10_no_l9} L10 memories have no L9 source edge"
            violations.append(msg)
            logger.warning(msg)

        # 3. L9 with level=P0/P1/P2 source (disallowed direct distillation)
        l9_from_p0 = conn.execute("""
            SELECT COUNT(*) FROM edges e
            JOIN memories s ON e.source_id = s.id
            JOIN memories t ON e.target_id = t.id
            WHERE s.level = 'L9' AND t.level IN ('P0', 'P1', 'P2') AND e.relation_type = 'refines'
        """).fetchone()[0]
        if l9_from_p0:
            msg = f"{l9_from_p0} L9 memories refine P0/P1/P2 sources (non-terminal, auto-migrated by cleanup)"
            logger.info(msg)

        # 4. Check for unexpected level values
        expected = {"P0", "P1", "P2", "P3", "P4", "L1", "L2", "L3", "L4", "L5",
                    "L6", "L7", "L8", "L9", "L10", "deleted", "info", "heartbeat", "archived"}
        unexpected = set(counts.keys()) - expected
        for lev in unexpected:
            msg = f"Unexpected level value '{lev}' ({counts[lev]} memories)"
            violations.append(msg)

        return {
            "level_counts": counts,
            "violations": violations,
            "violation_count": len(violations),
            "healthy": len(violations) == 0,
        }
    finally:
        conn.close()


def run_pipeline(
    dry_run: bool = False,
    include_persona: bool = True,
    include_cluster: bool = False,
    include_narrative: bool = False,
    include_suggest: bool = False,
    include_identity: bool = True,
    include_bridge: bool = False,
    include_distill: bool = True,
    include_reflect: bool = True,
    include_integrate: bool = True,
    include_improve: bool = True,
    include_procedure: bool = True,
    include_embed_index: bool = False,
    cluster_method: str = "embedding",
) -> dict:
    start = time.time()
    results = {
        "enrich": 0,
        "cleanup": 0,
        "classify": 0,
        "procedure": 0,
        "time_slice": 0,
        "arc_status": 0,
        "echo": 0,
        "epoch": 0,
        "convergence": 0,
        "link": 0,
        "decay": 0,
        "backup": 0,
        "integrate": 0,
        "embed_index": 0,
        "observation": 0,
    }

    if dry_run:
        return {"status": "dry_run", "results": results, "elapsed": 0}

    try:
        results["enrich"] = enrich_step()
        results["cleanup"] = cleanup_step()
        results["classify"] = classify_step()
        results["time_slice"] = time_slice_step()
        results["arc_status"] = arc_status_step()
        results["echo"] = echo_step()
        results["epoch"] = epoch_step()
        results["convergence"] = resolve_pending_deliberations()
        if include_procedure:
            results["procedure"] = procedure_step()
        results["link"] = link_step()
        results["decay"] = decay_step()
        results["backup"] = backup_step()
        results["embed_index"] = embed_index_step()

        # Reflect before distill: ensure refined L6 content is available for distillation
        if include_reflect:
            results["reflect"] = reflect_step()

        # Distill L7 from L6 reflections: extract lessons/improvement points
        results["distill_l7"] = distill_l7_step()

        # Distill after reflect: L9 created from enriched+classified+reflected content
        if include_distill:
            results["distill"] = distill_step()

        # Integrate after distill: L10 created from L9
        if include_integrate:
            results["integrate"] = integrate_step()

        # Improve: extract correction rules from L6 reflections
        if include_improve:
            results["improve"] = improve_step()

        # Observation: structured pipeline report (第二刀 第3点)
        results["observation"] = observation_step()

        if include_identity:
            results["identity"] = identity_step()

        if include_persona:
            from .persona import persona_step
            results["persona"] = persona_step()

        if include_narrative:
            results["narrative"] = narrative_step()

        if include_cluster:
            results["cluster"] = cluster_step(method=cluster_method)

        if include_suggest:
            results["suggest"] = suggest_step()

        if include_bridge:
            results["bridge"] = bridge_analysis_step()

        metrics = collect_metrics()
        metrics["timestamp"] = datetime.now(timezone.utc).isoformat()
        append_metrics(metrics)
        results["metrics"] = metrics

        # Level discipline validation
        discipline = check_level_discipline()
        results["discipline"] = discipline
        if discipline["violations"]:
            logger.warning("Level discipline violations:\n  " + "\n  ".join(discipline["violations"]))
    except Exception as e:
        elapsed = time.time() - start
        return {"status": "error", "error": str(e), "elapsed": elapsed}

    elapsed = time.time() - start
    HookRegistry.dispatch(HOOK_STOP, arguments={"results": results, "elapsed": elapsed})
    return {"status": "ok", "results": results, "elapsed": elapsed}
