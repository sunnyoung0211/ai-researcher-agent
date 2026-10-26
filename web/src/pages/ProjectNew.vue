<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { createProject, getDefaults, listTasks, uploadTemplate } from '../api/projects'
import { newRequestId } from '../api/client'
import { BUDGET, label } from '../constants/labels'

// P2 新建项目：先创建，拿到 project_id 后若选了模板再上传，最后跳到总览。
// 默认值（任务、运行模式、预算）来自 GET /api/defaults；预算只提交改过的类别，其余用后台默认值
const router = useRouter()
const formRef = ref()
const tasks = ref([])
const loadError = ref(false)
const defaults = ref(null)
const form = reactive({
  title: '',
  idea_text: '',
  compute: '',
  time: '',
  data: '',
  task: '',
  mode: 'fake',
  allow_local_execution: false,
  evidence_check: true,
  smoke_fail: '',
})
const budget = ref([]) // [{name, soft, hard}]
const templateFile = ref(null)
const submitting = ref(false)
let requestId = newRequestId()

const rules = {
  title: [{ required: true, message: '请填写项目标题', trigger: 'blur' }],
  idea_text: [{ required: true, message: '请用一两句话写下你的研究想法', trigger: 'blur' }],
  task: [{ required: true, message: '请选择任务配置', trigger: 'change' }],
}

const STAGE_NAME = { idea: '文献与选题', plan: '实验计划', experiment: '实验执行', writing: '论文撰写' }
const realStages = computed(() => defaults.value?.real_stages || [])

onMounted(async () => {
  try {
    const [t, d] = await Promise.all([listTasks(), getDefaults()])
    tasks.value = t
    defaults.value = d
    form.task = t.find((x) => x.path === d.task)?.path || t[0]?.path || ''
    form.mode = d.mode
    form.allow_local_execution = d.allow_local_execution
    budget.value = Object.entries(d.budget).map(([name, lim]) => ({ name, soft: lim.soft ?? null, hard: lim.hard ?? null }))
  } catch {
    loadError.value = true
  }
})

// 只提交和默认值不同的类别（后台按类别整类覆盖）
function changedBudget() {
  const out = {}
  for (const row of budget.value) {
    const d = defaults.value?.budget?.[row.name] || {}
    if ((d.soft ?? null) !== row.soft || (d.hard ?? null) !== row.hard) out[row.name] = { soft: row.soft, hard: row.hard }
  }
  return Object.keys(out).length ? out : null
}

function onFile(file) {
  if (!file.name.toLowerCase().endsWith('.zip')) {
    ElMessage.warning('请上传 .zip 格式的模板')
    return false
  }
  templateFile.value = file.raw
  return false
}

async function submit() {
  if (submitting.value) return
  try {
    await formRef.value.validate()
  } catch {
    return
  }
  submitting.value = true
  const constraints = Object.fromEntries(['compute', 'time', 'data'].filter((k) => form[k].trim()).map((k) => [k, form[k].trim()]))
  const dev = {}
  if (form.smoke_fail.trim()) { // 格式同 air new --smoke-fail：E2-seed=1:exit1，多个用逗号分隔
    dev.smoke_fail = Object.fromEntries(form.smoke_fail.split(',').map((s) => s.trim()).filter(Boolean).map((s) => {
      const i = s.lastIndexOf(':')
      return [s.slice(0, i), s.slice(i + 1)]
    }))
  }
  try {
    const p = await createProject({
      title: form.title.trim(),
      idea_text: form.idea_text.trim(),
      constraints,
      task: form.task,
      budget: changedBudget(),
      evidence_check: form.evidence_check,
      mode: form.mode,
      allow_local_execution: form.mode === 'real' && form.allow_local_execution,
      dev: Object.keys(dev).length ? dev : null,
      request_id: requestId,
    })
    if (templateFile.value) {
      try {
        await uploadTemplate(p.project_id, templateFile.value, { requestId: newRequestId() })
      } catch {
        ElMessage.warning('项目已创建，但模板上传失败；可以稍后在“模板”页重新上传')
      }
    }
    ElMessage.success('项目已创建，系统开始选题')
    router.push(`/projects/${p.project_id}`)
  } catch {
    requestId = newRequestId()
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="page">
    <div class="page-title"><h2>新建项目</h2></div>
    <el-alert v-if="loadError" type="error" :closable="false" show-icon class="mb" title="读取默认设置失败，请确认后台已启动后刷新页面" />
    <el-card shadow="never" class="wrap">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="120px" label-position="right">
        <el-form-item label="项目标题" prop="title">
          <el-input v-model="form.title" maxlength="100" placeholder="如：小样本文本分类的微调方法比较" />
        </el-form-item>
        <el-form-item label="研究想法" prop="idea_text">
          <el-input v-model="form.idea_text" type="textarea" :rows="4"
            placeholder="研究小型文本分类模型在数据很少时，哪种微调方法更好……" />
        </el-form-item>
        <el-form-item label="约束（可选）">
          <div class="cons">
            <el-input v-model="form.compute" placeholder="算力：如 只有 CPU / 1 张 GPU" />
            <el-input v-model="form.time" placeholder="时间：如 3 天内完成" />
            <el-input v-model="form.data" placeholder="数据：如 只用公开数据集" />
          </div>
        </el-form-item>
        <el-form-item label="任务配置" prop="task">
          <el-select v-model="form.task" placeholder="选择任务" class="full">
            <el-option v-for="t in tasks" :key="t.path" :label="`${t.name}（${t.path}）`" :value="t.path">
              <span>{{ t.name }}</span>
              <span class="muted opt-desc">{{ t.description }}</span>
            </el-option>
          </el-select>
        </el-form-item>
        <el-form-item label="运行模式">
          <el-radio-group v-model="form.mode">
            <el-radio value="fake">演示（假实现，不调用大模型，不花钱）</el-radio>
            <el-radio value="real" :disabled="!realStages.length">真实运行</el-radio>
          </el-radio-group>
          <div v-if="form.mode === 'real'" class="real">
            <div class="muted">
              这些阶段会真的调用大模型（需要先在 ~/air/.env 配好 Key，会产生费用）：
              <el-tag v-for="s in realStages" :key="s" size="small" class="stag">{{ STAGE_NAME[s] || s }}</el-tag>
              <template v-if="realStages.length < 4">；其余阶段仍用假实现。</template>
            </div>
            <el-checkbox v-model="form.allow_local_execution">允许在本机真的跑训练</el-checkbox>
            <div class="muted small">不勾选时实验只做模拟；勾选后会占用本机的 CPU / GPU。</div>
          </div>
          <div v-else-if="defaults && !realStages.length" class="muted small">还没有阶段完成真实实现，暂时只能用演示模式。</div>
        </el-form-item>
        <el-form-item label="预算">
          <el-table :data="budget" size="small" class="budget">
            <el-table-column label="类别" min-width="160"><template #default="{ row }">{{ label(BUDGET, row.name) }}</template></el-table-column>
            <el-table-column label="提醒" width="160">
              <template #default="{ row }"><el-input-number v-model="row.soft" :min="0" size="small" controls-position="right" :value-on-clear="null" placeholder="不提醒" /></template>
            </el-table-column>
            <el-table-column label="上限" width="160">
              <template #default="{ row }"><el-input-number v-model="row.hard" :min="0" size="small" controls-position="right" :value-on-clear="null" placeholder="不限" /></template>
            </el-table-column>
          </el-table>
          <div class="muted small">已填好后台的默认值。“提醒”到了会提示你，“上限”到了项目会自动暂停；只会提交你改过的类别。</div>
        </el-form-item>
        <el-form-item label="论文模板">
          <el-upload :auto-upload="false" :limit="1" accept=".zip" :on-change="onFile" :on-remove="() => (templateFile = null)">
            <el-button icon="Upload">选择模板 zip（可选）</el-button>
            <template #tip><div class="muted">不上传就用默认模板。之后也可以在项目的“模板”页上传。</div></template>
          </el-upload>
        </el-form-item>
        <el-collapse class="adv">
          <el-collapse-item title="高级选项" name="adv">
            <el-form-item label="论断-证据检查">
              <el-switch v-model="form.evidence_check" />
              <span class="muted gap">对比实验用，平时保持开启</span>
            </el-form-item>
            <el-form-item label="让运行失败">
              <el-input v-model="form.smoke_fail" placeholder="开发调试：如 E2-seed=1:exit1（同 air new --smoke-fail）" />
            </el-form-item>
          </el-collapse-item>
        </el-collapse>
        <el-form-item>
          <el-button type="primary" :loading="submitting" :disabled="loadError" @click="submit">创建并开始</el-button>
          <router-link to="/projects"><el-button class="gap">取消</el-button></router-link>
        </el-form-item>
      </el-form>
    </el-card>
  </div>
</template>

<style scoped>
.wrap { max-width: 860px; }
.mb { margin-bottom: 16px; max-width: 860px; }
.cons { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; width: 100%; }
.full { width: 100%; }
.opt-desc { margin-left: 12px; font-size: 12px; }
.real { width: 100%; margin-top: 4px; }
.stag { margin-left: 4px; }
.small { font-size: 12px; }
.budget { width: 100%; max-width: 520px; }
.gap { margin-left: 12px; }
.adv { margin: 0 0 18px 120px; }
</style>
