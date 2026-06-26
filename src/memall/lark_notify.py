"""Lark IM notifications for discussion lifecycle.

Pushes card messages to MemALL Bot Chat when discussions are created,
responded to, or converged.  All functions are best-effort (no raise).
"""

import json
import logging
import subprocess
import os

logger = logging.getLogger(__name__)

_CHAT_ID = os.environ.get("MEMALL_CHAT_ID", "")


def _lark(args: list[str]) -> dict:
    """Run lark-cli and return parsed JSON result."""
    try:
        result = subprocess.run(
            ["lark-cli"] + args,
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace",
        )
        if result.returncode != 0:
            logger.warning(f"lark-cli error ({result.returncode}): {result.stderr[:200]}")
            return {"ok": False, "error": result.stderr[:200]}
        return json.loads(result.stdout)
    except FileNotFoundError:
        logger.warning("lark-cli not found; skipping IM notification")
        return {"ok": False, "error": "lark-cli not found"}
    except json.JSONDecodeError:
        logger.warning("lark-cli returned non-JSON; skipping")
        return {"ok": False, "error": "non-JSON response"}
    except subprocess.TimeoutExpired:
        logger.warning("lark-cli timed out; skipping")
        return {"ok": False, "error": "timeout"}


def _mention_block(agent_name: str) -> dict:
    """Build a Lark IM mention block for a known agent.
    Falls back to plain text if the agent has no mapped open_id.
    """
    mention_map = {
        "zcode": "ou_bb563f888307b2c04339b8918542a7e9",
    }
    oid = mention_map.get(agent_name)
    if oid:
        return {
            "tag": "mention",
            "user_id": oid,
            "user_id_type": "open_id",
            "name": agent_name,
        }
    return {"tag": "plain_text", "content": agent_name}


def _card_header(title: str, color: str = "blue") -> dict:
    return {
        "title": {"tag": "plain_text", "content": title[:80]},
        "template": color,
    }


def _card_element(tag: str, content: str, **kwargs) -> dict:
    el = {"tag": tag, "content": content[:2000]}
    el.update(kwargs)
    return el


def notify_discussion_created(
    title: str,
    memory_id: int,
    creator: str,
    participants: list[str],
    options: list[str],
    timeout_hours: int,
) -> None:
    """Send a card message: new discussion created."""
    try:
        participant_mentions = []
        for p in participants:
            participant_mentions.append(_mention_block(p))

        elements = [
            _card_element("markdown", f"**{creator}** 发起了一个讨论"),
        ]

        if options:
            opts_text = "\n".join(f"• {o}" for o in options)
            elements.append(_card_element("markdown", f"**方案:**\n{opts_text}"))

        if participants:
            elements.append(_card_element("markdown", f"**参与者:** {', '.join(participants)}"))
            elements.append(_card_element("markdown", f"⏱ **超时:** {timeout_hours}h"))

        card = {
            "config": {"wide_screen_mode": True},
            "header": _card_header(f"📋 {title[:50]}"),
            "elements": elements + [
                {"tag": "hr"},
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "查看详情"},
                            "type": "default",
                            "multi_url": {
                                "url": f"http://127.0.0.1:8199/memories/{memory_id}",
                                "android_url": "",
                                "ios_url": "",
                                "pc_url": "",
                            },
                        }
                    ],
                },
            ],
        }

        _lark([
            "im", "+messages-send",
            "--chat-id", _CHAT_ID,
            "--msg-type", "interactive",
            "--content", json.dumps(card, ensure_ascii=False),
            "--as", "bot",
        ])
    except Exception as e:
        logger.warning(f"notify_discussion_created failed: {e}")


def notify_discussion_responded(
    discussion_id: int,
    agent_name: str,
    stance: str,
    participants: list[str],
    converged: bool = False,
) -> None:
    """Send a card message: agent responded."""
    try:
        remaining = [p for p in participants if p != agent_name]
        emoji = "✅" if stance == "agree" else ("❌" if stance == "disagree" else "⏳")

        remaining_str = ", ".join(remaining) if remaining else "无"

        card = {
            "config": {"wide_screen_mode": True},
            "header": _card_header(f"{emoji} {agent_name} 已表态", "green" if stance == "agree" else "red"),
            "elements": [
                _card_element("markdown", f"**{agent_name}** → {stance}"),
                _card_element("markdown", f"讨论 #**{discussion_id}**"),
                _card_element("markdown", f"待回应: {remaining_str}"),
            ],
        }

        if converged:
            card["elements"].append(_card_element("markdown", "🎯 **讨论已收敛！**"))
            card["header"]["template"] = "purple"

        _lark([
            "im", "+messages-send",
            "--chat-id", _CHAT_ID,
            "--msg-type", "interactive",
            "--content", json.dumps(card, ensure_ascii=False),
            "--as", "bot",
        ])
    except Exception as e:
        logger.warning(f"notify_discussion_responded failed: {e}")


def notify_discussion_converged(
    discussion_id: int,
    title: str,
    reason: str,
    participants: list[str],
    stances: dict[str, str],
    task_count: int,
) -> None:
    """Send a card message: discussion converged with tasks."""
    try:
        stances_text = "\n".join(
            f"{a}: {s}" for a, s in stances.items()
        )

        card = {
            "config": {"wide_screen_mode": True},
            "header": _card_header(f"🎯 讨论已收敛: {title[:40]}", "purple"),
            "elements": [
                _card_element("markdown", f"**议题:** {title}"),
                _card_element("markdown", f"**收敛方式:** {reason}"),
                _card_element("markdown", f"**各方立场:**\n{stances_text}"),
                _card_element("markdown", f"**任务数:** {task_count}"),
            ],
        }

        _lark([
            "im", "+messages-send",
            "--chat-id", _CHAT_ID,
            "--msg-type", "interactive",
            "--content", json.dumps(card, ensure_ascii=False),
            "--as", "bot",
        ])
    except Exception as e:
        logger.warning(f"notify_discussion_converged failed: {e}")
