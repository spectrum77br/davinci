<script setup lang="ts">
import { TABS_NF } from '~/lib/navGroups'
import { computed, ref, watch } from 'vue'
import { Plus, RefreshCw, X, Trash2, Lock, Eye, EyeOff } from 'lucide-vue-next'

definePageMeta({
  middleware: ['permission'],
  permission: { resource: 'nf_faturador', action: 'view' },
})

const { api } = useApi()
const canEdit = useCan('nf_faturador', 'edit')
const canDelete = useCan('nf_faturador', 'delete')

type Tab = 'faturador' | 'etiqueta' | 'impressao'
const TABS: { key: Tab; label: string }[] = [
  { key: 'faturador', label: 'Faturador' },
  { key: 'etiqueta', label: 'Etiqueta' },
  { key: 'impressao', label: 'Impressão' },
]
const tab = ref<Tab>('faturador')

// ===========================================================================
// FATURADOR
// ===========================================================================
type Faturador = {
  id: string
  nome: string
  modo: string
  nf_cheia: boolean
  percentual: string | number | null
  sku_fonte: string | null
  nome_fonte: string | null
  ncm: string | null
  ads_power: string | null
  usuario: string | null
  has_senha: boolean
  observacao: string | null
  sort_order: number
}
type FatForm = {
  nome: string; modo: string; nf_cheia: boolean; percentual: string
  sku_fonte: string; nome_fonte: string; ncm: string; ads_power: string
  usuario: string; senha: string; observacao: string; sort_order: string
}
function emptyFat(): FatForm {
  return { nome: '', modo: 'bling', nf_cheia: false, percentual: '', sku_fonte: '', nome_fonte: '', ncm: '', ads_power: '', usuario: '', senha: '', observacao: '', sort_order: '' }
}
const MODO_OPTIONS = [
  { value: 'bling', label: 'Bling (outra conta)' },
  { value: 'upseller', label: 'Upseller (serviço)' },
]
const SKU_FONTE_OPTIONS = [
  { value: '', label: '—' },
  { value: 'principal', label: 'SKU do produto (principal)' },
  { value: 'a001', label: 'a001' },
]
const NOME_FONTE_OPTIONS = [
  { value: '', label: '—' },
  { value: 'produto', label: 'Nome do produto (principal)' },
  { value: 'embalagem', label: 'embalagem' },
]

const faturadores = ref<Faturador[]>([])
const fatForm = ref<FatForm>(emptyFat())
const fatEditing = ref<Faturador | null>(null)
const fatShowNew = ref(false)
// Senha: campo mascarado sem acionar o gerenciador de senhas do navegador.
// senhaVisible alterna o disco; ao revelar uma senha JÁ salva busca o valor
// descriptografado no backend (a lista nunca devolve a senha por segurança).
const senhaVisible = ref(false)
const senhaRevealing = ref(false)

async function toggleSenha() {
  if (senhaVisible.value) { senhaVisible.value = false; return }
  if (canEdit.value && fatEditing.value?.has_senha && !fatForm.value.senha) {
    senhaRevealing.value = true
    try {
      const r = await api<{ senha: string }>(`/api/nf-cadastro/faturadores/${fatEditing.value.id}/senha`)
      fatForm.value.senha = r.senha || ''
    } catch (e: any) { saveErr.value = errCode(e); return }
    finally { senhaRevealing.value = false }
  }
  senhaVisible.value = true
}

function buildFatBody(f: FatForm) {
  return {
    nome: f.nome.trim(),
    modo: f.modo,
    nf_cheia: f.nf_cheia,
    percentual: f.percentual.trim() ? Number(f.percentual.replace(',', '.')) : null,
    sku_fonte: f.sku_fonte || null,
    nome_fonte: f.nome_fonte || null,
    ncm: f.ncm.trim() || null,
    ads_power: f.ads_power.trim() || null,
    usuario: f.usuario.trim() || null,
    observacao: f.observacao.trim() || null,
    sort_order: f.sort_order.trim() ? Number(f.sort_order) : 0,
  }
}
function openFatNew() { fatForm.value = emptyFat(); saveErr.value = null; senhaVisible.value = false; fatShowNew.value = true }
function openFatEdit(f: Faturador) {
  fatEditing.value = f
  senhaVisible.value = false
  fatForm.value = {
    nome: f.nome, modo: f.modo, nf_cheia: f.nf_cheia,
    percentual: f.percentual != null ? String(f.percentual) : '',
    sku_fonte: f.sku_fonte || '', nome_fonte: f.nome_fonte || '', ncm: f.ncm || '',
    ads_power: f.ads_power || '', usuario: f.usuario || '', senha: '',
    observacao: f.observacao || '', sort_order: String(f.sort_order ?? 0),
  }
  saveErr.value = null
}
async function createFat() {
  if (!fatForm.value.nome.trim()) { saveErr.value = 'preencha o nome'; return }
  saving.value = true; saveErr.value = null
  try {
    const body: any = buildFatBody(fatForm.value)
    if (fatForm.value.senha) body.senha = fatForm.value.senha
    await api('/api/nf-cadastro/faturadores', { method: 'POST', body })
    fatShowNew.value = false; await loadFaturadores()
  } catch (e: any) { saveErr.value = errCode(e) } finally { saving.value = false }
}
async function saveFat() {
  if (!fatEditing.value) return
  saving.value = true; saveErr.value = null
  try {
    const body: any = buildFatBody(fatForm.value)
    if (fatForm.value.senha) body.senha = fatForm.value.senha
    await api(`/api/nf-cadastro/faturadores/${fatEditing.value.id}`, { method: 'PATCH', body })
    fatEditing.value = null; await loadFaturadores()
  } catch (e: any) { saveErr.value = errCode(e) } finally { saving.value = false }
}
async function removeFat() {
  if (!fatEditing.value) return
  if (!confirm('Remover este faturador?')) return
  saving.value = true; saveErr.value = null
  try {
    await api(`/api/nf-cadastro/faturadores/${fatEditing.value.id}`, { method: 'DELETE' })
    fatEditing.value = null; await loadFaturadores()
  } catch (e: any) { saveErr.value = errCode(e) } finally { saving.value = false }
}
async function loadFaturadores() {
  loading.value = true; error.value = null
  try { faturadores.value = await api<Faturador[]>('/api/nf-cadastro/faturadores') }
  catch (e: any) { error.value = errCode(e) } finally { loading.value = false }
}
const sortedFaturadores = computed(() =>
  [...faturadores.value].sort((a, b) =>
    a.sort_order !== b.sort_order ? a.sort_order - b.sort_order
      : (a.nome || '').localeCompare(b.nome || '', 'pt-BR', { sensitivity: 'base', numeric: true })),
)
function fmtPercentual(v: string | number | null) {
  if (v == null || v === '') return '—'
  const n = Number(v)
  if (Number.isNaN(n)) return '—'
  return `${n.toLocaleString('pt-BR', { maximumFractionDigits: 3 })}%`
}
function modoLabel(m: string) { return MODO_OPTIONS.find((o) => o.value === m)?.label || m }

// ===========================================================================
// ETIQUETA
// ===========================================================================
type Etiqueta = {
  id: string; plataforma: string; modo: string | null; ads_power: string | null
  observacao: string | null; sort_order: number
}
type EtqForm = { plataforma: string; modo: string; ads_power: string; observacao: string; sort_order: string }
function emptyEtq(): EtqForm { return { plataforma: '', modo: '', ads_power: '', observacao: '', sort_order: '' } }
const ETQ_MODO_OPTIONS = [
  { value: '', label: '— (sem regra ainda)' },
  { value: 'amazon', label: 'Amazon (site + agenda)' },
  { value: 'upseller', label: 'Upseller' },
]
const etiquetas = ref<Etiqueta[]>([])
const etqForm = ref<EtqForm>(emptyEtq())
const etqEditing = ref<Etiqueta | null>(null)
const etqShowNew = ref(false)

function buildEtqBody(f: EtqForm) {
  return {
    plataforma: f.plataforma.trim(),
    modo: f.modo || null,
    ads_power: f.ads_power.trim() || null,
    observacao: f.observacao.trim() || null,
    sort_order: f.sort_order.trim() ? Number(f.sort_order) : 0,
  }
}
function openEtqNew() { etqForm.value = emptyEtq(); saveErr.value = null; etqShowNew.value = true }
function openEtqEdit(e: Etiqueta) {
  etqEditing.value = e
  etqForm.value = { plataforma: e.plataforma, modo: e.modo || '', ads_power: e.ads_power || '', observacao: e.observacao || '', sort_order: String(e.sort_order ?? 0) }
  saveErr.value = null
}
async function createEtq() {
  if (!etqForm.value.plataforma.trim()) { saveErr.value = 'preencha a plataforma'; return }
  saving.value = true; saveErr.value = null
  try { await api('/api/nf-cadastro/etiquetas', { method: 'POST', body: buildEtqBody(etqForm.value) }); etqShowNew.value = false; await loadEtiquetas() }
  catch (e: any) { saveErr.value = errCode(e) } finally { saving.value = false }
}
async function saveEtq() {
  if (!etqEditing.value) return
  saving.value = true; saveErr.value = null
  try { await api(`/api/nf-cadastro/etiquetas/${etqEditing.value.id}`, { method: 'PATCH', body: buildEtqBody(etqForm.value) }); etqEditing.value = null; await loadEtiquetas() }
  catch (e: any) { saveErr.value = errCode(e) } finally { saving.value = false }
}
async function removeEtq() {
  if (!etqEditing.value) return
  if (!confirm('Remover esta etiqueta?')) return
  saving.value = true; saveErr.value = null
  try { await api(`/api/nf-cadastro/etiquetas/${etqEditing.value.id}`, { method: 'DELETE' }); etqEditing.value = null; await loadEtiquetas() }
  catch (e: any) { saveErr.value = errCode(e) } finally { saving.value = false }
}
async function loadEtiquetas() {
  loading.value = true; error.value = null
  try { etiquetas.value = await api<Etiqueta[]>('/api/nf-cadastro/etiquetas') }
  catch (e: any) { error.value = errCode(e) } finally { loading.value = false }
}
const sortedEtiquetas = computed(() =>
  [...etiquetas.value].sort((a, b) =>
    a.sort_order !== b.sort_order ? a.sort_order - b.sort_order
      : (a.plataforma || '').localeCompare(b.plataforma || '', 'pt-BR', { sensitivity: 'base', numeric: true })),
)
function etqModoLabel(m: string | null) { return ETQ_MODO_OPTIONS.find((o) => o.value === (m || ''))?.label || m || '—' }

// ===========================================================================
// IMPRESSÃO
// ===========================================================================
type Impressao = {
  id: string; tipo: string; observacao: string | null; visualizacao: string | null
  usa_etiqueta: boolean; usa_declaracao: boolean; usa_nota: boolean; sort_order: number
}
type ImpForm = {
  tipo: string; observacao: string; visualizacao: string
  usa_etiqueta: boolean; usa_declaracao: boolean; usa_nota: boolean; sort_order: string
}
function emptyImp(): ImpForm { return { tipo: '', observacao: '', visualizacao: '', usa_etiqueta: false, usa_declaracao: false, usa_nota: false, sort_order: '' } }
const impressoes = ref<Impressao[]>([])
const impForm = ref<ImpForm>(emptyImp())
const impEditing = ref<Impressao | null>(null)
const impShowNew = ref(false)

function buildImpBody(f: ImpForm) {
  return {
    tipo: f.tipo.trim(),
    observacao: f.observacao.trim() || null,
    visualizacao: f.visualizacao.trim() || null,
    usa_etiqueta: f.usa_etiqueta,
    usa_declaracao: f.usa_declaracao,
    usa_nota: f.usa_nota,
    sort_order: f.sort_order.trim() ? Number(f.sort_order) : 0,
  }
}
function openImpNew() { impForm.value = emptyImp(); saveErr.value = null; impShowNew.value = true }
function openImpEdit(i: Impressao) {
  impEditing.value = i
  impForm.value = { tipo: i.tipo, observacao: i.observacao || '', visualizacao: i.visualizacao || '', usa_etiqueta: i.usa_etiqueta, usa_declaracao: i.usa_declaracao, usa_nota: i.usa_nota, sort_order: String(i.sort_order ?? 0) }
  saveErr.value = null
}
async function createImp() {
  if (!impForm.value.tipo.trim()) { saveErr.value = 'preencha o tipo'; return }
  saving.value = true; saveErr.value = null
  try { await api('/api/nf-cadastro/impressoes', { method: 'POST', body: buildImpBody(impForm.value) }); impShowNew.value = false; await loadImpressoes() }
  catch (e: any) { saveErr.value = errCode(e) } finally { saving.value = false }
}
async function saveImp() {
  if (!impEditing.value) return
  saving.value = true; saveErr.value = null
  try { await api(`/api/nf-cadastro/impressoes/${impEditing.value.id}`, { method: 'PATCH', body: buildImpBody(impForm.value) }); impEditing.value = null; await loadImpressoes() }
  catch (e: any) { saveErr.value = errCode(e) } finally { saving.value = false }
}
async function removeImp() {
  if (!impEditing.value) return
  if (!confirm('Remover esta impressão?')) return
  saving.value = true; saveErr.value = null
  try { await api(`/api/nf-cadastro/impressoes/${impEditing.value.id}`, { method: 'DELETE' }); impEditing.value = null; await loadImpressoes() }
  catch (e: any) { saveErr.value = errCode(e) } finally { saving.value = false }
}
async function loadImpressoes() {
  loading.value = true; error.value = null
  try { impressoes.value = await api<Impressao[]>('/api/nf-cadastro/impressoes') }
  catch (e: any) { error.value = errCode(e) } finally { loading.value = false }
}
const sortedImpressoes = computed(() =>
  [...impressoes.value].sort((a, b) =>
    a.sort_order !== b.sort_order ? a.sort_order - b.sort_order
      : (a.tipo || '').localeCompare(b.tipo || '', 'pt-BR', { sensitivity: 'base', numeric: true })),
)

// ===========================================================================
// Shared
// ===========================================================================
const loading = ref(false)
const error = ref<string | null>(null)
const saving = ref(false)
const saveErr = ref<string | null>(null)
function errCode(e: any) { return e?.data?.detail?.code || e?.message || 'erro' }

async function loadCurrent() {
  if (tab.value === 'faturador') return loadFaturadores()
  if (tab.value === 'etiqueta') return loadEtiquetas()
  return loadImpressoes()
}
watch(tab, () => { void loadCurrent() })
await loadFaturadores()
</script>

<template>
  <div class="space-y-4">
    <RouteTabs :tabs="TABS_NF" />
    <div class="flex flex-wrap items-center gap-3">
      <h1 class="text-2xl font-semibold">NF — Cadastros</h1>
      <Button size="sm" variant="ghost" :disabled="loading" @click="loadCurrent">
        <RefreshCw class="size-4 mr-1" /> recarregar
      </Button>
    </div>

    <!-- Tabs -->
    <div class="flex gap-1 border-b">
      <button
        v-for="t in TABS"
        :key="t.key"
        class="px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors"
        :class="tab === t.key ? 'border-primary text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground'"
        @click="tab = t.key"
      >
        {{ t.label }}
      </button>
    </div>

    <div v-if="error" class="text-sm text-red-500">erro: {{ error }}</div>

    <!-- ==================== FATURADOR ==================== -->
    <div v-if="tab === 'faturador'" class="space-y-3">
      <div class="flex items-center">
        <p class="text-sm text-muted-foreground">
          Emissores de NF. Cada linha é um tipo (bling avulso, exclusivo, upseller…). Lista aberta —
          a regra de emissão é programada depois.
        </p>
        <Button v-if="canEdit" size="sm" class="ml-auto shrink-0" @click="openFatNew">
          <Plus class="size-4 mr-1" /> Novo faturador
        </Button>
      </div>

      <div class="hidden md:block border rounded-md overflow-x-auto">
        <table class="w-full text-sm min-w-[900px] border-collapse [&_th]:border [&_td]:border [&_th]:border-border [&_td]:border-border">
          <thead class="bg-muted/40 text-left">
            <tr class="whitespace-nowrap">
              <th class="px-3 py-2">Nome</th>
              <th class="px-3 py-2">Modo</th>
              <th class="px-3 py-2 text-center">NF cheia</th>
              <th class="px-3 py-2 text-right">%</th>
              <th class="px-3 py-2">SKU</th>
              <th class="px-3 py-2">Nome produto</th>
              <th class="px-3 py-2">NCM</th>
              <th class="px-3 py-2">AdsPower</th>
              <th class="px-3 py-2">Usuário</th>
              <th class="px-3 py-2 text-center">Senha</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="f in sortedFaturadores" :key="f.id" class="border-t hover:bg-muted/20 cursor-pointer" @click="openFatEdit(f)">
              <td class="px-3 py-2 whitespace-nowrap font-medium">{{ f.nome }}</td>
              <td class="px-3 py-2 whitespace-nowrap text-muted-foreground">{{ modoLabel(f.modo) }}</td>
              <td class="px-3 py-2 text-center">{{ f.nf_cheia ? 'Sim' : '—' }}</td>
              <td class="px-3 py-2 whitespace-nowrap text-right font-mono">{{ fmtPercentual(f.percentual) }}</td>
              <td class="px-3 py-2 whitespace-nowrap text-muted-foreground">{{ f.sku_fonte || '—' }}</td>
              <td class="px-3 py-2 whitespace-nowrap text-muted-foreground">{{ f.nome_fonte || '—' }}</td>
              <td class="px-3 py-2 whitespace-nowrap font-mono text-muted-foreground">{{ f.ncm || '—' }}</td>
              <td class="px-3 py-2 whitespace-nowrap text-muted-foreground">{{ f.ads_power || '—' }}</td>
              <td class="px-3 py-2 whitespace-nowrap text-muted-foreground">{{ f.usuario || '—' }}</td>
              <td class="px-3 py-2 text-center">
                <Lock v-if="f.has_senha" class="size-4 inline text-emerald-500" />
                <span v-else class="text-muted-foreground">—</span>
              </td>
            </tr>
            <tr v-if="!loading && faturadores.length === 0">
              <td colspan="10" class="px-3 py-6 text-center text-muted-foreground">nenhum faturador</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="md:hidden space-y-2">
        <div v-for="f in sortedFaturadores" :key="f.id" class="border rounded-md p-3 space-y-2 hover:bg-muted/20 cursor-pointer" @click="openFatEdit(f)">
          <div class="flex items-start gap-2">
            <div class="flex-1 min-w-0">
              <div class="font-medium truncate">{{ f.nome }}</div>
              <div class="text-xs text-muted-foreground truncate">{{ modoLabel(f.modo) }}</div>
            </div>
            <Lock v-if="f.has_senha" class="size-4 shrink-0 text-emerald-500" />
          </div>
          <div class="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
            <div><span class="text-muted-foreground">NF cheia:</span> {{ f.nf_cheia ? 'Sim' : '—' }}</div>
            <div><span class="text-muted-foreground">%:</span> {{ fmtPercentual(f.percentual) }}</div>
            <div><span class="text-muted-foreground">SKU:</span> {{ f.sku_fonte || '—' }}</div>
            <div><span class="text-muted-foreground">Nome:</span> {{ f.nome_fonte || '—' }}</div>
            <div><span class="text-muted-foreground">NCM:</span> {{ f.ncm || '—' }}</div>
            <div><span class="text-muted-foreground">AdsPower:</span> {{ f.ads_power || '—' }}</div>
          </div>
        </div>
        <div v-if="!loading && faturadores.length === 0" class="text-center text-sm text-muted-foreground py-6 border rounded-md">nenhum faturador</div>
      </div>
    </div>

    <!-- ==================== ETIQUETA ==================== -->
    <div v-else-if="tab === 'etiqueta'" class="space-y-3">
      <div class="flex items-center">
        <p class="text-sm text-muted-foreground">
          Onde a NF já emitida é inserida na plataforma p/ liberar a etiqueta. Só a Amazon tem regra
          própria (site + agenda); o resto vai pelo Upseller.
        </p>
        <Button v-if="canEdit" size="sm" class="ml-auto shrink-0" @click="openEtqNew">
          <Plus class="size-4 mr-1" /> Nova etiqueta
        </Button>
      </div>

      <div class="hidden md:block border rounded-md overflow-x-auto">
        <table class="w-full text-sm min-w-[600px] border-collapse [&_th]:border [&_td]:border [&_th]:border-border [&_td]:border-border">
          <thead class="bg-muted/40 text-left">
            <tr class="whitespace-nowrap">
              <th class="px-3 py-2">Plataforma</th>
              <th class="px-3 py-2">Regra</th>
              <th class="px-3 py-2">AdsPower</th>
              <th class="px-3 py-2">Observação</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="e in sortedEtiquetas" :key="e.id" class="border-t hover:bg-muted/20 cursor-pointer" @click="openEtqEdit(e)">
              <td class="px-3 py-2 whitespace-nowrap font-medium">{{ e.plataforma }}</td>
              <td class="px-3 py-2 whitespace-nowrap text-muted-foreground">{{ etqModoLabel(e.modo) }}</td>
              <td class="px-3 py-2 whitespace-nowrap text-muted-foreground">{{ e.ads_power || '—' }}</td>
              <td class="px-3 py-2 text-muted-foreground">{{ e.observacao || '—' }}</td>
            </tr>
            <tr v-if="!loading && etiquetas.length === 0">
              <td colspan="4" class="px-3 py-6 text-center text-muted-foreground">nenhuma etiqueta</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="md:hidden space-y-2">
        <div v-for="e in sortedEtiquetas" :key="e.id" class="border rounded-md p-3 space-y-1 hover:bg-muted/20 cursor-pointer" @click="openEtqEdit(e)">
          <div class="font-medium">{{ e.plataforma }}</div>
          <div class="text-xs text-muted-foreground">{{ etqModoLabel(e.modo) }}</div>
          <div v-if="e.ads_power" class="text-xs"><span class="text-muted-foreground">AdsPower:</span> {{ e.ads_power }}</div>
          <div v-if="e.observacao" class="text-xs break-words">{{ e.observacao }}</div>
        </div>
        <div v-if="!loading && etiquetas.length === 0" class="text-center text-sm text-muted-foreground py-6 border rounded-md">nenhuma etiqueta</div>
      </div>
    </div>

    <!-- ==================== IMPRESSÃO ==================== -->
    <div v-else class="space-y-3">
      <div class="flex items-center">
        <p class="text-sm text-muted-foreground">
          Como a etiqueta é impressa depois da NF. Tipos: agência (etiqueta+declaração), correios
          (etiqueta+NF) e próprio (Melhor Envio, só Amazon). Uma loja pode ter mais de um.
        </p>
        <Button v-if="canEdit" size="sm" class="ml-auto shrink-0" @click="openImpNew">
          <Plus class="size-4 mr-1" /> Nova impressão
        </Button>
      </div>

      <div class="hidden md:block border rounded-md overflow-x-auto">
        <table class="w-full text-sm min-w-[720px] border-collapse [&_th]:border [&_td]:border [&_th]:border-border [&_td]:border-border">
          <thead class="bg-muted/40 text-left">
            <tr class="whitespace-nowrap">
              <th class="px-3 py-2">Tipo</th>
              <th class="px-3 py-2 text-center">Etiqueta</th>
              <th class="px-3 py-2 text-center">Declaração</th>
              <th class="px-3 py-2 text-center">Nota</th>
              <th class="px-3 py-2">Visualização</th>
              <th class="px-3 py-2">Regra</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="i in sortedImpressoes" :key="i.id" class="border-t hover:bg-muted/20 cursor-pointer" @click="openImpEdit(i)">
              <td class="px-3 py-2 whitespace-nowrap font-medium">{{ i.tipo }}</td>
              <td class="px-3 py-2 text-center">{{ i.usa_etiqueta ? 'x' : '—' }}</td>
              <td class="px-3 py-2 text-center">{{ i.usa_declaracao ? 'x' : '—' }}</td>
              <td class="px-3 py-2 text-center">{{ i.usa_nota ? 'x' : '—' }}</td>
              <td class="px-3 py-2 text-muted-foreground">{{ i.visualizacao || '—' }}</td>
              <td class="px-3 py-2 text-muted-foreground">{{ i.observacao || '—' }}</td>
            </tr>
            <tr v-if="!loading && impressoes.length === 0">
              <td colspan="6" class="px-3 py-6 text-center text-muted-foreground">nenhuma impressão</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="md:hidden space-y-2">
        <div v-for="i in sortedImpressoes" :key="i.id" class="border rounded-md p-3 space-y-1 hover:bg-muted/20 cursor-pointer" @click="openImpEdit(i)">
          <div class="font-medium">{{ i.tipo }}</div>
          <div class="flex gap-3 text-xs text-muted-foreground">
            <span>Etiqueta: {{ i.usa_etiqueta ? 'x' : '—' }}</span>
            <span>Declaração: {{ i.usa_declaracao ? 'x' : '—' }}</span>
            <span>Nota: {{ i.usa_nota ? 'x' : '—' }}</span>
          </div>
          <div v-if="i.visualizacao" class="text-xs break-words"><span class="text-muted-foreground">Visualização:</span> {{ i.visualizacao }}</div>
          <div v-if="i.observacao" class="text-xs break-words"><span class="text-muted-foreground">Regra:</span> {{ i.observacao }}</div>
        </div>
        <div v-if="!loading && impressoes.length === 0" class="text-center text-sm text-muted-foreground py-6 border rounded-md">nenhuma impressão</div>
      </div>
    </div>

    <!-- ==================== FATURADOR modals ==================== -->
    <div v-if="fatShowNew || fatEditing" class="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" @click.self="fatShowNew = false; fatEditing = null">
      <div class="bg-background border rounded-lg w-full max-w-lg p-5 space-y-4 max-h-[90vh] overflow-y-auto">
        <div class="flex items-center">
          <h2 class="text-lg font-semibold">{{ fatEditing ? 'Editar faturador' : 'Novo faturador' }}</h2>
          <Button class="ml-auto" size="sm" variant="ghost" @click="fatShowNew = false; fatEditing = null"><X class="size-4" /></Button>
        </div>
        <div class="space-y-3">
          <div><Label>Nome *</Label><Input v-model="fatForm.nome" placeholder="ex: Bling Avulso" /></div>
          <div class="grid grid-cols-2 gap-3">
            <div><Label>Modo</Label>
              <select v-model="fatForm.modo" class="w-full rounded-md border bg-background px-2 py-1.5 text-sm">
                <option v-for="o in MODO_OPTIONS" :key="o.value" :value="o.value">{{ o.label }}</option>
              </select>
            </div>
            <div><Label>Percentual (%)</Label><Input v-model="fatForm.percentual" inputmode="decimal" placeholder="ex: 2 ou 0,1" /></div>
          </div>
          <label class="flex items-center gap-2 text-sm"><input v-model="fatForm.nf_cheia" type="checkbox" class="size-4" /> NF cheia (valor integral)</label>
          <div class="grid grid-cols-2 gap-3">
            <div><Label>SKU</Label>
              <select v-model="fatForm.sku_fonte" class="w-full rounded-md border bg-background px-2 py-1.5 text-sm">
                <option v-for="o in SKU_FONTE_OPTIONS" :key="o.value" :value="o.value">{{ o.label }}</option>
              </select>
            </div>
            <div><Label>Nome do produto</Label>
              <select v-model="fatForm.nome_fonte" class="w-full rounded-md border bg-background px-2 py-1.5 text-sm">
                <option v-for="o in NOME_FONTE_OPTIONS" :key="o.value" :value="o.value">{{ o.label }}</option>
              </select>
            </div>
          </div>
          <div class="grid grid-cols-2 gap-3">
            <div><Label>NCM</Label><Input v-model="fatForm.ncm" placeholder="ex: 4202.12.10" /></div>
            <div><Label>AdsPower</Label><Input v-model="fatForm.ads_power" placeholder="perfil AdsPower" /></div>
          </div>
          <div class="grid grid-cols-2 gap-3">
            <div><Label>Usuário</Label><Input v-model="fatForm.usuario" autocomplete="off" /></div>
            <div><Label>Senha {{ fatEditing?.has_senha ? '(salva)' : '' }}</Label>
              <div class="relative">
                <Input
                  v-model="fatForm.senha"
                  type="text"
                  autocomplete="off"
                  data-1p-ignore
                  data-lpignore="true"
                  data-form-type="other"
                  name="nf-faturador-secret"
                  class="pr-9"
                  :style="senhaVisible ? undefined : { WebkitTextSecurity: 'disc' }"
                  :placeholder="fatEditing ? 'branco = manter' : ''"
                />
                <button
                  type="button"
                  class="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground disabled:opacity-50"
                  :disabled="senhaRevealing"
                  :title="senhaVisible ? 'ocultar' : 'ver senha salva'"
                  @click="toggleSenha"
                >
                  <EyeOff v-if="senhaVisible" class="size-4" />
                  <Eye v-else class="size-4" />
                </button>
              </div>
            </div>
          </div>
          <div><Label>Observação</Label><textarea v-model="fatForm.observacao" rows="2" class="w-full rounded-md border bg-background px-2 py-1.5 text-sm resize-y" /></div>
          <div><Label>Ordem</Label><Input v-model="fatForm.sort_order" inputmode="numeric" placeholder="0" /></div>
        </div>
        <div v-if="saveErr" class="text-sm text-red-500">erro: {{ saveErr }}</div>
        <div class="flex justify-end gap-2">
          <Button v-if="fatEditing && canDelete" variant="ghost" :disabled="saving" class="text-red-500 mr-auto" @click="removeFat"><Trash2 class="size-4 mr-1" /> remover</Button>
          <Button variant="ghost" :disabled="saving" @click="fatShowNew = false; fatEditing = null">{{ canEdit ? 'cancelar' : 'fechar' }}</Button>
          <Button v-if="canEdit" :disabled="saving" @click="fatEditing ? saveFat() : createFat()">{{ saving ? 'salvando…' : (fatEditing ? 'Salvar' : 'Criar') }}</Button>
        </div>
      </div>
    </div>

    <!-- ==================== ETIQUETA modals ==================== -->
    <div v-if="etqShowNew || etqEditing" class="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" @click.self="etqShowNew = false; etqEditing = null">
      <div class="bg-background border rounded-lg w-full max-w-md p-5 space-y-4 max-h-[90vh] overflow-y-auto">
        <div class="flex items-center">
          <h2 class="text-lg font-semibold">{{ etqEditing ? 'Editar etiqueta' : 'Nova etiqueta' }}</h2>
          <Button class="ml-auto" size="sm" variant="ghost" @click="etqShowNew = false; etqEditing = null"><X class="size-4" /></Button>
        </div>
        <div class="space-y-3">
          <div><Label>Plataforma *</Label><Input v-model="etqForm.plataforma" placeholder="ex: amazon, ml, shopee" /></div>
          <div><Label>Regra</Label>
            <select v-model="etqForm.modo" class="w-full rounded-md border bg-background px-2 py-1.5 text-sm">
              <option v-for="o in ETQ_MODO_OPTIONS" :key="o.value" :value="o.value">{{ o.label }}</option>
            </select>
          </div>
          <div><Label>AdsPower</Label><Input v-model="etqForm.ads_power" placeholder="usado no modo Amazon" /></div>
          <div><Label>Observação</Label><textarea v-model="etqForm.observacao" rows="2" class="w-full rounded-md border bg-background px-2 py-1.5 text-sm resize-y" /></div>
          <div><Label>Ordem</Label><Input v-model="etqForm.sort_order" inputmode="numeric" placeholder="0" /></div>
        </div>
        <div v-if="saveErr" class="text-sm text-red-500">erro: {{ saveErr }}</div>
        <div class="flex justify-end gap-2">
          <Button v-if="etqEditing && canDelete" variant="ghost" :disabled="saving" class="text-red-500 mr-auto" @click="removeEtq"><Trash2 class="size-4 mr-1" /> remover</Button>
          <Button variant="ghost" :disabled="saving" @click="etqShowNew = false; etqEditing = null">{{ canEdit ? 'cancelar' : 'fechar' }}</Button>
          <Button v-if="canEdit" :disabled="saving" @click="etqEditing ? saveEtq() : createEtq()">{{ saving ? 'salvando…' : (etqEditing ? 'Salvar' : 'Criar') }}</Button>
        </div>
      </div>
    </div>

    <!-- ==================== IMPRESSÃO modals ==================== -->
    <div v-if="impShowNew || impEditing" class="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" @click.self="impShowNew = false; impEditing = null">
      <div class="bg-background border rounded-lg w-full max-w-md p-5 space-y-4 max-h-[90vh] overflow-y-auto">
        <div class="flex items-center">
          <h2 class="text-lg font-semibold">{{ impEditing ? 'Editar impressão' : 'Nova impressão' }}</h2>
          <Button class="ml-auto" size="sm" variant="ghost" @click="impShowNew = false; impEditing = null"><X class="size-4" /></Button>
        </div>
        <div class="space-y-3">
          <div><Label>Tipo *</Label><Input v-model="impForm.tipo" placeholder="ex: agencia, correios, proprio" /></div>
          <div class="flex flex-wrap gap-4">
            <label class="flex items-center gap-2 text-sm"><input v-model="impForm.usa_etiqueta" type="checkbox" class="size-4" /> Etiqueta</label>
            <label class="flex items-center gap-2 text-sm"><input v-model="impForm.usa_declaracao" type="checkbox" class="size-4" /> Declaração</label>
            <label class="flex items-center gap-2 text-sm"><input v-model="impForm.usa_nota" type="checkbox" class="size-4" /> Nota</label>
          </div>
          <div><Label>Visualização</Label><textarea v-model="impForm.visualizacao" rows="3" placeholder="ex: trocar remetente = destinatário; apagar nº NF, cód. barras, chave; apagar marketplace" class="w-full rounded-md border bg-background px-2 py-1.5 text-sm resize-y" /></div>
          <div><Label>Regra</Label><textarea v-model="impForm.observacao" rows="2" class="w-full rounded-md border bg-background px-2 py-1.5 text-sm resize-y" /></div>
          <div><Label>Ordem</Label><Input v-model="impForm.sort_order" inputmode="numeric" placeholder="0" /></div>
        </div>
        <div v-if="saveErr" class="text-sm text-red-500">erro: {{ saveErr }}</div>
        <div class="flex justify-end gap-2">
          <Button v-if="impEditing && canDelete" variant="ghost" :disabled="saving" class="text-red-500 mr-auto" @click="removeImp"><Trash2 class="size-4 mr-1" /> remover</Button>
          <Button variant="ghost" :disabled="saving" @click="impShowNew = false; impEditing = null">{{ canEdit ? 'cancelar' : 'fechar' }}</Button>
          <Button v-if="canEdit" :disabled="saving" @click="impEditing ? saveImp() : createImp()">{{ saving ? 'salvando…' : (impEditing ? 'Salvar' : 'Criar') }}</Button>
        </div>
      </div>
    </div>

  </div>
</template>
