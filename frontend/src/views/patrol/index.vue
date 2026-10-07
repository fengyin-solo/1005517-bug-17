<template>
  <section class="page" data-module="patrol">
    <header class="page-head">
      <div>
        <h2>巡视检查管理</h2>
        <p class="page-desc">维护巡视记录，围绕记录编号、巡视区域、巡视日期、巡视人员做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记巡视记录</button>
        <button class="btn" type="button" @click="exportRows">导出巡视检查清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无巡视检查数据，可先登记巡视记录</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条巡视检查记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <!-- 储能电池组巡检清单：与判定同一口径，已停用的组也在册，条数与序号严格对齐 -->
    <div class="checklist-block">
      <div class="checklist-head">
        <h3>储能电池组巡检清单</h3>
        <span class="checklist-meta">共 {{ checklistTotal }} 组（含已停用 {{ disabledCount }} 组）</span>
        <button class="btn" type="button" @click="loadChecklist">刷新清单</button>
      </div>
      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in checklistColumns" :key="column">{{ column }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in checklist" :key="String(item.id)">
            <td v-for="column in checklistColumns" :key="column">
              <span v-if="column === '判定结论'" class="verdict" :class="verdictClass(item[column])">{{ item[column] ?? '—' }}</span>
              <template v-else>{{ item[column] ?? '—' }}</template>
            </td>
          </tr>
          <tr v-if="!checklist.length">
            <td :colspan="checklistColumns.length" class="empty-state">暂无储能电池组巡检项</td>
          </tr>
        </tbody>
      </table>
      <p v-if="checklistError" class="error-text">{{ checklistError }}</p>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/patrol'
const columns = ["记录编号", "巡视区域", "巡视日期", "巡视人员", "发现缺陷数", "红外测温结果", "接线端子温度", "巡视状态"]
const actions = ["开始巡视", "提交记录", "归档记录"]
const stats = [{ label: "今日巡视数", value: 0 }, { label: "发现缺陷数", value: 0 }, { label: "待巡视区域", value: 0 }]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

// 巡检清单走储能侧的统一口径接口，序号由后端连续给出，已停用的组同样在册
const CHECKLIST_ENDPOINT = '/api/energy_storage/patrol_checklist'
const checklistColumns = ["序号", "电池组编号", "电池类型", "SOC上限", "当前SOC", "内阻变化率", "判定结论", "已停用", "status"]
const checklist = ref<Row[]>([])
const checklistTotal = ref(0)
const checklistError = ref('')

const disabledCount = computed(
  () => checklist.value.filter((item) => item['已停用'] === '是' || item['status'] === '已停用').length,
)

function verdictClass(verdict: unknown): string {
  if (verdict === '过充风险') return 'verdict-danger'
  if (verdict === '内阻异常') return 'verdict-warn'
  if (verdict === '正常') return 'verdict-ok'
  return ''
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '巡视记录登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok || !payload || payload.ok === false) {
      throw new Error(payload?.message ? String(payload.message) : '巡视检查动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '巡视检查操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('巡视记录列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '巡视检查列表读取失败'
  }
}

async function loadChecklist() {
  checklistError.value = ''
  try {
    const response = await request(CHECKLIST_ENDPOINT)
    if (!response.ok) {
      throw new Error('储能电池组巡检清单读取失败')
    }
    const payload = await response.json()
    checklist.value = payload.items ?? []
    checklistTotal.value = payload.total ?? checklist.value.length
  } catch (error) {
    checklistError.value = error instanceof Error ? error.message : '储能电池组巡检清单读取失败'
  }
}

onMounted(() => {
  void reload()
  void loadChecklist()
})
</script>

<style scoped>
.checklist-block { margin-top: 24px; }
.checklist-head { display: flex; align-items: center; gap: 12px; margin-bottom: 8px; }
.checklist-head h3 { margin: 0; font-size: 15px; }
.checklist-meta { color: var(--muted); font-size: 12px; }
.verdict {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 10px;
  font-size: 12px;
}
.verdict-ok { background: #e7f6ec; color: #16733c; }
.verdict-warn { background: #fdf3e0; color: #b5570a; }
.verdict-danger { background: #fde8e8; color: #b42318; }
</style>
