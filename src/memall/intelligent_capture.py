"""
智能捕获 — 自动分析内容并生成结构化记忆。

无需手动指定 level/category/subject，系统自动识别意图并归类。
"""

import re
import logging
from typing import Optional

from memall.core.thin_waist import capture as _capture, MemoryInput
from memall.core.entity_extractor import extract_entities

logger = logging.getLogger(__name__)

# ── 意图模式 ──────────────────────────────────────────────

# 决策 (L4)
_DECISION_PATTERNS = [
    r"(?:决定|选择|采用|改用|替换|迁移)\s*(?:用|了)?\s*\S+",
    r"(?:选型|技术选型|方案选择|架构决策)",
    r"(?:比较|对比|权衡|考虑)\s*(?:了|过)?\s*(?:后|之后)?\s*(?:决定|选择|采用)",
    r"(?:决定|结论|最终方案|确认)",
    r"(?:we|我们|I|我)\s+(?:decided|chose|selected|picked|chosen)\s+",
]

# 问题/教训 (L6)
_LESSON_PATTERNS = [
    r"(?:坑|踩坑|教训|根因|root.cause|lesson.learned)",
    r"(?:导致|引起|引发|造成)\s*(?:了)?\s*(?:问题|故障|崩溃|错误|失败)",
    r"(?:不应该|不对|本应|本不该|错误|失误)",
    r"(?:修复|修正|纠正|解决)\s*(?:了)?\s*(?:一个|这个)?\s*(?:bug|问题|故障)",
    r"(?:反思|回顾|retrospective|复盘)",
    r"(?:警告|注意|小心|避免)",
]

# 偏好 (L7)
_PREFERENCE_PATTERNS = [
    r"(?:我喜欢|我偏好|prefer|偏好|倾向于|常用|习惯用|主要用)",
    r"(?:我觉得更好|更高效|更喜欢)",
    r"(?:我不喜欢|我排斥|避免|我不用|我讨厌|不建议)",
    r"(?:推荐|建议用|更推荐|优先选择)",
]

# 计划/任务 (L5)
_PLAN_PATTERNS = [
    r"(?:计划|规划|路线图|roadmap|目标|goal|milestone)",
    r"(?:待办|todo|task|任务|next.step|下一步)",
    r"(?:迭代|sprint|ETA|截止日期|deadline|预计|安排)",
    r"(?:需要做|需要完成|要做的|待完成)",
]

# 事实/事件 (P2)
_FACT_PATTERNS = [
    r"(?:今天|昨天|上周|这周|下周|\d{4}-\d{2}-\d{2})\s*(?:做了|完成|搞定了|处理了|修复了|上架了|部署了|上线了|发布了|写了|学了|看了|去了|参加了|开会|讨论了)",
    r"(?:上线|部署|发布|merged|deployed|completed|finished)",
    r"(?:会议|讨论|对齐|同步|评审|meeting)",
    r"(?:学习了|知道了|了解到|发现)",
]

# 身份 (L1)
_IDENTITY_PATTERNS = [
    r"(?:我叫|我是|本人|name|email|phone|contact)",
    r"(?:我从事|我担任|我的角色|我的职位|我的职业)",
    r"(?:我擅长|我精通|我的能力|我的技能|我会|我能|熟悉)",
]

# 知识 (L9/L10)
_KNOWLEDGE_PATTERNS = [
    r"(?:知识|概念|原理|定义|what.is)",
    r"(?:教程|指南|guide|tutorial|how.to)",
    r"(?:架构|设计|模式|原则|pattern|principle)",
]


def detect_intent(content: str) -> str:
    """检测内容意图类型。

    Returns:
        intent: decision | lesson | preference | plan | fact | identity | knowledge | general
    """
    for patterns, intent in [
        (_PLAN_PATTERNS, "plan"),           # 计划优先 (需要做 > 决定)
        (_DECISION_PATTERNS, "decision"),
        (_LESSON_PATTERNS, "lesson"),
        (_PREFERENCE_PATTERNS, "preference"),
        (_IDENTITY_PATTERNS, "identity"),
        (_KNOWLEDGE_PATTERNS, "knowledge"),
        (_FACT_PATTERNS, "fact"),           # 事实放在最后，避免"今天天气不错"误匹配
    ]:
        for pat in patterns:
            if re.search(pat, content, re.IGNORECASE):
                return intent
    return "general"


_INTENT_LEVEL = {
    "decision": "L4",
    "lesson": "L6",
    "preference": "L7",
    "plan": "L5",
    "identity": "L1",
    "knowledge": "L9",
    "fact": "P2",
    "general": "P2",
}

_INTENT_CATEGORY = {
    "decision": "decision",
    "lesson": "reflection",
    "preference": "preference",
    "plan": "planning",
    "identity": "identity",
    "knowledge": "knowledge",
    "fact": "general",
    "general": "general",
}


def _generate_subject(content: str, intent: str) -> str:
    """从内容中自动生成 subject。"""
    # 取第一句有意义的内容
    for sep in ["\n", "。", ". ", "！", "？", "!", "?"]:
        if sep in content:
            first = content.split(sep)[0].strip()
            if len(first) > 10:
                break
    else:
        first = content[:80]

    # 去掉开头填充词
    fillers = ["好的，", "明白了，", "嗯，", "那个，", "所以，", "然后，", "对了，"]
    for f in fillers:
        if first.startswith(f):
            first = first[len(f):]
            break

    # 添加意图前缀
    prefix = {
        "decision": "决策: ",
        "lesson": "教训: ",
        "preference": "偏好: ",
        "plan": "计划: ",
        "identity": "身份: ",
        "knowledge": "知识: ",
        "fact": "",
        "general": "",
    }.get(intent, "")

    return (prefix + first)[:200]


def score_importance(content: str, intent: str, entities: list) -> float:
    """评估内容重要性 (0-1)。"""
    score = 0.5  # baseline

    # 意图权重
    intent_weight = {
        "decision": 0.8,
        "lesson": 0.9,
        "preference": 0.6,
        "plan": 0.7,
        "identity": 0.7,
        "knowledge": 0.8,
        "fact": 0.4,
        "general": 0.3,
    }
    score += intent_weight.get(intent, 0.3) * 0.3

    # 长度因子
    length = len(content)
    if length > 200:
        score += 0.1
    elif length > 500:
        score += 0.2

    # 实体因子
    if len(entities) >= 3:
        score += 0.1

    # 代码/技术引用
    if re.search(r'[A-Z][A-Za-z]+\(|[A-Z]{2,}|@\w+', content):
        score += 0.1

    return min(1.0, max(0.0, score))


def intelligent_capture(content: str, agent_name: str = "system", **overrides) -> int:
    """智能捕获 — 自动分析内容并生成结构化记忆。

    Args:
        content: 记忆内容
        agent_name: Agent 名称
        **overrides: 覆盖自动识别的字段 (level, category, subject 等)

    Returns:
        记忆 ID
    """
    # 0. 长度检查
    if len(content.strip()) < 15:
        logger.debug("intelligent_capture: content too short (%d chars), skipping", len(content.strip()))
        return 0

    # 1. 意图识别
    intent = detect_intent(content)

    # 2. 实体提取
    entities = extract_entities(content)

    # 3. 重要性评分
    importance = score_importance(content, intent, entities)

    # 4. 自动分类 (允许覆盖, level 经白名单消毒)
    from memall.core.thin_waist import _sanitize_level
    level = _sanitize_level(overrides.pop("level", None) or _INTENT_LEVEL.get(intent, "P2"))
    category = overrides.pop("category", None) or _INTENT_CATEGORY.get(intent, "general")
    subject = overrides.pop("subject", None) or _generate_subject(content, intent)

    # 5. 构建结构化输入
    data = MemoryInput(
        content=content,
        agent_name=agent_name,
        level=level,
        category=category,
        subject=subject,
        **overrides,
    )

    # 6. 存入
    try:
        mem_id = _capture(data)
        logger.info(
            "intelligent_capture: intent=%s level=%s category=%s importance=%.2f id=%d",
            intent, level, category, importance, mem_id if mem_id else 0,
        )
        return mem_id
    except ValueError as e:
        # 质量门控失败时降级重试: 用 P2 级别再试一次
        logger.debug("intelligent_capture: quality gate failed (%s), retrying as P2", e)
        data.level = "P2"
        return _capture(data)