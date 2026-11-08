import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 本地开发：/api 与 /ws 代理到后端 8077，避免 CORS 与 WS 跨域
export default defineConfig({
  plugins: [vue()],
  server: {
    host: '127.0.0.1',
    port: 5177,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8077', changeOrigin: true },
      '/ws': { target: 'ws://127.0.0.1:8077', ws: true },
    },
  },
})
