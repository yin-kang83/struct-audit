# -*- coding: utf-8 -*-
"""确定性计算层：最小算子库。

所有算子只做 Decimal 四则运算与两个业务特化（税额、一阶差分），输出按分
ROUND_HALF_UP 取整。计算层不读自然语言、不调模型、没有随机性。

前置断言（在「算」之前就拒）
  · execute 层：算子必须已注册、参数个数正确、入参必须是有限 Decimal（float 直接拒）；
  · 业务算子：除数为零、税基为负、税率越界、λ<=0、资产余额推演为负，全部 SystemCollapse。

后置断言（在「算之后」再验）见 watchdog.py；本模块只保证单步结果合法。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Sequence

from .decimal_utils import ensure_decimal, q
from .errors import FinAgentError, SystemCollapse

Handler = Callable[..., Decimal]


@dataclass(frozen=True)
class Operator:
    """一个原子算子：名字、参数个数范围、纯函数实现。"""

    name: str
    min_args: int
    max_args: int
    fn: Handler
    doc: str

    def accepts(self, count: int) -> bool:
        return self.min_args <= count <= self.max_args


REGISTRY: dict[str, Operator] = {}


def register(name: str, *, min_args: int, max_args: int | None = None, doc: str = ""):
    """注册算子；同名重复注册直接崩塌（防止被静默覆盖）。"""

    def decorator(fn: Handler) -> Handler:
        if name in REGISTRY:
            raise SystemCollapse(f"算子重复注册：{name}")
        if min_args < 0 or (max_args is not None and max_args < min_args):
            raise SystemCollapse(f"算子 {name} 的参数个数范围非法")
        REGISTRY[name] = Operator(name, min_args, max_args if max_args is not None else min_args, fn, doc)
        return fn

    return decorator


# ── 四则与聚合 ──────────────────────────────────────────────────
@register("ADD", min_args=2, max_args=2, doc="x + y")
def _add(x: Decimal, y: Decimal) -> Decimal:
    return x + y


@register("SUB", min_args=2, max_args=2, doc="x - y")
def _sub(x: Decimal, y: Decimal) -> Decimal:
    return x - y


@register("MUL", min_args=2, max_args=2, doc="x × y")
def _mul(x: Decimal, y: Decimal) -> Decimal:
    return x * y


@register("DIV", min_args=2, max_args=2, doc="x ÷ y")
def _div(x: Decimal, y: Decimal) -> Decimal:
    if y == 0:
        raise SystemCollapse("前置断言失败：除数为 0")
    return x / y


@register("SUM", min_args=1, max_args=999, doc="Σ args（任意个参数）")
def _sum(*args: Decimal) -> Decimal:
    total = Decimal("0")
    for value in args:
        total += value
    return total


@register("NEG", min_args=1, max_args=1, doc="-x")
def _neg(x: Decimal) -> Decimal:
    return -x


@register("MAX", min_args=2, max_args=2, doc="max(x, y)")
def _max(x: Decimal, y: Decimal) -> Decimal:
    return max(x, y)


@register("MIN", min_args=2, max_args=2, doc="min(x, y)")
def _min(x: Decimal, y: Decimal) -> Decimal:
    return min(x, y)


# ── 业务特化 ────────────────────────────────────────────────────
@register("TAX", min_args=2, max_args=3, doc="税额 = (税基 − 扣除额) × 税率")
def _tax(base: Decimal, rate: Decimal, deductible: Decimal = Decimal("0")) -> Decimal:
    if base < 0:
        raise SystemCollapse(f"前置断言失败：税基不可为负，得到 {base}")
    if not (Decimal("0") <= rate <= Decimal("1")):
        raise SystemCollapse(f"前置断言失败：税率必须在 [0, 1]，得到 {rate}")
    if deductible < 0:
        raise SystemCollapse(f"前置断言失败：扣除额不可为负，得到 {deductible}")
    taxable = base - deductible
    if taxable < 0:
        raise SystemCollapse(f"前置断言失败：扣除额 {deductible} 超过税基 {base}")
    return taxable * rate


@register("DIFF_EQ", min_args=3, max_args=3, doc="一阶差分：A_t = λ·A_(t−1) + θ")
def _diff_eq(a_prev: Decimal, lam: Decimal, theta: Decimal) -> Decimal:
    if lam <= 0:
        raise SystemCollapse(f"前置断言失败：递推系数 λ 必须 > 0，得到 {lam}")
    next_a = lam * a_prev + theta
    if next_a < 0:
        raise SystemCollapse(f"后置断言失败：资产推演出现负数（{next_a}），违反商业实质")
    return next_a


# ── 统一入口 ────────────────────────────────────────────────────
def execute(name: str, args: Sequence[object]) -> Decimal:
    """按名字执行算子：先做前置断言，再计算，最后按分取整。"""
    operator = REGISTRY.get(name)
    if operator is None:
        raise SystemCollapse(f"未注册的算子：{name!r}（可用：{', '.join(operator_names())}）")
    if isinstance(args, (str, bytes)) or not isinstance(args, Sequence):
        raise SystemCollapse(f"算子 {name} 的入参必须是序列，得到 {type(args).__name__}")
    if not operator.accepts(len(args)):
        raise SystemCollapse(
            f"算子 {name} 需要 {operator.min_args}~{operator.max_args} 个参数，实际 {len(args)} 个"
        )

    decimals: list[Decimal] = []
    for index, arg in enumerate(args, start=1):
        try:
            decimals.append(ensure_decimal(arg, field=f"算子 {name} 第 {index} 个入参"))
        except (TypeError, ValueError) as exc:
            raise SystemCollapse(f"算子 {name} 前置断言失败：{exc}") from exc

    try:
        result = operator.fn(*decimals)
    except FinAgentError:
        raise
    except (ArithmeticError, ValueError, TypeError) as exc:
        raise SystemCollapse(f"算子 {name} 执行失败：{exc}") from exc

    if not isinstance(result, Decimal):
        raise SystemCollapse(f"算子 {name} 返回值不是 Decimal：{type(result).__name__}")
    return q(result)


def operator_names() -> tuple[str, ...]:
    return tuple(sorted(REGISTRY))
