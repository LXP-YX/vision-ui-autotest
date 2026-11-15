import { ref } from 'vue'

// 主题偏好：'system' 跟随系统 | 'light' 明亮 | 'dark' 暗夜
const STORAGE_KEY = 'vua-theme'
const mode = ref('system')   // 用户选择
const isDark = ref(false)    // 实际是否为暗色（mode=system 时由系统决定）

let mediaQuery = null

// 依据偏好 + 系统设置解析是否暗色
function resolveDark(pref) {
  if (pref === 'dark') return true
  if (pref === 'light') return false
  return mediaQuery ? mediaQuery.matches : false
}

// 把结果写到 <html> 的 class 上，Element Plus 与自定义样式都靠 html.dark 切换
function apply() {
  const dark = resolveDark(mode.value)
  isDark.value = dark
  document.documentElement.classList.toggle('dark', dark)
}

// 应用启动时初始化：读取本地偏好、监听系统主题变化。须在 mount 前调用，避免闪烁
export function initTheme() {
  mediaQuery = window.matchMedia('(prefers-color-scheme: dark)')
  const saved = localStorage.getItem(STORAGE_KEY)
  mode.value = (saved === 'light' || saved === 'dark' || saved === 'system') ? saved : 'system'
  apply()
  // 仅当「跟随系统」时，系统切换才联动
  mediaQuery.addEventListener('change', () => {
    if (mode.value === 'system') apply()
  })
}

export function useTheme() {
  function setMode(m) {
    mode.value = m
    localStorage.setItem(STORAGE_KEY, m)
    apply()
  }
  return { mode, isDark, setMode }
}
