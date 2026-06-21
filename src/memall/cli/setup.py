"""
memall setup --all: one-command agent MCP configuration.

Scans for installed AI agents, injects MemALL MCP Server config,
auto-backs up original configs, validates connectivity.
"""

import json
import sys
import shutil
import subprocess
from pathlib import Path
from datetime import datetime


MEMALL_HOME = Path.home() / ".memall"
MCP_SERVER_NAME = "memall"

AGENT_PATHS = {
    "claude": {
        "path": Path.home() / ".claude" / "claude_desktop_config.json",
        "format": "json",
        "name": "Claude Desktop",
        "config_key": "mcpServers",
    },
    "claude-code": {
        "path": Path.home() / ".claude" / ".claude.json",
        "format": "json",
        "name": "Claude Code (CLI)",
        "config_key": "mcpServers",
    },
    "cursor": {
        "path": Path.home() / ".cursor" / "mcp.json",
        "format": "json",
        "name": "Cursor",
        "config_key": "mcpServers",
    },
    "opencode": {
        "path": Path.home() / ".opencode" / "config.json",
        "format": "json",
        "name": "OpenCode",
        "config_key": "mcpServers",
    },
    "solo": {
        "path": Path.home() / ".solo" / "config.yaml",
        "format": "yaml",
        "name": "Solo",
        "config_key": "mcpServers",
    },
    "windsurf": {
        "path": Path.home() / ".codeium" / "windsurf" / "mcp_config.json",
        "format": "json",
        "name": "Windsurf",
        "config_key": "mcpServers",
    },
    "continue": {
        "path": Path.home() / ".continue" / "config.json",
        "format": "json",
        "name": "Continue.dev",
        "config_key": "mcpServers",
    },
    "goose": {
        "path": Path.home() / ".config" / "goose" / "config.yaml",
        "format": "yaml",
        "name": "Goose",
        "config_key": "mcpServers",
    },
    "cline-windows": {
        "path": Path.home() / "AppData" / "Roaming" / "Code" / "User" / "globalStorage"
                 / "saoudrizwan.claude-dev" / "settings" / "cline_mcp_settings.json",
        "format": "json",
        "name": "Cline (VS Code, Windows)",
        "config_key": "mcpServers",
    },
    "cline-mac": {
        "path": Path.home() / "Library" / "Application Support" / "Code" / "User" / "globalStorage"
                 / "saoudrizwan.claude-dev" / "settings" / "cline_mcp_settings.json",
        "format": "json",
        "name": "Cline (VS Code, macOS)",
        "config_key": "mcpServers",
    },
}


def _get_default_mcp_entry() -> dict:
    """Build MCP entry using the current Python interpreter path.

    Standard MCP format: explicit python executable + module invocation.
    Ensures the agent spawns memall's MCP server in the correct Python env.
    """
    return {
        "command": str(sys.executable),
        "args": ["-m", "memall.mcp.server"],
    }


def detect_agents():
    """Scan for installed agents and return dict {agent_key: {found, path, ...}}."""
    result = {}
    for key, info in AGENT_PATHS.items():
        config_path = info["path"]
        found = config_path.exists()
        result[key] = {
            "found": found,
            "path": str(config_path),
            "name": info["name"],
            "format": info["format"],
        }
    return result


def _read_json_config(path: Path) -> dict:
    """Read a JSON config file, return empty dict if missing or invalid."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _write_json_config(path: Path, data: dict):
    """Write a JSON config file atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp.replace(path)


def _read_yaml_config(path: Path) -> dict:
    """Read a YAML config file, return empty dict if missing or invalid."""
    try:
        import yaml
    except ImportError:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_yaml_config(path: Path, data: dict):
    """Write a YAML config file."""
    try:
        import yaml
    except ImportError:
        raise RuntimeError("PyYAML is required for Solo config. Install: pip install pyyaml")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, default_flow_style=False, allow_unicode=True)
    tmp.replace(path)


def backup_config(config_path: Path) -> str | None:
    """Create a .bak backup of the config file. Returns backup path or None."""
    if not config_path.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak_path = config_path.with_suffix(config_path.suffix + f".bak.{stamp}")
    shutil.copy2(config_path, bak_path)
    return str(bak_path)


def inject_mcp_config(agent_key: str, fix: bool = False, force: bool = False) -> dict:
    """
    Inject MemALL MCP Server config into an agent's config file.

    Returns:
        {"status": "ok", "backup": str, "warnings": [...]}
        {"status": "already_configured", ...}
        {"status": "error", "reason": str}
    """
    info = AGENT_PATHS.get(agent_key)
    if not info:
        return {"status": "error", "reason": f"Unknown agent: {agent_key}"}

    config_path = info["path"]

    if not config_path.exists():
        return {"status": "error",
                "reason": f"{info['name']} config not found at {config_path}. "
                          f"Install {info['name']} first, or use --config to specify a custom path."}

    # Build MCP entry (uses current Python interpreter, no params needed)
    mcp_entry = _get_default_mcp_entry()

    # Read existing config
    if info["format"] == "json":
        config = _read_json_config(config_path)
    else:
        config = _read_yaml_config(config_path)

    mcp_section = config.get(info["config_key"], {})
    if not isinstance(mcp_section, dict):
        mcp_section = {}

    # Check for existing memall config
    existing = mcp_section.get(MCP_SERVER_NAME)

    if existing is not None and not fix and not force:
        return {
            "status": "already_configured",
            "message": (f"{info['name']}: MemALL already configured. "
                        f"Use --fix to repair or --force to overwrite."),
            "existing": existing,
        }

    # Backup original config
    bak = backup_config(config_path)

    # Inject / update
    mcp_section[MCP_SERVER_NAME] = mcp_entry
    config[info["config_key"]] = mcp_section

    # Write back
    try:
        if info["format"] == "json":
            _write_json_config(config_path, config)
        else:
            _write_yaml_config(config_path, config)
    except Exception as e:
        return {"status": "error", "reason": str(e)}

    return {
        "status": "ok",
        "agent": agent_key,
        "name": info["name"],
        "path": str(config_path),
        "backup": bak,
        "entry": mcp_entry,
    }


def verify_connection(agent_key: str) -> dict:
    """
    Best-effort connection verification.
    Checks that the configured Python interpreter can import memall.mcp.server.
    """
    info = AGENT_PATHS.get(agent_key)
    if not info:
        return {"status": "error", "reason": f"Unknown agent: {agent_key}"}

    config_path = info["path"]
    if not config_path.exists():
        return {"status": "error", "reason": "Config file not found"}

    # Verify config contains memall entry
    if info["format"] == "json":
        config = _read_json_config(config_path)
    else:
        config = _read_yaml_config(config_path)

    mcp_section = config.get(info["config_key"], {})
    memall_entry = mcp_section.get(MCP_SERVER_NAME)

    if not memall_entry:
        return {"status": "error", "reason": "MemALL not configured in this agent"}

    python_path = memall_entry.get("command", "")
    try:
        result = subprocess.run(
            [python_path, "-c", "import memall.mcp.server; print('ok')"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return {"status": "ok", "message": "memall.mcp.server importable"}
        else:
            return {"status": "warning",
                    "reason": f"Import check failed (code {result.returncode})",
                    "stderr": result.stderr.strip()[:100]}
    except FileNotFoundError:
        return {"status": "warning",
                "reason": f"Python interpreter not found: {python_path}"}
    except subprocess.TimeoutExpired:
        return {"status": "warning", "reason": "Verification timed out"}
    except Exception as e:
        return {"status": "warning", "reason": str(e)}


def remove_mcp_config(agent_key: str) -> dict:
    """
    Remove MemALL MCP Server config from an agent's config file.
    Returns {"status": "ok"} or {"status": "error", ...}.
    """
    info = AGENT_PATHS.get(agent_key)
    if not info:
        return {"status": "error", "reason": f"Unknown agent: {agent_key}"}

    config_path = info["path"]
    if not config_path.exists():
        return {"status": "ok", "message": f"{info['name']} config not found, nothing to remove"}

    # Read config
    if info["format"] == "json":
        config = _read_json_config(config_path)
    else:
        config = _read_yaml_config(config_path)

    mcp_section = config.get(info["config_key"], {})
    if MCP_SERVER_NAME not in mcp_section:
        return {"status": "ok", "message": f"{info['name']}: MemALL not configured"}

    # Backup before removal
    bak = backup_config(config_path)

    # Remove
    del mcp_section[MCP_SERVER_NAME]
    config[info["config_key"]] = mcp_section

    # Clean up empty mcpServers key
    if not mcp_section and info["config_key"] in config:
        del config[info["config_key"]]

    if info["format"] == "json":
        _write_json_config(config_path, config)
    else:
        _write_yaml_config(config_path, config)

    return {
        "status": "ok",
        "agent": agent_key,
        "name": info["name"],
        "path": str(config_path),
        "backup": bak,
    }


def cmd_setup(args):
    """CLI handler for `memall setup`."""
    if args.all:
        return _setup_all(args)

    if args.agent:
        if args.fix:
            return _setup_agent_fix(args)
        return _setup_agent_single(args)

    # No args: print help
    print("Usage:")
    print("  memall setup --all                    Configure all detected agents")
    print("  memall setup --agent <name>           Configure a single agent")
    print("  memall setup --agent <name> --fix     Repair existing config")
    print("  memall setup --agent <name> --config <path>  Custom config path")
    print()
    print("Supported agents:")
    print("  claude         Claude Desktop")
    print("  claude-code    Claude Code (CLI)")
    print("  cursor         Cursor")
    print("  opencode       OpenCode")
    print("  solo           Solo")
    print("  windsurf       Windsurf")
    print("  continue       Continue.dev")
    print("  goose          Goose")
    print("  cline-windows  Cline (VS Code, Windows)")
    print("  cline-mac      Cline (VS Code, macOS)")
    print()
    print("Tip: run `memall doctor` to check connection status after setup.")


def _setup_all(args):
    """Scan all agents and configure each."""
    agents = detect_agents()

    # Phase 1: Scan
    print("Scanning installed agents...")
    installed = []
    not_found = []
    for key, info in agents.items():
        if info["found"]:
            print(f"  [OK] {info['name']:12s} -> {info['path']}")
            installed.append(key)
        else:
            print(f"  [--] {info['name']:12s} not detected (install to enable)")
            not_found.append(key)

    if not installed:
        print("\nNo supported AI agents detected.")
        print("Supported: Claude Desktop, Claude Code, Cursor, OpenCode, "
              "Solo, Windsurf, Continue.dev, Goose, Cline")
        print("Custom config: memall setup --agent <name> --config <path>")
        return

    # Phase 2: Configure
    print("\nConfiguring...")
    results = []
    for agent_key in installed:
        result = inject_mcp_config(agent_key)
        results.append((agent_key, result))
        info = agents[agent_key]
        if result["status"] == "ok":
            print(f"  [OK] {info['name']:12s} injected MemALL MCP Server")
        elif result["status"] == "already_configured":
            print(f"  [--] {info['name']:12s} {result['message']}")
        else:
            print(f"  [!!] {info['name']:12s} failed: {result.get('reason', 'unknown')}")

    # Show backups
    backups = [r.get("backup") for _, r in results if r.get("backup")]
    if backups:
        print(f"\nBackups saved ({len(backups)} files):")
        for b in backups:
            print(f"  {b}")

    # Phase 3: Verify
    print("\nVerifying...")
    for agent_key, result in results:
        if result["status"] == "ok":
            v = verify_connection(agent_key)
            info = agents[agent_key]
            if v["status"] == "ok":
                print(f"  [OK] {info['name']:12s} connection OK")
            elif v["status"] == "warning":
                print(f"  [??] {info['name']:12s} {v.get('reason', '?')}")
            else:
                print(f"  [!!] {info['name']:12s} {v.get('reason', '?')}")

    # Summary
    ok_count = sum(1 for _, r in results if r["status"] == "ok")
    already_count = sum(1 for _, r in results if r["status"] == "already_configured")
    print(f"\nDone! {ok_count} configured, {already_count} already set up."
          f" Restart your agents to activate memory features.")
    print(f"Tip: run `memall doctor` to check all agent connections.")


def _setup_agent_single(args):
    """Configure a single agent."""
    agent_key = args.agent.lower()
    if agent_key not in AGENT_PATHS:
        print(f"Unknown agent: {args.agent}. Supported: {', '.join(AGENT_PATHS)}", file=sys.stderr)
        sys.exit(1)

    # Override config path if provided
    if hasattr(args, "config") and args.config:
        custom_path = Path(args.config)
        AGENT_PATHS[agent_key]["path"] = custom_path

    info = AGENT_PATHS[agent_key]
    if not info["path"].exists() and not (hasattr(args, "config") and args.config):
        print(f"{info['name']} not detected at {info['path']}.", file=sys.stderr)
        print(f"You can manually register: memall setup --agent {agent_key} --config <path>", file=sys.stderr)
        sys.exit(1)

    result = inject_mcp_config(agent_key)

    if result["status"] == "ok":
        print(f"  [OK] {info['name']} configured")
        if result.get("backup"):
            print(f"  Backup: {result['backup']}")
        print(f"  Entry: {json.dumps(result['entry'], ensure_ascii=False)}")
        v = verify_connection(agent_key)
        if v["status"] == "ok":
            print(f"  [OK] Connection verified")
        elif v["status"] == "warning":
            print(f"  [??] {v.get('reason', '?')}")
        else:
            print(f"  [!!] {v.get('reason', '?')}")
    elif result["status"] == "already_configured":
        print(result["message"])
    else:
        print(f"Error: {result.get('reason', 'unknown')}", file=sys.stderr)
        sys.exit(1)


def _setup_agent_fix(args):
    """Repair existing MemALL config for a single agent."""
    agent_key = args.agent.lower()
    if agent_key not in AGENT_PATHS:
        print(f"Unknown agent: {args.agent}. Supported: {', '.join(AGENT_PATHS)}", file=sys.stderr)
        sys.exit(1)

    info = AGENT_PATHS[agent_key]
    if not info["path"].exists():
        print(f"{info['name']} config not found at {info['path']}.", file=sys.stderr)
        print(f"Use `memall setup --agent {agent_key}` to create a new config.", file=sys.stderr)
        sys.exit(1)

    # Force overwrite with fix
    result = inject_mcp_config(agent_key, fix=True)

    if result["status"] == "ok":
        print(f"  [OK] {info['name']} config repaired")
        if result.get("backup"):
            print(f"  Backup: {result['backup']}")
        v = verify_connection(agent_key)
        if v["status"] == "ok":
            print(f"  [OK] Connection verified")
        elif v["status"] == "warning":
            print(f"  [??] {v.get('reason', '?')}")
    else:
        print(f"Error: {result.get('reason', 'unknown')}", file=sys.stderr)
        sys.exit(1)
