# struct-audit · 结构审计内核

> 把「实际值 − 期望值 = 残差」变成**可归位、可追溯、可关闭**的审计对象：
> 残差默认拒绝（fail-closed），归位必须显式策略 + 留痕，关闭必须过反向审计与证据分层。

## 这是什么

结构审计的最小可跑内核，从确定性业财系统里抽出的审计相关模块（MIT 开源版）：

| 概念 | 代码 | 规则 |
|---|---|---|
| 残差 | `residual.py` | `残差 = 实际值 − 期望值`；默认严格为零，**不允许静默取整放行** |
| 残差归位 | `residual.py` + `working_paper.py` | 只有显式策略允许、且残差可解释为尾差时，才写入底稿"尾差归位"科目并留哈希链记录 |
| 反向审计 | `audit.py` | 给定 DAG 执行轨迹，反向核对每一步的输入/输出与依据 |
| 证据分层 | `guards.py` | 证据按等级（EvidenceLevel）分级，边界越界即拒绝 |
| 职责分离 | `sod.py` | Actor / SignRecord / SoDGuard：编制、复核、审批不能同一人 |
| 关闭 | `closure.py` | `close_run()`：残差、审计、证据、SoD 全过才允许关闭，否则拒绝 |

## 为什么不"抹账"

- 残差 ≠ 错报，也 ≠ 没问题；它需要**归因**，而不是抹平
- 归位不是"把差额抹掉"，而是作为**一笔可审计的调整**写进底稿
- 默认 **fail-closed**：拿不到证据就拒绝，不猜、不静默吸收

## 结构

```text
src/struct_audit/
  residual.py    残差对象、残差策略、归位与断言
  audit.py       反向审计、TraceStep、verify_backward
  guards.py      边界守卫、证据分层
  router.py      DAG 路由与执行报告
  sod.py         职责分离
  closure.py     关闭流程（全部通过才放行）
  working_paper.py 底稿与哈希链留痕
  decimal_utils.py / errors.py / operators.py / watchdog.py  支撑
tests/           残差 / 关闭 / 反向审计三组测试
docs/            结构审计框架文档（总纲、术语表、证据分层、审计程序表、方法篇）
```

## 运行

```bash
pip install -e ".[dev]"
pytest -q
```

## 文档

- `docs/FRAMEWORK-总纲-结构会计与结构审计.md`
- `docs/GLOSSARY-术语表.md`
- `docs/EVIDENCE-证据分层表.md`
- `docs/PROCEDURES-审计程序表.md`
- `docs/METHODS-一般审计风险防范.md`

## 边界（重要）

- 本仓是**机制参考实现**，不是审计软件产品，不替代注册会计师/审计师判断
- **不含任何真实数据**：无客户底稿、无财务数据、无题库
- 不构成审计意见、会计意见或法律意见
- 生产使用需要接入真实数据、权限、审计日志与人工复核，并自行承担合规责任

## License

MIT
