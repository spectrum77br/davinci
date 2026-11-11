<script setup lang="ts">
// ── Ouvidoria › Jurídico (11/11/2026) ───────────────────────────────────────
// Vinicius: "painel jurídico … no padrão da conciliação ou chamado, as mesmas cores, de forma
// simples sem informação desnecessária": data, nº do processo, origem (Denúncia / Logística / à
// mão), autor ("beneficiário"), réu ("contra quem"), advogado, última movimentação e Jusbrasil.
// Escolheu a Opção B: + Assunto, Situação e o Raio-X à direita. No mesmo dia: "esse prazo pode
// tirar" (saiu o Próximo prazo) e entrou o Pedido / referência — na Denúncia é o próprio caso (Caso 006:
// "as denúncias não têm pedido, vai confundir"), na Logística o pedido do Bling, no Manual o que digitarem.
// Os casos da Denúncia que foram ao advogado e os chamados encaminhados ao jurídico entram
// sozinhos (backend: routers/juridico.py); o resto pelo "+ novo processo".
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { AlertCircle, ExternalLink, Loader2, Plus, RotateCcw, Search, Trash2, X } from 'lucide-vue-next'
import { isoDateBrt, isoToday } from '~/lib/date'

type Origem = 'denuncia' | 'logistica' | 'manual'
type Situacao = 'aguardando_numero' | 'em_andamento' | 'audiencia' | 'sentenca' | 'encerrado'
// fonte 'djen' = lida no Diário de Justiça Eletrônico Nacional pelo Vigia do Jurídico (07:30)
type Mov = {
  id: number; data: string; texto: string; fonte: string; criado_por: string | null
  orgao?: string | null; tipo?: string | null; link?: string | null; texto_completo?: string | null
}
type Processo = {
  id: number
  origem: Origem
  denuncia_caso_id: number | null
  caso_codigo: string | null
  chamado_id: string | null
  pedido: string | null
  encaminhado_em: string | null
  numero: string | null
  lado: 'autor' | 'reu'
  autor: string | null
  autor_obs: string | null
  reu: string | null
  reu_obs: string | null
  advogado: string | null
  advogado_escritorio: string | null
  assunto: string | null
  situacao: Situacao
  vara: string | null
  valor_causa: number | null
  jusbrasil_link: string | null
  // 11/11: a última tentativa do robô do Mac mini no Jusbrasil e, se falhou, o motivo
  jusbrasil_tentado_em?: string | null
  jusbrasil_erro?: string | null
  observacao: string | null
  ultima_mov: Mov | null
  criado_por: string | null
  atualizado_por: string | null
  atualizado_em: string | null
}
type Leitura = {
  quando: string; origem: string; por: string | null; processos: number; publicacoes: number
  novas: number; falhas: number; erro: string | null
}
type Detalhe = Processo & {
  movimentacoes: Mov[]
  documentos: { pasta: { envio_em: string | null; pronta_em: string | null } | null; pasta_url: string | null; dossie_url: string | null }
}

const { api } = useApi()
const podeEditar = useCan('denuncia', 'edit')
const podeApagar = useCan('denuncia', 'delete')

const SITUACOES: { value: Situacao; label: string; cls: string }[] = [
  { value: 'aguardando_numero', label: 'Aguardando nº', cls: 'bg-slate-500/15 text-slate-700 dark:text-slate-300' },
  { value: 'em_andamento', label: 'Em andamento', cls: 'bg-sky-500/15 text-sky-700 dark:text-sky-300' },
  { value: 'audiencia', label: 'Audiência marcada', cls: 'bg-amber-500/15 text-amber-700 dark:text-amber-300' },
  { value: 'sentenca', label: 'Sentença', cls: 'bg-violet-500/15 text-violet-700 dark:text-violet-300' },
  { value: 'encerrado', label: 'Encerrado', cls: 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300' },
]
const ORIGENS: { value: Origem; label: string; cls: string }[] = [
  { value: 'denuncia', label: 'Denúncia', cls: 'bg-violet-500/15 text-violet-700 dark:text-violet-300' },
  { value: 'logistica', label: 'Logística', cls: 'bg-sky-500/15 text-sky-700 dark:text-sky-300' },
  { value: 'manual', label: 'Manual', cls: 'bg-slate-500/15 text-slate-700 dark:text-slate-300' },
]
const situacaoInfo = (s: Situacao) => SITUACOES.find((x) => x.value === s) || SITUACOES[0]!
const origemInfo = (o: Origem) => ORIGENS.find((x) => x.value === o) || ORIGENS[2]!

const itens = ref<Processo[]>([])
type Pedido = {
  id: number; origem: string; pedido_por: string | null; jusbrasil: boolean; pedido_em: string
  pego_em: string | null; progresso: string | null; terminado_em: string | null; ok: boolean | null; resultado: string | null
}
const diario = ref<{ mac_mini: Leitura | null; navegador: Leitura | null; jusbrasil?: Leitura | null }>({ mac_mini: null, navegador: null, jusbrasil: null })
const pedido = ref<Pedido | null>(null)
const hoje = ref(isoToday())
const carregando = ref(false)
const erro = ref('')

const aba = ref<'todos' | Origem>('todos')
// 'andando' = Em andamento + Audiência + Sentença
const filtroSituacao = ref<'all' | 'andando' | Situacao>('all')
const filtroLado = ref<'all' | 'autor' | 'reu'>('all')
const busca = ref('')

function erroDe(e: unknown): string {
  const d = (e as { data?: { detail?: unknown } })?.data?.detail
  if (d && typeof d === 'object' && 'code' in d) {
    const code = String((d as { code: string }).code)
    return {
      juridico_link_invalido: 'o link do Jusbrasil precisa começar com https://',
      juridico_valor_invalido: 'valor da causa inválido (ex.: 1.249,90)',
      juridico_data_invalida: 'data inválida',
      juridico_movimentacao_vazia: 'escreva o que aconteceu',
      juridico_processo_automatico: 'esse processo veio de um caso/chamado e voltaria sozinho; não dá pra apagar',
      juridico_sem_numero: 'preencha o nº do processo (20 dígitos) para buscar no Diário',
      juridico_djen_indisponivel: 'o Diário (DJEN) não respondeu agora; tente de novo em alguns minutos',
      forbidden: 'sem permissão',
    }[code] || code
  }
  return (e as Error)?.message || 'erro'
}

async function carregar() {
  carregando.value = true
  erro.value = ''
  try {
    const r = await api<{ hoje: string; itens: Processo[]; diario?: typeof diario.value; pedido?: Pedido | null }>('/api/juridico/processos')
    itens.value = r.itens
    if (r.diario) diario.value = r.diario
    pedido.value = r.pedido || null
    vigiarPedido()
    hoje.value = r.hoje
    if (aberto.value && aberto.value.id) await abrir(aberto.value.id, false)
  } catch (e) {
    erro.value = erroDe(e)
  } finally {
    carregando.value = false
  }
}
onMounted(() => {
  void carregar()
  window.addEventListener('keydown', escFecha)
})
onBeforeUnmount(() => window.removeEventListener('keydown', escFecha))
function escFecha(e: KeyboardEvent) {
  if (e.key === 'Escape' && (aberto.value || novo.value)) fechar()
}

// dd/mm (ISO 'YYYY-MM-DD')
const dm = (d: string | null | undefined) => (d ? `${d.slice(8, 10)}/${d.slice(5, 7)}` : '—')
const dma = (d: string | null | undefined) => (d ? `${d.slice(8, 10)}/${d.slice(5, 7)}/${d.slice(0, 4)}` : '—')
const andando = (p: Processo) => ['em_andamento', 'audiencia', 'sentenca'].includes(p.situacao)

const contagemAba = computed(() => {
  const c: Record<string, number> = { todos: itens.value.length, denuncia: 0, logistica: 0, manual: 0 }
  for (const p of itens.value) c[p.origem] = (c[p.origem] || 0) + 1
  return c
})
const daAba = computed(() => (aba.value === 'todos' ? itens.value : itens.value.filter((p) => p.origem === aba.value)))
const resumo = computed(() => ({
  total: daAba.value.length,
  aguardando: daAba.value.filter((p) => p.situacao === 'aguardando_numero').length,
  andando: daAba.value.filter(andando).length,
  encerrados: daAba.value.filter((p) => p.situacao === 'encerrado').length,
}))
const visiveis = computed(() => {
  const q = busca.value.trim().toLowerCase()
  return daAba.value.filter((p) => {
    const f = filtroSituacao.value
    if (f === 'andando' && !andando(p)) return false
    if (f !== 'all' && f !== 'andando' && p.situacao !== f) return false
    if (filtroLado.value !== 'all' && p.lado !== filtroLado.value) return false
    if (q) {
      const alvo = [p.numero, p.autor, p.reu, p.advogado, p.caso_codigo, p.pedido, p.assunto, p.vara]
        .filter(Boolean).join(' ').toLowerCase()
      if (!alvo.includes(q)) return false
    }
    return true
  })
})
function trocarAba(k: string) {
  aba.value = k as 'todos' | Origem
}
function filtrar(f: typeof filtroSituacao.value) {
  filtroSituacao.value = filtroSituacao.value === f ? 'all' : f
}
function limpar() {
  filtroSituacao.value = 'all'
  filtroLado.value = 'all'
  busca.value = ''
}

// ── Raio-X ────────────────────────────────────────────────────────────────
const aberto = ref<Detalhe | null>(null)
const abrindo = ref(false)
const salvando = ref(false)
const erroRaio = ref('')
const novo = ref(false)
const novoForm = ref({ origem: 'manual' as 'manual' | 'logistica', pedido: '', numero: '', lado: 'autor' as 'autor' | 'reu', autor: '', reu: '', advogado: '', assunto: '', encaminhado_em: isoToday() })
const movForm = ref({ data: isoToday(), texto: '' })

async function abrir(id: number, mostrarCarregando = true) {
  novo.value = false
  if (aberto.value?.id !== id) {
    avisoDiario.value = ''
    abertas.value = new Set()
  }
  erroRaio.value = ''
  if (mostrarCarregando) abrindo.value = true
  try {
    aberto.value = await api<Detalhe>(`/api/juridico/processos/${id}`)
  } catch (e) {
    erroRaio.value = erroDe(e)
  } finally {
    abrindo.value = false
  }
}
function fechar() {
  aberto.value = null
  novo.value = false
}
function abrirNovo() {
  aberto.value = null
  erroRaio.value = ''
  novoForm.value = { origem: 'manual', pedido: '', numero: '', lado: 'autor', autor: '', reu: '', advogado: '', assunto: '', encaminhado_em: isoToday() }
  novo.value = true
}
async function criar() {
  salvando.value = true
  erroRaio.value = ''
  try {
    const r = await api<{ processo: Processo }>('/api/juridico/processos', { method: 'POST', body: novoForm.value })
    novo.value = false
    await carregar()
    await abrir(r.processo.id)
  } catch (e) {
    erroRaio.value = erroDe(e)
  } finally {
    salvando.value = false
  }
}
// salva um campo do Raio-X (no change do input)
async function salvar(campos: Record<string, unknown>) {
  if (!aberto.value) return
  salvando.value = true
  erroRaio.value = ''
  try {
    const r = await api<{ processo: Processo }>(`/api/juridico/processos/${aberto.value.id}`, { method: 'PUT', body: campos })
    const i = itens.value.findIndex((p) => p.id === r.processo.id)
    if (i >= 0) itens.value[i] = r.processo
    aberto.value = { ...aberto.value, ...r.processo }
  } catch (e) {
    erroRaio.value = erroDe(e)
    await abrir(aberto.value.id, false)
  } finally {
    salvando.value = false
  }
}
const valorDe = (e: Event) => (e.target as HTMLInputElement).value
async function adicionarMov() {
  if (!aberto.value || !movForm.value.texto.trim()) return
  salvando.value = true
  erroRaio.value = ''
  try {
    await api(`/api/juridico/processos/${aberto.value.id}/movimentacoes`, { method: 'POST', body: movForm.value })
    movForm.value = { data: isoToday(), texto: '' }
    await carregar()
  } catch (e) {
    erroRaio.value = erroDe(e)
  } finally {
    salvando.value = false
  }
}
async function apagarMov(m: Mov) {
  if (!confirm(`Apagar a movimentação de ${dm(m.data)}?`)) return
  try {
    await api(`/api/juridico/movimentacoes/${m.id}`, { method: 'DELETE' })
    await carregar()
  } catch (e) {
    erroRaio.value = erroDe(e)
  }
}
// ── Diário oficial (DJEN, CNJ) ───────────────────────────────────────────────
// 11/11: o DJEN responde 403 ao servidor do DaVinci (fica fora do Brasil) e libera o navegador
// (CORS aberto). Então quem consulta é o NAVEGADOR de quem clica, e manda as publicações à API.
// Todo dia às 07:30 o Mac mini da Makisa faz o mesmo sozinho (juridico_diario.py no mini).
const URL_DJEN = 'https://comunicaapi.pje.jus.br/api/v1/comunicacao'
const PAUSA_DJEN_MS = 3200 // o DJEN aceita 20 consultas por minuto
const dorme = (ms: number) => new Promise((r) => setTimeout(r, ms))
const numero20 = (n: string | null | undefined) => {
  const d = (n || '').replace(/\D/g, '')
  return d.length === 20 ? d : null
}
async function lerDjen(numero: string): Promise<unknown[]> {
  const todos: unknown[] = []
  for (let pagina = 1; ; pagina++) {
    const u = `${URL_DJEN}?numeroProcesso=${numero}&pagina=${pagina}&itensPorPagina=100`
    let r = await fetch(u)
    if (r.status === 429) {
      await dorme(61000)
      r = await fetch(u)
    }
    if (!r.ok) throw new Error(`o Diário respondeu ${r.status}`)
    const d = (await r.json()) as { count?: number; items?: unknown[] }
    const lote = d.items || []
    todos.push(...lote)
    if (!lote.length || todos.length >= (d.count || 0) || lote.length < 100) return todos
    await dorme(PAUSA_DJEN_MS)
  }
}
async function diarioDoProcesso(p: { id: number; numero: string | null }) {
  const n = numero20(p.numero)
  if (!n) throw new Error('sem nº de 20 dígitos')
  const itens = await lerDjen(n)
  return api<{ novas: number; publicacoes: number }>(`/api/juridico/processos/${p.id}/diario`, { method: 'POST', body: { itens } })
}

// botão da janela do processo
const buscandoDiario = ref(false)
const avisoDiario = ref('')
const abertas = ref<Set<number>>(new Set())
function alternarTexto(id: number) {
  const n = new Set(abertas.value)
  if (n.has(id)) n.delete(id)
  else n.add(id)
  abertas.value = n
}
async function buscarDiario() {
  if (!aberto.value) return
  buscandoDiario.value = true
  avisoDiario.value = ''
  erroRaio.value = ''
  try {
    const r = await diarioDoProcesso(aberto.value)
    avisoDiario.value = r.novas
      ? `${r.novas} publicação(ões) nova(s) no Diário`
      : r.publicacoes ? 'nada novo no Diário' : 'o Diário ainda não tem publicação deste processo'
    await carregar()
  } catch (e) {
    erroRaio.value = `Diário: ${(e as Error)?.message || erroDe(e)}`
  } finally {
    buscandoDiario.value = false
  }
}

// ── botão Atualizar (11/11, Vinicius: "no botão atualizar, atualizar Jusbrasil e CNJ junto") ──
// 1) recarrega; 2) o navegador busca o Diário (CNJ) de todos os processos com nº, um por vez;
// 3) pede ao Mac mini a leitura do Jusbrasil (perfil 50, devagar) — no máximo uma a cada 2 h.
// Todo dia às 06:00 o próprio DaVinci faz o pedido 3 sozinho, e o mini lê o Diário junto.
const diarioTodos = ref<{ rodando: boolean; feitos: number; total: number; msg: string }>({ rodando: false, feitos: 0, total: 0, msg: '' })
async function buscarDiarioTodos() {
  const alvo = itens.value.filter((p) => numero20(p.numero) && p.situacao !== 'encerrado')
  if (!alvo.length) return
  diarioTodos.value = { rodando: true, feitos: 0, total: alvo.length, msg: '' }
  let novas = 0
  let publicacoes = 0
  let falhas = 0
  for (const [i, p] of alvo.entries()) {
    if (i) await dorme(PAUSA_DJEN_MS)
    try {
      const r = await diarioDoProcesso(p)
      novas += r.novas
      publicacoes += r.publicacoes
    } catch {
      falhas += 1
    }
    diarioTodos.value.feitos = i + 1
  }
  try {
    await api('/api/juridico/leituras', { method: 'POST', body: { processos: alvo.length - falhas, publicacoes, novas, falhas } })
  } catch { /* a leitura em si já foi gravada processo a processo */ }
  diarioTodos.value = {
    rodando: false, feitos: alvo.length, total: alvo.length,
    msg: `Diário: ${novas} nova(s)${falhas ? ` · ${falhas} sem resposta` : ''}`,
  }
}
const atualizando = ref(false)
async function atualizarTudo() {
  if (atualizando.value) return
  atualizando.value = true
  try {
    await carregar()
    if (podeEditar.value) {
      try {
        const r = await api<{ pedido: Pedido | null; motivo: string }>('/api/juridico/atualizar', { method: 'POST' })
        if (r.pedido) pedido.value = r.pedido
      } catch { /* sem o pedido do Jusbrasil, o Diário segue */ }
      await buscarDiarioTodos()
      await carregar()
    }
  } finally {
    atualizando.value = false
  }
}
// enquanto o Mac mini lê o Jusbrasil, a lista se recarrega a cada 30 s (os andamentos vão entrando)
let timerPedido: ReturnType<typeof setTimeout> | null = null
const pedidoAndando = computed(() => {
  const p = pedido.value
  return !!p && !p.terminado_em && Date.now() - new Date(p.pedido_em).getTime() < 3 * 3600 * 1000
})
function vigiarPedido() {
  if (timerPedido) clearTimeout(timerPedido)
  timerPedido = null
  if (pedidoAndando.value) timerPedido = setTimeout(() => { void carregar() }, 30000)
}
onBeforeUnmount(() => { if (timerPedido) clearTimeout(timerPedido) })

// topo: a última leitura do Diário e do Jusbrasil. Mac mini sem ler há mais de 26 h = vermelho
const quandoBr = (iso: string) => {
  const d = new Date(iso)
  const dia = isoDateBrt(d) === isoToday() ? 'hoje' : `${String(d.getDate()).padStart(2, '0')}/${String(d.getMonth() + 1).padStart(2, '0')}`
  return `${dia} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}
const velho = (iso: string) => Date.now() - new Date(iso).getTime() > 26 * 3600 * 1000
const statusTopo = computed(() => {
  const partes: { texto: string; alerta: boolean }[] = []
  if (diarioTodos.value.rodando) partes.push({ texto: `Diário: buscando ${diarioTodos.value.feitos}/${diarioTodos.value.total}…`, alerta: false })
  else if (diarioTodos.value.msg) partes.push({ texto: diarioTodos.value.msg, alerta: false })
  else {
    const m = diario.value.mac_mini
    const n = diario.value.navegador
    const ultimo = [m, n].filter(Boolean).sort((a, b) => (b!.quando > a!.quando ? 1 : -1))[0]
    if (!ultimo) partes.push({ texto: 'Diário: ainda não lido', alerta: true })
    else partes.push({ texto: `Diário ${quandoBr(ultimo.quando)}`, alerta: velho(ultimo.quando) || !!ultimo.erro })
  }
  const p = pedido.value
  if (pedidoAndando.value && p) {
    partes.push({ texto: p.pego_em ? `Jusbrasil: ${p.progresso || 'lendo…'}` : 'Jusbrasil: pedido enviado ao Mac mini…', alerta: false })
  } else {
    const j = diario.value.jusbrasil
    if (p && p.terminado_em && p.ok === false) partes.push({ texto: `Jusbrasil: ${p.resultado || 'falhou'}`, alerta: true })
    else if (j) partes.push({ texto: `Jusbrasil ${quandoBr(j.quando)} · ${j.novas} novo(s)`, alerta: velho(j.quando) || !!j.erro })
    else partes.push({ texto: 'Jusbrasil: ainda não lido', alerta: false })
  }
  return partes
})

const podeApagarAberto = computed(() => !!aberto.value && !aberto.value.denuncia_caso_id && !aberto.value.chamado_id && podeApagar.value)
async function apagarProcesso() {
  if (!aberto.value || !confirm('Apagar este processo do painel? Não dá pra desfazer.')) return
  try {
    await api(`/api/juridico/processos/${aberto.value.id}`, { method: 'DELETE' })
    aberto.value = null
    await carregar()
  } catch (e) {
    erroRaio.value = erroDe(e)
  }
}
const casoUrl = (id: number) => `/denuncia?aba=casos&caso=${id}`
const valorBr = (v: number | null) => (v === null ? '' : v.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 }))

const inputBase = 'h-7 rounded-md border bg-background px-2 text-xs disabled:opacity-70'
const inputCls = `${inputBase} w-full min-w-0`
const selectCls = 'h-7 w-full rounded-md border bg-background px-1.5 text-xs disabled:opacity-70'
</script>

<template>
  <div class="min-w-0 space-y-2.5">
    <div class="flex flex-wrap items-center gap-3 rounded-md border bg-card px-3 py-1.5">
      <h1 class="text-[15px] font-bold">Jurídico</h1>
      <div class="flex flex-1 flex-wrap justify-center gap-0.5 text-xs font-semibold">
        <button
          v-for="t in [{ k: 'todos', l: 'Todos' }, ...ORIGENS.map((o) => ({ k: o.value, l: o.label }))]"
          :key="t.k"
          type="button"
          class="border-b-2 px-2.5 py-1"
          :class="aba === t.k ? 'border-primary text-primary' : 'border-transparent text-muted-foreground hover:text-foreground'"
          @click="trocarAba(t.k)"
        >
          {{ t.l }} <span class="font-medium text-muted-foreground">{{ contagemAba[t.k] || 0 }}</span>
        </button>
      </div>
      <div class="ml-auto flex items-center gap-1.5 text-xs font-semibold">
        <span class="flex max-w-[460px] items-center gap-1.5 truncate text-[11px] font-normal" :title="'Diário (CNJ) e Jusbrasil: o Mac mini lê sozinho todo dia às 06:00; o Atualizar busca na hora (o Jusbrasil no máximo a cada 2 h)'">
          <template v-for="(st, k) in statusTopo" :key="k">
            <span v-if="k" class="text-muted-foreground">·</span>
            <span :class="st.alerta ? 'text-red-600 dark:text-red-400' : 'text-muted-foreground'">{{ st.texto }}</span>
          </template>
        </span>
        <button
          type="button"
          class="inline-flex items-center gap-1 rounded-md border px-2.5 py-1 hover:bg-muted disabled:opacity-50"
          :disabled="carregando || atualizando"
          title="Recarrega, busca agora o Diário (CNJ) dos processos com nº e pede ao Mac mini a leitura do Jusbrasil (no máximo uma a cada 2 h)"
          @click="atualizarTudo"
        >
          <RotateCcw class="size-3.5" :class="{ 'animate-spin': carregando || atualizando }" /> Atualizar
        </button>
        <button type="button" class="inline-flex items-center gap-1 whitespace-nowrap rounded-md bg-primary px-2.5 py-1 text-primary-foreground disabled:opacity-50" :disabled="!podeEditar" @click="abrirNovo">
          <Plus class="size-3.5" /> novo processo
        </button>
      </div>
    </div>

    <div class="flex flex-wrap items-center justify-center gap-2 text-xs">
      <label class="inline-flex h-7 items-center gap-1 rounded-md border bg-card px-2">
        <span class="font-semibold text-muted-foreground">Situação</span>
        <select v-model="filtroSituacao" class="bg-transparent outline-none">
          <option value="all">Todas</option>
          <option v-for="s in SITUACOES" :key="s.value" :value="s.value">{{ s.label }}</option>
        </select>
      </label>
      <label class="inline-flex h-7 items-center gap-1 rounded-md border bg-card px-2" title="de que lado nós estamos no processo">
        <span class="font-semibold text-muted-foreground">Lado</span>
        <select v-model="filtroLado" class="bg-transparent outline-none">
          <option value="all">Todos</option>
          <option value="autor">Nós somos o autor</option>
          <option value="reu">Nós somos o réu</option>
        </select>
      </label>
      <label class="inline-flex h-7 min-w-0 items-center gap-1 rounded-md border bg-card px-2">
        <Search class="size-3.5 text-muted-foreground" />
        <input v-model="busca" class="w-64 bg-transparent outline-none" placeholder="buscar nº do processo, autor, réu, caso">
      </label>
      <button type="button" class="text-muted-foreground underline" @click="limpar">limpar</button>
      <span class="text-muted-foreground">{{ visiveis.length }} de {{ daAba.length }} processos</span>
    </div>

    <div class="grid grid-cols-2 gap-2 lg:grid-cols-4">
      <button type="button" class="rounded-md border bg-card px-3 py-1.5 text-center hover:bg-muted/40" title="mostrar todos" @click="filtroSituacao = 'all'">
        <span class="block text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Processos</span>
        <span class="block text-xl font-bold leading-tight">{{ resumo.total }}</span>
      </button>
      <button type="button" class="rounded-md border bg-card px-3 py-1.5 text-center hover:bg-muted/40" :class="filtroSituacao === 'aguardando_numero' ? 'ring-2 ring-primary' : ''" title="enviados ao advogado, ainda sem nº do processo" @click="filtrar('aguardando_numero')">
        <span class="block text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Aguardando nº</span>
        <span class="block text-xl font-bold leading-tight text-slate-600 dark:text-slate-300">{{ resumo.aguardando }}</span>
      </button>
      <button type="button" class="rounded-md border bg-card px-3 py-1.5 text-center hover:bg-muted/40" :class="filtroSituacao === 'andando' ? 'ring-2 ring-primary' : ''" title="Em andamento, Audiência marcada e Sentença" @click="filtrar('andando')">
        <span class="block text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Em andamento</span>
        <span class="block text-xl font-bold leading-tight text-sky-700 dark:text-sky-300">{{ resumo.andando }}</span>
      </button>
      <button type="button" class="rounded-md border bg-card px-3 py-1.5 text-center hover:bg-muted/40" :class="filtroSituacao === 'encerrado' ? 'ring-2 ring-primary' : ''" @click="filtrar('encerrado')">
        <span class="block text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Encerrados</span>
        <span class="block text-xl font-bold leading-tight text-emerald-700 dark:text-emerald-300">{{ resumo.encerrados }}</span>
      </button>
    </div>

    <div v-if="erro" class="flex items-center gap-2 rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-600 dark:text-red-400">
      <AlertCircle class="size-4" /> {{ erro }}
    </div>

    <div>
      <div class="flex min-w-0 flex-col rounded-md border bg-card">
        <div class="relative flex items-center justify-center rounded-t-md bg-[#1f3a5f] px-3 py-1.5 text-xs font-semibold text-white">
          <span>Processos — uma linha por processo</span>
          <span class="absolute right-3 hidden text-[11px] font-normal text-slate-300 2xl:block">clique na linha abre o processo</span>
        </div>
        <div class="overflow-auto">
          <!-- larguras fixas; só a Última movimentação estica (a janela do processo abre no meio) -->
          <table class="w-full min-w-[1344px] table-fixed border-collapse text-[11.5px]">
            <colgroup>
              <col class="w-[48px]"><col class="w-[178px]"><col class="w-[130px]"><col class="w-[104px]">
              <col class="w-[130px]"><col class="w-[170px]"><col class="w-[116px]">
              <col class="w-[132px]"><col class="w-[92px]"><col class="w-[118px]"><col>
            </colgroup>
            <thead class="sticky top-0 z-10 bg-muted text-[10px] uppercase tracking-wide text-muted-foreground">
              <tr>
                <th class="border-b px-1.5 py-1 text-center font-semibold" colspan="4">Identificação</th>
                <th class="border-b border-l border-l-slate-300 px-1.5 py-1 text-center font-semibold dark:border-l-slate-600" colspan="3">Partes</th>
                <th class="border-b border-l border-l-slate-300 px-1.5 py-1 text-center font-semibold dark:border-l-slate-600" colspan="4">Acompanhamento</th>
              </tr>
              <tr>
                <th class="border-b px-1.5 py-1 text-center font-semibold" title="Quando foi encaminhado ao jurídico">Data</th>
                <th class="border-b px-1.5 py-1 text-center font-semibold">Nº do processo</th>
                <th class="border-b px-1.5 py-1 text-center font-semibold" title="Denúncia: o caso · Logística: o pedido do Bling · Manual: a referência que digitarem">Pedido / Ref.</th>
                <th class="border-b px-1.5 py-1 text-left font-semibold">Origem</th>
                <th class="border-b border-l border-l-slate-300 px-1.5 py-1 text-left font-semibold dark:border-l-slate-600" title="Quem entra com o processo">Autor</th>
                <th class="border-b px-1.5 py-1 text-left font-semibold" title="Contra quem é o processo">Réu</th>
                <th class="border-b px-1.5 py-1 text-left font-semibold">Advogado</th>
                <th class="border-b border-l border-l-slate-300 px-1.5 py-1 text-center font-semibold dark:border-l-slate-600">Assunto</th>
                <th class="border-b px-1.5 py-1 text-center font-semibold">Jusbrasil</th>
                <th class="border-b px-1.5 py-1 text-center font-semibold">Situação</th>
                <!-- 11/11 (Vinicius): a Última movimentação fica por último -->
                <th class="border-b px-1.5 py-1 text-left font-semibold">Última movimentação</th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="carregando && !itens.length">
                <td colspan="11" class="py-8 text-center text-muted-foreground"><Loader2 class="mr-1.5 inline size-4 animate-spin" />carregando…</td>
              </tr>
              <tr v-else-if="!visiveis.length">
                <td colspan="11" class="py-8 text-center text-muted-foreground">{{ itens.length ? 'nenhum processo com esse filtro' : 'nenhum processo ainda' }}</td>
              </tr>
              <tr
                v-for="p in visiveis"
                :key="p.id"
                class="h-10 cursor-pointer border-b hover:bg-muted/40"
                :class="[aberto?.id === p.id ? 'bg-blue-50 shadow-[inset_3px_0_0_hsl(var(--primary))] dark:bg-blue-950/30' : '', p.situacao === 'encerrado' ? 'opacity-60' : '']"
                @click="abrir(p.id)"
              >
                <td class="px-1.5 text-center text-muted-foreground" :title="dma(p.encaminhado_em)">{{ dm(p.encaminhado_em) }}</td>
                <td class="truncate px-1.5 text-center">
                  <span v-if="p.numero" class="cursor-text select-all font-mono text-[11px]" :title="p.numero" @click.stop>{{ p.numero }}</span>
                  <span v-else class="italic text-muted-foreground">aguardando nº</span>
                </td>
                <td class="truncate px-1.5 text-center">
                  <a v-if="p.denuncia_caso_id" :href="casoUrl(p.denuncia_caso_id)" target="_blank" rel="noopener" class="font-medium hover:underline" title="abrir o caso numa nova aba" @click.stop>{{ p.caso_codigo || 'caso' }}</a>
                  <span v-else-if="p.pedido" class="cursor-text select-all font-mono text-[11px]" :title="p.pedido" @click.stop>{{ p.pedido }}</span>
                  <span v-else class="text-muted-foreground">—</span>
                </td>
                <td class="truncate px-1.5">
                  <span class="rounded px-1.5 py-0.5 text-[11px] font-semibold" :class="origemInfo(p.origem).cls">{{ origemInfo(p.origem).label }}</span>
                </td>
                <td class="border-l border-l-slate-300 px-1.5 dark:border-l-slate-600">
                  <div class="truncate" :title="p.autor || ''">{{ p.autor || '—' }}</div>
                  <div v-if="p.autor_obs" class="truncate text-[10.5px] text-muted-foreground">{{ p.autor_obs }}</div>
                </td>
                <td class="px-1.5">
                  <div class="truncate" :title="p.reu || ''">{{ p.reu || '—' }}</div>
                  <div v-if="p.reu_obs" class="truncate text-[10.5px] text-muted-foreground">{{ p.reu_obs }}</div>
                </td>
                <td class="px-1.5">
                  <div class="truncate" :title="p.advogado || ''">{{ p.advogado || '—' }}</div>
                  <div v-if="p.advogado_escritorio" class="truncate text-[10.5px] text-muted-foreground">{{ p.advogado_escritorio }}</div>
                </td>
                <td class="truncate border-l border-l-slate-300 px-1.5 text-center dark:border-l-slate-600" :title="p.assunto || ''">{{ p.assunto || '—' }}</td>
                <!-- 11/11 (Vinicius: "como vou saber se ele tentou o caso 9 e não conseguiu?"): a última
                     tentativa do robô falhou → vermelho, com a hora e o motivo ao passar o mouse -->
                <td class="px-1.5 text-center">
                  <component
                    :is="p.jusbrasil_link ? 'a' : 'span'"
                    v-if="p.jusbrasil_erro"
                    :href="p.jusbrasil_link || undefined"
                    target="_blank"
                    rel="noopener"
                    class="inline-flex items-center gap-1 rounded border border-red-500/40 bg-red-500/10 px-1.5 py-px text-[11px] font-semibold text-red-700 dark:text-red-300"
                    :title="`${p.jusbrasil_tentado_em ? quandoBr(p.jusbrasil_tentado_em) + ' · ' : ''}${p.jusbrasil_erro}`"
                    @click.stop
                  >
                    <span class="size-[7px] rounded-full bg-red-600" /> falhou
                  </component>
                  <a
                    v-else-if="p.jusbrasil_link"
                    :href="p.jusbrasil_link"
                    target="_blank"
                    rel="noopener"
                    class="inline-flex items-center gap-1 rounded border border-emerald-500/40 bg-emerald-500/5 px-1.5 py-px text-[11px] text-emerald-700 hover:bg-emerald-500/10 dark:text-emerald-300"
                    :title="p.jusbrasil_tentado_em ? `abrir no Jusbrasil · lido pelo robô ${quandoBr(p.jusbrasil_tentado_em)}` : 'abrir no Jusbrasil'"
                    @click.stop
                  >
                    <span class="size-[7px] rounded-full bg-emerald-600" /> vinculado <ExternalLink class="size-3" />
                  </a>
                  <span v-else class="inline-block rounded border px-1.5 py-px text-[11px] text-muted-foreground" :title="p.numero ? 'o robô do Mac mini procura no Jusbrasil às 06:00' : 'preencha o nº do processo'">vincular</span>
                </td>
                <td class="px-1.5 text-center">
                  <span class="inline-block w-[104px] rounded py-0.5 text-center text-[11px] font-semibold" :class="situacaoInfo(p.situacao).cls">{{ situacaoInfo(p.situacao).label }}</span>
                </td>
                <td class="px-1.5">
                  <template v-if="p.ultima_mov">
                    <div class="truncate" :title="p.ultima_mov.texto">{{ p.ultima_mov.texto }}</div>
                    <div class="truncate text-[10.5px] text-muted-foreground">{{ dm(p.ultima_mov.data) }}{{ p.ultima_mov.fonte === 'djen' ? ' · Diário oficial' : p.ultima_mov.fonte === 'jusbrasil' ? ' · Jusbrasil' : '' }}</div>
                  </template>
                  <span v-else class="text-muted-foreground">—</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="border-t px-3 py-1.5 text-center text-[11px] text-muted-foreground">Data = quando foi encaminhado ao jurídico. Os casos da Denúncia que vão ao advogado entram sozinhos; o resto pelo "+ novo processo".</div>
      </div>

      <!-- 11/11 (Vinicius): "tirar o raio-x do lado … quando clicar no processo ele abre uma tela no
           meio quadrada com tudo" — a janela do processo (e a do novo processo) no meio da tela -->
      <div v-if="aberto || novo || abrindo" class="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 p-4" @click.self="fechar">
      <div class="flex max-h-[90vh] w-full flex-col overflow-hidden rounded-lg border bg-card shadow-xl" :class="novo ? 'max-w-md' : 'max-w-[920px]'">
        <div class="relative flex shrink-0 items-center justify-center bg-[#1f3a5f] px-3 py-2 text-sm font-semibold text-white">
          <span>{{ novo ? 'Novo processo' : 'Processo' }}<template v-if="aberto && !novo && aberto.caso_codigo"> · {{ aberto.caso_codigo }}</template><template v-if="aberto && !novo && aberto.reu"> · {{ aberto.reu }}</template></span>
          <button type="button" class="absolute right-3 rounded p-0.5 text-white/80 hover:bg-white/10 hover:text-white" title="fechar (Esc)" @click="fechar"><X class="size-4" /></button>
        </div>
        <div class="min-h-0 overflow-auto">
        <div v-if="erroRaio" class="mx-3 mt-2 rounded border border-red-500/40 bg-red-500/10 px-2 py-1 text-xs text-red-600 dark:text-red-400">{{ erroRaio }}</div>
        <div v-if="abrindo && !aberto" class="py-8 text-center text-xs text-muted-foreground"><Loader2 class="mr-1.5 inline size-4 animate-spin" />abrindo…</div>

        <!-- novo -->
        <div v-else-if="novo" class="space-y-2 px-3 py-2.5 text-xs">
          <dl class="grid grid-cols-[96px_minmax(0,1fr)] items-center gap-x-2 gap-y-1.5">
            <dt class="text-muted-foreground">Origem</dt>
            <dd>
              <select v-model="novoForm.origem" :class="selectCls">
                <option value="manual">Manual</option>
                <option value="logistica">Logística (de um pedido)</option>
              </select>
            </dd>
            <dt class="text-muted-foreground">Pedido / Ref.</dt><dd><input v-model="novoForm.pedido" :class="[inputCls, 'font-mono']" :placeholder="novoForm.origem === 'logistica' ? 'nº do pedido no Bling' : 'nº de referência'"></dd>
            <dt class="text-muted-foreground">Nº do processo</dt><dd><input v-model="novoForm.numero" :class="[inputCls, 'font-mono']" placeholder="vazio = aguardando nº"></dd>
            <dt class="text-muted-foreground">Nós somos</dt>
            <dd>
              <select v-model="novoForm.lado" :class="selectCls">
                <option value="autor">o autor (nós entramos)</option>
                <option value="reu">o réu (entraram contra nós)</option>
              </select>
            </dd>
            <dt class="text-muted-foreground">Autor</dt><dd><input v-model="novoForm.autor" :class="inputCls" placeholder="quem entra com o processo"></dd>
            <dt class="text-muted-foreground">Réu</dt><dd><input v-model="novoForm.reu" :class="inputCls" placeholder="contra quem"></dd>
            <dt class="text-muted-foreground">Advogado</dt><dd><input v-model="novoForm.advogado" :class="inputCls"></dd>
            <dt class="text-muted-foreground">Assunto</dt><dd><input v-model="novoForm.assunto" :class="inputCls" placeholder="ex.: extravio, consumidor"></dd>
            <dt class="text-muted-foreground">Data</dt><dd><input v-model="novoForm.encaminhado_em" type="date" :class="inputCls"></dd>
          </dl>
          <div class="flex justify-end gap-2 pt-1">
            <button type="button" class="rounded-md border px-2.5 py-1 font-semibold hover:bg-muted" @click="fechar">cancelar</button>
            <button type="button" class="rounded-md bg-primary px-2.5 py-1 font-semibold text-primary-foreground disabled:opacity-50" :disabled="salvando" @click="criar">{{ salvando ? 'criando…' : 'criar' }}</button>
          </div>
        </div>

        <!-- processo -->
        <div v-else-if="aberto" class="space-y-3 px-4 py-3 text-xs">
          <section class="flex items-center gap-2">
            <span v-if="aberto.numero" class="cursor-text select-all font-mono text-[12px] font-bold">{{ aberto.numero }}</span>
            <span v-else class="italic text-muted-foreground">aguardando nº</span>
            <span class="ml-auto rounded px-2 py-0.5 text-[11px] font-semibold" :class="situacaoInfo(aberto.situacao).cls">{{ situacaoInfo(aberto.situacao).label }}</span>
          </section>

          <div class="grid gap-x-5 gap-y-3 md:grid-cols-2">
          <div class="min-w-0">
          <section class="border-t pt-2">
            <h4 class="mb-1 text-center font-bold text-[#1f3a5f] dark:text-slate-200">Processo</h4>
            <dl class="grid grid-cols-[96px_minmax(0,1fr)] items-center gap-x-2 gap-y-1">
              <dt class="text-muted-foreground">Nº</dt>
              <dd><input :value="aberto.numero || ''" :disabled="!podeEditar" :class="[inputCls, 'font-mono']" placeholder="0000000-00.0000.0.00.0000" @change="(e) => salvar({ numero: valorDe(e) })"></dd>
              <dt class="text-muted-foreground">Pedido / Ref.</dt>
              <dd v-if="aberto.denuncia_caso_id" class="px-2"><a :href="casoUrl(aberto.denuncia_caso_id)" target="_blank" rel="noopener" class="font-medium text-primary hover:underline">{{ aberto.caso_codigo || 'abrir o caso' }} ↗</a></dd>
              <dd v-else><input :value="aberto.pedido || ''" :disabled="!podeEditar || !!aberto.chamado_id" :class="[inputCls, 'font-mono']" :placeholder="aberto.origem === 'logistica' ? 'nº do pedido no Bling' : 'nº de referência'" @change="(e) => salvar({ pedido: valorDe(e) })"></dd>
              <dt class="text-muted-foreground">Situação</dt>
              <dd>
                <select :value="aberto.situacao" :disabled="!podeEditar" :class="selectCls" @change="(e) => salvar({ situacao: valorDe(e) })">
                  <option v-for="s in SITUACOES" :key="s.value" :value="s.value">{{ s.label }}</option>
                </select>
              </dd>
              <dt class="text-muted-foreground">Nós somos</dt>
              <dd>
                <select :value="aberto.lado" :disabled="!podeEditar" :class="selectCls" @change="(e) => salvar({ lado: valorDe(e) })">
                  <option value="autor">o autor (nós entramos)</option>
                  <option value="reu">o réu (entraram contra nós)</option>
                </select>
              </dd>
              <dt class="text-muted-foreground">Autor</dt>
              <dd class="flex gap-1">
                <input :value="aberto.autor || ''" :disabled="!podeEditar" :class="inputCls" placeholder="quem entra" @change="(e) => salvar({ autor: valorDe(e) })">
                <input :value="aberto.autor_obs || ''" :disabled="!podeEditar" :class="[inputBase, 'w-[72px] shrink-0']" placeholder="obs" @change="(e) => salvar({ autor_obs: valorDe(e) })">
              </dd>
              <dt class="text-muted-foreground">Réu</dt>
              <dd class="flex gap-1">
                <input :value="aberto.reu || ''" :disabled="!podeEditar" :class="inputCls" placeholder="contra quem" @change="(e) => salvar({ reu: valorDe(e) })">
                <input :value="aberto.reu_obs || ''" :disabled="!podeEditar" :class="[inputBase, 'w-[72px] shrink-0']" placeholder="obs" @change="(e) => salvar({ reu_obs: valorDe(e) })">
              </dd>
              <dt class="text-muted-foreground">Advogado</dt>
              <dd class="flex gap-1">
                <input :value="aberto.advogado || ''" :disabled="!podeEditar" :class="inputCls" @change="(e) => salvar({ advogado: valorDe(e) })">
                <input :value="aberto.advogado_escritorio || ''" :disabled="!podeEditar" :class="[inputBase, 'w-[92px] shrink-0']" placeholder="escritório" @change="(e) => salvar({ advogado_escritorio: valorDe(e) })">
              </dd>
              <dt class="text-muted-foreground">Assunto</dt>
              <dd><input :value="aberto.assunto || ''" :disabled="!podeEditar" :class="inputCls" @change="(e) => salvar({ assunto: valorDe(e) })"></dd>
              <dt class="text-muted-foreground">Vara</dt>
              <dd><input :value="aberto.vara || ''" :disabled="!podeEditar" :class="inputCls" placeholder="ex.: JEC de Piracicaba · TJSP" @change="(e) => salvar({ vara: valorDe(e) })"></dd>
              <dt class="text-muted-foreground">Valor da causa</dt>
              <dd><input :value="valorBr(aberto.valor_causa)" :disabled="!podeEditar" :class="inputCls" placeholder="R$" @change="(e) => salvar({ valor_causa: valorDe(e) })"></dd>
              <dt class="text-muted-foreground">Data</dt>
              <dd><input :value="aberto.encaminhado_em || ''" type="date" :disabled="!podeEditar" :class="inputCls" title="quando foi encaminhado ao jurídico" @change="(e) => salvar({ encaminhado_em: valorDe(e) })"></dd>
              <dt class="text-muted-foreground">Jusbrasil</dt>
              <dd class="flex items-center gap-1">
                <input :value="aberto.jusbrasil_link || ''" :disabled="!podeEditar" :class="inputCls" placeholder="colar o link do processo" @change="(e) => salvar({ jusbrasil_link: valorDe(e) })">
                <a v-if="aberto.jusbrasil_link" :href="aberto.jusbrasil_link" target="_blank" rel="noopener" class="shrink-0 text-muted-foreground hover:text-foreground" title="abrir no Jusbrasil"><ExternalLink class="size-3.5" /></a>
              </dd>
              <dt class="text-muted-foreground">Origem</dt>
              <dd class="truncate">
                <span class="rounded px-1.5 py-0.5 text-[11px] font-semibold" :class="origemInfo(aberto.origem).cls">{{ origemInfo(aberto.origem).label }}</span>
              </dd>
            </dl>
          </section>

          </div>
          <div class="min-w-0 space-y-2.5">
          <section class="border-t pt-2">
            <div class="mb-1 flex items-center justify-center gap-2">
              <h4 class="font-bold text-[#1f3a5f] dark:text-slate-200">Movimentações<span v-if="aberto.movimentacoes.length" class="font-normal text-muted-foreground"> ({{ aberto.movimentacoes.length }})</span></h4>
              <button
                v-if="podeEditar"
                type="button"
                class="ml-auto inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-[11px] font-semibold hover:bg-muted disabled:opacity-50"
                :disabled="buscandoDiario || !aberto.numero"
                :title="aberto.numero ? 'lê agora no Diário de Justiça Eletrônico Nacional (o Mac mini faz sozinho todo dia às 06:00)' : 'preencha o nº do processo primeiro'"
                @click="buscarDiario"
              >
                <Loader2 v-if="buscandoDiario" class="size-3 animate-spin" />
                <RotateCcw v-else class="size-3" />
                buscar no Diário
              </button>
            </div>
            <div v-if="avisoDiario" class="mb-1 text-center text-[11px] text-emerald-700 dark:text-emerald-300">{{ avisoDiario }}</div>
            <div v-if="podeEditar" class="mb-1.5 flex gap-1">
              <input v-model="movForm.data" type="date" :class="[inputBase, 'w-[130px] shrink-0']">
              <input v-model="movForm.texto" :class="inputCls" placeholder="o que aconteceu" @keydown.enter.prevent="adicionarMov">
              <button type="button" class="shrink-0 rounded-md bg-primary px-2 font-semibold text-primary-foreground disabled:opacity-50" :disabled="salvando || !movForm.texto.trim()" @click="adicionarMov">+</button>
            </div>
            <!-- 11/11 (Vinicius: "e o histórico da movimentação como ficaria"): linha do tempo, a mais
                 nova em cima; quem lançou (ou "Jusbrasil" quando entrar sozinho) à direita -->
            <ol v-if="aberto.movimentacoes.length" class="mt-1 max-h-[320px] overflow-auto pr-1">
              <li v-for="(m, i) in aberto.movimentacoes" :key="m.id" class="group relative ml-[7px] border-l-2 border-slate-200 pb-2.5 pl-3 last:pb-0 dark:border-slate-700">
                <span class="absolute -left-[6px] top-[3px] size-2.5 rounded-full ring-2 ring-background" :class="i === 0 ? 'bg-[#1f3a5f] dark:bg-slate-200' : 'bg-slate-300 dark:bg-slate-600'" />
                <div class="flex items-center gap-1.5">
                  <span class="font-semibold tabular-nums">{{ dma(m.data) }}</span>
                  <span v-if="i === 0" class="rounded bg-sky-500/15 px-1 text-[10px] font-semibold text-sky-700 dark:text-sky-300">última</span>
                  <span v-if="m.fonte === 'djen'" class="ml-auto shrink-0 rounded bg-violet-500/10 px-1 text-[10px] font-semibold text-violet-700 dark:text-violet-300" title="lida no Diário de Justiça Eletrônico Nacional (CNJ)">Diário oficial</span>
                  <span v-else-if="m.fonte === 'jusbrasil'" class="ml-auto shrink-0 rounded bg-emerald-500/10 px-1 text-[10px] font-semibold text-emerald-700 dark:text-emerald-300" title="andamento lido no Jusbrasil pelo Mac mini (perfil 50)">Jusbrasil</span>
                  <span v-else class="ml-auto truncate text-[10px] text-muted-foreground">{{ m.criado_por ? `lançado por ${m.criado_por}` : 'lançado à mão' }}</span>
                  <button v-if="podeEditar && m.fonte !== 'djen' && m.fonte !== 'jusbrasil'" type="button" class="invisible shrink-0 text-muted-foreground hover:text-red-600 group-hover:visible" title="apagar esta movimentação" @click="apagarMov(m)"><X class="size-3.5" /></button>
                </div>
                <div class="mt-0.5 leading-snug" :class="i === 0 ? '' : 'text-muted-foreground'">{{ m.texto }}</div>
                <div v-if="m.fonte === 'djen'" class="mt-0.5 flex flex-wrap items-center gap-x-2 text-[10.5px] text-muted-foreground">
                  <span v-if="m.orgao && !m.texto.includes(m.orgao)" class="truncate">{{ m.orgao }}</span>
                  <button v-if="m.texto_completo" type="button" class="text-primary hover:underline" @click="alternarTexto(m.id)">{{ abertas.has(m.id) ? 'esconder o texto' : 'ver o texto' }}</button>
                  <a v-if="m.link" :href="m.link" target="_blank" rel="noopener" class="text-primary hover:underline">documento ↗</a>
                </div>
                <div v-if="abertas.has(m.id) && m.texto_completo" class="mt-1 max-h-[260px] overflow-auto whitespace-pre-wrap rounded border bg-muted/40 px-2 py-1.5 text-[11px] leading-snug text-foreground">{{ m.texto_completo }}</div>
              </li>
            </ol>
            <div v-else class="text-center text-muted-foreground">nenhuma movimentação ainda</div>
            <div class="mt-1 text-center text-[10.5px] text-muted-foreground">O Diário oficial (CNJ) e o Jusbrasil entram sozinhos todo dia às 06:00 pelo Mac mini, nos processos com nº. O Atualizar do topo busca na hora.</div>
          </section>

          <section v-if="aberto.documentos.pasta_url || aberto.documentos.dossie_url || aberto.denuncia_caso_id" class="border-t pt-2">
            <h4 class="mb-1 text-center font-bold text-[#1f3a5f] dark:text-slate-200">Documentos</h4>
            <div v-if="aberto.denuncia_caso_id" class="flex justify-between py-0.5"><span>Caso da Denúncia (provas e dossiê)</span><a :href="casoUrl(aberto.denuncia_caso_id)" target="_blank" rel="noopener" class="text-primary hover:underline">abrir</a></div>
            <div v-if="aberto.documentos.pasta_url" class="flex justify-between py-0.5">
              <span>Pasta enviada ao advogado (zip)<span v-if="aberto.documentos.pasta?.envio_em" class="text-muted-foreground"> · {{ dm(aberto.documentos.pasta.envio_em.slice(0, 10)) }}</span></span>
              <a :href="aberto.documentos.pasta_url" class="text-primary hover:underline">baixar</a>
            </div>
            <div v-if="aberto.documentos.dossie_url" class="flex justify-between py-0.5"><span>Dossiê do chamado</span><a :href="aberto.documentos.dossie_url" target="_blank" rel="noopener" class="text-primary hover:underline">abrir</a></div>
          </section>

          <section class="border-t pt-2">
            <h4 class="mb-1 text-center font-bold text-[#1f3a5f] dark:text-slate-200">Observação</h4>
            <textarea :value="aberto.observacao || ''" :disabled="!podeEditar" rows="3" class="w-full rounded-md border bg-background px-2 py-1 text-xs disabled:opacity-70" @change="(e) => salvar({ observacao: (e.target as HTMLTextAreaElement).value })" />
          </section>

          </div>
          </div>

          <div class="flex items-center gap-2 border-t pt-2 text-[10.5px] text-muted-foreground">
            <span v-if="aberto.atualizado_por">alterado por {{ aberto.atualizado_por }}</span>
            <span v-else-if="aberto.criado_por">criado por {{ aberto.criado_por }}</span>
            <span v-if="salvando" class="inline-flex items-center gap-1"><Loader2 class="size-3 animate-spin" /> salvando…</span>
            <button v-if="podeApagarAberto" type="button" class="ml-auto inline-flex items-center gap-1 text-red-600 hover:underline" @click="apagarProcesso"><Trash2 class="size-3" /> apagar processo</button>
          </div>
        </div>
        </div>
      </div>
      </div>
    </div>
  </div>
</template>
