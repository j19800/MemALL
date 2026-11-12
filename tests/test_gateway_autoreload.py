"""Gateway auto-reload contract tests (dev hot-reload via watcher + execv)."""

import os
import time
from pathlib import Path

from tests.test_helpers import init_temp_db, cleanup_temp_db


def test_autoreload_env_gate():
    """MEMALL_AUTORELOAD wins over config; default off."""
    import memall.config as cfg
    from memall.gateway_autoreload import autoreload_enabled

    db, orig = init_temp_db()
    try:
        cfg.reset_config(); cfg.get_config(); cfg._last_mtime_check = float("inf")
        old = os.environ.pop("MEMALL_AUTORELOAD", None)
        try:
            assert autoreload_enabled() is False  # default off
            os.environ["MEMALL_AUTORELOAD"] = "1"
            assert autoreload_enabled() is True
            os.environ["MEMALL_AUTORELOAD"] = "0"
            assert autoreload_enabled() is False
            # config fallback honored when env unset
            del os.environ["MEMALL_AUTORELOAD"]
            cfg._config.setdefault("gateway", {})["autoreload"] = {"enabled": True}
            assert autoreload_enabled() is True
        finally:
            os.environ.pop("MEMALL_AUTORELOAD", None)
            if old is not None:
                os.environ["MEMALL_AUTORELOAD"] = old
        print("PASS: test_autoreload_env_gate")
    finally:
        cleanup_temp_db(db, orig)


def test_snapshot_and_diff(tmp_path):
    """snapshot_py tracks .py mtimes and skips junk dirs; diff reports add/change/delete."""
    from memall.gateway_autoreload import snapshot_py, diff_changes

    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "a.py").write_text("x = 1\n")
    (pkg / "b.txt").write_text("not python\n")
    junk = pkg / "__pycache__"
    junk.mkdir()
    (junk / "c.py").write_text("z = 3\n")

    s1 = snapshot_py(pkg)
    assert str(pkg / "a.py") in s1
    assert str(pkg / "b.txt") not in s1
    assert all("__pycache__" not in k for k in s1)

    time.sleep(0.01)
    (pkg / "a.py").write_text("x = 2\n")
    (pkg / "d.py").write_text("new = 1\n")
    s2 = snapshot_py(pkg)
    ch = diff_changes(s1, s2)
    assert ch.get(str(pkg / "a.py")) is not None
    assert ch.get(str(pkg / "d.py")) is not None
    assert diff_changes(s2, s2) == {}

    (pkg / "d.py").unlink()
    s3 = snapshot_py(pkg)
    ch2 = diff_changes(s3, snapshot_py(pkg))  # noop
    assert ch2 == {}
    ch3 = diff_changes(s2, s3)
    assert ch3.get(str(pkg / "d.py")) is None  # deletion signal
    print("PASS: test_snapshot_and_diff")


def test_compile_ok_guard():
    from memall.gateway_autoreload import compile_ok

    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write("def ok():\n    return 1\n")
        good = f.name
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write("def broken(:\n")
        bad = f.name
    try:
        assert compile_ok(good) is True
        assert compile_ok(bad) is False
        assert compile_ok("/nonexistent/path/x.py") is False
    finally:
        os.unlink(good)
        os.unlink(bad)
    print("PASS: test_compile_ok_guard")


def test_watch_and_restart_debounced(tmp_path):
    """Restart fires only after the change set is stable AND compilable."""
    from memall.gateway_autoreload import watch_and_restart
    import threading

    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "a.py").write_text("x = 1\n")

    fired = []
    done = threading.Event()

    def fake_restart(paths):
        fired.append(paths)
        done.set()

    t = watch_and_restart(pkg, poll_seconds=0.05, stable_seconds=0.2,
                          restart_fn=fake_restart)
    try:
        (pkg / "a.py").write_text("x = 2\n")
        assert done.wait(timeout=5), "restart should fire after settle"
        assert len(fired) == 1
        assert any(p.endswith("a.py") for p in fired[0])
    finally:
        t.join(timeout=2)


def test_watch_and_restart_skips_broken_code(tmp_path):
    """A syntax-broken change must NOT trigger restart; the next valid save does."""
    from memall.gateway_autoreload import watch_and_restart
    import threading

    pkg = tmp_path / "pkg2"
    pkg.mkdir()
    (pkg / "a.py").write_text("x = 1\n")

    fired = []
    done = threading.Event()

    def fake_restart(paths):
        fired.append(list(paths))
        done.set()

    t = watch_and_restart(pkg, poll_seconds=0.05, stable_seconds=0.2,
                          restart_fn=fake_restart)
    try:
        (pkg / "a.py").write_text("def broken(:\n")
        time.sleep(1.0)
        assert fired == [], "broken code must never restart"
        (pkg / "a.py").write_text("x = 3\n")
        assert done.wait(timeout=5), "valid save after broken one should restart"
    finally:
        t.join(timeout=2)
