# -*- coding: utf-8 -*-
"""残差归位：默认严格零容差，只有显式策略才允许吸收 1 分尾差并留痕。"""
from decimal import Decimal

import pytest

from struct_audit import (
    ResidualBook,
    ResidualError,
    ResidualPolicy,
    UnverifiedStateError,
    WorkingPaper,
    require_zero,
    residual_of,
)


def _paper() -> WorkingPaper:
    paper = WorkingPaper()
    paper.seal_snapshot("利润总额", Decimal("300.00"))
    return paper


def test_residual_zero_passes():
    residual = residual_of("利润总额", "300.00", "300.00")
    assert residual.within_tolerance is True
    require_zero(residual)


def test_residual_is_strict_by_default():
    residual = residual_of("利润总额", "300.00", "300.01")
    assert residual.within_tolerance is False
    with pytest.raises(ResidualError, match="残差未归位"):
        require_zero(residual)


def test_residual_explicit_tolerance():
    residual = residual_of("利润总额", "300.00", "300.01", tolerance="0.01")
    assert residual.within_tolerance is True


def test_book_settle_passes_when_exact():
    paper = _paper()
    book = ResidualBook(paper)
    book.expect("利润总额", "300.00")
    items = book.settle()
    assert len(items) == 1
    assert paper.verify().ok is True


def test_book_rejects_missing_key():
    book = ResidualBook(WorkingPaper())
    with pytest.raises(UnverifiedStateError, match="未闭环"):
        book.expect("不存在的节点", "1.00")


def test_book_settle_rejects_residual_by_default():
    paper = _paper()
    book = ResidualBook(paper)
    book.expect("利润总额", "300.01")
    with pytest.raises(ResidualError, match="拒绝静默取整"):
        book.settle()


def test_book_absorbs_one_cent_rounding_with_explicit_policy():
    paper = _paper()
    policy = ResidualPolicy(allow_rounding=True)
    book = ResidualBook(paper, policy=policy)
    book.expect("利润总额", "300.01")
    book.settle()
    assert paper.has_snapshot("尾差归位")
    assert paper.snapshot("尾差归位") == Decimal("-0.01")
    assert paper.verify().ok is True


def test_book_rejects_more_than_one_cent_even_with_policy():
    paper = _paper()
    policy = ResidualPolicy(allow_rounding=True)
    book = ResidualBook(paper, policy=policy)
    book.expect("利润总额", "300.02")
    with pytest.raises(ResidualError):
        book.settle()


def test_policy_rejects_rounding_tolerance_larger_than_cent():
    with pytest.raises(ValueError, match="1 分"):
        ResidualPolicy(tolerance=Decimal("0.02"), allow_rounding=True)


def test_residual_rejects_bad_tolerance():
    with pytest.raises(ValueError, match="容差"):
        residual_of("x", "1.00", "1.00", tolerance="-0.01")
