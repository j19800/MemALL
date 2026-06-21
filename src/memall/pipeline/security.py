"""
Phase 13: Security Governance Module
=====================================
Provides three core capabilities:
1.  audit_sensitive    — scan memories for sensitive data leakage
2.  PermissionManager  — three-level (public/trusted/private) access control
3.  security_score     — composite database security health score
"""

import re
import sqlite3
from typing import Optional, List, Dict, Any

from memall.core.db import get_conn
from memall.federation.family import get_family_db_path


# ══════════════════════════════════════════════════════════════════
# Precompiled regex patterns (compiled once, reused)
# ══════════════════════════════════════════════════════════════════

_RE_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")
_RE_IP = re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")
_RE_PHONE = re.compile(r"1[3-9]\d{9}")
_RE_IDCARD = re.compile(r"\d{17}[\dXx]")

_API_KEYWORDS = ["api_key", "apikey", "secret", "token", "password", "passwd"]
_API_KEY_PATTERN = re.compile(
    r"(" + "|".join(re.escape(kw) for kw in _API_KEYWORDS) + r")\s*[:=]\s*\S+",
    re.IGNORECASE,
)


def _redact_api_key(text: str, keyword: str) -> str:
    """Redact the value after a keyword like api_key=xxx → api_key=***"""
    idx = text.lower().find(keyword.lower())
    if idx < 0:
        return f"{keyword}***"
    start = idx + len(keyword)
    # Show keyword + *** for the rest
    return text[idx:idx + len(keyword)] + "***"


def _redact_email(email: str) -> str:
    """Redact email: user@domain.com → u***@domain.com"""
    parts = email.split("@", 1)
    if len(parts) != 2:
        return email[0] + "***"
    return parts[0][0] + "***@" + parts[1]


def _redact_phone(phone: str) -> str:
    """Redact phone: 13812341234 → 138****1234"""
    if len(phone) >= 11:
        return phone[:3] + "****" + phone[-4:]
    return phone[:3] + "****"


def _redact_ip(ip: str) -> str:
    """Redact IP: 192.168.1.1 → 192.168.***.***"""
    parts = ip.split(".")
    if len(parts) == 4:
        return f"{parts[0]}.{parts[1]}.***.***"
    return ip


def _redact_idcard(idnum: str) -> str:
    """Redact ID: 320102199001011234 → 3201**********1234"""
    if len(idnum) >= 18:
        return idnum[:4] + "*" * 10 + idnum[-4:]
    return idnum[:4] + "****"


def _make_preview(match_type: str, match_text: str) -> str:
    """Generate a redacted preview string for a matched pattern."""
    redactors = {
        "api_key": lambda t: _redact_api_key(t, _matched_keyword(t)),
        "email": _redact_email,
        "ip": _redact_ip,
        "phone": _redact_phone,
        "id_card": _redact_idcard,
    }
    fn = redactors.get(match_type, lambda t: t[:20] + "..." if len(t) > 20 else t)
    return fn(match_text)


def _matched_keyword(text: str) -> str:
    """Find which API keyword appears in the text."""
    lower = text.lower()
    for kw in _API_KEYWORDS:
        if kw in lower:
            return kw
    return "secret"


def _is_valid_ip(ip: str) -> bool:
    """Validate each octet is 0-255."""
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(p) <= 255 for p in parts)
    except ValueError:
        return False


# ══════════════════════════════════════════════════════════════════
# 1. Sensitive Data Audit
# ══════════════════════════════════════════════════════════════════

def audit_sensitive(
    agent_name: Optional[str] = None,
    threshold: int = 3,
) -> Dict[str, Any]:
    """Scan memories table for potential sensitive information.

    Detection categories (case-insensitive):
        - api_key : content contains api_key/apikey/secret/token/password/passwd
          keywords AND content length > 20
        - email   : email-address pattern
        - ip      : IPv4 address pattern (validated 0-255 per octet)
        - phone   : Chinese mobile number pattern (1[3-9]xxxxxxxxx)
        - id_card : Chinese ID card number pattern (17 digits + digit/X)

    Args:
        agent_name: Optional agent filter.  Scans all agents when None.
        threshold: Minimum number of findings before risk_level raises.

    Returns:
        dict with keys: total_scanned, findings, by_type, details, risk_level
    """
    conn = get_conn()
    try:
        # ── Fetch memories ──
        if agent_name:
            rows = conn.execute(
                "SELECT id, content, agent_name, category FROM memories "
                "WHERE agent_name = ? ORDER BY id",
                (agent_name,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, content, agent_name, category FROM memories ORDER BY id"
            ).fetchall()

        total_scanned = len(rows)
        by_type: Dict[str, int] = {"api_key": 0, "email": 0, "ip": 0, "phone": 0, "id_card": 0}
        details: List[Dict[str, Any]] = []

        for row in rows:
            memory_id = row["id"]
            content = row["content"] or ""
            agent = row["agent_name"] or ""
            cat = row["category"] or ""

            if not content:
                continue

            # ── a) API Key detection ──
            lower = content.lower()
            keyword_hit = None
            for kw in _API_KEYWORDS:
                if kw in lower and len(content) > 20:
                    keyword_hit = kw
                    break
            if keyword_hit:
                by_type["api_key"] += 1
                details.append({
                    "memory_id": memory_id,
                    "agent_name": agent,
                    "category": cat,
                    "match_type": "api_key",
                    "match_preview": _redact_api_key(content, keyword_hit),
                })

            # ── b) Email detection ──
            for m in _RE_EMAIL.finditer(content):
                by_type["email"] += 1
                details.append({
                    "memory_id": memory_id,
                    "agent_name": agent,
                    "category": cat,
                    "match_type": "email",
                    "match_preview": _redact_email(m.group()),
                })

            # ── c) IP detection ──
            for m in _RE_IP.finditer(content):
                ip = m.group()
                if _is_valid_ip(ip):
                    # Exclude common non-sensitive patterns
                    if ip not in ("0.0.0.0", "127.0.0.1", "255.255.255.255"):
                        by_type["ip"] += 1
                        details.append({
                            "memory_id": memory_id,
                            "agent_name": agent,
                            "category": cat,
                            "match_type": "ip",
                            "match_preview": _redact_ip(ip),
                        })

            # ── d) Phone detection ──
            for m in _RE_PHONE.finditer(content):
                by_type["phone"] += 1
                details.append({
                    "memory_id": memory_id,
                    "agent_name": agent,
                    "category": cat,
                    "match_type": "phone",
                    "match_preview": _redact_phone(m.group()),
                })

            # ── e) ID card detection ──
            for m in _RE_IDCARD.finditer(content):
                by_type["id_card"] += 1
                details.append({
                    "memory_id": memory_id,
                    "agent_name": agent,
                    "category": cat,
                    "match_type": "id_card",
                    "match_preview": _redact_idcard(m.group()),
                })

        findings = sum(by_type.values())

        # ── Risk level ──
        if findings >= threshold * 3:
            risk_level = "high"
        elif findings >= threshold:
            risk_level = "medium"
        else:
            risk_level = "low"

        return {
            "total_scanned": total_scanned,
            "findings": findings,
            "by_type": by_type,
            "details": details[:20],
            "risk_level": risk_level,
        }
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════
# 2. Permission Manager
# ══════════════════════════════════════════════════════════════════

def _ensure_permission_column(conn: sqlite3.Connection) -> None:
    """Ensure identities table has permission_level column."""
    cur = conn.execute("PRAGMA table_info(identities)")
    cols = [r["name"] for r in cur.fetchall()]
    if "permission_level" not in cols:
        conn.execute(
            "ALTER TABLE identities ADD COLUMN permission_level "
            "TEXT NOT NULL DEFAULT 'private'"
        )
        conn.commit()


def set_permission(agent_name: str, level: str) -> Dict[str, Any]:
    """Set the permission level for an agent in identities table.

    Args:
        agent_name: The agent to configure.
        level: One of 'public', 'trusted', 'private'.

    Returns:
        dict: {agent_name, level, status}
    """
    if level not in ("public", "trusted", "private"):
        return {"error": f"invalid level '{level}', must be public/trusted/private"}

    conn = get_conn()
    try:
        _ensure_permission_column(conn)

        # Check agent exists
        existing = conn.execute(
            "SELECT id FROM identities WHERE agent_name = ?", (agent_name,)
        ).fetchone()

        if not existing:
            # Auto-create identity if missing
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO identities (agent_name, permission_level, last_heartbeat) "
                "VALUES (?, ?, ?)",
                (agent_name, level, now),
            )
        else:
            conn.execute(
                "UPDATE identities SET permission_level = ? WHERE agent_name = ?",
                (level, agent_name),
            )
        conn.commit()

        return {
            "agent_name": agent_name,
            "level": level,
            "status": "ok",
        }
    finally:
        conn.close()


def get_permission(agent_name: str) -> Dict[str, Any]:
    """Get the current permission level of an agent.

    Args:
        agent_name: The agent to query.

    Returns:
        dict: {agent_name, level} or {error: ...}
    """
    conn = get_conn()
    try:
        _ensure_permission_column(conn)
        row = conn.execute(
            "SELECT permission_level FROM identities WHERE agent_name = ?",
            (agent_name,),
        ).fetchone()
        if row:
            return {"agent_name": agent_name, "level": row["permission_level"]}
        return {"agent_name": agent_name, "level": "private"}  # default
    finally:
        conn.close()


def check_access(requester_agent: str, target_agent: str) -> Dict[str, Any]:
    """Check whether requester_agent is allowed to access target_agent's memories.

    Logic:
        - target private  → only requester == target
        - target trusted  → requester is in any family_circle
        - target public   → always allow
        - target unset    → treated as private

    Args:
        requester_agent: The agent requesting access.
        target_agent: The agent whose memories are being accessed.

    Returns:
        dict: {allowed: bool, reason: str}
    """
    if requester_agent == target_agent:
        return {"allowed": True, "reason": "self-access always permitted"}

    conn = get_conn()
    try:
        _ensure_permission_column(conn)
        row = conn.execute(
            "SELECT permission_level FROM identities WHERE agent_name = ?",
            (target_agent,),
        ).fetchone()

        perm = row["permission_level"] if row else "private"

        if perm == "public":
            return {"allowed": True, "reason": f"target '{target_agent}' is public"}

        if perm == "trusted":
            # Check family_circle table in family.db
            fam_db_path = get_family_db_path()
            if not fam_db_path.exists():
                return {
                    "allowed": False,
                    "reason": (
                        f"target '{target_agent}' is trusted but no family database exists; "
                        f"requester '{requester_agent}' not verified"
                    ),
                }
            try:
                fam_conn = sqlite3.connect(str(fam_db_path))
                fam_conn.row_factory = sqlite3.Row
                row = fam_conn.execute(
                    "SELECT 1 FROM family_circle WHERE member_name = ? AND status = 'active' LIMIT 1",
                    (requester_agent,),
                ).fetchone()
                fam_conn.close()
                if row:
                    return {
                        "allowed": True,
                        "reason": (
                            f"target '{target_agent}' is trusted and "
                            f"requester '{requester_agent}' is in family_circle"
                        ),
                    }
                return {
                    "allowed": False,
                    "reason": (
                        f"target '{target_agent}' is trusted but "
                        f"requester '{requester_agent}' is not in any family_circle"
                    ),
                }
            except Exception as e:
                return {
                    "allowed": False,
                    "reason": f"target '{target_agent}' is trusted but family_circle check failed: {e}",
                }

        # private
        return {
            "allowed": False,
            "reason": (
                f"target '{target_agent}' is private; "
                f"requester '{requester_agent}' is not the owner"
            ),
        }
    finally:
        conn.close()


def list_agents_by_permission(level: str) -> List[Dict[str, Any]]:
    """List all agents that have the specified permission level.

    Args:
        level: 'public', 'trusted', or 'private'.

    Returns:
        List of dicts with agent_name, permission_level, agent_type.
    """
    conn = get_conn()
    try:
        _ensure_permission_column(conn)
        rows = conn.execute(
            "SELECT agent_name, permission_level, agent_type FROM identities "
            "WHERE permission_level = ? ORDER BY agent_name",
            (level,),
        ).fetchall()
        return [
            {
                "agent_name": r["agent_name"],
                "permission_level": r["permission_level"],
                "agent_type": r["agent_type"],
            }
            for r in rows
        ]
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════
# 3. Security Score
# ══════════════════════════════════════════════════════════════════

def security_score() -> Dict[str, Any]:
    """Compute overall database security health score (0–100).

    Four indicators weighted into composite score:
        a. Sensitive exposure rate  (1 - findings/total) × 40
        b. Private memory ratio     (private ratio) × 20
        c. Orphan isolation         (1 - orphaned_sensitive/total) × 20
        d. Permission coverage      (agents with permission / total agents) × 20

    Returns:
        dict: {score, grade, breakdown, recommendations}
    """
    conn = get_conn()
    try:
        # ── Totals ──
        total_mem = conn.execute("SELECT COUNT(*) AS c FROM memories").fetchone()["c"]
        if total_mem == 0:
            return {
                "score": 100,
                "grade": "A",
                "breakdown": {
                    "exposure_rate": 0.0,
                    "private_ratio": 1.0,
                    "orphaned_ratio": 0.0,
                    "coverage": 1.0,
                },
                "recommendations": ["No memories in database — consider seeding initial data."],
            }

        # ── a) Sensitive exposure rate ──
        audit_result = audit_sensitive()
        findings = audit_result["findings"]
        exposure_rate = min(findings / total_mem, 1.0)

        # ── b) Private memory ratio ──
        _ensure_permission_column(conn)
        private_agents = [
            r["agent_name"]
            for r in conn.execute(
                "SELECT agent_name FROM identities WHERE permission_level = 'private'"
                " OR permission_level IS NULL"
            ).fetchall()
        ]
        if private_agents:
            placeholders = ",".join("?" for _ in private_agents)
            private_mem_count = conn.execute(
                f"SELECT COUNT(*) AS c FROM memories WHERE agent_name IN ({placeholders})",
                private_agents,
            ).fetchone()["c"]
        else:
            private_mem_count = 0
        private_ratio = private_mem_count / total_mem

        # ── c) Orphaned sensitive ratio ──
        if findings > 0:
            # Get memory ids from audit details
            sensitive_ids = [d["memory_id"] for d in audit_result["details"]]
        else:
            sensitive_ids = []

        orphaned_count = 0
        if sensitive_ids:
            for mid in sensitive_ids:
                in_deg = conn.execute(
                    "SELECT COUNT(*) AS c FROM edges WHERE target_id = ?", (mid,)
                ).fetchone()["c"]
                out_deg = conn.execute(
                    "SELECT COUNT(*) AS c FROM edges WHERE source_id = ?", (mid,)
                ).fetchone()["c"]
                if in_deg == 0 and out_deg == 0:
                    orphaned_count += 1
        orphaned_ratio = orphaned_count / max(total_mem, 1)

        # ── d) Permission coverage ──
        _ensure_permission_column(conn)
        total_agents = conn.execute("SELECT COUNT(*) AS c FROM identities").fetchone()["c"]
        covered_agents = conn.execute(
            "SELECT COUNT(*) AS c FROM identities "
            "WHERE permission_level IS NOT NULL AND permission_level != ''"
        ).fetchone()["c"]
        coverage = covered_agents / max(total_agents, 1)

        # ── Composite score ──
        score_raw = (
            (1.0 - exposure_rate) * 40
            + private_ratio * 20
            + (1.0 - orphaned_ratio) * 20
            + coverage * 20
        )
        score = round(max(0.0, min(100.0, score_raw)), 1)

        # ── Grade ──
        if score >= 90:
            grade = "A"
        elif score >= 75:
            grade = "B"
        elif score >= 60:
            grade = "C"
        elif score >= 40:
            grade = "D"
        else:
            grade = "F"

        # ── Recommendations ──
        recommendations: List[str] = []
        if exposure_rate > 0.05:
            recommendations.append(
                f"High sensitive data exposure ({exposure_rate:.1%}). "
                f"Review {findings} findings and redact secrets from memories."
            )
        if private_ratio < 0.5:
            recommendations.append(
                "Private memories are below 50%. Consider setting more agents to private."
            )
        if orphaned_ratio > 0.1:
            recommendations.append(
                f"{orphaned_count} sensitive memories are isolated (no edges). "
                "Review and link or delete them."
            )
        if coverage < 0.8:
            recommendations.append(
                f"Permission coverage is low ({coverage:.0%}). "
                "Run 'memall security permit --agent <name> --level <level>' "
                "for unconfigured agents."
            )
        if not recommendations:
            recommendations.append("Security posture looks good. No urgent actions needed.")

        return {
            "score": score,
            "grade": grade,
            "breakdown": {
                "exposure_rate": round(exposure_rate, 4),
                "private_ratio": round(private_ratio, 4),
                "orphaned_ratio": round(orphaned_ratio, 4),
                "coverage": round(coverage, 4),
            },
            "recommendations": recommendations,
        }
    finally:
        conn.close()
