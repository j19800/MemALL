import json
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


# ── Quality gate configuration ─────────────────────────────────────────

QUALITY_GATES = {
    "distill": {"min_input": 10, "min_output": 1},
    "integrate": {"min_input": 2, "min_output": 1},
    "reflect": {"min_input": 3, "min_output": 1},
    "reflect_aggregate": {"min_input": 3},
    "classify": {"min_input": 5, "coverage_gain": 0.01},
    "enrich": {"min_input": 1},
    "link": {"min_input": 3},
}


# ── Step runner ────────────────────────────────────────────────────────


def _coerce_int(val) -> int:
    """Normalise a step result to int (some steps return dict)."""
    if isinstance(val, int):
        return val
    if isinstance(val, dict):
        # Try common keys
        for k in ("processed", "count", "created", "total", "new"):
            v = val.get(k, None)
            if isinstance(v, int):
                return v
        return 0
    return 0


def _count_memories() -> int:
    """Total memories in DB (cheap COUNT query)."""
    try:
        conn = get_conn()
        row = conn.execute("SELECT COUNT(*) FROM memories").fetchone()
        return row[0] if row else 0
    except Exception:
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _check_quality_gate(step_name: str, entry: dict, gate: dict) -> dict:
    """Run quality checks against the step output.

    Returns ``{"passed": bool, "checks": {...}, "reason": "..."}``.
    Individual check results are always a boolean; ``reason`` is set only on
    failure and describes the first failing check.
    """
    checks: dict[str, bool] = {}
    records_in = entry.get("records_in", 0)
    records_out = entry.get("records_out", 0)
    result = entry.get("result", 0)

    if "min_input" in gate:
        checks["min_input"] = records_in >= gate["min_input"]
    if "min_output" in gate:
        checks["min_output"] = result >= gate["min_output"]

    passed = all(checks.values()) if checks else True
    reason = ""
    if not passed:
        failures = [k for k, v in checks.items() if not v]
        details = []
        for f in failures:
            threshold = gate.get(f.replace("min_", "").replace("_", ""), "?")
            if f == "min_input":
                details.append(f"输入 {records_in} < {gate.get('min_input', '?')}")
            elif f == "min_output":
                details.append(f"产出 {result} < {gate.get('min_output', '?')}")
            else:
                details.append(f"{f} 不达标")
        reason = "; ".join(details)

    return {"passed": passed, "checks": checks, "reason": reason}


def _run_step(step_name: str, step_fn, step_results: dict,
              quality_gate: dict | None = None) -> dict:
    """Run one pipeline step with timing, error isolation, and quality check.

    Args:
        step_name: Human-readable name for logs and DB.
        step_fn: Zero-argument callable (the pipeline step function).
        step_results: Shared results dict; mutated with the coerced result.
        quality_gate: Optional quality gate dict (see QUALITY_GATES).

    Returns:
        A step entry dict suitable for storing in the pipeline_runs.steps JSON.
    """
    start = time.time()
    records_before = _count_memories()
    try:
        result = step_fn()
        elapsed_ms = int((time.time() - start) * 1000)
        records_after = _count_memories()

        entry: dict = {
            "step": step_name,
            "status": "ok",
            "elapsed_ms": elapsed_ms,
            "records_in": records_before,
            "records_out": records_after,
            "result": _coerce_int(result),
            "error": None,
        }

        if quality_gate:
            entry["quality"] = _check_quality_gate(step_name, entry, quality_gate)

        step_results[step_name] = _coerce_int(result)
        logger.info("Pipeline step %s: %dms, result=%s", step_name, elapsed_ms, entry["result"])
        return entry
    except Exception as e:
        elapsed_ms = int((time.time() - start) * 1000)
        logger.error("Pipeline step '%s' failed after %dms: %s", step_name, elapsed_ms, e)
        step_results[step_name] = 0
        return {
            "step": step_name,
            "status": "failed",
            "elapsed_ms": elapsed_ms,
            "records_in": records_before,
            "error": str(e)[:300],
        }


# ── Pipeline run persistence ───────────────────────────────────────────


def _create_pipeline_run() -> int:
    """Insert a new pipeline_runs row and return its id."""
    conn = get_conn()
    try:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO pipeline_runs (started_at, status) VALUES (?, 'running')",
            (now,),
        )
        conn.commit()
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    finally:
        conn.close()


def _update_pipeline_run(run_id: int, entries: list[dict]) -> None:
    """Persist intermediate step results (called after each step)."""
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE pipeline_runs SET steps = ? WHERE id = ?",
            (json.dumps(entries, ensure_ascii=False), run_id),
        )
        conn.commit()
    finally:
        conn.close()


def _finalize_pipeline_run(run_id: int, entries: list[dict],
                            results: dict, error: str | None = None) -> None:
    """Mark a pipeline run as completed or failed, with final stats."""
    elapsed_ms = int((time.time() - _pipeline_start_time) * 1000) if _pipeline_start_time else 0
    status = "failed" if error else "completed"
    conn = get_conn()
    try:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE pipeline_runs SET ended_at = ?, status = ?, total_elapsed_ms = ?, "
            "error = ?, steps = ? WHERE id = ?",
            (now, status, elapsed_ms, (error or ""), json.dumps(entries, ensure_ascii=False), run_id),
        )
        conn.commit()
    finally:
        conn.close()


# Module-level holder for pipeline start time (used in _finalize_pipeline_run)
_pipeline_start_time: float = 0.0


# ── Level discipline ───────────────────────────────────────────────────


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


# ── Pipeline steps definitions ─────────────────────────────────────────

# Each step is defined as a list: (name, function, quality_gate_or_None)
_PIPELINE_STEPS = [
    ("enrich",          enrich_step,           QUALITY_GATES.get("enrich")),
    ("cleanup",         cleanup_step,          None),
    ("classify",        classify_step,         QUALITY_GATES.get("classify")),
    ("time_slice",      time_slice_step,       None),
    ("arc_status",      arc_status_step,       None),
    ("echo",            echo_step,             None),
    ("epoch",           epoch_step,            None),
    ("convergence",     resolve_pending_deliberations, None),
    ("link",            link_step,             QUALITY_GATES.get("link")),
    ("decay",           decay_step,            None),
    ("backup",          backup_step,           None),
    ("embed_index",     embed_index_step,      None),
    ("reflect",         reflect_step,          QUALITY_GATES.get("reflect")),
    ("distill_l7",      distill_l7_step,       None),
    ("distill",         distill_step,          QUALITY_GATES.get("distill")),
    ("integrate",       integrate_step,        QUALITY_GATES.get("integrate")),
    ("improve",         improve_step,          None),
    ("observation",     observation_step,      None),
    ("identity",        identity_step,         None),
]

_OPTIONAL_STEPS = {
    "persona":  ("persona",  "memall.pipeline.persona.persona_step", None),
    "narrative":("narrative","memall.pipeline.narrative.narrative_step", None),
    "cluster":  ("cluster",  "memall.pipeline.cluster.cluster_step", None),
    "suggest":  ("suggest",  "memall.pipeline.suggest.suggest_step", None),
    "bridge":   ("bridge",   "memall.pipeline.bridge.bridge_analysis_step", None),
}


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
    global _pipeline_start_time
    _pipeline_start_time = time.time()
    results: dict[str, int] = {}
    entries: list[dict] = []

    if dry_run:
        return {"status": "dry_run", "results": results, "elapsed": 0}

    run_id = _create_pipeline_run()

    try:
        # ── Core pipeline steps (always run) ──
        for step_name, step_fn, gate in _PIPELINE_STEPS:
            # Conditional skips based on arguments
            if step_name == "reflect" and not include_reflect:
                continue
            if step_name == "distill" and not include_distill:
                continue
            if step_name == "integrate" and not include_integrate:
                continue
            if step_name == "improve" and not include_improve:
                continue
            if step_name == "embed_index" and not include_embed_index:
                continue
            if step_name == "identity" and not include_identity:
                continue

            entry = _run_step(step_name, step_fn, results, quality_gate=gate)
            entries.append(entry)
            # Persist after every few steps so mid-run crashes are visible
            if len(entries) % 5 == 0:
                _update_pipeline_run(run_id, entries)

        # ── Optional steps ──
        if include_persona:
            try:
                from .persona import persona_step
                entries.append(_run_step("persona", persona_step, results))
            except Exception as e:
                entries.append({"step": "persona", "status": "failed", "error": str(e)[:200]})
            if len(entries) % 5 == 0:
                _update_pipeline_run(run_id, entries)
        if include_cluster:
            try:
                from .cluster import cluster_step
                entries.append(_run_step("cluster", lambda: cluster_step(method=cluster_method), results))
            except Exception as e:
                entries.append({"step": "cluster", "status": "failed", "error": str(e)[:200]})
        if include_narrative:
            try:
                from .narrative import narrative_step
                entries.append(_run_step("narrative", narrative_step, results))
            except Exception as e:
                entries.append({"step": "narrative", "status": "failed", "error": str(e)[:200]})
        if include_suggest:
            try:
                from .suggest import suggest_step
                entries.append(_run_step("suggest", suggest_step, results))
            except Exception as e:
                entries.append({"step": "suggest", "status": "failed", "error": str(e)[:200]})
        if include_bridge:
            try:
                from .bridge import bridge_analysis_step
                entries.append(_run_step("bridge", bridge_analysis_step, results))
            except Exception as e:
                entries.append({"step": "bridge", "status": "failed", "error": str(e)[:200]})

        # ── Metrics ──
        metrics = collect_metrics()
        metrics["timestamp"] = datetime.now(timezone.utc).isoformat()
        append_metrics(metrics)
        results["metrics"] = metrics

        # ── Level discipline ──
        discipline = check_level_discipline()
        results["discipline"] = discipline
        if discipline["violations"]:
            logger.warning("Level discipline violations:\n  " + "\n  ".join(discipline["violations"]))

        _finalize_pipeline_run(run_id, entries, results)
    except Exception as e:
        elapsed = time.time() - _pipeline_start_time
        _finalize_pipeline_run(run_id, entries, results, error=str(e))
        return {"status": "error", "error": str(e), "elapsed": elapsed}

    elapsed = time.time() - _pipeline_start_time
    HookRegistry.dispatch(HOOK_STOP, arguments={"results": results, "elapsed": elapsed})
    return {"status": "ok", "results": results, "elapsed": elapsed}
