"""LAN discovery, peer pairing, and federated cross-device queries.

Extracted from ``gateway.py`` (Phase 15).  Kept import-compatible by
re-exporting from ``memall.gateway``.
"""

import asyncio
import json
import logging
import socket
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from aiohttp import ClientSession, ClientTimeout

from memall.core.thin_waist import retrieve

logger = logging.getLogger("memall.gateway.peers")

_PROJECT_DIR = Path.home() / ".memall"
PEERS_FILE = _PROJECT_DIR / "peers.json"
_PEERS_LOCK = threading.Lock()

_DISCOVERY_THREAD: Optional[threading.Thread] = None
_DISCOVERY_RUNNING = threading.Event()
_DISCOVERY_LOCK = threading.Lock()
_DISCOVERY_PORT = 9920


# ── Peer registry ──

def _load_peers() -> List[Dict[str, Any]]:
    """Load paired peers from peers.json.  Returns empty list if missing."""
    with _PEERS_LOCK:
        if PEERS_FILE.exists():
            try:
                return json.loads(PEERS_FILE.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return []
        return []


def _save_peers(peers: List[Dict[str, Any]]) -> None:
    """Persist peer list to peers.json (thread-safe, atomic write)."""
    with _PEERS_LOCK:
        _PROJECT_DIR.mkdir(parents=True, exist_ok=True)
        tmp = PEERS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(peers, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(PEERS_FILE)


def start_discovery(port: int = 9920) -> None:
    """Start broadcasting MemALL discovery beacons on the LAN.

    Sends a UDP broadcast every 5 seconds.  Runs on a background
    daemon thread.  Call :func:`stop_discovery` to shut it down.

    Args:
        port: UDP port used for discovery broadcasts.
    """
    global _DISCOVERY_THREAD, _DISCOVERY_PORT
    with _DISCOVERY_LOCK:
        _DISCOVERY_PORT = port
        if _DISCOVERY_THREAD is not None and _DISCOVERY_RUNNING.is_set():
            return  # already running

        _DISCOVERY_RUNNING.set()

    def _broadcast_loop() -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        msg = json.dumps({
            "type": "memall_discovery",
            "device_name": socket.gethostname(),
            "port": port,
            "version": "1.0",
        }).encode("utf-8")
        while _DISCOVERY_RUNNING.is_set():
            try:
                sock.sendto(msg, ("255.255.255.255", port))
            except Exception:
                logger.warning("Discovery broadcast failed", exc_info=True)
            time.sleep(5)
        sock.close()

    _DISCOVERY_THREAD = threading.Thread(target=_broadcast_loop, daemon=True)
    logger.info("Discovery started on port %d", port)
    _DISCOVERY_THREAD.start()


def stop_discovery() -> None:
    """Stop the discovery broadcast thread."""
    global _DISCOVERY_THREAD
    with _DISCOVERY_LOCK:
        _DISCOVERY_RUNNING.clear()
        _DISCOVERY_THREAD = None


def discover_peers(timeout: float = 5.0) -> List[Dict[str, Any]]:
    """Listen for MemALL discovery beacons on the LAN.

    Opens a UDP socket on the discovery port and collects announcements
    for *timeout* seconds.  Returns a deduplicated list of peers.

    Args:
        timeout: How many seconds to listen (default 5).

    Returns:
        list of dicts: ``[{device_name, address, port, version}, ...]``
    """
    seen: set = set()
    peers: List[Dict[str, Any]] = []

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(timeout)
    try:
        try:
            sock.bind(("", _DISCOVERY_PORT))
        except OSError:
            # Port in use — try a random port for listening
            try:
                sock.bind(("", 0))
            except OSError:
                raise

        deadline = time.time() + timeout
        while time.time() < deadline:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            try:
                sock.settimeout(remaining)
                data, addr = sock.recvfrom(4096)
                msg = json.loads(data.decode("utf-8"))
                if msg.get("type") == "memall_discovery":
                    dev = msg.get("device_name", addr[0])
                    if dev not in seen:
                        seen.add(dev)
                        peers.append({
                            "device_name": dev,
                            "address": addr[0],
                            "port": msg.get("port", _DISCOVERY_PORT),
                            "version": msg.get("version", "1.0"),
                        })
            except (socket.timeout, json.JSONDecodeError, OSError):
                continue
    finally:
        sock.close()
    return peers


def pair_with_peer(address: str, local_token: str = "", code: str = "") -> Dict[str, Any]:
    """Send a pairing request to a remote MemALL gateway.

    The remote gateway must have its HTTP server running.  Sends
    ``POST /pair`` with the local device name and the remote's **one-time
    pairing code** (the out-of-band trust anchor printed in the remote's log /
    local UI).  On success, records the peer in ``peers.json`` along with the
    token the remote returned.

    Args:
        address: ``"IP:PORT"`` string, e.g. ``"192.168.1.5:9919"``.
        local_token: This gateway's auth token (used to authenticate
                     the remote peer's return requests).
        code: The remote gateway's one-time pairing code.  Pairing fails
              closed when it is missing or wrong.

    Returns:
        dict: {paired: bool, peer_name: str}
    """
    url = f"http://{address}/pair"
    payload = json.dumps({
        "device_name": socket.gethostname(),
        "token": local_token,
        "code": code,
    }).encode("utf-8")

    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # 403 => bad/absent pairing code; surface the remote's message.
        try:
            body = json.loads(exc.read().decode("utf-8"))
            detail = body.get("error", exc.reason)
        except Exception:
            detail = exc.reason
        return {"paired": False, "peer_name": address, "error": str(detail)}
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        return {"paired": False, "peer_name": address, "error": str(exc)}

    if not result.get("paired"):
        return {"paired": False, "peer_name": address,
                "error": result.get("error", "pairing rejected")}

    # ── Persist to peers.json ──
    peers = _load_peers()
    host = address.split(":")[0]
    port_str = address.split(":")[1] if ":" in address else "9919"
    peer_entry = {
        "device_name": result.get("peer_name", host),
        "address": host,
        "port": int(port_str),
        "token": result.get("token", ""),
        "paired_at": datetime.now(timezone.utc).isoformat(),
    }

    # Update or append
    found = False
    for p in peers:
        if p.get("address") == host and p.get("port") == int(port_str):
            p.update(peer_entry)
            found = True
            break
    if not found:
        peers.append(peer_entry)

    _save_peers(peers)

    return {"paired": True, "peer_name": peer_entry["device_name"]}


def list_peers() -> List[Dict[str, Any]]:
    """Return all currently paired peers from ``peers.json``."""
    return _load_peers()


def _remote_retrieve(peer: Dict[str, Any], query: str, timeout: float = 5.0) -> Tuple[str, List[dict]]:
    """POST /retrieve to a remote peer with Bearer auth.  Returns (peer_name, results)."""
    url = f"http://{peer['address']}:{peer['port']}/retrieve"
    payload = json.dumps({"query": query, "top_n": 10}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    peer_token = peer.get("token", "")
    if not peer_token:
        logger.warning("Federation: skipping peer %s (no auth token configured)", peer.get("address", "?"))
        return peer.get("name", "?"), []
    headers["Authorization"] = f"Bearer {peer_token}"
    req = urllib.request.Request(
        url, data=payload,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return (peer.get("device_name", peer["address"]), data.get("results", []))
    except Exception:
        return (peer.get("device_name", peer["address"]), [])


def federated_retrieve(query: str, max_peers: int = 3) -> Dict[str, Any]:
    """Query local database AND all paired peers, merge results.

    .. deprecated::
       Prefer ``federated_retrieve_async(query, max_peers)`` for better
       performance and native async I/O.  This sync wrapper is kept for
       CLI backwards compatibility.

    Local results are retrieved first, then parallel HTTP requests are
    sent to up to *max_peers* paired peers.  All results are interleaved
    (local first, then peer results deduplicated by content prefix).

    Args:
        query: Search query string.
        max_peers: Maximum number of peers to query (default 3).

    Returns:
        dict: {local_results, peer_results, merged_top}
    """
    # ── Local search ──
    local_raw = retrieve(query=query, limit=20)
    local_results = [
        {
            "id": r.id,
            "content": r.content,
            "agent_name": r.agent_name,
            "category": r.category,
            "source": "local",
        }
        for r in local_raw
    ]

    # ── Peer search (parallel threads) ──
    peers = _load_peers()[:max_peers]
    peer_results: Dict[str, list] = {}

    if peers:
        threads: List[threading.Thread] = []
        results_lock = threading.Lock()

        def _worker(p: dict) -> None:
            name, res = _remote_retrieve(p, query)
            with results_lock:
                peer_results[name] = res

        for p in peers:
            t = threading.Thread(target=_worker, args=(p,), daemon=True)
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=6)

    # ── Merge: local → top, then peer results deduped ──
    seen_content_prefix: set = set()
    merged_top: List[dict] = []

    for r in local_results:
        prefix = r["content"][:60]
        if prefix not in seen_content_prefix:
            seen_content_prefix.add(prefix)
            merged_top.append(r)

    for pname, results in peer_results.items():
        for r in results:
            r["source"] = pname
            prefix = r.get("content", "")[:60]
            if prefix not in seen_content_prefix:
                seen_content_prefix.add(prefix)
                merged_top.append(r)

    return {
        "local_results": local_results,
        "peer_results": peer_results,
        "merged_top": merged_top,
    }


async def _remote_retrieve_async(
    session: ClientSession, peer: Dict[str, Any], query: str, timeout: float = 5.0
) -> Tuple[str, List[dict]]:
    """Async POST /retrieve to a remote peer via aiohttp with Bearer auth.

    Returns (peer_name, results).
    """
    url = f"http://{peer['address']}:{peer['port']}/retrieve"
    headers = {"Content-Type": "application/json"}
    peer_token = peer.get("token", "")
    if not peer_token:
        logger.warning("Federation: skipping async peer %s (no auth token configured)", peer.get("address", "?"))
        return peer.get("device_name", peer.get("address", "?")), []
    headers["Authorization"] = f"Bearer {peer_token}"
    payload = {"query": query, "top_n": 10}
    try:
        async with session.post(url, json=payload, headers=headers,
                                timeout=ClientTimeout(total=timeout)) as resp:
            data = await resp.json()
            return (peer.get("device_name", peer["address"]), data.get("results", []))
    except Exception:
        return (peer.get("device_name", peer["address"]), [])


async def federated_retrieve_async(query: str, max_peers: int = 3) -> Dict[str, Any]:
    """Async federated query using aiohttp instead of threads.

    Local results are retrieved first, then *max_peers* peers are
    queried concurrently via ``asyncio.gather``.
    """
    # ── Local search ──
    local_raw = retrieve(query=query, limit=20)
    local_results = [
        {
            "id": r.id,
            "content": r.content,
            "agent_name": r.agent_name,
            "category": r.category,
            "source": "local",
        }
        for r in local_raw
    ]

    # ── Peer search (async concurrent) ──
    peers = _load_peers()[:max_peers]
    peer_results: Dict[str, list] = {}

    if peers:
        async with ClientSession() as session:
            tasks = [
                _remote_retrieve_async(session, p, query)
                for p in peers
            ]
            outcomes = await asyncio.gather(*tasks, return_exceptions=True)

        for p, outcome in zip(peers, outcomes):
            if isinstance(outcome, Exception):
                continue
            name, results = outcome
            peer_results[name] = results

    # ── Merge ──
    seen_content_prefix: set = set()
    merged_top: List[dict] = []

    for r in local_results:
        prefix = r["content"][:60]
        if prefix not in seen_content_prefix:
            seen_content_prefix.add(prefix)
            merged_top.append(r)

    for pname, results in peer_results.items():
        for r in results:
            r["source"] = pname
            prefix = r.get("content", "")[:60]
            if prefix not in seen_content_prefix:
                seen_content_prefix.add(prefix)
                merged_top.append(r)

    return {
        "local_results": local_results,
        "peer_results": peer_results,
        "merged_top": merged_top,
    }
