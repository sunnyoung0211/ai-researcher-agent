# GUI 同学开工指南

先读 [开工指南首页](README.md)（第 1 节的第 3 步“读示例阶段”你可以跳过）。详细要求见 [详细设计 5](../详细设计/5_GUI_详细设计.md)。

## 你负责什么

在仓库的 `web/` 目录下做一个网页（推荐 Vue 3 + Vite + Element Plus），通过后台的 HTTP 接口完成整个研究流程。
网页**只负责显示和提交操作**：项目状态、审批、运行全部从后台读取，不在浏览器里保存。

## 主干已经做好的（不用重复做）

- **后台 API**：启动 `air serve` 后，浏览器打开 <http://127.0.0.1:8765/docs>，每个接口的参数和返回格式都在上面，还能直接点“Try it out”试用。
- **样例项目**：一个完整跑完、停在“等待审批最终手稿”的项目，有 11 个运行（含失败和试运行）、图表、论文 PDF、2 条有问题的论断。可以登记到后台，接口能直接返回它的数据（见下文）。
- **中文标签**：状态、运行状态、失败原因的英文 → 中文对照在 `airesearcher/cli/labels.py`，照抄到你的 `constants/labels.js` 即可（详细设计 5 第 7 节）。
- **统一的错误格式**：出错时返回 `{"error": {"code": "...", "message": "给人看的中文说明", "detail": {...}}}`。
  审批时内容已经变了会返回 **409**，`code` 为 `STALE_VERSION`，处理方法见详细设计 5 第 6 节。
- **网页的交付方式已经接好**：`npm run build` 生成 `web/dist/` 后，`air serve` 会在 <http://127.0.0.1:8765/> 直接提供网页。

## 先做这几件事

1. 学框架（详细设计 5 第 2.1 节，约 2 天）。
2. **让后台显示样例项目**（在仓库根目录运行）：

   ```bash
   air dev new-workspace ~/air-sample --from fixtures/sample_project --register
   ```

   ```bash
   air serve
   ```

   Windows 上把 `~/air-sample` 换成如 `C:\air-sample`。然后在浏览器打开 <http://127.0.0.1:8765/api/projects>，应能看到这个项目。
   在网页上点了“批准”等操作后，样例会继续往下走；想恢复原样，停掉 `air serve`，删掉这个目录，再运行一遍上面两条命令。
3. **把真实返回值存成假数据**：后台没开时网页也能显示（详细设计 5 第 5.2 节）。把下面的 `<pid>` 换成上一步看到的项目编号：

   ```bash
   curl http://127.0.0.1:8765/api/projects/<pid> -o web/src/mock/data/project_status.json
   ```

4. 用 `npm create vite@latest web` 创建项目，按详细设计 5 第 3 节建目录。第一个 PR：左侧菜单布局 + P3 项目总览（用假数据）。

## 现在能用的接口

| 页面 | 接口（都以 `/api/projects/{pid}` 开头，除非另注） |
|---|---|
| P1 项目列表、P2 新建 | `GET /api/projects`、`POST /api/projects`、`GET /api/tasks` |
| P3 项目总览 | `GET /`（状态、预算、待办、待回答的问题）、`GET /question`、`POST /question/answer`、`POST /actions`（暂停 / 继续 / 取消 / 重新打开）、`PATCH /budget`、`GET /events` |
| P4、P5 审批 | `GET /approvals`、`GET /approvals/{aid}`（同时返回这一版 `target_content` 和上一版 `previous_content`，直接做差异对比）、`POST /approvals/{aid}/decision`、`GET /artifacts/content`、`GET /artifacts/versions` |
| P6、P7 运行 | `GET /runs`、`GET /runs/{run_id}`、`GET /runs/{run_id}/logs?stream=stdout&offset=0`、`POST /runs/{run_id}/cancel` |
| P8 文献 | `GET /literature`、`GET /literature/{paper_id}` |
| P9 图表 | `GET /figures`、`GET /files?path=...` |
| P10、P11 论文与证据 | `GET /paper`、`GET /paper/pdf`、`GET /claims`、`GET /claims/{claim_id}/trace` |
| P12、P13 文件与日志 | `GET /files`、`GET /events` |
| 实时更新 | `GET /stream`（SSE） |

**还没有、计划在 10-20 前补上的**：模板上传与列表（P2、P14 的 `/templates`）、导出 zip（`/export`）、日志逐行推送（`/runs/{run_id}/logs/stream`）、给运行加标签。
在这之前：

- **实时日志**：每 2 秒请求一次 `GET /runs/{run_id}/logs?offset=<上次返回的 next_offset>`，把新内容接在后面；返回的 `eof` 为 `true` 时停止；
- **SSE**：目前只推 `state` 和 `event` 两种消息（审批、问题、运行的变化都以 `event` 的形式出现）。按详细设计的原则，收到任何消息后重新请求 `GET /api/projects/{pid}` 即可，后台补全其他消息类型后你的代码不用改。

## 提交审批时必须带的字段

```js
await decide(pid, aid, {
  decision: 'approved',               // 或 'changes_requested'、'rejected'
  comment: '',                        // 退回和拒绝时必填
  expected_sha256: approval.target.sha256,   // 你看到的那一版的哈希；内容已变时后台返回 409
  request_id: crypto.randomUUID(),    // 每次点击生成一个；网络重试时用同一个，后台不会重复处理
})
```

回答问题（`POST /question/answer`）、项目操作（`POST /actions`）同样要带 `request_id`。

## 测试与交接

- 不需要写 Python 测试。每做完一个页面，**对着后台的真实数据**点一遍：样例项目、加上一个用 `air new` 新建并跑完的项目；
- 刷新页面、关掉再打开，显示应该不变（状态都在后台）；
- `node_modules/` 和 `web/dist/` 不要提交（在 `web/.gitignore` 中忽略）。
