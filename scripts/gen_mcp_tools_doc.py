"""MemALL MCP tool reference generator.

Scans ``memall.mcp.adapter.TOOL_DEFINITIONS`` and renders one markdown
table per tool to ``docs/mcp_tools_reference.md``.

Run:
    python scripts/gen_mcp_tools_doc.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

# Point the adapter at the live DB so it can load without side effects.
os.environ.setdefault("MEMALL_DB_PATH",
                      os.path.expanduser("~/.memall/data.db"))
os.environ.setdefault("MEMALL_HOME",
                      os.path.expanduser("~/.memall"))
os.environ.setdefault("MEMALL_PROJECT_DIR", str(Path(__file__).resolve().parent.parent))


def _first_line(text: str) -> str:
    return (text or "").strip().splitlines()[0] if (text or "").strip() else ""


def render(defs: list[dict]) -> str:
    import memall.mcp.adapter as adapter

    adapter._ensure_loaded()
    defs = sorted(defs, key=lambda d: d.get("name", ""))
    lines = [
        "# MemALL MCP 工具参考（自动生成）",
        "",
        f"> 本文件由 `scripts/gen_mcp_tools_doc.py` 从 `memall.mcp.adapter` 的 "
        f"TOOL_DEFINITIONS 生成，共 **{len(defs)}** 个工具。",
        "> **请勿手改本文件**——重新运行生成器即可。",
        "",
    ]
    for d in defs:
        name = d.get("name", "?")
        desc = _first_line(d.get("description"))
        sch = d.get("input_schema") or d.get("inputSchema") or {}
        props = sch.get("properties") or {}
        sch_required = set(sch.get("required") or [])
        lines += [f"## `{name}`", "", desc, ""]
        if not props:
            lines += ["_(no parameters)_", ""]
            continue
        lines += ["### Parameters", "",
                  "| name | type | required | description |",
                  "|---|---|---|---|"]
        for pname, pdef in props.items():
            ptype = pdef.get("type", "")
            if isinstance(ptype, list):
                ptype = "/".join(str(t) for t in ptype)
            enum = pdef.get("enum")
            if enum:
                enum_txt = " | ".join(map(str, enum))
                pdesc = f"`{enum_txt}`" + (" — " + pdef.get("description", "")
                                           if pdef.get("description") else "")
            else:
                pdesc = (pdef.get("description") or "").replace("\n", " ")
            req = "yes" if pname in sch_required else ""
            lines.append(f"| `{pname}` | {ptype} | {req} | {pdesc} |")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    import memall.mcp.adapter as adapter

    adapter._ensure_loaded()
    from memall.mcp.adapter import TOOL_DEFINITIONS

    out = render(TOOL_DEFINITIONS)
    target = Path(__file__).resolve().parent.parent / "docs" / "mcp_tools_reference.md"
    target.write_text(out, encoding="utf-8")
    n_params = sum(1 for l in out.splitlines() if l.startswith("| `"))
    print(f"wrote {target} ({len(out)} chars, {len(TOOL_DEFINITIONS)} tools, "
          f"{n_params} parameters)")


if __name__ == "__main__":
    main()
