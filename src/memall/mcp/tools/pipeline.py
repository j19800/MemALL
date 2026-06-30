import json
import concurrent.futures
from memall.pipeline.pipeline import run_pipeline

# Module-level executor shared across calls
_POOL = concurrent.futures.ThreadPoolExecutor()


def handle(arguments: dict) -> str:
    include_reflect = arguments.get("include_reflect", True)
    include_distill = arguments.get("include_distill", True)
    include_integrate = arguments.get("include_integrate", True)
    include_persona = arguments.get("include_persona", True)
    include_archive = arguments.get("include_archive", True)
    timeout = arguments.get("timeout", 300)
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
        result = {"status": "timeout", "error": f"pipeline exceeded {timeout}s timeout", "elapsed": timeout}
    return json.dumps(result, ensure_ascii=False, default=str)
