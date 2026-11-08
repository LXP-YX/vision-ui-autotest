// 统一多选 + 批量删除逻辑。
// 用法：
//   const { selection, onSelectionChange, batchDelete } = useMultiSelect()
//   <el-table @selection-change="onSelectionChange">
//     <el-table-column type="selection" width="42" />
//   <el-button :disabled="!selection.value.length" @click="onBatchDelete">删除选中</el-button>
//   function onBatchDelete() { batchDelete({ label: '用例', deleteOne: r => axios.delete(`/api/cases/${r.id}`), onDone: loadCases }) }
import { ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

export function useMultiSelect() {
  const selection = ref([])

  function onSelectionChange(rows) {
    selection.value = rows || []
  }

  // 批量删除：二次确认 → 并发调用单条删除接口 → 汇总结果
  async function batchDelete({ label = '记录', deleteOne, onDone, nameOf = r => r.name || r.id }) {
    const rows = selection.value
    if (!rows.length) return
    const preview = rows.slice(0, 3).map(nameOf).join('、')
    const suffix = rows.length > 3 ? ` 等 ${rows.length} 项` : ''
    try {
      await ElMessageBox.confirm(
        `确认删除${label}「${preview}${suffix}」？此操作不可恢复。`,
        '批量删除确认',
        { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
      )
    } catch { return }
    const results = await Promise.allSettled(rows.map(r => deleteOne(r)))
    const failed = results.filter(x => x.status === 'rejected').length
    if (failed) ElMessage.warning(`删除完成：成功 ${rows.length - failed} 项，失败 ${failed} 项`)
    else ElMessage.success(`已删除 ${rows.length} 项`)
    selection.value = []
    if (typeof onDone === 'function') await onDone()
  }

  // 单行删除：仅弹二次确认，具体动作外部执行
  async function confirmRemove({ label = '记录', name = '' } = {}) {
    try {
      await ElMessageBox.confirm(
        `确认删除${label}${name ? `「${name}」` : ''}？此操作不可恢复。`,
        '删除确认',
        { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
      )
      return true
    } catch {
      return false
    }
  }

  return { selection, onSelectionChange, batchDelete, confirmRemove }
}
