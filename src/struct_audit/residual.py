# -*- coding: utf-8 -*-
"""残差归位（图 1/2/4 的可执行版本）。

概念到代码
  · 残差（ε）= 实际值 − 期望值。会计里它可以是尾差、借贷差额、未分摊部分。
  · 环规校验：残差必须被断言吃掉；默认严格为零，**不允许静默取整放行**。
  · 残差归位：只有显式策略允许、且残差确实是尾差（≤1 分）时，才把差额
    写入底稿容器的「尾差归位」科目，并留下哈希链记录；否则抛出 ResidualError。

与「参谋长」版代码的差别
  · 不内置 0.001 之类的浮点容差（金额到分，容差必须显式）；
  · 归位不是把残差「抹掉」，而是作为一笔可审计的调整写进容器；
  · 默认拒绝（Fail-Closed），不是默认吸收。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping

from .decimal_utils import CENT, ensure_decimal
from .errors import ResidualError, UnverifiedStateError
from .working_paper import WorkingPaper

ZERO = Decimal("0")


@dataclass(frozen=True)
class Residual:
    """一条残差记录：ε = actual − expected。"""

    key: str
    expected: Decimal
    actual: Decimal
    tolerance: Decimal = ZERO

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or not self.key.strip():
            raise ValueError("残差键必须是非空字符串")
        object.__setattr__(self, "key", self.key.strip())
        object.__setattr__(self, "expected", ensure_decimal(self.expected, field="期望值"))
        object.__setattr__(self, "actual", ensure_decimal(self.actual, field="实际值"))
        tolerance = ensure_decimal(self.tolerance, field="容差")
        if tolerance < 0:
            raise ValueError("容差不能为负")
        object.__setattr__(self, "tolerance", tolerance)

    @property
    def difference(self) -> Decimal:
        return self.actual - self.expected

    @property
    def within_tolerance(self) -> bool:
        return abs(self.difference) <= self.tolerance

    def describe(self) -> str:
        return (
            f"{self.key}: 期望 {self.expected}，实际 {self.actual}，"
            f"残差 {self.difference}（容差 {self.tolerance}）"
        )


def residual_of(
    key: str, actual: object, expected: object, *, tolerance: object = ZERO
) -> Residual:
    """构造一条残差（不抛异常；是否通过由调用方判定）。"""
    return Residual(
        key=key,
        expected=ensure_decimal(expected, field=f"{key} 期望值"),
        actual=ensure_decimal(actual, field=f"{key} 实际值"),
        tolerance=ensure_decimal(tolerance, field="容差"),
    )


def require_zero(residual: Residual) -> None:
    """环规校验：残差不为零即崩。"""
    if not residual.within_tolerance:
        raise ResidualError(f"残差未归位：{residual.describe()}")


@dataclass(frozen=True)
class ResidualPolicy:
    """残差处理策略。默认严格零容差、不允许吸收尾差。"""

    tolerance: Decimal = ZERO
    allow_rounding: bool = False
    rounding_account: str = "尾差归位"

    def __post_init__(self) -> None:
        tolerance = ensure_decimal(self.tolerance, field="容差")
        if tolerance < 0:
            raise ValueError("容差不能为负")
        if self.allow_rounding and tolerance > CENT:
            raise ValueError("允许尾差归位时，容差不得超过 1 分（0.01）")
        if not isinstance(self.rounding_account, str) or not self.rounding_account.strip():
            raise ValueError("归位科目必须是非空字符串")
        object.__setattr__(self, "tolerance", tolerance)
        object.__setattr__(self, "rounding_account", self.rounding_account.strip())


class ResidualBook:
    """一组残差的登记与结算：全部归位才算闭环。"""

    def __init__(self, paper: WorkingPaper, *, policy: ResidualPolicy | None = None) -> None:
        if not isinstance(paper, WorkingPaper):
            raise TypeError("paper 必须是 WorkingPaper")
        self.paper = paper
        self.policy = policy or ResidualPolicy()
        self._items: list[Residual] = []

    @property
    def items(self) -> tuple[Residual, ...]:
        return tuple(self._items)

    def expect(self, key: str, expected: object, *, actual: object | None = None) -> Residual:
        """登记一条期望值；actual 不传时从底稿里取（快照优先，其次事实）。"""
        if actual is None:
            if self.paper.has_snapshot(key):
                actual = self.paper.snapshot(key)
            elif self.paper.has_fact(key):
                actual = self.paper.fact(key)
            else:
                raise UnverifiedStateError(
                    f"残差校验失败：底稿里没有「{key}」（未闭环的状态不能参与终审）"
                )
        item = residual_of(key, actual, expected, tolerance=self.policy.tolerance)
        self._items.append(item)
        return item

    def settle(self) -> tuple[Residual, ...]:
        """结算：全部通过则返回；否则按策略尝试尾差归位，最后仍不通过就崩。"""
        failures = [item for item in self._items if not item.within_tolerance]
        if not failures:
            return tuple(self._items)

        if self.policy.allow_rounding and all(
            abs(item.difference) <= CENT for item in failures
        ):
            total = sum((item.difference for item in failures), ZERO)
            # 尾差归位：把差额作为一笔可审计调整写进容器，而不是悄悄抹掉
            self.paper.seal_snapshot(self.policy.rounding_account, total)
            return tuple(self._items)

        raise ResidualError(
            "残差未归位（拒绝静默取整）：" + "；".join(item.describe() for item in failures)
        )


# 兼容常见的「对一组值做残差校验」写法
def check_residuals(
    paper: WorkingPaper,
    expected: Mapping[str, object],
    *,
    tolerance: object = ZERO,
    policy: ResidualPolicy | None = None,
) -> tuple[Residual, ...]:
    """一次性登记并结算一组残差；返回全部残差记录。"""
    active_policy = policy or ResidualPolicy(tolerance=ensure_decimal(tolerance, field="容差"))
    book = ResidualBook(paper, policy=active_policy)
    for key, value in expected.items():
        book.expect(key, value)
    return book.settle()
