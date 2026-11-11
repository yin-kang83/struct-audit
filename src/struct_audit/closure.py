# -*- coding: utf-8 -*-
"""自持闭环（图 3/4 的可执行版本）：一次执行没通过「链 + 反向印证 + 残差归位」，
就不允许被当作终态使用。

close_run 的闸门顺序：
  1. 底稿哈希链完整（状态与链一致）；
  2. 执行报告与当前底稿同一状态（防止「报告是 A、底稿是 B」）；
  3. 执行轨迹存在且至少一步可反解（空转预警：不产生可验证新状态就不算闭环）；
  4. 反向印证残差在容差内（结构 → 数 可回去）；
  5. 期望值残差归位（可选，严格或显式尾差策略）。

诚实边界
  · 闭环只证明「系统内部自洽 + 可反算」，不证明「业务口径正确」；
  · 没有独立证据（明细/总账/外部）时，报告会明确写「恒真风险」，不给假安全感。
"""
from __future__ import annotations

import pathlib
from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping

from .audit import BackwardAudit, verify_backward
from .decimal_utils import ensure_decimal
from .errors import ResidualError, UnverifiedStateError
from .guards import BoundaryGuard, EvidenceLevel
from .residual import Residual, ResidualPolicy, check_residuals
from .router import ExecutionReport
from .sod import Actor, SoDGuard, SoDViolation, assert_sod, make_record
from .working_paper import WorkingPaper

ZERO = Decimal("0")


@dataclass(frozen=True)
class ClosureReport:
    """一次闭环的终审报告。"""

    chain_ok: bool
    backward: BackwardAudit
    residuals: tuple[Residual, ...]
    digest: str
    notes: tuple[str, ...]

    @property
    def closed(self) -> bool:
        return self.chain_ok and self.backward.passed


def close_run(
    paper: WorkingPaper,
    report: ExecutionReport,
    *,
    expected: Mapping[str, object] | None = None,
    tolerance: object = ZERO,
    policy: ResidualPolicy | None = None,
    independent_evidence: bool = False,
    boundary_guard: BoundaryGuard | None = None,
    actor_role: str = "system",
    evidence: EvidenceLevel = EvidenceLevel.VERIFIED,
    override_reason: str = "",
    computer: Actor | None = None,
    signer: Actor | None = None,
    sign_record_path: str | pathlib.Path | None = None,
    review_store=None,
) -> ClosureReport:
    """闭环闸门：不通过直接抛，不返回「差不多能用」的状态。

    boundary_guard 可选：传入时在返回前检查 `report.mark_verified`
    （谁在什么证据等级下能标记“已验证”）；不传则保持旧行为。

    computer + signer 同时传入时启用 SoD：两者必须是不同 id/角色；
    启用 SoD 时必须传 boundary_guard；签字记录可选写入 sign_record_path。

    review_store 可选：传入时强制 substance_approval 快照必须有 approved 记录，
    否则不能闭环（经济实质未批准，不能当终态）。
    """
    if not isinstance(paper, WorkingPaper):
        raise TypeError("paper 必须是 WorkingPaper")
    if not isinstance(report, ExecutionReport):
        raise TypeError("report 必须是 ExecutionReport")

    if (computer is None) != (signer is None):
        raise SoDViolation("启用 SoD 时必须同时提供 computer 和 signer")
    effective_guard = boundary_guard
    if computer is not None and signer is not None:
        assert_sod(computer, signer)
        if boundary_guard is None:
            raise SoDViolation("启用 SoD 时必须传 boundary_guard，不能默认通过")
        effective_guard = SoDGuard(boundary_guard)
        actor_role = signer.role

    # 1. 链完整
    verify = paper.verify()
    if not verify.ok:
        raise UnverifiedStateError(f"底稿链校验失败，不能闭环：{verify.reason}")

    # 2. 报告与底稿同一状态
    if report.digest != paper.digest():
        raise UnverifiedStateError(
            "执行报告与当前底稿不是同一状态（疑似执行后改数），拒绝闭环"
        )

    # 3. 轨迹存在 + 空转预警
    if not report.trace:
        raise UnverifiedStateError("空转预警：本次执行没有轨迹，无法反向印证，不能闭环")
    backward = verify_backward(report.trace)
    if backward.checked_count == 0:
        raise UnverifiedStateError(
            "空转预警：本次执行没有任何可反解步骤（全是 SUM/MAX/MIN/TAX 等不可逆算子），"
            "双向闭环不成立"
        )

    # 4. 反向印证
    if not backward.passed:
        failures = [
            check
            for check in backward.checks
            if check.checked and (check.residual is None or abs(check.residual) > backward.tolerance)
        ]
        raise ResidualError(
            "反向印证残差超容差（闭环失败）："
            + "；".join(check.describe() for check in failures)
        )

    # 5. 期望值残差归位（可选）
    residuals: tuple[Residual, ...] = ()
    if expected:
        active_policy = policy or ResidualPolicy(tolerance=ensure_decimal(tolerance, field="容差"))
        residuals = check_residuals(paper, expected, policy=active_policy)

    notes: list[str] = []
    if review_store is not None:
        from .substance_reconcile import require_paper_approvals

        require_paper_approvals(paper, review_store)
        notes.append("实质审批对账通过：substance_approval 快照均有 approved 记录")
    if not independent_evidence:
        notes.append(
            "闭环只证明系统内部自洽与可反算；没有明细/总账/外部独立证据，"
            "不能当业务正确性证据（恒真风险）"
        )
    if backward.skipped:
        notes.append(f"反向印证跳过 {len(backward.skipped)} 个不可逆步骤，未假装通过")
    if effective_guard is not None:
        effective_guard.check(
            "report.mark_verified",
            role=actor_role,
            evidence=evidence,
            override_reason=override_reason,
        )
    if computer is not None and signer is not None:
        record = make_record(
            "report.mark_verified", computer, signer,
            evidence=evidence, digest=paper.digest(),
        )
        if sign_record_path is not None:
            record.append_jsonl(sign_record_path)
        notes.append(
            f"SoD：computer={computer.id}/{computer.role}，"
            f"signer={signer.id}/{signer.role}，已记录"
        )
    return ClosureReport(
        chain_ok=True,
        backward=backward,
        residuals=residuals,
        digest=paper.digest(),
        notes=tuple(notes),
    )
