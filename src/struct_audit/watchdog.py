# -*- coding: utf-8 -*-
"""安全边界层的后置断言：算完之后再验，不过即崩（不交付）。

两套用法
  1. 节点级：`Check.non_negative()` / `Check.at_most(limit)` / `Check.zero()`，
     挂在 NodeSpec 上，由 DAGRouter 在封存快照之前执行（失败则该次 DAG 不落库）；
  2. 场景级：`Watchdog.balanced(...)` / `Watchdog.identity(...)` / `Watchdog.zero(...)`，
     在整份结果出来后执行——此时底稿已留痕，但「不交付」。

「借贷必平」用严格相等（金额都已到分），不给容差放水。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import ClassVar, Sequence

from .decimal_utils import ensure_decimal
from .errors import SystemCollapse

_KINDS: tuple[str, ...] = ("non_negative", "at_most", "zero")


@dataclass(frozen=True)
class Check:
    """节点级后置断言。默认严格零容差：容差必须显式给出，防止「少一分也算过」。"""

    kind: str
    limit: Decimal | None = None
    tolerance: Decimal = Decimal("0")
    _VALID: ClassVar[tuple[str, ...]] = _KINDS

    def __post_init__(self) -> None:
        if self.kind not in self._VALID:
            raise ValueError(f"未知的检查类型：{self.kind!r}")
        if self.kind == "at_most":
            if self.limit is None:
                raise ValueError("at_most 必须提供 limit")
            object.__setattr__(self, "limit", ensure_decimal(self.limit, field="上限"))
        elif self.limit is not None:
            raise ValueError(f"检查类型 {self.kind} 不接受 limit")
        object.__setattr__(self, "tolerance", ensure_decimal(self.tolerance, field="容差"))
        if self.tolerance < 0:
            raise ValueError("容差不能为负")

    @classmethod
    def non_negative(cls) -> "Check":
        return cls("non_negative")

    @classmethod
    def at_most(cls, limit: object) -> "Check":
        return cls("at_most", ensure_decimal(limit, field="上限"))

    @classmethod
    def zero(cls, tolerance: object = Decimal("0")) -> "Check":
        return cls("zero", None, ensure_decimal(tolerance, field="容差"))

    def apply(self, node: str, value: Decimal) -> None:
        decimal = ensure_decimal(value, field=f"节点「{node}」结果")
        if self.kind == "non_negative":
            if decimal < 0:
                raise SystemCollapse(
                    f"后置断言失败：节点「{node}」为负数（{decimal}），违反商业实质"
                )
        elif self.kind == "at_most":
            if self.limit is None:  # 构造时已校验，这里是双保险：宁可崩，不静默跳过
                raise SystemCollapse(f"内部错误：at_most 断言缺少上限（节点「{node}」）")
            if decimal > self.limit:
                raise SystemCollapse(
                    f"后置断言失败：节点「{node}」={decimal} 超过上限 {self.limit}"
                )
        elif self.kind == "zero":
            if abs(decimal) > self.tolerance:
                raise SystemCollapse(
                    f"后置断言失败：节点「{node}」应为 0，实际 {decimal}（容差 {self.tolerance}）"
                )
        else:  # 构造时已校验；万一被绕过，也必须崩而不是静默通过
            raise SystemCollapse(f"内部错误：未知的检查类型 {self.kind!r}（节点「{node}」）")


class Watchdog:
    """场景级后置断言。"""

    @staticmethod
    def non_negative(label: str, value: object) -> None:
        decimal = ensure_decimal(value, field=label)
        if decimal < 0:
            raise SystemCollapse(f"后置断言失败：{label} 为负数（{decimal}）")

    @staticmethod
    def zero(label: str, value: object, tolerance: object = Decimal("0")) -> None:
        decimal = ensure_decimal(value, field=label)
        allowed = ensure_decimal(tolerance, field="容差")
        if abs(decimal) > allowed:
            raise SystemCollapse(
                f"后置断言失败：{label} 应为 0，实际 {decimal}（容差 {allowed}）"
            )

    @staticmethod
    def identity(label: str, left: object, right: object,
                 tolerance: object = Decimal("0")) -> None:
        a = ensure_decimal(left, field=f"{label} 左侧")
        b = ensure_decimal(right, field=f"{label} 右侧")
        allowed = ensure_decimal(tolerance, field="容差")
        if abs(a - b) > allowed:
            raise SystemCollapse(
                f"后置断言失败：{label} 不勾稽：{a} vs {b}，差 {a - b}（容差 {allowed}）"
            )

    @staticmethod
    def balanced(label: str, debits: Sequence[object], credits: Sequence[object]) -> None:
        debit_total = _sum_amounts(debits, label=f"{label} 借方")
        credit_total = _sum_amounts(credits, label=f"{label} 贷方")
        if debit_total != credit_total:
            raise SystemCollapse(
                f"后置断言失败：{label}借贷不平：借 {debit_total} vs 贷 {credit_total}，"
                f"差 {debit_total - credit_total}"
            )


def _sum_amounts(values: Sequence[object], *, label: str) -> Decimal:
    total = Decimal("0")
    for index, value in enumerate(values, start=1):
        total += ensure_decimal(value, field=f"{label} 第 {index} 项")
    return total
