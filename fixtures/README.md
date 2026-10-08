# 样例项目 `sample_project/`

全组共用的测试数据（详细设计 1 第 10.2 节）。它是一个**真实跑出来**的完整项目：用四个阶段的假实现在本机跑了一遍，
运行编号、哈希、版本记录都是真实计算的，所有文件都符合约定的格式（`air dev check fixtures/sample_project` 全部通过）。

**不要直接修改这个目录。** 需要一份可以随便改的副本时：

```bash
air dev new-workspace /tmp/ws --from fixtures/sample_project
```

在测试里用夹具 `sample_project`（`tests/conftest.py`），或 `airesearcher.testing.sample.copy_sample_project()`。

## 里面有什么

| 内容 | 位置 | 适合谁用 |
|---|---|---|
| 项目配置（任务 `tasks/smoke`） | `project.yaml` | 所有人 |
| 3 篇论文的文献记录、检索快照、评分、差异表；`selected.md` 有 v1、v2 两个版本（v1 被退回修改过） | `idea/` | 实验（计划生成）、论文（参考文献）、GUI（审批页的版本对比） |
| 实验计划：E1 基线、E2 主方法、E3 消融，各 3 个种子；运行清单；执行前检查 | `plan/` | 实验、论文、GUI |
| 11 个运行：9 个成功、1 个失败（`E2-seed=2` 程序报错）、1 个对失败运行的重试（`retry_of`）、1 个试运行（`kind=trial`） | `runs/` | 实验、论文、GUI（运行页） |
| 汇总结果 C1、C2、all（`.json` / `.csv` / `.md`），失败运行和试运行被列在 `excluded` 中 | `artifacts/aggregates/` | 论文 |
| 两张图（规格、数据、`figure.json`、SVG） | `artifacts/figures/` | 论文、GUI |
| 第 1 轮过程日志 | `logs/round_1.md` | GUI |
| 论文：`main.tex`、章节、`air_values.tex`、`claims.jsonl`、`references.bib`、编译好的 PDF、两版核验报告 | `paper/` | 论文、GUI |
| 审批记录：idea 退回 1 次后批准、计划批准、日志批准、手稿 v1 被取代、**手稿 v2 待审批** | `approvals/` | GUI（审批页） |
| 一个已回答的问题（运行失败 → 选择“重试”） | `questions/` | GUI |
| 事件日志、预算流水、检查点（状态 `ManuscriptPending`）、所有产物的历史版本 | `research_log.jsonl`、`budget.jsonl`、`.state/`、`.archive/` | 主干、GUI |

**故意放的 2 条错误论断**（给论文同学测试核验用）：`paper/claims.jsonl` 的最后两条——
一条写的数字与重算值对不上（`NUM_MISMATCH`），一条结论与数据相反（`CONTRADICTED`）。
最新核验报告 `paper/review/review_v2.json` 中有这 2 个 blocker，所以批准待审的手稿时必须填写意见。

与文档 10.2 的一点差别：文档写的是“1 个待审日志”，但引擎规定“有未处理的日志审批时不能提交手稿”，
所以样例改为日志已批准、**手稿待审批**，同样能给 GUI 提供一个待审批的例子。

## 重新生成

只有在契约（数据格式）改变时才需要：

```bash
python fixtures/build_sample.py
```

脚本会重新跑一遍流程、做契约检查，通过后覆盖本目录。每次生成的运行编号和时间都不同，所以改动会很大，请在 PR 中说明原因。
需要本机有 `latexmk` 才能得到真实编译的 PDF，否则是占位 PDF。
