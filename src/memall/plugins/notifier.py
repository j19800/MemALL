"""
Notifier Plugin — System notifications for forget triggers and security alerts.
"""

import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

from memall.core.db import get_conn

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
            print(f"[Notifier] Toast failed: {e}", file=sys.stderr)

    # Fallback
    prefix = {"info": "[INFO]", "warning": "[WARN]", "error": "[ERROR]"}.get(
        level, "[INFO]"
    )
    print(f"{prefix} {title}: {message}", file=sys.stderr)
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
        print(f"[Notifier] Anomaly check failed: {e}", file=sys.stderr)

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


def on_pipeline(**kwargs) -> None:
    """Send a notification when a long pipeline completes."""
    elapsed = kwargs.get("elapsed", 0)
    status = kwargs.get("status", "?")
    if elapsed > 30:  # only notify for long runs
        send_notification(
            f"MemALL — Pipeline {status}",
            f"Elapsed: {elapsed:.1f}s",
            level="info",
        )


def register():
    """Return plugin metadata."""
    return {
        "name": "notifier",
        "version": "1.0.0",
        "description": "System notifications for forget triggers and security alerts",
        "author": "MemALL",
    }