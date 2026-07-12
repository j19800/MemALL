"""
Test Suite — Scheduler Plugin
===============================
Tests TaskScheduler, add_task, remove_task, daily tasks.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_scheduler_add_task():
    """add_task should register a task and return True."""
    from memall.plugins.scheduler import TaskScheduler

    sched = TaskScheduler()
    result = sched.add_task("test_task", lambda: None, 60)
    assert result is True, f"Expected True, got {result}"
    tasks = sched.list_tasks()
    names = [t["name"] for t in tasks]
    assert "test_task" in names, f"Expected test_task in {names}"
    print("  PASS test_scheduler_add_task")


def test_scheduler_add_duplicate():
    """Adding the same task name twice should return False."""
    from memall.plugins.scheduler import TaskScheduler

    sched = TaskScheduler()
    sched.add_task("dup_task", lambda: None, 60)
    result = sched.add_task("dup_task", lambda: None, 60)
    assert result is False, f"Expected False for duplicate, got {result}"
    print("  PASS test_scheduler_add_duplicate")


def test_scheduler_remove_task():
    """remove_task should remove a registered task."""
    from memall.plugins.scheduler import TaskScheduler

    sched = TaskScheduler()
    sched.add_task("removable", lambda: None, 60)
    result = sched.remove_task("removable")
    assert result is True, f"Expected True, got {result}"
    tasks = sched.list_tasks()
    assert "removable" not in [t["name"] for t in tasks]
    print("  PASS test_scheduler_remove_task")


def test_scheduler_remove_nonexistent():
    """remove_task on nonexistent task should return False."""
    from memall.plugins.scheduler import TaskScheduler

    sched = TaskScheduler()
    result = sched.remove_task("nonexistent")
    assert result is False, f"Expected False, got {result}"
    print("  PASS test_scheduler_remove_nonexistent")


def test_scheduler_list_tasks():
    """list_tasks should return task metadata."""
    from memall.plugins.scheduler import TaskScheduler

    sched = TaskScheduler()
    sched.add_task("task_a", lambda: None, 30)
    sched.add_task("task_b", lambda: None, 60)
    tasks = sched.list_tasks()
    assert len(tasks) == 2, f"Expected 2 tasks, got {len(tasks)}"
    for t in tasks:
        assert "name" in t
        assert "interval_seconds" in t
        assert "run_count" in t
        assert "error_count" in t
    print("  PASS test_scheduler_list_tasks")


def test_scheduler_run_task_tracks_count():
    """_run_task should increment run_count on success."""
    from memall.plugins.scheduler import TaskScheduler

    counter = [0]
    def inc():
        counter[0] += 1

    sched = TaskScheduler()
    sched.add_task("counter", inc, 60)
    sched._run_task("counter")
    assert counter[0] == 1, f"Expected 1, got {counter[0]}"
    tasks = sched.list_tasks()
    for t in tasks:
        if t["name"] == "counter":
            assert t["run_count"] == 1, f"Expected run_count=1, got {t['run_count']}"
    print("  PASS test_scheduler_run_task_tracks_count")


def test_scheduler_run_task_tracks_error():
    """_run_task should increment error_count on failure."""
    from memall.plugins.scheduler import TaskScheduler

    def fail():
        raise ValueError("task failed")

    sched = TaskScheduler()
    sched.add_task("failing", fail, 60)
    sched._run_task("failing")
    tasks = sched.list_tasks()
    for t in tasks:
        if t["name"] == "failing":
            assert t["error_count"] == 1, f"Expected error_count=1, got {t['error_count']}"
    print("  PASS test_scheduler_run_task_tracks_error")


def test_create_default_scheduler():
    """create_default_scheduler should register built-in tasks."""
    from memall.plugins.scheduler import create_default_scheduler

    sched = create_default_scheduler()
    tasks = sched.list_tasks()
    names = [t["name"] for t in tasks]
    assert "daily_forget" in names, f"Expected daily_forget in {names}"
    assert "daily_security" in names
    assert "daily_lifecycle" in names
    print("  PASS test_create_default_scheduler")


if __name__ == "__main__":
    print("=" * 60)
    print("Scheduler Tests")
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