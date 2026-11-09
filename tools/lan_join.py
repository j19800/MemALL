#!/usr/bin/env python3
"""
lan_join.py — Smart one-command LAN onboarding for MemALL.

Goal: a client machine joins the MemALL gateway with ZERO knowledge of
IP / port / token / config format.  Just run:

    python3 lan_join.py            # first run: 1 confirmation, then done
    python3 lan_join.py            # later runs: fully silent, no questions

What it does, automatically:
  1. DISCOVER   UDP beacon on 9920; if none, fall back to a quick /health
                sweep of the local /24 subnet (no token needed for /health).
  2. PAIR       first run only: ask for the one-time pairing code (read from
                stdin, never stored) and exchange it for the Bearer token.
                Token is cached at ~/.config/memall/lan.json (0600).
  3. CONFIGURE  write mcp.json (generic clients) AND idempotently patch
                DSH's cordis.patch.yml (replace existing mcp-memall block,
                or insert one).  All output files are 0600.

No third-party dependencies, Python 3.9+.  Run with --help for options.

Security: the pairing code never touches disk or any generated file;
/pair rotates it after one use.  The cached token is a live credential —
keep ~/.config/memall/lan.json private.
"""
from __future__ import annotations

import argparse
import getpass
import ipaddress
import json
import os
import socket
import stat
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

DISCOVERY_PORT = 9920
BEACON_TYPE = "memall_discovery"
DEFAULT_AGENT = "lan-client"
CACHE = Path.home() / ".config" / "memall" / "lan.json"


# ── small utils ────────────────────────────────────────────────────────
def _get(url: str, token: str = "", timeout: float = 3.0):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def _post(url: str, body: dict, timeout: float = 8.0):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _private_write(path: Path, text: str, *, keep_mode: bool = False) -> None:
    """Write text; by default force 0600.  When patching an EXISTING config
    file, preserve its original mode so DSH can still read it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if keep_mode and path.exists():
        mode = stat.S_IMODE(path.stat().st_mode)
    else:
        mode = stat.S_IRUSR | stat.S_IWUSR  # 0600
    path.write_text(text, encoding="utf-8")
    os.chmod(path, mode)


# ── 1. discovery (beacon -> subnet sweep) ──────────────────────────────
def discover_beacon(timeout: float, port: int) -> str | None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.settimeout(timeout)
    try:
        try:
            s.bind(("", port))
        except OSError:
            s.bind(("", 0))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                s.settimeout(max(0.1, deadline - time.monotonic()))
                data, addr = s.recvfrom(4096)
                m = json.loads(data)
                if m.get("type") == BEACON_TYPE:
                    return f"http://{addr[0]}:{m.get('port', port)}"
            except socket.timeout:
                break
            except (ValueError, UnicodeDecodeError):
                continue
    finally:
        s.close()
    return None


def _probe(ip: str, port: int) -> str | None:
    try:
        status, _ = _get(f"http://{ip}:{port}/health", timeout=1.5)
        if status == 200:
            return f"http://{ip}:{port}"
    except Exception:
        pass
    return None


def sweep_subnet(port: int) -> str | None:
    """Fallback: probe /health on the local /24 (concurrent, ~2s)."""
    ips = []
    for iface_ip in _local_ipv4s():
        net = ipaddress.ip_network(f"{iface_ip}/24", strict=False)
        ips = [str(h) for h in net.hosts()]
        break
    if not ips:
        return None
    with ThreadPoolExecutor(max_workers=64) as ex:
        futs = [ex.submit(_probe, ip, port) for ip in ips]
        for f in as_completed(futs):
            r = f.result()
            if r:
                return r  # first responder wins
    return None


def _local_ipv4s() -> list[str]:
    out = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        out.append(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:
        out.append(socket.gethostbyname(socket.gethostname()))
    except Exception:
        pass
    return [i for i in dict.fromkeys(out) if not i.startswith("127.")]


def find_server(timeout: float, port: int) -> str:
    base = discover_beacon(timeout, port)
    if base:
        print(f"  ✓ beacon: {base}")
        return base
    print("  · no beacon, sweeping local subnet for /health ...")
    base = sweep_subnet(port)
    if not base:
        raise RuntimeError("no MemALL gateway found (beacon + subnet sweep both failed)")
    print(f"  ✓ /health: {base}")
    return base


# ── 2. token (pair once, then cache) ───────────────────────────────────
def load_cache() -> dict:
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_cache(data: dict) -> None:
    _private_write(CACHE, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def resolve_token(base: str, device: str, force_pair: bool) -> str:
    cache = load_cache()
    if not force_pair and cache.get("base") == base and cache.get("token"):
        print("  ✓ cached token (no pairing needed)")
        return cache["token"]
    print("  · first join for this server — one-time pairing")
    try:
        code = getpass.getpass("    pairing code (from server's gateway.log, hidden): ").strip()
    except (EOFError, KeyboardInterrupt):
        raise RuntimeError("aborted")
    if not code:
        raise RuntimeError("empty pairing code")
    r = _post(base + "/pair", {"device_name": device, "code": code})
    del code
    if not r.get("paired"):
        raise RuntimeError(f"pairing rejected: {r.get('error')}")
    token = r["token"]
    print("  ✓ paired (code now invalid on server)")
    return token


# ── 3. configure (idempotent) ──────────────────────────────────────────
def mcp_json(base: str, token: str, agent: str) -> str:
    return json.dumps({"mcpServers": {"memall": {
        "url": base + "/mcp",
        "transport": "streamable-http",
        "headers": {"Authorization": f"Bearer {token}",
                    "X-MemAll-Agent": agent},
    }}}, indent=2, ensure_ascii=False) + "\n"


def patch_cordis(path: Path, base: str, token: str, agent: str) -> bool:
    """Replace the existing mcp-memall block, or append a new one. Idempotent."""
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    # locate the '- id: mcp-memall' insert block
    start = next((i for i, l in enumerate(lines) if l.strip().startswith("- id: mcp-memall")), None)
    block = (
        "- insert:\n"
        "    - id: mcp-memall\n"
        "      name: '@deepseek-ai/dsh-mcp-client'\n"
        "      config:\n"
        "        serverName: memall\n"
        "        transport: streamable-http\n"
        f"        url: {base}/mcp\n"
        "        headers:\n"
        f"          Authorization: 'Bearer {token}'\n"
        f"          X-MemAll-Agent: '{agent}'\n"
        "        failOnStartupError: false\n"
        "        reconnect:\n"
        "          enabled: true\n"
        "          maxAttempts: 10\n"
    )
    if start is not None:
        # replace through the end of the indented block (until next top-level '- ')
        end = start + 1
        while end < len(lines) and (lines[end].startswith(" ") or lines[end].strip() == ""):
            end += 1
        new = lines[:start] + [block] + lines[end:]
        _private_write(path, "".join(new), keep_mode=True)
    else:
        _private_write(path, text.rstrip("\n") + "\n" + block, keep_mode=True)
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="Smart MemALL LAN join (one command)")
    ap.add_argument("--port", type=int, default=DISCOVERY_PORT)
    ap.add_argument("--timeout", type=float, default=6.0, help="beacon listen seconds")
    ap.add_argument("--agent", default=DEFAULT_AGENT)
    ap.add_argument("--device", default=socket.gethostname())
    ap.add_argument("--host", help="skip discovery, use fixed base URL")
    ap.add_argument("--token", help="skip pairing, use this token")
    ap.add_argument("--cordis", help="path to cordis.patch.yml to auto-patch")
    ap.add_argument("--out-dir", default=".", help="where to write mcp.json")
    ap.add_argument("--reset", action="store_true", help="force re-pair, ignore cache")
    ap.add_argument("--dry-run", action="store_true", help="show what would happen, write nothing")
    ap.add_argument("--info-only", action="store_true",
                    help="just print /join?format=info from the server (no config writes)")
    args = ap.parse_args()

    if args.info_only:
        # This script is one client among many; the server's /join endpoint is
        # the canonical, client-agnostic source of connection info.
        base = args.host.rstrip("/") if args.host else find_server(args.timeout, args.port)
        try:
            _, info = _get(base + "/join?format=info", timeout=5)
            print(json.dumps(info, indent=2, ensure_ascii=False))
        except Exception as e:
            print(f"  ✗ /join?format=info failed: {e}", file=sys.stderr)
            return 2
        return 0

    print("[1/3] discovering MemALL gateway ...")
    try:
        base = args.host.rstrip("/") if args.host else find_server(args.timeout, args.port)
    except Exception as e:
        print(f"  ✗ {e}", file=sys.stderr)
        return 2

    print("[2/3] resolving credential ...")
    try:
        if args.token:
            token = args.token.strip()
            print("  ✓ token supplied")
        else:
            token = resolve_token(base, args.device, args.reset)
        status, _ = _get(base + "/bus/health", token, timeout=5)
        if status != 200:
            raise RuntimeError("token rejected by /bus/health")
        print("  ✓ credential verified")
    except Exception as e:
        print(f"  ✗ {e}", file=sys.stderr)
        return 3

    print("[3/3] configuring client ...")
    mcp_text = mcp_json(base, token, args.agent)
    if args.dry_run:
        print("  (dry-run: nothing written)\n" + mcp_text)
        return 0
    out = Path(args.out_dir)
    _private_write(out / "memall-mcp.json", mcp_text)
    print(f"  ✓ {out / 'memall-mcp.json'} (0600)")
    if args.cordis:
        p = Path(args.cordis)
        if patch_cordis(p, base, token, args.agent):
            print(f"  ✓ patched {p} (idempotent)")
        else:
            print(f"  ⚠ {p} not found, skipped DSH auto-patch")
    save_cache({"base": base, "token": token, "agent": args.agent, "joined_at": int(time.time())})
    print(f"  ✓ cached at {CACHE} (0600) — next run is silent")
    print("\nDone. Point your MCP client at memall-mcp.json;")
    if args.cordis:
        print("      restart DSH to apply cordis.patch.yml.")
    print("      Rerun `python3 lan_join.py` any time — it will reconnect silently.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
