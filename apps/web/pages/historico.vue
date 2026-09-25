<script setup lang="ts">
// Sistema › Histórico (25/09/2026). Eduardo: "uma lista de histórico do que
// está sendo mudado no davinci, por exemplo alterou a tabela de preços". Só o
// que PESSOAS mudam; só quem ele liberou vê — para os outros, inclusive
// admin, esta página responde igual a uma página que não existe.
import { computed, onMounted, ref, watch } from 'vue'
import { ArrowRight, History, RefreshCw, Search, UserCheck, X } from 'lucide-vue-next'
import { isoDateBrt, isoDaysAgo, isoToday } from '~/lib/date'

definePageMeta({
  middleware: [
    (to) => {
      const auth = useAuthStore()
      if (auth.user?.historico !== true) {
        // Mesma mensagem do Nuxt para rota inexistente.
        return abortNavigation(
          createError({ statusCode: 404, statusMessage: `Page not found: ${to.fullPath}`, fatal: true }),
        )
      }
    },
  ],
})

type Campo = { campo: string; nome: string; antes: string | null; depois: string | null }
type Alteracao = {
  id: number
  tabela: string
  entidade: string
  operacao: 'I' | 'U' | 'D' | 'X'
  verbo: string
  item: string
  campos: Campo[]
  vezes: number
}
type Evento = {
  id: number
  criado_em: string
  ator: string | null
  via: string
  tela: string | null
  acao: string | null
  metodo: string
  status: number
  n_alteracoes: number
  itens: string | null
  alteracoes: Alteracao[]
}
type Detalhe = Evento & { pagina: string | null; caminho: string; ip: string | null; corpo: unknown }
type Pessoa = { id: string; nome: string }
type PessoaAcesso = {
  id: string; nome: string; liberado: boolean; pode_gerenciar: boolean; situacao: string | null
}

const { api } = useApi()

const POR_PAGINA = 100
const itens = ref<Evento[]>([])
const total = ref(0)
const telas = ref<string[]>([])
const pessoas = ref<Pessoa[]>([])
const podeGerenciar = ref(false)
const carregando = ref(false)
const erro = ref<string | null>(null)
const pagina = ref(1)

const busca = ref('')
const dias = ref(7)
const tela = ref('')
const ator = ref('')
const tipo = ref('')

// As opções dos filtros não encolhem enquanto a pessoa filtra.
const telasConhecidas = ref<string[]>([])
const pessoasConhecidas = ref<Pessoa[]>([])

let seq = 0
async function carregar() {
  const meu = ++seq
  carregando.value = true
  erro.value = null
  try {
    const query: Record<string, string | number> = {
      dias: dias.value,
      limit: POR_PAGINA,
      offset: (pagina.value - 1) * POR_PAGINA,
    }
    if (busca.value.trim()) query.busca = busca.value.trim()
    if (tela.value) query.tela = tela.value
    if (ator.value) query.ator = ator.value
    if (tipo.value) query.tipo = tipo.value
    const r = await api<{
      items: Evento[]; total: number; telas: string[]; pessoas: Pessoa[]; pode_gerenciar: boolean
    }>('/api/historico', { query })
    if (meu !== seq) return
    itens.value = r.items
    total.value = r.total
    telas.value = r.telas
    pessoas.value = r.pessoas
    podeGerenciar.value = r.pode_gerenciar
    telasConhecidas.value = [...new Set([...telasConhecidas.value, ...r.telas])].sort()
    const porId = new Map(pessoasConhecidas.value.map((p) => [p.id, p]))
    for (const p of r.pessoas) porId.set(p.id, p)
    pessoasConhecidas.value = [...porId.values()].sort((a, b) => a.nome.localeCompare(b.nome))
  } catch (e: any) {
    if (meu !== seq) return
    erro.value = 'Não deu para carregar o Histórico. Tente atualizar.'
  } finally {
    if (meu === seq) carregando.value = false
  }
}

let espera: ReturnType<typeof setTimeout> | null = null
watch(busca, () => {
  if (espera) clearTimeout(espera)
  espera = setTimeout(() => { pagina.value = 1; carregar() }, 300)
})
watch([dias, tela, ator, tipo], () => { pagina.value = 1; carregar() })
watch(pagina, () => carregar())
onMounted(carregar)

const temFiltro = computed(() => !!(busca.value.trim() || tela.value || ator.value || tipo.value || dias.value !== 7))
function limparFiltros() {
  busca.value = ''
  tela.value = ''
  ator.value = ''
  tipo.value = ''
  dias.value = 7
}

const paginas = computed(() => Math.max(1, Math.ceil(total.value / POR_PAGINA)))
const de = computed(() => (total.value === 0 ? 0 : (pagina.value - 1) * POR_PAGINA + 1))
const ate = computed(() => Math.min(total.value, pagina.value * POR_PAGINA))

// ── Datas: "hoje 16:09", "ontem 22:30", "16/09 12:09" (mesmo padrão da Ouvidoria)
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

// ── Como a linha é contada ─────────────────────────────────────────────────
function oQue(e: Evento): string {
  const a = e.alteracoes[0]
  if (!a) return e.acao || 'ação'
  if (e.acao) return e.acao
  return `${a.verbo} ${a.entidade.toLowerCase()}`
}
function item(e: Evento): string {
  // ação sem mudança no banco (ver senha, enviar preço): o nome vem do evento
  return e.alteracoes[0]?.item || e.itens || ''
}
function vezesTotal(e: Evento): number {
  return e.alteracoes.reduce((s, a) => s + (a.vezes || 1), 0)
}
function mais(e: Evento): number {
  return Math.max(0, e.n_alteracoes - 1)
}
function resumoCampos(e: Evento): Campo[] {
  const a = e.alteracoes[0]
  if (!a || a.operacao !== 'U') return []
  return a.campos.slice(0, 2)
}
function corVerbo(op?: string): string {
  if (op === 'I') return 'pill-success'
  if (op === 'D') return 'pill-danger'
  if (op === 'U') return 'pill-info'
  return 'pill-muted'
}

// ── Detalhe (gaveta) ───────────────────────────────────────────────────────
const detalhe = ref<Detalhe | null>(null)
const abrindo = ref(false)
// Fechar enquanto carrega descarta a resposta que chegar depois.
let seqDetalhe = 0
async function abrir(e: Evento) {
  const meu = ++seqDetalhe
  abrindo.value = true
  detalhe.value = null
  try {
    const d = await api<Detalhe>(`/api/historico/${e.id}`)
    if (meu === seqDetalhe) detalhe.value = d
  } catch {
    if (meu === seqDetalhe) erro.value = 'Não deu para abrir esse registro.'
  } finally {
    if (meu === seqDetalhe) abrindo.value = false
  }
}
function fecharDetalhe() {
  seqDetalhe++
  abrindo.value = false
  detalhe.value = null
}
const corpoTexto = computed(() => {
  const c = detalhe.value?.corpo
  if (c === null || c === undefined) return ''
  return JSON.stringify(c, (_k, v) => (v && typeof v === 'object' && v._oculto ? '(oculta)' : v), 2)
})

// ── Quem pode ver (só o Eduardo) ───────────────────────────────────────────
const acessoAberto = ref(false)
const acesso = ref<PessoaAcesso[]>([])
const acessoErro = ref<string | null>(null)
const salvandoAcesso = ref<string | null>(null)
async function abrirAcesso() {
  acessoAberto.value = true
  acessoErro.value = null
  try {
    acesso.value = await api<PessoaAcesso[]>('/api/historico/acesso')
  } catch {
    acessoErro.value = 'Não deu para carregar a lista.'
  }
}
async function mudarAcesso(p: PessoaAcesso, ev: Event) {
  const caixa = ev.target as HTMLInputElement
  const liberado = caixa.checked
  salvandoAcesso.value = p.id
  acessoErro.value = null
  try {
    await api(`/api/historico/acesso/${p.id}`, { method: 'PUT', body: { liberado } })
    p.liberado = liberado
  } catch {
    acessoErro.value = `Não deu para ${liberado ? 'liberar' : 'tirar'} ${p.nome}.`
    caixa.checked = p.liberado // a caixa volta a mostrar o que está valendo
  } finally {
    salvandoAcesso.value = null
  }
}
</script>

<template>
  <div>
    <PageHeader
      title="Histórico"
      description="O que as pessoas mudaram no DaVinci: quem, quando, em qual tela e o valor de antes e de depois. Robôs não entram; senhas nunca aparecem."
    >
      <template #actions>
        <button
          v-if="podeGerenciar"
          class="inline-flex items-center gap-1.5 h-9 rounded-md border bg-background px-3 text-sm hover:bg-muted"
          title="Escolher quais admins podem ver o Histórico"
          @click="abrirAcesso"
        >
          <UserCheck class="size-4" /> Quem pode ver
        </button>
        <button
          class="inline-flex items-center gap-1.5 h-9 rounded-md border bg-background px-3 text-sm hover:bg-muted"
          :disabled="carregando"
          @click="carregar"
        >
          <RefreshCw class="size-4" :class="{ 'animate-spin': carregando }" /> atualizar
        </button>
      </template>
    </PageHeader>

    <div class="flex flex-wrap items-center gap-2 mb-3">
      <div class="relative">
        <Search class="absolute left-2.5 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
        <input
          v-model="busca"
          class="h-9 w-72 rounded-md border bg-background pl-8 pr-3 text-sm"
          placeholder="SKU, empresa, pessoa, pedido…"
        />
      </div>
      <select v-model.number="dias" class="h-9 rounded-md border bg-background px-2 text-sm" title="Até quantos dias para trás">
        <option :value="1">Período: hoje</option>
        <option :value="7">Período: 7 dias</option>
        <option :value="30">Período: 30 dias</option>
        <option :value="90">Período: 90 dias</option>
        <option :value="366">Período: 1 ano</option>
      </select>
      <select v-model="tela" class="h-9 rounded-md border bg-background px-2 text-sm" title="Em qual tela a pessoa estava">
        <option value="">Tela: todas</option>
        <option v-for="t in telasConhecidas" :key="t" :value="t">{{ t }}</option>
      </select>
      <select v-model="ator" class="h-9 rounded-md border bg-background px-2 text-sm" title="Quem fez">
        <option value="">Quem: todos</option>
        <option v-for="p in pessoasConhecidas" :key="p.id" :value="p.id">{{ p.nome }}</option>
      </select>
      <select v-model="tipo" class="h-9 rounded-md border bg-background px-2 text-sm" title="Criou, alterou, excluiu, ou ação que não muda cadastro (ex. enviar preço)">
        <option value="">Tipo: tudo</option>
        <option value="criou">Tipo: criou</option>
        <option value="alterou">Tipo: alterou</option>
        <option value="excluiu">Tipo: excluiu</option>
        <option value="acao">Tipo: ações (enviar, sincronizar, ver senha)</option>
      </select>
      <button v-if="temFiltro" class="text-sm text-muted-foreground underline" @click="limparFiltros">limpar filtros</button>
      <span class="ml-auto text-sm text-muted-foreground">{{ de }}–{{ ate }} de {{ total.toLocaleString('pt-BR') }}</span>
    </div>

    <div v-if="erro" class="mb-3 rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">{{ erro }}</div>

    <div class="table-card overflow-x-auto">
      <table class="w-full text-sm">
        <thead>
          <tr>
            <th class="text-left whitespace-nowrap">Quando</th>
            <th class="text-left">Quem</th>
            <th class="text-left">Tela</th>
            <th class="text-left">O que</th>
            <th class="text-left">Antes → Depois</th>
            <th />
          </tr>
        </thead>
        <tbody>
          <tr v-if="!carregando && itens.length === 0">
            <td colspan="6" class="py-10 text-center text-muted-foreground">
              <History class="mx-auto mb-2 size-6 opacity-50" />
              {{ temFiltro ? 'Nada com esses filtros. Tente outro período ou limpe os filtros.' : 'Nenhuma mudança registrada ainda neste período.' }}
            </td>
          </tr>
          <tr
            v-for="e in itens"
            :key="e.id"
            class="cursor-pointer hover:bg-muted/40"
            @click="abrir(e)"
          >
            <td class="whitespace-nowrap text-muted-foreground" :title="fmtCompleta(e.criado_em)">{{ fmtRel(e.criado_em) }}</td>
            <td class="whitespace-nowrap">
              {{ e.ator || '—' }}
              <span v-if="e.via === 'claude'" class="pill-muted ml-1" title="Pelo Claude conectado, em nome desta pessoa">via Claude</span>
            </td>
            <td class="whitespace-nowrap text-muted-foreground">{{ e.tela || '—' }}</td>
            <td>
              <span :class="corVerbo(e.alteracoes[0]?.operacao)">{{ oQue(e) }}</span>
              <span v-if="item(e)" class="ml-1.5 font-medium">{{ item(e) }}</span>
              <span v-if="mais(e) > 0" class="ml-1.5 text-xs text-muted-foreground">+{{ mais(e) }} {{ mais(e) === 1 ? 'alteração' : 'alterações' }}</span>
              <span v-if="e.status >= 400" class="pill-warning ml-1.5" title="A ação terminou com erro depois de já ter mudado algo — confira o detalhe">terminou com erro</span>
            </td>
            <td>
              <div v-for="c in resumoCampos(e)" :key="c.campo" class="flex flex-wrap items-center gap-1 text-xs">
                <span class="text-muted-foreground">{{ c.nome }}:</span>
                <span class="line-through decoration-muted-foreground/50 text-muted-foreground max-w-[14rem] truncate">{{ c.antes }}</span>
                <ArrowRight class="size-3 text-muted-foreground" />
                <span class="font-medium max-w-[14rem] truncate">{{ c.depois }}</span>
              </div>
              <span v-if="resumoCampos(e).length && (e.alteracoes[0]?.campos.length || 0) > 2" class="text-xs text-muted-foreground">…e mais campos</span>
              <span v-else-if="e.alteracoes[0]?.operacao === 'I'" class="text-xs text-muted-foreground">registro novo</span>
              <span v-else-if="e.alteracoes[0]?.operacao === 'D'" class="text-xs text-muted-foreground">registro apagado</span>
            </td>
            <td class="text-muted-foreground">›</td>
          </tr>
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

    <!-- Gaveta do detalhe -->
    <Teleport to="body">
      <div v-if="detalhe || abrindo" class="fixed inset-0 z-50 flex justify-end bg-black/30" @click.self="fecharDetalhe">
        <div class="h-full w-full max-w-2xl overflow-y-auto bg-background p-5 shadow-xl">
          <div class="mb-4 flex items-start gap-2">
            <div class="min-w-0">
              <h2 class="text-lg font-semibold">{{ detalhe ? oQue(detalhe) : 'Abrindo…' }}</h2>
              <p v-if="detalhe" class="text-sm text-muted-foreground">
                {{ detalhe.ator || '—' }}<span v-if="detalhe.via === 'claude'"> (via Claude)</span>
                · {{ fmtCompleta(detalhe.criado_em) }} · {{ detalhe.tela || '—' }}
              </p>
              <p v-if="detalhe && detalhe.status >= 400" class="mt-1 text-sm text-amber-700">
                A ação terminou com erro, mas as mudanças abaixo chegaram a ser gravadas.
              </p>
            </div>
            <button class="ml-auto rounded p-1 hover:bg-muted" title="Fechar" @click="fecharDetalhe"><X class="size-5" /></button>
          </div>

          <template v-if="detalhe">
            <p v-if="detalhe.alteracoes.length === 0" class="mb-4 rounded-md border bg-muted/30 px-3 py-2 text-sm">
              Esta ação não mudou nenhum cadastro do DaVinci ({{ detalhe.acao || 'sem alteração registrada' }}<span v-if="detalhe.itens">: {{ detalhe.itens }}</span>).
            </p>
            <div v-for="a in detalhe.alteracoes" :key="a.id" class="mb-4 rounded-md border">
              <div class="flex flex-wrap items-center gap-2 border-b bg-muted/30 px-3 py-2 text-sm">
                <span :class="corVerbo(a.operacao)">{{ a.verbo }}</span>
                <span class="font-medium">{{ a.entidade }}</span>
                <span v-if="a.item" class="text-muted-foreground">{{ a.item }}</span>
                <span v-if="a.vezes > 1" class="text-xs text-muted-foreground">({{ a.vezes }}×)</span>
              </div>
              <p v-if="a.operacao === 'X'" class="px-3 py-2 text-sm text-muted-foreground">{{ a.item }}</p>
              <table v-else class="w-full text-sm">
                <thead>
                  <tr class="text-xs text-muted-foreground">
                    <th class="px-3 py-1 text-left font-normal">Campo</th>
                    <th v-if="a.operacao !== 'I'" class="px-3 py-1 text-left font-normal">Antes</th>
                    <th v-if="a.operacao !== 'D'" class="px-3 py-1 text-left font-normal">Depois</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="c in a.campos" :key="c.campo" class="border-t">
                    <td class="px-3 py-1.5 text-muted-foreground whitespace-nowrap">{{ c.nome }}</td>
                    <td v-if="a.operacao !== 'I'" class="px-3 py-1.5 break-all">{{ c.antes }}</td>
                    <td v-if="a.operacao !== 'D'" class="px-3 py-1.5 break-all font-medium">{{ c.depois }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p v-if="detalhe.n_alteracoes > vezesTotal(detalhe)" class="mb-4 text-sm text-muted-foreground">
              E mais {{ detalhe.n_alteracoes - vezesTotal(detalhe) }} alterações nesta mesma ação.
            </p>

            <details v-if="corpoTexto" class="mb-3 rounded-md border px-3 py-2 text-sm">
              <summary class="cursor-pointer text-muted-foreground">O que a tela enviou (senhas já escondidas)</summary>
              <pre class="mt-2 max-h-80 overflow-auto whitespace-pre-wrap break-all text-xs">{{ corpoTexto }}</pre>
            </details>
            <p class="text-xs text-muted-foreground">
              Página: {{ detalhe.pagina || '—' }} · endereço: {{ detalhe.metodo }} {{ detalhe.caminho }}
              <span v-if="detalhe.ip"> · IP: {{ detalhe.ip }}</span>
            </p>
          </template>
        </div>
      </div>
    </Teleport>

    <!-- Quem pode ver -->
    <Teleport to="body">
      <div v-if="acessoAberto" class="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4" @click.self="acessoAberto = false">
        <div class="w-full max-w-md rounded-lg border bg-background p-5 shadow-xl">
          <div class="mb-3 flex items-center gap-2">
            <UserCheck class="size-5" />
            <h2 class="text-lg font-semibold">Quem pode ver o Histórico</h2>
            <button class="ml-auto rounded p-1 hover:bg-muted" title="Fechar" @click="acessoAberto = false"><X class="size-5" /></button>
          </div>
          <p class="mb-3 text-sm text-muted-foreground">
            Para quem não está marcado, o Histórico não existe: não aparece no menu e a página responde "não encontrada".
            Quem você liberar só vê — não libera mais ninguém.
          </p>
          <div v-if="acessoErro" class="mb-3 rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">{{ acessoErro }}</div>
          <ul class="divide-y rounded-md border">
            <li v-for="p in acesso" :key="p.id" class="flex items-center gap-3 px-3 py-2 text-sm">
              <input
                :id="`acesso-${p.id}`"
                type="checkbox"
                class="size-4"
                :checked="p.liberado"
                :disabled="p.pode_gerenciar || salvandoAcesso === p.id || (!p.liberado && !!p.situacao)"
                @change="mudarAcesso(p, $event)"
              />
              <label :for="`acesso-${p.id}`" class="flex-1 cursor-pointer">
                {{ p.nome }}
                <span v-if="p.situacao" class="ml-1 text-xs text-amber-700">({{ p.situacao }} — desmarque para tirar)</span>
              </label>
              <span v-if="p.pode_gerenciar" class="text-xs text-muted-foreground">você (quem libera)</span>
            </li>
          </ul>
          <p class="mt-3 text-xs text-muted-foreground">Só aparecem administradores. Liberar ou tirar alguém também fica registrado no Histórico.</p>
        </div>
      </div>
    </Teleport>
  </div>
</template>
