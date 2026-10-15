<script setup lang="ts">
// Conciliação › Extrato do dia (passo 2c, 14/10/2026). A tela de trabalho da
// manhã: cada linha do extrato de ontem precisa de um destino — um pedido ou
// uma classificação — e o dia só fecha com 100%. Prova dos nove no rodapé.
import { ChevronLeft, ChevronRight, Loader2, Lock, LockOpen, Search } from 'lucide-vue-next'
import { STATUS_MOV, fmtDia, fmtDiaHora, fmtHora, fmtMoney, ontemISO, sitCls, somaDias, tipoMov } from '~/lib/conciliacao'

const props = defineProps<{ loja: string; canEdit: boolean }>()
const emit = defineEmits<{ (e: 'ver-pedido', pedido: string): void }>()

type Mov = {
  id: string
  data: string | null
  tipo: string
  tipo_label: string
  descricao: string | null
  sub_tipo: string | null
  pagamento_id: string | null
  pedido_marketplace: string | null
  pack_id: string | null
  pedido_bling: string | null
  bruto: number | null
  tarifa: number | null
  parcelamento: number | null
  frete: number | null
  cupom: number | null
  credito: number | null
  debito: number | null
  saldo_apos: number | null
  sku: string | null
  classificacao: string | null
  classificacao_label: string | null
  status: string
  vinculado_por: string | null
  vinculado_em: string | null
  pedido_situacao: string | null
  pedido_situacao_label: string | null
  pedido_etapa: string | null
}
type Totais = {
  linhas: number
  saldo_inicial: number | null
  creditos: number | null
  debitos: number | null
  saldo_final: number | null
  saldo_informado: number | null
  diferenca: number | null
}
type Extrato = {
  loja: string
  dia: string
  movimentos: Mov[]
  totais: Totais
  contagem: Record<string, number>
  resolvidos: number
  fechado: { em: string; por: string } | null
  classificacoes: Array<{ key: string; label: string }>
}
type Sugestao = { pedido_bling: string; numeroloja: string | null; data_pedido: string | null; total: number | null; motivo: string }

const { api } = useApi()
const toasts = useToasts()

const dia = ref(ontemISO())
const mostrar = ref<'todos' | 'pendentes' | 'divergentes' | 'sem_pedido'>('todos')
const q = ref('')
const dados = ref<Extrato | null>(null)
const loading = ref(false)
const erro = ref('')
const selecionado = ref<Mov | null>(null)
const sugestoes = ref<Sugestao[]>([])
const sugLoading = ref(false)
const pedidoInput = ref('')
const salvando = ref(false)
const fechando = ref(false)

const pendentes = computed(() => dados.value?.contagem.pendente || 0)
const pct = computed(() => {
  const t = dados.value?.totais.linhas || 0
  return t ? Math.round(((dados.value?.resolvidos || 0) / t) * 100) : 0
})

async function carregar() {
  if (!props.loja) return
  loading.value = true
  erro.value = ''
  try {
    const query: Record<string, string> = { loja: props.loja, dia: dia.value, mostrar: mostrar.value }
    if (q.value.trim()) query.q = q.value.trim()
    dados.value = await api<Extrato>('/api/conciliacao/extrato', { query })
    if (selecionado.value) {
      const atual = dados.value.movimentos.find((m) => m.id === selecionado.value?.id)
      selecionado.value = atual || null
      if (!atual) sugestoes.value = []
    }
  } catch (e: any) {
    erro.value = e?.data?.detail?.code || e?.message || 'erro ao carregar'
  } finally {
    loading.value = false
  }
}

async function abrir(m: Mov) {
  selecionado.value = m
  pedidoInput.value = ''
  sugestoes.value = []
  if (m.status === 'pendente' || !m.pedido_bling) {
    sugLoading.value = true
    try {
      const r = await api<{ sugestoes: Sugestao[] }>(`/api/conciliacao/movimentos/${m.id}/sugestoes`)
      sugestoes.value = r.sugestoes
    } catch {
      sugestoes.value = []
    } finally {
      sugLoading.value = false
    }
  }
}

async function marcar(body: Record<string, unknown>) {
  if (!selecionado.value || !props.canEdit) return
  salvando.value = true
  try {
    await api(`/api/conciliacao/movimentos/${selecionado.value.id}`, { method: 'PATCH', body: { loja: props.loja, ...body } })
    toasts.success?.('salvo')
    await carregar()
  } catch (e: any) {
    const d = e?.data?.detail
    toasts.error?.(d?.code === 'pedido_bling_nao_encontrado' ? `pedido ${d.pedido} não é desta loja` : d?.mensagem || d?.code || 'não consegui salvar')
  } finally {
    salvando.value = false
  }
}

async function fecharDia() {
  if (!dados.value || !props.canEdit) return
  fechando.value = true
  try {
    await api(`/api/conciliacao/dias/${dados.value.dia}/fechar`, { method: 'POST', query: { loja: props.loja } })
    toasts.success?.('dia fechado')
    await carregar()
  } catch (e: any) {
    const d = e?.data?.detail
    toasts.error?.(d?.code === 'dia_com_pendencias' ? `${d.pendentes} linha(s) ainda sem destino` : d?.code || 'não consegui fechar')
  } finally {
    fechando.value = false
  }
}

async function reabrirDia() {
  if (!dados.value || !props.canEdit) return
  fechando.value = true
  try {
    await api(`/api/conciliacao/dias/${dados.value.dia}/reabrir`, { method: 'POST', query: { loja: props.loja } })
    await carregar()
  } finally {
    fechando.value = false
  }
}

function statusCls(s: string) {
  return STATUS_MOV[s]?.cls || 'bg-slate-200 text-slate-700'
}
function statusLabel(s: string) {
  return STATUS_MOV[s]?.label || s
}
function rowCls(m: Mov) {
  if (m.status === 'pendente') return 'bg-amber-50/70 dark:bg-amber-950/10'
  if (m.status === 'divergente') return 'bg-red-50 dark:bg-red-950/20'
  return ''
}

watch(() => props.loja, () => carregar())
watch([dia, mostrar], () => carregar())
await carregar()
</script>

<template>
  <div class="space-y-3">
    <div class="flex flex-wrap items-center gap-2 text-xs">
      <div class="inline-flex items-center gap-1 rounded-md border bg-card px-1 py-0.5">
        <button type="button" class="rounded px-1 py-0.5 hover:bg-muted" title="dia anterior" @click="dia = somaDias(dia, -1)"><ChevronLeft class="size-3.5" /></button>
        <input v-model="dia" type="date" class="bg-transparent outline-none" />
        <button type="button" class="rounded px-1 py-0.5 hover:bg-muted" title="dia seguinte" @click="dia = somaDias(dia, 1)"><ChevronRight class="size-3.5" /></button>
        <button type="button" class="rounded px-1.5 py-0.5 text-muted-foreground underline" @click="dia = ontemISO()">ontem</button>
      </div>
      <label class="inline-flex items-center gap-1 rounded-md border bg-card px-2 py-1">
        <span class="font-semibold text-muted-foreground">Mostrar</span>
        <select v-model="mostrar" class="bg-transparent outline-none">
          <option value="todos">todos</option>
          <option value="pendentes">só pendentes</option>
          <option value="divergentes">só divergentes</option>
          <option value="sem_pedido">só sem pedido</option>
        </select>
      </label>
      <label class="inline-flex items-center gap-1 rounded-md border bg-card px-2 py-1">
        <Search class="size-3.5 text-muted-foreground" />
        <input v-model="q" placeholder="pedido, venda, pagamento, SKU" class="w-48 bg-transparent outline-none" @keyup.enter="carregar()" />
      </label>
      <span v-if="dados" class="text-muted-foreground">{{ dados.movimentos.length }} de {{ dados.totais.linhas }} movimentos</span>
      <span class="ml-auto inline-flex items-center gap-1.5">
        <template v-if="dados?.fechado">
          <span class="inline-flex items-center gap-1 rounded bg-slate-200 px-2 py-1 font-bold text-slate-800 dark:bg-slate-700 dark:text-slate-100"><Lock class="size-3.5" /> dia fechado por {{ dados.fechado.por }} em {{ fmtDiaHora(dados.fechado.em) }}</span>
          <button v-if="canEdit" type="button" class="rounded-md border px-2 py-1 font-semibold hover:bg-muted" :disabled="fechando" @click="reabrirDia()"><LockOpen class="mr-1 inline size-3.5" />Reabrir</button>
        </template>
        <button
          v-else-if="canEdit"
          type="button"
          class="inline-flex items-center gap-1 rounded-md bg-primary px-2.5 py-1.5 font-semibold text-primary-foreground disabled:opacity-50"
          :disabled="fechando || !dados || pendentes > 0 || !dados.totais.linhas"
          :title="pendentes > 0 ? `${pendentes} linha(s) ainda sem destino` : 'fecha o dia: todas as linhas têm destino'"
          @click="fecharDia()"
        >
          <Lock class="size-3.5" /> Fechar o dia<span v-if="pendentes > 0"> ({{ pendentes }} pendentes)</span>
        </button>
      </span>
    </div>

    <div v-if="dados" class="flex flex-wrap items-center gap-3 rounded-md border bg-card px-3 py-2 text-xs">
      <span class="text-sm font-bold">{{ fmtDia(dados.dia + 'T12:00:00') }} · {{ dados.loja }}</span>
      <span class="inline-flex items-center gap-2">
        <span class="inline-block h-2.5 w-48 overflow-hidden rounded-full bg-muted"><span class="block h-full bg-emerald-600" :style="{ width: pct + '%' }" /></span>
        <b>{{ dados.resolvidos }} de {{ dados.totais.linhas }}</b> movimentos conciliados
      </span>
      <template v-for="(info, key) in STATUS_MOV" :key="key">
        <span v-if="dados.contagem[key]" class="rounded px-1.5 py-0.5 font-bold" :class="info.cls">{{ dados.contagem[key] }} {{ info.label }}</span>
      </template>
    </div>

    <div class="grid gap-3 xl:grid-cols-[1fr_420px]">
      <div class="flex min-h-0 min-w-0 flex-col rounded-md border bg-card">
        <div class="flex items-center justify-between rounded-t-md bg-[#1f3a5f] px-3 py-1.5 text-xs font-semibold text-white">
          <span>Movimentos do dia — extrato do Mercado Pago</span>
          <span class="text-[11px] font-normal text-slate-300">cada linha precisa de um destino: um pedido ou uma classificação</span>
        </div>
        <div class="max-h-[62vh] overflow-auto">
          <table class="w-full border-collapse text-[11px]">
            <thead class="sticky top-0 z-10 bg-muted text-[10px] uppercase tracking-wide text-muted-foreground">
              <tr>
                <th class="border-b px-1.5 py-1 text-left">Hora</th>
                <th class="border-b px-1.5 py-1 text-left">Nº pagamento</th>
                <th class="border-b px-1.5 py-1 text-left">Tipo</th>
                <th class="border-b px-1.5 py-1 text-left">Pedido Bling</th>
                <th class="border-b px-1.5 py-1 text-left">Venda ML</th>
                <th class="border-b px-1.5 py-1 text-right">Bruto</th>
                <th class="border-b px-1.5 py-1 text-right">Tarifa</th>
                <th class="border-b px-1.5 py-1 text-right">Frete</th>
                <th class="border-b px-1.5 py-1 text-right">Parc.</th>
                <th class="border-b px-1.5 py-1 text-right">Débito</th>
                <th class="border-b px-1.5 py-1 text-right">Crédito</th>
                <th class="border-b px-1.5 py-1 text-right">Saldo</th>
                <th class="border-b px-1.5 py-1 text-left">Conciliação</th>
                <th class="border-b px-1.5 py-1 text-left">Quem / quando</th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="loading && !dados"><td colspan="14" class="py-8 text-center text-muted-foreground"><Loader2 class="mr-1 inline size-4 animate-spin" /> carregando…</td></tr>
              <tr v-else-if="erro"><td colspan="14" class="py-8 text-center text-red-600">{{ erro }}</td></tr>
              <tr v-else-if="dados && !dados.movimentos.length"><td colspan="14" class="py-8 text-center text-muted-foreground">{{ dados.totais.linhas ? 'nada com esse filtro' : 'nenhum movimento lido neste dia (o extrato é lido de madrugada; "Sincronizar agora" pede o de ontem)' }}</td></tr>
              <tr
                v-for="m in dados?.movimentos || []"
                :key="m.id"
                class="cursor-pointer border-b hover:bg-muted/60"
                :class="[rowCls(m), selecionado?.id === m.id && 'outline outline-2 -outline-offset-2 outline-primary']"
                @click="abrir(m)"
              >
                <td class="whitespace-nowrap px-1.5 py-1 tabular-nums">{{ fmtHora(m.data) }}</td>
                <td class="whitespace-nowrap px-1.5 py-1 text-muted-foreground">{{ m.pagamento_id }}</td>
                <td class="whitespace-nowrap px-1.5 py-1"><b>{{ m.tipo_label }}</b><span v-if="m.descricao && m.descricao !== m.tipo" class="ml-1 text-muted-foreground">{{ m.descricao }}</span></td>
                <td class="whitespace-nowrap px-1.5 py-1">
                  <button v-if="m.pedido_bling" type="button" class="font-bold underline decoration-dotted" :title="m.pedido_situacao_label || ''" @click.stop="emit('ver-pedido', m.pedido_bling)">{{ m.pedido_bling }}</button>
                  <span v-else-if="m.classificacao" class="text-muted-foreground">sem pedido</span>
                  <span v-else class="text-muted-foreground">?</span>
                </td>
                <td class="whitespace-nowrap px-1.5 py-1 text-muted-foreground">{{ m.pedido_marketplace || '—' }}<span v-if="m.pack_id"> · pack</span></td>
                <td class="whitespace-nowrap px-1.5 py-1 text-right tabular-nums">{{ fmtMoney(m.bruto, '') }}</td>
                <td class="whitespace-nowrap px-1.5 py-1 text-right tabular-nums">{{ m.tarifa ? fmtMoney(Math.abs(m.tarifa)) : '' }}</td>
                <td class="whitespace-nowrap px-1.5 py-1 text-right tabular-nums">{{ m.frete ? fmtMoney(Math.abs(m.frete)) : '' }}</td>
                <td class="whitespace-nowrap px-1.5 py-1 text-right tabular-nums">{{ m.parcelamento ? fmtMoney(Math.abs(m.parcelamento)) : '' }}</td>
                <td class="whitespace-nowrap px-1.5 py-1 text-right tabular-nums text-red-600">{{ m.debito ? fmtMoney(m.debito) : '' }}</td>
                <td class="whitespace-nowrap px-1.5 py-1 text-right tabular-nums text-emerald-700">{{ m.credito ? fmtMoney(m.credito) : '' }}</td>
                <td class="whitespace-nowrap px-1.5 py-1 text-right tabular-nums">{{ fmtMoney(m.saldo_apos, '') }}</td>
                <td class="whitespace-nowrap px-1.5 py-1"><span class="rounded px-1.5 py-0.5 text-[10px] font-bold" :class="statusCls(m.status)">{{ STATUS_MOV[m.status]?.dot || '●' }} {{ statusLabel(m.status) }}<template v-if="m.classificacao && m.status !== 'anterior'"> · {{ m.classificacao_label }}</template></span></td>
                <td class="whitespace-nowrap px-1.5 py-1 text-muted-foreground">
                  <template v-if="m.vinculado_por">{{ m.vinculado_por }} {{ fmtDiaHora(m.vinculado_em) }}</template>
                  <template v-else-if="m.status === 'divergente'">pedido {{ m.pedido_situacao_label }}</template>
                  <template v-else-if="m.pedido_bling || m.classificacao">robô</template>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-if="dados" class="grid grid-cols-3 border-t text-[11px] md:grid-cols-6">
          <div class="border-r px-2 py-1.5"><div class="text-muted-foreground">Saldo inicial</div><b class="text-xs tabular-nums">{{ fmtMoney(dados.totais.saldo_inicial) }}</b></div>
          <div class="border-r px-2 py-1.5"><div class="text-muted-foreground">Entradas (créditos)</div><b class="text-xs tabular-nums text-emerald-700">+ {{ fmtMoney(dados.totais.creditos) }}</b></div>
          <div class="border-r px-2 py-1.5"><div class="text-muted-foreground">Saídas (débitos)</div><b class="text-xs tabular-nums text-red-600">− {{ fmtMoney(dados.totais.debitos) }}</b></div>
          <div class="border-r px-2 py-1.5"><div class="text-muted-foreground">Saldo final calculado</div><b class="text-xs tabular-nums">{{ fmtMoney(dados.totais.saldo_final) }}</b></div>
          <div class="border-r px-2 py-1.5"><div class="text-muted-foreground">Saldo informado pelo Mercado Pago</div><b class="text-xs tabular-nums">{{ fmtMoney(dados.totais.saldo_informado) }}</b></div>
          <div class="px-2 py-1.5"><div class="text-muted-foreground">Diferença</div><b class="text-xs tabular-nums" :class="dados.totais.diferenca === null ? '' : Math.abs(dados.totais.diferenca) <= 0.05 ? 'text-emerald-700' : 'text-red-600'">{{ fmtMoney(dados.totais.diferenca) }}<span v-if="dados.totais.diferenca !== null && Math.abs(dados.totais.diferenca) <= 0.05"> ✔ bate</span></b></div>
        </div>
        <div class="border-t px-3 py-1.5 text-[11px] text-muted-foreground">O dia só fecha com todas as linhas resolvidas (vinculada a um pedido ou classificada). Reservas são dinheiro travado: entram e saem, não contam como perda.</div>
      </div>

      <div class="flex min-h-0 min-w-0 flex-col rounded-md border bg-card">
        <div class="flex items-center justify-between rounded-t-md bg-[#1f3a5f] px-3 py-1.5 text-xs font-semibold text-white">
          <span>{{ selecionado ? 'Movimento' : 'Vincular / classificar' }}</span>
          <span class="text-[11px] font-normal text-slate-300">o destino de cada linha</span>
        </div>
        <div v-if="!selecionado" class="p-6 text-center text-xs text-muted-foreground">clique numa linha pra ver o detalhe, vincular a um pedido ou classificar</div>
        <div v-else class="space-y-2 p-2.5 text-[11px]">
          <div class="rounded border bg-muted/40 px-2 py-1.5">
            <b>{{ fmtDiaHora(selecionado.data) }} · {{ selecionado.tipo_label }}</b>
            <span v-if="selecionado.descricao && selecionado.descricao !== selecionado.tipo" class="text-muted-foreground"> ({{ selecionado.descricao }})</span>
            <div class="mt-1 grid grid-cols-2 gap-x-3 gap-y-0.5 text-muted-foreground">
              <span>pagamento <b class="text-foreground">{{ selecionado.pagamento_id || '—' }}</b></span>
              <span>venda ML <b class="text-foreground">{{ selecionado.pedido_marketplace || '—' }}</b><span v-if="selecionado.pack_id"> · pack {{ selecionado.pack_id }}</span></span>
              <span>bruto <b class="text-foreground">{{ fmtMoney(selecionado.bruto) }}</b></span>
              <span>SKU <b class="text-foreground">{{ selecionado.sku || '—' }}</b></span>
              <span>crédito <b class="text-emerald-700">{{ fmtMoney(selecionado.credito) }}</b></span>
              <span>débito <b class="text-red-600">{{ fmtMoney(selecionado.debito) }}</b></span>
            </div>
            <div class="mt-1"><span class="rounded px-1.5 py-0.5 font-bold" :class="statusCls(selecionado.status)">{{ statusLabel(selecionado.status) }}<template v-if="selecionado.classificacao && selecionado.status !== 'anterior'"> · {{ selecionado.classificacao_label }}</template></span></div>
          </div>

          <div v-if="selecionado.pedido_bling" class="rounded border px-2 py-1.5">
            Vinculado ao pedido <button type="button" class="font-bold underline decoration-dotted" @click="emit('ver-pedido', selecionado.pedido_bling!)">{{ selecionado.pedido_bling }}</button>
            <span v-if="selecionado.pedido_situacao" class="ml-1 rounded px-1.5 py-0.5 font-bold" :class="sitCls(selecionado.pedido_situacao)">{{ selecionado.pedido_situacao_label }}</span>
            <div v-if="selecionado.vinculado_por" class="text-muted-foreground">por {{ selecionado.vinculado_por }} em {{ fmtDiaHora(selecionado.vinculado_em) }}</div>
            <button v-if="canEdit" type="button" class="mt-1 rounded border px-2 py-0.5 font-semibold hover:bg-muted" :disabled="salvando" @click="marcar({ desvincular: true })">Desvincular</button>
          </div>

          <template v-if="canEdit && !selecionado.pedido_bling">
            <div class="font-bold text-[#1f3a5f] dark:text-slate-200">Vincular a um pedido</div>
            <div class="flex gap-1.5">
              <input v-model="pedidoInput" placeholder="nº do pedido Bling" class="w-full rounded border bg-background px-2 py-1" @keyup.enter="pedidoInput.trim() && marcar({ pedido_bling: pedidoInput.trim() })" />
              <button type="button" class="rounded bg-primary px-2 py-1 font-semibold text-primary-foreground disabled:opacity-50" :disabled="salvando || !pedidoInput.trim()" @click="marcar({ pedido_bling: pedidoInput.trim() })">Vincular</button>
            </div>
            <div v-if="sugLoading" class="text-muted-foreground"><Loader2 class="mr-1 inline size-3.5 animate-spin" /> procurando sugestões…</div>
            <template v-else-if="sugestoes.length">
              <div class="text-muted-foreground">sugestões do robô:</div>
              <button
                v-for="s in sugestoes"
                :key="s.pedido_bling"
                type="button"
                class="flex w-full items-center justify-between rounded border px-2 py-1 text-left hover:bg-muted"
                :class="s.motivo === 'mesmo pagamento' && 'border-emerald-400 bg-emerald-50 dark:bg-emerald-950/20'"
                :disabled="salvando"
                @click="marcar({ pedido_bling: s.pedido_bling })"
              >
                <span><b>{{ s.pedido_bling }}</b><span v-if="s.numeroloja" class="text-muted-foreground"> · {{ s.numeroloja }}</span><span v-if="s.data_pedido" class="text-muted-foreground"> · {{ fmtDia(s.data_pedido) }}</span><span v-if="s.total !== null" class="text-muted-foreground"> · {{ fmtMoney(s.total) }}</span></span>
                <span class="rounded bg-muted px-1.5 py-0.5 text-[10px] font-bold">{{ s.motivo }}</span>
              </button>
            </template>
            <div v-else class="text-muted-foreground">nenhuma sugestão: digite o pedido ou classifique</div>
            <div class="font-bold text-[#1f3a5f] dark:text-slate-200">Ou classificar sem pedido</div>
            <div class="flex flex-wrap gap-1">
              <button v-for="c in dados?.classificacoes || []" :key="c.key" type="button" class="rounded border px-2 py-0.5 font-semibold hover:bg-muted" :class="selecionado.classificacao === c.key && 'bg-muted'" :disabled="salvando" @click="marcar({ classificacao: c.key })">{{ c.label }}</button>
            </div>
          </template>
          <div v-else-if="!canEdit" class="text-muted-foreground">você só pode ver; vincular e classificar pede permissão de edição</div>
        </div>
      </div>
    </div>
  </div>
</template>
