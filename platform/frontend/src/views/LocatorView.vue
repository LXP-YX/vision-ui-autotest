<template>
  <div>
    <el-tabs v-model="tab">
      <!-- ================= 自愈知识库 ================= -->
      <el-tab-pane label="自愈知识库" name="locator">
        <div class="bar">
          <span class="sec-title">自愈知识库</span>
          <span class="bar-actions">
            <el-button size="small" @click="load">刷新</el-button>
            <el-button size="small" type="primary" :loading="classifying" @click="onClassifyAll">
              AI 批量分类
            </el-button>
            <el-button size="small" type="danger" :disabled="!selection.length"
                       @click="onBatchDelete">删除选中{{ selection.length ? `（${selection.length}）` : '' }}</el-button>
          </span>
        </div>
        <p class="desc">自然语言锚点 → 稳定选择器的映射。执行含视觉定位的用例后自动积累，跨用例/跨运行复用，平台越跑越稳。</p>
        <el-table :data="locators" border size="small"
                  :default-sort="{ prop: 'id', order: 'ascending' }"
                  @sort-change="onSortChange"
                  @selection-change="onSelectionChange">
          <el-table-column type="selection" width="42" />
          <el-table-column label="" width="52" sortable="custom" prop="id" align="center" header-align="center">
            <template #default="{ row }">{{ indexOf(row) }}</template>
          </el-table-column>
          <el-table-column prop="anchor" label="锚点（自然语言）" min-width="150" />
          <el-table-column prop="selector" label="稳定选择器" min-width="190" show-overflow-tooltip />
          <el-table-column label="策略" width="88">
            <template #default="{ row }">
              <el-tag size="small" :type="strategyType(row.strategy)">{{ strategyLabel(row.strategy) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="元素类型" width="120">
            <template #default="{ row }">
              <span v-if="row.element_type" class="el-type-tag">{{ row.element_type }}</span>
              <el-button v-else size="small" link type="primary"
                         :loading="classifyingId === row.id"
                         @click="onClassifyOne(row)">AI 判断</el-button>
            </template>
          </el-table-column>
          <el-table-column prop="hit_count" label="命中" width="58" />
          <el-table-column prop="fail_count" label="失败" width="58" />
          <el-table-column label="更新时间" min-width="148">
            <template #default="{ row }">{{ (row.updated_at || '').replace('T', ' ').slice(0, 19) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="80">
            <template #default="{ row }">
              <el-button size="small" type="danger" plain @click="removeOne(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-if="!locators.length" description="暂无自愈记录（执行含视觉定位的用例后会自动积累）" />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import axios from 'axios'
import { ElMessage } from 'element-plus'
import { useIdSort } from '../composables/useIdSort'
import { useMultiSelect } from '../composables/useMultiSelect'

const tab = ref('locator')

const { items: locators, setItems: setLocators, onSortChange, indexOf } = useIdSort()
const { selection, onSelectionChange, batchDelete, confirmRemove } = useMultiSelect()

const classifying = ref(false)
const classifyingId = ref(null)

function strategyType(s) {
  return { store: 'info', semantic: 'success', vlm: 'warning', hint: '' }[s] || 'info'
}

function strategyLabel(s) {
  return { store: '知识库', semantic: '文本匹配', vlm: '视觉定位', hint: '手动指定' }[s] || s
}

async function load() {
  const { data } = await axios.get('/api/locators')
  setLocators(data)
}

async function removeOne(row) {
  if (!(await confirmRemove({ label: '自愈记录', name: row.anchor }))) return
  await axios.delete(`/api/locators/${row.id}`)
  ElMessage.success('已删除'); load()
}

function onBatchDelete() {
  batchDelete({
    label: '自愈记录',
    nameOf: r => r.anchor,
    deleteOne: r => axios.delete(`/api/locators/${r.id}`),
    onDone: load,
  })
}

async function onClassifyOne(row) {
  classifyingId.value = row.id
  try {
    const { data } = await axios.post('/api/locators/classify', { ids: [row.id] })
    const result = data.results?.find(r => r.id === row.id)
    if (result?.element_type) {
      row.element_type = result.element_type
      ElMessage.success(`已判断：${result.element_type}`)
    } else {
      ElMessage.warning('AI 未能识别该元素类型')
    }
  } catch (e) {
    ElMessage.error('AI 分类失败：' + (e.response?.data?.detail || e.message))
  } finally {
    classifyingId.value = null
  }
}

async function onClassifyAll() {
  const unclassified = locators.value.filter(r => !r.element_type)
  if (!unclassified.length) {
    ElMessage.info('所有条目已有分类，无需操作')
    return
  }
  classifying.value = true
  try {
    const { data } = await axios.post('/api/locators/classify', {})
    data.results?.forEach(r => {
      const row = locators.value.find(l => l.id === r.id)
      if (row && r.element_type) row.element_type = r.element_type
    })
    ElMessage.success(`AI 分类完成，共更新 ${data.updated} 条`)
  } catch (e) {
    ElMessage.error('AI 批量分类失败：' + (e.response?.data?.detail || e.message))
  } finally {
    classifying.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.bar { display: flex; justify-content: space-between; align-items: center; margin: 4px 0 8px; }
.bar-actions { display: flex; gap: 6px; }
.sec-title { font-weight: 600; }
.desc { color: #909399; font-size: 12px; margin: 0 0 10px; }
.el-type-tag {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 12px;
  background: #f0f9eb;
  color: #67c23a;
  border: 1px solid #b3e19d;
}
</style>
