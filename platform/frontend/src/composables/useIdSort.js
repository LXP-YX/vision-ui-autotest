// 统一「按 id 升/降序 + 序号列」逻辑。
// 用法：
//   const { items, sortAsc, setItems, onSortChange, indexOf } = useIdSort()
//   loadList().then(list => setItems(list))
//   <el-table :data="items" :default-sort="{ prop:'id', order:'ascending' }" @sort-change="onSortChange">
//     <el-table-column label="序号" prop="id" sortable="custom" width="60">
//       <template #default="{ row }">{{ indexOf(row) }}</template>
//     </el-table-column>
// 升序：1,2,3…；倒序：N,N-1,…,1（新增后总数 N+1，倒序时新数据在最前显示为 N+1）
import { ref, computed } from 'vue'

export function useIdSort() {
  const raw = ref([])
  const sortAsc = ref(true)

  const items = computed(() => {
    const list = [...raw.value]
    list.sort((a, b) => sortAsc.value ? (a.id - b.id) : (b.id - a.id))
    return list
  })

  function setItems(list) {
    raw.value = Array.isArray(list) ? list : []
  }

  function onSortChange({ prop, order }) {
    if (prop !== 'id') return
    // ascending / descending / null（第三态还原为升序）
    sortAsc.value = order !== 'descending'
  }

  // 序号：升序 = 位置 + 1；倒序 = 总数 - 位置
  function indexOf(row) {
    const idx = items.value.indexOf(row)
    if (idx < 0) return ''
    const total = items.value.length
    return sortAsc.value ? (idx + 1) : (total - idx)
  }

  return { items, sortAsc, setItems, onSortChange, indexOf }
}
