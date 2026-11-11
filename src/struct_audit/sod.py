# -*- coding: utf-8 -*-
"""职责分离（SoD）：计算者与签字者必须是不同身份。

设计边界
  · Actor 只做身份载体；认证由部署方/宿主系统负责；
  · 只有显式传入 computer + signer 时才启用 SoD；不传保持旧行为；
  · 只覆盖 close / 导出两个动作，不做完整 RBAC。
"""
from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass
from datetime import datetime, timezone

from .guards import BoundaryGuard, BoundaryViolation, EvidenceLevel

COMPUTER_ROLES = ("engine", "system")
SIGNER_ROLES = ("accountant", "auditor", "reviewer")


class SoDViolation(BoundaryViolation):
    """职责分离被破坏：同 id/同角色，或角色不合法。"""


@dataclass(frozen=True)
class Actor:
    """身份载体：id 是真实/宿主侧身份，role 是职责角色。"""

    id: str
    role: str
    source: str = "host"

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("Actor.id 必须是非空字符串")
        if not isinstance(self.role, str) or not self.role.strip():
            raise ValueError("Actor.role 必须是非空字符串")
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("Actor.source 必须是非空字符串")
        object.__setattr__(self, "id", self.id.strip())
        object.__setattr__(self, "role", self.role.strip())
        object.__setattr__(self, "source", self.source.strip())


@dataclass(frozen=True)
class SignRecord:
    action: str
    computer_id: str
    computer_role: str
    signer_id: str
    signer_role: str
    evidence: str
    digest: str
    created_at: str

    def to_dict(self) -> dict[str, str]:
        return {
            "action": self.action,
            "computer_id": self.computer_id,
            "computer_role": self.computer_role,
            "signer_id": self.signer_id,
            "signer_role": self.signer_role,
            "evidence": self.evidence,
            "digest": self.digest,
            "created_at": self.created_at,
        }

    def append_jsonl(self, path: str | pathlib.Path) -> None:
        target = pathlib.Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(self.to_dict(), ensure_ascii=False) + "\n")


def assert_sod(computer: Actor, signer: Actor) -> None:
    """硬检查：计算者与签字者必须不同身份、不同职责。"""
    if not isinstance(computer, Actor) or not isinstance(signer, Actor):
        raise TypeError("computer / signer 必须是 Actor")
    if computer.role not in COMPUTER_ROLES:
        raise SoDViolation(f"计算者角色不合法：{computer.role}")
    if signer.role not in SIGNER_ROLES:
        raise SoDViolation(f"签字者角色不合法：{signer.role}")
    if computer.id == signer.id:
        raise SoDViolation(f"SoD 违反：计算者与签字者 id 相同（{signer.id}）")
    if computer.role == signer.role:
        raise SoDViolation(f"SoD 违反：计算者与签字者角色相同（{signer.role}）")


def make_record(
    action: str,
    computer: Actor,
    signer: Actor,
    *,
    evidence: EvidenceLevel,
    digest: str,
) -> SignRecord:
    return SignRecord(
        action=action,
        computer_id=computer.id,
        computer_role=computer.role,
        signer_id=signer.id,
        signer_role=signer.role,
        evidence=evidence.value,
        digest=digest,
        created_at=datetime.now(timezone.utc).isoformat(),
    )


class SoDGuard(BoundaryGuard):
    """在基础 guard 上放行 signer 角色执行 report.mark_verified，其余动作仍走基础规则。"""

    def __init__(self, base: BoundaryGuard | None = None) -> None:
        self._base = base or BoundaryGuard()
        # 复用基础规则，避免复制一套角色/证据语义
        self._rules = dict(self._base._rules)  # type: ignore[attr-defined]

    def check(
        self,
        action: str,
        *,
        role: str,
        evidence: EvidenceLevel,
        override_reason: str = "",
    ) -> None:
        if action == "report.mark_verified":
            if role not in SIGNER_ROLES:
                raise BoundaryViolation(f"角色 {role} 不允许执行 {action}（签字角色）")
            if evidence is not EvidenceLevel.VERIFIED:
                raise BoundaryViolation(
                    f"{action} 需要证据≥verified，当前 {evidence.value}"
                )
            if override_reason.strip():
                raise BoundaryViolation(f"{action} 不允许 override")
            return
        self._base.check(action, role=role, evidence=evidence, override_reason=override_reason)


__all__ = [
    "Actor",
    "SignRecord",
    "SoDViolation",
    "SoDGuard",
    "assert_sod",
    "make_record",
    "COMPUTER_ROLES",
    "SIGNER_ROLES",
]
