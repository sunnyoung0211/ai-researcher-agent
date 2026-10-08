# AI Researcher Agent（DSA4213 第 6 组）

一个“AI 研究助理”：从一句研究想法出发，依次完成**选题 → 实验计划 → 跑实验 → 分析 → 写论文**，
每个关键节点都停下来等你审批。设计文档在 [`docs/`](docs/)，主干的详细设计见
[`docs/详细设计/1_Agent主干_详细设计.md`](docs/详细设计/1_Agent主干_详细设计.md)。

> **现在的进度：** 主干（流程引擎、档案、审批、后台、命令行）是真实代码；
> 文献、实验、论文三个阶段目前是**假实现**（不调用大模型、不联网），但生成的文件格式与设计文档完全一致，
> 所以整条流程已经可以从头跑到尾。各位组员之后用真实实现逐个替换即可（见第 5 节）。

---

## 1. 安装（只需做一次）

需要 Python 3.11 或更新版本。在仓库根目录打开终端：

```bash
python3 -m venv .venv
```

```bash
source .venv/bin/activate
```

```bash
pip install -e ".[dev]"
```

- 以后每次新开终端，都要先运行一次 `source .venv/bin/activate`（命令行前面会出现 `(.venv)`）。
- 以后要真实调用大模型时，再运行 `pip install -e ".[llm]"` 并设置 `ANTHROPIC_API_KEY`。**用假实现跑流程不需要任何 API Key。**
- 有 MacTeX / TeX Live（带 `latexmk`）时会真的编译出论文 PDF；没有也能跑完，只是生成一个占位 PDF。

## 2. 跑通一遍完整流程（约 3 分钟，不需要 API Key）

需要**两个终端窗口**，两个都先运行 `source .venv/bin/activate`。

**终端 1：启动后台**（保持这个窗口开着，不要关）

```bash
air serve
```

看到“AI Researcher 后台启动：http://127.0.0.1:8765”就成功了。

**终端 2：一步一步操作**

① 新建项目（用一句话写你的研究想法）：

```bash
air new --idea "比较三种方法在冒烟任务上的得分差异" --task tasks/smoke
```

② 看看现在在做什么、需要你做什么：

```bash
air status
```

`air status` 每次都会告诉你三件事：**现在的状态**（中文）、**需要你处理什么**、**接下来可以输入的命令**。
不确定下一步该做什么时，就输入 `air status`。

③ 第 1 次审批：选题（idea）。先看内容，再批准：

```bash
air show ap-0001
```

```bash
air approve ap-0001
```

④ 第 2 次审批：实验计划。系统会自动写好计划，用 `--wait` 等它写完：

```bash
air status --wait
```

```bash
air approve ap-0002
```

⑤ 批准计划后，系统开始**真的运行** 9 个小实验（每个约 2 秒）。等它们跑完、写好过程日志：

```bash
air status --wait
```

想看运行情况可以输入 `air runs`；看某个运行的输出：`air logs <运行编号>`。

⑥ 第 3 次审批：过程日志（实验结果的总结）：

```bash
air show ap-0003
```

```bash
air approve ap-0003
```

⑦ 第 4 次审批：最终手稿（论文）。等论文写好：

```bash
air status --wait
```

```bash
air show ap-0004
```

```bash
air approve ap-0004
```

⑧ 完成！

```bash
air status
```

会显示“已完成”，并给出论文 PDF 的位置。

> **审批编号会变：** 上面的 `ap-0001` ~ `ap-0004` 是“每次都直接批准”时的编号。如果你中途用了
> `air revise`（退回修改），系统会生成新版本、新的审批编号。**永远以 `air status` 显示的编号为准。**

### 2.1 试试“退回修改”和“拒绝”

```bash
air revise ap-0001 -m "研究问题再具体一点"
```

系统会按你的意见生成新版本（文件里能看到“修订说明”），再次请你审批。
`air reject ap-xxxx -m "理由"` 是拒绝：系统会问你“重写还是结束项目”，用 `air answer redo` 或 `air answer end` 回答。

### 2.2 试试“系统向你提问”

建项目时故意让一个实验运行失败：

```bash
air new --idea "测试失败时的提问" --task tasks/smoke --smoke-fail E2-seed=1:exit1
```

批准 idea 和计划后，`air status --wait` 会停在“需要你回答问题”。查看问题和选项：

```bash
air question
```

选择“跳过这个组合，继续其他运行”：

```bash
air answer skip
```

（也可以 `air answer retry` 重试，或 `air answer end` 结束项目。）

## 3. 命令一览

| 命令 | 作用 |
|---|---|
| `air serve` | 启动后台（一直开着） |
| `air new --idea "..." --task tasks/smoke` | 新建项目并开始 |
| `air list` | 列出所有项目 |
| `air status [项目编号]` | 当前状态、需要你做什么、下一步命令；加 `--wait` 会一直等到需要你处理 |
| `air approvals` / `air show <审批编号>` | 待审批列表 / 查看待审内容 |
| `air approve <审批编号> [-m 意见]` | 批准 |
| `air revise <审批编号> -m 意见` | 退回修改 |
| `air reject <审批编号> -m 理由` | 拒绝 |
| `air question` / `air answer <选项> [-m 文字]` | 查看 / 回答系统的问题 |
| `air pause` / `air resume` / `air cancel` / `air reopen` | 暂停 / 继续 / 取消 / 重新打开已完成的项目 |
| `air runs` / `air logs <运行编号> [-f]` | 查看实验运行 / 运行日志 |
| `air dev new-workspace <目录>` / `air dev run-stage <阶段> --workspace <目录>` | 开发调试用（不经过后台，见第 5 节） |

- 大多数命令的“项目编号”可以省略，省略时用**最近创建的项目**。
- 每个命令都可以加 `--help` 查看说明，例如 `air new --help`。
- 浏览器打开 http://127.0.0.1:8765/docs 可以看到后台全部接口（GUI 同学对接用）。

## 4. 项目文件在哪里

所有项目放在 `~/air/projects/<项目编号>/`（可以用环境变量 `AIR_HOME` 改位置）。里面都是普通文件，可以直接打开看：

| 位置 | 内容 |
|---|---|
| `project.yaml` | 项目配置 |
| `idea/selected.md` | 选题结果（开头是给程序读的 YAML，后面是给人读的正文） |
| `plan/experiment_plan.md`、`plan/tasks.json` | 实验计划、展开后的运行清单 |
| `runs/<运行编号>/` | 每次运行的配置、日志、原始指标 `metrics.jsonl`、状态 `status.json` |
| `artifacts/aggregates/` | 汇总结果（均值、标准差、95% 区间） |
| `logs/round_1.md` | 过程日志 |
| `paper/` | 论文：`main.tex`、`sections/`、`claims.jsonl`、`build/main.pdf`、`review/` 核验报告 |
| `approvals/`、`questions/` | 审批记录、问题与回答 |
| `research_log.jsonl` | 事件日志（发生过的每件事） |
| `.archive/` | 所有产物的历史版本（只增不删） |

## 5. 给组员：用真实实现替换假实现

| 阶段 | 假实现（现在） | 真实实现放这里 | 负责人 |
|---|---|---|---|
| 选题 idea | `airesearcher/stages/fakes/idea.py` | `airesearcher/stages/idea/stage.py` | 文献 |
| 实验计划 plan | `airesearcher/stages/fakes/plan.py` | `airesearcher/stages/plan/stage.py` | 实验 |
| 执行与分析 experiment | `airesearcher/stages/fakes/experiment.py` | `airesearcher/stages/experiment/stage.py` | 实验 |
| 论文 writing | `airesearcher/stages/fakes/writing.py` | `airesearcher/stages/writing/stage.py` | 论文 |

1. **先读示例阶段**：[`airesearcher/stages/example/README.md`](airesearcher/stages/example/README.md)，几十行代码演示了写阶段需要的全部写法。
2. 新建你的 `stage.py`，提供一个 `create_stage()` 函数，返回你的阶段对象（有 `name` 和 `step(ctx)`）。
3. 让项目用你的实现：建项目时加 `--impl idea=real`（其他阶段仍用假实现），或者设置环境变量
   `AIR_STAGE_IMPL=idea=real`，或者改 `project.yaml` 里的 `dev.stage_impl`。
4. 你的阶段**写出的文件格式必须和假实现一样**（都在设计文档里冻结了，`airesearcher/core/models/` 里有对应的数据模型）。
   假实现就是“格式样例”：不确定时，跑一遍流程，打开假实现生成的文件对照即可。
5. 不启动后台、只调试自己的阶段：

```bash
air dev new-workspace /tmp/ws --impl idea=real
```

```bash
air dev run-stage idea --workspace /tmp/ws --steps 5
```

每一步会打印阶段返回了什么（`Continue` / `NeedsApproval` / ……）。遇到审批可以加 `--auto-approve` 自动批准。

## 6. 哪些是真的，哪些是假的

| 部分 | 状态 |
|---|---|
| 流程引擎（状态机、检查点、重启恢复）、档案（版本、血缘、人工编辑检测）、审批、提问、预算、权限、事件日志 | **真实** |
| 后台 API（FastAPI）、命令行 `air` | **真实**（SSE 推送是每秒轮询的简化版） |
| LLM 网关 `complete()`（提示文件、格式校验与重试、记账、调用日志、录制/回放） | **真实**；`tool_loop()` 还没写（第 2 周） |
| 本地执行器 + 运行包装器、冒烟任务 `tasks/smoke` | **真实但精简**（没有环境 pip freeze、数据校验、GPU 串行），实验同学接手补全 |
| 汇总统计 `services/runs.py`、引用核验 `services/literature.py` | **真实** |
| 证据核验 `services/evidence.py` | **部分**：只做了数字重算、过期证据、结论相反、引用不存在四项检查 |
| 文献检索、选题、实验计划、编码 Agent、分析决策、论文写作 | **假实现**（固定内容，不调用大模型） |
| SQLite 索引 | **没做**：目前直接读文件（数据量小，够用） |
| GUI | 不在本仓库这部分（GUI 同学负责），接口见 http://127.0.0.1:8765/docs |

## 7. 常见问题

- **“连不上后台”**：终端 1 的 `air serve` 没开，或者被关掉了。重新运行 `air serve`。
- **端口被占用**：`air serve --port 8766`，然后在终端 2 先运行 `export AIR_SERVER=http://127.0.0.1:8766`。
- **关掉后台再打开会怎样？** 项目从上次的位置继续；正在跑的实验不受影响（它们是独立进程），重启后会自动核对状态。
- **`air: command not found`**：先 `source .venv/bin/activate`。

## 8. 开发者

```bash
pytest -q
```

```bash
ruff check .
```

测试包括：从创建到完成的端到端流程（引擎、HTTP、CLI 三种方式）、审批去重与过期（409）、提问与回答、
审批待处理时重启、运行进行中重启、后台停机期间运行结束、档案版本（含 `paper/` 目录）、引擎转换表。
