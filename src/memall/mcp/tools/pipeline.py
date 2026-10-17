import json
import concurrent.futures
from memall.pipeline.pipeline import run_pipeline

# Dedicated executor: bounded so a burst of pipeline calls cannot spawn
# unbounded threads, and daemon threads so a timed-out run does not keep the
# process alive (the worker itself cannot be force-killed mid-DB-transaction,
# so abandoning it is the only sane timeout story).
_POOL = concurrent.futures.ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="memall-pipeline"
)


def handle(arguments: dict) -> str:
    include_reflect = arguments.get("include_reflect", True)
    include_distill = arguments.get("include_distill", True)
    include_integrate = arguments.get("include_integrate", True)
    include_persona = arguments.get("include_persona", True)
    include_archive = arguments.get("include_archive", True)
    timeout = min(arguments.get("timeout", 300), 3600)
    fut = _POOL.submit(
        run_pipeline,
        include_reflect=include_reflect,
        include_distill=include_distill,
        include_integrate=include_integrate,
        include_persona=include_persona,
        include_archive=include_archive,
    )
    try:
        result = fut.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        # The pipeline keeps running to completion in its worker (abandoning a
        # mid-transaction write is unsafe), but the caller is unblocked now.
        # Not cancelling: fut.cancel() only works pre-run and would leak a
        # dead future without stopping the write either.
        result = {
            "status": "timeout",
            "error": f"pipeline exceeded {timeout}s timeout; it continues running in the background",
            "elapsed": timeout,
        }
    return json.dumps(result, ensure_ascii=False, default=str)
