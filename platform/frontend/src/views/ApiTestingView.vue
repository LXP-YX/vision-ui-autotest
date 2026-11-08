<template>
  <div>
    <el-tabs v-model="activeTab">
      <!-- ============ 接口库 ============ -->
      <el-tab-pane label="接口库" name="defs">
        <div class="bar">
          <span class="sec-title">接口库（可复用接口）</span>
          <span>
            <el-input v-model="defQuery" size="small" clearable placeholder="按接口名 / 路径 / 模块查询"
                      style="width:240px;margin-right:8px" :prefix-icon="Search" />
            <el-button size="small" @click="openImport()">导入</el-button>
            <el-button type="primary" size="small" @click="openDef()">新建接口</el-button>
            <el-button type="danger" size="small" :disabled="!defSel.length"
                       @click="onBatchDeleteDef">删除选中{{ defSel.length ? `（${defSel.length}）` : '' }}</el-button>
          </span>
        </div>
        <el-table :data="defsFiltered" border size="small"
                  :default-sort="{ prop: 'id', order: 'ascending' }"
                  @sort-change="onDefSort"
                  @selection-change="onDefSelChange">
          <el-table-column type="selection" width="42" />
          <el-table-column label="" width="62" sortable="custom" prop="id" align="center" header-align="center">
            <template #default="{ row }">{{ defIndex(row) }}</template>
          </el-table-column>
          <el-table-column prop="name" label="接口名" min-width="140" />
          <el-table-column prop="method" label="方法" width="80" />
          <el-table-column prop="path" label="路径" min-width="220" show-overflow-tooltip />
          <el-table-column prop="module" label="模块" width="110" />
          <el-table-column label="操作" width="300">
            <template #default="{ row }">
              <el-button size="small" type="success" @click="testDef(row)">调试</el-button>
              <el-button size="small" @click="openDef(row)">编辑</el-button>
              <el-button size="small" @click="copyDef(row)">复制</el-button>
              <el-button size="small" type="danger" plain :icon="Delete" circle @click="removeDef(row)" />
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- ============ 全局变量 ============ -->
      <el-tab-pane label="全局变量" name="vars">
        <div class="bar">
          <span class="sec-title">全局变量模板（按测试环境）</span>
          <span>
            <el-input v-model="varQuery" size="small" clearable placeholder="按变量名 / 值 / 模块查询"
                      style="width:240px;margin-right:8px" :prefix-icon="Search" />
            <el-button type="primary" size="small" :disabled="!envId" @click="openVar()">新建变量</el-button>
            <el-button type="danger" size="small" :disabled="!varSel.length"
                       @click="onBatchDeleteVar">删除选中{{ varSel.length ? `（${varSel.length}）` : '' }}</el-button>
          </span>
        </div>
        <div class="picker mb">
          <span>测试环境：</span>
          <el-select v-model="envId" size="small" style="width:220px" @change="loadVars">
            <el-option v-for="e in envs" :key="e.id" :label="e.name + (e.is_default ? '（默认）' : '')" :value="e.id" />
          </el-select>
          <span class="step-hint">变量作为场景运行的初始值，可被接口 <code>${var}</code> 引用</span>
        </div>
        <el-table :data="varsFiltered" border size="small"
                  :default-sort="{ prop: 'id', order: 'ascending' }"
                  @sort-change="onVarSort"
                  @selection-change="onVarSelChange">
          <el-table-column type="selection" width="42" />
          <el-table-column label="" width="62" sortable="custom" prop="id" align="center" header-align="center">
            <template #default="{ row }">{{ varIndex(row) }}</template>
          </el-table-column>
          <el-table-column prop="name" label="变量名" min-width="140" />
          <el-table-column prop="value" label="值" min-width="180" show-overflow-tooltip />
          <el-table-column prop="module" label="模块" width="110" />
          <el-table-column prop="description" label="说明" min-width="150" show-overflow-tooltip />
          <el-table-column label="操作" width="200">
            <template #default="{ row }">
              <el-button size="small" @click="openVar(row)">编辑</el-button>
              <el-button size="small" @click="copyVar(row)">复制</el-button>
              <el-button size="small" type="danger" plain :icon="Delete" circle @click="removeVar(row)" />
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- ============ 接口场景 ============ -->
      <el-tab-pane label="接口场景" name="scenarios">
        <el-row :gutter="12">
          <el-col :span="13">
            <div class="bar">
              <span class="sec-title">接口场景（多接口编排）</span>
              <span>
                <el-input v-model="scQuery" size="small" clearable placeholder="按场景名 / 模块查询"
                          style="width:180px;margin-right:8px" :prefix-icon="Search" />
                <el-button type="primary" size="small" @click="openScenario()">新建场景</el-button>
                <el-button type="danger" size="small" :disabled="!scSel.length"
                           @click="onBatchDeleteSc">删除选中{{ scSel.length ? `（${scSel.length}）` : '' }}</el-button>
              </span>
            </div>
            <el-table :data="scenariosFiltered" border size="small"
                      :default-sort="{ prop: 'id', order: 'ascending' }"
                      @sort-change="onScSort"
                      @selection-change="onScSelChange">
              <el-table-column type="selection" width="42" />
              <el-table-column label="" width="62" sortable="custom" prop="id" align="center" header-align="center">
                <template #default="{ row }">{{ scIndex(row) }}</template>
              </el-table-column>
              <el-table-column prop="name" label="场景名" min-width="140" />
              <el-table-column prop="module" label="模块" width="100" />
              <el-table-column label="步骤" width="90">
                <template #default="{ row }">{{ (row.setup_steps.length) }}+{{ row.steps.length }}</template>
              </el-table-column>
              <el-table-column label="操作" width="320">
                <template #default="{ row }">
                  <el-button size="small" type="primary" :disabled="running" @click="runScenario(row)">执行</el-button>
                  <el-button size="small" type="success" :disabled="debugging" @click="debugScenario(row)">调试</el-button>
                  <el-button size="small" @click="openScenario(row)">编辑</el-button>
                  <el-button size="small" @click="copyScenario(row)">复制</el-button>
                  <el-button size="small" type="danger" plain :icon="Delete" circle @click="removeScenario(row)" />
                </template>
              </el-table-column>
            </el-table>
          </el-col>

          <!-- 实时执行视图 -->
          <el-col :span="11">
            <div class="bar">
              <span class="sec-title">实时执行 {{ runId ? `（run #${runId}）` : '' }}</span>
              <el-tag v-if="status" :type="statusType" size="small">{{ statusText }}</el-tag>
            </div>
            <div class="log-box">
              <div v-if="!logs.length" class="empty">点击某个场景的「执行」，这里实时显示请求与断言过程</div>
              <div v-for="(l, i) in logs" :key="i" class="log-line" :class="l.lvl">{{ l.text }}</div>
            </div>
          </el-col>
        </el-row>
      </el-tab-pane>
    </el-tabs>

    <!-- ===== 接口编辑对话框 ===== -->
    <el-dialog v-model="defVisible" :title="defForm.id ? `编辑接口 #${defForm.id}` : '新建接口'" width="720px" top="5vh">
      <el-form :model="defForm" label-width="80px">
        <el-row :gutter="10">
          <el-col :span="12"><el-form-item label="接口名"><el-input v-model="defForm.name" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="模块"><el-input v-model="defForm.module" /></el-form-item></el-col>
        </el-row>
        <el-form-item label="请求">
          <el-select v-model="defForm.method" style="width:110px">
            <el-option v-for="m in methods" :key="m" :label="m" :value="m" />
          </el-select>
          <el-input v-model="defForm.path" placeholder="/api/xxx（相对 base_url）或完整 URL" style="flex:1;margin-left:8px" />
        </el-form-item>
        <el-form-item label="请求头">
          <div style="width:100%">
            <div v-for="(row, i) in defForm.headerRows" :key="'hr'+i" class="kv-row">
              <el-input v-model="row.k" placeholder="Key，如 Authorization" class="kv-key" />
              <el-input v-model="row.v" placeholder="Value，如 Bearer ${token}" class="kv-val" />
              <el-button type="danger" plain :icon="Delete" circle @click="defForm.headerRows.splice(i,1)" />
            </div>
            <el-button size="small" @click="defForm.headerRows.push({k:'',v:''})">+ 请求头</el-button>
          </div>
        </el-form-item>
        <el-form-item label="Query">
          <div style="width:100%">
            <div v-for="(row, i) in defForm.paramRows" :key="'pr'+i" class="kv-row">
              <el-input v-model="row.k" placeholder="Key，如 pageNo" class="kv-key" />
              <el-input v-model="row.v" placeholder="Value，如 1 或 ${TODAY_START}" class="kv-val" />
              <el-button type="danger" plain :icon="Delete" circle @click="defForm.paramRows.splice(i,1)" />
            </div>
            <el-button size="small" @click="defForm.paramRows.push({k:'',v:''})">+ Query 参数</el-button>
          </div>
        </el-form-item>
        <el-form-item label="请求体">
          <el-select v-model="defForm.body_type" style="width:110px;margin-bottom:6px">
            <el-option label="json" value="json" />
            <el-option label="form" value="form" />
            <el-option label="raw" value="raw" />
          </el-select>
          <el-input v-model="defForm.body" type="textarea" :rows="3" placeholder='支持 ${var}，json/form 填 JSON 对象' />
        </el-form-item>

        <el-divider content-position="left">变量提取器（从响应 JSON 提取）</el-divider>
        <div v-for="(ex, i) in defForm.extractors" :key="'ex'+i" class="row-line">
          <el-input v-model="ex.name" placeholder="变量名 如 token" style="width:180px" />
          <el-input v-model="ex.jsonpath" placeholder="JSONPath 如 $.data.token" style="flex:1" />
          <el-button type="danger" plain :icon="Delete" circle @click="removeExtractor(i)" />
        </div>
        <el-button size="small" @click="defForm.extractors.push({name:'',jsonpath:''})">+ 提取器</el-button>

        <el-divider content-position="left">断言</el-divider>
        <div v-for="(a, i) in defForm.assertions" :key="'as'+i" class="row-line">
          <el-select v-model="a.type" style="width:140px" @change="onAssertType(a)">
            <el-option label="状态码" value="status_code" />
            <el-option label="JSONPath 值" value="jsonpath" />
            <el-option label="响应包含文本" value="contains" />
            <el-option label="响应耗时(ms)" value="duration" />
          </el-select>
          <el-input v-if="a.type==='jsonpath'" v-model="a.jsonpath" placeholder="$.code" style="width:150px" />
          <el-select v-if="a.type==='jsonpath'" v-model="a.op" style="width:110px">
            <el-option label="等于" value="eq" /><el-option label="不等于" value="ne" />
            <el-option label="包含" value="contains" /><el-option label="非空" value="not_empty" />
            <el-option label="大于" value="gt" /><el-option label="小于" value="lt" />
          </el-select>
          <el-select v-if="a.type==='duration'" v-model="a.op" style="width:90px">
            <el-option label="<" value="<" /><el-option label="<=" value="<=" />
          </el-select>
          <el-input v-if="a.type!=='jsonpath' || a.op!=='not_empty'" v-model="a.expect" placeholder="期望值" style="flex:1" />
          <el-button type="danger" plain :icon="Delete" circle @click="removeAssertion(i)" />
        </div>
        <el-button size="small" @click="defForm.assertions.push({type:'status_code',op:'==',expect:'200'})">+ 断言</el-button>

        <el-divider content-position="left">断言失败重试</el-divider>
        <el-form-item label="重试">
          <el-switch v-model="defForm.retry_enabled" />
          <span class="step-hint" style="margin-left:8px">开启后，断言失败将按下方配置持续重试</span>
        </el-form-item>
        <el-form-item v-if="defForm.retry_enabled" label=" ">
          <span class="step-hint" style="margin-right:8px">断言失败后每隔</span>
          <el-input-number v-model="defForm.retry_interval" :min="0" :step="0.5" :precision="1"
                           size="small" style="width:120px" controls-position="right" />
          <span class="step-hint" style="margin:0 8px">秒重试，最多请求</span>
          <el-input-number v-model="defForm.retry_max" :min="1" :step="1" :precision="0"
                           size="small" style="width:110px" controls-position="right" />
          <span class="step-hint" style="margin-left:8px">次（上限前断言通过即判通过）</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="defVisible=false">取消</el-button>
        <el-button type="primary" @click="saveDef">保存</el-button>
      </template>
    </el-dialog>

    <!-- ===== 单接口调试结果对话框 ===== -->
    <el-dialog v-model="testVisible" title="接口调试" width="820px" top="5vh">
      <template v-if="testResult">
        <ApiResultDetail :res="testResult" />
        <el-divider content-position="left">变量快照（运行后）</el-divider>
        <pre class="code-box">{{ prettyJson(testVars) }}</pre>
      </template>
    </el-dialog>

    <!-- ===== 场景调试结果对话框 ===== -->
    <el-dialog v-model="debugVisible" title="场景调试" width="900px" top="4vh">
      <template v-if="debugResult">
        <el-alert
          :title="`场景结果：${debugResult.status === 'passed' ? '通过' : '未通过'}` + (debugResult.error ? `（${debugResult.error}）` : '')"
          :type="debugResult.status === 'passed' ? 'success' : 'error'"
          :closable="false" class="mb" />
        <el-collapse v-model="debugActive">
          <el-collapse-item v-for="(st, i) in debugResult.step_results" :key="i" :name="i">
            <template #title>
              <el-tag :type="st.ok ? 'success' : 'danger'" size="small" style="margin-right:8px">{{ st.ok ? '✓' : '✗' }}</el-tag>
              <span>{{ (st.phase || '步骤') }} #{{ (st.index ?? i) + 1 }}　{{ st.name }}　</span>
              <span class="dim2">{{ st.method }} → {{ st.status_code ?? '—' }}（{{ st.duration_ms ?? '—' }}ms）</span>
            </template>
            <ApiResultDetail :res="st" />
          </el-collapse-item>
        </el-collapse>
        <el-divider content-position="left">变量快照（运行后）</el-divider>
        <pre class="code-box">{{ prettyJson(debugResult.variables) }}</pre>
      </template>
    </el-dialog>

    <!-- ===== 导入对话框 ===== -->
    <el-dialog v-model="importVisible" title="导入接口（Postman / curl）" width="860px" top="4vh">
      <el-form label-width="80px">
        <el-form-item label="来源">
          <el-radio-group v-model="importKind">
            <el-radio-button label="auto">自动识别</el-radio-button>
            <el-radio-button label="postman">Postman JSON</el-radio-button>
            <el-radio-button label="curl">curl 命令</el-radio-button>
          </el-radio-group>
          <el-upload v-if="importKind !== 'curl'" :auto-upload="false" :show-file-list="false"
            accept=".json" :on-change="onPostmanFile" style="margin-left:10px;display:inline-block">
            <el-button size="small">选择 Postman 文件</el-button>
          </el-upload>
        </el-form-item>
        <el-form-item label="内容">
          <el-input v-model="importText" type="textarea" :rows="8"
            placeholder="粘贴 Postman Collection 的 JSON，或粘贴一条 curl 命令（支持 {{变量}} → ${变量}）" />
        </el-form-item>
      </el-form>
      <div class="bar">
        <el-button type="primary" size="small" :loading="importParsing" @click="doPreview">解析预览</el-button>
        <span v-if="importItems.length" class="step-hint">共解析 {{ importItems.length }} 个接口，勾选后入库</span>
      </div>
      <el-table v-if="importItems.length" ref="importTableRef" :data="importItems" border size="small"
        max-height="360" @selection-change="onImportSel">
        <el-table-column type="selection" width="42" />
        <el-table-column prop="name" label="接口名" min-width="130">
          <template #default="{ row }"><el-input v-model="row.name" size="small" /></template>
        </el-table-column>
        <el-table-column prop="module" label="模块" width="120">
          <template #default="{ row }"><el-input v-model="row.module" size="small" /></template>
        </el-table-column>
        <el-table-column prop="method" label="方法" width="72" />
        <el-table-column prop="path" label="路径" min-width="200" show-overflow-tooltip />
      </el-table>
      <template #footer>
        <el-button @click="importVisible=false">取消</el-button>
        <el-button type="primary" :disabled="!importSel.length" @click="doCommit">
          入库选中（{{ importSel.length }}）
        </el-button>
      </template>
    </el-dialog>

    <!-- ===== 全局变量编辑对话框 ===== -->
    <el-dialog v-model="varVisible" :title="varForm.id ? `编辑变量 #${varForm.id}` : '新建变量'" width="480px">
      <el-form :model="varForm" label-width="70px">
        <el-form-item label="变量名"><el-input v-model="varForm.name" /></el-form-item>
        <el-form-item label="值"><el-input v-model="varForm.value" /></el-form-item>
        <el-form-item label="模块"><el-input v-model="varForm.module" /></el-form-item>
        <el-form-item label="说明"><el-input v-model="varForm.description" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="varVisible=false">取消</el-button>
        <el-button type="primary" @click="saveVar">保存</el-button>
      </template>
    </el-dialog>

    <!-- ===== 场景编辑对话框 ===== -->
    <el-dialog v-model="scVisible" :title="scForm.id ? `编辑场景 #${scForm.id}` : '新建场景'" width="680px" top="6vh">
      <el-form :model="scForm" label-width="80px">
        <el-row :gutter="10">
          <el-col :span="12"><el-form-item label="场景名"><el-input v-model="scForm.name" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="模块"><el-input v-model="scForm.module" /></el-form-item></el-col>
        </el-row>
        <el-form-item label="描述"><el-input v-model="scForm.description" /></el-form-item>

        <el-divider content-position="left">前置步骤（先执行，如登录取 token）</el-divider>
        <div v-for="(st, i) in scForm.setup_steps" :key="'su'+i" class="row-line">
          <span class="idx">{{ i+1 }}</span>
          <el-select v-model="st.api_id" filterable placeholder="选择接口" style="flex:1" @change="st.name = defName(st.api_id)">
            <el-option v-for="d in defs" :key="d.id" :label="`${d.method} ${d.name}`" :value="d.id" />
          </el-select>
          <el-button type="danger" plain :icon="Delete" circle @click="removeSetupStep(i)" />
        </div>
        <el-button size="small" @click="scForm.setup_steps.push({api_id:null,name:'',group:0})">+ 前置步骤</el-button>

        <el-divider content-position="left">主步骤</el-divider>
        <div v-for="(st, i) in scForm.steps" :key="'sp'+i" class="row-line">
          <span class="idx">{{ i+1 }}</span>
          <el-select v-model="st.api_id" filterable placeholder="选择接口" style="flex:1" @change="st.name = defName(st.api_id)">
            <el-option v-for="d in defs" :key="d.id" :label="`${d.method} ${d.name}`" :value="d.id" />
          </el-select>
          <el-button type="danger" plain :icon="Delete" circle @click="removeMainStep(i)" />
        </div>
        <el-button size="small" @click="scForm.steps.push({api_id:null,name:'',group:0})">+ 主步骤</el-button>
      </el-form>
      <template #footer>
        <el-button @click="scVisible=false">取消</el-button>
        <el-button type="primary" @click="saveScenario">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import axios from 'axios'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, Search } from '@element-plus/icons-vue'
import ApiResultDetail from '../components/ApiResultDetail.vue'
import { useIdSort } from '../composables/useIdSort'
import { useMultiSelect } from '../composables/useMultiSelect'

const activeTab = ref('defs')
const methods = ['GET', 'POST', 'PUT', 'DELETE', 'PATCH']

// ---- 接口库 ----
const { items: defs, setItems: setDefs, onSortChange: onDefSort, indexOf: defIndex } = useIdSort()
const { selection: defSel, onSelectionChange: onDefSelChange, batchDelete: batchDeleteDef, confirmRemove: confirmDefRemove } = useMultiSelect()
const defQuery = ref('')
const defsFiltered = computed(() => {
  const q = defQuery.value.trim().toLowerCase()
  if (!q) return defs.value
  return defs.value.filter(d =>
    (d.name || '').toLowerCase().includes(q) ||
    (d.path || '').toLowerCase().includes(q) ||
    (d.module || '').toLowerCase().includes(q))
})
const defVisible = ref(false)
const defForm = reactive({ id: null, name: '', module: '', method: 'GET', path: '',
  headerRows: [{k:'',v:''}], paramRows: [{k:'',v:''}],
  body: '', body_type: 'json', extractors: [], assertions: [],
  retry_enabled: false, retry_interval: 1, retry_max: 1 })

async function loadDefs() {
  const { data } = await axios.get('/api/api-defs')
  setDefs(data)
}
function defName(id) { const d = defs.value.find(x => x.id === id); return d ? d.name : '' }

function openDef(row) {
  if (row) {
    Object.assign(defForm, {
      id: row.id, name: row.name, module: row.module, method: row.method, path: row.path,
      headerRows: objToRows(row.headers),
      paramRows: objToRows(row.params),
      body: row.body, body_type: row.body_type,
      extractors: JSON.parse(JSON.stringify(row.extractors)),
      assertions: JSON.parse(JSON.stringify(row.assertions)),
      retry_enabled: (row.retry_max || 1) > 1,
      retry_interval: row.retry_interval != null ? row.retry_interval : 1,
      retry_max: row.retry_max != null ? row.retry_max : 1,
    })
  } else {
    Object.assign(defForm, { id: null, name: '', module: '', method: 'GET', path: '',
      headerRows: [{k:'',v:''}], paramRows: [{k:'',v:''}],
      body: '', body_type: 'json', extractors: [], assertions: [],
      retry_enabled: false, retry_interval: 1, retry_max: 1 })
  }
  defVisible.value = true
}

function objToRows(obj) {
  const keys = Object.keys(obj || {})
  if (!keys.length) return [{ k: '', v: '' }]
  return keys.map(k => ({ k, v: String(obj[k]) }))
}
function rowsToObj(rows) {
  const obj = {}
  for (const { k, v } of rows) {
    if (k.trim()) obj[k.trim()] = v
  }
  return obj
}

function parseJsonField(text, label) {
  if (!text || !text.trim()) return {}
  try { return JSON.parse(text) } catch { throw new Error(`${label} 不是合法 JSON`) }
}

function coerceExpect(a) {
  // status_code / duration 期望值应传数字；jsonpath gt/lt 也需要数字
  // 如果用户输入的是纯数字字符串，转成 Number 保存
  if (a.type === 'status_code' || a.type === 'duration' || a.op === 'gt' || a.op === 'lt') {
    const n = Number(a.expect)
    return { ...a, expect: isNaN(n) ? a.expect : n }
  }
  return { ...a }
}

async function saveDef() {
  if (!defForm.name) { ElMessage.warning('请填写接口名'); return }
  const headers = rowsToObj(defForm.headerRows)
  const params = rowsToObj(defForm.paramRows)
  const payload = { name: defForm.name, module: defForm.module, method: defForm.method, path: defForm.path,
    headers, params, body: defForm.body, body_type: defForm.body_type,
    extractors: defForm.extractors.filter(x => x.name),
    assertions: defForm.assertions.map(coerceExpect),
    retry_interval: Number(defForm.retry_interval) || 0,
    retry_max: defForm.retry_enabled ? (Number(defForm.retry_max) || 1) : 1 }
  if (defForm.id) await axios.put(`/api/api-defs/${defForm.id}`, payload)
  else await axios.post('/api/api-defs', payload)
  defVisible.value = false; ElMessage.success('已保存'); loadDefs()
}

async function removeDef(row) {
  if (!(await confirmDefRemove({ label: '接口', name: row.name }))) return
  await axios.delete(`/api/api-defs/${row.id}`); ElMessage.success('已删除'); loadDefs()
}

function copyDef(row) {
  openDef(row)
  defForm.id = null
  defForm.name = `${row.name} 副本`
}

function onBatchDeleteDef() {
  batchDeleteDef({ label: '接口', deleteOne: r => axios.delete(`/api/api-defs/${r.id}`), onDone: loadDefs })
}

const JSONPATH_OPS = new Set(['eq', 'ne', 'contains', 'not_empty', 'gt', 'lt'])
function onAssertType(a) {
  if (a.type === 'status_code') { a.op = '=='; a.expect = a.expect || '200' }
  else if (a.type === 'jsonpath') { if (!JSONPATH_OPS.has(a.op)) a.op = 'eq'; a.jsonpath = a.jsonpath || '' }
  else if (a.type === 'duration') { a.op = '<'; a.expect = a.expect || '1000' }
}

// ---- 单接口调试 ----
const testVisible = ref(false)
const testResult = ref(null)
const testVars = ref({})
async function testDef(row) {
  const { data } = await axios.post(`/api/api-defs/${row.id}/test`)
  testResult.value = data.result; testVars.value = data.variables || {}; testVisible.value = true
}

function prettyJson(v) {
  try { return JSON.stringify(v || {}, null, 2) } catch { return String(v) }
}

// ---- 全局变量 ----
const envs = ref([])
const envId = ref(null)
const { items: vars, setItems: setVars, onSortChange: onVarSort, indexOf: varIndex } = useIdSort()
const { selection: varSel, onSelectionChange: onVarSelChange, batchDelete: batchDeleteVar, confirmRemove: confirmVarRemove } = useMultiSelect()
const varQuery = ref('')
const varsFiltered = computed(() => {
  const q = varQuery.value.trim().toLowerCase()
  if (!q) return vars.value
  return vars.value.filter(v =>
    (v.name || '').toLowerCase().includes(q) ||
    (v.value || '').toLowerCase().includes(q) ||
    (v.module || '').toLowerCase().includes(q))
})
const varVisible = ref(false)
const varForm = reactive({ id: null, name: '', value: '', module: '', description: '' })

async function loadEnvs() {
  envs.value = (await axios.get('/api/test-envs')).data
  const def = envs.value.find(e => e.is_default) || envs.value[0]
  if (def && !envId.value) { envId.value = def.id }
}
async function loadVars() {
  if (!envId.value) { setVars([]); return }
  const { data } = await axios.get('/api/api-vars', { params: { env_id: envId.value } })
  setVars(data)
}
function openVar(row) {
  if (row) Object.assign(varForm, { id: row.id, name: row.name, value: row.value, module: row.module, description: row.description })
  else Object.assign(varForm, { id: null, name: '', value: '', module: '', description: '' })
  varVisible.value = true
}
async function saveVar() {
  if (!varForm.name) { ElMessage.warning('请填写变量名'); return }
  const payload = { env_id: envId.value, name: varForm.name, value: varForm.value, module: varForm.module, description: varForm.description }
  try {
    if (varForm.id) await axios.put(`/api/api-vars/${varForm.id}`, payload)
    else await axios.post('/api/api-vars', payload)
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '保存失败')
    return
  }
  varVisible.value = false; ElMessage.success('已保存'); loadVars()
}
async function removeVar(row) {
  if (!(await confirmVarRemove({ label: '变量', name: row.name }))) return
  await axios.delete(`/api/api-vars/${row.id}`); ElMessage.success('已删除'); loadVars()
}

function copyVar(row) {
  openVar(row)
  varForm.id = null
  varForm.name = `${row.name} 副本`
}
function onBatchDeleteVar() {
  batchDeleteVar({ label: '变量', deleteOne: r => axios.delete(`/api/api-vars/${r.id}`), onDone: loadVars })
}

// ---- 场景 ----
const { items: scenarios, setItems: setScenarios, onSortChange: onScSort, indexOf: scIndex } = useIdSort()
const { selection: scSel, onSelectionChange: onScSelChange, batchDelete: batchDeleteSc, confirmRemove: confirmScRemove } = useMultiSelect()
const scQuery = ref('')
const scenariosFiltered = computed(() => {
  const q = scQuery.value.trim().toLowerCase()
  if (!q) return scenarios.value
  return scenarios.value.filter(x =>
    (x.name || '').toLowerCase().includes(q) ||
    (x.module || '').toLowerCase().includes(q))
})
const scVisible = ref(false)
const scForm = reactive({ id: null, name: '', module: '', description: '', setup_steps: [], steps: [] })

async function loadScenarios() {
  const { data } = await axios.get('/api/api-scenarios')
  setScenarios(data)
}
function openScenario(row) {
  if (row) Object.assign(scForm, { id: row.id, name: row.name, module: row.module, description: row.description,
    setup_steps: JSON.parse(JSON.stringify(row.setup_steps)), steps: JSON.parse(JSON.stringify(row.steps)) })
  else Object.assign(scForm, { id: null, name: '', module: '', description: '', setup_steps: [], steps: [] })
  scVisible.value = true
}
function copyScenario(row) {
  openScenario(row)
  scForm.id = null
  scForm.name = `${row.name} 副本`
}
async function saveScenario() {
  if (!scForm.name) { ElMessage.warning('请填写场景名'); return }
  const payload = { name: scForm.name, module: scForm.module, description: scForm.description,
    setup_steps: scForm.setup_steps.filter(s => s.api_id), steps: scForm.steps.filter(s => s.api_id), status: 'active' }
  if (scForm.id) await axios.put(`/api/api-scenarios/${scForm.id}`, payload)
  else await axios.post('/api/api-scenarios', payload)
  scVisible.value = false; ElMessage.success('已保存'); loadScenarios()
}
async function removeScenario(row) {
  if (!(await confirmScRemove({ label: '场景', name: row.name }))) return
  await axios.delete(`/api/api-scenarios/${row.id}`); ElMessage.success('已删除'); loadScenarios()
}
function onBatchDeleteSc() {
  batchDeleteSc({ label: '场景', deleteOne: r => axios.delete(`/api/api-scenarios/${r.id}`), onDone: loadScenarios })
}

// ---- 执行 + WebSocket 实时视图 ----
const running = ref(false)
const runId = ref(null)
const status = ref('')
const logs = ref([])
const statusText = computed(() => ({ running: '执行中', passed: '通过', failed: '失败', error: '错误', queued: '排队中' }[status.value] || status.value))
const statusType = computed(() => ({ passed: 'success', failed: 'danger', error: 'danger', running: 'warning' }[status.value] || 'info'))
function addLog(text, lvl = 'info') { logs.value.push({ text, lvl }) }

async function runScenario(row) {
  running.value = true; status.value = 'queued'; logs.value = []
  addLog(`▶ 开始执行场景「${row.name}」`)
  const { data } = await axios.post(`/api/api-scenarios/${row.id}/run`)
  runId.value = data.run_id
  openWs(data.run_id)
}

function openWs(id) {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const ws = new WebSocket(`${proto}://${location.host}/ws/api-runs/${id}`)
  ws.onmessage = (ev) => {
    const { kind, payload } = JSON.parse(ev.data)
    if (kind === 'step') {
      status.value = 'running'
      if (payload.state === 'running') addLog(`… [${payload.phase}] ${payload.name}`, 'info')
      else {
        const icon = payload.state === 'passed' ? '✓' : '✗'
        addLog(`${icon} ${payload.name}${payload.detail ? ' — ' + payload.detail : ''}`, payload.state === 'passed' ? 'ok' : 'err')
      }
    } else if (kind === 'api') {
      if (payload.phase === 'response') addLog(`  ← ${payload.status}（${payload.duration_ms}ms）`, 'dim')
    } else if (kind === 'assert') {
      addLog(`  ${payload.passed ? '✓' : '✗'} 断言：${payload.detail}`, payload.passed ? 'ok' : 'err')
    } else if (kind === 'log') addLog(`  ${payload.msg}`, 'dim')
    else if (kind === 'done') {
      status.value = payload.status
      addLog(`■ 执行结束：${statusText.value}`, payload.status === 'passed' ? 'ok' : 'err')
      running.value = false; ws.close()
    }
  }
  ws.onerror = () => { addLog('WebSocket 连接错误', 'err'); running.value = false }
}

// ---- 场景调试（同步，逐步完整信息）----
const debugging = ref(false)
const debugVisible = ref(false)
const debugResult = ref(null)
const debugActive = ref([])
async function debugScenario(row) {
  debugging.value = true
  try {
    const { data } = await axios.post(`/api/api-scenarios/${row.id}/debug`)
    debugResult.value = data
    // 默认展开未通过的步骤，全通过则展开第一步
    const bad = (data.step_results || []).map((s, i) => s.ok ? -1 : i).filter(i => i >= 0)
    debugActive.value = bad.length ? bad : (data.step_results?.length ? [0] : [])
    debugVisible.value = true
  } catch (e) {
    ElMessage.error('场景调试失败：' + (e.response?.data?.detail || e.message))
  } finally {
    debugging.value = false
  }
}

// ---- 导入（Postman / curl）----
const importVisible = ref(false)
const importKind = ref('auto')
const importText = ref('')
const importItems = ref([])
const importSel = ref([])
const importParsing = ref(false)
const importTableRef = ref(null)
function openImport() {
  importText.value = ''; importItems.value = []; importSel.value = []
  importKind.value = 'auto'; importVisible.value = true
}
function onPostmanFile(file) {
  const reader = new FileReader()
  reader.onload = (e) => { importText.value = e.target.result; importKind.value = 'postman' }
  reader.readAsText(file.raw)
}
async function doPreview() {
  if (!importText.value.trim()) { ElMessage.warning('请粘贴内容或选择文件'); return }
  importParsing.value = true
  try {
    const { data } = await axios.post('/api/api-defs/import/preview', { text: importText.value, kind: importKind.value })
    importItems.value = data.items
    if (!data.items.length) ElMessage.warning('未解析到接口')
  } catch (e) {
    ElMessage.error('解析失败：' + (e.response?.data?.detail || e.message))
  } finally {
    importParsing.value = false
  }
}
function onImportSel(rows) { importSel.value = rows }
async function doCommit() {
  if (!importSel.value.length) return
  const { data } = await axios.post('/api/api-defs/import/commit', { items: importSel.value })
  ElMessage.success(`成功入库 ${data.count} 个接口`)
  importVisible.value = false
  loadDefs()
}

// ---- 弹窗内子行删除（提取器 / 断言 / 场景步骤） ----
async function removeExtractor(i) {
  const ex = defForm.extractors[i]
  if (!(await confirmDefRemove({ label: '提取器', name: ex && ex.name }))) return
  defForm.extractors.splice(i, 1)
}
async function removeAssertion(i) {
  if (!(await confirmDefRemove({ label: '断言' }))) return
  defForm.assertions.splice(i, 1)
}
async function removeSetupStep(i) {
  const st = scForm.setup_steps[i]
  if (!(await confirmScRemove({ label: '前置步骤', name: st && st.name }))) return
  scForm.setup_steps.splice(i, 1)
}
async function removeMainStep(i) {
  const st = scForm.steps[i]
  if (!(await confirmScRemove({ label: '主步骤', name: st && st.name }))) return
  scForm.steps.splice(i, 1)
}

onMounted(() => { loadDefs(); loadEnvs().then(loadVars); loadScenarios() })
</script>

<style scoped>
.kv-row { display: flex; gap: 6px; align-items: center; margin-bottom: 6px; }
.kv-key { width: 200px; flex-shrink: 0; }
.kv-val { flex: 1; }
.bar { display: flex; justify-content: space-between; align-items: center; margin: 4px 0 8px; }
.sec-title { font-weight: 600; }
.mb { margin-bottom: 12px; }
.mt { margin-top: 8px; }
.picker { display: flex; align-items: center; gap: 8px; }
.row-line { display: flex; gap: 6px; align-items: center; margin-bottom: 6px; }
.row-line .idx { width: 18px; color: #909399; font-size: 12px; }
.step-hint { color: #909399; font-size: 12px; }
.step-hint code { background: rgba(0,0,0,0.06); padding: 0 3px; border-radius: 2px; }
.log-box { height: 460px; overflow: auto; background: #1e1e1e; border-radius: 4px; padding: 8px; font-family: monospace; font-size: 12px; }
.log-box .empty { color: #888; }
.log-line { line-height: 1.6; white-space: pre-wrap; word-break: break-all; }
.log-line.info { color: #d4d4d4; }
.log-line.ok { color: #6bd968; }
.log-line.err { color: #f56c6c; }
.log-line.dim { color: #909399; }
.err { color: #f56c6c; }
.ok { color: #529b2e; }
.dim2 { color: #909399; font-size: 12px; }
.code-box {
  background: #1e1e1e; color: #d4d4d4; border-radius: 4px; padding: 8px;
  font-family: Consolas, Menlo, monospace; font-size: 12px; line-height: 1.5;
  white-space: pre-wrap; word-break: break-all; max-height: 240px; overflow: auto; margin: 0;
}
</style>
