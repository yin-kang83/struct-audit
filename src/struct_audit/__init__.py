# -*- coding: utf-8 -*-
"""struct_audit —— 结构审计：残差归位、反向审计、证据分层与关闭。

从 accounting-domain 的 accounting_core 抽取的结构审计内核（MIT 开源版）。
只包含审计相关的最小实现；业务数据、题库、论文正文不随本仓分发。
"""
from __future__ import annotations

from .audit import BackwardAudit, BackwardCheck, TraceStep, verify_backward
from .closure import ClosureReport, close_run
from .decimal_utils import CENT, ensure_decimal, money_str, parse_money, parse_rate, q
from .errors import ResidualError, UnverifiedStateError
from .guards import BoundaryGuard, BoundaryViolation, EvidenceLevel
from .residual import Residual, ResidualBook, ResidualPolicy, check_residuals, require_zero, residual_of
from .router import DAGRouter, ExecutionReport, node
from .sod import Actor, SignRecord, SoDGuard, SoDViolation, assert_sod, make_record
from .working_paper import WorkingPaper

__all__ = [
    "BackwardAudit", "BackwardCheck", "TraceStep", "verify_backward",
    "ClosureReport", "close_run",
    "CENT", "ensure_decimal", "money_str", "parse_money", "parse_rate", "q",
    "ResidualError", "UnverifiedStateError",
    "BoundaryGuard", "BoundaryViolation", "EvidenceLevel",
    "node",
    "Residual", "ResidualBook", "ResidualPolicy", "check_residuals", "require_zero", "residual_of",
    "DAGRouter", "ExecutionReport",
    "Actor", "SignRecord", "SoDGuard", "SoDViolation", "assert_sod", "make_record",
    "WorkingPaper",
]
