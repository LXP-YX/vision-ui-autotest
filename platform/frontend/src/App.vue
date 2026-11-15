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
          <span>测试环境管理</span>
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

      <!-- 底部主题切换：跟随系统 / 明亮 / 暗夜 -->
      <div class="theme-switch">
        <span class="theme-label">
          <el-icon><Moon v-if="isDark" /><Sunny v-else /></el-icon>
          主题
        </span>
        <el-select v-model="themeMode" size="small" class="theme-select" @change="setMode">
          <el-option label="跟随系统" value="system" />
          <el-option label="明亮" value="light" />
          <el-option label="暗夜" value="dark" />
        </el-select>
      </div>
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
import { useTheme } from './composables/useTheme'

const route = useRoute()
const activeMenu = computed(() => route.path)
const currentTitle = computed(() => route.meta.title || 'vision-ui-autotest')

const { mode: themeMode, isDark, setMode } = useTheme()

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
/* 应用级语义变量：值来自 Element Plus 主题变量，html.dark 下自动切换为暗色取值。
   各视图统一引用这些变量，避免出现「暗色下仍是白底黑字」的残块。 */
:root {
  --app-content-bg: var(--el-bg-color-page);      /* 内容区背景 */
  --app-surface: var(--el-bg-color);              /* 卡片/面板底色 */
  --app-surface-sub: var(--el-fill-color-light);  /* 次级底色（日志、展开区） */
  --app-text: var(--el-text-color-primary);       /* 主文字 */
  --app-text-regular: var(--el-text-color-regular);
  --app-text-dim: var(--el-text-color-secondary);  /* 次要文字 */
  --app-text-faint: var(--el-text-color-placeholder);
  --app-border: var(--el-border-color-light);
  --app-border-strong: var(--el-border-color);
  --app-code-bg: var(--el-fill-color);            /* 行内代码底色 */

  --app-success-bg: var(--el-color-success-light-9);
  --app-success-border: var(--el-color-success-light-8);
  --app-success-text: var(--el-color-success);
  --app-warning-bg: var(--el-color-warning-light-9);
  --app-warning-border: var(--el-color-warning-light-8);
  --app-warning-text: var(--el-color-warning);
  --app-danger-bg: var(--el-color-danger-light-9);
  --app-danger-border: var(--el-color-danger-light-8);
  --app-danger-text: var(--el-color-danger);
  --app-primary-text: var(--el-color-primary);
}
/* 暗色下把状态色提亮一档，保证在深色底上有足够对比度 */
html.dark {
  --app-success-text: var(--el-color-success-dark-2);
  --app-danger-text: var(--el-color-danger-dark-2);
}

body { margin: 0; background: var(--app-content-bg); color: var(--app-text); }
.side { background: #1f2d3d; color: #fff; display: flex; flex-direction: column; }
.brand { height: 56px; line-height: 56px; text-align: center; font-size: 16px; font-weight: 600; color: #fff; border-bottom: 1px solid #2b3c50; }
.side-menu { border-right: none; background: transparent; flex: 1; }
.side-menu .el-menu-item { color: #c0ccda; }
.side-menu .el-menu-item.is-active { color: #fff; background: #263445; }
.side-menu .el-menu-item:hover { background: #263445; }
/* 侧边栏底部主题切换 */
.theme-switch { display: flex; align-items: center; gap: 8px; padding: 12px; border-top: 1px solid #2b3c50; }
.theme-label { display: inline-flex; align-items: center; gap: 4px; font-size: 12px; color: #c0ccda; white-space: nowrap; }
.theme-select { flex: 1; min-width: 0; }
/* 下拉框嵌在深色侧边栏里，用深色底 + 浅色字，两种主题下都与侧边栏融为一体 */
.theme-select .el-select__wrapper {
  background: #263445;
  box-shadow: 0 0 0 1px #2b3c50 inset;
}
.theme-select .el-select__selected-item { color: #c0ccda; }
.header { display: flex; align-items: baseline; gap: 16px; background: var(--app-surface); color: var(--app-text); border-bottom: 1px solid var(--app-border); }
.title { font-size: 18px; font-weight: 600; }
.sub { font-size: 12px; color: var(--app-text-dim); }
.content { padding: 12px; background: var(--app-content-bg); color: var(--app-text); overflow: auto; }

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
