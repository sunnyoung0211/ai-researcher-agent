---
plan_id: plan-001
idea_ref:
  artifact_id: idea/selected.md
  version: 2
  sha256: da042f112a87fd48fd901fff41188f5cc94dd1e70975bba04e66ac0ebcabef7f
task_config: tasks/smoke
experiments:
- id: E1
  kind: baseline
  purpose: 基线 baseline
  hypotheses:
  - H1
  method: baseline
  variables: {}
  fixed:
    seconds: 1.0
  seeds:
  - 0
  - 1
  - 2
  eval_condition: final
  depends_on: []
  target_env: local
  needs_code: false
  est_minutes_per_run: 0.017
  success_criteria: 3 个种子均完成
  failure_criteria: 任一种子修复后仍失败
- id: E2
  kind: main
  purpose: 主方法 main 与基线比较
  hypotheses:
  - H1
  method: main
  variables: {}
  fixed:
    seconds: 1.0
  seeds:
  - 0
  - 1
  - 2
  eval_condition: final
  depends_on: []
  target_env: local
  needs_code: false
  est_minutes_per_run: 0.017
  success_criteria: ''
  failure_criteria: ''
- id: E3
  kind: ablation
  purpose: 消融 ablation
  hypotheses: []
  method: ablation
  variables: {}
  fixed:
    seconds: 1.0
  seeds:
  - 0
  - 1
  - 2
  eval_condition: final
  depends_on: []
  target_env: local
  needs_code: false
  est_minutes_per_run: 0.017
  success_criteria: ''
  failure_criteria: ''
comparisons:
- id: C1
  question: main 是否优于基线 baseline
  experiments:
  - E1
  - E2
  group_by:
  - method
  metric: score
  filter: {}
- id: C2
  question: 消融：ablation 与 main 的差别
  experiments:
  - E2
  - E3
  group_by:
  - method
  metric: score
  filter: {}
replication_policy:
  max_extra_seeds_per_group: 3
  trigger: ''
stopping_conditions:
  max_rounds: 3
  no_progress_rounds: 2
  budget_fraction: 0.9
budget_estimate:
  runs: 9
  wall_hours: 0.0025
  gpu_hours: 0
approval_points:
- 每轮结束的重要过程日志
- 计划外实验
- 最终手稿
reuse_runs: []
---

# 目的与假设
依据 idea：`idea/selected.md` v2。

# 实验矩阵
| ID | 类型 | 方法 | 变量 | 种子 | 评价条件 |
|---|---|---|---|---|---|
| E1 | baseline | baseline | — | [0, 1, 2] | final |
| E2 | main | main | — | [0, 1, 2] | final |
| E3 | ablation | ablation | — | [0, 1, 2] | final |

# 变量与控制
固定参数：{'seconds': 1.0}；共 9 次运行。

# 比较与统计方法
- C1：main 是否优于基线 baseline（['E1', 'E2']，按 ['method'] 分组，指标 score）
- C2：消融：ablation 与 main 的差别（['E2', 'E3']，按 ['method'] 分组，指标 score）
- 每组报告均值、样本标准差、t 分布 95% 区间；组间用 Welch t 检验（仅供参考）。

# 资源估计
{'runs': 9, 'wall_hours': 0.0025, 'gpu_hours': 0}

# 停止条件
{'max_rounds': 3, 'no_progress_rounds': 2, 'budget_fraction': 0.9}

# 执行前检查结果
- license：pass（任务没有外部数据）
- leakage_split：pass（所有实验都在 final 划分上评价，没有探索性实验）
- metric：pass（主要指标 score）
- fairness：pass（各实验的变量取值与种子数相同）
- repeats：pass（每个实验 3 个种子）
- coverage：pass（覆盖假设 ['H1']）
- budget：pass（估计 0.0025 小时，预算 6.0 小时）

# 审批点
- 每轮结束的重要过程日志
- 计划外实验
- 最终手稿
