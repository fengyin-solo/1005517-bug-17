<template>
  <section class="page" data-module="energy_storage">
    <header class="page-head">
      <div>
        <h2>储能电池组管理</h2>
        <p class="page-desc">维护储能电池组，围绕电池组编号、电池类型、额定容量、SOC上限做登记、检测判定与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记储能电池组</button>
        <button class="btn" type="button" @click="loadInspection">巡检清单</button>
        <button class="btn" type="button" @click="exportRows">导出储能电池组清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form v-if="assessTarget" class="filter-bar" @submit.prevent="submitAssess">
      <span>上报检测：{{ assessTarget.电池组编号 }}</span>
      <label class="filter-item">
        <span>当前SOC(%)</span>
        <input v-model="assessForm.当前SOC" placeholder="如 96.5" />
      </label>
      <label class="filter-item">
        <span>内阻变化率(%)</span>
        <input v-model="assessForm.内阻变化率" placeholder="如 12.3" />
      </label>
      <label class="filter-item">
        <span>检测日期</span>
        <input v-model="assessForm.检测日期" type="date" />
      </label>
      <button class="btn primary" type="submit">提交判定</button>
      <button class="btn ghost" type="button" @click="assessTarget = null">取消</button>
    </form>

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
            <button class="link" type="button" @click="openAssess(row)">上报检测</button>
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
          <td :colspan="columns.length + 1" class="empty-state">暂无储能电池组数据，可先登记储能电池组</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条储能电池组记录<span v-if="inspectionMode">（巡检清单，含故障停机）</span></span>
      <span v-if="noticeMessage" class="notice-text">{{ noticeMessage }}</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/energy_storage'
const columns = ["电池组编号", "电池类型", "额定容量", "SOC上限", "当前SOC", "内阻变化率", "结论", "运行状态"]
const actions = ["启动充电", "启动放电", "切换到待机"]
const statuses = ["充电中", "放电中", "待机", "故障停机"]

const rows = ref<Row[]>([])
const total = ref(0)
const stats = ref([{ label: 'SOC均值', value: '—' }, { label: '充放电组数', value: 0 }, { label: '故障组数', value: 0 }])
const errorMessage = ref('')
const noticeMessage = ref('')
const inspectionMode = ref(false)
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const assessTarget = ref<Row | null>(null)
const assessForm = ref({ 当前SOC: '', 内阻变化率: '', 检测日期: '' })

function parsePercent(value: unknown): number | null {
  const text = String(value ?? '').replace(/[%％\s]/g, '')
  if (!text) return null
  const num = Number(text)
  return Number.isFinite(num) ? num : null
}

function refreshStats() {
  const socs = rows.value.map((row) => parsePercent(row['当前SOC'])).filter((v): v is number => v !== null)
  const avg = socs.length ? `${(socs.reduce((a, b) => a + b, 0) / socs.length).toFixed(1)}%` : '—'
  stats.value = [
    { label: 'SOC均值', value: avg },
    { label: '充放电组数', value: rows.value.filter((row) => ['充电中', '放电中'].includes(String(row['运行状态'] ?? row.status))).length },
    { label: '故障组数', value: rows.value.filter((row) => String(row['运行状态'] ?? row.status) === '故障停机').length },
  ]
}

function resetFilters() {
  filters.value = {}
  inspectionMode.value = false
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '储能电池组登记入口尚未接入审批流'
}

function openAssess(row: Row) {
  errorMessage.value = ''
  noticeMessage.value = ''
  assessTarget.value = row
  assessForm.value = { 当前SOC: '', 内阻变化率: '', 检测日期: '' }
}

async function submitAssess() {
  if (!assessTarget.value) return
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/assessments`, {
      method: 'POST',
      body: JSON.stringify({
        values: {
          电池组编号: assessTarget.value['电池组编号'],
          当前SOC: assessForm.value.当前SOC,
          内阻变化率: assessForm.value.内阻变化率,
          检测日期: assessForm.value.检测日期,
        },
      }),
    })
    const payload = await response.json()
    if (!payload.ok) {
      errorMessage.value = payload.message || '检测结论未通过校验，已退回'
      return
    }
    noticeMessage.value = payload.message || '检测结论已落库'
    assessTarget.value = null
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '检测结论提交失败'
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const payload = await response.json()
    if (!payload.ok) {
      throw new Error(payload.message || '储能电池组动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '储能电池组操作失败'
  }
}

async function loadInspection() {
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/inspection`)
    if (!response.ok) {
      throw new Error('巡检清单读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    inspectionMode.value = true
    refreshStats()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '巡检清单读取失败'
  }
}

async function reload() {
  errorMessage.value = ''
  inspectionMode.value = false
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('储能电池组列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    refreshStats()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '储能电池组列表读取失败'
  }
}

onMounted(reload)
</script>
