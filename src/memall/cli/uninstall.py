"""
memall uninstall: remove MemALL MCP server config from agents.

Design principle: data never leaves you — uninstall only removes MCP config.
Use --purge to delete the entire ~/.memall/ directory.
"""

import json
import shutil
import sys
from pathlib import Path
from datetime import datetime

from memall.cli.setup import AGENT_PATHS, MCP_SERVER_NAME

MEMALL_HOME = Path.home() / ".memall"


def _remove_mcp_config(agent_key):
    """Remove memall MCP config from a single agent. Returns (status, message)."""
    if agent_key not in AGENT_PATHS:
        return "unknown", f"Unknown agent: {agent_key}. Use: claude, cursor, opencode, solo"

    agent_info = AGENT_PATHS[agent_key]
    config_path = agent_info["path"]

    if not config_path.exists():
        return "not_found", f"{agent_info['name']} config not found at {config_path}"

    if agent_info["format"] == "yaml":
        removed = _remove_from_yaml(config_path, agent_info)
        if removed:
            return "ok", f"Removed {agent_info['name']} MCP config from {config_path}"
        else:
            return "not_configured", f"{agent_info['name']} does not have MemALL configured"

    # JSON format
    try:
        raw = config_path.read_text(encoding="utf-8")
        config = json.loads(raw)
    except (json.JSONDecodeError, FileNotFoundError):
        return "error", f"Failed to parse {config_path}"

    config_key = agent_info["config_key"]
    if config_key not in config:
        return "not_configured", f"{agent_info['name']} has no mcpServers section"

    if MCP_SERVER_NAME not in config[config_key]:
        return "not_configured", f"{agent_info['name']} does not have MemALL configured"

    # Backup before removal
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak_path = config_path.parent / (config_path.name + f".uninstall-{timestamp}.bak")
    shutil.copy2(config_path, bak_path)

    # Remove the memall entry
    del config[config_key][MCP_SERVER_NAME]
    # Atomic write: temp → rename
    tmp_path = config_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.rename(config_path)

    return "ok", f"Removed MemALL from {agent_info['name']} (backup: {bak_path.name})"


def _remove_from_yaml(config_path, agent_info):
    """Remove memall entry from a YAML config file (Solo). Returns True if removed."""
    try:
        import yaml
    except ImportError:
        print("PyYAML not installed. Cannot process YAML config.", file=sys.stderr)
        sys.exit(1)

    raw = config_path.read_text(encoding="utf-8")
    config = yaml.safe_load(raw) or {}

    config_key = agent_info["config_key"]
    if config_key not in config or not isinstance(config[config_key], dict):
        return False

    if MCP_SERVER_NAME not in config[config_key]:
        return False

    # Backup
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak_path = config_path.parent / (config_path.name + f".uninstall-{timestamp}.bak")
    shutil.copy2(config_path, bak_path)

    del config[config_key][MCP_SERVER_NAME]
    # Atomic write: temp → rename
    tmp_path = config_path.with_suffix(".tmp")
    tmp_path.write_text(yaml.dump(config, allow_unicode=True, default_flow_style=False), encoding="utf-8")
    tmp_path.rename(config_path)
    return True


def _purge_data():
    """Delete ~/.memall/ directory entirely."""
    if not MEMALL_HOME.exists():
        print(f"Data directory {MEMALL_HOME} does not exist. Nothing to purge.")
        return

    shutil.rmtree(MEMALL_HOME)
    print(f"Purged {MEMALL_HOME}")


def cmd_uninstall(args):
    all_agents = args.all or False
    target_agent = getattr(args, "agent", None)
    purge = getattr(args, "purge", False)

    if not all_agents and not target_agent:
        print("Use --all to remove from all agents, or --agent to target one.", file=sys.stderr)
        sys.exit(1)

    results = {}
    if all_agents:
        for key in AGENT_PATHS:
            status, msg = _remove_mcp_config(key)
            results[key] = (status, msg)
    elif target_agent:
        status, msg = _remove_mcp_config(target_agent)
        results[target_agent] = (status, msg)

    # Print results
    print()
    for agent_key, (status, msg) in results.items():
        icon = {"ok": "✓", "unknown": "✗", "not_found": "⚠", "not_configured": "○", "error": "✗"}.get(status, "?")
        print(f"  {icon} {msg}")

    if purge:
        print()
        print("Purging all MemALL data...")
        _purge_data()
    else:
        print()
        print(f"Data directory {MEMALL_HOME} preserved.")
        print("To delete all data, run: memall uninstall --all --purge")
