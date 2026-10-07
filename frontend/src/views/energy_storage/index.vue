<template>
  <section class="page" data-module="energy_storage">
    <header class="page-head">
      <div>
        <h2>储能电池组管理</h2>
        <p class="page-desc">判定口径统一：内阻变化率越出许可区间（-10%~20%）为内阻异常，实测 SOC 超过本组上限为过充风险，两条同时命中取更严重一档。列表与详情同取落库结论。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记储能电池组</button>
        <button class="btn" type="button" @click="exportRows">导出储能电池组清单</button>
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
          <th>判定/操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">
            <span v-if="column === '判定结论'" class="verdict" :class="verdictClass(row[column])">{{ row[column] ?? '—' }}</span>
            <template v-else>{{ row[column] ?? '—' }}</template>
          </td>
          <td class="row-actions">
            <button class="link" type="button" @click="openDetail(row)">详情</button>
            <button class="link" type="button" @click="openJudgment(row)">巡检判定</button>
            <button
              v-if="row['运行状态'] !== '已停用' && row['status'] !== '已停用'"
              class="link danger"
              type="button"
              @click="runAction('停用', row)"
            >
              停用
            </button>
            <button v-else class="link" type="button" @click="runAction('启用', row)">启用</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无储能电池组数据，可先登记储能电池组</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条储能电池组记录（判定记录缺数据时不予保存）</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <!-- 详情弹层：与列表取同一个接口出口，不再各算各的 -->
    <div v-if="detailRow" class="modal-mask" @click.self="closeDetail">
      <div class="modal">
        <div class="modal-head">
          <h3>电池组详情 · {{ detailRow['电池组编号'] }}</h3>
          <button class="btn ghost" type="button" @click="closeDetail">关闭</button>
        </div>
        <dl class="detail-grid">
          <template v-for="field in detailFields" :key="field">
            <dt>{{ field }}</dt>
            <dd>{{ detailRow[field] ?? '—' }}</dd>
          </template>
        </dl>
        <p class="modal-tip">判定结论由后端统一计算并落库，本页与列表显示完全一致。</p>
      </div>
    </div>

    <!-- 巡检判定弹层：缺数据 / 数据非法会逐字段退回 -->
    <div v-if="judgmentRow" class="modal-mask" @click.self="closeJudgment">
      <div class="modal">
        <div class="modal-head">
          <h3>巡检判定 · {{ judgmentRow['电池组编号'] }}</h3>
          <button class="btn ghost" type="button" @click="closeJudgment">关闭</button>
        </div>
        <form class="judgment-form" @submit.prevent="submitJudgment">
          <label>
            <span>额定容量（台账值，kWh）</span>
            <input v-model="judgmentForm['额定容量']" placeholder="如 500" />
          </label>
          <label>
            <span>SOC 上限（%）</span>
            <input v-model="judgmentForm['SOC上限']" placeholder="如 90" />
          </label>
          <label>
            <span>实测 SOC（%）</span>
            <input v-model="judgmentForm['当前SOC']" placeholder="如 92" />
          </label>
          <label>
            <span>内阻变化率（%，许可区间 -10~20）</span>
            <input v-model="judgmentForm['内阻变化率']" placeholder="如 4.2" />
          </label>
          <p class="form-hint">内阻越出许可区间判内阻异常；SOC 超过上限判过充风险；同时命中取更严重一档。同一份数据重复提交只生效一次；结论变化会自动写入检修计划待办。</p>
          <div class="modal-actions">
            <button class="btn primary" type="submit" :disabled="submitting">{{ submitting ? '判定中…' : '提交判定' }}</button>
            <span v-if="judgmentMessage" :class="judgmentOk ? 'success-text' : 'error-text'">{{ judgmentMessage }}</span>
          </div>
        </form>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/energy_storage'
const columns = ["电池组编号", "电池类型", "额定容量", "SOC上限", "当前SOC", "充放电循环", "电池温度", "内阻变化率", "判定结论", "运行状态"]
const detailFields = ["电池组编号", "电池类型", "额定容量", "SOC上限", "当前SOC", "充放电循环", "电池温度", "内阻变化率", "判定结论", "判定时间", "status"]
const filterFields = columns.slice(0, 3)

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})

const stats = computed(() => [
  { label: '电池组总数', value: total.value },
  { label: '内阻异常', value: rows.value.filter((row) => row['判定结论'] === '内阻异常').length },
  { label: '过充风险', value: rows.value.filter((row) => row['判定结论'] === '过充风险').length },
  { label: '已停用', value: rows.value.filter((row) => row['status'] === '已停用' || row['运行状态'] === '已停用').length },
])

const detailRow = ref<Row | null>(null)
const judgmentRow = ref<Row | null>(null)
const judgmentForm = ref<Record<string, string>>({})
const judgmentMessage = ref('')
const judgmentOk = ref(true)
const submitting = ref(false)

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
  errorMessage.value = '储能电池组登记入口尚未接入审批流'
}

async function openDetail(row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}`)
    if (!response.ok) {
      throw new Error('详情读取失败')
    }
    detailRow.value = await response.json()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '详情读取失败'
  }
}

function closeDetail() {
  detailRow.value = null
}

function openJudgment(row: Row) {
  judgmentRow.value = row
  judgmentMessage.value = ''
  judgmentOk.value = true
  judgmentForm.value = {
    '额定容量': String(row['额定容量'] ?? ''),
    'SOC上限': String(row['SOC上限'] ?? ''),
    '当前SOC': '',
    '内阻变化率': '',
  }
}

function closeJudgment() {
  judgmentRow.value = null
}

async function submitJudgment() {
  if (!judgmentRow.value) return
  submitting.value = true
  judgmentMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${judgmentRow.value.id}/judgment`, {
      method: 'POST',
      body: JSON.stringify({ values: judgmentForm.value }),
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok || !payload || payload.ok === false) {
      judgmentOk.value = false
      judgmentMessage.value = payload?.message ? String(payload.message) : '判定未生效，请检查提交数据'
      return
    }
    judgmentOk.value = true
    judgmentMessage.value = String(payload.message ?? '判定完成')
    await reload()
  } catch (error) {
    judgmentOk.value = false
    judgmentMessage.value = error instanceof Error ? error.message : '判定提交失败'
  } finally {
    submitting.value = false
  }
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
      throw new Error(payload?.message ? String(payload.message) : '储能电池组动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '储能电池组操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('储能电池组列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '储能电池组列表读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.verdict {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 10px;
  font-size: 12px;
}
.verdict-ok { background: #e7f6ec; color: #16733c; }
.verdict-warn { background: #fdf3e0; color: #b5570a; }
.verdict-danger { background: #fde8e8; color: #b42318; }
.link.danger { color: #b42318; }

.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(16, 24, 40, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 20;
}
.modal {
  width: 560px;
  max-width: calc(100vw - 32px);
  background: #fff;
  border-radius: 10px;
  padding: 16px 20px;
}
.modal-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}
.modal-head h3 { margin: 0; font-size: 16px; }
.detail-grid {
  display: grid;
  grid-template-columns: 130px 1fr;
  gap: 6px 12px;
  margin: 0;
  font-size: 13px;
}
.detail-grid dt { color: var(--muted); }
.detail-grid dd { margin: 0; }
.modal-tip, .form-hint { color: var(--muted); font-size: 12px; margin: 12px 0 0; }
.judgment-form label { display: block; margin-bottom: 10px; font-size: 12px; color: var(--muted); }
.judgment-form input {
  width: 100%;
  margin-top: 4px;
  padding: 6px 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 13px;
}
.modal-actions { display: flex; align-items: center; gap: 12px; margin-top: 8px; }
.success-text { color: #16733c; font-size: 12px; }
.error-text { font-size: 12px; }
</style>
