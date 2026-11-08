<template>
  <div class="exec-view">
    <!-- 上：测试计划列表 -->
    <div class="bar">
      <span class="sec-title">测试计划</span>
      <el-button type="primary" size="small" @click="openTplEdit()">新建计划</el-button>
    </div>
    <el-table :data="templatesSorted" border size="small" style="margin-bottom:16px">
      <el-table-column label="序号" type="index" width="60" align="center" />
      <el-table-column label="计划名" min-width="140">
        <template #default="{ row }">
          <b>{{ row.name }}</b>
          <div class="tip" v-if="row.description">{{ row.description }}</div>
        </template>
      </el-table-column>
      <el-table-column label="用例数" width="80" align="center">
        <template #default="{ row }">{{ (row.case_ids || []).length }}</template>
      </el-table-column>
      <el-table-column label="失败策略" width="100">
        <template #default="{ row }">
          <el-tag size="small" :type="row.on_failure === 'abort' ? 'danger' : 'info'">
            {{ row.on_failure === 'abort' ? '失败中止' : '失败跳过' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="单条超时" width="90" align="center">
        <template #default="{ row }">{{ row.case_timeout }}s</template>
      </el-table-column>
      <el-table-column label="操作" width="240">
        <template #default="{ row }">
          <el-button type="primary" size="small" @click="runTemplate(row)">执行</el-button>
          <el-button size="small" @click="openTplEdit(row)">编辑</el-button>
          <el-button type="danger" plain size="small" @click="removeTemplate(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 下：执行记录列表（点操作列「详情」展开：测试计划 → 用例结果 → 抽屉看日志/截图） -->
    <div class="bar">
      <span class="sec-title">执行记录</span>
      <span class="tip">逻辑删除记录超 7 天后由系统自动物理清理</span>
    </div>
    <el-table :data="runsSorted" border size="small" row-key="id"
              :expand-row-keys="expandKeys" @expand-change="onRunExpand">
      <!-- expand 列必须存在才能启用内建展开；隐藏其表头/图标格，改由操作列「详情」按钮控制 -->
      <el-table-column type="expand" width="1" class-name="col-hidden" label-class-name="col-hidden">
        <template #default="{ row }">
          <div class="run-expand">
            <div v-if="loadingRun[row.id]" class="tip">加载中…</div>
            <el-table
              v-else-if="runDetails[row.id]"
              :data="runDetails[row.id].case_results || []"
              border size="small"
              class="inner-table">
              <el-table-column label="序号" type="index" width="60" align="center" />
              <el-table-column label="用例名" prop="case_name" min-width="180" />
              <el-table-column label="状态" width="90">
                <template #default="{ row: cr }">
                  <el-tag :type="caseStatusType(cr.status)" size="small">
                    {{ caseStatusText(cr.status) }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="耗时" width="90" align="center">
                <template #default="{ row: cr }">{{ formatDuration(cr.started_at, cr.ended_at) }}</template>
              </el-table-column>
              <el-table-column label="错误" min-width="180" show-overflow-tooltip>
                <template #default="{ row: cr }">{{ cr.error || '—' }}</template>
              </el-table-column>
              <el-table-column label="操作" width="90">
                <template #default="{ row: cr }">
                  <el-button size="small" @click="openCaseDetail(cr)">详情</el-button>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="序号" type="index" width="60" align="center" />
      <el-table-column label="测试计划" min-width="180">
        <template #default="{ row }">
          <b>{{ row.template_name || `[已删除计划 #${row.template_id}]` }}</b>
          <el-tag v-if="!row.template_id" type="info" size="small" effect="plain">计划已删</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="结果" width="100">
        <template #default="{ row }">
          <el-tag :type="runStatusType(row.status)" size="small">
            {{ runStatusText(row.status) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="用例汇总" width="180">
        <template #default="{ row }">
          <span class="summary">
            <el-tag type="success" size="small">通过 {{ row.summary?.passed || 0 }}</el-tag>
            <el-tag type="danger" size="small">失败 {{ (row.summary?.failed || 0) + (row.summary?.error || 0) + (row.summary?.timeout || 0) }}</el-tag>
            <el-tag type="info" size="small" v-if="row.summary?.skipped">跳过 {{ row.summary.skipped }}</el-tag>
          </span>
        </template>
      </el-table-column>
      <el-table-column label="开始时间" width="170">
        <template #default="{ row }">{{ formatDt(row.started_at) }}</template>
      </el-table-column>
      <el-table-column label="结束时间" width="170">
        <template #default="{ row }">{{ formatDt(row.ended_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="160">
        <template #default="{ row }">
          <el-button size="small" @click="toggleRunExpand(row)">详情</el-button>
          <el-button type="danger" plain size="small" @click="softDeleteRun(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 新建/编辑测试计划弹框 -->
    <el-dialog v-model="tplEditVisible" :title="tplForm.id ? `编辑计划 #${tplForm.id}` : '新建测试计划'" width="640px">
      <el-form :model="tplForm" label-width="90px">
        <el-form-item label="计划ID" v-if="tplForm.id">
          <span class="tpl-id">{{ tplForm.id }}</span>
        </el-form-item>
        <el-form-item label="计划名">
          <el-input v-model="tplForm.name" placeholder="给这个计划起个名，如：冒烟回归" />
        </el-form-item>
        <el-form-item label="用例">
          <el-select v-model="tplForm.case_ids" multiple filterable clearable
                     collapse-tags collapse-tags-tooltip
                     placeholder="下拉搜索、多选或勾选用例（支持单选、多选、批量勾选）"
                     style="width:100%">
            <el-option v-for="c in allCases" :key="c.id"
                       :label="`#${c.id} ${c.name}${c.module ? ' - ' + c.module : ''}`"
                       :value="c.id" />
          </el-select>
          <div class="tip">已选 {{ tplForm.case_ids.length }} 条，将按下拉框中的选择顺序串行执行</div>
        </el-form-item>
        <el-form-item label="失败策略">
          <el-radio-group v-model="tplForm.on_failure">
            <el-radio-button label="continue">失败跳过（继续下一条）</el-radio-button>
            <el-radio-button label="abort">失败中止（剩余标跳过）</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="单条超时">
          <el-input-number v-model="tplForm.case_timeout" :min="30" :max="1800" :step="30" />
          <span class="tip" style="margin-left:8px">秒。超时后该条判失败，按上面策略处理</span>
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="tplForm.description" type="textarea" :rows="2" placeholder="计划用途、备注（可选）" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="tplEditVisible = false">取消</el-button>
        <el-button type="primary" @click="saveTemplate">保存</el-button>
      </template>
    </el-dialog>

    <!-- 用例详情抽屉：步骤 + 日志 + 截图 -->
    <el-drawer v-model="drawerVisible" :title="drawerTitle" size="60%">
      <div v-if="drawerCase" class="drawer-body">
        <el-descriptions :column="2" size="small" border>
          <el-descriptions-item label="用例名">{{ drawerCase.case_name }}</el-descriptions-item>
          <el-descriptions-item label="状态">
            <el-tag :type="caseStatusType(drawerCase.status)" size="small">
              {{ caseStatusText(drawerCase.status) }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="开始">{{ formatDt(drawerCase.started_at) }}</el-descriptions-item>
          <el-descriptions-item label="结束">{{ formatDt(drawerCase.ended_at) }}</el-descriptions-item>
          <el-descriptions-item :span="2" label="错误" v-if="drawerCase.error">
            <span class="err-msg">{{ drawerCase.error }}</span>
          </el-descriptions-item>
        </el-descriptions>

        <div class="drawer-sec">
          <div class="drawer-sec-title">步骤结果</div>
          <el-timeline v-if="(drawerCase.step_results || []).length">
            <el-timeline-item
              v-for="(s, i) in drawerCase.step_results" :key="i"
              :type="s.ok ? 'success' : 'danger'"
              :timestamp="`步骤 ${(s.index ?? i) + 1}`"
              placement="top">
              <div><b>{{ s.text }}</b></div>
              <div v-if="s.detail" class="step-detail">{{ s.detail }}</div>
            </el-timeline-item>
          </el-timeline>
          <div v-else class="tip">无步骤数据</div>
        </div>

        <div class="drawer-sec" v-if="(drawerCase.logs || []).length">
          <div class="drawer-sec-title">日志</div>
          <div class="logs">
            <div v-for="(l, i) in drawerCase.logs" :key="i" :class="['log', l.level || 'info']">
              <span class="log-ts">{{ l.ts }}</span> {{ l.text }}
            </div>
          </div>
        </div>

        <div class="drawer-sec" v-if="(drawerCase.screenshots || []).length">
          <div class="drawer-sec-title">失败截图</div>
          <div class="shots">
            <a v-for="sh in drawerCase.screenshots" :key="sh.id"
               :href="`/api/execution-screenshots/${sh.id}`" target="_blank">
              <img :src="`/api/execution-screenshots/${sh.id}`" class="shot" :alt="sh.name" />
            </a>
          </div>
        </div>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount, computed } from 'vue'
import axios from 'axios'
import { ElMessage, ElMessageBox } from 'element-plus'

// ------ 数据 ------
const templates = ref([])
const runs = ref([])
const allCases = ref([])

// 测试计划：按 id 升序（先建的靠前，符合"顺序排"直觉）
const templatesSorted = computed(() =>
  [...templates.value].sort((a, b) => (a.id || 0) - (b.id || 0))
)
// 执行记录：按开始时间倒序；未开始（started_at 为空）的用 id 兜底放最前
const runsSorted = computed(() =>
  [...runs.value].sort((a, b) => {
    const ta = a.started_at ? new Date(a.started_at).getTime() : Number.MAX_SAFE_INTEGER
    const tb = b.started_at ? new Date(b.started_at).getTime() : Number.MAX_SAFE_INTEGER
    if (tb !== ta) return tb - ta
    return (b.id || 0) - (a.id || 0)
  })
)

// ------ 测试计划弹框 ------
const tplEditVisible = ref(false)
const emptyTplForm = () => ({
  id: null, name: '', case_ids: [], on_failure: 'continue', case_timeout: 180, description: '',
})
const tplForm = ref(emptyTplForm())

// ------ 执行记录展开缓存 ------
const runDetails = ref({})        // {run_id: {case_results, template_snapshot, ...}}
const loadingRun = ref({})        // {run_id: bool}
const expandKeys = ref([])        // 受控展开的 run.id 列表（由「详情」按钮切换）

function toggleRunExpand(row) {
  const i = expandKeys.value.indexOf(row.id)
  if (i >= 0) {
    expandKeys.value.splice(i, 1)
  } else {
    expandKeys.value.push(row.id)
    // 受控展开不会触发 @expand-change，这里主动拉一次详情
    if (!runDetails.value[row.id]) fetchRunDetail(row.id)
  }
}

// ------ 抽屉 ------
const drawerVisible = ref(false)
const drawerCase = ref(null)
const drawerTitle = computed(() => drawerCase.value ? `用例详情：${drawerCase.value.case_name}` : '用例详情')

// ------ WS + 轮询定时 ------
let ws = null
let pollTimer = null

// ------ 加载 ------
async function loadTemplates() {
  const { data } = await axios.get('/api/execution-templates')
  templates.value = data || []
}
async function loadRuns() {
  const { data } = await axios.get('/api/execution-runs')
  runs.value = data || []
  // 已展开的 run 若还在跑，刷新其详情
  for (const rid of Object.keys(runDetails.value)) {
    const numId = Number(rid)
    const r = runs.value.find(x => x.id === numId)
    if (r && r.status === 'running') {
      await fetchRunDetail(numId)
    }
  }
}
async function loadCases() {
  const { data } = await axios.get('/api/cases')
  // 只显示 active 用例；draft 不参与批量执行（避免草稿混进模板）
  allCases.value = (data || []).filter(c => c.status !== 'draft')
}

async function fetchRunDetail(runId) {
  loadingRun.value[runId] = true
  try {
    const { data } = await axios.get(`/api/execution-runs/${runId}`)
    runDetails.value[runId] = data
  } finally {
    loadingRun.value[runId] = false
  }
}

// ------ 测试计划 CRUD ------
function openTplEdit(row) {
  if (row) {
    tplForm.value = {
      id: row.id, name: row.name,
      case_ids: [...(row.case_ids || [])],
      on_failure: row.on_failure || 'continue',
      case_timeout: row.case_timeout || 180,
      description: row.description || '',
    }
  } else {
    tplForm.value = emptyTplForm()
  }
  tplEditVisible.value = true
}

async function saveTemplate() {
  if (!tplForm.value.name.trim()) { ElMessage.warning('请填写计划名'); return }
  if (!tplForm.value.case_ids.length) { ElMessage.warning('至少选择一条用例'); return }
  const payload = {
    name: tplForm.value.name.trim(),
    case_ids: tplForm.value.case_ids,
    on_failure: tplForm.value.on_failure,
    case_timeout: tplForm.value.case_timeout,
    description: tplForm.value.description,
  }
  if (tplForm.value.id) {
    await axios.put(`/api/execution-templates/${tplForm.value.id}`, payload)
  } else {
    await axios.post('/api/execution-templates', payload)
  }
  tplEditVisible.value = false
  ElMessage.success('已保存')
  loadTemplates()
}

async function removeTemplate(row) {
  await ElMessageBox.confirm(
    `确认删除计划「${row.name}」？该计划的所有执行记录、用例结果、截图将一并物理删除，不可恢复。`,
    '删除确认',
    { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
  )
  await axios.delete(`/api/execution-templates/${row.id}`)
  ElMessage.success('已删除')
  loadTemplates()
  loadRuns()
}

async function runTemplate(row) {
  try {
    const { data } = await axios.post(`/api/execution-templates/${row.id}/run`)
    const runId = data.run_id
    ElMessage.success(`已开始执行 #${runId}`)
    await loadRuns()
    // 开 WebSocket 订阅这次运行
    openRunWs(runId)
  } catch (e) {
    ElMessage.error(`执行失败：${e?.response?.data?.detail || e.message}`)
  }
}

// ------ 执行记录 ------
async function onRunExpand(row, expanded) {
  if (!expanded.includes(row)) return  // 收起时不管
  await fetchRunDetail(row.id)
}

async function softDeleteRun(row) {
  await ElMessageBox.confirm(
    `确认删除执行记录 #${row.id}？记录将被逻辑删除，超 7 天由系统物理清理。`,
    '删除确认',
    { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
  )
  await axios.delete(`/api/execution-runs/${row.id}`)
  ElMessage.success('已删除')
  loadRuns()
}

async function openCaseDetail(cr) {
  // 直接拉最新（含 screenshots）
  const { data } = await axios.get(`/api/execution-case-results/${cr.id}`)
  drawerCase.value = data
  drawerVisible.value = true
}

// ------ WebSocket ------
function openRunWs(runId) {
  if (ws) { try { ws.close() } catch (e) {} ws = null }
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const url = `${proto}://${location.host}/ws/execution-runs/${runId}`
  ws = new WebSocket(url)
  ws.onmessage = ev => {
    let msg
    try { msg = JSON.parse(ev.data) } catch (e) { return }
    // 一到 case_end / done 就刷新记录列表和展开详情
    if (msg.kind === 'case_end' || msg.kind === 'done' || msg.kind === 'run_start') {
      loadRuns()
      if (runDetails.value[runId]) fetchRunDetail(runId)
    }
    if (msg.kind === 'done') { try { ws.close() } catch (e) {} ws = null }
  }
  ws.onerror = () => {}
  ws.onclose = () => { ws = null }
}

// ------ 显示辅助 ------
function runStatusType(s) {
  return ({ passed: 'success', failed: 'danger', error: 'danger',
            running: 'warning', queued: 'info' })[s] || 'info'
}
function runStatusText(s) {
  return ({ passed: '成功', failed: '失败', error: '异常',
            running: '执行中', queued: '排队中' })[s] || s
}
function caseStatusType(s) {
  return ({ passed: 'success', failed: 'danger', error: 'danger',
            timeout: 'danger', skipped: 'info',
            running: 'warning', queued: 'info' })[s] || 'info'
}
function caseStatusText(s) {
  return ({ passed: '成功', failed: '失败', error: '异常',
            timeout: '超时', skipped: '跳过',
            running: '执行中', queued: '排队中' })[s] || s
}
function formatDt(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  const pad = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}
function formatDuration(startIso, endIso) {
  if (!startIso || !endIso) return '—'
  const ms = new Date(endIso).getTime() - new Date(startIso).getTime()
  if (ms < 0) return '—'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

// ------ 生命周期 ------
onMounted(() => {
  loadTemplates()
  loadRuns()
  loadCases()
  // 兜底：running 状态下每 5s 拉一次列表（防 WS 断开后没数据）
  pollTimer = setInterval(() => {
    if (runs.value.some(r => r.status === 'running' || r.status === 'queued')) {
      loadRuns()
    }
  }, 5000)
})
onBeforeUnmount(() => {
  if (ws) { try { ws.close() } catch (e) {} }
  if (pollTimer) clearInterval(pollTimer)
})
</script>

<style scoped>
.exec-view { padding: 0; }
.bar { display: flex; align-items: center; justify-content: space-between; margin: 8px 0; }
.sec-title { font-size: 14px; font-weight: 600; color: #303133; }
.tip { color: #909399; font-size: 12px; }
.tpl-id { font-family: Menlo, monospace; color: #303133; }
.run-expand { padding: 8px 24px 12px 48px; background: #fafbfc; }
.inner-table :deep(th) { background: #eef2f7 !important; }
.summary { display: inline-flex; gap: 4px; flex-wrap: wrap; }
.err-msg { color: #f56c6c; }
.drawer-body { padding: 4px 16px 24px; }
.drawer-sec { margin-top: 16px; }
.drawer-sec-title { font-weight: 600; margin-bottom: 8px; color: #303133; }
.step-detail { color: #606266; font-size: 12px; margin-top: 2px; }
.logs { max-height: 260px; overflow: auto; background: #f5f7fa; border-radius: 4px; padding: 8px 12px; }
.log { font-family: Menlo, monospace; font-size: 12px; line-height: 1.6; color: #303133; }
.log.error { color: #f56c6c; }
.log.warn { color: #e6a23c; }
.log-ts { color: #909399; margin-right: 6px; }
.shots { display: flex; flex-wrap: wrap; gap: 8px; }
.shot { max-width: 320px; max-height: 240px; border: 1px solid #dcdfe6; border-radius: 4px; }

/* 隐藏内建的 expand 列：宽度压到 0、不显示图标格，仍保留展开容器 */
:deep(.el-table th.col-hidden),
:deep(.el-table td.col-hidden) {
  width: 0 !important;
  padding: 0 !important;
  border-right: 0 !important;
}
:deep(.el-table th.col-hidden > .cell),
:deep(.el-table td.col-hidden > .cell) { display: none; }
:deep(.el-table td.col-hidden .el-table__expand-icon) { display: none; }

</style>
