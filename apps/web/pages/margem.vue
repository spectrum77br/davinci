<script setup lang="ts">
import { AlertCircle, Check, ChevronLeft, ChevronRight, Download, Loader2, Pencil, RefreshCw, Search, X } from 'lucide-vue-next'

definePageMeta({
  middleware: ['permission'],
  permission: { resource: 'margem', action: 'view' },
})

type MarketplaceRow = {
  bling_order_item_id: string
  bling_id: number | null
  data: string | null
  pedido_bling: string | null
  pedido_marketplace: string | null
  plataforma: string | null
  conta: string | null
  sku: string | null
  produto: string | null
  quantidade: number | null
  custo_produto: number | null
  frete_plataforma: number | null
  frete_anuncio: number | null
  frete_projetado: number | null
  reembolso: number | null
  resultado_frete: number | null
  saldo_plataforma: number | null
  saldo_projetado: boolean | null
  saldo_bling: number | null
  saldo_efetivo: number | null
  margem: number | null
  margem_bling: number | null
  margem_pos_reembolso: number | null
  margem_minima: number | null
  situacao_id: number | null
  situacao: string | null
  ajustes: number | null
  saldo_final: number | null
  status: string | null
  pricing_account_name: string | null
  pricing_account_listing_type: string | null
  pricing_leaf_segment_name: string | null
  bling_listing_type: string | null
  observacao: string | null
  attention_margem: boolean
  attention_frete: boolean
  attention_saldo: boolean
  // Data Especial do segmento ativa (período casa e a margem passa na regra
  // especial) — margem baixa não trava este pedido. Badge na coluna Margem Mín.
  data_especial: boolean
}

type PageResponse = {
  items: MarketplaceRow[]
  total: number
  limit: number
  offset: number
  platforms: string[]
  contas: string[]
  historico_disponivel?: boolean
}

type MargensStatus = 'Pendente' | 'Reprovado' | 'Aprovado'
const STATUS_OPTIONS: MargensStatus[] = ['Pendente', 'Reprovado', 'Aprovado']
const STATUS_CLS: Record<string, string> = {
  Pendente:  'bg-amber-500/15 text-amber-400 border-amber-500/40',
  Reprovado: 'bg-red-500/15 text-red-400 border-red-500/40',
  Aprovado:  'bg-emerald-500/15 text-emerald-400 border-emerald-500/40',
}
function statusCls(s: string | null | undefined): string {
  return STATUS_CLS[s ?? ''] ?? 'bg-muted text-muted-foreground border-border'
}

const PAGE_SIZE = 100
type StatusFilter = 'Pendente' | 'Aprovado' | 'Reprovado' | 'all'
const STATUS_FILTER_OPTIONS: StatusFilter[] = ['Pendente', 'Aprovado', 'Reprovado', 'all']

type AttentionType = 'all' | 'margem' | 'saldo'
const ATTENTION_LABEL: Record<AttentionType, string> = {
  all:    'todos motivos',
  margem: 'margem baixa',
  saldo:  'saldo divergente',
}
const ATTENTION_OPTIONS: AttentionType[] = ['all', 'margem', 'saldo']

const { api } = useApi()
const canEdit = useCan('margem', 'edit')

// Coluna "Frete Resultado" restrita a estes usuários.
const _auth = useAuthStore()
const FRETE_RESULTADO_USERS = [
  'spectrum77@tuta.com',
  'maconer06@tuta.com',
]
const canSeeFreteResultado = computed(() => {
  const email = _auth.user?.email?.toLowerCase()
  return !!email && FRETE_RESULTADO_USERS.includes(email)
})

// Usuários para quem o snapshot é reconstruído automaticamente a cada
// carregamento da página (com indicador visual de progresso).
const SNAPSHOT_AUTO_REFRESH_USERS = [
  'joffer4@tuta.com',
  'maconer06@tuta.com',
  'hans21@tuta.com',
  'spectrum77@tuta.com',
]
const shouldAutoRefresh = computed(() => {
  const email = _auth.user?.email?.toLowerCase()
  return !!email && SNAPSHOT_AUTO_REFRESH_USERS.includes(email)
})

// Coluna "Lucro" (R$) restrita a administradores.
const isAdmin = useIsAdmin()

const items = ref<MarketplaceRow[]>([])
const total = ref(0)
const platforms = ref<string[]>([])
const contas = ref<string[]>([])
const loading = ref(false)
const error = ref<string | null>(null)
// Aviso informativo (não-erro): ex. saldo gravado mas margem abaixo da mínima,
// pedido mantido como Pendente. Persiste através do reload que segue a ação.
const notice = ref<string | null>(null)

// --- Download da planilha de rentabilidade por período (admin) ---
function isoDate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
const _today = new Date()
const rentInicio = ref(isoDate(new Date(_today.getFullYear(), _today.getMonth(), 1)))
const rentFim = ref(isoDate(_today))
const rentExporting = ref(false)

async function exportRentabilidade() {
  if (rentExporting.value || !rentInicio.value || !rentFim.value) return
  if (rentFim.value < rentInicio.value) {
    error.value = 'A data final não pode ser anterior à inicial.'
    return
  }
  rentExporting.value = true
  error.value = null
  try {
    const params = new URLSearchParams({ inicio: rentInicio.value, fim: rentFim.value })
    const blob = await api<Blob>(
      `/api/margens/rentabilidade/export.xlsx?${params.toString()}`,
      { responseType: 'blob' as any },
    )
    const href = URL.createObjectURL(blob as any)
    const a = document.createElement('a')
    a.href = href
    a.download = `rentabilidade_${rentInicio.value}_${rentFim.value}.xlsx`
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(href)
  } catch (e: any) {
    error.value = apiError(e)
  } finally {
    rentExporting.value = false
  }
}

const search = ref('')
const platform = ref<'all' | string>('all')
const conta = ref<'all' | string>('all')
const statusFilter = ref<StatusFilter>('Pendente')
const attentionType = ref<AttentionType>('all')
const page = ref(1)

type Tab = 'list' | 'lookup'
const tab = ref<Tab>('list')
const lookupInput = ref('')
const lookupTerm = ref('')
const historicoDisponivel = ref(false)
const historicoLoading = ref(false)
const historicoElapsedMs = ref(0)

// Auto-refresh do snapshot ao abrir a página (apenas para SNAPSHOT_AUTO_REFRESH_USERS).
const autoRefreshing = ref(false)
const autoRefreshDone = ref(false)
const autoRefreshElapsedMs = ref(0)

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))

function apiError(e: any) {
  const detail = e?.data?.detail
  if (detail && typeof detail === 'object') return detail.message || detail.code || e?.message || 'erro'
  return detail || e?.message || 'erro'
}

function apiErrorCode(e: any) {
  const detail = e?.data?.detail
  return detail && typeof detail === 'object' ? detail.code : null
}

function isBlingPatchError(e: any) {
  return ['bling_patch_failed', 'bling_integration_missing'].includes(apiErrorCode(e))
}

async function load() {
  loading.value = true
  error.value = null
  try {
    const params = new URLSearchParams()
    params.set('limit', String(PAGE_SIZE))
    params.set('offset', String((page.value - 1) * PAGE_SIZE))
    if (platform.value !== 'all') params.set('platform', platform.value)
    if (conta.value !== 'all') params.set('conta', conta.value)
    if (statusFilter.value !== 'all') params.set('status', statusFilter.value)
    if (attentionType.value !== 'all') {
      params.set('attention_type', attentionType.value)
    }
    if (search.value.trim()) params.set('search', search.value.trim())
    const res = await api<PageResponse>(`/api/margens/marketplace?${params.toString()}`)
    items.value = res.items
    total.value = res.total
    platforms.value = res.platforms
    contas.value = res.contas
  } catch (e: any) {
    error.value = apiError(e)
  } finally {
    loading.value = false
  }
}

async function lookup(forceRefresh = false) {
  const term = lookupInput.value.trim()
  if (!term) {
    error.value = 'informe o numero do pedido'
    return
  }
  error.value = null
  lookupTerm.value = term
  historicoDisponivel.value = false

  // Slow-path (force_refresh) gets its own visual state with elapsed timer.
  // Fast-path (snapshot only) reuses the standard `loading` spinner.
  let timerHandle: ReturnType<typeof setInterval> | null = null
  if (forceRefresh) {
    historicoLoading.value = true
    historicoElapsedMs.value = 0
    const started = Date.now()
    timerHandle = setInterval(() => {
      historicoElapsedMs.value = Date.now() - started
    }, 250)
  } else {
    loading.value = true
  }

  try {
    const params = new URLSearchParams()
    params.set('pedido', term)
    if (forceRefresh) params.set('force_refresh', 'true')
    const res = await api<PageResponse>(`/api/margens/marketplace/lookup?${params.toString()}`)
    items.value = res.items
    total.value = res.total
    historicoDisponivel.value = !!res.historico_disponivel
  } catch (e: any) {
    error.value = apiError(e)
    items.value = []
    total.value = 0
  } finally {
    if (timerHandle) clearInterval(timerHandle)
    loading.value = false
    historicoLoading.value = false
  }
}

function lookupHistorico() {
  lookup(true)
}

function fmtElapsed(ms: number) {
  const s = Math.floor(ms / 1000)
  const m = Math.floor(s / 60)
  const rem = s % 60
  return m > 0 ? `${m}m ${String(rem).padStart(2, '0')}s` : `${s}s`
}

function switchTab(next: Tab) {
  if (tab.value === next) return
  tab.value = next
  error.value = null
  if (next === 'lookup') {
    // clear list to avoid confusing display; lookup only after submit
    items.value = []
    total.value = 0
    lookupTerm.value = ''
    historicoDisponivel.value = false
  } else {
    lookupInput.value = ''
    lookupTerm.value = ''
    historicoDisponivel.value = false
    page.value = 1
    load()
  }
}

await load()

async function reloadCurrent() {
  if (tab.value === 'lookup') {
    if (lookupTerm.value) await lookup()
  } else {
    await load()
  }
}

async function refreshAndLoad() {
  loading.value = true
  error.value = null
  try {
    await api('/api/margens/marketplace/refresh', { method: 'POST' })
  } catch (e: any) {
    error.value = apiError(e)
    loading.value = false
    return
  }
  await reloadCurrent()
}

// Reconstrói o snapshot ao abrir a página E a cada 1 minuto enquanto ela fica
// aberta (cadência pedida pelo Eduardo 2026-08-31: "tem que atualizar, de 1 em
// 1 minuto"). A tabela já foi renderizada com o snapshot atual (await load()
// acima), então NÃO bloqueia: o usuário usa os dados na hora e o banner só
// sinaliza a atualização em curso.
//
// O throttle é do servidor: passamos max_age_s=60 e o backend pula o rebuild
// (resp.refreshed=false) se o snapshot já estiver fresco — autoritativo entre
// todos os usuários — e garante single-flight (um rebuild por vez). Aba em
// segundo plano não dispara (economiza rebuilds); ao voltar para a aba,
// atualiza na hora. Quando o rebuild roda de fato, a tabela visível é trocada
// ao concluir — os números "caem frescos" sem o usuário tocar em nada.
async function autoRefreshSnapshot() {
  if (autoRefreshing.value) return
  autoRefreshing.value = true
  autoRefreshDone.value = false
  autoRefreshElapsedMs.value = 0
  const started = Date.now()
  const timer = setInterval(() => {
    autoRefreshElapsedMs.value = Date.now() - started
  }, 250)
  try {
    const res = await api<{ refreshed?: boolean }>(
      '/api/margens/marketplace/refresh?max_age_s=60',
      { method: 'POST' },
    )
    // Só recarrega/anuncia quando houve rebuild de fato (servidor não pulou).
    if (res?.refreshed) {
      await reloadCurrent()
      autoRefreshDone.value = true
      setTimeout(() => { autoRefreshDone.value = false }, 4000)
    }
  } catch (e: any) {
    error.value = apiError(e)
  } finally {
    clearInterval(timer)
    autoRefreshing.value = false
  }
}

// Timer da cadência de 1 min. Só dispara com a aba visível — aba esquecida em
// segundo plano não fica reconstruindo o snapshot à toa; o listener de
// visibilidade cobre a volta (atualiza assim que o usuário retorna à aba).
let autoRefreshTimer: ReturnType<typeof setInterval> | null = null
function onTabVisible() {
  if (document.visibilityState === 'visible' && shouldAutoRefresh.value) autoRefreshSnapshot()
}
onMounted(() => {
  if (!shouldAutoRefresh.value) return
  autoRefreshSnapshot()
  autoRefreshTimer = setInterval(() => {
    if (document.visibilityState === 'visible') autoRefreshSnapshot()
  }, 60_000)
  document.addEventListener('visibilitychange', onTabVisible)
})
onBeforeUnmount(() => {
  if (autoRefreshTimer) clearInterval(autoRefreshTimer)
  document.removeEventListener('visibilitychange', onTabVisible)
})

// reload on filter change (debounced search) — only in list mode
let searchTimer: ReturnType<typeof setTimeout> | null = null
watch(search, () => {
  if (tab.value !== 'list') return
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    page.value = 1
    load()
  }, 350)
})
watch(platform, () => {
  if (tab.value !== 'list') return
  page.value = 1
  load()
})
watch(conta, () => {
  if (tab.value !== 'list') return
  page.value = 1
  load()
})
watch(statusFilter, () => {
  if (tab.value !== 'list') return
  notice.value = null
  page.value = 1
  load()
})
watch(attentionType, () => {
  if (tab.value !== 'list') return
  page.value = 1
  load()
})
watch(page, () => {
  if (tab.value !== 'list') return
  load()
})

// Raio-X do saldo: pedido aberto na janelinha (null = fechada).
const raioXPedido = ref<string | null>(null)

function brl(v: number | null | undefined) {
  if (v == null) return '—'
  return v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })
}

function pct(v: number | null | undefined) {
  if (v == null) return '—'
  return `${(v * 100).toFixed(1)}%`
}

// Interpreta um número digitado em pt-BR. Quando há vírgula, ela é o separador
// decimal: removem-se os pontos (separador de milhar) e a vírgula vira ponto —
// então "3.919,92" → 3919.92. Sem vírgula, o ponto é tratado como decimal, o que
// cobre tanto o valor pré-preenchido cru ("4133.04") quanto inteiros ("3919").
// Retorna NaN para entrada vazia/inválida. Sem isto, "3.919,92" virava "3.919.92"
// (só a vírgula era trocada) → NaN → a edição do Saldo Efetivo era descartada em
// silêncio para qualquer valor ≥ R$ 1.000.
function parseBrNumber(s: string): number {
  const t = s.trim()
  if (t === '') return NaN
  const normalized = t.includes(',') ? t.replace(/\./g, '').replace(',', '.') : t
  return Number(normalized)
}

function fmtDate(v: string | null) {
  if (!v) return '—'
  // Bling envia 'data' como YYYY-MM-DDT00:00:00Z — usar a parte YYYY-MM-DD
  // direto evita o off-by-one por causa do fuso BRT (UTC-3).
  const ymd = v.slice(0, 10)
  const [y, m, d] = ymd.split('-')
  if (!y || !m || !d) return v
  return `${d}/${m}/${y}`
}

function platformRowBg(platform: string | null): string {
  switch ((platform || '').toLowerCase()) {
    case 'shopee':       return 'bg-orange-50/50 dark:bg-orange-900/10'
    case 'mercadolivre':
    case 'mercado livre':
    case 'meli':         return 'bg-blue-50/50 dark:bg-blue-900/10'
    case 'amazon':       return 'bg-yellow-50/50 dark:bg-yellow-900/10'
    case 'magalu':       return 'bg-blue-50/30 dark:bg-blue-900/10'
    case 'tiktok':       return 'bg-pink-50/50 dark:bg-pink-900/10'
    default:             return ''
  }
}

function freteProjMissingReason(r: MarketplaceRow): string | null {
  if (r.frete_projetado != null) return null
  if (!r.pricing_leaf_segment_name) {
    return `Loja: ${r.conta ?? '—'} · SKU "${r.sku}" sem cadastro em pricing_products — cadastre em /pricing aba Produtos.`
  }
  if (!r.pricing_account_name) {
    return `Sem pricing_account p/ esta loja+segmento (${r.pricing_leaf_segment_name}). Cadastre em /pricing aba Contas.`
  }
  return `Pricing account "${r.pricing_account_name}" não tem shipping para o segmento "${r.pricing_leaf_segment_name}".`
}

// Margem exibida na coluna "Margem": usa a pós-reembolso; quando null ou zero
// (pedido sem refund/ajuste), cai na margem do Bling. EXCEÇÃO ML/Shopee/
// TikTok sem repasse real: Lucro/Final ficam EM BRANCO (01/09 à noite —
// nada de número do Bling nem projeção enquanto a plataforma não confirma).
function margemFinal(r: MarketplaceRow): number | null {
  if (saldoAncoradoNaPlataforma(r) && r.saldo_plataforma == null) return null
  const m = r.margem_pos_reembolso
  return (m == null || m === 0) ? r.margem_bling : m
}
function margemFinalCls(r: MarketplaceRow): string {
  const m = margemFinal(r)
  if (m == null) return ''
  const ref = r.margem_minima != null ? r.margem_minima : 0
  return m >= ref ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-600 dark:text-red-400'
}

// Lucro (R$) coerente com a coluna Margem: lucro = margemFinal × custo
// (= saldo − custo). Quando margemFinal cai no Bling, o lucro também reflete o
// Bling. Só exibido para admins.
function lucroFinal(r: MarketplaceRow): number | null {
  const m = margemFinal(r)
  if (m == null || r.custo_produto == null) return null
  return m * r.custo_produto
}
function lucroFinalCls(r: MarketplaceRow): string {
  const l = lucroFinal(r)
  if (l == null) return ''
  return l >= 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-600 dark:text-red-400'
}

// Saldo Efetivo exibido = saldo pós-reembolso (saldo_final = efetivo +
// reembolso; o prejuízo é só visual e NÃO desconta). Ambos vêm ancorados no
// Bling (valor_base − frete − taxa), não no líquido do marketplace — é o que a
// edição inline grava e o que o snapshot patcheia, então o valor editado aparece
// na hora. saldo_efetivo é a base (sem reembolso) e é o que a edição inline
// usa/grava; saldo_final é a base + reembolso.
function saldoEfetivoDisplay(r: MarketplaceRow): number | null {
  return r.saldo_final != null ? r.saldo_final : r.saldo_efetivo
}

// ML/Shopee/TikTok: o saldo da plataforma é a fonte da verdade — o backend
// ancora o Saldo Efetivo no líquido REAL da plataforma, ou deixa EM BRANCO
// enquanto ele não sincroniza (01/09 à noite, Eduardo: "o saldo efetivo
// deixe sempre em branco, retirar a projeção, sempre pegar a da plataforma"
// — a projeção ≈ aprovava às cegas). Nunca cai no Bling (tarde: "você
// aprovou usando o saldo do Bling"). Enquanto em branco, a linha fica
// Pendente por "aguardando saldo da plataforma" (sem hold do robô). Aqui a
// mesma condição decide: (a) divergência Bling × Plataforma não zera o
// Efetivo de laranja (não há o que conciliar — vale a plataforma); (b) o
// lápis some SEMPRE nessas plataformas (editar gravaria no Bling e o número
// da célula não mudaria — pareceria "não salvou").
function saldoAncoradoNaPlataforma(r: MarketplaceRow): boolean {
  return ['ml', 'shopee', 'tiktok'].includes(r.plataforma ?? '')
}

// Divergência de saldo: Bling e Plataforma ambos presentes e diferindo por mais
// de R$0,01 (qualquer situação). Saldo Plataforma agora é só o repasse REAL
// (projeção retirada em 01/09 à noite), então comparar com ele é sempre
// legítimo. ML/Shopee/TikTok ficam fora: o Efetivo delas JÁ É o saldo da
// plataforma, divergência com o Bling não é pendência.
function saldoDivergente(r: MarketplaceRow): boolean {
  return r.saldo_bling != null
    && r.saldo_plataforma != null
    && !saldoAncoradoNaPlataforma(r)
    && Math.abs(r.saldo_bling - r.saldo_plataforma) > 0.01
}

// Exibe o Saldo Efetivo como 0 (apenas visual) SOMENTE enquanto o pedido não foi
// aprovado E Bling × Plataforma divergem — é o alerta de "ainda falta conciliar".
// Uma vez aprovado, a conciliação está fechada e a coluna volta a mostrar o valor
// real (não zera mais). Sem esse gate, um pedido aprovado-e-divergente ficava em
// R$0,00 para sempre e, como a edição grava valorbase mas NÃO mexe no saldo da
// plataforma, a divergência persistia após salvar — parecendo que "não salvou".
function saldoZeradoVisual(r: MarketplaceRow): boolean {
  return r.status !== 'Aprovado' && saldoDivergente(r)
}

// Valor exibido na coluna "Efetivo". Mostra 0 APENAS visualmente quando
// `saldoZeradoVisual` — não altera o banco nem o valor que a edição inline
// usa/grava (startEditSaldoEfetivo continua partindo de r.saldo_efetivo real).
function saldoEfetivoVisual(r: MarketplaceRow): number | null {
  return saldoZeradoVisual(r) ? 0 : saldoEfetivoDisplay(r)
}

// ---------- Status: aprovar/reprovar/pendente (atualiza Bling) ----------

async function setStatus(row: MarketplaceRow, value: MargensStatus) {
  if (!canEdit.value || value === row.status || !row.pedido_bling) return
  notice.value = null
  const prev = row.status
  const pedido = row.pedido_bling
  // Push to Bling apenas quando o problema é de margem. Frete/saldo divergente
  // só altera status local — sem situacao no Bling, sem dialog de fallback.
  const pushBling = row.attention_margem
  // optimistic update — propagar pra todas as linhas do mesmo pedido
  for (const r of items.value) if (r.pedido_bling === pedido) r.status = value

  async function call(local_only: boolean) {
    await api(`/api/margens/marketplace/status/${encodeURIComponent(pedido)}`, {
      method: 'PATCH',
      body: { status: value, sku: row.sku, local_only },
    })
  }

  function removeFromView() {
    if (statusFilter.value !== 'all' && value !== statusFilter.value) {
      const removed = items.value.filter(r => r.pedido_bling === pedido).length
      items.value = items.value.filter(r => r.pedido_bling !== pedido)
      total.value = Math.max(0, total.value - removed)
    }
  }

  try {
    await call(!pushBling)
    error.value = null
    removeFromView()
  } catch (e: any) {
    if (pushBling && isBlingPatchError(e)) {
      // Bling não aceitou o patch de situação: marca como aprovado/reprovado
      // apenas no DaVinci automaticamente, sem pedir confirmação ao usuário.
      try {
        await call(true)
        error.value = null
        removeFromView()
        return
      } catch (fallback: any) {
        error.value = apiError(fallback)
      }
    } else {
      error.value = apiError(e)
    }
    for (const r of items.value) if (r.pedido_bling === pedido) r.status = prev
  }
}

// ---------- Sync Saldo Final (grava como final no Bling) ----------

const syncingSaldoFinal = ref<Set<string>>(new Set())
function isSyncingSaldoFinal(id: string): boolean { return syncingSaldoFinal.value.has(id) }

async function syncFromSaldoFinal(row: MarketplaceRow, valorBase?: number) {
  if (!canEdit.value) return
  const id = row.bling_order_item_id
  if (syncingSaldoFinal.value.has(id)) return
  syncingSaldoFinal.value.add(id)
  notice.value = null
  try {
    const res = await api<{ status?: string; margem?: number | null; margem_minima?: number | null }>(
      `/api/margens/marketplace/${encodeURIComponent(id)}/sync-saldo-final`,
      {
        method: 'POST',
        body: valorBase != null ? { valor_base: valorBase } : undefined,
      },
    )
    error.value = null
    // Saldo gravado, mas a margem recalculada não atingiu a mínima: não aprova
    // automaticamente, o pedido fica como Pendente para decisão manual.
    notice.value = res?.status === 'Pendente'
      ? `Saldo gravado. Margem recalculada ${pct(res.margem)} ficou abaixo da mínima ${pct(res.margem_minima)} — pedido mantido como Pendente (sem aprovação automática).`
      : null
    await reloadCurrent()
  } catch (e: any) {
    error.value = apiError(e)
  } finally {
    const next = new Set(syncingSaldoFinal.value)
    next.delete(id)
    syncingSaldoFinal.value = next
  }
}

// ---------- Edição inline de Saldo Efetivo (grava como final no Bling) ----------

const editingSaldoEfetivo = ref<string | null>(null)
const saldoEfetivoDraft = ref('')

function startEditSaldoEfetivo(row: MarketplaceRow) {
  // ML/Shopee com repasse real: o Efetivo É o valor da plataforma — não há o
  // que editar (gravaria no Bling sem mudar o número exibido).
  if (!canEdit.value || !row.pedido_bling || saldoAncoradoNaPlataforma(row)) return
  editingSaldoEfetivo.value = row.bling_order_item_id
  saldoEfetivoDraft.value = row.saldo_efetivo != null ? String(row.saldo_efetivo) : ''
}

function cancelEditSaldoEfetivo() {
  editingSaldoEfetivo.value = null
  saldoEfetivoDraft.value = ''
}

async function saveSaldoEfetivo(row: MarketplaceRow) {
  const next = parseBrNumber(saldoEfetivoDraft.value)
  if (Number.isNaN(next)) {
    cancelEditSaldoEfetivo()
    return
  }
  if (row.saldo_efetivo != null && Math.abs(next - row.saldo_efetivo) < 0.005) {
    cancelEditSaldoEfetivo()
    return
  }
  cancelEditSaldoEfetivo()
  await syncFromSaldoFinal(row, next)
}

// ---------- Edição inline de observação ----------

const editingObs = ref<string | null>(null)
const obsDraft = ref('')

function startEditObs(row: MarketplaceRow) {
  if (!canEdit.value || !row.pedido_bling) return
  editingObs.value = row.bling_order_item_id
  obsDraft.value = row.observacao ?? ''
}

function cancelEditObs() {
  editingObs.value = null
  obsDraft.value = ''
}

async function saveObs(row: MarketplaceRow) {
  if (!row.pedido_bling) {
    cancelEditObs()
    return
  }
  const next = obsDraft.value.trim() || null
  if (next === (row.observacao ?? null)) {
    cancelEditObs()
    return
  }
  try {
    await api(`/api/margens/marketplace/observacao/${encodeURIComponent(row.pedido_bling)}`, {
      method: 'PATCH',
      body: { observacao: next },
    })
    for (const r of items.value) {
      if (r.pedido_bling === row.pedido_bling) r.observacao = next
    }
  } catch (e: any) {
    error.value = apiError(e)
  } finally {
    cancelEditObs()
  }
}

const rangeStart = computed(() => total.value === 0 ? 0 : (page.value - 1) * PAGE_SIZE + 1)
const rangeEnd = computed(() => Math.min(page.value * PAGE_SIZE, total.value))
</script>

<template>
  <div class="space-y-5">
    <PageHeader title="Margem" description="Margem por pedido — conciliação marketplace (últimos 30 dias).">
      <template #actions>
        <div class="flex flex-wrap items-center gap-2">
          <div v-if="isAdmin" class="flex items-center gap-1.5">
            <input
              v-model="rentInicio"
              type="date"
              class="text-sm rounded-md border bg-background px-2 py-1.5"
              title="data inicial"
            />
            <span class="text-muted-foreground text-sm">→</span>
            <input
              v-model="rentFim"
              type="date"
              class="text-sm rounded-md border bg-background px-2 py-1.5"
              title="data final"
            />
            <Button
              size="sm"
              variant="outline"
              :disabled="rentExporting || !rentInicio || !rentFim"
              title="Baixar planilha de rentabilidade por pedido no período"
              @click="exportRentabilidade"
            >
              <Download class="size-4 mr-1.5" :class="{ 'animate-pulse': rentExporting }" />
              planilha
            </Button>
          </div>
          <Button size="sm" variant="outline" :disabled="loading" @click="refreshAndLoad">
            <RefreshCw class="size-4 mr-1.5" :class="{ 'animate-spin': loading }" />
            atualizar
          </Button>
        </div>
      </template>
    </PageHeader>

    <div v-if="error" class="flex items-center gap-2 rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-400">
      <AlertCircle class="size-4" />
      {{ error }}
    </div>

    <div v-if="notice" class="flex items-center gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-600 dark:text-amber-400">
      <AlertCircle class="size-4 shrink-0" />
      {{ notice }}
    </div>

    <!-- Auto-refresh do snapshot ao abrir a página e a cada 1 min (não
         bloqueia: a tabela abaixo já mostra o snapshot atual). O guard de
         1,5s evita piscar o banner nas checagens rápidas em que o servidor
         responde "já está fresco" — ele só aparece em rebuild de verdade. -->
    <div
      v-if="autoRefreshing && autoRefreshElapsedMs > 1500"
      class="flex items-center gap-2.5 rounded-md border border-blue-500/40 bg-blue-500/10 px-3 py-2 text-sm text-blue-700 dark:text-blue-300"
    >
      <Loader2 class="size-4 shrink-0 animate-spin" />
      <span class="font-medium">Atualizando a margem em segundo plano…</span>
      <span class="text-xs text-blue-700/70 dark:text-blue-300/70">
        pode continuar usando — a tabela troca sozinha quando os novos dados chegarem
      </span>
      <span class="ml-auto text-xs text-muted-foreground tabular-nums">{{ fmtElapsed(autoRefreshElapsedMs) }}</span>
    </div>
    <div
      v-else-if="autoRefreshDone"
      class="flex items-center gap-2.5 rounded-md border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-700 dark:text-emerald-300"
    >
      <Check class="size-4 shrink-0" />
      <span class="font-medium">Margem atualizada.</span>
    </div>

    <div class="flex items-center gap-1 border-b">
      <button
        type="button"
        class="px-3 py-1.5 text-sm border-b-2 -mb-px transition-colors"
        :class="tab === 'list'
          ? 'border-primary text-foreground font-medium'
          : 'border-transparent text-muted-foreground hover:text-foreground'"
        @click="switchTab('list')"
      >
        Pendentes (30d)
      </button>
      <button
        type="button"
        class="px-3 py-1.5 text-sm border-b-2 -mb-px transition-colors"
        :class="tab === 'lookup'
          ? 'border-primary text-foreground font-medium'
          : 'border-transparent text-muted-foreground hover:text-foreground'"
        @click="switchTab('lookup')"
      >
        Buscar pedido
      </button>
    </div>

    <div v-if="tab === 'list'" class="flex flex-wrap items-center gap-2">
      <div class="relative">
        <Search class="absolute left-2.5 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
        <input
          v-model="search"
          class="pl-8 pr-3 py-1.5 text-sm rounded-md border bg-background w-72"
          placeholder="buscar pedido, sku, conta, pricing account…"
        />
      </div>
      <select
        v-model="platform"
        class="text-sm rounded-md border bg-background px-2 py-1.5"
      >
        <option value="all">todas plataformas</option>
        <option v-for="p in platforms" :key="p" :value="p">{{ p }}</option>
      </select>
      <select
        v-model="conta"
        class="text-sm rounded-md border bg-background px-2 py-1.5"
      >
        <option value="all">todas contas</option>
        <option v-for="c in contas" :key="c" :value="c">{{ c }}</option>
      </select>
      <select
        v-model="statusFilter"
        class="text-sm rounded-md border bg-background px-2 py-1.5"
      >
        <option v-for="s in STATUS_FILTER_OPTIONS" :key="s" :value="s">
          {{ s === 'all' ? 'todos status' : s }}
        </option>
      </select>
      <select
        v-model="attentionType"
        class="text-sm rounded-md border bg-background px-2 py-1.5"
      >
        <option v-for="a in ATTENTION_OPTIONS" :key="a" :value="a">
          {{ ATTENTION_LABEL[a] }}
        </option>
      </select>
      <span class="ml-auto text-xs text-muted-foreground">
        {{ rangeStart }}–{{ rangeEnd }} de {{ total }}
      </span>
    </div>

    <div v-else class="flex flex-wrap items-center gap-2">
      <div class="relative">
        <Search class="absolute left-2.5 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
        <input
          v-model="lookupInput"
          class="pl-8 pr-3 py-1.5 text-sm rounded-md border bg-background w-72"
          placeholder="numero do pedido (Bling ou marketplace)"
          :disabled="historicoLoading"
          @keydown.enter="lookup(false)"
        />
      </div>
      <Button
        size="sm"
        :disabled="loading || historicoLoading || !lookupInput.trim()"
        @click="lookup(false)"
      >
        <Search class="size-4 mr-1.5" />
        buscar
      </Button>
      <span v-if="lookupTerm" class="text-xs text-muted-foreground">
        pedido <span class="font-mono">{{ lookupTerm }}</span> · {{ total }} {{ total === 1 ? 'item' : 'itens' }}
      </span>
      <span v-else class="text-xs text-muted-foreground">
        digite o numero do pedido e clique em buscar
      </span>
      <Button
        v-if="lookupTerm && items.length && historicoDisponivel"
        size="sm"
        variant="outline"
        :disabled="loading || historicoLoading"
        title="Recalcula da view completa (sem janela de 30 dias). Pode levar alguns minutos."
        @click="lookupHistorico"
      >
        <RefreshCw class="size-4 mr-1.5" :class="{ 'animate-spin': historicoLoading }" />
        atualizar este pedido
      </Button>
    </div>

    <!-- Slow-path overlay: only when user opted-in via "buscar no historico" -->
    <div
      v-if="historicoLoading"
      class="rounded-lg border border-amber-500/40 bg-amber-500/5 px-4 py-5"
    >
      <div class="flex items-start gap-4">
        <svg
          class="size-10 shrink-0 animate-spin text-amber-500"
          viewBox="0 0 24 24"
          fill="none"
          xmlns="http://www.w3.org/2000/svg"
          aria-hidden="true"
        >
          <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-opacity="0.25" stroke-width="3" />
          <path
            d="M22 12a10 10 0 0 1-10 10"
            stroke="currentColor"
            stroke-width="3"
            stroke-linecap="round"
          />
        </svg>
        <div class="flex-1">
          <div class="text-sm font-medium text-amber-700 dark:text-amber-300">
            Buscando pedido <span class="font-mono">{{ lookupTerm }}</span> no historico…
          </div>
          <p class="mt-1 text-xs text-muted-foreground">
            A view de conciliacao precisa materializar todos os pedidos antes
            de filtrar, entao essa busca pode levar varios minutos. Pode deixar
            essa aba aberta — o resultado aparece assim que terminar.
          </p>
          <div class="mt-2 text-xs font-mono tabular-nums text-amber-700 dark:text-amber-300">
            {{ fmtElapsed(historicoElapsedMs) }} decorrido
          </div>
        </div>
      </div>
    </div>

    <!-- Snapshot miss + CTA to opt-in to slow path -->
    <div
      v-else-if="tab === 'lookup' && historicoDisponivel && !items.length"
      class="rounded-lg border border-blue-500/40 bg-blue-500/5 px-4 py-4"
    >
      <div class="flex items-start gap-3">
        <AlertCircle class="size-5 shrink-0 text-blue-500 mt-0.5" />
        <div class="flex-1">
          <div class="text-sm font-medium">
            Pedido <span class="font-mono">{{ lookupTerm }}</span> nao esta no snapshot recente
          </div>
          <p class="mt-1 text-xs text-muted-foreground">
            O pedido existe no Bling mas esta fora da janela de 20 dias do
            cache. A busca no historico le a view completa de conciliacao e
            pode levar varios minutos.
          </p>
          <Button size="sm" class="mt-3" @click="lookupHistorico">
            <Search class="size-4 mr-1.5" />
            buscar no historico
          </Button>
        </div>
      </div>
    </div>

    <div class="overflow-auto rounded border max-h-[75vh] focus:outline-none" tabindex="0">
      <table class="text-xs border-collapse">
        <thead class="bg-background sticky top-0 z-20">
          <tr>
            <th class="px-2 py-1 text-left text-[11px] font-semibold border-b" colspan="3">Identificação</th>
            <th class="px-2 py-1 text-left text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600" colspan="5">Anúncio</th>
            <th class="px-2 py-1 text-right text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600" colspan="2">Item</th>
            <th class="px-2 py-1 text-center text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600 bg-amber-50 dark:bg-amber-900/20" :colspan="canSeeFreteResultado ? 4 : 3">Frete</th>
            <th class="px-2 py-1 text-center text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600 bg-emerald-50 dark:bg-emerald-900/20" colspan="4">Saldo</th>
            <th class="px-2 py-1 text-center text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600 bg-orange-50 dark:bg-orange-900/20" colspan="1">Reembolsos</th>
            <th class="px-2 py-1 text-center text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600 bg-blue-50 dark:bg-blue-900/20" :colspan="isAdmin ? 3 : 2">Margem</th>
            <th class="px-2 py-1 text-center text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600" colspan="1">Situação</th>
            <th class="px-2 py-1 text-left text-[11px] font-semibold border-b border-l-[3px] border-gray-400 dark:border-gray-600" colspan="2">Aprovação</th>
          </tr>
          <tr class="border-b">
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[80px]">Data</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[110px]">Pedido Bling</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[140px]">Pedido Marketplace</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[90px] border-l-[3px] border-gray-400 dark:border-gray-600">Plataforma</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[130px]">Conta</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[140px]">Segmento</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[110px]">SKU</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground min-w-[240px]">Produto</th>
            <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[60px] border-l-[3px] border-gray-400 dark:border-gray-600">Quantidade</th>
            <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[90px]">Custo</th>
            <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[110px] bg-amber-50 dark:bg-amber-900/20 border-l-[3px] border-gray-400 dark:border-gray-600">Plataforma</th>
            <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[110px] bg-amber-50 dark:bg-amber-900/20">Anúncio</th>
            <!-- Frete "Projetado" = a REGRA de frete cadastrada por conta (R$26
                 Shopee, R$10 TikTok, ...) + ⚠ de conta sem cadastro. Retirada
                 em 01/09 por interpretação errada do "e o frete tbm"; Eduardo
                 corrigiu em 02/09 ("as regras não está aparecendo") — o que ele
                 pediu para tirar era SÓ o ≈ do Saldo. Restaurada. -->
            <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[110px] bg-amber-50 dark:bg-amber-900/20">Projetado</th>
            <th v-if="canSeeFreteResultado" class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[110px] bg-amber-50 dark:bg-amber-900/20">Resultado</th>
            <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[110px] bg-emerald-50 dark:bg-emerald-900/20 border-l-[3px] border-gray-400 dark:border-gray-600">Plataforma</th>
            <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[100px] bg-emerald-50 dark:bg-emerald-900/20">Bling</th>
            <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[140px] bg-emerald-50 dark:bg-emerald-900/20">Efetivo</th>
            <th class="px-2 py-1 text-center font-semibold text-[11px] text-muted-foreground whitespace-nowrap bg-emerald-50 dark:bg-emerald-900/20">Raio-X</th>
            <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[100px] bg-orange-50 dark:bg-orange-900/20 border-l-[3px] border-gray-400 dark:border-gray-600">&nbsp;</th>
            <th v-if="isAdmin" class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[90px] bg-blue-50 dark:bg-blue-900/20 border-l-[3px] border-gray-400 dark:border-gray-600">Lucro</th>
            <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[80px] bg-blue-50 dark:bg-blue-900/20" :class="!isAdmin && 'border-l-[3px] border-gray-400 dark:border-gray-600'">Final</th>
            <th class="px-2 py-1 text-right font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[100px] bg-blue-50 dark:bg-blue-900/20">Mínima</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[140px] border-l-[3px] border-gray-400 dark:border-gray-600">&nbsp;</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[120px] border-l-[3px] border-gray-400 dark:border-gray-600">Status</th>
            <th class="px-2 py-1 text-left font-semibold text-[11px] text-muted-foreground whitespace-nowrap min-w-[180px]">Observação</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading && !items.length">
            <td :colspan="(canSeeFreteResultado ? 23 : 22) + (isAdmin ? 1 : 0)" class="text-center py-8 text-muted-foreground">
              <Loader2 class="size-4 inline animate-spin mr-1.5" /> carregando…
            </td>
          </tr>
          <tr v-else-if="!items.length">
            <td :colspan="(canSeeFreteResultado ? 23 : 22) + (isAdmin ? 1 : 0)" class="text-center py-8 text-muted-foreground">
              <template v-if="tab === 'lookup' && !lookupTerm">
                digite o numero do pedido acima para buscar
              </template>
              <template v-else-if="tab === 'lookup' && historicoDisponivel">
                <!-- a CTA "buscar no historico" ja foi exibida acima -->
                snapshot vazio para este pedido
              </template>
              <template v-else-if="tab === 'lookup'">
                nenhum item encontrado para o pedido <span class="font-mono">{{ lookupTerm }}</span>
              </template>
              <template v-else>
                sem registros
              </template>
            </td>
          </tr>
          <tr
            v-for="r in items"
            :key="r.bling_order_item_id"
            class="border-t hover:brightness-95 dark:hover:brightness-110"
            :class="platformRowBg(r.plataforma)"
          >
            <td class="px-2 py-1 whitespace-nowrap text-muted-foreground">{{ fmtDate(r.data) }}</td>
            <td class="px-2 py-1 tabular-nums font-medium whitespace-nowrap">{{ r.pedido_bling ?? '—' }}</td>
            <td class="px-2 py-1 tabular-nums text-muted-foreground whitespace-nowrap">{{ r.pedido_marketplace ?? '—' }}</td>
            <td class="px-2 py-1 uppercase whitespace-nowrap border-l-[3px] border-gray-400 dark:border-gray-600">{{ r.plataforma || '—' }}</td>
            <td
              class="px-2 py-1 whitespace-nowrap cursor-help"
              :class="!r.pricing_account_name && !r.pricing_leaf_segment_name ? 'text-red-400' : ''"
              :title="freteProjMissingReason(r) || ''"
            >
              <template v-if="r.pricing_account_name">{{ r.pricing_account_name }}</template>
              <template v-else-if="!r.pricing_leaf_segment_name">⚠️ sem cadastro</template>
              <template v-else-if="r.conta"><span class="text-muted-foreground">{{ r.conta }}</span></template>
              <template v-else><span class="text-amber-500">sem account</span></template>
            </td>
            <td class="px-2 py-1 whitespace-nowrap text-muted-foreground">{{ r.pricing_leaf_segment_name || '—' }}</td>
            <td class="px-2 py-1 font-mono whitespace-nowrap">{{ r.sku || '—' }}</td>
            <td class="px-2 py-1 max-w-[260px] truncate" :title="r.produto || ''">{{ r.produto || '—' }}</td>
            <td class="px-2 py-1 text-right tabular-nums border-l-[3px] border-gray-400 dark:border-gray-600">{{ r.quantidade ?? '—' }}</td>
            <td class="px-2 py-1 text-right tabular-nums text-muted-foreground whitespace-nowrap">{{ brl(r.custo_produto) }}</td>
            <td class="px-2 py-1 text-right tabular-nums whitespace-nowrap bg-amber-50/40 dark:bg-amber-900/10 border-l-[3px] border-gray-400 dark:border-gray-600">{{ brl(r.frete_plataforma) }}</td>
            <td class="px-2 py-1 text-right tabular-nums whitespace-nowrap bg-amber-50/40 dark:bg-amber-900/10">{{ brl(r.frete_anuncio) }}</td>
            <!-- Frete Projetado (restaurado 02/09 — ver comentário no th): a
                 regra de frete da conta; ⚠ vermelho = conta sem cadastro. -->
            <td
              class="px-2 py-1 text-right tabular-nums bg-amber-50/40 dark:bg-amber-900/10 cursor-help whitespace-nowrap"
              :class="r.frete_projetado == null
                ? (!r.pricing_leaf_segment_name ? 'text-red-500' : 'text-amber-500')
                : ''"
              :title="freteProjMissingReason(r) || ''"
            >
              {{ r.frete_projetado != null ? brl(r.frete_projetado) : (!r.pricing_leaf_segment_name ? '⚠️' : '—') }}
            </td>
            <td
              v-if="canSeeFreteResultado"
              class="px-2 py-1 text-right tabular-nums whitespace-nowrap bg-amber-50/40 dark:bg-amber-900/10 font-medium"
              :class="r.resultado_frete != null ? (r.resultado_frete > 0 ? 'text-red-600 dark:text-red-400' : 'text-emerald-600 dark:text-emerald-400') : ''"
            >
              {{ brl(r.resultado_frete) }}
            </td>
            <td class="px-2 py-1 text-right tabular-nums whitespace-nowrap bg-emerald-50/40 dark:bg-emerald-900/10 border-l-[3px] border-gray-400 dark:border-gray-600">
              <!-- Só o repasse REAL do marketplace; sem projeção ≈ (01/09 à
                   noite) — em branco até o financeiro sincronizar. -->
              {{ brl(r.saldo_plataforma) }}
            </td>
            <td class="px-2 py-1 text-right tabular-nums whitespace-nowrap bg-emerald-50/40 dark:bg-emerald-900/10 text-muted-foreground">{{ brl(r.saldo_bling) }}</td>
            <td class="px-2 py-1 whitespace-nowrap bg-emerald-50/40 dark:bg-emerald-900/10 font-medium">
              <div v-if="editingSaldoEfetivo === r.bling_order_item_id" class="flex items-center justify-end">
                <input
                  v-model="saldoEfetivoDraft"
                  type="text"
                  inputmode="decimal"
                  class="w-24 px-2 py-1 text-xs text-right tabular-nums rounded-md border bg-background"
                  autofocus
                  @keydown.enter="saveSaldoEfetivo(r)"
                  @keydown.esc="cancelEditSaldoEfetivo"
                  @blur="saveSaldoEfetivo(r)"
                />
              </div>
              <div v-else class="flex items-center justify-end gap-2">
                <button
                  type="button"
                  class="flex items-center justify-end gap-1 text-right tabular-nums hover:text-foreground disabled:cursor-default"
                  :disabled="!canEdit || !r.pedido_bling || isSyncingSaldoFinal(r.bling_order_item_id) || saldoAncoradoNaPlataforma(r)"
                  :title="saldoAncoradoNaPlataforma(r)
                    ? (r.saldo_plataforma != null
                      ? `Saldo Efetivo = repasse real da plataforma (${(r.plataforma || '').toUpperCase()}) — valor confirmado, sem conciliação nem edição.`
                      : `Em branco até a plataforma (${(r.plataforma || '').toUpperCase()}) confirmar o repasse — preenche sozinho; sem projeção, sem edição. Para liberar antes, use Aprovar.`)
                    : saldoZeradoVisual(r)
                      ? `Saldo Bling × Plataforma divergente — Efetivo exibido como 0 (apenas visual). Valor real: ${brl(saldoEfetivoDisplay(r))}.${canEdit && r.pedido_bling ? ' Clique para editar → grava como final no Bling (zera taxa e frete).' : ''}`
                      : (canEdit && r.pedido_bling ? `Editar Saldo Efetivo → grava o valor como final no Bling (zera taxa e frete)` : '')"
                  @click="startEditSaldoEfetivo(r)"
                >
                  <Loader2 v-if="isSyncingSaldoFinal(r.bling_order_item_id)" class="h-3 w-3 animate-spin inline" />
                  <span :class="saldoZeradoVisual(r) ? 'text-amber-600 dark:text-amber-400' : ''">{{ brl(saldoEfetivoVisual(r)) }}</span>
                  <Pencil v-if="canEdit && !saldoAncoradoNaPlataforma(r)" class="size-3 shrink-0 opacity-50" />
                </button>
              </div>
            </td>
            <td class="px-2 py-1 text-center whitespace-nowrap bg-emerald-50/40 dark:bg-emerald-900/10">
              <button
                type="button"
                class="px-1.5 py-0.5 text-[11px] rounded border bg-background hover:border-primary/50 disabled:opacity-40 disabled:cursor-default"
                :disabled="!r.pedido_bling"
                title="Ver de onde vêm o Saldo Plataforma e o Saldo Bling deste pedido"
                @click="raioXPedido = r.pedido_bling"
              >
                Ver saldo
              </button>
            </td>
            <td class="px-2 py-1 text-right tabular-nums whitespace-nowrap bg-orange-50/40 dark:bg-orange-900/10 border-l-[3px] border-gray-400 dark:border-gray-600">{{ brl(r.ajustes) }}</td>
            <td
              v-if="isAdmin"
              class="px-2 py-1 text-right tabular-nums whitespace-nowrap bg-blue-50/40 dark:bg-blue-900/10 font-medium border-l-[3px] border-gray-400 dark:border-gray-600"
              :class="lucroFinalCls(r)"
            >
              {{ brl(lucroFinal(r)) }}
            </td>
            <td
              class="px-2 py-1 text-right tabular-nums whitespace-nowrap bg-blue-50/40 dark:bg-blue-900/10 font-medium"
              :class="[margemFinalCls(r), !isAdmin && 'border-l-[3px] border-gray-400 dark:border-gray-600']"
            >
              {{ pct(margemFinal(r)) }}
            </td>
            <td class="px-2 py-1 text-right tabular-nums whitespace-nowrap bg-blue-50/40 dark:bg-blue-900/10 text-muted-foreground">
              {{ pct(r.margem_minima) }}
              <span
                v-if="r.data_especial"
                class="ml-1 inline-block rounded bg-amber-100 dark:bg-amber-900/40 px-1 py-px text-[10px] font-medium text-amber-800 dark:text-amber-300 align-middle"
                title="Data Especial do segmento ativa: neste período, margem abaixo da mínima não trava o pedido (configurado em Cadastros → Segmentos)"
              >especial</span>
            </td>
            <td class="px-2 py-1 whitespace-nowrap text-muted-foreground border-l-[3px] border-gray-400 dark:border-gray-600">{{ r.situacao || '—' }}</td>
            <td class="px-2 py-1 border-l-[3px] border-gray-400 dark:border-gray-600">
              <select
                :value="r.status ?? 'Pendente'"
                :disabled="!canEdit || !r.pedido_bling"
                class="pill border text-[11px] font-medium px-2 py-1 rounded-md cursor-pointer disabled:cursor-default disabled:opacity-70"
                :class="statusCls(r.status ?? 'Pendente')"
                @change="(e) => setStatus(r, (e.target as HTMLSelectElement).value as MargensStatus)"
              >
                <option v-for="s in STATUS_OPTIONS" :key="s" :value="s">{{ s }}</option>
              </select>
            </td>
            <td class="px-2 py-1 max-w-[200px]">
              <div v-if="editingObs === r.bling_order_item_id" class="flex items-center gap-1">
                <input
                  v-model="obsDraft"
                  class="flex-1 px-2 py-1 text-xs rounded-md border bg-background"
                  autofocus
                  @keydown.enter="saveObs(r)"
                  @keydown.esc="cancelEditObs"
                />
                <button class="p-1 text-emerald-500 hover:opacity-80" @click="saveObs(r)">
                  <Check class="size-3.5" />
                </button>
                <button class="p-1 text-muted-foreground hover:opacity-80" @click="cancelEditObs">
                  <X class="size-3.5" />
                </button>
              </div>
              <button
                v-else
                class="flex items-center gap-1.5 text-left text-muted-foreground hover:text-foreground w-full truncate disabled:cursor-default"
                :disabled="!canEdit || !r.pedido_bling"
                :title="r.observacao || ''"
                @click="startEditObs(r)"
              >
                <span class="truncate">{{ r.observacao || (canEdit ? 'adicionar…' : '—') }}</span>
                <Pencil v-if="canEdit" class="size-3 shrink-0 opacity-50" />
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Pagination -->
    <div v-if="tab === 'list' && total > PAGE_SIZE" class="flex items-center justify-between gap-2">
      <span class="text-xs text-muted-foreground">
        página {{ page }} de {{ totalPages }} · {{ PAGE_SIZE }}/página
      </span>
      <div class="flex items-center gap-1">
        <Button
          size="sm"
          variant="outline"
          :disabled="page <= 1 || loading"
          @click="page = 1"
        >
          «
        </Button>
        <Button
          size="sm"
          variant="outline"
          :disabled="page <= 1 || loading"
          @click="page = page - 1"
        >
          <ChevronLeft class="size-4" />
        </Button>
        <input
          v-model.number="page"
          type="number"
          :min="1"
          :max="totalPages"
          class="w-16 text-sm text-center rounded-md border bg-background px-2 py-1"
          @change="page = Math.min(Math.max(1, page), totalPages)"
        />
        <Button
          size="sm"
          variant="outline"
          :disabled="page >= totalPages || loading"
          @click="page = page + 1"
        >
          <ChevronRight class="size-4" />
        </Button>
        <Button
          size="sm"
          variant="outline"
          :disabled="page >= totalPages || loading"
          @click="page = totalPages"
        >
          »
        </Button>
      </div>
    </div>

    <MargemSaldoRaioXModal
      :open="raioXPedido !== null"
      :pedido="raioXPedido ?? ''"
      @close="raioXPedido = null"
    />
  </div>
</template>
