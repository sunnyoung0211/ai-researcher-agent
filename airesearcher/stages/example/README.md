# 示例阶段：照着它写你自己的阶段

`stage.py` 只有几十行，但用到了写阶段需要的全部写法。阅读顺序：

1. **`class ExampleStage` 和 `name = "example"`**：一个阶段就是一个有 `name` 和 `step(ctx)` 的类。
   文件末尾的 `create_stage()` 让主干能找到它（见 `airesearcher/stages/__init__.py` 的约定）。
2. **`s = ctx.scratch.setdefault("example", {})`**：`scratch` 是阶段的“记事本”，引擎每一步之后都会保存。
   重启后从这里知道做到哪一步了。
3. **`ctx.answer`**：你上一次 `Stop(question=...)` 提的问题，用户回答后，下一次 `step` 能读到一次。
   用 `s["asked"]`（引擎自动写入）判断回答对应哪个问题。
4. **`return Stop(..., question=Question(...))`**：需要用户决定时提问。这里没有选项，所以要求文字回答。
5. **`ctx.feedback`**：用户“退回修改”时，最近一次被退回的审批（含意见 `decision_comment`）。
   用 `handled_feedback` 记住处理过哪一次，避免重复处理。
6. **`if "draft_ref" not in s`**：**幂等**。引擎可能在任意两步之间崩溃，每一步开头先检查“这件事做过没有”。
7. **`ctx.llm.complete(..., response_schema=Summary)`**：调大模型。提示文件在 `prompts/summarize.v1.md`，
   输出会按 `Summary` 自动校验，格式不对时网关会让模型改正。
8. **`ctx.archive.put(...)`**：保存产物。同样的内容保存两次不会产生新版本，所以重复保存是安全的。
9. **`ctx.events.append(...)`**：写一条给人看的日志。
10. **`return NeedsApproval(...)`**：提交审批。引擎把项目转到 `IdeaPending`，等用户在 CLI / GUI 中决定。

**不能做的事：** 在 `step` 里 `time.sleep`；自己决定跳过审批；直接改别人目录下的文件。

## 自己试一试

```bash
air dev new-workspace /tmp/ws --idea "比较两种小模型在情感分类上的表现" --impl idea=example
air dev run-stage example --workspace /tmp/ws --steps 3 --fake-llm
```

测试：`tests/test_example_stage.py` 用 `FakeLLM` 跑通“生成 → 审批 → 退回 → 修改 → 批准”和“提问 → 回答 → 继续”。
