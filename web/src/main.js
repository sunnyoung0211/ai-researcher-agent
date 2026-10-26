import { createApp } from 'vue'
import 'element-plus/dist/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css'
import './style.css'

import App from './App.vue'
import router from './router'
import icons from './icons'
import { initTheme } from './utils/theme'

// Element Plus 组件由 vite.config.js 按需引入；语言包由 App.vue 的 el-config-provider 提供
initTheme()
const app = createApp(App)
app.use(icons)
app.use(router)
app.mount('#app')
