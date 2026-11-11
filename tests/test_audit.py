# -*- coding: utf-8 -*-
"""反向印证：可逆算子能反解回来，不可逆算子明示跳过，轨迹被改即失败。"""
from decimal import Decimal

import pytest

from struct_audit import DAGRouter, TraceStep, WorkingPaper, node, verify_backward


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


def test_router_records_trace():
    _, report = _tax_run()
    assert [step.node for step in report.trace] == ["毛利", "利润总额", "当期所得税"]
    assert [step.op for step in report.trace] == ["SUB", "SUB", "TAX"]
    assert report.trace[-1].result == Decimal("75.00")


def test_backward_passes_for_invertible_steps():
    _, report = _tax_run()
    audit = verify_backward(report.trace)
    assert audit.checked_count == 2  # 两个 SUB 可反解
    assert audit.passed is True
    assert audit.max_abs_residual == Decimal("0")


def test_non_invertible_step_is_skipped_not_faked():
    _, report = _tax_run()
    audit = verify_backward(report.trace)
    assert len(audit.skipped) == 1
    skipped = audit.skipped[0]
    assert skipped.op == "TAX"
    assert skipped.checked is False
    assert "不可唯一反解" in skipped.reason


def test_tampered_trace_is_detected():
    good = TraceStep("x", "ADD", (Decimal("100.00"), Decimal("5.00")), Decimal("105.00"))
    assert verify_backward((good,)).passed is True

    bad = TraceStep("x", "ADD", (Decimal("100.00"), Decimal("5.00")), Decimal("999.00"))
    audit = verify_backward((bad,))
    assert audit.passed is False
    assert audit.max_abs_residual is not None and audit.max_abs_residual > Decimal("0.01")


def test_only_invertible_ops_are_checked():
    steps = (
        TraceStep("a", "ADD", (Decimal("1.00"), Decimal("2.00")), Decimal("3.00")),
        TraceStep("s", "SUM", (Decimal("1.00"), Decimal("2.00")), Decimal("3.00")),
    )
    audit = verify_backward(steps)
    assert audit.checked_count == 1
    assert len(audit.skipped) == 1
    assert audit.passed is True


def test_no_invertible_steps_is_not_passed():
    steps = (TraceStep("s", "SUM", (Decimal("1.00"), Decimal("2.00")), Decimal("3.00")),)
    audit = verify_backward(steps)
    assert audit.checked_count == 0
    assert audit.passed is False


def test_trace_step_validates_types():
    with pytest.raises(TypeError):
        TraceStep("x", "ADD", [Decimal("1.00")], Decimal("1.00"))  # type: ignore[arg-type]
