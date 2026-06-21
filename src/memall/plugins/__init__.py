"""
MemALL Plugins Package
======================
Phase 16+: Plugin system — loader, dashboard, exporter, notifier, scheduler.

Plugins are independently loadable modules that extend MemALL functionality
without modifying core code. Each plugin implements `register()` returning
metadata, and may optionally implement hook functions.
"""

__all__ = [
    "discover_plugins",
    "load_plugin",
    "reload_plugin",
    "run_plugin_hook",
    "generate_dashboard",
    "export_markdown",
    "export_jsonl",
    "export_csv",
    "export_html",
    "send_notification",
    "watch_forget_trigger",
    "watch_anomaly",
    "TaskScheduler",
]