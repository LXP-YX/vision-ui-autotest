<template>
  <div>
    <div class="bar">
      <span class="sec-title">APP 测试环境管理</span>
      <span>
        <el-button type="primary" size="small" @click="openEdit()">新建环境</el-button>
        <el-button type="danger" size="small" :disabled="!selection.length"
                   @click="onBatchDelete">删除选中{{ selection.length ? `（${selection.length}）` : '' }}</el-button>
      </span>
    </div>
    <el-alert type="info" :closable="false" show-icon class="mb"
      title="维护多套 APP 测试环境（平台 + 设备 + 被测 App + 可选账号），全部落库，与 WEB 测试环境相互独立。Android 执行走 adb，iOS 执行走 Appium（需 Appium Server 与 WebDriverAgent）。" />

    <div class="picker mb">
      <span>当前默认环境：</span>
      <el-select v-model="defaultId" placeholder="选择默认环境" size="small" style="width:220px" @change="onPickDefault">
        <el-option v-for="e in envs" :key="e.id" :label="e.name" :value="e.id" />
      </el-select>
      <el-button v-if="defaultPlatform === 'ios'" size="small" @click="checkAppium">检查 Appium Server</el-button>
      <el-tag v-if="appiumState" :type="appiumState.ok ? 'success' : 'danger'" size="small">
        {{ appiumState.ok ? 'Appium 正常' : `Appium 异常：${appiumState.error || ''}` }}
      </el-tag>
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
      <el-table-column label="平台" width="90">
        <template #default="{ row }">
          <el-tag :type="row.platform === 'ios' ? 'warning' : 'success'" size="small">
            {{ row.platform === 'ios' ? 'iOS' : 'Android' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="device_id" label="设备（serial / udid）" min-width="180" show-overflow-tooltip />
      <el-table-column prop="app_id" label="被测 App（package / bundle_id）" min-width="180" show-overflow-tooltip />
      <el-table-column prop="app_activity" label="启动 Activity" min-width="130" show-overflow-tooltip />
      <el-table-column prop="username" label="账号" width="100" />
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

    <el-dialog v-model="editVisible" :title="form.id ? `编辑环境 #${form.id}` : '新建环境'" width="560px">
      <el-form :model="form" label-width="150px">
        <el-form-item label="环境名"><el-input v-model="form.name" placeholder="如 安卓测试机 / iOS 模拟器" /></el-form-item>
        <el-form-item label="平台">
          <el-radio-group v-model="form.platform">
            <el-radio value="android">Android（adb）</el-radio>
            <el-radio value="ios">iOS（Appium）</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="设备标识">
          <el-select v-model="form.device_id" filterable allow-create default-first-option
                     placeholder="选择已连接设备或直接输入 serial / udid" style="width:100%">
            <el-option v-for="d in devices" :key="d.udid || d.serial"
                       :label="`${d.name || d.serial}（${d.kind === 'simulator' ? '模拟器' : d.kind === 'device' ? '真机' : 'adb'} · ${d.udid || d.serial}）`"
                       :value="d.udid || d.serial" />
          </el-select>
        </el-form-item>
        <el-form-item :label="form.platform === 'ios' ? '被测 App（bundle_id）' : '被测 App（包名）'">
          <el-input v-model="form.app_id" :placeholder="form.platform === 'ios' ? '如 com.example.myapp' : '如 com.example.myapp'" />
        </el-form-item>
        <el-form-item v-if="form.platform === 'android'" label="启动 Activity（可选）">
          <el-input v-model="form.app_activity" placeholder="如 .ui.LoginActivity；留空则启动默认 Activity" />
        </el-form-item>
        <el-form-item label="登录账号（可选）"><el-input v-model="form.username" placeholder="App 内登录用户名" /></el-form-item>
        <el-form-item label="登录密码（可选）">
          <el-input v-model="form.password" type="password" show-password
            :placeholder="form.id ? '留空则不修改' : 'App 内登录密码'" />
        </el-form-item>
        <el-form-item label="备注"><el-input v-model="form.remark" placeholder="设备型号 / 版本等备注" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, watch } from 'vue'
import axios from 'axios'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete } from '@element-plus/icons-vue'
import { useIdSort } from '../composables/useIdSort'
import { useMultiSelect } from '../composables/useMultiSelect'

const { items: envs, setItems: setEnvs, onSortChange, indexOf } = useIdSort()
const { selection, onSelectionChange, batchDelete } = useMultiSelect()
const defaultId = ref(null)
const editVisible = ref(false)
const devices = ref([])
const appiumState = ref(null)
const form = reactive({ id: null, name: '', platform: 'android', device_id: '',
  app_id: '', app_activity: '', username: '', password: '', remark: '', is_default: false })

const defaultPlatform = computed(() => {
  const d = (envs.value || []).find(e => e.id === defaultId.value)
  return d ? d.platform : ''
})

async function load() {
  const { data } = await axios.get('/api/app/envs')
  setEnvs(data)
  const def = (data || []).find(e => e.is_default)
  defaultId.value = def ? def.id : null
}

async function loadDevices(platform) {
  try {
    const { data } = await axios.get('/api/app/devices', { params: { platform } })
    devices.value = platform === 'ios'
      ? (data || []).map(d => ({ ...d, serial: d.udid }))
      : (data || []).map(d => ({ ...d, serial: d.serial, name: d.serial }))
  } catch (e) {
    devices.value = []
  }
}

watch(() => form.platform, p => { if (editVisible.value) loadDevices(p) })

async function checkAppium() {
  try {
    const { data } = await axios.get('/api/app/appium-status')
    appiumState.value = data
    ElMessage[data.ok ? 'success' : 'error'](data.ok ? 'Appium Server 连接正常' : `Appium 异常：${data.error || ''}`)
  } catch (e) {
    ElMessage.error('Appium 状态查询失败')
  }
}

function openEdit(row) {
  if (row) {
    Object.assign(form, { id: row.id, name: row.name, platform: row.platform || 'android',
      device_id: row.device_id || '', app_id: row.app_id || '', app_activity: row.app_activity || '',
      username: row.username || '', password: '', remark: row.remark || '', is_default: !!row.is_default })
  } else {
    Object.assign(form, { id: null, name: '', platform: 'android', device_id: '',
      app_id: '', app_activity: '', username: '', password: '', remark: '', is_default: false })
  }
  editVisible.value = true
  loadDevices(form.platform)
}

async function save() {
  if (!form.name) { ElMessage.warning('请填写环境名'); return }
  if (!form.device_id) { ElMessage.warning('请填写设备标识（serial / udid）'); return }
  if (form.platform === 'android' && !form.app_id) { ElMessage.warning('请填写被测 App 包名'); return }
  const payload = { name: form.name, platform: form.platform, device_id: form.device_id,
    app_id: form.app_id, app_activity: form.app_activity, username: form.username,
    password: form.password, remark: form.remark, is_default: form.is_default }
  if (form.id) await axios.put(`/api/app/envs/${form.id}`, payload)
  else await axios.post('/api/app/envs', payload)
  editVisible.value = false
  ElMessage.success('已保存')
  load()
}

async function remove(row) {
  await ElMessageBox.confirm(`确认删除 APP 环境「${row.name}」？`, '确认', { type: 'warning' })
  await axios.delete(`/api/app/envs/${row.id}`)
  ElMessage.success('已删除'); load()
}

async function setDefault(row) {
  await axios.post(`/api/app/envs/${row.id}/set-default`)
  ElMessage.success(`已将「${row.name}」设为默认`); load()
}

async function onPickDefault(id) {
  await axios.post(`/api/app/envs/${id}/set-default`)
  ElMessage.success('默认环境已切换'); load()
}

function onBatchDelete() {
  batchDelete({
    label: 'APP 环境',
    deleteOne: r => axios.delete(`/api/app/envs/${r.id}`),
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
