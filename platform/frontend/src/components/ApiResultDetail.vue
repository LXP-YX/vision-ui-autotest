<template>
  <div class="detail">
    <el-descriptions :column="2" border size="small">
      <el-descriptions-item label="方法">{{ res.method }}</el-descriptions-item>
      <el-descriptions-item label="状态码">
        <el-tag v-if="res.status_code" :type="res.status_code < 400 ? 'success' : 'danger'" size="small">
          {{ res.status_code }}
        </el-tag>
        <span v-else>—</span>
      </el-descriptions-item>
      <el-descriptions-item label="URL" :span="2">{{ req.url || res.url }}</el-descriptions-item>
      <el-descriptions-item label="耗时">{{ res.duration_ms ?? '—' }} ms</el-descriptions-item>
      <el-descriptions-item label="结果">
        <el-tag :type="res.ok ? 'success' : 'danger'" size="small">{{ res.ok ? '通过' : '未通过' }}</el-tag>
        <el-tag v-if="res.attempts > 1" type="warning" size="small" style="margin-left:6px">
          重试 {{ res.attempts }}/{{ res.retry_max }} 次
        </el-tag>
      </el-descriptions-item>
    </el-descriptions>
    <div v-if="res.error" class="err mt">错误：{{ res.error }}</div>

    <el-tabs class="mt" model-value="req">
      <el-tab-pane label="请求信息" name="req">
        <div class="lbl">请求头</div>
        <pre class="code-box">{{ pretty(req.headers) }}</pre>
        <div v-if="hasParams" class="lbl">Query 参数</div>
        <pre v-if="hasParams" class="code-box">{{ pretty(req.params) }}</pre>
        <div v-if="hasBody" class="lbl">请求体（{{ req.body_type }}，变量替换后）</div>
        <pre v-if="hasBody" class="code-box">{{ pretty(req.body) }}</pre>
      </el-tab-pane>

      <el-tab-pane label="响应信息" name="resp">
        <div class="lbl">响应头</div>
        <pre class="code-box">{{ pretty(resp.headers) }}</pre>
        <div class="lbl">响应体<span v-if="resp.truncated" class="dim2">（已截断前 20000 字符）</span></div>
        <pre class="code-box resp-body">{{ prettyBody(resp.body) }}</pre>
      </el-tab-pane>

      <el-tab-pane label="变量提取" name="extract">
        <div v-if="!hasExtractors" class="dim2">未配置变量提取器</div>
        <template v-else>
          <el-table v-if="extractedKeys.length" :data="extractedRows" border size="small">
            <el-table-column prop="k" label="变量名" width="200" />
            <el-table-column prop="v" label="提取值" show-overflow-tooltip />
          </el-table>
          <div v-if="missedRows.length" style="margin-top:6px">
            <div v-for="(m, i) in missedRows" :key="i" class="err assert-line">
              ✗ {{ m.name }}（{{ m.jsonpath }}）— {{ m.reason }}
            </div>
          </div>
          <div v-if="!extractedKeys.length && !missedRows.length" class="dim2">配置了提取器但无任何结果（未执行？）</div>
        </template>
      </el-tab-pane>

      <el-tab-pane label="断言结果" name="assert">
        <div v-if="!(res.asserts && res.asserts.length)" class="dim2">
          无显式断言（默认判据：状态码 &lt; 400）
        </div>
        <div v-for="(a, i) in res.asserts" :key="i" :class="a.ok ? 'ok' : 'err'" class="assert-line">
          {{ a.ok ? '✓' : '✗' }} {{ a.detail }}
        </div>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({ res: { type: Object, required: true } })

const req = computed(() => props.res.request || {})
const resp = computed(() => props.res.response || {})
const hasParams = computed(() => req.value.params && Object.keys(req.value.params).length > 0)
const hasBody = computed(() => req.value.body !== undefined && req.value.body !== null && req.value.body !== '')

const extractedKeys = computed(() => Object.keys(props.res.extracted || {}))
const extractedRows = computed(() =>
  extractedKeys.value.map(k => ({ k, v: fmt(props.res.extracted[k]) })))
const missedRows = computed(() => props.res.missed_extractors || [])
// 用接口定义里的 extractors 配置判断"是否配置了提取器"，而非依赖运行结果推断
const hasExtractors = computed(() => (props.res.extractors || []).length > 0)

function fmt(v) {
  if (v === null || v === undefined) return ''
  return typeof v === 'object' ? JSON.stringify(v) : String(v)
}

// 对象/数组美化；字符串原样
function pretty(v) {
  if (v === null || v === undefined || v === '') return '（空）'
  if (typeof v === 'string') return v
  try { return JSON.stringify(v, null, 2) } catch { return String(v) }
}

// 响应体：尝试 JSON 美化，失败按原文
function prettyBody(text) {
  if (!text) return '（空）'
  try { return JSON.stringify(JSON.parse(text), null, 2) } catch { return text }
}
</script>

<style scoped>
.detail { font-size: 13px; }
.mt { margin-top: 8px; }
.lbl { font-weight: 600; margin: 8px 0 4px; font-size: 12px; color: #606266; }
.code-box {
  background: #1e1e1e; color: #d4d4d4; border-radius: 4px; padding: 8px;
  font-family: Consolas, Menlo, monospace; font-size: 12px; line-height: 1.5;
  white-space: pre-wrap; word-break: break-all; max-height: 260px; overflow: auto; margin: 0;
}
.resp-body { max-height: 320px; }
.assert-line { font-family: Consolas, Menlo, monospace; font-size: 12px; line-height: 1.7; }
.ok { color: #67c23a; }
.err { color: #f56c6c; }
.dim2 { color: #909399; font-size: 12px; }
</style>
