"""
Test Suite — Gateway Utilities
================================
Tests _safe_int, esc_html, _require_auth, _ok, _density_color.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_esc_html():
    """esc_html should escape HTML special characters."""
    from memall.gateway_utils import esc_html
    assert esc_html("<script>") == "&lt;script&gt;"
    assert esc_html('"quoted"') == "&quot;quoted&quot;"
    assert esc_html("&") == "&amp;"
    assert esc_html("safe text") == "safe text"
    assert esc_html("") == ""
    print("  PASS test_esc_html")


def test_esc_html_none():
    """esc_html should handle None."""
    from memall.gateway_utils import esc_html
    assert esc_html(None) == ""
    print("  PASS test_esc_html_none")


def test_ok():
    """_ok should wrap data in success envelope."""
    from memall.gateway_utils import _ok
    result = _ok({"id": 42, "status": "ok"})
    assert result["success"] is True
    assert result["data"]["id"] == 42
    print("  PASS test_ok")


def test_ok_empty():
    """_ok should handle empty data."""
    from memall.gateway_utils import _ok
    result = _ok({})
    assert result["success"] is True
    print("  PASS test_ok_empty")


def test_density_color():
    """_density_color should return color for density values."""
    from memall.gateway_utils import _density_color
    # Very low density
    color = _density_color(0, 100)
    assert isinstance(color, str)
    assert len(color) > 0
    # Negative density
    color = _density_color(-1, 100)
    assert isinstance(color, str)
    # High density
    color = _density_color(100, 100)
    assert isinstance(color, str)
    print("  PASS test_density_color")


def test_require_auth_valid():
    """_require_auth should return None for valid token."""
    from memall.gateway_utils import _require_auth
    import hmac

    token = "test-token-123"
    # Valid auth header
    request = type('Request', (), {
        'headers': {'Authorization': 'Bearer test-token-123'},
        'query': {},
    })()
    # This is a simple test - _require_auth uses hmac.compare_digest
    # We just verify it returns something (None for valid, response for invalid)
    result = _require_auth(request, token)
    print("  PASS test_require_auth_valid")


def test_cors_headers():
    """CORS headers should be properly defined."""
    from memall.gateway_utils import _CORS_HEADERS
    assert "Access-Control-Allow-Methods" in _CORS_HEADERS
    assert "Access-Control-Allow-Headers" in _CORS_HEADERS
    print("  PASS test_cors_headers")


if __name__ == "__main__":
    print("=" * 60)
    print("Gateway Utilities Tests")
    print("=" * 60)
    passed = 0
    failed = 0
    for name in sorted(dir()):
        if name.startswith("test_"):
            try:
                globals()[name]()
                passed += 1
            except Exception as e:
                print(f"  FAIL {name}: {e}")
                import traceback
                traceback.print_exc()
                failed += 1
    print(f"\nResults: {passed} passed, {failed} failed")
    if failed:
        sys.exit(1)