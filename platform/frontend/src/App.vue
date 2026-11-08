<template>
  <el-container style="height: 100vh">
    <!-- 左侧菜单栏 -->
    <el-aside width="200px" class="side">
      <div class="brand">vision-ui-autotest</div>
      <el-menu :default-active="activeMenu" router class="side-menu">
        <el-menu-item index="/executions">
          <el-icon><VideoPlay /></el-icon>
          <span>测试计划</span>
        </el-menu-item>
        <el-menu-item index="/cases">
          <el-icon><List /></el-icon>
          <span>用例管理</span>
        </el-menu-item>
        <el-menu-item index="/app-workbench">
          <el-icon><Cellphone /></el-icon>
          <span>APP 工作台</span>
        </el-menu-item>
        <el-menu-item index="/api-testing">
          <el-icon><Connection /></el-icon>
          <span>接口管理</span>
        </el-menu-item>
        <el-menu-item v-if="recordEnabled" index="/record">
          <el-icon><VideoCamera /></el-icon>
          <span>录制用例</span>
        </el-menu-item>
        <el-menu-item index="/test-data">
          <el-icon><Setting /></el-icon>
          <span>测试数据管理</span>
        </el-menu-item>
        <el-menu-item index="/llm">
          <el-icon><Cpu /></el-icon>
          <span>大模型配置</span>
        </el-menu-item>
        <el-menu-item index="/locators">
          <el-icon><MagicStick /></el-icon>
          <span>自愈知识库</span>
        </el-menu-item>
        <el-menu-item index="/users" disabled>
          <el-icon><User /></el-icon>
          <span>用户管理（待建）</span>
        </el-menu-item>
      </el-menu>
    </el-aside>

    <!-- 右侧内容区 -->
    <el-container>
      <el-header class="header">
        <span class="title">{{ currentTitle }}</span>
      </el-header>
      <el-main class="content">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { computed, ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import axios from 'axios'

const route = useRoute()
const activeMenu = computed(() => route.path)
const currentTitle = computed(() => route.meta.title || 'vision-ui-autotest')

const recordEnabled = ref(false)
onMounted(async () => {
  try {
    const { data } = await axios.get('/api/record/enabled')
    recordEnabled.value = !!data.enabled
  } catch (e) {
    recordEnabled.value = false
  }
})
</script>

<style>
body { margin: 0; }
.side { background: #1f2d3d; color: #fff; display: flex; flex-direction: column; }
.brand { height: 56px; line-height: 56px; text-align: center; font-size: 16px; font-weight: 600; color: #fff; border-bottom: 1px solid #2b3c50; }
.side-menu { border-right: none; background: transparent; flex: 1; }
.side-menu .el-menu-item { color: #c0ccda; }
.side-menu .el-menu-item.is-active { color: #fff; background: #263445; }
.side-menu .el-menu-item:hover { background: #263445; }
.header { display: flex; align-items: baseline; gap: 16px; background: #fff; border-bottom: 1px solid #e4e7ed; }
.title { font-size: 18px; font-weight: 600; }
.sub { font-size: 12px; color: #909399; }
.content { padding: 12px; background: #f5f7fa; overflow: auto; }

/* 全局表头统一为主题蓝底 + 白字，覆盖所有 el-table 表头行 */
.el-table thead th.el-table__cell {
  background-color: var(--el-color-primary) !important;
  color: #fff !important;
  font-weight: 600;
}
.el-table thead th.el-table__cell .cell,
.el-table thead th.el-table__cell .caret-wrapper,
.el-table thead th.el-table__cell .sort-caret {
  color: #fff !important;
}
.el-table thead th.el-table__cell .sort-caret.ascending { border-bottom-color: #fff !important; }
.el-table thead th.el-table__cell .sort-caret.descending { border-top-color: #fff !important; }
.el-table thead th.el-table__cell.is-sortable:hover {
  background-color: var(--el-color-primary-light-3) !important;
}
/* 表头 checkbox 在蓝底上仍清晰 */
.el-table thead th.el-table__cell .el-checkbox__inner {
  border-color: #fff;
  background-color: transparent;
}
.el-table thead th.el-table__cell .el-checkbox__input.is-checked .el-checkbox__inner,
.el-table thead th.el-table__cell .el-checkbox__input.is-indeterminate .el-checkbox__inner {
  background-color: #fff;
  border-color: #fff;
}
.el-table thead th.el-table__cell .el-checkbox__input.is-checked .el-checkbox__inner::after {
  border-color: var(--el-color-primary);
}
.el-table thead th.el-table__cell .el-checkbox__input.is-indeterminate .el-checkbox__inner::before {
  background-color: var(--el-color-primary);
}

/* 序号列（label 为空的排序列）：文字与排序箭头都居中 + 收紧内边距，避免看起来偏左 */
.el-table thead th.el-table__cell.is-center .cell {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding-left: 6px;
  padding-right: 6px;
}
.el-table thead th.el-table__cell.is-center .caret-wrapper {
  margin-left: 2px;
}
</style>
