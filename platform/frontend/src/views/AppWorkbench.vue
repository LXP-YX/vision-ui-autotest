<template>
  <div class="wb">
    <!-- 顶栏：设备选择 + 状态 + 刷新 + 启动 App -->
    <div class="bar">
      <span class="sec-title">设备</span>
      <el-select v-model="serial" placeholder="选择设备" size="small" style="width: 220px"
                 @change="onDeviceChange">
        <el-option v-for="d in devices" :key="d.serial" :label="`${d.serial}（${d.state}）`" :value="d.serial" />
      </el-select>
      <el-tag :type="online ? 'success' : 'danger'" size="small" effect="dark">
        {{ online ? '在线' : (serial ? '离线' : '未选设备') }}
      </el-tag>
      <span v-if="devSize" class="dim">{{ devSize.w }}×{{ devSize.h }}</span>
      <el-button size="small" plain :icon="Refresh" @click="loadDevices">刷新设备</el-button>
      <el-button size="small" type="primary" plain :icon="VideoPlay" :disabled="!online" @click="launchDlg = true">
        启动 App
      </el-button>
    </div>

    <el-row :gutter="12" class="body">
      <!-- 左：App 用例列表 -->
      <el-col :span="12">
        <el-card shadow="never" class="card">
          <template #header>
            <span class="sec-title">App 用例</span>
            <el-button size="small" type="primary" plain style="float:right" :icon="Plus" @click="openEditor()">新建</el-button>
          </template>
          <el-table :data="appCases" size="small" max-height="560" empty-text="暂无 App 用例（driver_type=app）">
            <el-table-column prop="name" label="名称" min-width="110" show-overflow-tooltip />
            <el-table-column prop="app_package" label="包名" min-width="120" show-overflow-tooltip />
            <el-table-column label="操作" width="140" align="center">
              <template #default="{ row }">
                <el-button size="small" type="success" link :loading="running && runId && runCaseId === row.id"
                           @click="runCase(row)">运行</el-button>
                <el-button size="small" link @click="openEditor(row)">编辑</el-button>
                <el-button size="small" type="danger" link @click="delCase(row)">删</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>

      <!-- 右：App 管理 + 投屏/日志并列 -->
      <el-col :span="12">
        <!-- App 管理 -->
        <el-card shadow="never" class="card">
          <template #header><span class="sec-title">App 管理</span></template>
          <div class="app-row">
            <el-upload :show-file-list="false" accept=".apk" :http-request="uploadApk" :disabled="!online">
              <el-button size="small" type="success" plain :icon="Upload" :loading="uploading" :disabled="!online">
                上传 APK 安装
              </el-button>
            </el-upload>
            <el-button size="small" plain :icon="Refresh" @click="loadPackages" :disabled="!online">刷新列表</el-button>
            <span class="dim">已装 {{ packages.length }} 个第三方 App</span>
          </div>
          <el-table :data="packages" size="small" max-height="160" empty-text="暂无第三方 App">
            <el-table-column prop="label" label="名称" min-width="100" show-overflow-tooltip />
            <el-table-column prop="package" label="包名" min-width="180" show-overflow-tooltip />
            <el-table-column prop="version" label="版本" width="70" show-overflow-tooltip />
            <el-table-column label="操作" width="60" align="center">
              <template #default="{ row }">
                <el-button size="small" type="danger" link @click="uninstall(row.package)">卸载</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <!-- 模拟器实时画面 与 执行日志 并列 -->
        <el-row :gutter="12">
          <el-col :span="13">
            <el-card shadow="never" class="card">
              <template #header>
                <span class="sec-title">手机投屏</span>
              </template>
              <div class="screen-wrap" :class="{ offline: !online }">
                <canvas ref="canvas" class="screen"
                        @mousedown="onMouseDown" @mouseup="onMouseUp" @click="focusKbd"
                        @mousemove="onMouseMove" @mouseleave="onMouseUp" />
                <!-- 隐藏 input：捕获键盘输入（可打印字符走 adb input text，特殊键走 keyevent） -->
                <input ref="kbd" class="kbd-capture" type="text" autocomplete="off"
                       @keydown="onKeydown" @input="onKbdInput"
                       @focus="kbdFocus = true" @blur="kbdFocus = false" />
                <div v-if="!online" class="screen-mask">设备不在线</div>
              </div>
              <!-- 按键栏：BACK / HOME / 最近任务 / 回车 / 截图，纯图标 -->
              <div class="keys">
                <el-button size="small" :icon="Back" @click="key(4)" :disabled="!online" title="返回" />
                <el-button size="small" :icon="HomeFilled" @click="key(3)" :disabled="!online" title="主屏" />
                <el-button size="small" :icon="Menu" @click="key(187)" :disabled="!online" title="最近任务" />
                <el-button size="small" :icon="Position" @click="key(66)" :disabled="!online" title="回车" />
                <el-button size="small" :icon="Camera" @click="snapshot" :disabled="!online" title="截图" />
              </div>
            </el-card>
          </el-col>

          <el-col :span="11">
            <el-card shadow="never" class="card log-card">
              <template #header>
                <span class="sec-title">执行日志</span>
                <span v-if="running" class="dim">运行中…</span>
                <el-button v-if="running" size="small" type="danger" plain style="float:right" @click="stopRun">停止</el-button>
              </template>
              <div class="logs">
                <div v-for="(l, i) in logs" :key="i" class="log" :class="l.level">
                  <span class="t">{{ l.t }}</span>{{ l.text }}
                </div>
                <div v-if="!logs.length" class="hint">点击「运行」执行 App 用例，步骤进度与自愈事件实时展示于此</div>
              </div>
            </el-card>
          </el-col>
        </el-row>
      </el-col>
    </el-row>

    <!-- 启动 App 对话框 -->
    <el-dialog v-model="launchDlg" title="启动 App" width="460px">
      <el-form label-width="90px">
        <el-form-item label="包名">
          <el-input v-model="launchForm.package" placeholder="com.example.myapp" />
        </el-form-item>
        <el-form-item label="Activity">
          <el-input v-model="launchForm.activity" placeholder=".ui.LoginActivity" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="launchDlg = false">取消</el-button>
        <el-button type="primary" @click="doLaunch">启动</el-button>
      </template>
    </el-dialog>

    <!-- 用例编辑器 -->
    <el-dialog v-model="editorDlg" :title="editorForm.id ? '编辑 App 用例' : '新建 App 用例'" width="600px">
      <el-form label-width="90px">
        <el-form-item label="用例名称"><el-input v-model="editorForm.name" /></el-form-item>
        <el-form-item label="App 包名"><el-input v-model="editorForm.app_package" placeholder="com.example.myapp" /></el-form-item>
        <el-form-item label="启动 Activity"><el-input v-model="editorForm.app_activity" placeholder=".ui.LoginActivity" /></el-form-item>
        <el-form-item label="目标设备">
          <el-input v-model="editorForm.device_serial" placeholder="emulator-5554（留空时取工作台当前设备）" />
        </el-form-item>
        <el-form-item label="步骤（每行一步，自然语言）">
          <el-input v-model="stepText" type="textarea" :rows="8"
            placeholder="1. 启动 App com.example.myapp/.ui.MainActivity&#10;2. 点击登录按钮&#10;3. 在用户名输入框输入 admin&#10;4. 断言页面显示欢迎语" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editorDlg = false">取消</el-button>
        <el-button type="primary" @click="saveCase">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import axios from 'axios'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Refresh, VideoPlay, Upload, Back, HomeFilled, Camera, Plus,
} from '@element-plus/icons-vue'

// ---- 设备 ----
const devices = ref([])
const serial = ref('')
const online = ref(false)
const devSize = ref(null)  // {w, h}

async function loadDevices() {
  try {
    const { data } = await axios.get('/api/app/devices')
    devices.value = data || []
    if (!serial.value && devices.value.length) serial.value = devices.value[0].serial
    await loadStatus()
  } catch (e) {
    ElMessage.error('获取设备失败：' + (e.response?.data?.detail || e.message))
  }
}

async function loadStatus() {
  if (!serial.value) { online.value = false; devSize.value = null; return }
  try {
    const { data } = await axios.get(`/api/app/devices/${serial.value}/status`)
    online.value = !!data.online
    devSize.value = data.width ? { w: data.width, h: data.height } : null
  } catch (e) {
    online.value = false; devSize.value = null
  }
}

async function onDeviceChange() {
  stopScreen()
  await loadStatus()
  if (online.value) {
    loadPackages()
    startScreen()
  }
}

// ---- 投屏 ----
const canvas = ref(null)
let wsScreen = null
let pendingFrame = null
let frameDecoding = false
let rafId = 0

function startScreen() {
  if (!serial.value || !online.value) return
  stopScreen()
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  wsScreen = new WebSocket(`${proto}://${location.host}/ws/app/screen/${serial.value}`)
  wsScreen.binaryType = 'arraybuffer'
  const ctx = canvas.value?.getContext('2d')
  startRenderLoop(ctx)
  wsScreen.onmessage = (ev) => {
    if (ev.data instanceof ArrayBuffer) { pendingFrame = ev.data; return }
    // 文本消息（log/error）忽略或提示
    try {
      const { kind, payload } = JSON.parse(ev.data)
      if (kind === 'log') addLog(payload.msg, 'dim')
      else if (kind === 'error') ElMessage.warning(payload.msg)
    } catch { /* ignore */ }
  }
  wsScreen.onerror = () => {}
  wsScreen.onclose = () => { stopRenderLoop() }
}

function stopScreen() {
  if (wsScreen) { try { wsScreen.close() } catch {} wsScreen = null }
  stopRenderLoop()
}

function startRenderLoop(ctx) {
  if (rafId) return
  const tick = () => {
    if (pendingFrame && !frameDecoding && ctx) {
      const buf = pendingFrame; pendingFrame = null; frameDecoding = true
      createImageBitmap(new Blob([buf], { type: 'image/jpeg' })).then((bmp) => {
        const cv = canvas.value
        if (cv) {
          if (cv.width !== bmp.width) cv.width = bmp.width
          if (cv.height !== bmp.height) cv.height = bmp.height
          ctx.drawImage(bmp, 0, 0)
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

// ---- 触控（鼠标点击 / 拖拽滑动） ----
let downPt = null
function toDevice(offsetX, offsetY) {
  // offset 相对 canvas 内容盒：canvas 居中于 wrap，需校正其自身像素尺寸
  const cv = canvas.value
  if (!cv || !devSize.value) return null
  const rx = offsetX / cv.clientWidth
  const ry = offsetY / cv.clientHeight
  if (rx < 0 || rx > 1 || ry < 0 || ry > 1) return null
  return { x: Math.round(rx * devSize.value.w), y: Math.round(ry * devSize.value.h) }
}

function evtPos(e) {
  // 统一按 canvas 自身 getBoundingClientRect 取相对坐标，不受兄弟元素影响
  const cv = canvas.value
  if (!cv) return null
  const r = cv.getBoundingClientRect()
  return { x: e.clientX - r.left, y: e.clientY - r.top }
}

function onMouseDown(e) {
  if (!online.value) return
  const p = evtPos(e)
  downPt = p ? { x: p.x, y: p.y } : null
}
function onMouseMove() { /* 预留：绘制滑动轨迹提示线 */ }
async function onMouseUp(e) {
  if (!downPt || !online.value) { downPt = null; return }
  const x1 = downPt.x, y1 = downPt.y
  const p0 = evtPos(e)
  downPt = null
  if (!p0) return
  const x2 = p0.x, y2 = p0.y
  const dist = Math.abs(x2 - x1) + Math.abs(y2 - y1)
  const p1 = toDevice(x1, y1)
  const p2 = toDevice(x2, y2)
  if (!p1 || !p2) return
  try {
    if (dist <= 10) {
      await axios.post(`/api/app/devices/${serial.value}/tap`, { x: p1.x, y: p1.y })
    } else {
      await axios.post(`/api/app/devices/${serial.value}/swipe`,
        { x1: p1.x, y1: p1.y, x2: p2.x, y2: p2.y, duration: 400 })
    }
  } catch (err) {
    ElMessage.error('触控失败：' + (err.response?.data?.detail || err.message))
  }
}

// ---- 按键 / 截图 ----
async function key(code) {
  try { await axios.post(`/api/app/devices/${serial.value}/keyevent`, { keycode: code }) }
  catch (e) { ElMessage.error('按键失败：' + (e.response?.data?.detail || e.message)) }
}
async function snapshot() {
  try {
    const { data } = await axios.get(`/api/app/devices/${serial.value}/screenshot`)
    const a = document.createElement('a')
    a.href = `data:image/jpeg;base64,${data.data}`
    a.download = `screen_${serial.value}_${Date.now()}.jpg`
    a.click()
  } catch (e) { ElMessage.error('截图失败：' + (e.response?.data?.detail || e.message)) }
}

// ---- App 管理 ----
const packages = ref([])
const uploading = ref(false)

async function loadPackages() {
  if (!online.value) return
  try {
    const { data } = await axios.get(`/api/app/packages/${serial.value}`)
    packages.value = data || []
  } catch (e) {
    ElMessage.error('获取 App 列表失败：' + (e.response?.data?.detail || e.message))
  }
}

async function uploadApk(opt) {
  if (!online.value) { ElMessage.warning('设备不在线'); return }
  uploading.value = true
  const fd = new FormData()
  fd.append('file', opt.file)
  try {
    const { data } = await axios.post(`/api/app/install/${serial.value}`, fd)
    if (data.success) { ElMessage.success('安装成功'); loadPackages() }
    else ElMessage.error('安装失败：' + data.message)
  } catch (e) {
    ElMessage.error('上传失败：' + (e.response?.data?.detail || e.message))
  } finally {
    uploading.value = false
  }
}

async function uninstall(pkg) {
  try {
    await ElMessageBox.confirm(`确认卸载 ${pkg}？`, '卸载确认', { type: 'warning' })
    const { data } = await axios.delete(`/api/app/packages/${serial.value}/${pkg}`)
    if (data.success) { ElMessage.success('已卸载'); loadPackages() }
    else ElMessage.error('卸载失败：' + data.message)
  } catch (e) {
    if (e !== 'cancel' && e?.message !== 'cancel') {
      ElMessage.error('卸载失败：' + (e.response?.data?.detail || e.message))
    }
  }
}

// ---- 启动 App ----
const launchDlg = ref(false)
const launchForm = ref({ package: '', activity: '' })

async function doLaunch() {
  if (!launchForm.value.package || !launchForm.value.activity) {
    ElMessage.warning('请填写包名和 Activity'); return
  }
  try {
    await axios.post(`/api/app/devices/${serial.value}/launch`, {
      package: launchForm.value.package, activity: launchForm.value.activity,
    })
    ElMessage.success('已发送启动命令')
    launchDlg.value = false
  } catch (e) {
    ElMessage.error('启动失败：' + (e.response?.data?.detail || e.message))
  }
}

// ---- 键盘输入：点击投屏后捕获键盘，可打印字符走 input text，特殊键走 keyevent ----
const kbd = ref(null)
const kbdFocus = ref(false)
const keycodeMap = {
  backspace: 67, enter: 66, tab: 61, escape: 111,
  arrowup: 19, arrowdown: 20, arrowleft: 21, arrowright: 22, delete: 112,
}

function focusKbd() {
  if (online.value) kbd.value?.focus()
}

function onKeydown(e) {
  if (!online.value) return
  const code = keycodeMap[e.key?.toLowerCase?.() || '']
  if (code) {
    e.preventDefault()
    axios.post(`/api/app/devices/${serial.value}/keyevent`, { keycode: code })
      .then(() => addLog(`⌨ 按键 ${e.key}`, 'dim'))
      .catch((err) => ElMessage.error('按键失败：' + (err.response?.data?.detail || err.message)))
  }
}

async function onKbdInput(e) {
  if (!online.value) { e.target.value = ''; return }
  const text = e.target.value || ''
  e.target.value = ''
  if (!text) return
  try {
    await axios.post(`/api/app/devices/${serial.value}/type`, { text })
    addLog(`⌨ 输入 "${text}"`, 'dim')
  } catch (err) {
    ElMessage.error('输入失败：' + (err.response?.data?.detail || err.message))
  }
}

// ---- App 用例管理 ----
const appCases = computed(() => cases.value.filter(c => (c.driver_type || 'web') === 'app'))
const cases = ref([])

async function loadCases() {
  try {
    const { data } = await axios.get('/api/cases')
    cases.value = data || []
  } catch (e) {
    ElMessage.error('获取用例失败：' + (e.response?.data?.detail || e.message))
  }
}

// 用例编辑器
const editorDlg = ref(false)
const editorForm = ref({ id: null, name: '', app_package: '', app_activity: '', device_serial: '' })
const stepText = ref('')

function openEditor(row) {
  if (row) {
    editorForm.value = {
      id: row.id, name: row.name,
      app_package: row.app_package || '', app_activity: row.app_activity || '',
      device_serial: row.device_serial || '',
    }
    stepText.value = (row.steps || []).map(s => typeof s === 'string' ? s : (s.text || '')).join('\n')
  } else {
    editorForm.value = { id: null, name: '', app_package: '', app_activity: '', device_serial: serial.value }
    stepText.value = ''
  }
  editorDlg.value = true
}

async function saveCase() {
  if (!editorForm.value.name) { ElMessage.warning('请填写用例名称'); return }
  const steps = stepText.value.split('\n').map(s => s.trim()).filter(Boolean)
  const payload = {
    name: editorForm.value.name,
    module: 'APP',
    steps,
    driver_type: 'app',
    app_package: editorForm.value.app_package,
    app_activity: editorForm.value.app_activity,
    device_serial: editorForm.value.device_serial || serial.value,
  }
  try {
    if (editorForm.value.id) {
      await axios.put(`/api/cases/${editorForm.value.id}`, payload)
    } else {
      await axios.post('/api/cases', payload)
    }
    ElMessage.success('已保存')
    editorDlg.value = false
    loadCases()
  } catch (e) {
    ElMessage.error('保存失败：' + (e.response?.data?.detail || e.message))
  }
}

async function delCase(row) {
  try {
    await ElMessageBox.confirm(`确认删除用例「${row.name}」？`, '删除确认', { type: 'warning' })
    await axios.delete(`/api/cases/${row.id}`)
    ElMessage.success('已删除')
    loadCases()
  } catch (e) {
    if (e !== 'cancel' && e?.message !== 'cancel') {
      ElMessage.error('删除失败：' + (e.response?.data?.detail || e.message))
    }
  }
}

// ---- 执行 ----
const running = ref(false)
const runId = ref(null)
const runCaseId = ref(null)
const logs = ref([])

function addLog(text, level = 'info') {
  const d = new Date()
  const t = `${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`
  logs.value.push({ text, level, t })
}

async function runCase(row) {
  if (running.value) { ElMessage.warning('已有用例在执行'); return }
  running.value = true; runCaseId.value = row.id; logs.value = []
  addLog(`▶ 开始执行「${row.name}」`)
  try {
    const { data } = await axios.post(`/api/cases/${row.id}/run`)
    runId.value = data.run_id
    openRunWs(data.run_id, row)
  } catch (e) {
    ElMessage.error('启动失败：' + (e.response?.data?.detail || e.message))
    running.value = false; runCaseId.value = null
  }
}

let wsRun = null
function openRunWs(id, row) {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  wsRun = new WebSocket(`${proto}://${location.host}/ws/runs/${id}`)
  wsRun.binaryType = 'arraybuffer'
  wsRun.onmessage = (ev) => {
    // App 执行也推 frame（adb 截图），但左侧投屏已在显示设备画面，此处只处理事件
    if (ev.data instanceof ArrayBuffer) return
    const { kind, payload } = JSON.parse(ev.data)
    if (kind === 'step') {
      const icon = payload.state === 'passed' ? '✓' : payload.state === 'failed' ? '✗' : '…'
      const lvl = payload.state === 'failed' ? 'err' : payload.state === 'passed' ? 'ok' : 'info'
      addLog(`${icon} 步骤${payload.index + 1}：${payload.text}${payload.detail ? ' — ' + payload.detail : ''}`, lvl)
    } else if (kind === 'log') {
      addLog(`  ${payload.msg}`, 'dim')
    } else if (kind === 'heal') {
      const tag = payload.kind === 'assert'
        ? (payload.passed ? '✅ 断言通过' : '❌ 断言失败')
        : (payload.healed ? '🔧 VLM 自愈' : '🎯 定位')
      addLog(`  ${tag}：${payload.target || payload.claim}（${payload.strategy || payload.reason || ''}）`,
             payload.kind === 'assert' ? (payload.passed ? 'ok' : 'err') : 'vlm')
    } else if (kind === 'done') {
      const st = payload.status
      addLog(`■ 执行结束：${st}${payload.error ? ' — ' + payload.error : ''}`,
             st === 'passed' ? 'ok' : 'err')
      running.value = false; runCaseId.value = null
      try { wsRun.close() } catch {}
    }
  }
  wsRun.onerror = () => { addLog('执行连接错误', 'err'); running.value = false; runCaseId.value = null }
}

async function stopRun() {
  if (!runId.value) return
  try {
    await axios.post(`/api/runs/${runId.value}/cancel`)
    addLog('正在停止…', 'dim')
  } catch (e) {
    ElMessage.error('停止失败：' + (e.response?.data?.detail || e.message))
  }
}

onMounted(async () => {
  await loadDevices()
  await loadCases()
  if (online.value) {
    startScreen()
    loadPackages()
  }
})
onBeforeUnmount(() => {
  stopScreen()
  if (wsRun) { try { wsRun.close() } catch {} }
})
</script>

<style scoped>
.wb { display: flex; flex-direction: column; height: 100%; }
.bar { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; flex-wrap: wrap; }
.sec-title { font-weight: 600; }
.dim { color: #909399; font-size: 12px; }
.tip { margin-left: 8px; font-weight: 400; }
.body { flex: 1; overflow: auto; }
.card { margin-bottom: 12px; }
.app-row { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }

.screen-wrap { position: relative; background: #1f2d3d; border-radius: 4px; min-height: 420px;
               display: flex; justify-content: center; align-items: center; }
.screen-wrap.offline { opacity: 0.5; }
/* 只显示单个手机竖屏视图：限制最大宽度，不被拉成横向大块 */
.screen { max-width: 100%; max-height: 62vh; width: auto; height: auto; display: block;
          cursor: crosshair; touch-action: none; }
.screen-mask { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
               color: #fff; font-size: 14px; }
/* 隐藏 input：覆盖在投屏上方但完全透明，只负责捕获键盘；不响应鼠标避免挡 canvas 点击 */
.kbd-capture { position: absolute; left: 50%; top: 50%; width: 1px; height: 1px;
               opacity: 0; pointer-events: none; border: none; outline: none;
               background: transparent; color: transparent; caret-color: transparent; }
.keys { display: flex; gap: 6px; margin-top: 10px; flex-wrap: wrap; }

.log-card :deep(.el-card__body) { display: flex; flex-direction: column; }
.logs { background: #1f2d3d; border-radius: 4px; padding: 10px; min-height: 480px; max-height: 62vh; overflow: auto; }
.log { font-size: 12px; line-height: 1.8; color: #d0d6e0; font-family: ui-monospace, Menlo, monospace; }
.log .t { color: #5a6473; margin-right: 6px; }
.log.ok { color: #67c23a; }
.log.err { color: #f56c6c; }
.log.vlm { color: #e6a23c; }
.log.dim { color: #8a919f; }
.hint { color: #8a919f; font-size: 13px; text-align: center; padding: 40px; }
</style>
