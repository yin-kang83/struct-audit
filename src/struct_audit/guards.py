# -*- coding: utf-8 -*-
"""语义漂移与边界滑移的防护。

语义漂移（semantic drift）
  LLM/提示词/契约/模型版本变化后，字段含义、单位、期间、含税口径悄悄变化，
  数值可能仍然“合法”，但已经不是同一个意思。本模块用指纹+基线做**检测/告警**：
    · schema_digest：契约 schema 的稳定摘要；
    · prompt_digest：提示词摘要；
    · source_digest：原文摘要（同一份原文才可比较）；
    · ExtractionStamp：一次抽取的 (extractor, model, schema, prompt, source) 指纹；
    · SemanticBaseline.assert_no_drift：schema/prompt/model/extractor 变化 → SemanticDrift。
  注意：检测不等于消除；变化必须走版本升级/评审，而不是静默接受。

边界滑移（boundary slippage）
  系统能力/责任边界被一点点磨掉：模型算数、缺证据默认通过、容差悄悄放大、
  汇总表当明细证据、垂直场景无限扩张。本模块用**规则 + 证据等级**拦截：
    · EvidenceLevel：verified / estimated / unverified / blocked；
    · BoundaryRule：动作允许的角色、最低证据、是否允许显式 override；
    · BoundaryGuard.check：越权/证据不足/无理由 override → BoundaryViolation。
  注意：guard 不是业务判断，只保证“谁在什么证据下能做什么动作”不被静默绕过。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from .errors import RejectedInput, SystemCollapse


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def schema_digest(model: type) -> str:
    """Pydantic 契约 schema 的稳定摘要（排序键、紧凑 JSON）。"""
    if not isinstance(model, type) or not hasattr(model, "model_json_schema"):
        raise TypeError("model 必须是 pydantic BaseModel 子类")
    payload = json.dumps(
        model.model_json_schema(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return _sha256(payload)


def prompt_digest(prompt: str) -> str:
    return _sha256(str(prompt))


def source_digest(text: str) -> str:
    """原文指纹：同一份输入才可比较语义是否漂移。"""
    return _sha256(str(text))


def assert_same_source(expected: str, actual: str) -> None:
    if expected != actual:
        raise SemanticDrift("原文指纹不一致：不能拿不同原文的抽取结果做语义一致性比较")


class SemanticDrift(RejectedInput, ValueError):
    """语义漂移：契约/提示词/模型/原文变化，导致含义可能变了。"""


class BoundaryViolation(SystemCollapse, ValueError):
    """边界滑移：动作越权、证据不足、override 无理由。"""


class EvidenceLevel(str, Enum):
    """证据等级：从“无证据”到“已验证”。"""

    BLOCKED = "blocked"
    UNVERIFIED = "unverified"
    ESTIMATED = "estimated"
    VERIFIED = "verified"


_RANK = {
    EvidenceLevel.BLOCKED: 0,
    EvidenceLevel.UNVERIFIED: 1,
    EvidenceLevel.ESTIMATED: 2,
    EvidenceLevel.VERIFIED: 3,
}


@dataclass(frozen=True)
class ExtractionStamp:
    """一次抽取的指纹：用于发现 schema/prompt/model/extractor/原文变化。"""

    extractor: str
    model: str
    schema: str
    prompt: str
    source: str

    @classmethod
    def capture(cls, *, extractor: str, model: str, schema: type, prompt: str, text: str) -> "ExtractionStamp":
        return cls(
            extractor=str(extractor),
            model=str(model),
            schema=schema_digest(schema),
            prompt=prompt_digest(prompt),
            source=source_digest(text),
        )


@dataclass(frozen=True)
class SemanticBaseline:
    """某个场景的语义基线：schema/prompt/model/extractor 的指纹。"""

    extractor: str
    model: str
    schema: str
    prompt: str

    @classmethod
    def from_stamp(cls, stamp: ExtractionStamp) -> "SemanticBaseline":
        return cls(stamp.extractor, stamp.model, stamp.schema, stamp.prompt)

    def assert_no_drift(self, current: ExtractionStamp, *, allow_model_update: bool = False) -> None:
        mismatches: list[str] = []
        if current.schema != self.schema:
            mismatches.append("schema")
        if current.prompt != self.prompt:
            mismatches.append("prompt")
        if current.extractor != self.extractor:
            mismatches.append("extractor")
        if not allow_model_update and current.model != self.model:
            mismatches.append("model")
        if mismatches:
            raise SemanticDrift(
                "语义基线变化（需版本升级/评审，不能静默接受）："
                + "、".join(mismatches)
            )


@dataclass(frozen=True)
class BoundaryRule:
    """一个动作的边界：谁可以做、最低证据、是否允许显式 override。"""

    action: str
    allowed_roles: tuple[str, ...]
    minimum_evidence: EvidenceLevel = EvidenceLevel.VERIFIED
    allow_override: bool = False


DEFAULT_BOUNDARY_RULES: tuple[BoundaryRule, ...] = (
    BoundaryRule("model.compute", allowed_roles=("engine", "system")),
    BoundaryRule("model.write_working_paper", allowed_roles=("system",)),
    BoundaryRule("report.mark_verified", allowed_roles=("system",), minimum_evidence=EvidenceLevel.VERIFIED),
    BoundaryRule("summary.use_as_detail_evidence", allowed_roles=()),
    BoundaryRule(
        "tolerance.override",
        allowed_roles=("accountant", "auditor"),
        minimum_evidence=EvidenceLevel.UNVERIFIED,
        allow_override=True,
    ),
    BoundaryRule(
        "vertical.scope_expand",
        allowed_roles=("product_owner",),
        minimum_evidence=EvidenceLevel.VERIFIED,
        allow_override=True,
    ),
)


def assert_summary_not_detail_evidence(
    guard: BoundaryGuard,
    *,
    role: str,
    evidence: EvidenceLevel,
    override_reason: str = "",
) -> None:
    """显式断言：汇总表不得当明细证据（默认规则不允许任何角色）。"""
    guard.check(
        "summary.use_as_detail_evidence",
        role=role,
        evidence=evidence,
        override_reason=override_reason,
    )


class BoundaryGuard:
    """按规则检查“动作 + 角色 + 证据 + override 理由”。"""

    def __init__(self, rules: Sequence[BoundaryRule] = DEFAULT_BOUNDARY_RULES) -> None:
        self._rules: dict[str, BoundaryRule] = {}
        for rule in rules:
            if rule.action in self._rules:
                raise ValueError(f"重复的边界规则：{rule.action}")
            if not rule.action.strip():
                raise ValueError("action 不能为空")
            self._rules[rule.action] = rule

    def check(
        self,
        action: str,
        *,
        role: str,
        evidence: EvidenceLevel,
        override_reason: str = "",
    ) -> None:
        rule = self._rules.get(action)
        if rule is None:
            raise BoundaryViolation(f"未注册的边界动作：{action}")
        if role not in rule.allowed_roles:
            raise BoundaryViolation(f"角色 {role} 不允许执行 {action}")
        if _RANK[evidence] < _RANK[rule.minimum_evidence]:
            if not (rule.allow_override and override_reason.strip()):
                raise BoundaryViolation(
                    f"{action} 需要证据≥{rule.minimum_evidence.value}，当前 {evidence.value}"
                )
        if override_reason.strip() and not rule.allow_override:
            raise BoundaryViolation(f"{action} 不允许 override")
