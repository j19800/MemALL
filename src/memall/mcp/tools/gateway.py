import json
import time as _time
from memall.gateway import (
    MemAllGateway, export_bundle, import_bundle,
    start_discovery, stop_discovery, discover_peers,
    pair_with_peer, list_peers, federated_retrieve,
)


def handle(arguments: dict) -> str:
    action = arguments["action"]

    if action == "start":
        port = arguments.get("port", 9919)
        gw = MemAllGateway(port=port)
        gw.start()
        return json.dumps({"status": "ok", "host": "127.0.0.1", "port": port})

    elif action == "stop":
        port = arguments.get("port", 9919)
        gw = MemAllGateway(port=port)
        gw.stop()
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
        _time.sleep(0.5)
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
        return json.dumps({"error": f"unknown action: {action}"})
