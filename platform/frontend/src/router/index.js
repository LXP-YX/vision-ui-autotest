import { createRouter, createWebHashHistory } from 'vue-router'
import CasesView from '../views/CasesView.vue'
import ExecutionsView from '../views/ExecutionsView.vue'
import LLMConfigView from '../views/LLMConfigView.vue'
import LocatorView from '../views/LocatorView.vue'
import RecordView from '../views/RecordView.vue'
import TestDataView from '../views/TestDataView.vue'
import ApiTestingView from '../views/ApiTestingView.vue'
import AppWorkbenchView from '../views/AppWorkbench.vue'
import AppEnvView from '../views/AppEnvView.vue'

const routes = [
  { path: '/', redirect: '/executions' },
  { path: '/executions', name: 'executions', component: ExecutionsView, meta: { title: '测试计划' } },
  { path: '/cases', name: 'cases', component: CasesView, meta: { title: '用例管理' } },
  { path: '/app-workbench', name: 'app-workbench', component: AppWorkbenchView, meta: { title: 'APP 工作台' } },
  { path: '/app-env', name: 'app-env', component: AppEnvView, meta: { title: 'APP 测试环境' } },
  { path: '/record', name: 'record', component: RecordView, meta: { title: '录制用例' } },
  { path: '/api-testing', name: 'api-testing', component: ApiTestingView, meta: { title: '接口管理' } },
  { path: '/test-data', name: 'test-data', component: TestDataView, meta: { title: '测试环境管理' } },
  { path: '/llm', name: 'llm', component: LLMConfigView, meta: { title: '大模型配置' } },
  { path: '/locators', name: 'locators', component: LocatorView, meta: { title: '自愈知识库' } },
]

export default createRouter({
  history: createWebHashHistory(),
  routes,
})
