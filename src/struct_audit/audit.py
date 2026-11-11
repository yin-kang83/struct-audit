# -*- coding: utf-8 -*-
"""反向印证（图 1「来去印证」/ 图 3「本体闭环」的可执行版本）。

前向：数 → 术 → 结构（DAG 计算 + 底稿封存）。
反向：结构 → 术 → 数（沿算子逆运算把输入解回来，与底稿记录比对）。

诚实边界
  · ADD / SUB / NEG 可精确反解；
  · MUL / DIV / DIFF_EQ 受「按分取整」影响，反解残差用 1 分容差；
  · SUM / MAX / MIN / TAX 不可唯一反解（多参数、取整、非单射），
    显式标记为 skipped，**不假装通过**。
反向印证只能证明「结果与记录一致」，不能证明「口径/分类正确」——后者仍是 L5 判断层。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from .decimal_utils import CENT, ensure_decimal

ZERO = Decimal("0")


@dataclass(frozen=True)
class TraceStep:
    """DAG 执行轨迹中的一步：节点、算子、实际入参、输出。"""

    node: str
    op: str
    args: tuple[Decimal, ...]
    result: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.node, str) or not self.node.strip():
            raise ValueError("TraceStep.node 必须是非空字符串")
        if not isinstance(self.op, str) or not self.op.strip():
            raise ValueError("TraceStep.op 必须是非空字符串")
        if not isinstance(self.args, tuple):
            raise TypeError("TraceStep.args 必须是 tuple")
        object.__setattr__(self, "args", tuple(ensure_decimal(arg, field="轨迹入参") for arg in self.args))
        object.__setattr__(self, "result", ensure_decimal(self.result, field="轨迹输出"))


@dataclass(frozen=True)
class BackwardCheck:
    """一个节点的反解结果。checked=False 表示该算子不可唯一反解。"""

    node: str
    op: str
    checked: bool
    recovered: Decimal | None = None
    recorded: Decimal | None = None
    residual: Decimal | None = None
    reason: str = ""

    def describe(self) -> str:
        if not self.checked:
            return f"{self.node}（{self.op}）跳过：{self.reason}"
        return (
            f"{self.node}（{self.op}）反解 {self.recovered} vs 记录 {self.recorded}，"
            f"残差 {self.residual}"
        )


@dataclass(frozen=True)
class BackwardAudit:
    """一次反向印证的总报告。"""

    checks: tuple[BackwardCheck, ...]
    tolerance: Decimal = CENT

    def __post_init__(self) -> None:
        tolerance = ensure_decimal(self.tolerance, field="容差")
        if tolerance < 0:
            raise ValueError("容差不能为负")
        object.__setattr__(self, "tolerance", tolerance)

    @property
    def checked_count(self) -> int:
        return sum(1 for check in self.checks if check.checked)

    @property
    def skipped(self) -> tuple[BackwardCheck, ...]:
        return tuple(check for check in self.checks if not check.checked)

    @property
    def max_abs_residual(self) -> Decimal | None:
        values = [abs(check.residual) for check in self.checks if check.checked and check.residual is not None]
        return max(values) if values else None

    @property
    def passed(self) -> bool:
        if self.checked_count == 0:
            return False
        return all(
            check.residual is not None and abs(check.residual) <= self.tolerance
            for check in self.checks
            if check.checked
        )

    def describe(self) -> str:
        return (
            f"反向印证：{self.checked_count} 步可反解、{len(self.skipped)} 步跳过；"
            f"最大残差 {self.max_abs_residual}（容差 {self.tolerance}）；"
            f"{'通过' if self.passed else '不通过'}"
        )


def _invert(step: TraceStep) -> tuple[int, Decimal] | None:
    """返回 (要反解的入参下标, 反解值)；不可唯一反解返回 None。"""
    op, args, result = step.op, step.args, step.result
    try:
        if op == "ADD" and len(args) == 2:
            return 0, result - args[1]
        if op == "SUB" and len(args) == 2:
            return 0, result + args[1]
        if op == "NEG" and len(args) == 1:
            return 0, -result
        if op == "MUL" and len(args) == 2:
            return (0, result / args[1]) if args[1] != 0 else None
        if op == "DIV" and len(args) == 2:
            return 0, result * args[1]
        if op == "DIFF_EQ" and len(args) == 3:
            return (0, (result - args[2]) / args[1]) if args[1] != 0 else None
    except (ArithmeticError, IndexError):
        return None
    return None


def verify_backward(
    steps: Sequence[TraceStep], *, tolerance: object = CENT
) -> BackwardAudit:
    """沿执行轨迹做反向印证；返回报告（不抛异常，由调用方决定是否崩）。"""
    checks: list[BackwardCheck] = []
    for index, step in enumerate(steps, start=1):
        if not isinstance(step, TraceStep):
            raise TypeError(f"第 {index} 条轨迹不是 TraceStep")
        inverted = _invert(step)
        if inverted is None:
            checks.append(
                BackwardCheck(
                    node=step.node,
                    op=step.op,
                    checked=False,
                    reason=f"{step.op} 不可唯一反解（多参数/取整/非单射），不假装通过",
                )
            )
            continue
        arg_index, recovered = inverted
        recorded = step.args[arg_index]
        checks.append(
            BackwardCheck(
                node=step.node,
                op=step.op,
                checked=True,
                recovered=recovered,
                recorded=recorded,
                residual=recovered - recorded,
            )
        )
    return BackwardAudit(checks=tuple(checks), tolerance=ensure_decimal(tolerance, field="容差"))
