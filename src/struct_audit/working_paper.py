# -*- coding: utf-8 -*-
"""状态快照层（Working Paper）：事实写死、快照写一次、追加式哈希链留痕。

三条纪律
  1. 事实（fact）与快照（snapshot）都「写一次」：重复写直接 WorkingPaperSealed；
  2. 每次写入都追加一个哈希块：块内容 = 前一块哈希 + 本次内容，改一字即断链；
  3. verify() 同时核对「链本身」与「当前状态是否等于链重放结果」，
     因此改内存里的 dict 也会被发现。

边界（写进代码注释，也写进 README，避免过度声称）
  · 哈希链只能「检测」篡改，不能阻止重算：整条链从头重算并更新锚点，链内仍然自洽；
  · 无外锚点时，链尾被整段删除无法发现（verify() 会照常通过）——
    这是链式结构的固有局限，必须用 verify_against_anchor(expected_len, expected_head)。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Mapping

from .decimal_utils import ensure_decimal
from .errors import TamperDetected, WorkingPaperSealed

GENESIS = "0" * 64


def _block_hash(seq: int, kind: str, key: str, value: str, prev_hash: str) -> str:
    payload = f"{seq}\x1f{kind}\x1f{key}\x1f{value}\x1f{prev_hash}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class Block:
    """一个追加式哈希块。"""

    seq: int
    kind: str          # "fact" | "snapshot"
    key: str
    value: str         # Decimal 的十进制字符串表示
    prev_hash: str
    hash: str

    def to_dict(self) -> dict[str, object]:
        return {
            "seq": self.seq,
            "kind": self.kind,
            "key": self.key,
            "value": self.value,
            "prev_hash": self.prev_hash,
            "hash": self.hash,
        }


@dataclass(frozen=True)
class VerifyReport:
    """链校验结果。anchor_checked=False 表示「只做了链内一致性检查」。"""

    ok: bool
    reason: str
    blocks: int
    head: str
    anchor_checked: bool = False


class WorkingPaper:
    """一本可审计的底稿：facts + snapshots + 追加式哈希链。"""

    def __init__(self) -> None:
        self._facts: dict[str, Decimal] = {}
        self._snapshots: dict[str, Decimal] = {}
        self._blocks: list[Block] = []

    # ── 写入（全部写一次）──────────────────────────────────────────
    def commit_fact(self, key: str, value: object) -> None:
        """写死一个事实。事实不可覆盖，也不与快照重名。"""
        self._validate_key(key)
        if key in self._facts:
            raise WorkingPaperSealed(f"事实「{key}」已写死，拒绝覆盖（底稿写一次）")
        if key in self._snapshots:
            raise WorkingPaperSealed(f"「{key}」已是快照，不能再作为事实写入")
        decimal = ensure_decimal(value, field=f"事实「{key}」")
        self._facts[key] = decimal
        self._append("fact", key, decimal)

    def seal_snapshot(self, key: str, value: object) -> None:
        """封存一个计算结果快照。快照同样不可覆盖。"""
        self._validate_key(key)
        if key in self._snapshots:
            raise WorkingPaperSealed(f"快照「{key}」已封存，拒绝覆盖（底稿写一次）")
        if key in self._facts:
            raise WorkingPaperSealed(f"「{key}」已是事实，不能再作为快照写入")
        decimal = ensure_decimal(value, field=f"快照「{key}」")
        self._snapshots[key] = decimal
        self._append("snapshot", key, decimal)

    # ── 读取 ─────────────────────────────────────────────────────
    @property
    def facts(self) -> Mapping[str, Decimal]:
        return MappingProxyType(self._facts)

    @property
    def snapshots(self) -> Mapping[str, Decimal]:
        return MappingProxyType(self._snapshots)

    def has_fact(self, key: str) -> bool:
        return key in self._facts

    def has_snapshot(self, key: str) -> bool:
        return key in self._snapshots

    def fact(self, key: str) -> Decimal:
        return self._facts[key]

    def snapshot(self, key: str) -> Decimal:
        return self._snapshots[key]

    def audit_trail(self) -> list[dict[str, object]]:
        return [block.to_dict() for block in self._blocks]

    # ── 校验 ─────────────────────────────────────────────────────
    def anchor(self) -> tuple[int, str]:
        """链外锚点：(块数, 链头哈希)。请把它存到本进程之外（日志/报告/另一个系统）。"""
        head = self._blocks[-1].hash if self._blocks else GENESIS
        return len(self._blocks), head

    def verify(self) -> VerifyReport:
        """链内一致性 + 状态一致性（不检查尾部截断，需配 anchor）。"""
        prev = GENESIS
        facts: dict[str, Decimal] = {}
        snapshots: dict[str, Decimal] = {}
        for index, block in enumerate(self._blocks, start=1):
            if block.seq != index:
                return VerifyReport(False, f"块序号断裂：期望 {index}，实际 {block.seq}",
                                    len(self._blocks), prev)
            if block.prev_hash != prev:
                return VerifyReport(False, f"第 {index} 块前链接断开", len(self._blocks), prev)
            expected = _block_hash(block.seq, block.kind, block.key, block.value, block.prev_hash)
            if expected != block.hash:
                return VerifyReport(False, f"第 {index} 块内容哈希不符", len(self._blocks), prev)
            try:
                decimal = Decimal(block.value)
            except Exception:  # noqa: BLE001 - 链被人为写入非十进制字符串
                return VerifyReport(False, f"第 {index} 块的值不是合法 Decimal：{block.value!r}",
                                    len(self._blocks), prev)
            if block.kind == "fact":
                if block.key in facts:
                    return VerifyReport(False, f"第 {index} 块重复写入事实「{block.key}」",
                                        len(self._blocks), prev)
                facts[block.key] = decimal
            elif block.kind == "snapshot":
                if block.key in snapshots:
                    return VerifyReport(False, f"第 {index} 块重复写入快照「{block.key}」",
                                        len(self._blocks), prev)
                snapshots[block.key] = decimal
            else:
                return VerifyReport(False, f"第 {index} 块类型非法：{block.kind!r}",
                                    len(self._blocks), prev)
            prev = block.hash

        if facts != self._facts or snapshots != self._snapshots:
            return VerifyReport(False, "底稿状态与哈希链重放结果不一致（疑似篡改）",
                                len(self._blocks), prev)
        return VerifyReport(True, "链完整，且当前状态与链一致", len(self._blocks), prev)

    def verify_strict(self) -> VerifyReport:
        """校验失败即抛 TamperDetected；成功返回报告。"""
        report = self.verify()
        if not report.ok:
            raise TamperDetected(f"底稿校验失败：{report.reason}")
        return report

    def verify_against_anchor(self, expected_len: int, expected_head: str) -> VerifyReport:
        """在链内校验之外，再核对「块数 + 链头」，用于发现链尾截断。"""
        report = self.verify()
        if not report.ok:
            return report
        if report.blocks != expected_len:
            return VerifyReport(
                False,
                f"链长度与锚点不符：期望 {expected_len} 块，实际 {report.blocks} 块（尾部被删/追加）",
                report.blocks,
                report.head,
                anchor_checked=True,
            )
        if report.head != expected_head:
            return VerifyReport(
                False,
                "链头哈希与锚点不符（链被重写）",
                report.blocks,
                report.head,
                anchor_checked=True,
            )
        return VerifyReport(True, "链完整，且与外部锚点一致", report.blocks, report.head,
                            anchor_checked=True)

    def digest(self) -> str:
        """状态摘要（只含 facts/snapshots，不含链），用于复现实验比对。"""
        payload = json.dumps(
            {
                "facts": {key: str(self._facts[key]) for key in sorted(self._facts)},
                "snapshots": {key: str(self._snapshots[key]) for key in sorted(self._snapshots)},
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    # ── 内部 ─────────────────────────────────────────────────────
    @staticmethod
    def _validate_key(key: str) -> None:
        if not isinstance(key, str) or not key.strip():
            raise WorkingPaperSealed("底稿键必须是非空字符串")
        if "\x1f" in key:
            raise WorkingPaperSealed(f"底稿键不允许包含分隔符 \\x1f：{key!r}")

    def _append(self, kind: str, key: str, value: Decimal) -> None:
        seq = len(self._blocks) + 1
        prev_hash = self._blocks[-1].hash if self._blocks else GENESIS
        value_text = str(value)
        digest = _block_hash(seq, kind, key, value_text, prev_hash)
        self._blocks.append(Block(seq, kind, key, value_text, prev_hash, digest))
