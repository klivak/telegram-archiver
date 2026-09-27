import { createRouter, createWebHashHistory } from 'vue-router'

// Route-level code splitting keeps the startup bundle small (docs/17).
export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', name: 'dashboard', component: () => import('./views/DashboardView.vue') },
    { path: '/onboarding', name: 'onboarding', component: () => import('./views/OnboardingView.vue'), meta: { bare: true } },
    { path: '/chats', name: 'chats', component: () => import('./views/ChatsView.vue') },
    { path: '/chats/:id', name: 'chat', component: () => import('./views/ChatView.vue'), props: true },
    { path: '/downloads', name: 'downloads', component: () => import('./views/DownloadsView.vue') },
    { path: '/search', name: 'search', component: () => import('./views/SearchView.vue') },
    { path: '/miniapps', name: 'miniapps', component: () => import('./views/MiniAppsView.vue') },
    { path: '/monitor', name: 'monitor', component: () => import('./views/MonitorView.vue') },
    { path: '/settings', name: 'settings', component: () => import('./views/SettingsView.vue') },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})
