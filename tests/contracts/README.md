# 契约测试

“契约”是模块之间通过文件交接时约定的格式（详细设计 1 第 11 节）。这里每个文件归一位**提供方**负责：

| 文件 | 提供方 | 检查的交接文件 |
|---|---|---|
| `test_backbone_contract.py` | 主干 | project.yaml、事件、预算、审批、问题、检查点、`.archive/` |
| `test_literature_contract.py` | 文献 | `idea/literature.jsonl`、检索快照、阅读卡、`selected.md`、`scores.json` |
| `test_experiment_contract.py` | 实验 | `plan/`、`runs/<run_id>/`、`artifacts/aggregates/` |
| `test_paper_contract.py` | 论文 | `artifacts/figures/`、`paper/`（论断、数字宏、参考文献、编译与核验报告） |

- 测试数据是样例项目 `fixtures/sample_project/`（由 `python fixtures/build_sample.py` 生成）。
- 具体检查规则写在 `airesearcher/testing/contracts.py`，测试只是调用它。
- 自查自己写出的文件：`air dev check <项目目录>`。

**要改格式时（契约变更）**：同时修改数据模型、`contracts.py` 中的检查、样例项目，在 PR 描述里写明“契约变更”，并 @ 使用这份文件的同学。
