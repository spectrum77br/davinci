<script lang="ts">
// Aba "Manual do agente" do Atendimento (13/11/2026; antes "Manual da IA", 25/09/2026). O
// Eduardo: "essa parte aqui não é toda da IA? a gente nem usa mais, usa o agente". A IA antiga
// não roda mais; o AGENTE lê ao vivo, pelo `consultar_manual`, as regras POR ASSUNTO
// (`tipo = categoria`) — a política e os fatos de cada assunto, que valem ACIMA do banco de
// respostas (o jeito de responder vem do banco: o que a equipe já respondeu). As regras de
// SEGURANÇA e de ESTILO não chegam ao agente (o conteúdo delas está fixo no prompt dele,
// apps/api/app/services/atendimento/agente/prompt.py): ficam no banco como estão — nada é
// desativado nem apagado —, só fora da tela.
//
// A tela, no padrão da aba do Banco: agrupada por assunto (na ordem do manual; a regra SEM
// assunto, que o agente não lê, no fim e avisada), com a busca por texto, o filtro da
// plataforma, as ativas e desativadas de cada assunto, o QUANDO/FAÇA legível — a marca
// `precisa_humano=true` vira o selo "pessoa decide" (e `=false`, "agente responde") e as
// lacunas ({numero_pedido}, {rastreio}…) viram chips; só na exibição, o texto salvo não muda —
// e quem mexeu por último ("editada por Fulano em 12/11", o `atualizado_por_nome` do GET
// /regras). Criar, editar, ativar/desativar e apagar continuam como antes; a API não deixa
// nascer conflito (409 `regra_conflitante`) e a tela pinta o que já existe de vermelho.
//
// Fatos do agente (Claude, 20/10/2026, spec 5.2): abaixo do manual, a lista
// mínima de aprovação de `atendimento_fatos` — o que o agente pode AFIRMAR ao
// cliente. O candidato (a correção de um 👎, a recusa padrão, o texto novo de
// política) espera o admin; só o ativo vale. Quem lê vê (com a fonte e onde o
// fato vale); só ADMIN aprova — com o texto corrigido antes, se quiser — ou
// reprova, e os dois ficam com quem e quando (o backend recusa com 403).
import { LACUNAS, categoriaLabel, prioridadeDe, type Categoria, type Regra } from '~/components/AtendimentoPlataforma.vue'

// A regra como o GET /regras manda (o conflito e quem mexeu por último).
export type RegraDoManual = Regra & { em_conflito?: boolean; conflita_com?: string[]; atualizado_por_nome?: string | null }

// Os pedaços do QUANDO/FAÇA para a tela: o texto, a lacuna que o agente preenche e a marca de
// quem decide. Só a exibição: o texto salvo não muda.
export type Pedaco = { tipo: 'texto' | 'lacuna' | 'pessoa' | 'agente'; valor: string; rotulo?: string }
const RE_MARCAS = /\{([a-z_]+)\}|precisa_humano\s*=\s*(true|false)/gi
export function rotuloDaLacuna(chave: string): string {
  return LACUNAS.find((l) => l.chave === chave)?.label || chave.replace(/_/g, ' ')
}
export function pedacos(texto: string | null | undefined): Pedaco[] {
  const t = String(texto ?? '')
  const saida: Pedaco[] = []
  let ultimo = 0
  for (const m of t.matchAll(RE_MARCAS)) {
    const i = m.index ?? 0
    if (i > ultimo) saida.push({ tipo: 'texto', valor: t.slice(ultimo, i) })
    if (m[1]) saida.push({ tipo: 'lacuna', valor: m[1], rotulo: rotuloDaLacuna(m[1]) })
    else saida.push({ tipo: m[2].toLowerCase() === 'true' ? 'pessoa' : 'agente', valor: m[0] })
    ultimo = i + m[0].length
  }
  if (ultimo < t.length) saida.push({ tipo: 'texto', valor: t.slice(ultimo) })
  return saida
}

// "editada por Fulano em 12/11" (o dia de São Paulo); sem autor (o importador), só o dia.
export function quemEditou(r: Pick<RegraDoManual, 'atualizado_por_nome' | 'updated_at'>): string {
  const d = r.updated_at ? new Date(r.updated_at) : null
  const dia = d && !Number.isNaN(d.getTime()) ? d.toLocaleDateString('pt-BR', { timeZone: 'America/Sao_Paulo', day: '2-digit', month: '2-digit' }) : ''
  const nome = (r.atualizado_por_nome || '').trim()
  if (nome) return dia ? `editada por ${nome} em ${dia}` : `editada por ${nome}`
  return dia ? `editada em ${dia}` : ''
}

// A busca: todas as palavras, sem acento nem caixa, no QUANDO, no FAÇA e no assunto.
export function semAcento(s: string | null | undefined): string {
  return String(s ?? '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase()
}
export function bateComBusca(r: Pick<RegraDoManual, 'quando' | 'faca' | 'categoria'>, termo: string, nomeDoAssunto = ''): boolean {
  const palavras = semAcento(termo).split(/\s+/).filter(Boolean)
  if (!palavras.length) return true
  const alvo = semAcento(`${r.quando} ${r.faca} ${nomeDoAssunto} ${r.categoria || ''}`)
  return palavras.every((p) => alvo.includes(p))
}

// A ordem em que o agente lê as regras de um assunto (`ia.organizar_manual`): a menor
// prioridade primeiro; a de todas as plataformas (e caixas) antes da específica.
export function ordemDoAgente<T extends RegraDoManual>(lista: T[]): T[] {
  return lista
    .map((r, i) => ({ r, i }))
    .sort((a, b) =>
      prioridadeDe(a.r) - prioridadeDe(b.r)
      || Number(!!a.r.plataforma) - Number(!!b.r.plataforma)
      || (a.r.plataforma || '').localeCompare(b.r.plataforma || '')
      || Number(!!a.r.canal) - Number(!!b.r.canal)
      || (a.r.canal || '').localeCompare(b.r.canal || '')
      || a.i - b.i)
    .map((x) => x.r)
}

export type GrupoDoManual = {
  chave: string
  assunto: string | null
  titulo: string
  hint: string
  // O assunto da regra saiu da lista do manual: o agente não o usa.
  foraDaLista: boolean
  regras: RegraDoManual[]
  ativas: number
  inativas: number
}
// Por assunto, na ordem do manual; a regra sem assunto (o agente não lê) no fim. As contagens
// vêm de `todas` (sem o filtro da tela), as regras de `regras` (as que passam no filtro).
export function agruparPorAssunto(regras: RegraDoManual[], categorias: Pick<Categoria, 'id' | 'nome' | 'descricao' | 'ordem'>[], todas: RegraDoManual[] = regras): GrupoDoManual[] {
  const pos = new Map(categorias.map((c, i) => [c.id, c.ordem ?? i]))
  const info = new Map(categorias.map((c) => [c.id, c]))
  const por = new Map<string, RegraDoManual[]>()
  for (const r of regras) por.set(r.categoria || '', [...(por.get(r.categoria || '') || []), r])
  const conta = (k: string, ativa: boolean) => todas.filter((r) => (r.categoria || '') === k && r.ativa === ativa).length
  return [...por.entries()]
    .sort(([a], [b]) => {
      if (!a || !b) return a ? -1 : b ? 1 : 0
      const pa = pos.get(a) ?? Number.MAX_SAFE_INTEGER
      const pb = pos.get(b) ?? Number.MAX_SAFE_INTEGER
      return pa - pb || categoriaLabel(a).localeCompare(categoriaLabel(b), 'pt-BR')
    })
    .map(([k, lista]) => {
      const c = k ? info.get(k) : undefined
      return {
        chave: k || '-',
        assunto: k || null,
        titulo: k ? c?.nome || categoriaLabel(k) : 'Sem assunto',
        hint: (k && c?.descricao) || '',
        foraDaLista: !!k && !c,
        regras: ordemDoAgente(lista),
        ativas: conta(k, true),
        inativas: conta(k, false),
      }
    })
}
</script>

<script setup lang="ts">
import { BadgeCheck, BookOpen, ExternalLink, Loader2, Pencil, Plus, RotateCcw, Search, Trash2, TriangleAlert, X } from 'lucide-vue-next'
import {
  CATEGORIAS,
  CATEGORIAS_SO_HUMANO,
  ERROS,
  ESCOPOS_FATO,
  ESTADOS_FATO,
  FONTES_FATO,
  PLATAFORMAS_COM_CANAL,
  PRIORIDADE_PADRAO,
  alcanceDoFato,
  canalLabel,
  categoriasDe,
  comoLista,
  comoObjeto,
  erroDaApi,
  estadoFatoInfo,
  fmtDataHora,
  plataformaInfo,
  registrarCategorias,
  tipoRegraDe,
  type EstadoFato,
  type Fato,
} from '~/components/AtendimentoPlataforma.vue'
import type { ConflitoForm, FormRegra } from '~/components/AtendimentoManualForm.vue'

const props = defineProps<{ canEdit: boolean; canDelete: boolean }>()
const { api } = useApi()
const toasts = useToasts()

const regras = ref<RegraDoManual[]>([])
// `conflitos` do GET /regras, cru (o formato é do backend; lido abaixo).
const conflitosApi = ref<unknown>(null)
const carregando = ref(false)
const erro = ref<string | null>(null)
const salvando = ref(false)

// `silencioso`: depois de salvar, relê para os conflitos acompanharem (mudar
// o assunto de uma regra pode criar ou desfazer um) sem piscar a lista.
async function carregar(silencioso = false) {
  if (!silencioso) {
    carregando.value = true
    erro.value = null
  }
  try {
    const r = await api<unknown>('/api/atendimento/regras')
    regras.value = comoLista<RegraDoManual>(r, 'regras')
    conflitosApi.value = r && typeof r === 'object' && !Array.isArray(r) ? ((r as Record<string, unknown>).conflitos ?? null) : null
    erro.value = null
  } catch (e: any) {
    if (!silencioso) erro.value = erroDaApi(e, 'Não consegui carregar o manual').texto
  } finally {
    if (!silencioso) carregando.value = false
  }
}

// ─── assuntos (categorias) ──────────────────────────────────────────────────
// A lista oficial vem do GET /categorias (a tabela do manual base); sem ela
// (API antiga, fora do ar), a lista fixa da tela — a mesma do backend.
const PADRAO: Categoria[] = Object.entries(CATEGORIAS).map(([id, nome], i) => ({ id, nome, so_humano: CATEGORIAS_SO_HUMANO.includes(id), ativa: true, ordem: i }))
const categorias = ref<Categoria[]>(PADRAO)
async function carregarCategorias() {
  try {
    const l = categoriasDe(await api<unknown>('/api/atendimento/categorias'))
    if (l.length) {
      categorias.value = l
      registrarCategorias(l)
    }
  } catch {
    // fica a lista fixa
  }
}
onMounted(() => {
  void carregar()
  void carregarCategorias()
})

// ─── conflitos ──────────────────────────────────────────────────────────────
// O backend manda a lista de conflitos; cada item junta as regras que batem
// entre si. O formato de cada item não é fixo (ids soltos, regras inteiras,
// {regras: [...]}, {regra, com}…): a tela junta todo id de regra que achar
// no item. Também vale o conflito que vier na própria regra (`conflitos`).
function idsDe(v: unknown): string[] {
  if (typeof v === 'string') return v ? [v] : []
  if (Array.isArray(v)) return v.flatMap(idsDe)
  if (v && typeof v === 'object') {
    const id = (v as Record<string, unknown>).id
    return typeof id === 'string' && id ? [id] : []
  }
  return []
}
const CHAVES_IDS = ['ids', 'regras', 'regra_ids', 'regra_id', 'id', 'regra', 'com', 'com_id', 'outra', 'outra_id', 'conflita_com', 'existente', 'regra_existente']
function idsDoConflito(item: unknown): string[] {
  if (Array.isArray(item) || typeof item === 'string') return idsDe(item)
  if (!item || typeof item !== 'object') return []
  const o = item as Record<string, unknown>
  return [...new Set(CHAVES_IDS.flatMap((k) => idsDe(o[k])))]
}
const conflitos = computed(() => {
  const mapa = new Map<string, Set<string>>()
  const existentes = new Set(regras.value.map((r) => r.id))
  const ligar = (ids: string[]) => {
    const validos = [...new Set(ids.filter((id) => existentes.has(id)))]
    for (const a of validos) {
      const s = mapa.get(a) ?? new Set<string>()
      for (const b of validos) if (b !== a) s.add(b)
      mapa.set(a, s)
    }
  }
  const lista = conflitosApi.value
  if (Array.isArray(lista)) for (const item of lista) ligar(idsDoConflito(item))
  for (const r of regras.value) {
    const extra = r as RegraDoManual & { em_conflito?: unknown }
    const outras = [...idsDe(r.conflitos), ...idsDe(extra.conflita_com)]
    if (outras.length || r.conflitos === true || extra.em_conflito === true) ligar([r.id, ...outras])
  }
  return mapa
})
function temConflito(id: string) {
  return conflitos.value.has(id)
}

// ─── o manual do agente: só as regras por assunto ───────────────────────────
// Segurança e estilo não chegam ao agente: ficam no banco, fora da tela.
const doAgente = computed(() => regras.value.filter((r) => tipoRegraDe(r) === 'categoria'))
const emConflito = computed(() => doAgente.value.filter((r) => temConflito(r.id)).length)
function nomeDoAssunto(id: string | null | undefined): string {
  if (!id) return 'Sem assunto'
  return categorias.value.find((c) => c.id === id)?.nome || categoriaLabel(id)
}
// O nº de cada regra sai do manual INTEIRO (sem filtro): "bate com a #7" tem
// de continuar certo com a busca ou o filtro de plataforma ligados.
const numeros = computed(() => {
  const m = new Map<string, number>()
  let n = 0
  for (const g of agruparPorAssunto(doAgente.value, categorias.value)) for (const r of g.regras) m.set(r.id, ++n)
  return m
})

const busca = ref('')
const filtroPlataforma = ref('')
const soConflitos = ref(false)
const visiveis = computed(() =>
  doAgente.value.filter((r) =>
    (!filtroPlataforma.value || !r.plataforma || r.plataforma === filtroPlataforma.value)
    && (!soConflitos.value || temConflito(r.id))
    && bateComBusca(r, busca.value, nomeDoAssunto(r.categoria))),
)
const grupos = computed(() => agruparPorAssunto(visiveis.value, categorias.value, doAgente.value))
const ativas = computed(() => doAgente.value.filter((r) => r.ativa).length)
const assuntosComRegra = computed(() => new Set(doAgente.value.filter((r) => r.ativa && r.categoria).map((r) => r.categoria)).size)
function limparFiltros() {
  busca.value = ''
  filtroPlataforma.value = ''
  soConflitos.value = false
}
watch(emConflito, (n) => {
  if (!n) soConflitos.value = false
})

function alcance(r: Pick<Regra, 'plataforma' | 'canal'>) {
  if (!r.plataforma) return 'todas as lojas'
  const p = plataformaInfo(r.plataforma).nome
  return r.canal ? `${p} · ${canalLabel(r.canal)}` : p
}
// As outras regras com que esta bate, com o nº e o começo do "quando".
function outrasDoConflito(id: string): { id: string; numero: number | null; quando: string }[] {
  return [...(conflitos.value.get(id) ?? [])].flatMap((oid) => {
    const r = regras.value.find((x) => x.id === oid)
    return r ? [{ id: oid, numero: numeros.value.get(oid) ?? null, quando: r.quando }] : []
  })
}

// ─── achar uma regra na lista (vindo do conflito ou da regra nova) ──────────
const destacada = ref<string | null>(null)
let tiraDestaque: ReturnType<typeof setTimeout> | null = null
onBeforeUnmount(() => { if (tiraDestaque) clearTimeout(tiraDestaque) })
function rolarAte(id: string) {
  if (typeof document === 'undefined') return false
  const el = document.getElementById(id)
  if (!el) return false
  let suave = true
  try {
    suave = !window.matchMedia('(prefers-reduced-motion: reduce)').matches
  } catch {
    // sem matchMedia — rola suave
  }
  el.scrollIntoView({ block: 'center', behavior: suave ? 'smooth' : 'auto' })
  return true
}
function verRegra(id: string) {
  // O filtro pode estar escondendo a outra regra: some com ele.
  if (!visiveis.value.some((r) => r.id === id)) limparFiltros()
  void nextTick(() => {
    if (!rolarAte(`regra-${id}`)) return
    destacada.value = id
    if (tiraDestaque) clearTimeout(tiraDestaque)
    tiraDestaque = setTimeout(() => { destacada.value = null }, 2500)
  })
}

// ─── formulário (nova e edição) ─────────────────────────────────────────────
// O tipo é sempre "por assunto": a tela só tem o que o agente lê.
const vazio = (): FormRegra => ({ quando: '', faca: '', plataforma: '', canal: '', tipo: 'categoria', categoria: '', prioridade: PRIORIDADE_PADRAO })
const nova = ref<FormRegra>(vazio())
const novaAberta = ref(false)
const conflitoNova = ref<ConflitoForm | null>(null)
const editando = ref<string | null>(null)
const edicao = ref<FormRegra>(vazio())
const conflitoEdicao = ref<ConflitoForm | null>(null)
// Mexeu no formulário depois do 409: o aviso era sobre o que estava antes.
watch(nova, () => { conflitoNova.value = null }, { deep: true })
watch(edicao, () => { conflitoEdicao.value = null }, { deep: true })

function abrirNova(assunto = '') {
  nova.value = { ...vazio(), categoria: assunto }
  conflitoNova.value = null
  novaAberta.value = true
  void nextTick(() => { rolarAte('nova-regra') })
}
function corpo(f: FormRegra) {
  return {
    quando: f.quando.trim(),
    faca: f.faca.trim(),
    plataforma: f.plataforma || null,
    canal: (f.plataforma && f.canal) || null,
    tipo: f.tipo,
    categoria: f.tipo === 'categoria' ? f.categoria || null : null,
    prioridade: Number.isFinite(f.prioridade) ? Math.round(f.prioridade) : PRIORIDADE_PADRAO,
  }
}
// Na edição, o PATCH leva só o que MUDOU. Com o corpo inteiro, uma regra cujo
// assunto saiu do manual (importação do manual base com outra taxonomia, ou
// o assunto desativado) nunca mais salvava: o router confere a `categoria`
// sempre que ela vem no corpo e recusa assunto fora da lista (422
// `categoria_invalida`) — corrigir só o texto do "Faça" obrigava a trocar o
// assunto. Plataforma e caixa vão juntas (o backend confere o par), e tipo
// e assunto também.
type CorpoRegra = ReturnType<typeof corpo>
function mudancas(f: FormRegra, r: Regra): Partial<CorpoRegra> {
  const novo = corpo(f)
  const tipo = tipoRegraDe(r)
  const antes: CorpoRegra = {
    quando: r.quando,
    faca: r.faca,
    plataforma: r.plataforma || null,
    canal: r.canal || null,
    tipo,
    categoria: tipo === 'categoria' ? r.categoria || null : null,
    prioridade: prioridadeDe(r),
  }
  const saida: Partial<CorpoRegra> = {}
  const mudou = (k: keyof CorpoRegra) => novo[k] !== antes[k]
  if (mudou('quando')) saida.quando = novo.quando
  if (mudou('faca')) saida.faca = novo.faca
  if (mudou('plataforma') || mudou('canal')) {
    saida.plataforma = novo.plataforma
    saida.canal = novo.canal
  }
  if (mudou('tipo') || mudou('categoria')) {
    saida.tipo = novo.tipo
    saida.categoria = novo.categoria
  }
  if (mudou('prioridade')) saida.prioridade = novo.prioridade
  return saida
}
function fecharNova() {
  nova.value = vazio()
  conflitoNova.value = null
  novaAberta.value = false
}

function trocar(r: RegraDoManual) {
  const i = regras.value.findIndex((x) => x.id === r.id)
  if (i >= 0) regras.value[i] = r
}
function falhou(e: any, padrao: string) {
  const er = erroDaApi(e, padrao)
  toasts.error(er.texto, er.motivos)
}

// 409 `regra_conflitante` → a frase e a regra que já existe. O backend manda
// a outra regra (inteira ou só o id) em `regra`/`existente`/`conflitos`; a
// que está sendo editada (`ignorar`) não conta.
function conflitoDe(e: any, ignorar?: string | null): ConflitoForm | null {
  const d = e?.data?.detail
  if (!d || typeof d !== 'object' || d.code !== 'regra_conflitante') return null
  const candidatos: unknown[] = [d.regra, d.existente, d.regra_existente, d.conflito, d.com, ...(Array.isArray(d.conflitos) ? d.conflitos : [])]
  let regra: ConflitoForm['regra'] = null
  for (const c of candidatos) {
    if (regra) break
    if (c && typeof c === 'object' && !Array.isArray(c)) {
      const o = c as Record<string, unknown>
      if (typeof o.id === 'string' && o.id !== ignorar && typeof o.quando === 'string') {
        regra = { id: o.id, quando: o.quando, faca: typeof o.faca === 'string' ? o.faca : '' }
        break
      }
    }
    for (const id of idsDoConflito(c)) {
      const r = id !== ignorar ? regras.value.find((x) => x.id === id) : undefined
      if (r) {
        regra = { id: r.id, quando: r.quando, faca: r.faca }
        break
      }
    }
  }
  const det = typeof d.detail === 'string' ? d.detail.trim() : ''
  return { texto: det || ERROS.regra_conflitante, regra, numero: regra ? numeros.value.get(regra.id) ?? null : null }
}

async function criar() {
  if (!nova.value.quando.trim() || !nova.value.faca.trim() || !nova.value.categoria) return
  salvando.value = true
  try {
    const r = comoObjeto<RegraDoManual>(await api<unknown>('/api/atendimento/regras', { method: 'POST', body: corpo(nova.value) }), 'regra')
    if (r) regras.value = [...regras.value, r]
    fecharNova()
    toasts.success('Regra salva', 'O agente passa a seguir na próxima resposta.')
    await carregar(true)
    if (r) verRegra(r.id)
  } catch (e: any) {
    const c = conflitoDe(e)
    if (c) conflitoNova.value = c
    else falhou(e, 'Não consegui salvar a regra')
  } finally {
    salvando.value = false
  }
}

function editar(r: Regra) {
  editando.value = r.id
  edicao.value = {
    quando: r.quando,
    faca: r.faca,
    plataforma: r.plataforma || '',
    canal: r.canal || '',
    tipo: 'categoria',
    categoria: r.categoria || '',
    prioridade: prioridadeDe(r),
  }
  conflitoEdicao.value = null
}
// "editar a outra regra" do aviso de conflito.
function editarPorId(id: string) {
  const r = regras.value.find((x) => x.id === id)
  if (!r) return
  if (editando.value && editando.value !== id && !confirm('Largar a edição desta regra para editar a outra?')) return
  editar(r)
  verRegra(id)
}
async function salvarEdicao(r: Regra) {
  if (!edicao.value.quando.trim() || !edicao.value.faca.trim()) return
  const body = mudancas(edicao.value, r)
  // Nada mudou: não há o que gravar.
  if (!Object.keys(body).length) {
    editando.value = null
    return
  }
  salvando.value = true
  try {
    const x = comoObjeto<RegraDoManual>(await api<unknown>(`/api/atendimento/regras/${encodeURIComponent(r.id)}`, { method: 'PATCH', body }), 'regra')
    trocar(x ?? { ...r, ...body })
    editando.value = null
    await carregar(true)
  } catch (e: any) {
    const c = conflitoDe(e, r.id)
    if (c) conflitoEdicao.value = c
    else falhou(e, 'Não consegui salvar a regra')
  } finally {
    salvando.value = false
  }
}
async function alternar(r: Regra) {
  salvando.value = true
  try {
    const x = comoObjeto<RegraDoManual>(await api<unknown>(`/api/atendimento/regras/${encodeURIComponent(r.id)}`, { method: 'PATCH', body: { ativa: !r.ativa } }), 'regra')
    trocar(x ?? { ...r, ativa: !r.ativa })
    await carregar(true)
  } catch (e: any) {
    // Ativar a segunda regra do mesmo assunto/loja/caixa: diz qual é a outra
    // e leva até ela.
    const c = conflitoDe(e, r.id)
    if (c) {
      const outra = c.regra ? `${c.numero ? `#${c.numero} — ` : ''}Quando: ${c.regra.quando}` : ''
      toasts.error(c.texto, [outra, 'Desative a outra (ou mude o assunto, a plataforma ou a caixa desta) antes de ativar.'].filter(Boolean))
      if (c.regra) verRegra(c.regra.id)
    } else {
      falhou(e, 'Não consegui mudar a regra')
    }
  } finally {
    salvando.value = false
  }
}

// ─── fatos do agente: a lista de aprovação ──────────────────────────────────
// Aprovar e reprovar só admin (o fato é o que o agente afirma, inclusive no
// automático). `canEdit` já é "mexe e edita" (a fase de observação).
const ehAdmin = useIsAdmin()
const podeDecidir = computed(() => props.canEdit && ehAdmin.value)
const fatos = ref<Fato[]>([])
const contagemFatos = ref<Record<string, number>>({})
const carregandoFatos = ref(false)
const erroFatos = ref<string | null>(null)
const filtroFatos = reactive({ estado: 'candidato' as EstadoFato | 'todos', fonte: '', escopo: '', plataforma: '', busca: '' })
// `id` do fato sendo decidido (um por vez); `editandoFato` = o que está com o
// texto aberto para corrigir antes de aprovar.
const decidindo = ref<number | null>(null)
const editandoFato = ref<number | null>(null)
const textoFato = ref('')
const MAX_TEXTO_FATO = 2000

async function carregarFatos() {
  carregandoFatos.value = true
  erroFatos.value = null
  try {
    const query: Record<string, string> = {}
    for (const [k, v] of Object.entries(filtroFatos)) if (v && !(k === 'estado' && v === 'todos')) query[k] = v.trim()
    const r = await api<unknown>('/api/atendimento/fatos', { query })
    fatos.value = comoLista<Fato>(r, 'fatos')
    const c = r && typeof r === 'object' && !Array.isArray(r) ? (r as Record<string, unknown>).contagem : null
    contagemFatos.value = c && typeof c === 'object' ? (c as Record<string, number>) : {}
  } catch (e: any) {
    erroFatos.value = erroDaApi(e, 'Não consegui carregar os fatos do agente').texto
  } finally {
    carregandoFatos.value = false
  }
}
onMounted(() => { void carregarFatos() })
// A busca espera a pessoa parar de digitar; os seletores recarregam na hora.
let esperaBusca: ReturnType<typeof setTimeout> | null = null
watch(() => filtroFatos.busca, () => {
  if (esperaBusca) clearTimeout(esperaBusca)
  esperaBusca = setTimeout(() => void carregarFatos(), 400)
})
watch(() => [filtroFatos.estado, filtroFatos.fonte, filtroFatos.escopo, filtroFatos.plataforma], () => void carregarFatos())
onBeforeUnmount(() => { if (esperaBusca) clearTimeout(esperaBusca) })
const totalFatos = computed(() => Object.values(contagemFatos.value).reduce((a, n) => a + (Number(n) || 0), 0))

function trocarFato(f: Fato) {
  // Fora do filtro de estado depois da decisão: sai da lista (o número acompanha no recarregar).
  if (filtroFatos.estado !== 'todos' && f.estado !== filtroFatos.estado) fatos.value = fatos.value.filter((x) => x.id !== f.id)
  else fatos.value = fatos.value.map((x) => (x.id === f.id ? f : x))
}
function editarFato(f: Fato) {
  editandoFato.value = f.id
  textoFato.value = f.texto
}
async function decidirFato(f: Fato, acao: 'aprovar' | 'reprovar', texto?: string) {
  if (decidindo.value) return
  const novo = (texto ?? '').trim()
  if (texto !== undefined && !novo) return
  if (acao === 'reprovar' && !confirm(`${f.ativo ? 'Desligar' : 'Reprovar'} este fato?\n\n${f.texto}\n\n${f.ativo ? 'O agente deixa de poder afirmá-lo.' : 'Ele fica guardado como reprovado (a mesma correção não volta como candidata).'}`)) return
  decidindo.value = f.id
  try {
    const body = acao === 'aprovar' ? (texto !== undefined && novo !== f.texto ? { texto: novo } : {}) : undefined
    const r = await api<unknown>(`/api/atendimento/fatos/${f.id}/${acao}`, { method: 'POST', body })
    const x = r && typeof r === 'object' && typeof (r as Fato).id === 'number' ? (r as Fato) : null
    trocarFato(x ?? { ...f, ativo: acao === 'aprovar', estado: acao === 'aprovar' ? 'ativo' : 'reprovado', texto: novo || f.texto })
    editandoFato.value = null
    toasts.success(acao === 'aprovar' ? 'Fato aprovado' : 'Fato reprovado', acao === 'aprovar' ? 'O agente passa a poder afirmá-lo.' : undefined)
    void carregarFatos()
  } catch (e: any) {
    const er = erroDaApi(e, acao === 'aprovar' ? 'Não consegui aprovar o fato' : 'Não consegui reprovar o fato')
    toasts.error(er.texto, er.motivos)
  } finally {
    decidindo.value = null
  }
}
function decisaoDe(f: Fato): string {
  if (!f.aprovado_por) return ''
  const quando = f.aprovado_em ? ` em ${fmtDataHora(f.aprovado_em)}` : ''
  return `${f.ativo ? 'aprovado' : 'reprovado'} por ${f.aprovado_por}${quando}`
}

// ─── fatos do agente: o "novo fato" (21/10/2026) ────────────────────────────
// O admin escreve o fato direto, sem esperar uma correção de 👎 virar
// candidata (POST /fatos; quem não é admin leva 403 do backend). Nasce
// APROVADO por quem criou, fonte "o dono". Aqui vale para tudo, para uma
// plataforma ou para uma marca; loja e anúncio ficam só na API.
const MARCAS_FATO = [
  { value: 'uranyx', nome: 'Uranyx' },
  { value: 'charlots', nome: 'Charlots' },
]
const MIN_TEXTO_NOVO_FATO = 10
const MAX_TEXTO_NOVO_FATO = 1000
type FormFato = { texto: string; assunto: string; vale: 'global' | 'plataforma' | 'marca'; plataforma: string; marca: string }
const fatoVazio = (): FormFato => ({ texto: '', assunto: '', vale: 'global', plataforma: '', marca: '' })
const novoFato = ref<FormFato>(fatoVazio())
const novoFatoAberto = ref(false)
const criandoFato = ref(false)
const assuntosNovoFato = computed(() => categorias.value.filter((c) => c.ativa !== false))
const novoFatoPronto = computed(() => {
  const f = novoFato.value
  const n = f.texto.trim().length
  if (n < MIN_TEXTO_NOVO_FATO || n > MAX_TEXTO_NOVO_FATO) return false
  if (f.vale === 'plataforma') return !!f.plataforma
  if (f.vale === 'marca') return !!f.marca
  return true
})
function abrirNovoFato() {
  novoFato.value = fatoVazio()
  novoFatoAberto.value = true
}
function fecharNovoFato() {
  novoFatoAberto.value = false
  novoFato.value = fatoVazio()
}
async function criarFato() {
  if (!novoFatoPronto.value || criandoFato.value) return
  const f = novoFato.value
  criandoFato.value = true
  try {
    const body = {
      texto: f.texto.trim(),
      assunto: f.assunto || null,
      escopo: f.vale,
      plataforma: f.vale === 'plataforma' ? f.plataforma : null,
      marca: f.vale === 'marca' ? f.marca : null,
    }
    await api('/api/atendimento/fatos', { method: 'POST', body })
    fecharNovoFato()
    toasts.success('Fato criado', 'O agente passa a poder afirmá-lo.')
    // Nasce ativo: na aba dos candidatos (ou dos reprovados) ele não apareceria.
    if (filtroFatos.estado === 'ativo' || filtroFatos.estado === 'todos') void carregarFatos()
    else filtroFatos.estado = 'ativo' // o `watch` dos seletores recarrega
  } catch (e: any) {
    const er = erroDaApi(e, 'Não consegui criar o fato')
    toasts.error(er.texto, er.motivos)
  } finally {
    criandoFato.value = false
  }
}

async function apagar(r: Regra) {
  if (!confirm(`Apagar a regra?\n\nQuando: ${r.quando}\nFaça: ${r.faca}\n\nSe for só por um tempo, use "desativar" — ela fica guardada.`)) return
  salvando.value = true
  try {
    await api(`/api/atendimento/regras/${encodeURIComponent(r.id)}`, { method: 'DELETE' })
    regras.value = regras.value.filter((x) => x.id !== r.id)
    await carregar(true)
  } catch (e: any) {
    falhou(e, 'Não consegui apagar a regra')
  } finally {
    salvando.value = false
  }
}
</script>

<template>
  <section class="space-y-3" data-manual-agente>
    <div class="flex flex-wrap items-center gap-2">
      <h2 class="flex items-center gap-1.5 text-sm font-semibold"><BookOpen class="size-4 text-muted-foreground" /> Manual do agente</h2>
      <span class="text-xs text-muted-foreground" data-contagem>{{ ativas }} regra{{ ativas === 1 ? '' : 's' }} ativa{{ ativas === 1 ? '' : 's' }} em {{ assuntosComRegra }} assunto{{ assuntosComRegra === 1 ? '' : 's' }}</span>
      <div class="ml-auto flex flex-wrap items-center gap-2">
        <div class="relative w-full sm:w-64">
          <Search class="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
          <input v-model="busca" class="h-8 w-full rounded-md border bg-background pl-7 pr-2 text-xs" placeholder="buscar no manual…" aria-label="buscar nas regras" maxlength="200" />
        </div>
        <select v-model="filtroPlataforma" class="h-8 rounded-md border bg-background px-2 text-xs" aria-label="filtrar por plataforma">
          <option value="">todas as plataformas</option>
          <option v-for="p in PLATAFORMAS_COM_CANAL" :key="p.value" :value="p.value">valem para {{ p.nome }}</option>
        </select>
        <Button size="sm" variant="outline" class="h-8" :disabled="carregando" title="recarregar o manual" aria-label="recarregar o manual" @click="carregar()">
          <RotateCcw class="size-4" :class="{ 'animate-spin': carregando }" />
        </Button>
        <Button v-if="props.canEdit && !novaAberta" size="sm" class="h-8" @click="abrirNova()">
          <Plus class="mr-1.5 size-4" /> nova regra
        </Button>
      </div>
    </div>
    <p class="text-xs text-muted-foreground" data-explicacao>
      O agente consulta estas regras antes de responder: são a política e os fatos de cada assunto. O jeito de responder vem do
      <b>banco de respostas</b>, o que a equipe já respondeu. Quando os dois divergem, <b>vale a regra daqui</b>.
    </p>

    <!-- regras que já batem entre si -->
    <div v-if="emConflito" class="flex flex-wrap items-center gap-2 rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-700 dark:text-red-300" role="alert">
      <TriangleAlert class="size-4 shrink-0" />
      <span class="min-w-0 flex-1">
        <b>{{ emConflito }} regra{{ emConflito > 1 ? 's' : '' }} em conflito</b> — regras ativas para o mesmo assunto, plataforma e caixa se
        contradizem, e o agente não sabe qual seguir. Desative uma ou mude o assunto, a plataforma ou a caixa dela.
      </span>
      <button type="button" class="shrink-0 underline" :aria-pressed="soConflitos" @click="soConflitos = !soConflitos">
        {{ soConflitos ? 'mostrar todas' : 'mostrar só essas' }}
      </button>
    </div>

    <div v-if="erro" class="rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-600 dark:text-red-400">
      {{ erro }} <button type="button" class="ml-2 text-xs underline" @click="carregar()">tentar de novo</button>
    </div>

    <div v-if="novaAberta" id="nova-regra" class="rounded-lg border bg-card px-3 py-3" data-nova-regra>
      <h3 class="mb-2 text-xs font-semibold">Nova regra</h3>
      <AtendimentoManualForm
        v-model="nova"
        :categorias="categorias"
        :salvando="salvando"
        :conflito="conflitoNova"
        @salvar="criar"
        @cancelar="fecharNova"
        @ver-regra="verRegra"
        @editar-regra="editarPorId"
      />
    </div>

    <div v-if="carregando && !regras.length" class="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 class="size-4 animate-spin" /> carregando…</div>
    <div v-else-if="!doAgente.length && !novaAberta && !erro" class="rounded-lg border bg-card px-3 py-8 text-center text-sm text-muted-foreground">
      Nenhuma regra ainda. Comece pelo assunto que mais aparece (rastreio, nota fiscal, prazo de envio).
    </div>
    <div v-else-if="!visiveis.length && doAgente.length" class="rounded-lg border bg-card px-3 py-6 text-center text-sm text-muted-foreground">
      Nenhuma regra com esse filtro. <button type="button" class="ml-1 underline" @click="limparFiltros()">limpar</button>
    </div>

    <!-- por assunto, na ordem do manual; dentro, na ordem em que o agente lê -->
    <div v-for="g in grupos" :key="g.chave" class="rounded-lg border bg-card" :data-assunto="g.chave">
      <header class="flex flex-wrap items-center gap-x-2 gap-y-1 border-b px-3 py-2">
        <h3 class="text-sm font-semibold">{{ g.titulo }}</h3>
        <span class="rounded-full bg-emerald-500/15 px-2 py-0.5 text-[11px] text-emerald-800 dark:text-emerald-300" data-ativas>{{ g.ativas }} ativa{{ g.ativas === 1 ? '' : 's' }}</span>
        <span v-if="g.inativas" class="rounded-full bg-gray-500/15 px-2 py-0.5 text-[11px] text-muted-foreground" data-inativas>{{ g.inativas }} desativada{{ g.inativas === 1 ? '' : 's' }}</span>
        <span v-if="!g.assunto" class="rounded-full bg-amber-500/20 px-2 py-0.5 text-[11px] text-amber-800 dark:text-amber-300" title="o agente só lê a regra do assunto da mensagem">o agente não lê</span>
        <span v-else-if="g.foraDaLista" class="rounded-full bg-amber-500/20 px-2 py-0.5 text-[11px] text-amber-800 dark:text-amber-300" title="o assunto saiu da lista do manual: o agente não o usa">assunto fora da lista</span>
        <span v-if="g.hint" class="min-w-0 basis-full text-[11px] text-muted-foreground sm:basis-auto sm:flex-1 sm:truncate" :title="g.hint">{{ g.hint }}</span>
        <Button v-if="props.canEdit && g.assunto && !g.foraDaLista" size="sm" variant="ghost" class="ml-auto h-7 px-2 text-xs" :title="`nova regra em ${g.titulo}`" @click="abrirNova(g.assunto)">
          <Plus class="mr-1 size-3.5" /> regra
        </Button>
      </header>
      <p v-if="!g.assunto" class="border-b bg-amber-500/5 px-3 py-1.5 text-[11px] text-amber-800 dark:text-amber-300">
        O agente só lê a regra do assunto da mensagem: edite e escolha o assunto para estas valerem.
      </p>
      <ol class="divide-y">
        <li
          v-for="r in g.regras"
          :id="`regra-${r.id}`"
          :key="r.id"
          class="scroll-mt-20 px-3 py-2.5 transition-shadow"
          :class="[
            r.ativa ? '' : 'opacity-60',
            temConflito(r.id) ? 'border-l-2 border-l-red-500 bg-red-500/5' : '',
            destacada === r.id ? 'ring-2 ring-inset ring-primary/70' : '',
          ]"
          :data-regra="r.id"
        >
          <AtendimentoManualForm
            v-if="editando === r.id"
            v-model="edicao"
            edicao
            :categorias="categorias"
            :salvando="salvando"
            :conflito="conflitoEdicao"
            @salvar="salvarEdicao(r)"
            @cancelar="editando = null"
            @ver-regra="verRegra"
            @editar-regra="editarPorId"
          />
          <div v-else class="flex flex-wrap items-start gap-x-3 gap-y-2">
            <span class="mt-0.5 w-6 shrink-0 text-right text-xs font-semibold tabular-nums" :class="temConflito(r.id) ? 'text-red-600 dark:text-red-400' : 'text-muted-foreground'">#{{ numeros.get(r.id) }}</span>
            <div class="min-w-0 grow basis-72 space-y-1.5">
              <div class="grid gap-x-4 gap-y-1.5 lg:grid-cols-2">
                <div class="min-w-0" data-quando>
                  <div class="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Quando</div>
                  <p class="whitespace-pre-line break-words text-sm leading-relaxed">
                    <template v-for="(p, k) in pedacos(r.quando)" :key="k">
                      <span v-if="p.tipo === 'lacuna'" class="mx-0.5 inline-flex items-center rounded border border-sky-500/40 bg-sky-500/10 px-1 text-[11px] font-medium leading-5 text-sky-800 dark:text-sky-300" :title="`{${p.valor}}: o agente preenche com o dado do pedido`" data-lacuna>{{ p.rotulo }}</span>
                      <span v-else-if="p.tipo === 'pessoa'" class="mx-0.5 inline-flex items-center rounded-full bg-amber-500/20 px-1.5 text-[11px] font-medium leading-5 text-amber-800 dark:text-amber-300" title="a regra pede que uma pessoa decida antes de a resposta sair" data-pessoa-decide>pessoa decide</span>
                      <span v-else-if="p.tipo === 'agente'" class="mx-0.5 inline-flex items-center rounded-full bg-emerald-500/15 px-1.5 text-[11px] font-medium leading-5 text-emerald-800 dark:text-emerald-300" title="a regra deixa o agente responder sem uma pessoa" data-agente-responde>agente responde</span>
                      <template v-else>{{ p.valor }}</template>
                    </template>
                  </p>
                </div>
                <div class="min-w-0" data-faca>
                  <div class="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Faça</div>
                  <p class="whitespace-pre-line break-words text-sm leading-relaxed">
                    <template v-for="(p, k) in pedacos(r.faca)" :key="k">
                      <span v-if="p.tipo === 'lacuna'" class="mx-0.5 inline-flex items-center rounded border border-sky-500/40 bg-sky-500/10 px-1 text-[11px] font-medium leading-5 text-sky-800 dark:text-sky-300" :title="`{${p.valor}}: o agente preenche com o dado do pedido`" data-lacuna>{{ p.rotulo }}</span>
                      <span v-else-if="p.tipo === 'pessoa'" class="mx-0.5 inline-flex items-center rounded-full bg-amber-500/20 px-1.5 text-[11px] font-medium leading-5 text-amber-800 dark:text-amber-300" title="a regra pede que uma pessoa decida antes de a resposta sair" data-pessoa-decide>pessoa decide</span>
                      <span v-else-if="p.tipo === 'agente'" class="mx-0.5 inline-flex items-center rounded-full bg-emerald-500/15 px-1.5 text-[11px] font-medium leading-5 text-emerald-800 dark:text-emerald-300" title="a regra deixa o agente responder sem uma pessoa" data-agente-responde>agente responde</span>
                      <template v-else>{{ p.valor }}</template>
                    </template>
                  </p>
                </div>
              </div>
              <!-- com qual regra esta bate -->
              <div v-if="temConflito(r.id)" class="flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-xs text-red-700 dark:text-red-300">
                <TriangleAlert class="size-3.5 shrink-0" />
                <span class="font-medium">Em conflito</span>
                <template v-if="outrasDoConflito(r.id).length">
                  <span>com</span>
                  <button
                    v-for="o in outrasDoConflito(r.id)"
                    :key="o.id"
                    type="button"
                    class="max-w-[280px] truncate underline"
                    :title="`Quando: ${o.quando}`"
                    @click="verRegra(o.id)"
                  >{{ o.numero ? `#${o.numero}` : 'outra regra' }} ({{ o.quando }})</button>
                </template>
                <span class="text-red-700/80 dark:text-red-300/80">— o agente não sabe qual seguir; desative uma ou mude o assunto/loja/caixa.</span>
              </div>
              <div class="flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
                <span class="rounded-full bg-muted px-2 py-0.5 text-foreground">{{ alcance(r) }}</span>
                <span v-if="prioridadeDe(r) !== PRIORIDADE_PADRAO" class="rounded-full border px-2 py-0.5 tabular-nums" title="menor número = o agente lê antes, dentro do assunto">prioridade {{ prioridadeDe(r) }}</span>
                <span v-if="!r.ativa" class="rounded-full bg-gray-500/15 px-2 py-0.5">desativada</span>
                <span v-if="quemEditou(r)" :title="fmtDataHora(r.updated_at)" data-quem-editou>{{ quemEditou(r) }}</span>
              </div>
            </div>
            <div v-if="props.canEdit" class="ml-auto flex items-center gap-0.5">
              <Button size="sm" variant="ghost" class="h-7 px-2 text-xs" :disabled="salvando" @click="alternar(r)">{{ r.ativa ? 'desativar' : 'ativar' }}</Button>
              <Button size="sm" variant="ghost" class="h-7 px-2" title="editar" aria-label="editar a regra" @click="editar(r)"><Pencil class="size-3.5" /></Button>
              <Button v-if="props.canDelete" size="sm" variant="ghost" class="h-7 px-2 text-red-600" title="apagar" aria-label="apagar a regra" :disabled="salvando" @click="apagar(r)"><Trash2 class="size-3.5" /></Button>
            </div>
          </div>
        </li>
      </ol>
    </div>
  </section>

  <!-- Fatos do agente (Claude): o que ele pode afirmar — a lista de aprovação -->
  <section class="mt-4 rounded-lg border bg-card" aria-labelledby="fatos-do-agente">
    <div class="flex flex-wrap items-center gap-2 border-b px-3 py-2">
      <BadgeCheck class="size-4 text-muted-foreground" />
      <h2 id="fatos-do-agente" class="text-sm font-semibold">Fatos do agente (Claude)</h2>
      <span class="text-xs text-muted-foreground">{{ contagemFatos.ativo ?? 0 }} ativo(s) de {{ totalFatos }}</span>
      <div class="ml-auto flex flex-wrap items-center gap-2">
        <select v-model="filtroFatos.fonte" class="h-8 rounded-md border bg-background px-2 text-xs" aria-label="filtrar fatos pela fonte">
          <option value="">toda fonte</option>
          <option v-for="(label, k) in FONTES_FATO" :key="k" :value="k">{{ label }}</option>
        </select>
        <select v-model="filtroFatos.escopo" class="h-8 rounded-md border bg-background px-2 text-xs" aria-label="filtrar fatos por onde valem">
          <option value="">vale em qualquer lugar</option>
          <option v-for="(label, k) in ESCOPOS_FATO" :key="k" :value="k">vale para {{ label }}</option>
        </select>
        <select v-model="filtroFatos.plataforma" class="h-8 rounded-md border bg-background px-2 text-xs" aria-label="filtrar fatos pela plataforma">
          <option value="">toda plataforma</option>
          <option v-for="p in PLATAFORMAS_COM_CANAL" :key="p.value" :value="p.value">{{ p.nome }}</option>
        </select>
        <input v-model="filtroFatos.busca" class="h-8 w-44 rounded-md border bg-background px-2 text-xs" placeholder="buscar no texto…" aria-label="buscar no texto dos fatos" maxlength="200" />
        <Button size="sm" variant="outline" class="h-8" :disabled="carregandoFatos" title="recarregar os fatos" aria-label="recarregar os fatos" @click="carregarFatos()">
          <RotateCcw class="size-4" :class="{ 'animate-spin': carregandoFatos }" />
        </Button>
        <Button v-if="podeDecidir && !novoFatoAberto" size="sm" class="h-8" @click="abrirNovoFato()">
          <Plus class="mr-1.5 size-4" /> novo fato
        </Button>
      </div>
    </div>
    <p class="border-b px-3 py-2 text-xs text-muted-foreground">
      O que o agente pode <b>afirmar</b> ao cliente, citando a fonte. <b>Candidato</b> (a correção de um 👎, a recusa padrão, um texto novo de política) só vale
      depois que um admin aprova — dá para corrigir o texto antes. Reprovado fica guardado, para a mesma correção não voltar.
      <template v-if="!podeDecidir"> Só administrador aprova ou reprova.</template>
    </p>

    <!-- novo fato (só admin): nasce aprovado por quem criou -->
    <div v-if="podeDecidir && novoFatoAberto" class="border-b bg-muted/30 px-3 py-3">
      <div class="space-y-2">
        <label class="block space-y-1">
          <span class="block text-[11px] font-medium text-muted-foreground">Texto</span>
          <textarea
            v-model="novoFato.texto"
            rows="3"
            :maxlength="MAX_TEXTO_NOVO_FATO"
            class="w-full rounded-md border bg-background px-2 py-1.5 text-sm"
            placeholder="ex.: Todos os produtos são enviados com nota fiscal, que vai junto com o pedido."
          />
        </label>
        <div class="grid gap-2 sm:grid-cols-2 xl:grid-cols-[220px_minmax(180px,1fr)]">
          <label class="block space-y-1">
            <span class="block text-[11px] font-medium text-muted-foreground">Assunto</span>
            <select v-model="novoFato.assunto" class="h-9 w-full rounded-md border bg-background px-2 text-sm">
              <option value="">qualquer assunto</option>
              <option v-for="c in assuntosNovoFato" :key="c.id" :value="c.id">{{ c.nome || categoriaLabel(c.id) }}</option>
            </select>
          </label>
          <div class="space-y-1">
            <span class="block text-[11px] font-medium text-muted-foreground">Vale para</span>
            <div class="flex gap-1.5">
              <select v-model="novoFato.vale" class="h-9 min-w-0 flex-1 rounded-md border bg-background px-2 text-sm" aria-label="o fato vale para">
                <option value="global">tudo (todas as lojas)</option>
                <option value="plataforma">uma plataforma</option>
                <option value="marca">uma marca</option>
              </select>
              <select v-if="novoFato.vale === 'plataforma'" v-model="novoFato.plataforma" class="h-9 min-w-0 flex-1 rounded-md border bg-background px-2 text-sm" aria-label="qual plataforma">
                <option value="" disabled>escolha a plataforma</option>
                <option v-for="p in PLATAFORMAS_COM_CANAL" :key="p.value" :value="p.value">{{ p.nome }}</option>
              </select>
              <select v-else-if="novoFato.vale === 'marca'" v-model="novoFato.marca" class="h-9 min-w-0 flex-1 rounded-md border bg-background px-2 text-sm" aria-label="qual marca">
                <option value="" disabled>escolha a marca</option>
                <option v-for="m in MARCAS_FATO" :key="m.value" :value="m.value">{{ m.nome }}</option>
              </select>
            </div>
          </div>
        </div>
        <div class="flex flex-wrap items-center justify-end gap-1.5">
          <span class="mr-auto text-[11px] text-muted-foreground">
            Nasce aprovado por você (fonte: o dono) — o agente já pode afirmá-lo.
            <span class="tabular-nums" :title="`de ${MIN_TEXTO_NOVO_FATO} a ${MAX_TEXTO_NOVO_FATO} caracteres`">{{ novoFato.texto.trim().length }}/{{ MAX_TEXTO_NOVO_FATO }}</span>
          </span>
          <Button size="sm" variant="ghost" @click="fecharNovoFato()"><X class="mr-1 size-4" /> cancelar</Button>
          <Button size="sm" :disabled="criandoFato || !novoFatoPronto" @click="criarFato()">
            <Loader2 v-if="criandoFato" class="mr-1.5 size-4 animate-spin" /><BadgeCheck v-else class="mr-1.5 size-4" /> salvar
          </Button>
        </div>
      </div>
    </div>
    <div class="flex flex-wrap gap-1 border-b px-3 py-1.5" role="tablist" aria-label="estado dos fatos">
      <button
        v-for="e in [...ESTADOS_FATO, { value: 'todos', label: 'Todos', hint: 'candidatos, ativos e reprovados', cls: '' }]"
        :key="e.value"
        type="button"
        role="tab"
        :aria-selected="filtroFatos.estado === e.value"
        class="rounded-full px-2.5 py-0.5 text-xs"
        :class="filtroFatos.estado === e.value ? 'bg-primary text-primary-foreground' : 'hover:bg-muted'"
        :title="e.hint"
        @click="filtroFatos.estado = e.value as EstadoFato | 'todos'"
      >
        {{ e.label }}<span class="ml-1 tabular-nums opacity-80">{{ e.value === 'todos' ? totalFatos : contagemFatos[e.value] ?? 0 }}</span>
      </button>
    </div>

    <div v-if="erroFatos" class="m-3 rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-600 dark:text-red-400">
      {{ erroFatos }} <button type="button" class="ml-2 text-xs underline" @click="carregarFatos()">tentar de novo</button>
    </div>
    <div v-if="carregandoFatos && !fatos.length" class="px-3 py-6 text-center text-sm text-muted-foreground">
      <Loader2 class="mr-1.5 inline size-4 animate-spin" />carregando…
    </div>
    <div v-else-if="!fatos.length && !erroFatos" class="px-3 py-6 text-center text-sm text-muted-foreground">
      {{ filtroFatos.estado === 'candidato' ? 'Nenhum candidato esperando aprovação.' : 'Nenhum fato com esse filtro.' }}
    </div>
    <ol v-else class="divide-y">
      <li v-for="f in fatos" :key="f.id" class="px-3 py-2.5" :class="f.estado === 'reprovado' ? 'opacity-60' : ''">
        <div class="flex flex-wrap items-start gap-x-3 gap-y-1.5">
          <div class="min-w-0 grow basis-72 space-y-1">
            <template v-if="editandoFato === f.id">
              <textarea
                v-model="textoFato"
                rows="3"
                :maxlength="MAX_TEXTO_FATO"
                class="w-full rounded-md border bg-background px-2 py-1.5 text-sm"
                :aria-label="`texto do fato ${f.id}`"
              />
              <div class="flex items-center gap-1">
                <Button size="sm" class="h-7 text-xs" :disabled="decidindo === f.id || !textoFato.trim()" @click="decidirFato(f, 'aprovar', textoFato)">
                  <Loader2 v-if="decidindo === f.id" class="mr-1 size-3.5 animate-spin" /><BadgeCheck v-else class="mr-1 size-3.5" /> aprovar com este texto
                </Button>
                <Button size="sm" variant="ghost" class="h-7 text-xs" @click="editandoFato = null">cancelar</Button>
                <span class="ml-auto text-[10px] tabular-nums text-muted-foreground">{{ textoFato.length }}/{{ MAX_TEXTO_FATO }}</span>
              </div>
            </template>
            <div v-else class="whitespace-pre-line text-sm">{{ f.texto }}</div>
            <div class="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-muted-foreground">
              <span>fonte: {{ FONTES_FATO[f.fonte] || f.fonte }}</span>
              <span v-if="f.assunto">· assunto: {{ categoriaLabel(f.assunto) }}</span>
              <span v-if="decisaoDe(f)">· {{ decisaoDe(f) }}</span>
              <a
                v-if="f.conversa_id"
                :href="`/atendimento?conversa=${encodeURIComponent(f.conversa_id)}`"
                target="_blank"
                rel="noopener"
                class="inline-flex items-center gap-0.5 underline"
                title="abrir a conversa de onde a correção saiu (outra aba)"
              >· abrir a conversa <ExternalLink class="size-3" /></a>
            </div>
          </div>
          <div class="ml-auto flex max-w-full flex-col items-end gap-1">
            <div class="flex flex-wrap items-center justify-end gap-1">
              <span class="rounded-full bg-muted px-2 py-0.5 text-[11px]" :title="`escopo: ${ESCOPOS_FATO[f.escopo] || f.escopo}`">{{ alcanceDoFato(f) }}</span>
              <span class="rounded-full px-2 py-0.5 text-[11px]" :class="estadoFatoInfo(f.estado).cls">{{ estadoFatoInfo(f.estado).label.replace(/s$/, '').toLowerCase() }}</span>
              <span v-if="f.vencido" class="rounded-full bg-gray-500/15 px-2 py-0.5 text-[11px] text-muted-foreground" :title="`valia até ${fmtDataHora(f.valido_ate)}`">vencido</span>
            </div>
            <div v-if="podeDecidir && editandoFato !== f.id" class="flex items-center gap-0.5">
              <template v-if="!f.ativo">
                <Button size="sm" variant="ghost" class="h-7 px-2 text-xs text-emerald-700 dark:text-emerald-300" :disabled="decidindo !== null" @click="decidirFato(f, 'aprovar')">aprovar</Button>
                <Button size="sm" variant="ghost" class="h-7 px-2" title="corrigir o texto e aprovar" aria-label="corrigir o texto e aprovar" :disabled="decidindo !== null" @click="editarFato(f)"><Pencil class="size-3.5" /></Button>
              </template>
              <Button
                v-if="f.estado !== 'reprovado'"
                size="sm"
                variant="ghost"
                class="h-7 px-2 text-xs text-red-600"
                :disabled="decidindo !== null"
                :title="f.ativo ? 'o agente deixa de poder afirmar' : 'não entra'"
                @click="decidirFato(f, 'reprovar')"
              ><X class="mr-0.5 size-3.5" />{{ f.ativo ? 'desligar' : 'reprovar' }}</Button>
              <Loader2 v-if="decidindo === f.id" class="size-3.5 animate-spin text-muted-foreground" />
            </div>
            <span v-if="f.created_at" class="text-[10px] text-muted-foreground">{{ fmtDataHora(f.created_at) }}</span>
          </div>
        </div>
      </li>
    </ol>
  </section>
</template>
