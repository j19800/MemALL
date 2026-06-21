import logging
import time
from typing import Generator

logger = logging.getLogger(__name__)


class PipelineEvent:
    """Lightweight pipeline event — step name, status, result."""
    __slots__ = ("step", "status", "result", "elapsed", "error")

    def __init__(self, step: str, status: str, result=None, elapsed: float = 0, error: str = ""):
        self.step = step
        self.status = status  # "start" | "done" | "error"
        self.result = result
        self.elapsed = elapsed
        self.error = error

    def to_dict(self) -> dict:
        d = {"step": self.step, "status": self.status, "elapsed": self.elapsed}
        if self.result is not None:
            d["result"] = self.result
        if self.error:
            d["error"] = self.error
        return d


def _run_step(name: str, step_fn, *args, start: float, **kwargs) -> PipelineEvent:
    yield PipelineEvent(name, "start", elapsed=time.time() - start)
    try:
        result = step_fn(*args, **kwargs)
        yield PipelineEvent(name, "done", result=result, elapsed=time.time() - start)
    except Exception as e:
        logger.exception("Pipeline step %s failed", name)
        yield PipelineEvent(name, "error", error=str(e), elapsed=time.time() - start)


def run_pipeline_stream(
    include_reflect: bool = True,
    include_distill: bool = True,
    include_integrate: bool = True,
    include_persona: bool = True,
    include_identity: bool = True,
    include_improve: bool = True,
    include_procedure: bool = True,
    include_cluster: bool = False,
    include_narrative: bool = False,
    include_suggest: bool = False,
    include_bridge: bool = False,
    include_embed_index: bool = False,
    cluster_method: str = "embedding",
) -> Generator[PipelineEvent, None, dict]:
    """Streaming pipeline — yields PipelineEvent for each step."""
    from memall.pipeline.pipeline import check_level_discipline
    from memall.pipeline.enrich import enrich_step
    from memall.pipeline.classify import classify_step
    from memall.pipeline.link import link_step
    from memall.pipeline.decay import decay_step
    from memall.pipeline.backup import backup_step
    from memall.pipeline.metrics import collect_metrics, append_metrics
    from memall.pipeline.narrative import narrative_step
    from memall.pipeline.cluster import cluster_step
    from memall.pipeline.suggest import suggest_step
    from memall.pipeline.bridge import bridge_analysis_step
    from memall.pipeline.distill import distill_step
    from memall.pipeline.reflect import reflect_step
    from memall.pipeline.embed_index import embed_index_step
    from memall.pipeline.integrate import integrate_step
    from memall.pipeline.procedure import procedure_step
    from memall.pipeline.cleanup import cleanup_step
    from memall.pipeline.observe import observation_step
    from memall.pipeline.identity import identity_step
    from memall.pipeline.time_slice import time_slice_step
    from memall.pipeline.arc_status import arc_status_step
    from memall.pipeline.echo import echo_step
    from memall.pipeline.epoch import epoch_step
    from memall.pipeline.convergence import convergence_step, resolve_pending_deliberations
    from memall.pipeline.improve import improve_step
    from memall.pipeline.persona import persona_step

    start = time.time()
    results: dict[str, int] = {}

    step_names = [
        ("enrich", enrich_step),
        ("cleanup", cleanup_step),
        ("classify", classify_step),
        ("time_slice", time_slice_step),
        ("arc_status", arc_status_step),
        ("echo", echo_step),
        ("epoch", epoch_step),
        ("convergence", resolve_pending_deliberations),
    ]
    if include_procedure:
        step_names.append(("procedure", procedure_step))
    step_names += [
        ("link", link_step),
        ("decay", decay_step),
        ("backup", backup_step),
        ("embed_index", embed_index_step),
    ]
    if include_reflect:
        step_names.append(("reflect", reflect_step))
    if include_distill:
        step_names.append(("distill", distill_step))
    if include_integrate:
        step_names.append(("integrate", integrate_step))
    if include_improve:
        step_names.append(("improve", improve_step))
    step_names.append(("observation", observation_step))
    if include_identity:
        step_names.append(("identity", identity_step))
    if include_persona:
        step_names.append(("persona", persona_step))
    if include_narrative:
        step_names.append(("narrative", narrative_step))
    if include_cluster:
        step_names.append(("cluster", lambda: cluster_step(method=cluster_method)))
    if include_suggest:
        step_names.append(("suggest", suggest_step))
    if include_bridge:
        step_names.append(("bridge", bridge_analysis_step))

    for name, fn in step_names:
        yield PipelineEvent(name, "start", elapsed=time.time() - start)
        try:
            r = fn()
            results[name] = r if not isinstance(r, dict) else r.get("count", r)
            yield PipelineEvent(name, "done", result=r, elapsed=time.time() - start)
        except Exception as e:
            logger.exception("Pipeline step %s failed", name)
            yield PipelineEvent(name, "error", error=str(e), elapsed=time.time() - start)

    try:
        metrics = collect_metrics()
        metrics["timestamp"] = __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat()
        append_metrics(metrics)
    except Exception:
        logger.warning("stream.py: silent error", exc_info=True)

    try:
        discipline = check_level_discipline()
    except Exception:
        discipline = {}

    elapsed = time.time() - start
    return {"status": "ok", "results": results, "elapsed": elapsed, "discipline": discipline}
