"""Behavioral stage annotation for the observe→model→predict→deviate→correct loop.

Phase 1: regex-based heuristic detection in enrich_step.
Phase 2+: swap in LLM-based annotation for higher accuracy.

Each memory gets tagged with zero or more behavioral stages under
metadata → behavior → stages, plus a human-readable summary.
"""

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

Stage = Literal["observe", "model", "predict", "deviate", "correct"]

STAGE_ORDER: list[Stage] = ["observe", "model", "predict", "deviate", "correct"]

# ── Regex signals per stage ──

_OBSERVE_SIGNALS = re.compile(
    r"(?:发现|看到|注意到|收集|采集|获取|读取|查看|查询|搜索|查找|检查|观察|"
    r"monitor|watch|track|collect|gather|fetch|read|get|check|observe|scan)",
    re.I,
)

_MODEL_SIGNALS = re.compile(
    r"(?:理解|分析|推测|推断|关联|模式|规律|框架|结构|"
    r"分类|归类|映射|关系|拓扑|图谱|"
    r"model|analyze|understand|pattern|framework|structure|"
    r"schema|ontology|categorize|map|relate|infer)",
    re.I,
)

_PREDICT_SIGNALS = re.compile(
    r"(?:预测|预期|预计|趋势|走向|接下来|下一步|后续|未来|"
    r"大概率|可能|很可能|估计|大概|"
    r"predict|forecast|trend|expect|next|estimate|likely|probability)",
    re.I,
)

_DEVIATE_SIGNALS = re.compile(
    r"(?:意外|异常|偏差|不符|矛盾|冲突|断裂|缺口|"
    r"不符合|不一致|没达到|低于预期|超出范围|"
    r"unexpected|abnormal|deviation|mismatch|conflict|gap|"
    r"bug|error|fail|crash|wrong|issue|problem)",
    re.I,
)

_CORRECT_SIGNALS = re.compile(
    r"(?:纠正|调整|改进|优化|修复|重构|补充|修改|"
    r"修正|重做|更换|替换|更新|升级|降级|"
    r"fix|correct|adjust|improve|optimize|refactor|update|"
    r"modify|change|replace|upgrade|patch|tune)",
    re.I,
)

_STAGE_TO_REGEX: dict[Stage, re.Pattern] = {
    "observe": _OBSERVE_SIGNALS,
    "model": _MODEL_SIGNALS,
    "predict": _PREDICT_SIGNALS,
    "deviate": _DEVIATE_SIGNALS,
    "correct": _CORRECT_SIGNALS,
}


@dataclass
class BehavioralAnnotation:
    """Annotation output per memory."""
    stages: list[Stage] = field(default_factory=list)
    matched_signals: dict[str, list[str]] = field(default_factory=dict)
    dominant_stage: str = ""
    summary: str = ""


def annotate_text(text: str) -> BehavioralAnnotation:
    """Run regex-based behavioral stage detection on a text.

    Returns a BehavioralAnnotation with:
      - stages: all detected stages (in STAGE_ORDER)
      - matched_signals: {stage_name: [matched_keywords]}
      - dominant_stage: the stage with the most matches
      - summary: one-line description
    """
    ann = BehavioralAnnotation()
    match_counts: dict[str, int] = {}

    for stage, regex in _STAGE_TO_REGEX.items():
        matches = regex.findall(text)
        if matches:
            ann.stages.append(stage)
            ann.matched_signals[stage] = list(set(m.strip() for m in matches[:5]))
            match_counts[stage] = len(matches)

    if match_counts:
        ann.dominant_stage = max(match_counts, key=match_counts.get)
        ann.summary = f"dominant:{ann.dominant_stage}"
        if len(ann.stages) > 1:
            ordered = sorted(ann.stages, key=lambda s: STAGE_ORDER.index(s))
            ann.summary = f"dominant:{ann.dominant_stage} seq:{'→'.join(ordered)}"

    return ann


def format_for_injection(annotations: list[dict]) -> str:
    """Format behavioral annotations for [BEHAVIOR] session_start injection."""
    if not annotations:
        return ""
    stage_stats: dict[str, int] = {}
    seqs: list[str] = []
    for ann in annotations:
        stages = ann.get("stages", [])
        if stages:
            stage_stats[ann.get("dominant_stage", stages[0])] = (
                stage_stats.get(ann.get("dominant_stage", stages[0]), 0) + 1
            )
            if len(stages) > 1:
                seqs.append("→".join(stages))

    parts = []
    # Most common dominant stages
    top_stages = sorted(stage_stats, key=stage_stats.get, reverse=True)[:3]
    if top_stages:
        parts.append("stage:" + "/".join(top_stages))
    # Common sequences
    if seqs:
        top_seq = Counter(seqs).most_common(3)
        seq_str = " | ".join(f"{s}({c})" for s, c in top_seq)
        parts.append(f"seq:{seq_str}")
    return " · ".join(parts) if parts else ""


def annotate_batch(memories: list[dict]) -> list[dict]:
    """Batch annotate behavioral stages for multiple memories.

    Each memory dict must have 'id' and 'content' keys.
    Returns same list with 'behavior' key added (suitable for metadata.behavior).
    """
    results = []
    for mem in memories:
        ann = annotate_text(mem.get("content", ""))
        if ann.stages:
            results.append({
                "stages": ann.stages,
                "dominant_stage": ann.dominant_stage,
                "matched_signals": ann.matched_signals,
                "summary": ann.summary,
                "written_at": datetime.now(timezone.utc).isoformat(),
            })
        else:
            results.append(None)
    return results