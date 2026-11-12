<template>
  <div v-if="!enabled">
    <el-alert type="warning" :closable="false" show-icon
      title="录制功能不可用"
      description="录制生成用例需要真实浏览器窗口，仅在本地部署（RECORDING_ENABLED=1）时开放。请下载代码后本地启动平台再使用此功能。" />
  </div>

  <div v-else>
    <div class="bar">
      <span class="sec-title">录制生成用例（本地）</span>
      <span>
        <el-button v-if="!recording" type="success" size="small" :loading="starting" @click="start">开始录制</el-button>
        <template v-else>
          <el-button type="primary" size="small" plain @click="mark">切分用例</el-button>
          <el-button type="danger" size="small" :loading="stopping" @click="stop">停止并生成</el-button>
        </template>
      </span>
    </div>

    <el-alert type="info" :closable="false" show-icon class="mb"
      title="点『开始录制』后，平台会在你的电脑上弹出 Playwright Inspector 浏览器窗口，直接打开「测试环境管理」里默认环境的站点 URL。请在弹出窗口中操作被测系统，所有操作（含新增、删除、提交等）均会被录制。想把操作拆成多条用例时，在一条流程结束后点『切分用例』（点几次就多切几条）。完成后回到本页点『停止并生成』或直接关闭浏览器，平台会用 AI 把录制内容翻译成中文用例草稿。" />

    <div class="sec-title mb">录制日志</div>
    <div class="acts">
      <div v-for="(log, i) in logs" :key="i" class="act">
        <span class="idx">{{ i + 1 }}</span>
        <span>{{ log }}</span>
      </div>
      <div v-if="!logs.length" class="hint2">
        {{ recording ? '录制中，请在弹出的 Playwright Inspector 窗口里操作…' : '尚未开始录制' }}
      </div>
    </div>

    <!-- 停止后：翻译草稿对照 -->
    <el-dialog v-model="draftVisible" title="录制生成的用例草稿（待审核）" width="640px">
      <el-alert type="success" :closable="false" show-icon class="mb"
        :title="`已翻译出 ${drafts.length} 条用例草稿，已存入草稿区（用例列表中标记『草稿/AI』），请到用例列表审核入库。`" />
      <div v-for="(c,i) in drafts" :key="i" class="draft">
        <b>{{ c.name }}</b> <el-tag size="small">{{ c.module }}</el-tag>
        <div v-for="(s,j) in c.steps" :key="j" class="step-line">{{ j+1 }}. {{ s }}</div>
      </div>
      <template #footer>
        <el-button type="primary" @click="draftVisible=false">知道了</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import axios from 'axios'
import { ElMessage } from 'element-plus'

const enabled = ref(false)
const recording = ref(false)
const starting = ref(false)
const stopping = ref(false)
const sid = ref(null)
const ws = ref(null)
const logs = ref([])
const drafts = ref([])
const draftVisible = ref(false)

onMounted(async () => {
  try {
    const { data } = await axios.get('/api/record/enabled')
    enabled.value = !!data.enabled
  } catch (e) {
    enabled.value = false
  }
})

async function start() {
  starting.value = true
  logs.value = []
  try {
    const { data } = await axios.post('/api/record/start')
    sid.value = data.session_id
    openWs(data.session_id)
  } catch (e) {
    ElMessage.error('启动失败：' + (e.response?.data?.detail || e.message))
    starting.value = false
  }
}

function mark() {
  if (ws.value && ws.value.readyState === WebSocket.OPEN) {
    ws.value.send(JSON.stringify({ type: 'mark' }))
  }
}

function openWs(id) {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const sock = new WebSocket(`${proto}://${location.host}/ws/record/${id}`)
  ws.value = sock
  sock.onmessage = (ev) => {
    const { kind, payload } = JSON.parse(ev.data)
    if (kind === 'ready') {
      recording.value = true; starting.value = false
      ElMessage.success('录制就绪，请在弹出的 Playwright Inspector 窗口里操作')
    } else if (kind === 'record_log') {
      logs.value.push(payload.msg)
    } else if (kind === 'error') {
      // 启动失败（如浏览器未安装）：写进日志并退回未录制态，否则只有一条误导的提示
      logs.value.push(payload.msg)
      ElMessage.error(payload.msg)
      starting.value = false
      recording.value = false
      sock.close()
    }
  }
  sock.onerror = () => { ElMessage.error('录制连接错误'); starting.value = false }
}

async function stop() {
  stopping.value = true
  try {
    if (ws.value) ws.value.close()
    const { data } = await axios.post(`/api/record/${sid.value}/stop`)
    drafts.value = data.cases || []
    draftVisible.value = true
    ElMessage.success(`录制完成，生成 ${data.count} 条草稿`)
  } catch (e) {
    ElMessage.error('生成失败：' + (e.response?.data?.detail || e.message))
  } finally {
    recording.value = false
    stopping.value = false
  }
}
</script>

<style scoped>
.bar { display: flex; justify-content: space-between; align-items: center; margin: 4px 0 8px; }
.sec-title { font-weight: 600; }
.mb { margin-bottom: 10px; }
.acts { background: #f6f8fa; border-radius: 4px; padding: 12px; min-height: 300px; max-height: 60vh; overflow: auto; }
.act { display: flex; gap: 8px; font-size: 13px; line-height: 2; }
.act .idx { flex: none; width: 24px; color: #909399; }
.hint2 { color: #8a919f; font-size: 13px; text-align: center; padding: 40px; }
.draft { border-left: 3px solid #67c23a; background: #f0f9eb; padding: 6px 10px; margin-bottom: 8px; }
.draft .step-line { font-size: 12px; color: #606266; line-height: 1.6; }
</style>
