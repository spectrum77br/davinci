<script setup lang="ts">
// Histórico › Envios Threema (tarefa 16, C2). Cada mensagem que o servidor
// mandou pelo Threema: quando, de qual envio, quem disparou (ou se foi
// automático), para quem e se chegou. Lê o registro gravado pelo envio
// (`threema_envio_registro`), um item por LOTE (uma chamada de envio, que pode
// ir para várias pessoas). O ID do Threema nunca aparece inteiro.
import { computed, onMounted, ref, watch } from 'vue'
import { CheckCircle2, ChevronDown, ChevronRight, MessageSquare, RefreshCw, Search, XCircle } from 'lucide-vue-next'
import { isoDateBrt, isoDaysAgo, isoToday } from '~/lib/date'

type Destinatario = {
  nome: string | null
  id: string | null
  ok: boolean
  http_status: number | null
  erro: string | null
  quando: string
}
type EnvioInfo = { chave: string | null; nome: string; painel: string | null }
type Lote = {
  lote_id: string
  criado_em: string
  envio: EnvioInfo
  escopo: string | null
  contexto: string | null
  canal: string | null
  onde_roda: string
  origem: 'automatico' | 'manual' | 'teste'
  via: string | null
  ator: string | null
  titulo: string | null
  bytes: number | null
  total: number
  entregues: number
  falhas: number
  erro_geral: string | null
  destinatarios: Destinatario[]
}
type Resposta = {
  items: Lote[]
  total: number
  envios: { chave: string; nome: string; painel: string | null }[]
}

const { api } = useApi()

const POR_PAGINA = 100
const itens = ref<Lote[]>([])
const total = ref(0)
const carregando = ref(false)
const erro = ref<string | null>(null)
const pagina = ref(1)

const busca = ref('')
const dias = ref(7)
const envio = ref('')
const resultado = ref<'' | 'ok' | 'falhou'>('')
const origem = ref('')

// As opções do filtro de envio não encolhem enquanto a pessoa filtra.
const enviosConhecidos = ref<Resposta['envios']>([])

let seq = 0
async function carregar() {
  const meu = ++seq
  carregando.value = true
  erro.value = null
  try {
    const query: Record<string, string | number | boolean> = {
      dias: dias.value,
      limit: POR_PAGINA,
      offset: (pagina.value - 1) * POR_PAGINA,
    }
    if (busca.value.trim()) query.busca = busca.value.trim()
    if (envio.value) query.chave = envio.value
    if (resultado.value) query.ok = resultado.value === 'ok'
    if (origem.value) query.origem = origem.value
    const r = await api<Resposta>('/api/historico/threema', { query })
    if (meu !== seq) return
    itens.value = r.items
    total.value = r.total
    const porChave = new Map(enviosConhecidos.value.map((e) => [e.chave, e]))
    for (const e of r.envios) porChave.set(e.chave, e)
    enviosConhecidos.value = [...porChave.values()].sort(
      (a, b) => (a.painel || '~').localeCompare(b.painel || '~') || a.nome.localeCompare(b.nome),
    )
  } catch {
    if (meu !== seq) return
    erro.value = 'Não deu para carregar os envios do Threema. Tente atualizar.'
  } finally {
    if (meu === seq) carregando.value = false
  }
}

let espera: ReturnType<typeof setTimeout> | null = null
watch(busca, () => {
  if (espera) clearTimeout(espera)
  espera = setTimeout(() => { pagina.value = 1; carregar() }, 300)
})
watch([dias, envio, resultado, origem], () => { pagina.value = 1; carregar() })
watch(pagina, () => carregar())
onMounted(carregar)

const temFiltro = computed(
  () => !!(busca.value.trim() || envio.value || resultado.value || origem.value || dias.value !== 7),
)
function limparFiltros() {
  busca.value = ''
  envio.value = ''
  resultado.value = ''
  origem.value = ''
  dias.value = 7
}

const paginas = computed(() => Math.max(1, Math.ceil(total.value / POR_PAGINA)))
const de = computed(() => (total.value === 0 ? 0 : (pagina.value - 1) * POR_PAGINA + 1))
const ate = computed(() => Math.min(total.value, pagina.value * POR_PAGINA))

// ── Datas: "hoje 16:09", "ontem 22:30", "16/09 12:09" (igual às Alterações)
const BRT = 'America/Sao_Paulo'
function fmtRel(v: string | null | undefined): string {
  if (!v) return '—'
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return v
  const dia = isoDateBrt(d)
  const hora = d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit', timeZone: BRT })
  if (dia === isoToday()) return `hoje ${hora}`
  if (dia === isoDaysAgo(1)) return `ontem ${hora}`
  const mesmoAno = dia.slice(0, 4) === isoToday().slice(0, 4)
  const data = d.toLocaleDateString('pt-BR', {
    day: '2-digit', month: '2-digit', ...(mesmoAno ? {} : { year: '2-digit' }), timeZone: BRT,
  })
  return `${data} ${hora}`
}
function fmtCompleta(v: string): string {
  return new Date(v).toLocaleString('pt-BR', { timeZone: BRT, dateStyle: 'short', timeStyle: 'medium' })
}

// ── Como cada lote é contado ────────────────────────────────────────────────
const ORIGEM: Record<Lote['origem'], string> = {
  automatico: 'automático',
  manual: 'botão',
  teste: 'teste',
}
function quem(l: Lote): string {
  if (l.origem === 'automatico') return 'automático'
  return l.ator ? `${l.ator} (${ORIGEM[l.origem]})` : ORIGEM[l.origem]
}
// O que o Threema respondeu, em frase de gente.
const ERROS: Record<string, string> = {
  http_400: 'pedido recusado pelo Threema (400)',
  http_401: 'remetente recusado: ID ou senha do gateway (401)',
  http_402: 'sem crédito no Threema (402)',
  http_404: 'esse ID do Threema não existe (404)',
  http_413: 'mensagem grande demais (413)',
  http_500: 'Threema fora do ar (500)',
  threema_gateway_id_missing: 'remetente sem ID configurado no servidor',
  threema_gateway_secret_missing: 'remetente sem senha configurada no servidor',
  threema_recipients_missing: 'ninguém para receber',
  threema_contexto_desativado: 'canal desativado no servidor',
  threema_contexto_desconhecido: 'remetente desconhecido',
  threema_contexto_missing: 'envio sem remetente definido',
  threema_context_channels_invalid: 'mapa de canais do servidor inválido',
  threema_contexto_gateway_id_repetido: 'dois canais com o mesmo remetente',
}
function frase(e: string | null): string {
  if (!e) return ''
  return ERROS[e] || e
}
function situacao(l: Lote): { texto: string; classe: string } {
  if (l.total === 0) return { texto: 'não saiu', classe: 'pill-danger' }
  if (l.falhas === 0) return { texto: `${l.entregues}/${l.total} receberam`, classe: 'pill-success' }
  if (l.entregues === 0) return { texto: `0/${l.total} receberam`, classe: 'pill-danger' }
  return { texto: `${l.entregues}/${l.total} receberam`, classe: 'pill-warning' }
}
function resumoPessoas(l: Lote): string {
  const nomes = l.destinatarios.map((d) => d.nome || d.id || '?')
  if (nomes.length <= 3) return nomes.join(', ')
  return `${nomes.slice(0, 3).join(', ')} e mais ${nomes.length - 3}`
}

const aberto = ref<string | null>(null)
function alternar(l: Lote) {
  aberto.value = aberto.value === l.lote_id ? null : l.lote_id
}
</script>

<template>
  <div>
    <div class="flex flex-wrap items-center gap-2 mb-3">
      <div class="relative">
        <Search class="absolute left-2.5 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
        <input
          v-model="busca"
          class="h-9 w-72 max-w-full rounded-md border bg-background pl-8 pr-3 text-sm"
          placeholder="pessoa, assunto da mensagem…"
        />
      </div>
      <select v-model.number="dias" class="h-9 rounded-md border bg-background px-2 text-sm" title="Até quantos dias para trás">
        <option :value="1">Período: hoje</option>
        <option :value="7">Período: 7 dias</option>
        <option :value="30">Período: 30 dias</option>
        <option :value="90">Período: 90 dias</option>
        <option :value="180">Período: 180 dias</option>
      </select>
      <select v-model="envio" class="h-9 max-w-[18rem] rounded-md border bg-background px-2 text-sm" title="Qual envio">
        <option value="">Envio: todos</option>
        <option v-for="e in enviosConhecidos" :key="e.chave" :value="e.chave">
          {{ e.painel ? `${e.painel} › ${e.nome}` : e.nome }}
        </option>
      </select>
      <select v-model="resultado" class="h-9 rounded-md border bg-background px-2 text-sm" title="Chegou para todos ou alguém ficou sem">
        <option value="">Resultado: todos</option>
        <option value="ok">Resultado: todos receberam</option>
        <option value="falhou">Resultado: com falha</option>
      </select>
      <select v-model="origem" class="h-9 rounded-md border bg-background px-2 text-sm" title="Automático (robô) ou disparado por alguém">
        <option value="">Origem: todas</option>
        <option value="automatico">Origem: automático</option>
        <option value="manual">Origem: botão</option>
        <option value="teste">Origem: teste</option>
      </select>
      <button v-if="temFiltro" class="text-sm text-muted-foreground underline" @click="limparFiltros">limpar filtros</button>
      <button
        class="inline-flex items-center gap-1.5 h-9 rounded-md border bg-background px-3 text-sm hover:bg-muted"
        :disabled="carregando"
        @click="carregar"
      >
        <RefreshCw class="size-4" :class="{ 'animate-spin': carregando }" /> atualizar
      </button>
      <span class="ml-auto text-sm text-muted-foreground">{{ de }}–{{ ate }} de {{ total.toLocaleString('pt-BR') }}</span>
    </div>

    <div v-if="erro" class="mb-3 rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">{{ erro }}</div>

    <div class="table-card overflow-x-auto">
      <table class="w-full text-sm">
        <thead>
          <tr>
            <th class="text-left whitespace-nowrap">Quando</th>
            <th class="text-left">Envio</th>
            <th class="text-left">Quem disparou</th>
            <th class="text-left">Para quem</th>
            <th class="text-left">Resultado</th>
            <th />
          </tr>
        </thead>
        <tbody>
          <tr v-if="!carregando && itens.length === 0">
            <td colspan="6" class="py-10 text-center text-muted-foreground">
              <MessageSquare class="mx-auto mb-2 size-6 opacity-50" />
              {{ temFiltro ? 'Nada com esses filtros. Tente outro período ou limpe os filtros.' : 'Nenhum envio do Threema registrado neste período.' }}
            </td>
          </tr>
          <template v-for="l in itens" :key="l.lote_id">
            <tr class="cursor-pointer hover:bg-muted/40" @click="alternar(l)">
              <td class="whitespace-nowrap text-muted-foreground" :title="fmtCompleta(l.criado_em)">{{ fmtRel(l.criado_em) }}</td>
              <td>
                <div class="font-medium">{{ l.envio.nome }}</div>
                <div class="text-xs text-muted-foreground">
                  <span v-if="l.envio.painel">{{ l.envio.painel }}</span>
                  <span v-if="l.titulo" class="ml-1">· {{ l.titulo }}</span>
                </div>
              </td>
              <td class="whitespace-nowrap">{{ quem(l) }}</td>
              <td class="text-muted-foreground">{{ resumoPessoas(l) || '—' }}</td>
              <td class="whitespace-nowrap">
                <span :class="situacao(l).classe">{{ situacao(l).texto }}</span>
                <div v-if="l.erro_geral" class="mt-0.5 text-xs text-red-700">{{ frase(l.erro_geral) }}</div>
              </td>
              <td class="text-muted-foreground">
                <ChevronDown v-if="aberto === l.lote_id" class="size-4" />
                <ChevronRight v-else class="size-4" />
              </td>
            </tr>
            <tr v-if="aberto === l.lote_id" class="bg-muted/20">
              <td colspan="6" class="px-4 py-3">
                <ul v-if="l.destinatarios.length" class="space-y-1">
                  <li v-for="(d, i) in l.destinatarios" :key="i" class="flex flex-wrap items-center gap-2 text-sm">
                    <CheckCircle2 v-if="d.ok" class="size-4 text-emerald-600" />
                    <XCircle v-else class="size-4 text-red-600" />
                    <span class="font-medium">{{ d.nome || 'Sem nome' }}</span>
                    <span v-if="d.id" class="text-xs text-muted-foreground" title="ID do Threema (só o começo)">{{ d.id }}</span>
                    <span v-if="d.ok" class="text-xs text-muted-foreground">recebeu</span>
                    <span v-else class="text-xs text-red-700">{{ frase(d.erro) || 'não recebeu' }}</span>
                  </li>
                </ul>
                <p v-else class="text-sm text-red-700">
                  A mensagem não chegou a sair para ninguém<span v-if="l.erro_geral">: {{ frase(l.erro_geral) }}</span>.
                </p>
                <p class="mt-2 text-xs text-muted-foreground">
                  Remetente: {{ l.contexto || 'geral' }}<span v-if="l.canal && l.canal !== l.contexto"> (canal {{ l.canal }})</span>
                  · {{ fmtCompleta(l.criado_em) }}
                  <span v-if="l.bytes"> · {{ l.bytes.toLocaleString('pt-BR') }} bytes</span>
                  <span v-if="l.envio.chave"> · chave {{ l.envio.chave }}</span>
                </p>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>

    <div v-if="paginas > 1" class="mt-3 flex items-center justify-center gap-2 text-sm">
      <button class="h-8 w-8 rounded border disabled:opacity-40" :disabled="pagina === 1" @click="pagina = 1">«</button>
      <button class="h-8 w-8 rounded border disabled:opacity-40" :disabled="pagina === 1" @click="pagina--">‹</button>
      <span>página {{ pagina }} de {{ paginas }} · {{ POR_PAGINA }}/página</span>
      <button class="h-8 w-8 rounded border disabled:opacity-40" :disabled="pagina >= paginas" @click="pagina++">›</button>
      <button class="h-8 w-8 rounded border disabled:opacity-40" :disabled="pagina >= paginas" @click="pagina = paginas">»</button>
    </div>
  </div>
</template>
