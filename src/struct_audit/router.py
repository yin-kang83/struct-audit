# -*- coding: utf-8 -*-
"""状态快照与调度层（DAG Router）：按依赖顺序分发算子，执行前先做结构断言。

职责
  · 解析引用：DAG 节点 / 底稿事实 / 已封存快照（支持跨期递推：本期 DAG 引用上期快照）；
  · 拓扑排序：稳定顺序（同一 DAG 每次执行顺序一致，digest 可复现）+ 环检测；
  · 原子性：本次 DAG 全部节点计算并通过节点级后置断言后，才一次性封存快照；
    任一失败 → SystemCollapse，本次不落库，不会留下「半套数」。

引用写法
  node("SUB", "毛利", "管理费用")        # 字符串 = Ref（引用节点/事实/快照）
  node("DIFF_EQ", Ref("期初"), Const("1.10"), Ref("现金流出"))
  常量必须显式用 Const / Decimal / int；字符串永远当引用，避免「数字字符串被猜成常量」。
"""
from __future__ import annotations

import heapq
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Mapping, Sequence

from .audit import TraceStep
from .decimal_utils import ensure_decimal
from .errors import SystemCollapse
from .operators import REGISTRY, execute
from .watchdog import Check
from .working_paper import WorkingPaper


@dataclass(frozen=True)
class Ref:
    """引用：名字可以是本 DAG 节点、底稿事实或已封存快照。"""

    name: str

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("Ref 的名字必须是非空字符串")
        if "\x1f" in self.name:
            raise ValueError(f"Ref 名字不允许包含分隔符 \\x1f：{self.name!r}")


@dataclass(frozen=True)
class Const:
    """常量：只接受有限 Decimal（字符串/整数可转，float 直接拒）。"""

    value: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", ensure_decimal(self.value, field="常量"))


@dataclass(frozen=True)
class NodeSpec:
    """一个 DAG 节点：算子 + 参数引用 + 节点级后置断言。构造时即做前置校验。"""

    op: str
    args: tuple[Ref | Const, ...]
    checks: tuple[Check, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.op, str) or not self.op.strip():
            raise SystemCollapse("节点算子名必须是非空字符串")
        operator = REGISTRY.get(self.op)
        if operator is None:
            raise SystemCollapse(f"未注册的算子：{self.op!r}（可用：{', '.join(sorted(REGISTRY))}）")
        if not isinstance(self.args, tuple):
            raise SystemCollapse(f"节点 {self.op} 的 args 必须是 tuple")
        if not operator.accepts(len(self.args)):
            raise SystemCollapse(
                f"算子 {self.op} 需要 {operator.min_args}~{operator.max_args} 个参数，"
                f"实际 {len(self.args)} 个"
            )
        for arg in self.args:
            if not isinstance(arg, (Ref, Const)):
                raise SystemCollapse(f"节点 {self.op} 的参数类型非法：{type(arg).__name__}")
        if not isinstance(self.checks, tuple):
            raise SystemCollapse(f"节点 {self.op} 的 checks 必须是 tuple")
        for check in self.checks:
            if not isinstance(check, Check):
                raise SystemCollapse(f"节点 {self.op} 的后置断言类型非法：{type(check).__name__}")


def node(op: str, *args: object, checks: Sequence[Check] = ()) -> NodeSpec:
    """构造节点的便捷函数：str → Ref；int/Decimal → Const；float 拒绝。"""
    normalized: list[Ref | Const] = []
    for arg in args:
        if isinstance(arg, (Ref, Const)):
            normalized.append(arg)
        elif isinstance(arg, str):
            normalized.append(Ref(arg))
        elif isinstance(arg, bool):
            raise TypeError("节点常量不接受 bool")
        elif isinstance(arg, (int, Decimal)):
            normalized.append(Const(ensure_decimal(arg, field="节点常量")))
        elif isinstance(arg, float):
            raise TypeError(
                f"节点常量拒绝 float：{arg!r}；请用 Const('1.10') 或 Decimal('1.10')"
            )
        else:
            raise TypeError(f"节点参数类型不支持：{type(arg).__name__}")
    return NodeSpec(op=op, args=tuple(normalized), checks=tuple(checks))


@dataclass(frozen=True)
class ExecutionReport:
    """一次 DAG 执行的报告：执行顺序、本 DAG 节点值、执行后的底稿摘要、执行轨迹。"""

    order: tuple[str, ...]
    values: Mapping[str, Decimal]
    digest: str
    sealed: tuple[str, ...]
    trace: tuple[TraceStep, ...] = ()


class DAGRouter:
    """把一份 DAG 调度到底稿池上；同一实例可复用（跨期递推时复用同一本底稿）。"""

    def __init__(self, paper: WorkingPaper) -> None:
        if not isinstance(paper, WorkingPaper):
            raise TypeError("paper 必须是 WorkingPaper")
        self.paper = paper

    def execute(self, dag: Mapping[str, NodeSpec]) -> ExecutionReport:
        if not isinstance(dag, Mapping):
            raise SystemCollapse(f"dag 必须是 Mapping，得到 {type(dag).__name__}")
        if not dag:
            raise SystemCollapse("DAG 为空，拒绝执行")

        # ── 前置断言：节点名、类型、重名、引用完整性 ──────────────
        for name, spec in dag.items():
            if not isinstance(name, str) or not name.strip():
                raise SystemCollapse("DAG 节点名必须是非空字符串")
            if "\x1f" in name:
                raise SystemCollapse(f"DAG 节点名不允许包含分隔符 \\x1f：{name!r}")
            if not isinstance(spec, NodeSpec):
                raise SystemCollapse(f"节点「{name}」不是 NodeSpec")
            if self.paper.has_fact(name):
                raise SystemCollapse(f"节点名与已写死的事实重名：{name}")
            if self.paper.has_snapshot(name):
                raise SystemCollapse(f"节点名与已封存的快照重名：{name}（底稿写一次）")
        self._check_references(dag)

        order = self._topo_order(dag)

        # ── 计算：先全部算完并通过节点级后置断言 ──────────────────
        local: dict[str, Decimal] = {}
        trace: list[TraceStep] = []
        for name in order:
            spec = dag[name]
            values = [self._resolve(arg, dag, local) for arg in spec.args]
            result = execute(spec.op, values)
            for check in spec.checks:
                check.apply(name, result)
            local[name] = result
            trace.append(TraceStep(node=name, op=spec.op, args=tuple(values), result=result))

        # ── 原子封存：到这里才允许落库 ────────────────────────────
        sealed: list[str] = []
        for name in order:
            self.paper.seal_snapshot(name, local[name])
            sealed.append(name)

        return ExecutionReport(
            order=order,
            values=MappingProxyType(dict(local)),
            digest=self.paper.digest(),
            sealed=tuple(sealed),
            trace=tuple(trace),
        )

    # ── 内部 ─────────────────────────────────────────────────────
    def _check_references(self, dag: Mapping[str, NodeSpec]) -> None:
        missing: list[str] = []
        for name, spec in dag.items():
            for arg in spec.args:
                if isinstance(arg, Ref) and arg.name not in dag:
                    if not (self.paper.has_fact(arg.name) or self.paper.has_snapshot(arg.name)):
                        missing.append(f"{name} → {arg.name}")
        if missing:
            raise SystemCollapse(
                "DAG 引用了不存在的节点/事实/快照：" + "；".join(sorted(set(missing)))
            )

    def _topo_order(self, dag: Mapping[str, NodeSpec]) -> tuple[str, ...]:
        order_index = {name: index for index, name in enumerate(dag)}
        deps: dict[str, list[str]] = {}
        for name, spec in dag.items():
            seen = {
                arg.name
                for arg in spec.args
                if isinstance(arg, Ref) and arg.name in dag
            }
            deps[name] = sorted(seen, key=order_index.__getitem__)

        indegree = {name: len(node_deps) for name, node_deps in deps.items()}
        children: dict[str, list[str]] = defaultdict(list)
        for name, node_deps in deps.items():
            for dep in node_deps:
                children[dep].append(name)

        ready = [(order_index[name], name) for name in dag if indegree[name] == 0]
        heapq.heapify(ready)
        order: list[str] = []
        while ready:
            _, name = heapq.heappop(ready)
            order.append(name)
            for child in sorted(children[name], key=order_index.__getitem__):
                indegree[child] -= 1
                if indegree[child] == 0:
                    heapq.heappush(ready, (order_index[child], child))

        if len(order) != len(dag):
            stuck = sorted(
                (name for name in dag if indegree[name] > 0), key=order_index.__getitem__
            )
            raise SystemCollapse(f"DAG 存在环，拒绝执行（涉及节点：{stuck}）")
        return tuple(order)

    def _resolve(
        self, arg: Ref | Const, dag: Mapping[str, NodeSpec], local: Mapping[str, Decimal]
    ) -> Decimal:
        if isinstance(arg, Const):
            return arg.value
        name = arg.name
        if name in dag:
            if name not in local:
                raise SystemCollapse(f"节点「{name}」尚未计算（拓扑排序异常）")
            return local[name]
        if self.paper.has_fact(name):
            return self.paper.fact(name)
        if self.paper.has_snapshot(name):
            return self.paper.snapshot(name)
        # _check_references 已拦住，这里只是双保险
        raise SystemCollapse(f"引用不存在：{name}")
