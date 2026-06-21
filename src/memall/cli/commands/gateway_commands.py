"""Gateway CLI commands: gateway (start/stop/export/import/discover/pair/peers/federated)."""

import sys
import time

from memall.gateway import (
    MemAllGateway, export_bundle, import_bundle,
    start_discovery, stop_discovery, discover_peers,
    pair_with_peer, list_peers, federated_retrieve,
)


def cmd_gateway(args):
    """CLI handler for `memall gateway` — Phase 15 Device Interconnection."""
    action = getattr(args, "action", None)

    if action == "start":
        port = args.port or 9919
        gw = MemAllGateway(port=port)
        gw.start()
        print(f"Gateway started on http://127.0.0.1:{port}")

    elif action == "stop":
        gw = MemAllGateway()
        gw.stop()
        print("Gateway stopped")

    elif action == "export":
        agent = args.agent or ""
        if not agent:
            print("error: --agent is required", file=sys.stderr)
            sys.exit(1)
        bundle = export_bundle(agent, fmt=args.format or "json")
        print(f"Exported {len(bundle.get('memories', []))} memories to {bundle['file_path']}")

    elif action == "import":
        path = args.file
        if not path:
            print("error: --file is required", file=sys.stderr)
            sys.exit(1)
        result = import_bundle(path)
        print(f"Imported: {result['imported_memories']} memories, {result['imported_edges']} edges")
        print(f"Identity updated: {result['identity_updated']}")

    elif action == "discover":
        port = args.port or 9920
        # Start discovery broadcast briefly then scan
        start_discovery(port=port)
        time.sleep(0.5)
        peers = discover_peers(timeout=args.timeout or 5)
        stop_discovery()
        if not peers:
            print("No MemALL peers found on LAN.")
        else:
            print(f"Found {len(peers)} peer(s):")
            for p in peers:
                print(f"  {p['device_name']:20s} {p['address']}:{p['port']}  v{p['version']}")

    elif action == "pair":
        addr = args.address
        if not addr:
            print("error: --address IP:PORT is required", file=sys.stderr)
            sys.exit(1)
        result = pair_with_peer(addr)
        if result["paired"]:
            print(f"Paired with {result['peer_name']}")
        else:
            print(f"Pairing failed: {result.get('error', 'unknown error')}")

    elif action == "peers":
        peers = list_peers()
        if not peers:
            print("No paired peers.")
        else:
            print(f"{len(peers)} paired peer(s):")
            for p in peers:
                paired_at = p.get("paired_at", "?")[:19]
                print(f"  {p['device_name']:20s} {p['address']}:{p['port']}  since {paired_at}")

    elif action == "federated":
        query = args.query or ""
        if not query:
            print("error: --query is required", file=sys.stderr)
            sys.exit(1)
        result = federated_retrieve(query, max_peers=args.max_peers or 3)
        print(f"Local results: {len(result['local_results'])}")
        for name, items in result.get("peer_results", {}).items():
            print(f"  {name}: {len(items)} results")
        print(f"\nMerged top ({len(result['merged_top'])}):")
        for r in result["merged_top"][:10]:
            src = r.get("source", "?")
            print(f"  [{src}] {r.get('content', '')[:100]}")

    else:
        print("Usage: memall gateway {start|stop|export|import|discover|pair|peers|federated}")