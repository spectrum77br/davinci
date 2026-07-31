<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ChevronLeft, ChevronRight, FileDown, Loader2, RefreshCw, Send, Truck, X } from 'lucide-vue-next'

definePageMeta({
  middleware: ['permission'],
  permission: { resource: 'nf_faturamento', action: 'view' },
})

const { api, url } = useApi()
const toasts = useToasts()
const canEdit = useCan('nf_faturamento', 'edit')

type Row = {
  data: string | null
  pedido_bling: string
  pedido_marketplace: string | null
  plataforma: string | null
  conta: string | null
  status_bling: string | null
  status_faturamento: string
  erro_faturamento: string | null
  status_etiqueta: string
  erro_etiqueta: string | null
  status_impressao: string
  erro_impressao: string | null
}

const rows = ref<Row[]>([])
const loading = ref(false)
const error = ref<string | null>(null)
const dias = ref(7)

async function load() {
  loading.value = true
  error.value = null
  try {
    rows.value = await api<Row[]>(`/api/nf-cadastro/faturamento?dias=${dias.value}&limit=2000`)
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'Erro ao carregar'
  } finally {
    loading.value = false
  }
}
load()
watch(dias, () => load())

// -- Filtros client-side ----------------------------------------------------
const search = ref('')
const plataformaFilter = ref('')
const contaFilter = ref('')

const plataformas = computed(() =>
  Array.from(new Set(rows.value.map((r) => r.plataforma).filter((x): x is string => !!x))).sort(),
)
const contas = computed(() =>
  Array.from(new Set(rows.value.map((r) => r.conta).filter((x): x is string => !!x))).sort(),
)

const filteredRows = computed(() => {
  const q = search.value.trim().toLowerCase()
  return rows.value.filter((r) => {
    if (plataformaFilter.value && r.plataforma !== plataformaFilter.value) return false
    if (contaFilter.value && r.conta !== contaFilter.value) return false
    if (q) {
      const hay = [
        r.pedido_bling,
        r.pedido_marketplace,
        r.plataforma,
        r.conta,
        r.status_bling,
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase()
      if (!hay.includes(q)) return false
    }
    return true
  })
})

function limparFiltros() {
  search.value = ''
  plataformaFilter.value = ''
  contaFilter.value = ''
}

// -- Seleção + gerar planilha ----------------------------------------------
const selected = ref<Set<string>>(new Set())
const gerando = ref(false)

function toggle(numero: string) {
  const s = new Set(selected.value)
  if (s.has(numero)) s.delete(numero)
  else s.add(numero)
  selected.value = s
}

const allFilteredSelected = computed(
  () => filteredRows.value.length > 0 && filteredRows.value.every((r) => selected.value.has(r.pedido_bling)),
)
function toggleAll() {
  const s = new Set(selected.value)
  if (allFilteredSelected.value) {
    for (const r of filteredRows.value) s.delete(r.pedido_bling)
  } else {
    for (const r of filteredRows.value) s.add(r.pedido_bling)
  }
  selected.value = s
}

function decodePulados(b64: string | null): { numero: string; motivo: string }[] {
  if (!b64) return []
  try {
    return JSON.parse(decodeURIComponent(escape(atob(b64))))
  } catch {
    return []
  }
}

async function gerarPlanilha() {
  const numeros = Array.from(selected.value)
  if (!numeros.length) {
    toasts.warning('Selecione ao menos um pedido')
    return
  }
  gerando.value = true
  try {
    const resp = await fetch(url('/api/nf-cadastro/faturamento/gerar-planilha'), {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ numeros }),
    })
    if (!resp.ok) {
      let code = `HTTP ${resp.status}`
      try {
        code = (await resp.json())?.detail?.code || code
      } catch {}
      toasts.error('Não foi possível gerar a planilha', code)
      return
    }
    const blob = await resp.blob()
    const ok = resp.headers.get('X-Pedidos-Ok') || '0'
    const pulados = resp.headers.get('X-Pedidos-Pulados') || '0'
    const detalhe = decodePulados(resp.headers.get('X-Pedidos-Pulados-Detalhe'))

    const cd = resp.headers.get('Content-Disposition') || ''
    const m = cd.match(/filename="?([^"]+)"?/)
    const fname = m?.[1] || 'nf_avulsa.csv'

    const link = document.createElement('a')
    link.href = URL.createObjectURL(blob)
    link.download = fname
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(link.href)

    if (Number(pulados) > 0) {
      toasts.warning(
        `${ok} pedido(s) na planilha · ${pulados} pulado(s)`,
        detalhe.map((d) => `${d.numero}: ${d.motivo}`),
      )
    } else {
      toasts.success(`Planilha gerada com ${ok} pedido(s)`)
    }
  } catch (e: any) {
    toasts.error('Não foi possível gerar a planilha', e?.message || 'erro')
  } finally {
    gerando.value = false
  }
}

// -- Enfileirar importação (marionete) -------------------------------------
const enfileirando = ref(false)

async function enfileirar() {
  const numeros = Array.from(selected.value)
  if (!numeros.length) {
    toasts.warning('Selecione ao menos um pedido')
    return
  }
  if (!confirm(`Enfileirar a importação de ${numeros.length} pedido(s)? A marionete vai importar no destino (Bling/Upseller) de cada faturador.`)) {
    return
  }
  enfileirando.value = true
  try {
    const res = await api<{ comandos: number; pedidos_ok: number; pulados: { numero: string; motivo: string }[] }>(
      '/api/nf-cadastro/faturamento/enfileirar',
      { method: 'POST', body: { numeros } },
    )
    if (res.pulados.length > 0) {
      toasts.warning(
        `${res.comandos} comando(s) · ${res.pedidos_ok} pedido(s) · ${res.pulados.length} pulado(s)`,
        res.pulados.map((d) => `${d.numero}: ${d.motivo}`),
      )
    } else {
      toasts.success(`${res.comandos} comando(s) enfileirado(s) com ${res.pedidos_ok} pedido(s)`)
    }
    selected.value = new Set()
    await load()
  } catch (e: any) {
    toasts.error('Não foi possível enfileirar', e?.data?.detail?.code || e?.message || 'erro')
  } finally {
    enfileirando.value = false
  }
}

// -- Conferir frete (Melhor Envio × frete projetado) -----------------------
type ConfereCotacao = {
  servico_id: number | null
  servico_nome: string
  empresa: string
  preco: string | null
  prazo_dias: number | null
  erro: string | null
}
type ConfereResult = {
  libera: boolean
  motivo: string
  menor_frete: string | null
  servico_escolhido: string | null
  empresa_escolhida: string | null
  prazo_dias: number | null
  frete_projetado: string | null
  diferenca: string | null
  cotacoes: ConfereCotacao[]
}
const confereOpen = ref(false)
const conferindo = ref(false)
const confereErro = ref<string | null>(null)
const confereRes = ref<ConfereResult | null>(null)
const confere = ref({
  from_cep: '',
  to_cep: '',
  width: '20',
  height: '10',
  length: '20',
  weight: '1',
  insurance_value: '0',
  frete_projetado: '',
})

function abrirConfere() {
  confereErro.value = null
  confereRes.value = null
  confereOpen.value = true
}

async function conferirFrete() {
  conferindo.value = true
  confereErro.value = null
  confereRes.value = null
  try {
    const body: Record<string, unknown> = {
      from_cep: confere.value.from_cep,
      to_cep: confere.value.to_cep,
      produtos: [
        {
          id: '1',
          width: confere.value.width,
          height: confere.value.height,
          length: confere.value.length,
          weight: confere.value.weight,
          insurance_value: confere.value.insurance_value || '0',
          quantity: 1,
        },
      ],
    }
    if (confere.value.frete_projetado.trim()) {
      body.frete_projetado = confere.value.frete_projetado.replace(',', '.')
    }
    confereRes.value = await api<ConfereResult>('/api/nf-cadastro/faturamento/conferir-frete', {
      method: 'POST',
      body,
    })
  } catch (e: any) {
    confereErro.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    conferindo.value = false
  }
}

const CONFERE_MOTIVO: Record<string, string> = {
  dentro_do_projetado: 'Menor frete cabe no projetado — libera a etiqueta',
  acima_do_projetado: 'Menor frete acima do projetado — não libera',
  sem_cotacao: 'Nenhuma transportadora atende este trecho',
  sem_frete_projetado: 'Informe o frete projetado para decidir',
}
function fmtBrl(v: string | null): string {
  if (v == null) return '—'
  const n = Number(v)
  return Number.isFinite(n) ? n.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' }) : v
}

// -- Paginação --------------------------------------------------------------
const PAGE_SIZE = 50
const page = ref(1)
const totalPages = computed(() => Math.max(1, Math.ceil(filteredRows.value.length / PAGE_SIZE)))
const pagedRows = computed(() =>
  filteredRows.value.slice((page.value - 1) * PAGE_SIZE, page.value * PAGE_SIZE),
)
function goToPage(p: number) {
  page.value = Math.min(Math.max(1, p), totalPages.value)
}
watch([search, plataformaFilter, contaFilter, dias], () => {
  page.value = 1
})

// -- Helpers ----------------------------------------------------------------
function fmtData(d: string | null): string {
  if (!d) return '—'
  const [y, m, dd] = d.slice(0, 10).split('-')
  return `${dd}/${m}/${y}`
}
function badgeClass(status: string): string {
  const s = (status || '').toLowerCase()
  if (s === 'ok') return 'bg-emerald-100 text-emerald-700'
  if (s === 'erro') return 'bg-red-100 text-red-700'
  if (s === 'processando') return 'bg-amber-100 text-amber-700'
  return 'bg-gray-100 text-gray-600'
}
function badgeLabel(status: string): string {
  const s = (status || '').toLowerCase()
  if (s === 'ok') return 'OK'
  if (s === 'erro') return 'Erro'
  if (s === 'processando') return 'Processando'
  if (s === 'pendente') return 'Pendente'
  return status
}
</script>

<template>
  <div class="space-y-4 p-4">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <div>
        <h1 class="text-xl font-semibold">Painel de Faturamento (NF)</h1>
        <p class="text-sm text-muted-foreground">
          Status por etapa (faturamento → etiqueta → impressão) dos pedidos das lojas com cadastro de NF.
        </p>
      </div>
      <div class="flex items-center gap-2">
        <label class="text-sm text-muted-foreground">Janela</label>
        <select v-model.number="dias" class="rounded-md border bg-background px-2 py-1.5 text-sm">
          <option :value="1">Hoje</option>
          <option :value="3">3 dias</option>
          <option :value="7">7 dias</option>
          <option :value="15">15 dias</option>
          <option :value="30">30 dias</option>
          <option :value="60">60 dias</option>
          <option :value="90">90 dias</option>
        </select>
        <button
          class="inline-flex items-center gap-1 rounded-md border px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-50"
          :disabled="loading"
          @click="load"
        >
          <RefreshCw class="h-4 w-4" :class="loading ? 'animate-spin' : ''" />
          Recarregar
        </button>
        <button
          v-if="canEdit"
          class="inline-flex items-center gap-1 rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
          :disabled="gerando || !selected.size"
          @click="gerarPlanilha"
        >
          <FileDown v-if="!gerando" class="h-4 w-4" />
          <Loader2 v-else class="h-4 w-4 animate-spin" />
          Gerar planilha{{ selected.size ? ` (${selected.size})` : '' }}
        </button>
        <button
          v-if="canEdit"
          class="inline-flex items-center gap-1 rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
          :disabled="enfileirando || !selected.size"
          @click="enfileirar"
        >
          <Send v-if="!enfileirando" class="h-4 w-4" />
          <Loader2 v-else class="h-4 w-4 animate-spin" />
          Enfileirar{{ selected.size ? ` (${selected.size})` : '' }}
        </button>
        <button
          v-if="canEdit"
          class="inline-flex items-center gap-1 rounded-md border px-3 py-1.5 text-sm hover:bg-muted"
          @click="abrirConfere"
        >
          <Truck class="h-4 w-4" />
          Conferir frete
        </button>
      </div>
    </div>

    <!-- Filtros -->
    <div class="flex flex-wrap items-center gap-2">
      <input
        v-model="search"
        type="text"
        placeholder="Buscar pedido, conta, status…"
        class="w-64 rounded-md border bg-background px-2 py-1.5 text-sm"
      />
      <select v-model="plataformaFilter" class="rounded-md border bg-background px-2 py-1.5 text-sm">
        <option value="">Todas as plataformas</option>
        <option v-for="p in plataformas" :key="p" :value="p">{{ p }}</option>
      </select>
      <select v-model="contaFilter" class="rounded-md border bg-background px-2 py-1.5 text-sm">
        <option value="">Todas as contas</option>
        <option v-for="c in contas" :key="c" :value="c">{{ c }}</option>
      </select>
      <button
        v-if="search || plataformaFilter || contaFilter"
        class="inline-flex items-center gap-1 rounded-md border px-2 py-1.5 text-sm hover:bg-muted"
        @click="limparFiltros"
      >
        <X class="h-4 w-4" /> Limpar
      </button>
      <span class="text-sm text-muted-foreground">
        {{ filteredRows.length }} de {{ rows.length }}
      </span>
    </div>

    <div v-if="error" class="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
      {{ error }}
    </div>

    <!-- Tabela -->
    <div class="overflow-x-auto rounded-md border">
      <table class="min-w-[1000px] w-full text-sm">
        <thead class="bg-muted/50 text-left">
          <tr>
            <th v-if="canEdit" class="px-3 py-2">
              <input
                type="checkbox"
                :checked="allFilteredSelected"
                :disabled="!filteredRows.length"
                @change="toggleAll"
              />
            </th>
            <th class="px-3 py-2 font-medium">Data</th>
            <th class="px-3 py-2 font-medium">Pedido Bling</th>
            <th class="px-3 py-2 font-medium">Pedido Marketplace</th>
            <th class="px-3 py-2 font-medium">Plataforma</th>
            <th class="px-3 py-2 font-medium">Conta</th>
            <th class="px-3 py-2 font-medium">Status Bling</th>
            <th class="px-3 py-2 font-medium">Faturamento</th>
            <th class="px-3 py-2 font-medium">Etiqueta</th>
            <th class="px-3 py-2 font-medium">Impressão</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading">
            <td :colspan="canEdit ? 10 : 9" class="px-3 py-8 text-center text-muted-foreground">
              <Loader2 class="mx-auto h-5 w-5 animate-spin" />
            </td>
          </tr>
          <tr v-else-if="!filteredRows.length">
            <td :colspan="canEdit ? 10 : 9" class="px-3 py-8 text-center text-muted-foreground">
              Nenhum pedido. As lojas precisam ter um cadastro de NF (Faturador/Etiqueta/Impressão) atribuído na tela Lojas.
            </td>
          </tr>
          <tr v-for="r in pagedRows" :key="r.pedido_bling" class="border-t hover:bg-muted/30">
            <td v-if="canEdit" class="px-3 py-2">
              <input
                type="checkbox"
                :checked="selected.has(r.pedido_bling)"
                @change="toggle(r.pedido_bling)"
              />
            </td>
            <td class="whitespace-nowrap px-3 py-2">{{ fmtData(r.data) }}</td>
            <td class="whitespace-nowrap px-3 py-2 font-medium">{{ r.pedido_bling }}</td>
            <td class="whitespace-nowrap px-3 py-2">{{ r.pedido_marketplace || '—' }}</td>
            <td class="whitespace-nowrap px-3 py-2">{{ r.plataforma || '—' }}</td>
            <td class="whitespace-nowrap px-3 py-2">{{ r.conta || '—' }}</td>
            <td class="whitespace-nowrap px-3 py-2">{{ r.status_bling || '—' }}</td>
            <td class="whitespace-nowrap px-3 py-2">
              <span
                class="rounded px-2 py-0.5 text-xs font-medium"
                :class="badgeClass(r.status_faturamento)"
                :title="r.erro_faturamento || ''"
              >{{ badgeLabel(r.status_faturamento) }}</span>
            </td>
            <td class="whitespace-nowrap px-3 py-2">
              <span
                class="rounded px-2 py-0.5 text-xs font-medium"
                :class="badgeClass(r.status_etiqueta)"
                :title="r.erro_etiqueta || ''"
              >{{ badgeLabel(r.status_etiqueta) }}</span>
            </td>
            <td class="whitespace-nowrap px-3 py-2">
              <span
                class="rounded px-2 py-0.5 text-xs font-medium"
                :class="badgeClass(r.status_impressao)"
                :title="r.erro_impressao || ''"
              >{{ badgeLabel(r.status_impressao) }}</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Paginação -->
    <div v-if="filteredRows.length > PAGE_SIZE" class="flex items-center justify-center gap-2">
      <button
        class="inline-flex items-center rounded-md border px-2 py-1 text-sm hover:bg-muted disabled:opacity-50"
        :disabled="page <= 1"
        @click="goToPage(page - 1)"
      >
        <ChevronLeft class="h-4 w-4" />
      </button>
      <span class="text-sm text-muted-foreground">Página {{ page }} de {{ totalPages }}</span>
      <button
        class="inline-flex items-center rounded-md border px-2 py-1 text-sm hover:bg-muted disabled:opacity-50"
        :disabled="page >= totalPages"
        @click="goToPage(page + 1)"
      >
        <ChevronRight class="h-4 w-4" />
      </button>
    </div>

    <!-- Modal: Conferir frete (Melhor Envio) -->
    <div
      v-if="confereOpen"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      @click.self="confereOpen = false"
    >
      <div class="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-lg bg-background p-4 shadow-lg">
        <div class="mb-3 flex items-center justify-between">
          <h2 class="text-lg font-semibold">Conferir frete (Melhor Envio)</h2>
          <button class="rounded p-1 hover:bg-muted" @click="confereOpen = false">
            <X class="h-4 w-4" />
          </button>
        </div>
        <p class="mb-3 text-xs text-muted-foreground">
          Impressão "próprio" (Amazon): cota a etiqueta e vê se a menor opção cabe no frete projetado da Tabela de Preços. Só consulta — não compra.
        </p>

        <div class="grid grid-cols-2 gap-3">
          <label class="text-sm">
            CEP origem
            <input v-model="confere.from_cep" class="mt-1 w-full rounded-md border bg-background px-2 py-1.5 text-sm" placeholder="13400-000" />
          </label>
          <label class="text-sm">
            CEP destino
            <input v-model="confere.to_cep" class="mt-1 w-full rounded-md border bg-background px-2 py-1.5 text-sm" placeholder="01310-100" />
          </label>
          <label class="text-sm">
            Largura (cm)
            <input v-model="confere.width" type="number" class="mt-1 w-full rounded-md border bg-background px-2 py-1.5 text-sm" />
          </label>
          <label class="text-sm">
            Altura (cm)
            <input v-model="confere.height" type="number" class="mt-1 w-full rounded-md border bg-background px-2 py-1.5 text-sm" />
          </label>
          <label class="text-sm">
            Comprimento (cm)
            <input v-model="confere.length" type="number" class="mt-1 w-full rounded-md border bg-background px-2 py-1.5 text-sm" />
          </label>
          <label class="text-sm">
            Peso (kg)
            <input v-model="confere.weight" type="number" class="mt-1 w-full rounded-md border bg-background px-2 py-1.5 text-sm" />
          </label>
          <label class="text-sm">
            Valor segurado (R$)
            <input v-model="confere.insurance_value" type="number" class="mt-1 w-full rounded-md border bg-background px-2 py-1.5 text-sm" />
          </label>
          <label class="text-sm">
            Frete projetado (R$)
            <input v-model="confere.frete_projetado" class="mt-1 w-full rounded-md border bg-background px-2 py-1.5 text-sm" placeholder="opcional" />
          </label>
        </div>

        <button
          class="mt-3 inline-flex items-center gap-1 rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
          :disabled="conferindo || !confere.from_cep || !confere.to_cep"
          @click="conferirFrete"
        >
          <Loader2 v-if="conferindo" class="h-4 w-4 animate-spin" />
          <Truck v-else class="h-4 w-4" />
          Cotar
        </button>

        <div v-if="confereErro" class="mt-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          {{ confereErro }}
        </div>

        <div v-if="confereRes" class="mt-3 space-y-2">
          <div
            class="rounded-md px-3 py-2 text-sm font-medium"
            :class="confereRes.libera ? 'bg-emerald-100 text-emerald-700' : 'bg-amber-100 text-amber-700'"
          >
            {{ confereRes.libera ? 'LIBERA' : 'NÃO LIBERA' }} — {{ CONFERE_MOTIVO[confereRes.motivo] || confereRes.motivo }}
          </div>
          <div class="grid grid-cols-2 gap-2 text-sm">
            <div>Menor frete: <strong>{{ fmtBrl(confereRes.menor_frete) }}</strong></div>
            <div>Projetado: <strong>{{ fmtBrl(confereRes.frete_projetado) }}</strong></div>
            <div v-if="confereRes.servico_escolhido">Serviço: {{ confereRes.empresa_escolhida }} {{ confereRes.servico_escolhido }}</div>
            <div v-if="confereRes.prazo_dias != null">Prazo: {{ confereRes.prazo_dias }} dia(s)</div>
            <div v-if="confereRes.diferenca != null">Folga: <strong>{{ fmtBrl(confereRes.diferenca) }}</strong></div>
          </div>
          <div v-if="confereRes.cotacoes.length" class="overflow-x-auto rounded-md border">
            <table class="w-full text-xs">
              <thead class="bg-muted/50 text-left">
                <tr>
                  <th class="px-2 py-1 font-medium">Transportadora</th>
                  <th class="px-2 py-1 font-medium">Preço</th>
                  <th class="px-2 py-1 font-medium">Prazo</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(c, i) in confereRes.cotacoes" :key="i" class="border-t">
                  <td class="px-2 py-1">{{ c.empresa }} {{ c.servico_nome }}</td>
                  <td class="px-2 py-1">{{ c.erro ? '—' : fmtBrl(c.preco) }}</td>
                  <td class="px-2 py-1">{{ c.erro ? c.erro : (c.prazo_dias != null ? `${c.prazo_dias}d` : '—') }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
