"""
Notifier Plugin — System notifications for forget triggers and security alerts.
"""

import logging
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

from memall.core.db import get_conn

logger = logging.getLogger(__name__)

# Windows notification support (optional)
try:
    from win10toast import ToastNotifier

    _HAS_TOAST = True
except ImportError:
    _HAS_TOAST = False


def send_notification(title: str, message: str, level: str = "info") -> bool:
    """Send a system notification.

    On Windows, uses win10toast (if available). Falls back to printing to
    stderr on all other platforms.

    Args:
        title: Notification title.
        message: Notification body.
        level: Severity level (info/warning/error), used for fallback prefix.

    Returns:
        True if notification was sent natively, False if fallback was used.
    """
    if _HAS_TOAST:
        try:
            toaster = ToastNotifier()
            toaster.show_toast(
                title,
                message,
                duration=5,
                threaded=True,
            )
            return True
        except Exception as e:
            logger.warning("Toast failed: %s", e)

    # Fallback
    prefix = {"info": "[INFO]", "warning": "[WARN]", "error": "[ERROR]"}.get(
        level, "[INFO]"
    )
    fallback_msg = f"{prefix} {title}: {message}"
    logger.warning("%s", fallback_msg)
    return False


def watch_forget_trigger(threshold_days: int = 90) -> Optional[dict]:
    """Check for memories approaching TTL expiration and send a notification.

    Memories within 7 days of the threshold trigger a warning.

    Args:
        threshold_days: TTL threshold in days (default 90).

    Returns:
        Dict with 'expiring_count' and 'message', or None if nothing expiring.
    """
    conn = get_conn()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=threshold_days - 7)).isoformat()

    count = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE created_at < ? AND level != 'P0'",
        (cutoff,),
    ).fetchone()[0]

    if count > 0:
        msg = f"{count} memories will expire within 7 days (TTL={threshold_days}d)"
        send_notification("MemALL — Expiry Warning", msg, level="warning")
        return {"expiring_count": count, "message": msg}

    return None


def watch_anomaly() -> Optional[dict]:
    """Run security audit and notify if sensitive data is found.

    Returns:
        Dict with 'sensitive_count' and risk level, or None if clean.
    """
    try:
        from memall.pipeline.security import audit_sensitive

        result = audit_sensitive()
        count = result.get("total_findings", 0)

        if count > 0:
            risk = result.get("risk_level", "high")
            msg = f"Sensitive data found: {count} items (risk={risk})"
            send_notification("MemALL — Security Alert", msg, level="error")
            return {"sensitive_count": count, "risk_level": risk, "message": msg}

    except ImportError:
        # security module not available
        pass
    except Exception as e:
        logger.warning("Anomaly check failed: %s", e)

    return None


def on_step_fail(**kwargs) -> None:
    """Send a notification when a pipeline step fails."""
    step = kwargs.get("step_name", "?")
    error = kwargs.get("error", "?")
    send_notification(
        f"MemALL — Step Failed: {step}",
        error,
        level="error",
    )
    _record_plugin_event(
        "on_step_fail",
        f"Pipeline step '{step}' failed: {error[:120]}",
        status="failed",
    )


def on_pipeline(**kwargs) -> None:
    """Send a notification when a pipeline completes, with rich reporting."""
    elapsed = kwargs.get("elapsed", 0)
    status = kwargs.get("status", "?")
    results = kwargs.get("results", {})

    # Build a human-readable summary from step results
    step_oks = 0
    step_skipped = 0
    step_fails = 0
    skipped_reasons = []
    detail_lines = []

    for step_name, step_result in results.items():
        if step_name in ("metrics", "discipline"):
            continue
        if isinstance(step_result, int):
            if step_result > 0:
                step_oks += 1
                detail_lines.append(f"  {step_name}: {step_result}")
            else:
                step_skipped += 1
                skipped_reasons.append(step_name)
        elif isinstance(step_result, dict):
            # Step results with quality gate info
            err = step_result.get("error")
            if err:
                step_fails += 1
                detail_lines.append(f"  {step_name}: FAILED - {err[:80]}")
            elif step_result.get("status") == "ok":
                step_oks += 1
                val = _coerce_step_value(step_result)
                detail_lines.append(f"  {step_name}: {val}")
            elif step_result.get("status") == "failed":
                step_fails += 1
                detail_lines.append(f"  {step_name}: FAILED - {step_result.get('error', '?')[:80]}")

    # Compose notification message
    summary = f"{status} in {elapsed:.1f}s | {step_oks} ok"
    if step_fails:
        summary += f", {step_fails} failed"
    if step_skipped:
        summary += f", {step_skipped} gated"

    message_lines = [summary]
    if detail_lines:
        message_lines.append("Steps:")
        message_lines.extend(detail_lines[:8])  # limit to 8 lines
        if len(detail_lines) > 8:
            message_lines.append(f"  ... and {len(detail_lines) - 8} more")

    message = "\n".join(message_lines)

    # Notify only for meaningful runs (ok steps > 0 or pipeline took > 5s)
    if step_oks > 0 or elapsed > 5:
        send_notification(
            f"MemALL — Pipeline {status}",
            message,
            level="info" if status == "completed" else "warning",
        )

    # Record hook activity event
    _record_plugin_event(
        "on_pipeline",
        f"Pipeline {status}: {step_oks} ok, {step_fails} failed, {step_skipped} gated in {elapsed:.1f}s",
        status="failed" if step_fails else "ok",
    )


def _coerce_step_value(result: dict) -> int:
    """Extract a numeric value from a step result dict."""
    for k in ("processed", "count", "created", "result", "total", "new",
              "personal_created", "global_created", "integrated"):
        v = result.get(k)
        if isinstance(v, int):
            return v
        if isinstance(v, dict):
            # Quality gate wrapped result
            inner = v.get("result", v.get("value", 0))
            if isinstance(inner, int):
                return inner
    return 0


def register():
    """Return plugin metadata."""
    return {
        "name": "notifier",
        "version": "1.0.0",
        "description": "System notifications for forget triggers and security alerts",
        "author": "MemALL",
    }


# ── Hook activity recording helper ───────────────────────────────────────

def _record_plugin_event(hook_point: str, description: str, status: str = "ok") -> None:
    """Record a notifier plugin event into the hook effects ring buffer."""
    try:
        from memall.mcp.hook_effects import record_event as _re
        _re(hook_point=hook_point, description=description, plugin="notifier", status=status)
    except Exception:
        logger.warning("Failed to record notifier plugin event for %s", hook_point, exc_info=True)