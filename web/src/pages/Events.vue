<script setup>
import { onMounted, reactive, ref } from 'vue'
import PageState from '../components/PageState.vue'
import { listEvents } from '../api/projects'
import { fullTime } from '../utils/format'
import { eventText } from '../utils/events'
import { useProject, useProjectRefresh } from '../utils/useLoader'
import { useI18n } from 'vue-i18n'
const { t } = useI18n()

// P13 事件日志：按类型筛选、按关键词搜索；新的在上，滚动到底部加载更早的（tail + before_seq 分页）
const { pid } = useProject()
const PAGE = 100
const TYPES = ['state', 'approval', 'question', 'run', 'artifact', 'stage', 'project', 'budget', 'literature', 'template', 'permission', 'engine']
const filter = reactive({ types: [], q: '', run_id: '' })
const rows = ref([])
const loading = ref(false)
const more = ref(true)
const error = ref(null)

function params(extra) {
  return { ...extra, type: filter.types.join(',') || undefined, q: filter.q.trim() || undefined, run_id: filter.run_id.trim() || undefined }
}
async function reset() {
  rows.value = []
  more.value = true
  error.value = null
  await loadMore()
}
async function loadMore() {
  if (loading.value || !more.value) return
  loading.value = true
  try {
    const before = rows.value.length ? rows.value[rows.value.length - 1].seq : undefined
    const got = await listEvents(pid.value, params({ tail: PAGE, before_seq: before }))
    rows.value.push(...got.reverse())
    more.value = got.length === PAGE
  } catch (e) {
    error.value = e
  } finally {
    loading.value = false
  }
}
onMounted(reset)
// 有新事件时把比最上面一条更新的接到顶部
let fetchingNew = false
async function loadNew() {
  if (fetchingNew || loading.value || !rows.value.length) return
  fetchingNew = true
  try {
    for (;;) {
      const got = await listEvents(pid.value, params({ after_seq: rows.value[0].seq, limit: 500 }))
      rows.value.unshift(...got.reverse())
      if (got.length < 500) break
    }
  } catch {
    /* 下次有新事件再接 */
  } finally {
    fetchingNew = false
  }
}
useProjectRefresh(() => (rows.value.length ? loadNew() : reset()), ['event'])

const color = (t) => (t.includes('fail') || t.includes('error') ? 'danger' : t.startsWith('approval') ? 'warning' : t.startsWith('run') ? 'primary' : t.startsWith('state') ? 'success' : '')
const expanded = ref({})
</script>

<template>
  <div>
    <div class="page-title"><h2>{{ t('events.title') }}</h2><span class="muted">{{ t('events.subtitle') }}</span></div>
    <div class="toolbar">
      <el-select v-model="filter.types" multiple collapse-tags collapse-tags-tooltip :placeholder="t('events.allTypes')" clearable style="width: 240px" @change="reset">
        <el-option v-for="ty in TYPES" :key="ty" :value="`${ty}.*`" :label="t(`events.types.${ty}`)" />
      </el-select>
      <el-input v-model="filter.q" :placeholder="t('events.searchPh')" clearable style="width: 220px" @keyup.enter="reset" @clear="reset" />
      <el-input v-model="filter.run_id" :placeholder="t('events.runPh')" clearable style="width: 220px" class="mono" @keyup.enter="reset" @clear="reset" />
      <el-button type="primary" icon="Search" @click="reset">{{ t('events.search') }}</el-button>
    </div>
    <el-card shadow="never">
      <PageState :loading="loading && !rows.length" :error="error && !rows.length ? error : null" :empty="!loading && !rows.length" :empty-text="t('events.empty')" @retry="reset">
        <div v-infinite-scroll="loadMore" :infinite-scroll-disabled="loading || !more" :infinite-scroll-distance="80" class="list">
          <el-timeline>
            <el-timeline-item v-for="e in rows" :key="e.seq" :type="color(e.type)" :timestamp="fullTime(e.ts)" placement="top">
              <div class="row">
                <span class="mono muted seq">#{{ e.seq }}</span>
                <span>{{ eventText(e) }}</span>
              </div>
              <div class="meta muted">
                <span class="mono">{{ e.type }}</span> · {{ e.actor }}
                <router-link v-if="e.run_id" :to="`/projects/${pid}/runs/${e.run_id}`" class="mono link">{{ e.run_id }}</router-link>
                <router-link v-for="r in e.refs" :key="r.artifact_id + r.version" :to="`/projects/${pid}/files?path=${encodeURIComponent(r.artifact_id)}`" class="mono link">{{ r.artifact_id }} v{{ r.version }}</router-link>
                <el-button v-if="Object.keys(e.data || {}).length" link size="small" @click="expanded[e.seq] = !expanded[e.seq]">{{ expanded[e.seq] ? t('common.collapse') : t('common.details') }}</el-button>
              </div>
              <pre v-if="expanded[e.seq]" class="code mono">{{ JSON.stringify(e.data, null, 2) }}</pre>
            </el-timeline-item>
          </el-timeline>
          <div class="end muted">
            <span v-if="loading">{{ t('events.loading') }}</span>
            <el-button v-else-if="more" size="small" @click="loadMore">{{ t('events.loadMore') }}</el-button>
            <span v-else>{{ t('events.end', { n: rows.length }) }}</span>
          </div>
        </div>
      </PageState>
    </el-card>
  </div>
</template>

<style scoped>
.row { display: flex; gap: 8px; }
.seq { flex: none; }
.meta { font-size: 12px; margin-top: 2px; display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.link { color: var(--el-color-primary); }
.end { text-align: center; padding: 8px; }
</style>
