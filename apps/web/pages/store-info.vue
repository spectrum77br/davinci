<script setup lang="ts">
import { TABS_CADASTROS } from '~/lib/navGroups'
import { Plus, RefreshCw, Trash2, X, Check, Loader2, Eye, EyeOff, Copy, ExternalLink, AlertCircle, Unlink, Link2, Archive, ArchiveRestore } from 'lucide-vue-next'

definePageMeta({
  // `/store-info.` (trailing period) shows up consistently in prod logs —
  // browsers/copy-paste tend to inherit the period from surrounding text.
  // Without an alias, Nuxt 404s and the error page itself crashes (Pinia
  // SSR hydration bug, separate issue), surfacing as a hard 500.
  alias: ['/store-info.', '/store-info/'],
  middleware: ['permission'],
  permission: { resource: 'lojas_info', action: 'view' },
})

// Regra de exceção de envio da loja (campo "Exceções") — o sweep automático
// de NF bloqueia o pedido que casa (Aguardando Cancelamento + "restrição").
// A UF vem do campo "Restrição" da loja (uf_restrictions).
type StoreExcecao = {
  tipo: 'valor' | 'sku' | 'palavra'
  valor?: number | null
  termos?: string[] | null
}

type StoreInfo = {
  id: string
  user_id: string
  platform: string
  segment: string | null
  freight: string | null
  cpf_name: string | null
  account_name: string | null
  server: string | null
  cnpj: string | null
  email: string | null
  observation: string | null
  shipping_address: string | null
  return_address: string | null
  phone: string | null
  link: string | null
  integration_id: string | null
  sort_order: number
  has_password: boolean
  departments: string[]
  has_pricing: boolean
  has_integration: boolean
  bling_store_id: string | null
  upseseller: boolean | null
  duoker: boolean | null
  uf_restrictions: string[] | null
  excecoes: StoreExcecao[] | null
  sales_team: number | null
  nf_faturador_id: string | null
  nf_etiqueta_id: string | null
  etiqueta_horarios: string | null
  nf_impressao_id: string | null
  archived_at: string | null
  created_at: string
  updated_at: string
}

type NfRef = { id: string; label: string }

const UF_OPTIONS = [
  'AC','AL','AM','AP','BA','CE','DF','ES','GO','MA','MG','MS','MT',
  'PA','PB','PE','PI','PR','RJ','RN','RO','RR','RS','SC','SE','SP','TO',
]

// Tipo badges all share the neutral palette — colors per dept were too noisy.
const DEPT_BADGE_CLS = 'bg-muted text-muted-foreground border-border'
const DEPT_BADGE: Record<string, { label: string; cls: string }> = {
  celular:  { label: 'Cel',      cls: DEPT_BADGE_CLS },
  mala:     { label: 'Mala',     cls: DEPT_BADGE_CLS },
  eletro:   { label: 'Eletro',   cls: DEPT_BADGE_CLS },
  catalogo: { label: 'Catálogo', cls: DEPT_BADGE_CLS },
  shein:    { label: 'Shein',    cls: DEPT_BADGE_CLS },
}

type IntegrationRef = {
  id: string
  platform: string
  name: string
  store_id: string | null
}

const STORE_INFO_TO_INTEGRATION_PLATFORM: Record<string, string> = {
  mercadolivre: 'ml',
  ml: 'ml',
  shopee: 'shopee',
  amazon: 'amazon',
  tiktok: 'tiktok',
  temu: 'temu',
  shein: 'shein',
}

const PLATFORM_ALIASES: Record<string, string> = {
  ml: 'mercadolivre',
  mercadolivre: 'mercadolivre',
}

function normPlatform(p: string | null | undefined): string {
  if (!p) return ''
  const lower = p.toLowerCase()
  return PLATFORM_ALIASES[lower] ?? lower
}

const PLATFORMS = [
  { value: 'mercadolivre', label: 'ML' },
  { value: 'shopee', label: 'Shopee' },
  { value: 'amazon', label: 'Amazon' },
  { value: 'aliexpress', label: 'AliExpress' },
  { value: 'temu', label: 'Temu' },
  { value: 'tiktok', label: 'TikTok' },
  { value: 'magalu', label: 'Magalu' },
  { value: 'shein', label: 'Shein' },
]

function platformLabel(p: string) {
  return PLATFORMS.find((x) => x.value === normPlatform(p))?.label ?? p
}

const DEPARTMENTS = [
  { value: 'celular', label: 'Celular' },
  { value: 'mala', label: 'Mala' },
  { value: 'eletro', label: 'Eletro' },
  { value: 'catalogo', label: 'Catálogo' },
  { value: 'shein', label: 'Shein' },
]

const { api } = useApi()
// Lojas é SEPARADO de Tabela de Preços (decisão 13/ago): a tela obedece só a
// permissão "Lojas (info)" — quem tem tabela_precos mas não lojas_info NÃO
// edita aqui. O backend espelha (require_permission("lojas_info") nos
// endpoints /pricing/store-info*).
const canEdit = useCan('lojas_info', 'edit')
const canDelete = useCan('lojas_info', 'delete')

const items = ref<StoreInfo[]>([])
const integrations = ref<IntegrationRef[]>([])
// Cadastros de NF (admin-only). Não-admin recebe 403 → lista vazia (dropdowns
// ficam sem opção, o que é ok: quem gerencia NF é admin).
const faturadores = ref<NfRef[]>([])
const etiquetas = ref<NfRef[]>([])
const impressoes = ref<NfRef[]>([])
const loading = ref(false)
const error = ref<string | null>(null)
const filterPlatform = ref<string>('all')
const search = ref('')
// false = Lojas ativas (default); true = aba "Arquivadas" (contas suspensas
// tiradas de circulação, com botão "Ativar" pra reverter).
const archivedView = ref(false)

async function load() {
  loading.value = true
  error.value = null
  try {
    const q = archivedView.value ? '?archived=true' : ''
    const [storeInfo, integs, fats, etqs, imps] = await Promise.all([
      api<StoreInfo[]>(`/api/pricing/store-info${q}`),
      api<IntegrationRef[]>('/api/integrations').catch(() => [] as IntegrationRef[]),
      api<any[]>('/api/nf-cadastro/faturadores').catch(() => [] as any[]),
      api<any[]>('/api/nf-cadastro/etiquetas').catch(() => [] as any[]),
      api<any[]>('/api/nf-cadastro/impressoes').catch(() => [] as any[]),
    ])
    items.value = storeInfo
    integrations.value = integs
    faturadores.value = fats.map((f) => ({ id: f.id, label: f.nome }))
    etiquetas.value = etqs.map((e) => ({ id: e.id, label: e.plataforma }))
    impressoes.value = imps.map((i) => ({ id: i.id, label: i.tipo }))
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    loading.value = false
  }
}

await load()

function integrationFor(row: StoreInfo): IntegrationRef | null {
  if (!row.integration_id) return null
  return integrations.value.find((i) => i.id === row.integration_id) || null
}

function nfLabel(list: NfRef[], id: string | null): string {
  if (!id) return '—'
  return list.find((x) => x.id === id)?.label ?? '—'
}

function availableIntegrationsFor(row: StoreInfo): IntegrationRef[] {
  const plat = STORE_INFO_TO_INTEGRATION_PLATFORM[normPlatform(row.platform)]
  if (!plat) return []
  return integrations.value.filter((i) => i.platform === plat)
}

async function attachIntegration(row: StoreInfo, integrationId: string) {
  if (!integrationId || integrationId === row.integration_id) return
  try {
    const updated = await api<StoreInfo>(`/api/pricing/store-info/${row.id}`, {
      method: 'PATCH',
      body: { integration_id: integrationId },
    })
    Object.assign(row, updated)
    flash(row.id, 'account_name')
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  }
}

async function unlinkIntegration(row: StoreInfo) {
  try {
    const updated = await api<StoreInfo>(`/api/pricing/store-info/${row.id}`, {
      method: 'PATCH',
      body: { integration_id: null },
    })
    Object.assign(row, updated)
    flash(row.id, 'account_name')
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  }
}

// Generic single-field PATCH used by the new Bling ID / UpseSeller / Duoker
// / UF cells.
async function updateField(row: StoreInfo, field: keyof StoreInfo, value: unknown) {
  try {
    const body: Record<string, unknown> = { [field]: value }
    const updated = await api<StoreInfo>(`/api/pricing/store-info/${row.id}`, {
      method: 'PATCH',
      body,
    })
    Object.assign(row, updated)
    flash(row.id, String(field))
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  }
}

function boolFromSelect(v: string): boolean | null {
  if (v === 'sim') return true
  if (v === 'nao') return false
  return null
}

// UF multi-select popover state: tracks which row's popover is open.
const openUfRowId = ref<string | null>(null)
function toggleUfPopover(id: string) {
  openUfRowId.value = openUfRowId.value === id ? null : id
}
async function toggleUf(row: StoreInfo, uf: string, checked: boolean) {
  const current = new Set(row.uf_restrictions || [])
  if (checked) current.add(uf)
  else current.delete(uf)
  const next = Array.from(current).sort()
  await updateField(row, 'uf_restrictions', next.length ? next : null)
}

// Tri-state boolean badge helpers (UpseSeller / Duoker). Mirrors the
// "Sim / ✕ Não / —" visual that Tab.Preço and Integração use, plus an
// extra null state so the field stays "unset" until the user picks one.
function labelTriBool(v: boolean | null): string {
  if (v === true) return 'Sim'
  if (v === false) return '✕ Não'
  return '—'
}
function badgeClassTriBool(v: boolean | null): string {
  if (v === true) return 'bg-emerald-500/15 text-emerald-400 border-emerald-500/40'
  if (v === false) return 'bg-red-500/15 text-red-400 border-red-500/40'
  return 'bg-muted/40 text-muted-foreground border-border'
}
async function cycleTriBool(row: StoreInfo, field: 'upseseller' | 'duoker') {
  const cur = row[field]
  const next: boolean | null = cur === null ? true : cur === true ? false : null
  await updateField(row, field, next)
}

// Computed for the UF popover overlay so the template can read the
// currently-open row reactively.
const openUfRow = computed(() =>
  openUfRowId.value ? items.value.find((r) => r.id === openUfRowId.value) || null : null
)

// Horário Etiqueta — mesmo padrão do popover de UF. O horário é escolhido no
// relógio do `input type=time` (BRT); nenhum horário na lista = contínuo
// (imprime quando a NF fecha).
const novoHorario = ref('')
const openHorRowId = ref<string | null>(null)
function toggleHorPopover(id: string) {
  openHorRowId.value = openHorRowId.value === id ? null : id
}
const openHorRow = computed(() =>
  openHorRowId.value ? items.value.find((r) => r.id === openHorRowId.value) || null : null
)
function horariosDe(row: StoreInfo): string[] {
  return (row.etiqueta_horarios || '').split(',').map((s) => s.trim()).filter(Boolean)
}
async function setHorarios(row: StoreInfo, lista: string[]) {
  const next = Array.from(new Set(lista)).sort()
  await updateField(row, 'etiqueta_horarios', next.length ? next.join(', ') : null)
}
async function addHorario(row: StoreInfo) {
  if (!novoHorario.value) return
  await setHorarios(row, [...horariosDe(row), novoHorario.value])
  novoHorario.value = ''
}
async function removeHorario(row: StoreInfo, hhmm: string) {
  await setHorarios(row, horariosDe(row).filter((h) => h !== hhmm))
}

// Exceções popover — mirrors the UF popover pattern (overlay outside the
// table). Each add/remove PATCHes `excecoes` via updateField.
const openExcRowId = ref<string | null>(null)
function toggleExcPopover(id: string) {
  openExcRowId.value = openExcRowId.value === id ? null : id
}
const openExcRow = computed(() =>
  openExcRowId.value ? items.value.find((r) => r.id === openExcRowId.value) || null : null
)
const excDraft = reactive({
  tipo: 'valor' as StoreExcecao['tipo'],
  valor: '',
  termos: '',
})
function excecaoLabel(r: StoreExcecao): string {
  if (r.tipo === 'valor') {
    const v = Number(r.valor ?? 0)
    return `valor ≥ R$ ${v.toLocaleString('pt-BR', { minimumFractionDigits: 2 })}`
  }
  const termos = (r.termos || []).join(', ')
  return r.tipo === 'sku' ? `SKU ${termos}` : `nome contém ${termos}`
}
async function addExcecao(row: StoreInfo) {
  let regra: StoreExcecao
  if (excDraft.tipo === 'valor') {
    const v = Number(String(excDraft.valor).replace(/\./g, '').replace(',', '.'))
    if (!Number.isFinite(v) || v <= 0) return
    regra = { tipo: 'valor', valor: v }
  } else {
    const termos = excDraft.termos.split(',').map((t) => t.trim()).filter(Boolean)
    if (!termos.length) return
    regra = { tipo: excDraft.tipo, termos }
  }
  await updateField(row, 'excecoes', [...(row.excecoes || []), regra])
  excDraft.valor = ''
  excDraft.termos = ''
}
async function removeExcecao(row: StoreInfo, idx: number) {
  const next = (row.excecoes || []).filter((_, i) => i !== idx)
  await updateField(row, 'excecoes', next.length ? next : null)
}

const sorted = computed(() => {
  let list = [...items.value]
  if (filterPlatform.value !== 'all') {
    const want = normPlatform(filterPlatform.value)
    list = list.filter((s) => normPlatform(s.platform) === want)
  }
  const q = search.value.trim().toLowerCase()
  if (q) {
    list = list.filter(
      (s) =>
        (s.account_name || '').toLowerCase().includes(q) ||
        (s.cpf_name || '').toLowerCase().includes(q) ||
        (s.email || '').toLowerCase().includes(q) ||
        (s.cnpj || '').toLowerCase().includes(q) ||
        s.platform.toLowerCase().includes(q),
    )
  }
  return list
})

// Grouped by platform (alphabetical platform order, alphabetical account_name
// within each group). Mirrors the SSH "Lojas" view with platform headers.
const groups = computed(() => {
  const map = new Map<string, StoreInfo[]>()
  for (const r of sorted.value) {
    const key = normPlatform(r.platform)
    if (!map.has(key)) map.set(key, [])
    map.get(key)!.push(r)
  }
  for (const arr of map.values()) {
    arr.sort((a, b) =>
      (a.account_name || '').localeCompare(b.account_name || '', 'pt-BR', { sensitivity: 'base' })
    )
  }
  return Array.from(map.keys())
    .sort()
    .map((platform) => ({
      platform,
      count: map.get(platform)!.length,
      rows: map.get(platform)!,
    }))
})

// =========================================================== inline edit

const editing = ref<{ id: string; field: string } | null>(null)
const editValue = ref<string>('')
const editOriginal = ref<string>('')
const editInputRef = ref<HTMLInputElement | HTMLSelectElement | null>(null)
function setEditInputRef(el: any) {
  if (el) editInputRef.value = el
}
const flashed = ref<Set<string>>(new Set())

function isEditing(id: string, field: string) {
  return editing.value?.id === id && editing.value?.field === field
}

function isFlashed(id: string, field: string) {
  return flashed.value.has(`${id}::${field}`)
}

function flash(id: string, field: string) {
  const k = `${id}::${field}`
  flashed.value.add(k)
  setTimeout(() => flashed.value.delete(k), 1200)
}

// Equipe no formato empresa.membro: o int codificado E*100+M vira "2.1"
// (display) e "2.1" digitado vira 201 (gravado). O número cru é a chave.
function teamLabel(n: number) {
  return `${Math.floor(n / 100)}.${n % 100}`
}
function parseTeam(raw: unknown): number | null {
  const s = String(raw ?? '').trim()
  const m = s.match(/^(\d+)\.(\d+)$/)
  if (!m) return null
  const e = parseInt(m[1], 10)
  const mem = parseInt(m[2], 10)
  if (e < 1 || mem < 1 || mem > 99) return null
  return e * 100 + mem
}

async function startEdit(row: StoreInfo, field: string) {
  if (!canEdit.value) return
  editing.value = { id: row.id, field }
  let initial: string
  if (field === 'password') {
    // Revela a senha real ANTES de editar. Sem isso, o campo abria vazio
    // (a máscara não é a senha) e parecia que a senha tinha sumido ao
    // clicar nos pontos — e um blur poderia salvar em branco.
    if (row.has_password && !revealedPasswords.value.has(row.id)) {
      try {
        const r = await api<{ password: string }>(`/api/pricing/store-info/${row.id}/password`)
        revealedPasswords.value.set(row.id, r.password)
        revealed.value.add(row.id)
      } catch { /* mantém vazio se falhar */ }
    }
    initial = revealedPasswords.value.get(row.id) ?? ''
  } else if (field === 'sales_team') {
    // Edita no formato "empresa.membro" (ex.: 2.1), não o int cru.
    initial = row.sales_team == null ? '' : teamLabel(row.sales_team)
  } else {
    const raw = (row as any)[field]
    initial = raw == null ? '' : String(raw)
  }
  editValue.value = initial
  editOriginal.value = initial
  await nextTick()
  const el = editInputRef.value
  if (el) {
    el.focus()
    if ('select' in el) (el as HTMLInputElement).select?.()
  }
}

function cancelEdit() {
  editing.value = null
  editValue.value = ''
  editOriginal.value = ''
}

async function commitEdit() {
  if (!editing.value) return
  const { id, field } = editing.value
  const row = items.value.find((x) => x.id === id)
  if (!row) return cancelEdit()

  if (editValue.value === editOriginal.value) {
    return cancelEdit()
  }

  const raw = String(editValue.value ?? '').trim()
  const payload: Record<string, unknown> = {}

  if (field === 'platform') {
    if (!raw) return cancelEdit()
    payload.platform = raw
  } else if (field === 'sort_order') {
    const n = parseInt(raw)
    if (Number.isNaN(n)) return cancelEdit()
    payload.sort_order = n
  } else if (field === 'sales_team') {
    // Vazio limpa a equipe; senão exige "empresa.membro" (ex.: 2.1).
    if (!raw) {
      payload.sales_team = null
    } else {
      const n = parseTeam(raw)
      if (n == null) return cancelEdit()
      payload.sales_team = n
    }
  } else if (field === 'password') {
    payload.password = raw || null
  } else {
    payload[field] = raw || null
  }

  try {
    const updated = await api<StoreInfo>(`/api/pricing/store-info/${id}`, {
      method: 'PATCH',
      body: payload,
    })
    Object.assign(row, updated)
    flash(id, field)
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    cancelEdit()
  }
}

// =========================================================== add row

const showAdd = ref(false)
const newRow = reactive({ platform: 'mercadolivre', account_name: '' })
const adding = ref(false)

function openAdd() {
  newRow.platform = 'mercadolivre'
  newRow.account_name = ''
  showAdd.value = true
  nextTick(() => {
    const el = document.getElementById('new-store-account') as HTMLInputElement | null
    el?.focus()
  })
}

async function submitNew() {
  if (!newRow.platform) return
  adding.value = true
  try {
    const body: Record<string, unknown> = { platform: newRow.platform }
    if (newRow.account_name) body.account_name = newRow.account_name
    const created = await api<StoreInfo>('/api/pricing/store-info', {
      method: 'POST',
      body,
    })
    items.value.push(created)
    showAdd.value = false
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    adding.value = false
  }
}

// =========================================================== delete

async function remove(row: StoreInfo) {
  if (!confirm(`Excluir loja "${row.account_name || row.platform}"?`)) return
  try {
    await api(`/api/pricing/store-info/${row.id}`, { method: 'DELETE' })
    items.value = items.value.filter((x) => x.id !== row.id)
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  }
}

// =========================================================== archive

// Arquivar uma conta suspensa: some de Lojas, Tabela de Preço e Produtos, e o
// sync para de mirá-la. A integração vinculada é arquivada junto. Reversível
// pelo botão "Ativar" na aba Arquivadas.
const archiveBusy = ref<Set<string>>(new Set())

async function archiveRow(row: StoreInfo) {
  if (!confirm(`Arquivar "${row.account_name || row.platform}"? Ela sai de Lojas, Tabela de Preço e Produtos, e o sync para de empurrar estoque/preço. Reversível em "Arquivadas".`)) return
  archiveBusy.value.add(row.id)
  try {
    await api(`/api/pricing/store-info/${row.id}/archive`, { method: 'POST' })
    items.value = items.value.filter((x) => x.id !== row.id)
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    archiveBusy.value.delete(row.id)
  }
}

async function unarchiveRow(row: StoreInfo) {
  archiveBusy.value.add(row.id)
  try {
    await api(`/api/pricing/store-info/${row.id}/unarchive`, { method: 'POST' })
    items.value = items.value.filter((x) => x.id !== row.id)
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    archiveBusy.value.delete(row.id)
  }
}

function toggleArchivedView() {
  archivedView.value = !archivedView.value
  load()
}

// =========================================================== bind dept

const TIPO_OPTIONS = [
  { slug: 'celular',  label: 'Celular' },
  { slug: 'mala',     label: 'Mala' },
  { slug: 'eletro',   label: 'Eletro' },
  { slug: 'catalogo', label: 'Catálogo' },
] as const

const tipoPopoverFor = ref<string | null>(null)
const tipoBusy = ref<Set<string>>(new Set())

function openTipoPopover(rowId: string) {
  if (!canEdit.value) return
  tipoPopoverFor.value = rowId === tipoPopoverFor.value ? null : rowId
}

// Close the Tipo popover on any click outside the popover/cell.
const onDocClick = () => { tipoPopoverFor.value = null }
onMounted(() => document.addEventListener('click', onDocClick))
onBeforeUnmount(() => document.removeEventListener('click', onDocClick))

async function toggleDepartment(row: StoreInfo, slug: string, checked: boolean) {
  const key = `${row.id}:${slug}`
  if (tipoBusy.value.has(key)) return
  tipoBusy.value.add(key)
  try {
    if (checked) {
      await api(`/api/pricing/store-info/${row.id}/department`, {
        method: 'POST',
        body: { department: slug },
      })
    } else {
      await api(
        `/api/pricing/store-info/${row.id}/department/${encodeURIComponent(slug)}`,
        { method: 'DELETE' },
      )
    }
    flash(row.id, 'department')
    await load()
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    tipoBusy.value.delete(key)
  }
}

// =========================================================== password reveal

const revealed = ref<Set<string>>(new Set())
const revealedPasswords = ref<Map<string, string>>(new Map())
async function toggleReveal(id: string) {
  if (revealed.value.has(id)) {
    revealed.value.delete(id)
    revealedPasswords.value.delete(id)
    return
  }
  try {
    const r = await api<{ password: string }>(`/api/pricing/store-info/${id}/password`)
    revealedPasswords.value.set(id, r.password)
    revealed.value.add(id)
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  }
}

const copiedId = ref<string | null>(null)
async function copyPassword(id: string) {
  let pw = revealedPasswords.value.get(id)
  if (pw == null) {
    try {
      const r = await api<{ password: string }>(`/api/pricing/store-info/${id}/password`)
      pw = r.password
      revealedPasswords.value.set(id, pw)
    } catch (e: any) {
      error.value = e?.data?.detail?.code || e?.message || 'erro'
      return
    }
  }
  try {
    await navigator.clipboard.writeText(pw ?? '')
    copiedId.value = id
    setTimeout(() => { if (copiedId.value === id) copiedId.value = null }, 1200)
  } catch { /* clipboard indisponível */ }
}

async function copyText(text: string) {
  await navigator.clipboard.writeText(text)
}
</script>

<template>
  <div class="space-y-4">
    <RouteTabs :tabs="TABS_CADASTROS" />
    <PageHeader
      title="Lojas"
      description="Cadastros completos das lojas — clique em qualquer célula para editar"
    >
      <template #actions>
        <select v-model="filterPlatform" class="border rounded px-2 py-1 text-sm bg-background">
          <option value="all">Todas plataformas</option>
          <option v-for="p in PLATFORMS" :key="p.value" :value="p.value">{{ p.label }}</option>
        </select>
        <input
          v-model="search"
          placeholder="buscar conta, CPF, e-mail, CNPJ…"
          class="border rounded px-2 py-1 text-sm bg-background w-64"
        />
        <Button size="sm" variant="ghost" :disabled="loading" @click="load">
          <RefreshCw class="size-4 mr-1" :class="{ 'animate-spin': loading }" /> recarregar
        </Button>
        <Button
          size="sm"
          :variant="archivedView ? 'default' : 'ghost'"
          :disabled="loading"
          @click="toggleArchivedView"
        >
          <Archive class="size-4 mr-1" /> {{ archivedView ? 'Ativas' : 'Arquivadas' }}
        </Button>
        <Button v-if="canEdit && !archivedView" size="sm" :disabled="showAdd" @click="openAdd">
          <Plus class="size-4 mr-1" /> Nova loja
        </Button>
      </template>
    </PageHeader>

    <div v-if="error" class="rounded border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive flex items-center gap-2">
      <AlertCircle class="h-4 w-4" /> {{ error }}
    </div>

    <div class="border rounded-lg overflow-auto max-h-[calc(100vh-220px)]">
      <table class="w-full text-sm border-collapse">
        <thead class="sticky top-0 bg-muted z-10">
          <tr>
            <th class="text-left px-2 py-2 font-medium border-b border-border min-w-[110px]">Plataforma</th>
            <th class="text-left px-2 py-2 font-medium border-b border-border min-w-[120px]">Conta</th>
            <th class="text-left px-2 py-2 font-medium border-b border-border min-w-[120px]">Faturador</th>
            <th class="text-left px-2 py-2 font-medium border-b border-border min-w-[110px]">Etiqueta</th>
            <th class="text-left px-2 py-2 font-medium border-b border-border min-w-[120px]" title="Horários (BRT) em que as etiquetas desta loja são impressas. Vazio = contínuo.">Horário Etiqueta</th>
            <th class="text-left px-2 py-2 font-medium border-b border-border min-w-[110px]">Impressão</th>
            <th class="text-left px-2 py-2 font-medium border-b border-border min-w-[140px]">Responsável</th>
            <th class="text-center px-2 py-2 font-medium border-b border-border w-20">Equipe</th>
            <th class="text-left px-2 py-2 font-medium border-b border-border min-w-[80px]">Servidor</th>
            <th class="text-left px-2 py-2 font-medium border-b border-border min-w-[140px]">CNPJ</th>
            <th class="text-left px-2 py-2 font-medium border-b border-border min-w-[180px]">E-mail</th>
            <th class="text-left px-2 py-2 font-medium border-b border-border min-w-[120px]">Fone</th>
            <th class="text-left px-2 py-2 font-medium border-b border-border min-w-[120px]">Senha</th>
            <th class="text-left px-2 py-2 font-medium border-b border-border w-32">End. Envio</th>
            <th class="text-left px-2 py-2 font-medium border-b border-border w-32">End. Dev.</th>
            <th class="text-left px-2 py-2 font-medium border-b border-border min-w-[160px]">Obs</th>
            <th class="text-center px-2 py-2 font-medium border-b border-border min-w-[110px]">Tipo</th>
            <th class="text-center px-2 py-2 font-medium border-b border-border w-20">Tab. Preço</th>
            <th class="text-center px-2 py-2 font-medium border-b border-border w-20">Integração</th>
            <th class="text-left px-2 py-2 font-medium border-b border-border min-w-[110px]">Bling ID</th>
            <th class="text-center px-2 py-2 font-medium border-b border-border w-20">Upseller</th>
            <th class="text-center px-2 py-2 font-medium border-b border-border w-20">Duoke</th>
            <th class="text-left px-2 py-2 font-medium border-b border-border min-w-[140px]">Restrição</th>
            <th class="text-center px-2 py-2 font-medium border-b border-border min-w-[100px]">Exceções</th>
            <th class="text-center px-2 py-2 font-medium border-b border-border w-12"></th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading && !items.length">
            <td colSpan="19" class="text-center py-6 text-muted-foreground">
              <Loader2 class="inline h-4 w-4 animate-spin" /> carregando…
            </td>
          </tr>
          <tr v-else-if="!sorted.length && !showAdd">
            <td colSpan="19" class="text-center py-8 text-muted-foreground">Nenhuma loja cadastrada.</td>
          </tr>

          <!-- add row -->
          <tr v-if="showAdd" class="bg-blue-50/40 dark:bg-blue-900/10">
            <td class="border border-border px-1 py-1">
              <select v-model="newRow.platform" class="w-full text-xs border rounded px-1 py-1 bg-background">
                <option v-for="p in PLATFORMS" :key="p.value" :value="p.value">{{ p.label }}</option>
              </select>
            </td>
            <td class="border border-border px-1 py-1">
              <input
                id="new-store-account"
                v-model="newRow.account_name"
                type="text" placeholder="Nome da conta"
                class="w-full text-xs border rounded px-1.5 py-1 bg-background"
                @keydown.enter="submitNew"
                @keydown.escape="showAdd = false"
              />
            </td>
            <td v-for="i in 16" :key="i" class="border border-border text-center text-xs text-muted-foreground">—</td>
            <td class="border border-border px-1 py-1 text-center">
              <div class="flex gap-0.5 justify-center">
                <button class="p-1 text-emerald-600 hover:bg-emerald-50 rounded" :disabled="adding" @click="submitNew">
                  <Loader2 v-if="adding" class="h-3 w-3 animate-spin" />
                  <Check v-else class="h-3 w-3" />
                </button>
                <button class="p-1 text-destructive hover:bg-destructive/10 rounded" @click="showAdd = false">
                  <X class="h-3 w-3" />
                </button>
              </div>
            </td>
          </tr>

          <!-- platform groups: header + rows -->
          <template v-for="group in groups" :key="group.platform">
            <tr class="bg-muted/60">
              <td colSpan="18" class="px-3 py-2 text-xs font-bold uppercase tracking-wide text-foreground/80 border-b border-border">
                {{ group.platform }}
                <span class="font-normal text-muted-foreground normal-case">
                  {{ group.count }} conta{{ group.count > 1 ? 's' : '' }}
                </span>
              </td>
            </tr>
          <tr v-for="row in group.rows" :key="row.id" class="hover:bg-accent/30">
            <!-- platform -->
            <td
              class="border border-border px-2 py-1.5 text-xs cursor-pointer"
              :class="{
                'ring-2 ring-blue-500 ring-inset bg-background': isEditing(row.id, 'platform'),
                'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(row.id, 'platform'),
              }"
              @click="!isEditing(row.id, 'platform') && startEdit(row, 'platform')"
            >
              <select
                v-if="isEditing(row.id, 'platform')"
                :ref="setEditInputRef"
                v-model="editValue"
                class="w-full text-xs bg-transparent outline-none"
                @blur="commitEdit" @change="commitEdit" @keydown.escape.prevent="cancelEdit"
              >
                <option v-for="p in PLATFORMS" :key="p.value" :value="p.value">{{ p.label }}</option>
              </select>
              <span v-else>{{ platformLabel(row.platform) }}</span>
            </td>
            <!-- account_name + integration link -->
            <td
              class="border border-border px-2 py-1.5 text-xs"
              :class="{
                'ring-2 ring-blue-500 ring-inset bg-background': isEditing(row.id, 'account_name'),
                'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(row.id, 'account_name'),
              }"
            >
              <input
                v-if="isEditing(row.id, 'account_name')"
                :ref="setEditInputRef"
                v-model="editValue" type="text"
                class="w-full text-xs bg-transparent outline-none"
                @blur="commitEdit" @keydown.enter.prevent="commitEdit" @keydown.escape.prevent="cancelEdit"
              />
              <div v-else class="flex items-center gap-1 group min-w-0">
                <span
                  class="cursor-pointer truncate flex-1"
                  :class="{ 'text-muted-foreground': !row.account_name }"
                  :title="row.account_name || ''"
                  @click="startEdit(row, 'account_name')"
                >
                  {{ row.account_name || '—' }}
                </span>
                <button
                  v-if="row.integration_id && canEdit"
                  class="opacity-0 group-hover:opacity-100 p-0.5 hover:bg-muted rounded shrink-0"
                  :title="integrationFor(row) ? `Desvincular ${integrationFor(row)!.name}` : 'Desvincular integração'"
                  @click.stop="unlinkIntegration(row)"
                >
                  <Unlink class="h-3 w-3" />
                </button>
              </div>
            </td>
            <!-- NF: Faturador / Etiqueta / Impressão (esta última substitui a
                 antiga coluna "Frete"). Select inline igual ao de plataforma. -->
            <td
              class="border border-border px-2 py-1.5 text-xs cursor-pointer"
              :class="{
                'ring-2 ring-blue-500 ring-inset bg-background': isEditing(row.id, 'nf_faturador_id'),
                'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(row.id, 'nf_faturador_id'),
              }"
              @click="!isEditing(row.id, 'nf_faturador_id') && startEdit(row, 'nf_faturador_id')"
            >
              <select
                v-if="isEditing(row.id, 'nf_faturador_id')"
                :ref="setEditInputRef"
                v-model="editValue"
                class="w-full text-xs bg-transparent outline-none"
                @blur="commitEdit" @change="commitEdit" @keydown.escape.prevent="cancelEdit"
              >
                <option value="">—</option>
                <option v-for="o in faturadores" :key="o.id" :value="o.id">{{ o.label }}</option>
              </select>
              <span v-else :class="{ 'text-muted-foreground': !row.nf_faturador_id }">
                {{ nfLabel(faturadores, row.nf_faturador_id) }}
              </span>
            </td>
            <td
              class="border border-border px-2 py-1.5 text-xs cursor-pointer"
              :class="{
                'ring-2 ring-blue-500 ring-inset bg-background': isEditing(row.id, 'nf_etiqueta_id'),
                'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(row.id, 'nf_etiqueta_id'),
              }"
              @click="!isEditing(row.id, 'nf_etiqueta_id') && startEdit(row, 'nf_etiqueta_id')"
            >
              <select
                v-if="isEditing(row.id, 'nf_etiqueta_id')"
                :ref="setEditInputRef"
                v-model="editValue"
                class="w-full text-xs bg-transparent outline-none"
                @blur="commitEdit" @change="commitEdit" @keydown.escape.prevent="cancelEdit"
              >
                <option value="">—</option>
                <option v-for="o in etiquetas" :key="o.id" :value="o.id">{{ o.label }}</option>
              </select>
              <span v-else :class="{ 'text-muted-foreground': !row.nf_etiqueta_id }">
                {{ nfLabel(etiquetas, row.nf_etiqueta_id) }}
              </span>
            </td>
            <!-- Horário Etiqueta — badge com os horários; popover escolhe da lista. -->
            <td
              class="border border-border px-1 py-1 text-center"
              :class="{ 'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(row.id, 'etiqueta_horarios') }"
            >
              <button
                type="button"
                :disabled="!canEdit"
                class="inline-block px-1.5 py-0.5 rounded border text-[10px] font-semibold transition-colors"
                :class="row.etiqueta_horarios
                  ? 'bg-blue-500/15 text-blue-400 border-blue-500/40'
                  : 'bg-muted/40 text-muted-foreground border-border'"
                @click="canEdit && toggleHorPopover(row.id)"
              >
                {{ row.etiqueta_horarios || 'contínuo' }}
              </button>
            </td>
            <td
              class="border border-border px-2 py-1.5 text-xs cursor-pointer"
              :class="{
                'ring-2 ring-blue-500 ring-inset bg-background': isEditing(row.id, 'nf_impressao_id'),
                'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(row.id, 'nf_impressao_id'),
              }"
              @click="!isEditing(row.id, 'nf_impressao_id') && startEdit(row, 'nf_impressao_id')"
            >
              <select
                v-if="isEditing(row.id, 'nf_impressao_id')"
                :ref="setEditInputRef"
                v-model="editValue"
                class="w-full text-xs bg-transparent outline-none"
                @blur="commitEdit" @change="commitEdit" @keydown.escape.prevent="cancelEdit"
              >
                <option value="">—</option>
                <option v-for="o in impressoes" :key="o.id" :value="o.id">{{ o.label }}</option>
              </select>
              <span v-else :class="{ 'text-muted-foreground': !row.nf_impressao_id }">
                {{ nfLabel(impressoes, row.nf_impressao_id) }}
              </span>
            </td>
            <!-- text field: cpf_name (responsável) -->
            <template
              v-for="f in ['cpf_name']"
              :key="f"
            >
              <td
                class="border border-border px-2 py-1.5 text-xs cursor-pointer"
                :class="{
                  'ring-2 ring-blue-500 ring-inset bg-background': isEditing(row.id, f),
                  'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(row.id, f),
                }"
                @click="!isEditing(row.id, f) && startEdit(row, f)"
              >
                <input
                  v-if="isEditing(row.id, f)"
                  :ref="setEditInputRef"
                  v-model="editValue" type="text"
                  class="w-full text-xs bg-transparent outline-none"
                  @blur="commitEdit" @keydown.enter.prevent="commitEdit" @keydown.escape.prevent="cancelEdit"
                />
                <span v-else :class="{ 'text-muted-foreground': !((row as any)[f]) }">
                  {{ (row as any)[f] || '—' }}
                </span>
              </td>
            </template>
            <!-- Equipe de Vendas: inteiro positivo ou vazio (sem equipe).
                 Mesma UX de edição inline; type=number no input. -->
            <td
              class="border border-border px-2 py-1.5 text-xs text-center cursor-pointer"
              :class="{
                'ring-2 ring-blue-500 ring-inset bg-background': isEditing(row.id, 'sales_team'),
                'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(row.id, 'sales_team'),
              }"
              @click="!isEditing(row.id, 'sales_team') && startEdit(row, 'sales_team')"
            >
              <input
                v-if="isEditing(row.id, 'sales_team')"
                :ref="setEditInputRef"
                v-model="editValue" type="text" placeholder="2.1"
                class="w-full text-xs bg-transparent outline-none text-center"
                @blur="commitEdit" @keydown.enter.prevent="commitEdit" @keydown.escape.prevent="cancelEdit"
              />
              <span v-else :class="{ 'text-muted-foreground': row.sales_team == null }">
                {{ row.sales_team == null ? '—' : teamLabel(row.sales_team) }}
              </span>
            </td>
            <!-- text fields: server / cnpj / email / phone -->
            <template
              v-for="f in ['server', 'cnpj', 'email', 'phone']"
              :key="f"
            >
              <td
                class="border border-border px-2 py-1.5 text-xs cursor-pointer"
                :class="{
                  'ring-2 ring-blue-500 ring-inset bg-background': isEditing(row.id, f),
                  'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(row.id, f),
                  'font-mono': f === 'cnpj' || f === 'phone',
                }"
                @click="!isEditing(row.id, f) && startEdit(row, f)"
              >
                <input
                  v-if="isEditing(row.id, f)"
                  :ref="setEditInputRef"
                  v-model="editValue" type="text"
                  class="w-full text-xs bg-transparent outline-none"
                  :class="{ 'font-mono': f === 'cnpj' || f === 'phone' }"
                  @blur="commitEdit" @keydown.enter.prevent="commitEdit" @keydown.escape.prevent="cancelEdit"
                />
                <span v-else :class="{ 'text-muted-foreground': !((row as any)[f]) }">
                  {{ (row as any)[f] || '—' }}
                </span>
              </td>
            </template>
            <!-- password -->
            <td class="border border-border px-2 py-1.5 text-xs">
              <div class="flex items-center gap-1 group">
                <span
                  class="cursor-pointer truncate flex-1 font-mono"
                  :class="{
                    'ring-2 ring-blue-500 ring-inset bg-background': isEditing(row.id, 'password'),
                    'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(row.id, 'password'),
                    'text-muted-foreground': !row.has_password,
                  }"
                  @click="!isEditing(row.id, 'password') && startEdit(row, 'password')"
                >
                  <input
                    v-if="isEditing(row.id, 'password')"
                    :ref="setEditInputRef"
                    v-model="editValue" type="text"
                    class="w-full text-xs bg-transparent outline-none font-mono"
                    @blur="commitEdit" @keydown.enter.prevent="commitEdit" @keydown.escape.prevent="cancelEdit"
                  />
                  <template v-else>
                    {{ row.has_password ? (revealed.has(row.id) ? (revealedPasswords.get(row.id) || '••••') : '••••••••') : '—' }}
                  </template>
                </span>
                <button
                  v-if="row.has_password && !isEditing(row.id, 'password')"
                  class="opacity-0 group-hover:opacity-100 transition-opacity p-0.5 hover:bg-muted rounded"
                  :title="revealed.has(row.id) ? 'Ocultar' : 'Mostrar senha'"
                  @click="toggleReveal(row.id)"
                >
                  <EyeOff v-if="revealed.has(row.id)" class="h-3 w-3" />
                  <Eye v-else class="h-3 w-3" />
                </button>
                <button
                  v-if="row.has_password && !isEditing(row.id, 'password')"
                  class="opacity-0 group-hover:opacity-100 transition-opacity p-0.5 hover:bg-muted rounded"
                  :title="copiedId === row.id ? 'Copiado!' : 'Copiar senha'"
                  @click="copyPassword(row.id)"
                >
                  <Check v-if="copiedId === row.id" class="h-3 w-3 text-emerald-600" />
                  <Copy v-else class="h-3 w-3" />
                </button>
              </div>
            </td>
            <!-- shipping_address (compact: truncate + tooltip) -->
            <td
              class="border border-border px-2 py-1.5 text-xs cursor-pointer max-w-[140px]"
              :class="{
                'ring-2 ring-blue-500 ring-inset bg-background': isEditing(row.id, 'shipping_address'),
                'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(row.id, 'shipping_address'),
              }"
              :title="row.shipping_address || ''"
              @click="!isEditing(row.id, 'shipping_address') && startEdit(row, 'shipping_address')"
            >
              <input
                v-if="isEditing(row.id, 'shipping_address')"
                :ref="setEditInputRef"
                v-model="editValue" type="text"
                class="w-full text-xs bg-transparent outline-none"
                @blur="commitEdit" @keydown.enter.prevent="commitEdit" @keydown.escape.prevent="cancelEdit"
              />
              <span v-else class="block truncate" :class="{ 'text-muted-foreground': !row.shipping_address }">
                {{ row.shipping_address || '—' }}
              </span>
            </td>
            <!-- return_address (compact: truncate + tooltip) -->
            <td
              class="border border-border px-2 py-1.5 text-xs cursor-pointer max-w-[140px]"
              :class="{
                'ring-2 ring-blue-500 ring-inset bg-background': isEditing(row.id, 'return_address'),
                'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(row.id, 'return_address'),
              }"
              :title="row.return_address || ''"
              @click="!isEditing(row.id, 'return_address') && startEdit(row, 'return_address')"
            >
              <input
                v-if="isEditing(row.id, 'return_address')"
                :ref="setEditInputRef"
                v-model="editValue" type="text"
                class="w-full text-xs bg-transparent outline-none"
                @blur="commitEdit" @keydown.enter.prevent="commitEdit" @keydown.escape.prevent="cancelEdit"
              />
              <span v-else class="block truncate" :class="{ 'text-muted-foreground': !row.return_address }">
                {{ row.return_address || '—' }}
              </span>
            </td>
            <!-- observation -->
            <td
              class="border border-border px-2 py-1.5 text-xs cursor-pointer"
              :class="{
                'ring-2 ring-blue-500 ring-inset bg-background': isEditing(row.id, 'observation'),
                'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(row.id, 'observation'),
              }"
              @click="!isEditing(row.id, 'observation') && startEdit(row, 'observation')"
            >
              <input
                v-if="isEditing(row.id, 'observation')"
                :ref="setEditInputRef"
                v-model="editValue" type="text"
                class="w-full text-xs bg-transparent outline-none"
                @blur="commitEdit" @keydown.enter.prevent="commitEdit" @keydown.escape.prevent="cancelEdit"
              />
              <span v-else :class="{ 'text-muted-foreground': !row.observation }">
                {{ row.observation || '—' }}
              </span>
            </td>
            <!-- Tipo (badges from linked pricing_accounts; catálogo excluded;
                 click to open the bind/unbind popover) -->
            <td
              class="border border-border px-1 py-1 text-center relative"
              :class="{ 'ring-2 ring-blue-500 ring-inset bg-background': tipoPopoverFor === row.id }"
              :title="canEdit ? 'Vincular departamentos' : ''"
              @click.stop="openTipoPopover(row.id)"
            >
              <div
                v-if="row.departments.some((d) => DEPT_BADGE[d])"
                class="flex flex-wrap gap-0.5 justify-center cursor-pointer"
              >
                <span
                  v-for="d in row.departments.filter((x) => DEPT_BADGE[x])"
                  :key="d"
                  class="px-1.5 py-0.5 rounded border text-[10px] font-semibold"
                  :class="DEPT_BADGE[d].cls"
                >
                  {{ DEPT_BADGE[d].label }}
                </span>
              </div>
              <span v-else class="text-muted-foreground cursor-pointer text-xs">—</span>
              <!-- popover -->
              <div
                v-if="tipoPopoverFor === row.id"
                class="absolute z-20 mt-1 left-1/2 -translate-x-1/2 w-36 rounded-md border bg-popover p-2 shadow-lg text-left"
                @click.stop
              >
                <label
                  v-for="opt in TIPO_OPTIONS"
                  :key="opt.slug"
                  class="flex items-center gap-2 py-1 cursor-pointer text-xs hover:bg-accent/50 px-1 rounded"
                >
                  <input
                    type="checkbox"
                    :checked="row.departments.includes(opt.slug)"
                    :disabled="tipoBusy.has(`${row.id}:${opt.slug}`)"
                    @change="(e) => toggleDepartment(row, opt.slug, (e.target as HTMLInputElement).checked)"
                  />
                  <span>{{ opt.label }}</span>
                </label>
                <button
                  class="mt-1 w-full text-center text-[10px] text-muted-foreground hover:text-foreground py-0.5"
                  @click="tipoPopoverFor = null"
                >
                  fechar
                </button>
              </div>
            </td>
            <!-- Tab. Preço -->
            <td class="border border-border px-1 py-1 text-center">
              <span
                class="inline-block px-1.5 py-0.5 rounded border text-[10px] font-semibold"
                :class="row.has_pricing
                  ? 'bg-emerald-500/15 text-emerald-400 border-emerald-500/40'
                  : 'bg-red-500/15 text-red-400 border-red-500/40'"
              >
                {{ row.has_pricing ? 'Sim' : '✕ Não' }}
              </span>
            </td>
            <!-- Integração -->
            <td class="border border-border px-1 py-1 text-center">
              <span
                class="inline-block px-1.5 py-0.5 rounded border text-[10px] font-semibold"
                :class="row.has_integration
                  ? 'bg-emerald-500/15 text-emerald-400 border-emerald-500/40'
                  : 'bg-red-500/15 text-red-400 border-red-500/40'"
              >
                {{ row.has_integration ? 'Sim' : '✕ Não' }}
              </span>
            </td>
            <!-- Bling ID — click anywhere in the cell to edit (matches the
                 platform cell pattern). Empty cells get a "—" placeholder
                 but the whole <td> is the click target so a row with no
                 value is still trivially editable. -->
            <td
              class="border border-border px-2 py-1.5 text-xs cursor-pointer"
              :class="{
                'ring-2 ring-blue-500 ring-inset bg-background': isEditing(row.id, 'bling_store_id'),
                'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(row.id, 'bling_store_id'),
              }"
              @click="!isEditing(row.id, 'bling_store_id') && canEdit && startEdit(row, 'bling_store_id')"
            >
              <input
                v-if="isEditing(row.id, 'bling_store_id')"
                :ref="setEditInputRef"
                v-model="editValue" type="text"
                class="w-full text-xs bg-transparent outline-none"
                @click.stop
                @blur="commitEdit" @keydown.enter.prevent="commitEdit" @keydown.escape.prevent="cancelEdit"
              />
              <span
                v-else
                :class="{ 'text-muted-foreground': !row.bling_store_id }"
              >{{ row.bling_store_id || '—' }}</span>
            </td>
            <!-- UpseSeller — matches Tab.Preço/Integração badge style. Click cycles
                 null → Sim → Não → null. -->
            <td class="border border-border px-1 py-1 text-center">
              <button
                type="button"
                :disabled="!canEdit"
                class="inline-block px-1.5 py-0.5 rounded border text-[10px] font-semibold transition-colors"
                :class="badgeClassTriBool(row.upseseller)"
                @click="canEdit && cycleTriBool(row, 'upseseller')"
              >
                {{ labelTriBool(row.upseseller) }}
              </button>
            </td>
            <!-- Duoker -->
            <td class="border border-border px-1 py-1 text-center">
              <button
                type="button"
                :disabled="!canEdit"
                class="inline-block px-1.5 py-0.5 rounded border text-[10px] font-semibold transition-colors"
                :class="badgeClassTriBool(row.duoker)"
                @click="canEdit && cycleTriBool(row, 'duoker')"
              >
                {{ labelTriBool(row.duoker) }}
              </button>
            </td>
            <!-- UF — compact badge showing count; click opens popover anchored
                 to the badge (not inside the cell to avoid clipping). -->
            <td class="border border-border px-1 py-1 text-center">
              <button
                type="button"
                :disabled="!canEdit"
                class="inline-block px-1.5 py-0.5 rounded border text-[10px] font-semibold transition-colors"
                :class="(row.uf_restrictions && row.uf_restrictions.length)
                  ? 'bg-blue-500/15 text-blue-400 border-blue-500/40'
                  : 'bg-muted/40 text-muted-foreground border-border'"
                @click="canEdit && toggleUfPopover(row.id)"
              >
                {{ (row.uf_restrictions && row.uf_restrictions.length)
                  ? `${row.uf_restrictions.length} UF`
                  : '—' }}
              </button>
            </td>
            <!-- Exceções — badge com contagem de regras; popover editor. -->
            <td class="border border-border px-1 py-1 text-center">
              <button
                type="button"
                :disabled="!canEdit"
                class="inline-block px-1.5 py-0.5 rounded border text-[10px] font-semibold transition-colors"
                :class="(row.excecoes && row.excecoes.length)
                  ? 'bg-amber-500/15 text-amber-400 border-amber-500/40'
                  : 'bg-muted/40 text-muted-foreground border-border'"
                @click="canEdit && toggleExcPopover(row.id)"
              >
                {{ (row.excecoes && row.excecoes.length)
                  ? `${row.excecoes.length} regra${row.excecoes.length > 1 ? 's' : ''}`
                  : '—' }}
              </button>
            </td>
            <!-- actions: arquivar/ativar + delete -->
            <td class="border border-border px-1 py-1 text-center whitespace-nowrap">
              <button
                v-if="canEdit && archivedView"
                class="p-1 text-emerald-500 hover:bg-emerald-500/10 rounded disabled:opacity-40"
                :title="`Ativar ${row.account_name || row.platform}`"
                :disabled="archiveBusy.has(row.id)"
                @click="unarchiveRow(row)"
              >
                <ArchiveRestore class="h-3 w-3" />
              </button>
              <button
                v-else-if="canEdit"
                class="p-1 text-amber-500 hover:bg-amber-500/10 rounded disabled:opacity-40"
                :title="`Arquivar ${row.account_name || row.platform}`"
                :disabled="archiveBusy.has(row.id)"
                @click="archiveRow(row)"
              >
                <Archive class="h-3 w-3" />
              </button>
              <button
                v-if="canDelete"
                class="p-1 text-destructive hover:bg-destructive/10 rounded"
                :title="`Excluir ${row.account_name || row.platform}`"
                @click="remove(row)"
              >
                <Trash2 class="h-3 w-3" />
              </button>
            </td>
          </tr>
          </template>
        </tbody>
      </table>
    </div>

    <!-- UF multi-select popover — rendered outside the table to escape its
         overflow-clipping. Centered overlay; click outside or "fechar" to
         close. -->
    <div
      v-if="openUfRow"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      @click.self="openUfRowId = null"
    >
      <div class="bg-background border rounded-lg shadow-xl p-4 w-80">
        <div class="flex items-center justify-between mb-2">
          <div class="text-sm font-semibold">
            UF — {{ openUfRow.platform }} / {{ openUfRow.account_name || '—' }}
          </div>
          <button
            class="text-muted-foreground hover:text-foreground"
            @click="openUfRowId = null"
          >
            <X class="h-4 w-4" />
          </button>
        </div>
        <div class="grid grid-cols-5 gap-1 text-xs">
          <label
            v-for="uf in UF_OPTIONS"
            :key="uf"
            class="flex items-center gap-1 cursor-pointer px-1 py-0.5 rounded hover:bg-muted"
          >
            <input
              type="checkbox"
              :checked="(openUfRow.uf_restrictions || []).includes(uf)"
              @change="toggleUf(openUfRow, uf, ($event.target as HTMLInputElement).checked)"
            />
            <span>{{ uf }}</span>
          </label>
        </div>
        <div class="mt-3 flex justify-end gap-2 text-xs">
          <button
            class="px-2 py-1 rounded border hover:bg-muted"
            @click="updateField(openUfRow, 'uf_restrictions', null); openUfRowId = null"
          >
            limpar tudo
          </button>
          <button
            class="px-2 py-1 rounded bg-blue-600 text-white hover:bg-blue-700"
            @click="openUfRowId = null"
          >
            fechar
          </button>
        </div>
      </div>
    </div>

    <!-- Horário Etiqueta — escolhe os horários (BRT) no relógio do input time.
         Lista vazia = contínuo (imprime quando a NF fecha). -->
    <div
      v-if="openHorRow"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      @click.self="openHorRowId = null"
    >
      <div class="bg-background border rounded-lg shadow-xl p-4 w-80">
        <div class="flex items-center justify-between mb-1">
          <div class="text-sm font-semibold">
            Horário Etiqueta — {{ openHorRow.platform }} / {{ openHorRow.account_name || '—' }}
          </div>
          <button
            class="text-muted-foreground hover:text-foreground"
            @click="openHorRowId = null"
          >
            <X class="h-4 w-4" />
          </button>
        </div>
        <p class="text-xs text-muted-foreground mb-2">
          Sem horário na lista = contínuo (imprime assim que a NF fecha).
        </p>
        <div class="flex flex-wrap gap-1 mb-2 text-xs">
          <span
            v-for="h in horariosDe(openHorRow)"
            :key="h"
            class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded border bg-blue-500/15 text-blue-400 border-blue-500/40"
          >
            {{ h }}
            <button
              class="hover:text-foreground"
              title="remover"
              @click="removeHorario(openHorRow, h)"
            >
              <X class="h-3 w-3" />
            </button>
          </span>
          <span v-if="!horariosDe(openHorRow).length" class="text-muted-foreground">
            nenhum horário
          </span>
        </div>
        <div class="flex items-center gap-2 text-xs">
          <input
            v-model="novoHorario"
            type="time"
            class="flex-1 border rounded px-2 py-1 bg-background"
            @keydown.enter.prevent="addHorario(openHorRow)"
          >
          <button
            class="px-2 py-1 rounded border hover:bg-muted disabled:opacity-40"
            :disabled="!novoHorario"
            @click="addHorario(openHorRow)"
          >
            adicionar
          </button>
        </div>
        <div class="mt-3 flex justify-end gap-2 text-xs">
          <button
            class="px-2 py-1 rounded border hover:bg-muted"
            @click="updateField(openHorRow, 'etiqueta_horarios', null); openHorRowId = null"
          >
            contínuo (limpar)
          </button>
          <button
            class="px-2 py-1 rounded bg-blue-600 text-white hover:bg-blue-700"
            @click="openHorRowId = null"
          >
            fechar
          </button>
        </div>
      </div>
    </div>

    <!-- Exceções popover — editor de regras de bloqueio de envio da loja.
         Pedido que casa vai pra Aguardando Cancelamento no sweep de NF. -->
    <div
      v-if="openExcRow"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      @click.self="openExcRowId = null"
    >
      <div class="bg-background border rounded-lg shadow-xl p-4 w-[26rem]">
        <div class="flex items-center justify-between mb-2">
          <div class="text-sm font-semibold">
            Exceções — {{ openExcRow.platform }} / {{ openExcRow.account_name || '—' }}
          </div>
          <button
            class="text-muted-foreground hover:text-foreground"
            @click="openExcRowId = null"
          >
            <X class="h-4 w-4" />
          </button>
        </div>
        <p class="text-[11px] text-muted-foreground mb-3">
          As regras valem pras UFs do campo <b>Restrição</b> da loja
          ({{ (openExcRow.uf_restrictions || []).join(', ') || 'nenhuma UF — configure a Restrição' }}).
          Pedido pra essas UFs que casar uma regra NÃO é enviado — vai pra
          Aguardando Cancelamento com "restrição" nas observações do Bling.
        </p>
        <div v-if="openExcRow.excecoes && openExcRow.excecoes.length" class="space-y-1 mb-3">
          <div
            v-for="(r, i) in openExcRow.excecoes"
            :key="i"
            class="flex items-center justify-between gap-2 text-xs px-2 py-1 rounded border bg-muted/30"
          >
            <span class="truncate">{{ excecaoLabel(r) }}</span>
            <button
              class="text-destructive hover:bg-destructive/10 rounded p-0.5 shrink-0"
              title="Remover regra"
              @click="removeExcecao(openExcRow, i)"
            >
              <Trash2 class="h-3 w-3" />
            </button>
          </div>
        </div>
        <div v-else class="text-xs text-muted-foreground mb-3">Nenhuma regra.</div>
        <div class="border rounded p-2 space-y-2 text-xs">
          <select v-model="excDraft.tipo" class="border rounded px-1 py-1 bg-background w-full">
            <option value="valor">Valor do pedido (≥)</option>
            <option value="sku">SKU do item</option>
            <option value="palavra">Palavra no nome</option>
          </select>
          <input
            v-if="excDraft.tipo === 'valor'"
            v-model="excDraft.valor"
            placeholder="Valor mínimo bloqueado, ex. 700"
            class="border rounded px-2 py-1 bg-background w-full"
          />
          <input
            v-else
            v-model="excDraft.termos"
            :placeholder="excDraft.tipo === 'sku'
              ? 'SKUs separados por vírgula, ex. a001, b002'
              : 'Palavras separadas por vírgula, ex. apple, iphone'"
            class="border rounded px-2 py-1 bg-background w-full"
          />
          <div class="flex justify-end">
            <button
              class="px-2 py-1 rounded bg-blue-600 text-white hover:bg-blue-700"
              @click="addExcecao(openExcRow)"
            >
              adicionar regra
            </button>
          </div>
        </div>
        <div class="mt-3 flex justify-end text-xs">
          <button
            class="px-2 py-1 rounded border hover:bg-muted"
            @click="openExcRowId = null"
          >
            fechar
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
