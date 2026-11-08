<template>
  <div>
    <div class="bar">
      <span class="sec-title">用例列表</span>
      <span>
        <el-button type="success" size="small" @click="genVisible = true">AI 生成用例</el-button>
        <el-button type="primary" size="small" @click="openEdit()">新建用例</el-button>
        <el-button type="danger" size="small" :disabled="!selection.length"
                   @click="onBatchDelete">删除选中{{ selection.length ? `（${selection.length}）` : '' }}</el-button>
      </span>
    </div>

    <el-row :gutter="12">
      <!-- 左：用例表格 -->
      <el-col :span="13">
        <el-table :data="cases" border size="small"
                  :default-sort="{ prop: 'id', order: 'ascending' }"
                  @sort-change="onSortChange"
                  @selection-change="onSelectionChange">
          <el-table-column type="selection" width="42" />
          <el-table-column label="" width="62" sortable="custom" prop="id" align="center" header-align="center">
            <template #default="{ row }">{{ indexOf(row) }}</template>
          </el-table-column>
          <el-table-column label="用例名" min-width="130">
            <template #default="{ row }">
              {{ row.name }}
              <el-tag v-if="row.status === 'draft'" type="warning" size="small">草稿</el-tag>
              <el-tag v-if="row.source && row.source.startsWith('ai')" type="success" size="small" effect="plain">AI</el-tag>
              <el-tag v-if="row.script_py" type="info" size="small" effect="plain">已编译</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="module" label="模块" width="90" />
          <el-table-column label="步骤" min-width="160">
            <template #default="{ row }">
              <div v-for="(s, i) in row.steps" :key="i" class="step-line">
                {{ i + 1 }}. {{ stepText(s) }}
                <div v-for="(p, j) in stepParallels(s)" :key="j" class="parallel-line">
                  ‖ 并行接口场景：{{ p.name }}
                </div>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="310">
            <template #default="{ row }">
              <el-button v-if="row.status === 'draft'" type="warning" size="small" @click="approve(row)">审核入库</el-button>
              <el-button type="primary" size="small" :disabled="running" @click="runCase(row)">执行</el-button>
              <el-button size="small" @click="openEdit(row)">编辑</el-button>
              <el-button size="small" @click="copyCase(row)">复制</el-button>
              <el-button size="small" :disabled="!row.script_py" @click="openScript(row)">脚本</el-button>
              <el-tooltip content="删除" placement="top">
                <el-button type="danger" plain circle size="small" :icon="Delete" @click="removeCase(row)" />
              </el-tooltip>
            </template>
          </el-table-column>
        </el-table>
      </el-col>

      <!-- 右：实时视图 + 日志 -->
      <el-col :span="11">
        <div class="bar">
          <span class="sec-title">实时视图 {{ runId ? `（run #${runId}）` : '' }}</span>
          <div style="display:flex;gap:8px;align-items:center">
            <el-button v-if="running" size="small" type="danger" plain @click="stopRun">停止执行</el-button>
            <el-tag v-if="status" :type="statusType">{{ statusText }}</el-tag>
          </div>
        </div>
        <div class="screen-wrap">
          <canvas ref="canvas" class="screen"></canvas>
          <div v-if="!frameCount" class="hint">
            {{ running ? '等待浏览器画面…' : '点击某条用例的「执行」，这里实时显示浏览器操作过程' }}
          </div>
        </div>

        <!-- AI 事件高亮：自愈定位 / VLM 视觉断言 -->
        <div class="heal-panel" v-if="healEvents.length">
          <div class="heal-title">AI 事件</div>
          <div v-for="(h, i) in healEvents" :key="i" :class="['heal-card', healClass(h)]">
            <span class="heal-badge">{{ healBadge(h) }}</span>
            <span class="heal-body">
              <template v-if="h.kind === 'assert'">
                视觉断言「{{ h.claim }}」：<b>{{ h.passed ? '通过' : '未通过' }}</b> — {{ h.reason }}
              </template>
              <template v-else-if="h.kind === 'vlm'">
                <b>VLM 自愈</b>：「{{ h.target }}」原选择器失效，视觉重定位并回写 → <code>{{ h.selector }}</code>
              </template>
              <template v-else>
                定位「{{ h.target }}」（{{ h.strategy }}）→ <code>{{ h.selector }}</code>
              </template>
            </span>
          </div>
        </div>

        <div class="shots" v-if="shots.length">
          <div class="shots-title">失败截图</div>
          <img v-for="(sp, i) in shots" :key="i" :src="`/api/screenshots/${sp}`" class="shot" />
        </div>
        <div class="logs">
          <div v-for="(l, i) in logs" :key="i" :class="['log', l.level]">{{ l.text }}</div>
        </div>
      </el-col>
    </el-row>

    <!-- 用例编辑弹窗（P3） -->
    <el-dialog v-model="editVisible" :title="form.id ? `编辑用例 #${form.id}` : '新建用例'" width="560px">
      <el-form :model="form" label-width="80px">
        <el-form-item label="用例名"><el-input v-model="form.name" /></el-form-item>
        <el-form-item label="模块"><el-input v-model="form.module" /></el-form-item>
        <el-form-item label="标签"><el-input v-model="tagsText" placeholder="逗号分隔，如 冒烟,只读" /></el-form-item>
        <el-form-item label="步骤">
          <div style="width:100%">
            <div v-for="(s, i) in form.steps" :key="i" class="step-block">
              <div class="step-edit">
                <span class="step-idx">{{ i + 1 }}</span>
                <el-input v-model="s.text" type="textarea" :autosize="{ minRows: 1, maxRows: 4 }"
                          resize="none" class="step-input"
                          placeholder="用自然语言描述一步，如 进入 系统设置 → 用户管理" />
                <div class="step-actions">
                  <el-dropdown size="small" trigger="click" @command="cmd => onInsertCmd(cmd, i)">
                    <el-button size="small" plain circle :icon="Top" title="上方插入" />
                    <template #dropdown>
                      <el-dropdown-menu>
                        <el-dropdown-item command="text:above">插入普通步骤</el-dropdown-item>
                        <el-dropdown-item command="scene:above">插入接口场景</el-dropdown-item>
                      </el-dropdown-menu>
                    </template>
                  </el-dropdown>
                  <el-dropdown size="small" trigger="click" @command="cmd => onInsertCmd(cmd, i)">
                    <el-button size="small" plain circle :icon="Bottom" title="下方插入" />
                    <template #dropdown>
                      <el-dropdown-menu>
                        <el-dropdown-item command="text:below">插入普通步骤</el-dropdown-item>
                        <el-dropdown-item command="scene:below">插入接口场景</el-dropdown-item>
                      </el-dropdown-menu>
                    </template>
                  </el-dropdown>
                  <el-tooltip content="添加并行接口场景" placement="top">
                    <el-button size="small" type="primary" plain circle :icon="Connection" @click="openParallelPicker(i)" />
                  </el-tooltip>
                  <el-tooltip :content="s.locator_hint ? '已设置定位' : '指定元素定位'" placement="top">
                    <el-button size="small" :type="s.locator_hint ? 'warning' : 'default'" plain circle
                               :icon="Aim" @click="s._showLocator = !s._showLocator" />
                  </el-tooltip>
                  <el-tooltip content="删除步骤" placement="top">
                    <el-button size="small" type="danger" plain circle :icon="Delete" @click="removeStep(i)" />
                  </el-tooltip>
                </div>
              </div>
              <!-- 定位提示面板（可展开） -->
              <div v-if="s._showLocator" class="locator-panel">
                <div class="locator-header">
                  <span class="locator-title">元素定位提示</span>
                  <span class="locator-tip">设置后执行此步时优先使用，覆盖自动三级定位</span>
                </div>
                <div class="locator-row">
                  <el-select v-model="s.locator_hint.type" size="small" style="width:130px;flex-shrink:0"
                             placeholder="定位方式">
                    <el-option label="XPath" value="xpath" />
                    <el-option label="CSS 选择器" value="css" />
                    <el-option label="精确文字" value="text" />
                    <el-option label="包含文字" value="hastext" />
                    <el-option label="Role" value="role" />
                    <el-option label="Label" value="label" />
                    <el-option label="Placeholder" value="placeholder" />
                    <el-option label="data-testid" value="testid" />
                    <el-option label="id属性" value="id" />
                    <el-option label="name属性" value="name" />
                  </el-select>
                  <el-input v-model="s.locator_hint.value" size="small" clearable
                            placeholder="定位值，如 //button[text()='确定起飞'] 或 .ant-btn-primary"
                            style="flex:1" @clear="clearLocatorHint(s)" />
                  <el-button size="small" type="danger" text @click="clearLocatorHint(s)">清除</el-button>
                </div>
                <div class="locator-examples">
                  示例：XPath <code>//button[contains(text(),'确定')]</code> &nbsp;|&nbsp;
                  CSS <code>.ant-modal-confirm-btns .ant-btn-primary</code> &nbsp;|&nbsp;
                  Role <code>button[name="确定起飞"]</code>
                </div>
              </div>
              <div v-for="(p, j) in s.parallel" :key="j" class="parallel-item">
                <span class="parallel-tag">‖ 并行接口场景</span>
                <span class="parallel-name">{{ p.name }}</span>
                <el-button size="small" type="danger" text :icon="Delete" @click="s.parallel.splice(j, 1)" />
              </div>
            </div>
            <el-button size="small" @click="insertStep(form.steps.length)">+ 添加步骤</el-button>
            <el-button size="small" type="primary" plain @click="openScenarioPicker">+ 接口场景</el-button>
            <div class="step-hint">
              支持的步骤：登录系统 / 进入 X → Y / 点击 X / 确认 X / 确认页面正常无报错 /
              <b>[接口场景#N] 场景名</b>。<br/>
              可用「上方/下方插入」在任意位置加步骤；点某步的「并行」图标为其挂接口场景，<br/>
              该 UI 步骤与并行场景<b>同时执行</b>、全部通过才进入下一步；登录后提取的 <code>${token}</code> 自动共享给场景使用。
            </div>
          </div>
        </el-form-item>
        <el-form-item>
          <template #label>
            数据操作
            <el-tooltip placement="top">
              <template #content>
                是否允许增删改数据。开启后用例可执行会改变系统<br/>
                数据或状态的操作，如新增 / 修改 / 删除 / 提交 / 保存等。<br/>
                本平台默认<b>只读</b>（只做查看/导航/搜索/断言），<br/>
                以免污染共享测试环境。仅在明确需要且已确认<br/>
                安全时才开启。
              </template>
              <el-icon class="help-icon"><QuestionFilled /></el-icon>
            </el-tooltip>
          </template>
          <el-switch v-model="form.allow_risky" active-text="是否允许增删改数据" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" @click="saveCase">保存</el-button>
      </template>
    </el-dialog>

    <!-- AI 生成用例弹窗（P3.6） -->
    <el-dialog v-model="genVisible" title="AI 生成用例" width="600px">
      <el-form label-width="80px">
        <el-form-item label="生成方式">
          <el-radio-group v-model="genMode">
            <el-radio-button label="requirement">需求描述</el-radio-button>
            <el-radio-button label="explore">页面探索</el-radio-button>
          </el-radio-group>
        </el-form-item>

        <template v-if="genMode === 'requirement'">
          <el-form-item label="需求描述">
            <el-input v-model="genReq" type="textarea" :rows="3" placeholder="用自然语言描述要测什么，如：测试系统设置下各页面能正常打开并展示数据" />
          </el-form-item>
        </template>
        <template v-else>
          <el-form-item label="目标页面">
            <el-select v-model="exploreTop" placeholder="一级菜单" style="width:150px" @change="exploreSub=''">
              <el-option v-for="t in Object.keys(menuMap)" :key="t" :label="t" :value="t" />
            </el-select>
            <el-select v-model="exploreSub" placeholder="二级菜单" style="width:150px;margin-left:8px">
              <el-option v-for="sub in (menuMap[exploreTop] || [])" :key="sub" :label="sub" :value="sub" />
            </el-select>
          </el-form-item>
          <el-form-item label="">
            <span class="tip">页面探索会真实打开该页面、用视觉模型看懂控件后生成贴合实际的用例（较慢，约 30~60 秒）。</span>
          </el-form-item>
        </template>

        <el-form-item label="生成数量">
          <el-input-number v-model="genCount" :min="1" :max="8" />
        </el-form-item>
      </el-form>
      <el-alert type="info" :closable="false" show-icon
        title="AI 生成结果为『草稿』，需人工审核后入库；仅生成只读类步骤，不含破坏性操作。" />
      <template #footer>
        <el-button @click="genVisible = false">取消</el-button>
        <el-button type="success" :loading="generating" @click="doGenerate">生成草稿</el-button>
      </template>
    </el-dialog>

    <!-- 选择接口场景弹窗 -->
    <el-dialog v-model="scenarioPickerVisible" title="选择接口场景" width="640px">
      <el-input v-model="scenarioKeyword" size="small" placeholder="按名称 / 模块过滤" clearable style="margin-bottom:8px" />
      <el-table :data="filteredScenarios" border size="small" highlight-current-row
                @current-change="onScenarioRowChange" height="360">
        <el-table-column width="50" label="选">
          <template #default="{ row }">
            <el-radio :label="row.id" v-model="pickedScenarioId" @change="pickedScenario = row">&nbsp;</el-radio>
          </template>
        </el-table-column>
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column prop="name" label="场景名" min-width="140" />
        <el-table-column prop="module" label="模块" width="100" />
        <el-table-column label="步骤数" width="70">
          <template #default="{ row }">{{ (row.steps || []).length }}</template>
        </el-table-column>
        <el-table-column prop="description" label="描述" min-width="140" show-overflow-tooltip />
      </el-table>
      <div v-if="!scenarios.length" class="tip" style="margin-top:8px">
        暂无接口场景。请先到「接口场景」页面新建。
      </div>
      <template #footer>
        <el-button @click="scenarioPickerVisible = false">取消</el-button>
        <el-button type="primary" :disabled="!pickedScenario" @click="confirmPickScenario">添加为步骤</el-button>
      </template>
    </el-dialog>

    <!-- 查看/编辑脚本（Playwright Python 脚本）-->
    <el-dialog v-model="scriptVisible" :title="`Playwright 脚本 #${scriptCase.id} ${scriptCase.name}`" width="860px">
      <div class="script-meta" v-if="scriptCase.script_updated_at">
        编译时间：{{ scriptCase.script_updated_at }} · 修改用例步骤后自动作废，重新执行可触发重新编译。
      </div>
      <el-input
        v-model="scriptPyContent"
        type="textarea"
        :autosize="{ minRows: 18, maxRows: 36 }"
        class="script-editor"
        placeholder="暂无脚本，执行用例后自动 AI 编译生成"
        spellcheck="false"
      />
      <template #footer>
        <el-button :loading="compiling" type="primary" plain @click="recompileScript">重新编译</el-button>
        <el-button type="success" :disabled="!scriptPyContent" @click="saveScriptPy">保存修改</el-button>
        <el-button @click="scriptVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import axios from 'axios'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, Top, Bottom, Connection, Aim } from '@element-plus/icons-vue'
import { useIdSort } from '../composables/useIdSort'
import { useMultiSelect } from '../composables/useMultiSelect'

const { items: cases, setItems: setCases, onSortChange, indexOf } = useIdSort()
const { selection, onSelectionChange, batchDelete, confirmRemove } = useMultiSelect()
const running = ref(false)
const runId = ref(null)
const status = ref('')
const logs = ref([])
const healEvents = ref([])
const shots = ref([])
const frameCount = ref(0)
const canvas = ref(null)
// 投屏渲染节流：CDP 稳定期可达 ~120fps，远超前端 canvas 绘制能力，逐帧绘制会累积延迟。
// 故只保留"最新一帧"（新帧覆盖旧的未渲染帧 = 自动丢弃过期/重复帧），用 rAF 每刷新周期
// 只画最新一帧，渲染频率被限制在显示器刷新率(~60fps)，前端永不落后。符合"流畅最新即可"。
let pendingFrame = null   // 最新待渲染的 ArrayBuffer（JPEG）
let rafId = 0             // requestAnimationFrame 句柄，0 表示未运行
let frameDecoding = false // 上一帧是否仍在异步解码，防止并发解码堆积

const editVisible = ref(false)
const form = ref({ id: null, name: '', module: '', tags: [], steps: [{ text: '', parallel: [] }], allow_risky: false })
const tagsText = ref('')

const genVisible = ref(false)
const genMode = ref('requirement')
const genReq = ref('')
const genCount = ref(3)
const generating = ref(false)
const exploreTop = ref('')
const exploreSub = ref('')
// 站点菜单映射示例（页面探索生成的下拉提示用）：接入被测站点后按实际菜单修改
const menuMap = {
  '系统设置': ['用户管理', '角色管理', '参数配置'],
  '业务模块A': ['列表页', '统计页'],
}

// 接口场景选择器
const scenarios = ref([])
const scenarioPickerVisible = ref(false)
const scenarioKeyword = ref('')
const pickedScenario = ref(null)
const pickedScenarioId = ref(null)

// 查看/编辑 Playwright 脚本
const scriptVisible = ref(false)
const scriptCase = ref({})
const scriptPyContent = ref('')
const compiling = ref(false)

function openScript(row) {
  scriptCase.value = row
  scriptPyContent.value = row.script_py || ''
  scriptVisible.value = true
}

async function recompileScript() {
  compiling.value = true
  try {
    const { data } = await axios.post(`/api/cases/${scriptCase.value.id}/compile`)
    scriptPyContent.value = data.script_py || ''
    scriptCase.value = { ...scriptCase.value, script_py: data.script_py, script_updated_at: data.script_updated_at }
    // 同步更新列表里的行
    const idx = cases.value.findIndex(c => c.id === scriptCase.value.id)
    if (idx !== -1) cases.value[idx] = { ...cases.value[idx], script_py: data.script_py, script_updated_at: data.script_updated_at }
    ElMessage.success('脚本编译成功')
  } catch (e) {
    ElMessage.error('编译失败：' + (e.response?.data?.detail || e.message))
  } finally {
    compiling.value = false
  }
}

async function saveScriptPy() {
  try {
    const { data } = await axios.put(`/api/cases/${scriptCase.value.id}/script-py`, { script_py: scriptPyContent.value })
    scriptCase.value = { ...scriptCase.value, script_updated_at: data.script_updated_at }
    const idx = cases.value.findIndex(c => c.id === scriptCase.value.id)
    if (idx !== -1) cases.value[idx] = { ...cases.value[idx], script_py: scriptPyContent.value, script_updated_at: data.script_updated_at }
    ElMessage.success('脚本已保存')
  } catch (e) {
    ElMessage.error('保存失败：' + (e.response?.data?.detail || e.message))
  }
}
const pickerMode = ref('step')       // 'step'=新增独立场景步骤 | 'parallel'=挂到某步并行
const parallelTargetIndex = ref(-1)  // pickerMode='parallel' 时目标步骤下标
const pickerInsertIndex = ref(-1)    // pickerMode='step' 时场景插入位置（-1=末尾）

const filteredScenarios = computed(() => {
  const kw = scenarioKeyword.value.trim().toLowerCase()
  if (!kw) return scenarios.value
  return scenarios.value.filter(s =>
    (s.name || '').toLowerCase().includes(kw) ||
    (s.module || '').toLowerCase().includes(kw)
  )
})

async function loadScenarios() {
  try {
    const { data } = await axios.get('/api/api-scenarios')
    scenarios.value = data || []
  } catch (e) {
    scenarios.value = []
  }
}

async function openScenarioPicker() {
  pickerMode.value = 'step'
  parallelTargetIndex.value = -1
  pickerInsertIndex.value = -1
  pickedScenario.value = null
  pickedScenarioId.value = null
  scenarioKeyword.value = ''
  await loadScenarios()
  scenarioPickerVisible.value = true
}

async function openParallelPicker(i) {
  pickerMode.value = 'parallel'
  parallelTargetIndex.value = i
  pickedScenario.value = null
  pickedScenarioId.value = null
  scenarioKeyword.value = ''
  await loadScenarios()
  scenarioPickerVisible.value = true
}

function onScenarioRowChange(row) {
  if (!row) return
  pickedScenario.value = row
  pickedScenarioId.value = row.id
}

function confirmPickScenario() {
  const s = pickedScenario.value
  if (!s) return
  if (pickerMode.value === 'parallel') {
    const target = form.value.steps[parallelTargetIndex.value]
    if (target) {
      if (!target.parallel) target.parallel = []
      if (target.parallel.some(p => p.id === s.id)) {
        ElMessage.warning('该步骤已挂载此接口场景')
        return
      }
      target.parallel.push({ id: s.id, name: s.name })
    }
  } else {
    const newStep = { text: `[接口场景#${s.id}] ${s.name}`, parallel: [] }
    const idx = pickerInsertIndex.value
    if (idx >= 0 && idx <= form.value.steps.length) {
      form.value.steps.splice(idx, 0, newStep)
    } else {
      form.value.steps.push(newStep)
    }
  }
  scenarioPickerVisible.value = false
}

const statusText = computed(() => ({ running: '执行中', passed: '通过', failed: '失败', error: '错误', queued: '排队中', cancelled: '已停止' }[status.value] || status.value))
const statusType = computed(() => ({ passed: 'success', failed: 'danger', error: 'danger', running: 'warning', cancelled: 'info' }[status.value] || 'info'))

async function loadCases() {
  const { data } = await axios.get('/api/cases')
  setCases(data)
}

// 列表展示：兼容字符串 / 对象两种步骤形态
function stepText(s) { return typeof s === 'object' && s !== null ? (s.text || '') : s }
function stepParallels(s) { return typeof s === 'object' && s !== null ? (s.parallel || []) : [] }

// 持久化步骤（str|obj）→ 编辑模型 {text, parallel}
function _emptyLocatorHint() { return { type: 'xpath', value: '' } }

function toEditStep(s) {
  if (typeof s === 'object' && s !== null) {
    const hint = (s.locator_hint && s.locator_hint.type && s.locator_hint.value)
      ? { type: s.locator_hint.type, value: s.locator_hint.value }
      : null
    return {
      text: s.text || '',
      parallel: (s.parallel || []).map(p => ({ id: p.id, name: p.name })),
      locator_hint: hint || _emptyLocatorHint(),
      _showLocator: !!hint,
    }
  }
  return { text: s, parallel: [], locator_hint: _emptyLocatorHint(), _showLocator: false }
}

function openEdit(row) {
  if (row) {
    const steps = (row.steps && row.steps.length) ? row.steps.map(toEditStep) : [toEditStep('')]
    form.value = { id: row.id, name: row.name, module: row.module, tags: [...row.tags], steps, allow_risky: row.allow_risky }
    tagsText.value = (row.tags || []).join(',')
  } else {
    form.value = { id: null, name: '', module: '', tags: [], steps: [toEditStep('')], allow_risky: false }
    tagsText.value = ''
  }
  editVisible.value = true
}

function insertStep(i) {
  form.value.steps.splice(i, 0, toEditStep(''))
}

async function onInsertCmd(cmd, i) {
  // cmd = 'text:above' | 'text:below' | 'scene:above' | 'scene:below'
  const [type, dir] = cmd.split(':')
  const idx = dir === 'above' ? i : i + 1
  if (type === 'text') {
    insertStep(idx)
  } else {
    pickerMode.value = 'step'
    pickerInsertIndex.value = idx
    parallelTargetIndex.value = -1
    pickedScenario.value = null
    pickedScenarioId.value = null
    scenarioKeyword.value = ''
    await loadScenarios()
    scenarioPickerVisible.value = true
  }
}

function clearLocatorHint(s) {
  s.locator_hint = _emptyLocatorHint()
  s._showLocator = false
}

async function saveCase() {
  // 编辑模型 → 持久化：
  // - 有 locator_hint（type+value 非空）且/或有 parallel → 存对象
  // - 否则存字符串（兼容旧格式）
  const steps = form.value.steps
    .map(s => {
      const text = (s.text || '').trim()
      const parallel = s.parallel || []
      const hint = (s.locator_hint && s.locator_hint.type && s.locator_hint.value)
        ? { type: s.locator_hint.type, value: s.locator_hint.value }
        : null
      return { text, parallel, locator_hint: hint }
    })
    .filter(s => s.text || s.parallel.length)
    .map(s => {
      if (s.parallel.length || s.locator_hint) {
        const obj = { text: s.text, parallel: s.parallel }
        if (s.locator_hint) obj.locator_hint = s.locator_hint
        return obj
      }
      return s.text
    })
  const payload = {
    name: form.value.name, module: form.value.module,
    tags: tagsText.value.split(/[,，]/).map(s => s.trim()).filter(Boolean),
    steps,
    allow_risky: form.value.allow_risky,
  }
  if (!payload.name) { ElMessage.warning('请填写用例名'); return }
  if (form.value.id) await axios.put(`/api/cases/${form.value.id}`, payload)
  else await axios.post('/api/cases', payload)
  editVisible.value = false
  ElMessage.success('已保存')
  loadCases()
}

async function removeCase(row) {
  await ElMessageBox.confirm(`确认删除用例「${row.name}」？此操作不可恢复。`, '删除确认', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' })
  await axios.delete(`/api/cases/${row.id}`)
  ElMessage.success('已删除')
  loadCases()
}

async function copyCase(row) {
  await axios.post(`/api/cases/${row.id}/copy`)
  ElMessage.success('已复制为草稿，请审核后入库')
  loadCases()
}

function onBatchDelete() {
  batchDelete({
    label: '用例',
    deleteOne: r => axios.delete(`/api/cases/${r.id}`),
    onDone: loadCases,
  })
}

async function removeStep(i) {
  const s = form.value.steps[i]
  if (!(await confirmRemove({ label: '步骤', name: s.text || '(空步骤)' }))) return
  form.value.steps.splice(i, 1)
  if (!form.value.steps.length) form.value.steps.push({ text: '', parallel: [] })
}

async function doGenerate() {
  generating.value = true
  try {
    let data
    if (genMode.value === 'explore') {
      if (!exploreTop.value || !exploreSub.value) { ElMessage.warning('请选择目标页面'); generating.value = false; return }
      ;({ data } = await axios.post('/api/cases/explore', { top: exploreTop.value, sub: exploreSub.value, count: genCount.value }))
    } else {
      if (!genReq.value.trim()) { ElMessage.warning('请填写需求描述'); generating.value = false; return }
      ;({ data } = await axios.post('/api/cases/generate', { requirement: genReq.value, count: genCount.value }))
    }
    ElMessage.success(`已生成 ${data.count} 条草稿，请审核后入库`)
    genVisible.value = false
    genReq.value = ''
    loadCases()
  } catch (e) {
    ElMessage.error('生成失败：' + (e.response?.data?.detail || e.message))
  } finally {
    generating.value = false
  }
}

async function approve(row) {
  await axios.post(`/api/cases/${row.id}/approve`)
  ElMessage.success('已审核入库')
  loadCases()
}

function addLog(text, level = 'info') { logs.value.push({ text, level }) }

function healClass(h) {
  if (h.kind === 'assert') return h.passed ? 'ok' : 'err'
  if (h.kind === 'vlm') return 'vlm'
  return 'locate'
}
function healBadge(h) {
  if (h.kind === 'assert') return '👁 视觉断言'
  if (h.kind === 'vlm') return '🔧 VLM 自愈'
  return '🎯 定位'
}

async function runCase(row) {
  running.value = true; status.value = 'queued'; logs.value = []; shots.value = []; frameCount.value = 0; healEvents.value = []
  addLog(`▶ 开始执行「${row.name}」`)
  const { data } = await axios.post(`/api/cases/${row.id}/run`)
  runId.value = data.run_id
  openWs(data.run_id)
}

function openWs(id) {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const ws = new WebSocket(`${proto}://${location.host}/ws/runs/${id}`)
  ws.binaryType = 'arraybuffer'  // 投屏帧走二进制帧（JPEG），省 base64 膨胀与解码
  const ctx = canvas.value?.getContext('2d')
  startRenderLoop(ctx)  // 启动 rAF 渲染循环（只画最新帧，节流到刷新率）
  ws.onmessage = (ev) => {
    // 二进制消息 = 一帧 JPEG；文本消息 = JSON 事件（step/log/heal/done）
    // 帧只暂存最新一帧（覆盖旧的未渲染帧 = 丢弃过期/重复帧），实际绘制交给 rAF 循环
    if (ev.data instanceof ArrayBuffer) { pendingFrame = ev.data; return }
    const { kind, payload } = JSON.parse(ev.data)
    if (kind === 'heal') healEvents.value.push(payload)
    else if (kind === 'step') {
      status.value = 'running'
      const icon = payload.state === 'passed' ? '✓' : payload.state === 'failed' ? '✗' : '…'
      const lvl = payload.state === 'failed' ? 'err' : payload.state === 'passed' ? 'ok' : 'info'
      addLog(`${icon} 步骤${payload.index + 1}：${payload.text}${payload.detail ? ' — ' + payload.detail : ''}`, lvl)
    } else if (kind === 'log') addLog(`  ${payload.msg}`, 'dim')
    else if (kind === 'done') {
      status.value = payload.status
      addLog(`■ 执行结束：${statusText.value}`, payload.status === 'passed' ? 'ok' : 'err')
      running.value = false; stopRenderLoop(); ws.close(); fetchResult(id)
    }
  }
  ws.onerror = () => { addLog('WebSocket 连接错误', 'err'); running.value = false; stopRenderLoop() }
}

function startRenderLoop(ctx) {
  if (rafId) return
  const tick = () => {
    // 每刷新周期只解码+绘制最新一帧；上一帧仍在解码则跳过本轮，避免并发解码堆积
    if (pendingFrame && !frameDecoding && ctx) {
      const buf = pendingFrame; pendingFrame = null; frameDecoding = true
      createImageBitmap(new Blob([buf], { type: 'image/jpeg' })).then((bmp) => {
        const cv = canvas.value
        if (cv) {
          if (cv.width !== bmp.width) cv.width = bmp.width
          if (cv.height !== bmp.height) cv.height = bmp.height
          ctx.drawImage(bmp, 0, 0)
          frameCount.value++
        }
        bmp.close()
      }).catch(() => {}).finally(() => { frameDecoding = false })
    }
    rafId = requestAnimationFrame(tick)
  }
  rafId = requestAnimationFrame(tick)
}

function stopRenderLoop() {
  if (rafId) { cancelAnimationFrame(rafId); rafId = 0 }
  pendingFrame = null; frameDecoding = false
}

async function stopRun() {
  if (!runId.value) return
  try {
    await axios.post(`/api/runs/${runId.value}/cancel`)
    addLog('正在停止执行…', 'dim')
  } catch (e) {
    ElMessage.error('停止失败：' + (e.response?.data?.detail || e.message))
  }
}

async function fetchResult(id) {
  const { data } = await axios.get(`/api/runs/${id}`)
  shots.value = data.screenshots || []
}

onMounted(loadCases)
onBeforeUnmount(stopRenderLoop)  // 组件卸载时停 rAF 循环，防泄漏
</script>

<style scoped>
.bar { display: flex; justify-content: space-between; align-items: center; margin: 4px 0 8px; }
.sec-title { font-weight: 600; }
.step-line { font-size: 12px; color: #606266; line-height: 1.5; }
.parallel-line { font-size: 12px; color: #409eff; line-height: 1.5; padding-left: 14px; }
.step-block { margin-bottom: 8px; }
.step-edit { display: flex; gap: 10px; align-items: flex-start; }
.step-idx { flex: none; width: 18px; text-align: center; color: #909399; font-size: 12px; line-height: 24px; }
.step-input { flex: 1; min-width: 0; }
.step-actions { flex: none; display: flex; gap: 4px; align-items: center; padding-top: 1px; }
/* 清除 element-plus 相邻按钮默认 12px 外边距，改用容器 gap 紧凑排列 */
.step-actions .el-button + .el-button { margin-left: 0; }
.parallel-item { display: flex; align-items: center; gap: 6px; margin: 4px 0 0 24px; font-size: 12px; }
.parallel-tag { color: #409eff; font-weight: 600; white-space: nowrap; }
.parallel-name { color: #606266; }
.locator-panel { margin: 4px 0 0 24px; padding: 8px 10px; background: #fdf6ec; border: 1px solid #faecd8; border-radius: 4px; }
.locator-header { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.locator-title { font-size: 12px; font-weight: 600; color: #e6a23c; }
.locator-tip { font-size: 11px; color: #999; }
.locator-row { display: flex; gap: 6px; align-items: center; }
.locator-examples { margin-top: 5px; font-size: 11px; color: #999; line-height: 1.6; }
.locator-examples code { background: rgba(0,0,0,0.06); padding: 0 3px; border-radius: 2px; font-family: monospace; }
.step-hint { color: #909399; font-size: 12px; line-height: 1.6; margin-top: 8px; }
.step-hint code { background: rgba(0,0,0,0.06); padding: 0 3px; border-radius: 2px; }
.help-icon { color: #909399; cursor: help; margin-left: 2px; vertical-align: middle; }
.tip { color: #909399; font-size: 12px; line-height: 1.5; }
.heal-panel { margin-top: 8px; }
.heal-title { font-size: 12px; font-weight: 600; color: #606266; margin-bottom: 4px; }
.heal-card { display: flex; gap: 8px; align-items: flex-start; padding: 6px 8px; margin-bottom: 5px; border-radius: 4px; font-size: 12px; line-height: 1.5; border-left: 3px solid; }
.heal-card.vlm { background: #fdf6ec; border-color: #e6a23c; }
.heal-card.locate { background: #f0f9eb; border-color: #67c23a; }
.heal-card.ok { background: #f0f9eb; border-color: #67c23a; }
.heal-card.err { background: #fef0f0; border-color: #f56c6c; }
.heal-badge { flex: none; font-weight: 600; white-space: nowrap; }
.heal-body { color: #5c5c5c; word-break: break-all; }
.heal-body code { background: rgba(0,0,0,0.06); padding: 0 3px; border-radius: 2px; }
.screen-wrap { position: relative; background: #0d1117; border-radius: 4px; width: 100%; aspect-ratio: 16 / 9; overflow: hidden; }
.screen { width: 100%; height: 100%; object-fit: contain; display: block; }
.hint { position: absolute; top: 0; left: 0; right: 0; bottom: 0; display: flex; align-items: center; justify-content: center; text-align: center; padding: 16px; color: #8a919f; font-size: 13px; }
.shots { margin-top: 8px; }
.shots-title { font-size: 12px; color: #c0341d; font-weight: 600; margin-bottom: 4px; }
.shot { max-width: 100%; border: 1px solid #e4e7ed; margin-bottom: 8px; }
.logs { margin-top: 8px; background: #fff; border: 1px solid #e4e7ed; border-radius: 4px; padding: 8px; font-family: monospace; font-size: 12px; max-height: 26vh; overflow: auto; }
.log { line-height: 1.7; white-space: pre-wrap; }
.log.ok { color: #17803d; }
.log.err { color: #c0341d; }
.log.dim { color: #8a919f; }
.script-meta { font-size: 12px; color: #8a919f; margin-bottom: 10px; line-height: 1.5; }
.script-editor :deep(textarea) { font-family: 'JetBrains Mono', 'Cascadia Code', Consolas, 'Courier New', monospace; font-size: 12.5px; line-height: 1.6; tab-size: 4; }
.script-sel { font-family: monospace; font-size: 12px; color: #2d6cdf; word-break: break-all; }
.script-args { font-size: 12px; color: #606266; }
.script-dim { color: #a8abb2; font-size: 12px; }
</style>
