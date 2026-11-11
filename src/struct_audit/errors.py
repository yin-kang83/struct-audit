# -*- coding: utf-8 -*-
"""Fin-Agent 的失败语义：只有两种出口——拒（RejectedInput）与违（SystemCollapse）。

对应「四态」纪律（成 / 拒 / 误 / 违）里的两种失败：

  · 拒：输入不满足前置断言（抽取格式、事实缺失、金额精度、利率区间），
        系统不执行，责任在上游。Fail-Closed，不降级、不猜。
  · 违：系统内部契约被破坏（算子前置条件、DAG 结构、后置断言、底稿链），
        立即崩塌并中止，绝不「算一个差不多的数」交出去。

本模块不提供第三种出口。所有异常都继承 FinAgentError，便于上层统一拦截。
"""
from __future__ import annotations


class FinAgentError(Exception):
    """本仓所有可预期失败的基类。"""


class RejectedInput(FinAgentError):
    """拒：输入不满足前置断言，Fail-Closed。"""


class ExtractionRejected(RejectedInput):
    """拒：LLM 抽取结果不满足强类型契约。"""


class SystemCollapse(FinAgentError):
    """违：系统内部断言失败，立即中断（不降级、不重试掩盖、不输出金额）。"""


class WorkingPaperSealed(SystemCollapse):
    """违：试图覆盖已写死的事实/快照（底稿写一次，不可改）。"""


class TamperDetected(SystemCollapse):
    """违：哈希链或底稿状态与链不一致，疑似篡改。"""


class ResidualError(SystemCollapse):
    """违：残差不为零（反向印证/勾稽/尾差归位失败）。"""


class UnverifiedStateError(SystemCollapse):
    """违：试图使用未闭环、未验证的状态（没有环外终审）。"""
