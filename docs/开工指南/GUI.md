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

详细设计 5 第 8 节列的接口都有了。

| 页面 | 接口（都以 `/api/projects/{pid}` 开头，除非另注） |
|---|---|
| P1 项目列表、P2 新建 | `GET /api/projects`、`POST /api/projects`、`GET /api/tasks`、`POST /templates`（上传模板） |
| P3 项目总览 | `GET /`（状态、预算、待办、待回答的问题）、`GET /question`、`POST /question/answer`、`POST /actions`（暂停 / 继续 / 取消 / 重新打开）、`PATCH /budget`、`GET /events`、`GET /checkpoints` + `POST /rollback`（回滚，可选） |
| P4、P5 审批 | `GET /approvals`、`GET /approvals/{aid}`（同时返回这一版 `target_content` 和上一版 `previous_content`，直接做差异对比）、`POST /approvals/{aid}/decision`、`GET /artifacts/content`、`GET /artifacts/versions` |
| P6、P7 运行 | `GET /runs`、`GET /runs/{run_id}`、`GET /runs/{run_id}/logs`、`GET /runs/{run_id}/logs/stream`（实时日志）、`POST /runs/{run_id}/cancel`、`POST /runs/{run_id}/label`（标注可信 / 可疑 / 无效） |
| P8 文献 | `GET /literature`、`GET /literature/{paper_id}` |
| P9 图表 | `GET /figures`、`GET /files?path=...` |
| P10、P11 论文与证据 | `GET /paper`、`GET /paper/pdf`、`GET /claims`、`GET /claims/{claim_id}/trace`、`GET /export?what=paper`（下载 zip） |
| P12、P13、P14 | `GET /files`、`GET /events`、`GET /templates`、`POST /templates` |
| 实时更新 | `GET /stream`（SSE） |

下面每条命令都可以在后台开着时（上一节第 2 步）直接运行，看看返回什么。把 `<pid>` 换成样例项目的编号，`<rid>` 换成 `GET /runs` 里的任意一个运行编号。

**项目实时更新**：

```bash
curl -N "http://127.0.0.1:8765/api/projects/<pid>/stream?from_now=true"
```

先看到一条 `event: state` 和一条 `event: budget`，之后有变化才会再推（按 Ctrl+C 停止）。
不加 `?from_now=true` 时会先把历史事件全部补推一遍。页面里这样写：

```js
const es = new EventSource(`/api/projects/${pid}/stream?from_now=true`)
for (const t of ['state', 'approval', 'question', 'run', 'budget']) {
  es.addEventListener(t, () => refreshSoon())     // 收到任何通知都重新请求 GET /api/projects/{pid}
}
```

**实时日志**：

```bash
curl -N "http://127.0.0.1:8765/api/projects/<pid>/runs/<rid>/logs/stream"
```

每行日志是一条 `event: line`，最后是一条 `event: end`，然后连接自动断开。页面里**收到 `end` 后必须 `close()`**，否则浏览器会自动重连：

```js
const es = new EventSource(`/api/projects/${pid}/runs/${rid}/logs/stream`)
es.addEventListener('line', (m) => lines.value.push(JSON.parse(m.data).text))
es.addEventListener('end', () => es.close())
```

**标注运行**：

```bash
curl -X POST "http://127.0.0.1:8765/api/projects/<pid>/runs/<rid>/label" -H "Content-Type: application/json" -d "{\"label\": \"suspicious\", \"reason\": \"test\", \"request_id\": \"demo-1\"}"
```

返回 `{"run_id": ..., "label": "suspicious", ...}`；之后 `GET /runs` 的这一行多了 `"label": "suspicious"`。`label` 只能是 `trusted`、`suspicious`、`invalid`。

**上传模板**（`tpl.zip` 换成任意一个含 `.tex` 文件的 zip）：

```bash
curl -X POST "http://127.0.0.1:8765/api/projects/<pid>/templates" -F file=@tpl.zip -F request_id=demo-2
```

返回 `{"template_id": "tpl-01", "version": 1, "in_use": true, "check": null, ...}`。`check` 在论文阶段检查完模板后才有内容。
上传修好的新版本时多加一个字段 `-F template_id=tpl-01`，返回 `"version": 2`。网页里用 Element Plus 的 `el-upload`，字段名写 `file`。

**导出**：

```bash
curl -o paper.zip "http://127.0.0.1:8765/api/projects/<pid>/export?what=paper"
```

得到一个 zip，里面有 `ARCHIVE_INDEX.md`、`paper/`、`artifacts/figures/`。网页里直接用链接：`<a :href="`/api/projects/${pid}/export?what=paper`">导出论文</a>`；`what=archive` 是整个研究档案。

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
