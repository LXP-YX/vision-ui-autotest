<template>
  <div>
    <div class="bar">
      <span class="sec-title">测试环境管理</span>
      <span>
        <el-button type="primary" size="small" @click="openEdit()">新建环境</el-button>
        <el-button type="danger" size="small" :disabled="!selection.length"
                   @click="onBatchDelete">删除选中{{ selection.length ? `（${selection.length}）` : '' }}</el-button>
      </span>
    </div>
    <el-alert type="info" :closable="false" show-icon class="mb"
      title="维护多套测试环境（站点 URL / 账号 / 密码），全部落库。站点 URL 填打开即用的地址（一般填登录页 URL），登录、录制、执行都直接打开它。下拉/按钮选中的默认环境供登录、录制、执行使用。" />

    <div class="picker mb">
      <span>当前默认环境：</span>
      <el-select v-model="defaultId" placeholder="选择默认环境" size="small" style="width:220px" @change="onPickDefault">
        <el-option v-for="e in envs" :key="e.id" :label="e.name" :value="e.id" />
      </el-select>
    </div>

    <el-table :data="envs" border size="small"
              :default-sort="{ prop: 'id', order: 'ascending' }"
              @sort-change="onSortChange"
              @selection-change="onSelectionChange">
      <el-table-column type="selection" width="42" />
      <el-table-column label="" width="62" sortable="custom" prop="id" align="center" header-align="center">
        <template #default="{ row }">{{ indexOf(row) }}</template>
      </el-table-column>
      <el-table-column prop="name" label="环境名" min-width="110" />
      <el-table-column prop="base_url" label="站点 URL" min-width="200" show-overflow-tooltip />
      <el-table-column prop="username" label="账号" width="120" />
      <el-table-column label="默认" width="70">
        <template #default="{ row }">
          <el-tag v-if="row.is_default" type="success" size="small">默认</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="220">
        <template #default="{ row }">
          <el-button size="small" type="success" :disabled="row.is_default" @click="setDefault(row)">设默认</el-button>
          <el-button size="small" @click="openEdit(row)">编辑</el-button>
          <el-tooltip content="删除" placement="top">
            <el-button size="small" type="danger" plain circle :icon="Delete" @click="remove(row)" />
          </el-tooltip>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="editVisible" :title="form.id ? `编辑环境 #${form.id}` : '新建环境'" width="520px">
      <el-form :model="form" label-width="90px">
        <el-form-item label="环境名"><el-input v-model="form.name" placeholder="如 测试环境 / 预发环境" /></el-form-item>
        <el-form-item label="站点 URL"><el-input v-model="form.base_url" placeholder="https://your-site.example.com/login" /></el-form-item>
        <el-form-item label="账号"><el-input v-model="form.username" placeholder="登录用户名" /></el-form-item>
        <el-form-item label="密码">
          <el-input v-model="form.password" type="password" show-password
            :placeholder="form.id ? '留空则不修改' : '登录密码'" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import axios from 'axios'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete } from '@element-plus/icons-vue'
import { useIdSort } from '../composables/useIdSort'
import { useMultiSelect } from '../composables/useMultiSelect'

const { items: envs, setItems: setEnvs, onSortChange, indexOf } = useIdSort()
const { selection, onSelectionChange, batchDelete } = useMultiSelect()
const defaultId = ref(null)
const editVisible = ref(false)
const form = reactive({ id: null, name: '', base_url: '', username: '', password: '' })

async function load() {
  const { data } = await axios.get('/api/test-envs')
  setEnvs(data)
  const def = (data || []).find(e => e.is_default)
  defaultId.value = def ? def.id : null
}

function openEdit(row) {
  if (row) {
    Object.assign(form, { id: row.id, name: row.name, base_url: row.base_url,
      username: row.username, password: '' })
  } else {
    Object.assign(form, { id: null, name: '', base_url: '', username: '', password: '' })
  }
  editVisible.value = true
}

async function save() {
  if (!form.name || !form.base_url) { ElMessage.warning('请填写环境名和站点 URL'); return }
  const payload = { name: form.name, base_url: form.base_url,
    username: form.username, password: form.password }
  if (form.id) await axios.put(`/api/test-envs/${form.id}`, payload)
  else await axios.post('/api/test-envs', payload)
  editVisible.value = false
  ElMessage.success('已保存')
  load()
}

async function remove(row) {
  await ElMessageBox.confirm(`确认删除环境「${row.name}」？`, '确认', { type: 'warning' })
  await axios.delete(`/api/test-envs/${row.id}`)
  ElMessage.success('已删除'); load()
}

async function setDefault(row) {
  await axios.post(`/api/test-envs/${row.id}/set-default`)
  ElMessage.success(`已将「${row.name}」设为默认`); load()
}

async function onPickDefault(id) {
  await axios.post(`/api/test-envs/${id}/set-default`)
  ElMessage.success('默认环境已切换'); load()
}

function onBatchDelete() {
  batchDelete({
    label: '测试环境',
    deleteOne: r => axios.delete(`/api/test-envs/${r.id}`),
    onDone: load,
  })
}

onMounted(load)
</script>

<style scoped>
.bar { display: flex; justify-content: space-between; align-items: center; margin: 4px 0 8px; }
.sec-title { font-weight: 600; }
.mb { margin-bottom: 12px; }
.picker { display: flex; align-items: center; gap: 8px; }
</style>
