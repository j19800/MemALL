#!/usr/bin/env python3
"""MemALL batch acceptance gate — one command, a pass/fail verdict.

Why this exists
---------------
Until 0.1.74 there was no single command that answered "is this tree fit to
commit/release?".  The two real gates existed but nobody ran them together:

  * ``src/check_fixes.py`` — 92 named guards, each replaying every migration.
    A full run takes ~40 minutes, so it was only ever run "when someone
    remembered".  That is exactly how the bare-``BEGIN`` defect merged.
  * ``pytest tests`` — hung outright for years-worth of runs (the embedding
    loader revalidated against HuggingFace and blocked), so the suite was
    never a gate either.

Both are now usable, so this script makes them a *batch* verdict:

    python tools/verify.py                 # full gate
    python tools/verify.py --stage fast    # guards' static checks + quick tests
    python tools/verify.py --list          # show the stages

Stages run in order and every stage records its own verdict; a failing stage
does not abort the rest (you want the whole picture, not the first error).
Exit code 0 = every stage passed, 1 = at least one failed, 2 = usage error.

Design notes
------------
  * Subprocesses, not imports: check_fixes and pytest each leave global state
    behind (module-level caches, connection pools), and this script must not
    inherit any of it.
  * ``MEMALL_DB_PATH`` is pointed at a throwaway directory so a gate run can
    never touch the operator's database.  The suites already use their own
    temp DBs, but the guards' *suite* subprocesses read the ambient env.
  * Output is captured per stage and summarised; the full log stays on disk
    for the operator, with the path printed.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
TESTS = REPO / "tests"


@dataclass
class Stage:
    name: str
    description: str
    ok: bool = True
    detail: str = ""
    duration: float = 0.0
    log: Path | None = None
    # Human-readable list of failures pulled out of the log
    failures: list[str] = field(default_factory=list)


@dataclass
class Plan:
    name: str
    stages: list[str]


PLANS: dict[str, Plan] = {
    "fast": Plan("fast", ["import", "redblue", "pytest-quick"]),
    "guards": Plan("guards", ["import", "redblue", "guards"]),
    "full": Plan("full", ["import", "redblue", "guards", "pytest"]),
}


def _log_path(stage: str) -> Path:
    root = Path(os.environ.get("MEMALL_GATE_LOG_DIR") or tempfile.gettempdir())
    return root / f"memall_gate_{stage}.log"


def _run(name: str, description: str, cmd: list[str], cwd: Path,
         env: dict, timeout: int | None = None) -> Stage:
    stage = Stage(name=name, description=description)
    log = _log_path(name)
    stage.log = log
    t0 = time.time()
    print(f"\n── {name} — {description}", flush=True)
    try:
        with open(log, "w", encoding="utf-8") as fh:
            proc = subprocess.run(cmd, cwd=str(cwd), env=env, stdout=fh,
                                  stderr=subprocess.STDOUT, timeout=timeout)
        stage.ok = proc.returncode == 0
        stage.detail = f"exit {proc.returncode}"
    except subprocess.TimeoutExpired:
        stage.ok = False
        stage.detail = f"timeout after {timeout}s"
    except FileNotFoundError as exc:
        stage.ok = False
        stage.detail = f"cannot launch: {exc}"
    stage.duration = time.time() - t0
    return stage


def _scan(stage: Stage, patterns: list[str]) -> None:
    """Pull failure lines out of a stage log."""
    if not stage.log or not stage.log.exists():
        return
    rx = re.compile("|".join(patterns))
    seen = []
    for line in stage.log.read_text(encoding="utf-8", errors="replace").splitlines():
        if rx.search(line):
            clean = line.strip()
            if clean and clean not in seen:
                seen.append(clean)
    stage.failures = seen[:20]


def stage_import(env: dict) -> Stage:
    """Every module imports, and the package reports its version."""
    code = (
        "import memall, sys; "
        "sys.path.insert(0, 'src'); "
        "from memall.mcp.adapter import handle_call; "
        "from memall.core.db import init_db; "
        "import memall.core.task_cas, memall.core.agent_names, memall.core.pins; "
        "print('version', memall.__version__)"
    )
    stage = _run("import", "package imports and version", [sys.executable, "-c", code],
                 REPO, env)
    if stage.ok and stage.log and stage.log.exists():
        for line in stage.log.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("version"):
                stage.detail = line.strip()
    return stage


def stage_redblue(env: dict) -> Stage:
    """The adversarial duels: each one is a real attack that must be repelled."""
    stage = _run("redblue", "red/blue adversarial duels",
                 [sys.executable, "-X", "utf8", str(SRC / "red_blue_checks.py")],
                 REPO, env)
    _scan(stage, [r"^\s*FAIL\s+", r"^\s*SKIP\s+"])
    if stage.ok and stage.log and stage.log.exists():
        m = re.search(r"(\d+ passed / \d+ failed / \d+ duels)",
                      stage.log.read_text(encoding="utf-8", errors="replace"))
        if m:
            stage.detail = m.group(1)
    return stage


def stage_guards(env: dict) -> Stage:
    """The full 92-guard suite (slow: every guard replays all migrations)."""
    stage = _run("guards", "check_fixes full guard suite (slow)",
                 [sys.executable, "-X", "utf8", str(SRC / "check_fixes.py")],
                 REPO, env)
    _scan(stage, [r"^\s*FAIL\s+", r"^\s*FAILED:"])
    if stage.log and stage.log.exists():
        text = stage.log.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"=== check_fixes: .* ===", text)
        if m:
            stage.detail = m.group(0).strip()
    return stage


def stage_pytest(env: dict) -> Stage:
    """The full pytest suite."""
    stage = _run("pytest", "full pytest suite",
                 [sys.executable, "-X", "utf8", "-m", "pytest", str(TESTS),
                  "-q", "--no-header", "-p", "no:cacheprovider"],
                 REPO, env)
    _scan(stage, [r"^FAILED ", r"^ERROR "])
    if stage.log and stage.log.exists():
        text = stage.log.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"(\d+ passed(?:\s*,\s*\d+ skipped)?(?:\s*,\s*\d+ failed)?)",
                      text)
        if m:
            stage.detail = m.group(1)
    return stage


def stage_pytest_quick(env: dict) -> Stage:
    """A fast subset for pre-commit: the modules this project keeps breaking."""
    quick = ["test_task_lifecycle.py", "test_session.py", "test_gateway.py",
             "test_gateway_utils.py", "test_gateway_html.py",
             "test_gateway_security_f06.py", "test_mcp_adapter.py",
             "test_hooks.py", "test_pipeline_contract.py"]
    args = [sys.executable, "-X", "utf8", "-m", "pytest"]
    args += [str(TESTS / q) for q in quick]
    args += ["-q", "--no-header", "-p", "no:cacheprovider"]
    stage = _run("pytest-quick", "fast pytest subset", args, REPO, env)
    _scan(stage, [r"^FAILED ", r"^ERROR "])
    if stage.log and stage.log.exists():
        text = stage.log.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"(\d+ passed(?:\s*,\s*\d+ failed)?)", text)
        if m:
            stage.detail = m.group(1)
    return stage


RUNNERS = {
    "import": stage_import,
    "redblue": stage_redblue,
    "guards": stage_guards,
    "pytest": stage_pytest,
    "pytest-quick": stage_pytest_quick,
}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="MemALL batch acceptance gate")
    ap.add_argument("--plan", default="full", choices=sorted(PLANS))
    ap.add_argument("--stages", nargs="*", choices=sorted(RUNNERS),
                    help="run only these stages (overrides --plan)")
    ap.add_argument("--list", action="store_true", help="show plans and stages")
    args = ap.parse_args(argv)

    if args.list:
        print("plans:")
        for name, plan in PLANS.items():
            print(f"  {name:8s} {' → '.join(plan.stages)}")
        print("\nstages:")
        for name, fn in RUNNERS.items():
            print(f"  {name:14s} {fn.__doc__.strip().splitlines()[0]}")
        return 0

    selected = args.stages or PLANS[args.plan].stages

    # A gate run must never touch the operator's database.
    sandbox = Path(tempfile.mkdtemp(prefix="memall_gate_db_"))
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC)
    env["MEMALL_DB_PATH"] = str(sandbox / "data.db")
    env.setdefault("PYTHONUTF8", "1")

    print(f"MemALL acceptance gate — plan={args.plan} stages={selected}")
    print(f"  repo     : {REPO}")
    print(f"  sandboxDB: {sandbox}")
    print(f"  head     : ", end="", flush=True)
    try:
        rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(REPO),
                             capture_output=True, text=True)
        print(rev.stdout.strip() or "(no git)")
    except Exception:
        print("(no git)")

    results: list[Stage] = []
    try:
        for name in selected:
            results.append(RUNNERS[name](env))
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)

    print("\n" + "=" * 68)
    print("ACCEPTANCE RESULT")
    print("=" * 68)
    width = max(len(s.name) for s in results)
    for s in results:
        mark = "PASS" if s.ok else "FAIL"
        print(f"  {mark}  {s.name:{width}s}  {s.duration:6.1f}s  {s.detail}")
        for f in s.failures:
            print(f"          ↳ {f}")
        if s.log and s.log.exists():
            print(f"          log: {s.log}")

    failed = [s for s in results if not s.ok]
    print("-" * 68)
    if failed:
        print(f"VERDICT: FAIL — {len(failed)}/{len(results)} stage(s) failed: "
              f"{', '.join(s.name for s in failed)}")
        print("Do not commit/release until these are green (or explained in the commit message).")
        return 1
    print(f"VERDICT: PASS — {len(results)}/{len(results)} stage(s) green")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
