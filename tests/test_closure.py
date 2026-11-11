# -*- coding: utf-8 -*-
"""自持闭环：链 + 反向印证 + 残差归位任一不过，就不允许当终态使用。"""
from decimal import Decimal
from types import MappingProxyType

import pytest

from struct_audit import (
    BoundaryGuard,
    BoundaryViolation,
    DAGRouter,
    EvidenceLevel,
    ExecutionReport,
    ResidualError,
    UnverifiedStateError,
    WorkingPaper,
    close_run,
    node,
)


def _tax_run():
    paper = WorkingPaper()
    for key, value in {
        "营业收入": "1000.00",
        "营业成本": "600.00",
        "管理费用": "100.00",
        "所得税税率": "0.25",
    }.items():
        paper.commit_fact(key, Decimal(value))
    dag = {
        "毛利": node("SUB", "营业收入", "营业成本"),
        "利润总额": node("SUB", "毛利", "管理费用"),
        "当期所得税": node("TAX", "利润总额", "所得税税率"),
    }
    return paper, DAGRouter(paper).execute(dag)


def test_close_run_passes():
    paper, report = _tax_run()
    closure = close_run(
        paper,
        report,
        expected={
            "毛利": "400.00",
            "利润总额": "300.00",
            "当期所得税": "75.00",
        },
    )
    assert closure.closed is True
    assert closure.chain_ok is True
    assert closure.backward.passed is True
    assert len(closure.residuals) == 3
    assert any("恒真" in note for note in closure.notes)


def test_close_run_guard_rejects_wrong_role():
    paper, report = _tax_run()
    with pytest.raises(BoundaryViolation, match="角色"):
        close_run(paper, report, boundary_guard=BoundaryGuard(), actor_role="model")


def test_close_run_guard_rejects_low_evidence():
    paper, report = _tax_run()
    with pytest.raises(BoundaryViolation, match="证据"):
        close_run(
            paper, report, boundary_guard=BoundaryGuard(),
            actor_role="system", evidence=EvidenceLevel.ESTIMATED,
        )


def test_close_run_guard_allows_system_verified():
    paper, report = _tax_run()
    closure = close_run(paper, report, boundary_guard=BoundaryGuard())
    assert closure.closed is True


def test_independent_evidence_suppresses_hollow_closure_warning():
    paper, report = _tax_run()
    closure = close_run(paper, report, independent_evidence=True)
    assert not any("恒真" in note for note in closure.notes)


def test_close_run_detects_residual():
    paper, report = _tax_run()
    with pytest.raises(ResidualError):
        close_run(paper, report, expected={"当期所得税": "75.01"})


def test_empty_trace_is_idle_warning():
    paper = WorkingPaper()
    report = ExecutionReport(
        order=(),
        values=MappingProxyType({}),
        digest=paper.digest(),
        sealed=(),
        trace=(),
    )
    with pytest.raises(UnverifiedStateError, match="空转"):
        close_run(paper, report)


def test_report_from_other_state_rejected():
    _, report_a = _tax_run()
    paper_b, _ = _tax_run()
    paper_b.commit_fact("其他", Decimal("1.00"))  # 让两份底稿状态不同
    with pytest.raises(UnverifiedStateError, match="同一状态"):
        close_run(paper_b, report_a)


def test_tampered_paper_rejected():
    paper, report = _tax_run()
    paper._snapshots["当期所得税"] = Decimal("0.00")  # 模拟执行后改数
    with pytest.raises(UnverifiedStateError, match="链校验"):
        close_run(paper, report)
