import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css'
import * as ElIcons from '@element-plus/icons-vue'
import App from './App.vue'
import router from './router'
import { initTheme } from './composables/useTheme'

// 先定主题再挂载，避免首屏闪白
initTheme()

const app = createApp(App)
for (const [name, comp] of Object.entries(ElIcons)) {
  app.component(name, comp)
}
app.use(ElementPlus).use(router).mount('#app')
