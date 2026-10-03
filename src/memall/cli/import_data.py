"""
MemALL 记忆导入工具 — 从其他系统迁移数据

支持格式:
  - JSONL:  每行一个 JSON 对象 (MemALL 原生格式)
  - CSV:    id,content,level,agent_name,category,...
  - JSON:   JSON 数组 [{...}, ...]
  - Mem0:   Mem0 导出格式自动检测

用法:
  python -m memall.cli.import_data --format jsonl --file export.jsonl
  python -m memall.cli.import_data --format csv --file memories.csv
"""

import json, csv, sys, os, logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

logger = logging.getLogger("memall.import")

LEVEL_MAP = {
    # canonical enum (memall.core.levels) has no P3/P4 — clamp low-importance
    # external values to P2 so imports never hit the memories.level CHECK.
    "P0": "P0", "P1": "P1", "P2": "P2", "P3": "P2", "P4": "P2",
    "L1": "L1", "L2": "L2", "L3": "L3", "L4": "L4", "L5": "L5",
    "L6": "L6", "L7": "L7", "L8": "L8", "L9": "L9", "L10": "L10", "L11": "L11",
    "observation": "P2", "human": "P2", "system": "P2",
    "memory": "P2", "chat": "P2", "message": "P2",
    "session": "L4", "decision": "L4",
    "reflection": "L6", "lesson": "L7",
    "summary": "L9", "distilled": "L9",
}

# Case-insensitive lookup built from LEVEL_MAP (numeric keys uppercase,
# word aliases lowercase — see _normalize_level).
_LEVEL_LOOKUP = {k.lower(): v for k, v in LEVEL_MAP.items()}


def _normalize_level(level: Any) -> str:
    """Map external level names to MemALL levels (case-insensitive).

    Numeric keys in ``LEVEL_MAP`` are uppercase (``P0``..``L11``) while the word
    aliases are lowercase (``session``, ``decision``, ...).  Compare
    case-insensitively so both families resolve — previously the input was
    upper-cased and the word aliases never matched, silently falling back to P2.
    """
    if not level:
        return "P2"
    key = str(level).strip().lower()
    if key in _LEVEL_LOOKUP:
        return _LEVEL_LOOKUP[key]
    # Try partial match
    for k, v in _LEVEL_LOOKUP.items():
        if k in key or key in k:
            return v
    return "P2"


def _normalize_row(row: dict) -> dict:
    """Normalize a single memory row to MemALL format."""
    content = row.get("content") or row.get("text") or row.get("message") or ""
    if not content:
        return None

    return {
        "content": content[:10000],
        "level": _normalize_level(row.get("level") or row.get("type") or row.get("memory_type", "P2")),
        "agent_name": (row.get("agent_name") or row.get("agent") or row.get("user") or "imported")[:200],
        "category": (row.get("category") or row.get("memory_type") or row.get("type") or "general")[:100],
        "subject": (row.get("subject") or row.get("title") or "")[:500],
        "project": (row.get("project") or "")[:500],
        "summary": (row.get("summary") or "")[:2000],
        "metadata": {},
    }


def import_jsonl(filepath: str, limit: int = 0) -> dict:
    """Import from JSONL file (one JSON object per line)."""
    counts = {"total": 0, "imported": 0, "skipped": 0, "errors": 0}
    from memall.core.thin_waist import capture, MemoryInput

    with open(filepath, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            if limit and counts["total"] >= limit:
                break
            line = line.strip()
            if not line:
                continue
            counts["total"] += 1
            try:
                row = json.loads(line)
                normalized = _normalize_row(row)
                if not normalized:
                    counts["skipped"] += 1
                    continue
                capture(MemoryInput(**normalized))
                counts["imported"] += 1
            except Exception as e:
                counts["errors"] += 1
                logger.debug("line %d: %s", line_num, e)

            if counts["total"] % 100 == 0:
                print(f"  ... {counts['total']} lines processed, {counts['imported']} imported")

    return counts


def import_csv(filepath: str, limit: int = 0) -> dict:
    """Import from CSV file."""
    counts = {"total": 0, "imported": 0, "skipped": 0, "errors": 0}
    from memall.core.thin_waist import capture, MemoryInput

    with open(filepath, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if limit and counts["total"] >= limit:
                break
            counts["total"] += 1
            try:
                normalized = _normalize_row(row)
                if not normalized:
                    counts["skipped"] += 1
                    continue
                capture(MemoryInput(**normalized))
                counts["imported"] += 1
            except Exception as e:
                counts["errors"] += 1
                logger.debug("row %d: %s", counts["total"], e)

    return counts


def import_json(filepath: str, limit: int = 0) -> dict:
    """Import from JSON array file."""
    counts = {"total": 0, "imported": 0, "skipped": 0, "errors": 0}
    from memall.core.thin_waist import capture, MemoryInput

    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict):
        data = data.get("data") or data.get("memories") or data.get("results") or []

    for row in data:
        if limit and counts["total"] >= limit:
            break
        counts["total"] += 1
        try:
            normalized = _normalize_row(row)
            if not normalized:
                counts["skipped"] += 1
                continue
            capture(MemoryInput(**normalized))
            counts["imported"] += 1
        except Exception as e:
            counts["errors"] += 1
            logger.debug("row %d: %s", counts["total"], e)

    return counts


def import_mem0(filepath: str, limit: int = 0) -> dict:
    """Import from Mem0 export format."""
    counts = {"total": 0, "imported": 0, "skipped": 0, "errors": 0}
    from memall.core.thin_waist import capture, MemoryInput

    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Mem0 format: { "results": [...], "user_id": "...", "type": "user" }
    memories = []
    if isinstance(data, list):
        memories = data
    elif isinstance(data, dict):
        memories = data.get("results") or data.get("memories") or data.get("data", [])

    for row in memories:
        if limit and counts["total"] >= limit:
            break
        counts["total"] += 1
        try:
            content = row.get("content") or row.get("memory") or row.get("text") or ""
            if not content:
                counts["skipped"] += 1
                continue

            meta = row.get("metadata", {})
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except Exception:
                    meta = {}

            normalized = {
                "content": content[:10000],
                "level": _normalize_level(row.get("level") or meta.get("level", "P2")),
                "agent_name": (row.get("agent_name") or row.get("user_id") or "mem0_imported")[:200],
                "category": (row.get("category") or meta.get("category") or "general")[:100],
                "subject": (row.get("subject") or row.get("title") or "")[:500],
                "project": (row.get("project") or meta.get("project") or "")[:500],
                "summary": (row.get("summary") or "")[:2000],
            }
            capture(MemoryInput(**normalized))
            counts["imported"] += 1
        except Exception as e:
            counts["errors"] += 1
            logger.debug("import error: %s", e)

    return counts


def main():
    import argparse

    parser = argparse.ArgumentParser(description="MemALL 记忆导入工具")
    parser.add_argument("--format", choices=["jsonl", "csv", "json", "mem0"], required=True, help="输入格式")
    parser.add_argument("--file", required=True, help="输入文件路径")
    parser.add_argument("--limit", type=int, default=0, help="最大导入条数 (0 = 不限)")
    args = parser.parse_args()

    FORMATS = {
        "jsonl": import_jsonl,
        "csv": import_csv,
        "json": import_json,
        "mem0": import_mem0,
    }

    print(f"导入 {args.file} ({args.format} 格式)...")
    counts = FORMATS[args.format](args.file, args.limit)
    print(f"完成: {counts['imported']} 条导入, {counts['skipped']} 条跳过, {counts['errors']} 条错误")


if __name__ == "__main__":
    main()