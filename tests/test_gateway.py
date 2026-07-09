"""
Test Suite — Gateway (Phase 16+)
================================
Tests Gateway start/stop, capture, health check, and export/import bundle.
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from memall.gateway import (
    MemAllGateway,
    export_bundle,
    import_bundle,
)

# High port range to avoid conflicts with previous test runs
GW_PORT_HEALTH = 19940
GW_PORT_CAPTURE = 19941


def _free_port(port: int) -> None:
    """Kill any process listening on *port* (idempotent)."""
    try:
        import subprocess
        result = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.splitlines():
            if f":{port} " in line and "LISTENING" in line:
                parts = line.strip().split()
                pid = parts[-1]
                if pid.isdigit():
                    subprocess.run(
                        ["taskkill", "/F", "/PID", pid],
                        capture_output=True, timeout=3
                    )
    except Exception:
        pass


def setup_module():
    """Clean up lingering gateway processes before running tests."""
    _free_port(GW_PORT_HEALTH)
    _free_port(GW_PORT_CAPTURE)


def _wait_for_health(host: str, port: int, timeout: float = 5) -> bool:
    """Poll /health until the gateway responds (startup wait)."""
    deadline = time.monotonic() + timeout
    url = f"http://{host}:{port}/health"
    while time.monotonic() < deadline:
        try:
            req = urllib.request.Request(url)
            resp = urllib.request.urlopen(req, timeout=1)
            data = json.loads(resp.read().decode())
            if data.get("status") == "ok":
                return True
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError):
            pass
        time.sleep(0.1)
    return False


def _wait_for_stop(host: str, port: int, timeout: float = 5) -> bool:
    """Poll until the gateway stops responding (stop wait)."""
    deadline = time.monotonic() + timeout
    url = f"http://{host}:{port}/health"
    while time.monotonic() < deadline:
        try:
            req = urllib.request.Request(url)
            urllib.request.urlopen(req, timeout=1)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError):
            return True
        time.sleep(0.1)
    return False


def test_gateway_start_stop():
    """Test: start gateway, hit /health, stop gateway."""
    gw = MemAllGateway(host="127.0.0.1", port=GW_PORT_HEALTH)
    gw.start()
    assert _wait_for_health("127.0.0.1", GW_PORT_HEALTH), "Gateway did not start in time"

    try:
        req = urllib.request.Request(f"http://127.0.0.1:{GW_PORT_HEALTH}/health")
        resp = urllib.request.urlopen(req, timeout=3)
        data = json.loads(resp.read().decode())
        assert data["status"] == "ok", f"Expected ok, got {data.get('status')}"
        assert "uptime" in data, "Missing uptime"
        assert "memory_count" in data, "Missing memory_count"
        print(f"  PASS test_gateway_start_stop — health: {data}")
    finally:
        gw.stop()
        assert _wait_for_stop("127.0.0.1", GW_PORT_HEALTH), "Gateway did not stop in time"


def test_gateway_capture():
    """Test: POST /capture through gateway."""
    gw = MemAllGateway(host="127.0.0.1", port=GW_PORT_CAPTURE)
    gw.start()
    assert _wait_for_health("127.0.0.1", GW_PORT_CAPTURE), "Gateway did not start in time"

    try:
        import urllib.request

        payload = json.dumps({
            "agent_name": "gateway_test",
            "content": "Gateway capture test memory",
            "category": "test",
        }).encode()

        req = urllib.request.Request(
            f"http://127.0.0.1:{GW_PORT_CAPTURE}/capture",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {gw._auth_token}",
            },
        )
        resp = urllib.request.urlopen(req, timeout=3)
        data = json.loads(resp.read().decode())
        assert "id" in data or "success" in data, (
            f"No success indicator in: {data}"
        )
        print(f"  PASS test_gateway_capture — {data}")
    finally:
        gw.stop()
        assert _wait_for_stop("127.0.0.1", GW_PORT_CAPTURE), "Gateway did not stop in time"


def test_export_import_bundle():
    """Test: export bundle, then import it (idempotent)."""
    agent_name = "bundle_test_agent"
    # export_bundle returns dict with "file_path" key
    bundle = export_bundle(agent_name)
    assert isinstance(bundle, dict), f"export_bundle returned {type(bundle)}"
    assert "file_path" in bundle, f"No file_path in bundle: {list(bundle.keys())}"

    bundle_path = bundle["file_path"]
    assert os.path.exists(bundle_path), f"Bundle not written: {bundle_path}"
    assert "memories" in bundle, f"No memories in bundle: {list(bundle.keys())}"

    # Import the bundle back (should be idempotent/no duplicates)
    result = import_bundle(bundle_path)
    assert isinstance(result, dict), f"import_bundle returned non-dict: {type(result)}"
    assert "imported_memories" in result, f"Missing imported_memories: {list(result.keys())}"

    print(
        f"  PASS test_export_import_bundle — "
        f"imported {result['imported_memories']} memories, "
        f"{result['imported_edges']} edges"
    )


if __name__ == "__main__":
    print("=" * 60)
    print("MemALL Gateway Tests")
    print("=" * 60)
    passed = 0
    failed = 0

    tests = [
        ("test_gateway_start_stop", test_gateway_start_stop),
        ("test_gateway_capture", test_gateway_capture),
        ("test_export_import_bundle", test_export_import_bundle),
    ]

    for name, func in tests:
        try:
            func()
            passed += 1
        except Exception as e:
            print(f"  FAIL {name}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\nResults: {passed} passed, {failed} failed")
    if failed:
        sys.exit(1)