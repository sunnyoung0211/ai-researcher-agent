import { createApp } from 'vue'
import 'element-plus/dist/index.css'
import './style.css'

import App from './App.vue'
import router from './router'
import icons from './icons'

// Element Plus 组件由 vite.config.js 按需引入；语言包由 App.vue 的 el-config-provider 提供
const app = createApp(App)
app.use(icons)
app.use(router)
app.mount('#app')
