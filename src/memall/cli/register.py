"""memall register --agent: manual agent registration for setup --all undetected agents.

Creates registration files in ~/.memall/agents/, supports MCP and HTTP agent types.
Registered agents are visible to `memall doctor` for connection status checks.
"""

import logging

logger = logging.getLogger(__name__)

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from datetime import datetime, timezone

AGENTS_DIR = Path.home() / ".memall" / "agents"


def _ensure_agents_dir():
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)


def register_agent(name: str, agent_type: str, url: str = "", command: str = "",
                   description: str = "") -> dict:
    """
    Register a new agent in ~/.memall/agents/<name>.json
    """
    _ensure_agents_dir()

    valid_types = ["mcp", "http", "stdio"]
    if agent_type not in valid_types:
        return {"status": "error", "reason": f"Invalid type: {agent_type}. Must be one of {valid_types}"}

    if agent_type == "http" and not url:
        return {"status": "error", "reason": "--url is required for HTTP agents"}

    if agent_type == "mcp" and not command:
        command = "memall-mcp-server"

    reg_path = AGENTS_DIR / f"{name}.json"
    now = datetime.now(timezone.utc).isoformat()

    reg_data = {
        "agent_name": name,
        "agent_type": agent_type,
        "registered_at": now,
        "description": description,
    }

    if agent_type == "http":
        reg_data["url"] = url
    elif agent_type == "mcp":
        reg_data["command"] = command

    # Preserve existing data if updating
    existing = None
    if reg_path.exists():
        try:
            existing = json.loads(reg_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.warning("register.py: silent error", exc_info=True)

    if existing:
        reg_data["registered_at"] = existing.get("registered_at", now)
        reg_data["updated_at"] = now
        reg_data["url"] = url or existing.get("url", "")
        reg_data["command"] = command or existing.get("command", "")
    else:
        reg_data["updated_at"] = now

    # Atomic write: temp → rename
    tmp_path = reg_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(reg_data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp_path.rename(reg_path)
    return {"status": "ok", "agent_name": name, "agent_type": agent_type, "path": str(reg_path)}


def list_registered_agents() -> list:
    """List all manually registered agents."""
    _ensure_agents_dir()
    agents = []
    for f in sorted(AGENTS_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            data["_file"] = str(f)
            agents.append(data)
        except (json.JSONDecodeError, OSError):
            logger.warning("register.py: silent error", exc_info=True)
    return agents


def remove_registered_agent(name: str) -> dict:
    """Remove a manually registered agent."""
    reg_path = AGENTS_DIR / f"{name}.json"
    if not reg_path.exists():
        return {"status": "error", "reason": f"Agent '{name}' not registered"}
    reg_path.unlink()
    return {"status": "ok", "agent_name": name, "message": f"Agent '{name}' unregistered"}


def check_agent_connection(agent_name: str, agent_type: str, url: str = "",
                           command: str = "") -> dict:
    """
    Check if a registered agent is reachable.
    Used by `memall doctor` to verify registered agent health.
    """
    if agent_type == "http" and url:
        try:
            req = urllib.request.Request(url, method="HEAD")
            resp = urllib.request.urlopen(req, timeout=5)
            return {"status": "PASS", "detail": f"HTTP {resp.status}", "code": resp.status}
        except urllib.error.URLError as e:
            return {"status": "FAIL", "detail": f"Connection failed: {e.reason}", "code": 0}
        except Exception as e:
            return {"status": "FAIL", "detail": str(e), "code": 0}

    if agent_type == "mcp" and command:
        from shutil import which
        exe = which(command)
        if exe:
            return {"status": "PASS", "detail": f"Found at {exe}"}
        else:
            return {"status": "WARN", "detail": f"Command '{command}' not found in PATH"}

    return {"status": "PASS", "detail": "stdio/passive agent — no connectivity check"}


def cmd_register(args):
    """CLI handler for `memall register`."""
    if args.list:
        agents = list_registered_agents()
        if not agents:
            print("No manually registered agents.")
            return
        print(f"Registered agents ({len(agents)}):")
        for a in agents:
            a_type = a.get("agent_type", "?")
            extra = f"url={a.get('url')}" if a.get("url") else f"cmd={a.get('command')}"
            print(f"  {a['agent_name']:20s} type={a_type:5s}  {extra}")
            if a.get("description"):
                print(f"  {'':20s} {a['description']}")
        return

    if args.remove:
        result = remove_registered_agent(args.remove)
        if result["status"] == "ok":
            print(result["message"])
        else:
            print(f"Error: {result['reason']}", file=sys.stderr)
            sys.exit(1)
        return

    if args.agent:
        result = register_agent(
            name=args.agent,
            agent_type=args.type or "mcp",
            url=args.url or "",
            command=args.boot_cmd or "",
            description=args.description or "",
        )
        if result["status"] == "ok":
            print(f"Agent '{result['agent_name']}' registered ({result['agent_type']})")
            print(f"  config: {result['path']}")
            print(f"  Run 'memall doctor' to check connection status.")
        else:
            print(f"Error: {result['reason']}", file=sys.stderr)
            sys.exit(1)
        return

    # Fallback: show help
    print("Usage:")
    print("  memall register --agent <name> --type mcp|http|stdio [--url URL] [--command CMD] [--description TEXT]")
    print("  memall register --list")
    print("  memall register --remove <name>")
