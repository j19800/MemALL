import json
import time as _time
from memall.gateway import (
    MemAllGateway, export_bundle, import_bundle,
    start_discovery, stop_discovery, discover_peers,
    pair_with_peer, list_peers, federated_retrieve,
)

# Module-level reference to the active gateway instance
_active_gateway: MemAllGateway | None = None


def handle(arguments: dict) -> str:
    global _active_gateway
    # Canonical field is ``gateway_sub_action``; ``sub_action`` stays as a
    # deprecated alias.  ``action`` is already rewritten by the dispatcher.
    action = (
        arguments.get("action")
        or arguments.get("gateway_sub_action")
        or arguments.get("sub_action")
        or ""
    )
    if not action:
        return json.dumps({
            "error": "gateway_sub_action is required "
                     "(status|start|stop|export|import|discover|pair|peers|federated)"
        })

    if action == "status":
        # Read-only view of the in-process gateway: whether one is running,
        # which port, and how many peers are known.  Useful before start/stop
        # so callers don't have to guess.
        if _active_gateway is None:
            return json.dumps({
                "status": "stopped",
                "running": False,
                "peers": 0,
                "message": "No gateway is running in this process.",
            })
        try:
            peers = list_peers() or []
        except Exception:
            peers = []
        return json.dumps({
            "status": "running",
            "running": True,
            "port": getattr(_active_gateway, "port", None),
            "host": getattr(_active_gateway, "host", "127.0.0.1"),
            "peers": len(peers),
            "peer_list": peers,
        }, default=str)

    if action == "start":
        port = arguments.get("port", 9919)
        gw = MemAllGateway(port=port)
        gw.start()
        _active_gateway = gw
        return json.dumps({"status": "ok", "host": "127.0.0.1", "port": port})

    elif action == "stop":
        if _active_gateway is None:
            gw = MemAllGateway(port=arguments.get("port", 9919))
            gw.stop()
            return json.dumps({"status": "ok", "message": "gateway stopped (cleanup)"})
        _active_gateway.stop()
        _active_gateway = None
        return json.dumps({"status": "ok", "message": "gateway stopped"})

    elif action == "export":
        agent = arguments.get("agent_name", "")
        if not agent:
            return json.dumps({"error": "agent_name is required"})
        bundle = export_bundle(agent)
        return json.dumps({
            "memories": len(bundle.get("memories", [])),
            "file_path": bundle.get("file_path", ""),
        })

    elif action == "import":
        path = arguments.get("file_path", "")
        if not path:
            return json.dumps({"error": "file_path is required"})
        result = import_bundle(path)
        return json.dumps(result)

    elif action == "discover":
        port = arguments.get("port") or 9920
        start_discovery(port=port)
        # Startup handshake needs a beat, but 0.5s inside an MCP call blocks
        # the whole event loop; the discovery socket is ready almost instantly,
        # so poll briefly for first response instead of a fixed sleep.
        deadline = _time.monotonic() + 0.5
        while _time.monotonic() < deadline:
            _time.sleep(0.05)
            if _active_gateway is not None:
                break
        peers = discover_peers(timeout=5)
        stop_discovery()
        return json.dumps(peers, default=str)

    elif action == "pair":
        addr = arguments.get("address", "")
        if not addr:
            return json.dumps({"error": "address is required"})
        result = pair_with_peer(addr)
        return json.dumps(result)

    elif action == "peers":
        peers = list_peers()
        return json.dumps(peers, default=str)

    elif action == "federated":
        query = arguments.get("query", "")
        if not query:
            return json.dumps({"error": "query is required"})
        max_peers = arguments.get("max_peers", 3)
        result = federated_retrieve(query, max_peers=max_peers)
        return json.dumps(result, default=str, ensure_ascii=False)

    else:
        return json.dumps({
            "error": f"unknown gateway_sub_action: {action}",
            "valid": ["status", "start", "stop", "export", "import",
                      "discover", "pair", "peers", "federated"],
        })
