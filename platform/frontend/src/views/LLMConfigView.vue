<template>
  <div>
    <div class="bar">
      <span class="sec-title">大模型配置</span>
      <span>
        <el-button type="primary" size="small" @click="openEdit()">新建配置</el-button>
        <el-button type="danger" size="small" :disabled="!selection.length"
                   @click="onBatchDelete">删除选中{{ selection.length ? `（${selection.length}）` : '' }}</el-button>
      </span>
    </div>

    <el-table :data="configs" border size="small"
              :default-sort="{ prop: 'id', order: 'ascending' }"
              @sort-change="onSortChange"
              @selection-change="onSelectionChange">
      <el-table-column type="selection" width="42" />
      <el-table-column label="" width="62" sortable="custom" prop="id" align="center" header-align="center">
        <template #default="{ row }">{{ indexOf(row) }}</template>
      </el-table-column>
      <el-table-column prop="name" label="别名" min-width="120" />
      <el-table-column prop="chat_model" label="对话模型" min-width="110" />
      <el-table-column prop="base_url" label="端点" min-width="200" show-overflow-tooltip />
      <el-table-column label="默认" width="70">
        <template #default="{ row }">
          <el-tag v-if="row.is_default" type="success" size="small">默认</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="260">
        <template #default="{ row }">
          <el-button size="small" @click="testConn(row)" :loading="testingId === row.id">测试连接</el-button>
          <el-button size="small" @click="openEdit(row)">编辑</el-button>
          <el-button size="small" type="success" :disabled="row.is_default" @click="setDefault(row)">设默认</el-button>
          <el-tooltip content="删除" placement="top">
            <el-button size="small" type="danger" plain circle :icon="Delete" @click="remove(row)" />
          </el-tooltip>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="editVisible" :title="form.id ? `编辑配置 #${form.id}` : '新建配置'" width="560px">
      <el-form :model="form" label-width="90px">
        <el-form-item label="别名"><el-input v-model="form.name" /></el-form-item>
        <el-form-item label="端点 URL"><el-input v-model="form.base_url" placeholder="https://.../v1" /></el-form-item>
        <el-form-item label="API Key"><el-input v-model="form.api_key" placeholder="pk-... （编辑时留掩码则不改）" /></el-form-item>
        <el-form-item label="对话模型"><el-input v-model="form.chat_model" placeholder="GLM-4.6V" /></el-form-item>
        <el-form-item label="视觉模型"><el-input v-model="form.vision_model" placeholder="GLM-4.6V" /></el-form-item>
        <el-form-item label="超时(秒)"><el-input-number v-model="form.timeout" :min="5" :max="300" /></el-form-item>
        <el-form-item label="校验SSL"><el-switch v-model="form.verify_ssl" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import axios from 'axios'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete } from '@element-plus/icons-vue'
import { useIdSort } from '../composables/useIdSort'
import { useMultiSelect } from '../composables/useMultiSelect'

const { items: configs, setItems: setConfigs, onSortChange, indexOf } = useIdSort()
const { selection, onSelectionChange, batchDelete } = useMultiSelect()
const editVisible = ref(false)
const testingId = ref(null)
const form = ref({})

async function load() {
  const { data } = await axios.get('/api/llm-configs')
  setConfigs(data)
}

function openEdit(row) {
  form.value = row
    ? { ...row }
    : { id: null, name: '', base_url: 'https://your-llm-endpoint.example.com/v1', api_key: '', chat_model: '', vision_model: '', timeout: 60, verify_ssl: true }
  editVisible.value = true
}

async function save() {
  if (!form.value.name) { ElMessage.warning('请填写别名'); return }
  const payload = { ...form.value }
  delete payload.id; delete payload.is_default; delete payload.enabled
  if (payload.enabled === undefined) payload.enabled = true
  if (form.value.id) await axios.put(`/api/llm-configs/${form.value.id}`, { ...payload, enabled: true })
  else await axios.post('/api/llm-configs', { ...payload, enabled: true })
  editVisible.value = false
  ElMessage.success('已保存')
  load()
}

async function remove(row) {
  await ElMessageBox.confirm(`确认删除配置「${row.name}」？`, '确认', { type: 'warning' })
  await axios.delete(`/api/llm-configs/${row.id}`)
  ElMessage.success('已删除'); load()
}

async function setDefault(row) {
  await axios.post(`/api/llm-configs/${row.id}/set-default`)
  ElMessage.success('已设为默认'); load()
}

async function testConn(row) {
  testingId.value = row.id
  try {
    const { data } = await axios.post(`/api/llm-configs/${row.id}/test`)
    if (data.ok) ElMessage.success(`连接成功：${data.model}，延迟 ${data.latency_ms}ms，回复「${data.reply}」`)
    else ElMessage.error(`连接失败：${data.error}`)
  } finally {
    testingId.value = null
  }
}

function onBatchDelete() {
  batchDelete({
    label: '大模型配置',
    deleteOne: r => axios.delete(`/api/llm-configs/${r.id}`),
    onDone: load,
  })
}

onMounted(load)
</script>

<style scoped>
.bar { display: flex; justify-content: space-between; align-items: center; margin: 4px 0 8px; }
.sec-title { font-weight: 600; }
</style>
