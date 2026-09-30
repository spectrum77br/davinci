<script setup lang="ts">
// Aba "Manual da IA" do Atendimento (25/09/2026): as regras QUANDO → FAÇA que
// a IA lê antes de sugerir cada resposta — as da plataforma/caixa da conversa
// e as gerais (sem plataforma). Mesmo formato do manual da IA de Chamado
// (ChamadosIa.vue): escrito como se fala com uma pessoa. Regra: a IA NUNCA
// escreve as próprias regras — o manual só muda pela mão de alguém daqui;
// "desativar" guarda a regra sem valer, "apagar" é para sempre.
//
// Parte 2 (P7, 28/09/2026) — manual sem regra batendo com regra:
// - a lista vem AGRUPADA na ordem em que a IA lê: Segurança (vale para toda
//   mensagem, vem primeiro), Por assunto (só quando a mensagem é daquele
//   assunto; separadas por assunto, "geral" primeiro) e Estilo (tom e
//   assinatura, por último); dentro do grupo, pela prioridade (menor antes);
// - duas regras de assunto ATIVAS para o mesmo assunto/plataforma/caixa se
//   contradizem: as que já existem vêm em `conflitos` do GET /regras e ficam
//   em vermelho, dizendo com qual regra batem; a API recusa criar ou ativar
//   a segunda (409 `regra_conflitante`) e o formulário mostra qual é a outra;
// - o formulário ganhou tipo, assunto (a lista oficial do GET /categorias) e
//   prioridade (AtendimentoManualForm).
// A tela NÃO calcula conflito por conta própria: quem decide o que é
// conflito é o backend (a mesma regra que recusa o 409).
import { BookOpen, Loader2, Palette, Pencil, Plus, RotateCcw, ShieldAlert, Tag, Trash2, TriangleAlert } from 'lucide-vue-next'
import {
  CATEGORIAS,
  CATEGORIAS_SO_HUMANO,
  ERROS,
  PLATAFORMAS_COM_CANAL,
  PRIORIDADE_PADRAO,
  TIPOS_REGRA,
  canalLabel,
  categoriaLabel,
  categoriasDe,
  comoLista,
  comoObjeto,
  erroDaApi,
  fmtDataHora,
  plataformaInfo,
  prioridadeDe,
  registrarCategorias,
  tipoRegraDe,
  type Categoria,
  type Regra,
  type TipoRegra,
} from '~/components/AtendimentoPlataforma.vue'
import type { ConflitoForm, FormRegra } from '~/components/AtendimentoManualForm.vue'

const props = defineProps<{ canEdit: boolean; canDelete: boolean }>()
const { api } = useApi()
const toasts = useToasts()

const regras = ref<Regra[]>([])
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
    regras.value = comoLista<Regra>(r, 'regras')
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
const infoCategoria = computed(() => new Map(categorias.value.map((c, i) => [c.id, { ...c, pos: c.ordem ?? i }])))

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
    const extra = r as Regra & { conflita_com?: unknown; em_conflito?: unknown }
    const outras = [...idsDe(r.conflitos), ...idsDe(extra.conflita_com)]
    if (outras.length || r.conflitos === true || extra.em_conflito === true) ligar([r.id, ...outras])
  }
  return mapa
})
function temConflito(id: string) {
  return conflitos.value.has(id)
}
const emConflito = computed(() => regras.value.filter((r) => temConflito(r.id)).length)

// ─── agrupar na ordem em que a IA lê ────────────────────────────────────────
const ICONE_TIPO: Record<TipoRegra, any> = { seguranca: ShieldAlert, categoria: Tag, estilo: Palette }
type Grupo = { chave: string; titulo: string; hint: string; soHumano: boolean; regras: Regra[] }
type Secao = { tipo: TipoRegra; label: string; hint: string; grupos: Grupo[]; total: number }

// Prioridade menor antes; empate fica na ordem em que foram criadas (a API
// manda por data de criação e o sort é estável).
function ordenar(l: Regra[]): Regra[] {
  return l.slice().sort((a, b) => prioridadeDe(a) - prioridadeDe(b))
}
function agrupar(lista: Regra[]): Secao[] {
  return TIPOS_REGRA.map((t) => {
    const doTipo = lista.filter((r) => tipoRegraDe(r) === t.value)
    let grupos: Grupo[] = []
    if (t.value === 'categoria') {
      const porAssunto = new Map<string, Regra[]>()
      for (const r of doTipo) {
        const k = r.categoria || ''
        porAssunto.set(k, [...(porAssunto.get(k) || []), r])
      }
      grupos = [...porAssunto.entries()]
        .sort(([a], [b]) => {
          // "geral" primeiro; depois a ordem do manual; assunto fora da lista no fim
          if (!a) return -1
          if (!b) return 1
          const pa = infoCategoria.value.get(a)?.pos ?? Number.MAX_SAFE_INTEGER
          const pb = infoCategoria.value.get(b)?.pos ?? Number.MAX_SAFE_INTEGER
          return pa - pb || categoriaLabel(a).localeCompare(categoriaLabel(b), 'pt-BR')
        })
        .map(([k, l]) => {
          const info = k ? infoCategoria.value.get(k) : undefined
          return {
            chave: `categoria:${k}`,
            titulo: k ? (info?.nome || categoriaLabel(k)) : 'Geral — qualquer assunto',
            hint: k ? (info?.descricao || '') : 'vale para toda mensagem, seja qual for o assunto',
            soHumano: !!info?.so_humano,
            regras: ordenar(l),
          }
        })
    } else if (doTipo.length) {
      grupos = [{ chave: t.value, titulo: '', hint: '', soHumano: false, regras: ordenar(doTipo) }]
    }
    return { tipo: t.value, label: t.label, hint: t.hint, grupos, total: doTipo.length }
  })
}

// O nº de cada regra sai da lista INTEIRA (sem filtro): "bate com a #7" tem
// de continuar certo com o filtro de plataforma ligado.
const numeros = computed(() => {
  const m = new Map<string, number>()
  let n = 0
  for (const s of agrupar(regras.value)) for (const g of s.grupos) for (const r of g.regras) m.set(r.id, ++n)
  return m
})

const filtroPlataforma = ref('')
const soConflitos = ref(false)
const visiveis = computed(() =>
  regras.value.filter((r) =>
    (!filtroPlataforma.value || !r.plataforma || r.plataforma === filtroPlataforma.value)
    && (!soConflitos.value || temConflito(r.id))),
)
const secoes = computed(() => agrupar(visiveis.value))
const ativas = computed(() => regras.value.filter((r) => r.ativa).length)
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

// ─── achar uma regra na lista (vindo do conflito) ───────────────────────────
const destacada = ref<string | null>(null)
let tiraDestaque: ReturnType<typeof setTimeout> | null = null
onBeforeUnmount(() => { if (tiraDestaque) clearTimeout(tiraDestaque) })
function verRegra(id: string) {
  // O filtro pode estar escondendo a outra regra: some com ele.
  if (!visiveis.value.some((r) => r.id === id)) {
    filtroPlataforma.value = ''
    soConflitos.value = false
  }
  void nextTick(() => {
    const el = document.getElementById(`regra-${id}`)
    if (!el) return
    let suave = true
    try {
      suave = !window.matchMedia('(prefers-reduced-motion: reduce)').matches
    } catch {
      // sem matchMedia — rola suave
    }
    el.scrollIntoView({ block: 'center', behavior: suave ? 'smooth' : 'auto' })
    destacada.value = id
    if (tiraDestaque) clearTimeout(tiraDestaque)
    tiraDestaque = setTimeout(() => { destacada.value = null }, 2500)
  })
}

// ─── formulário (nova e edição) ─────────────────────────────────────────────
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

function corpo(f: FormRegra) {
  return {
    quando: f.quando.trim(),
    faca: f.faca.trim(),
    plataforma: f.plataforma || null,
    canal: (f.plataforma && f.canal) || null,
    tipo: f.tipo,
    // Segurança e estilo valem para qualquer assunto.
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
// e assunto também (mudar o tipo apaga o assunto).
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

function trocar(r: Regra) {
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
  if (!nova.value.quando.trim() || !nova.value.faca.trim()) return
  salvando.value = true
  try {
    const r = comoObjeto<Regra>(await api<unknown>('/api/atendimento/regras', { method: 'POST', body: corpo(nova.value) }), 'regra')
    if (r) regras.value = [...regras.value, r]
    fecharNova()
    toasts.success('Regra salva', 'A IA passa a seguir na próxima sugestão.')
    await carregar(true)
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
    tipo: tipoRegraDe(r),
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
    const x = comoObjeto<Regra>(await api<unknown>(`/api/atendimento/regras/${encodeURIComponent(r.id)}`, { method: 'PATCH', body }), 'regra')
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
    const x = comoObjeto<Regra>(await api<unknown>(`/api/atendimento/regras/${encodeURIComponent(r.id)}`, { method: 'PATCH', body: { ativa: !r.ativa } }), 'regra')
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
  <section class="rounded-lg border bg-card">
    <div class="flex flex-wrap items-center gap-2 border-b px-3 py-2">
      <BookOpen class="size-4 text-muted-foreground" />
      <h2 class="text-sm font-semibold">Manual da IA</h2>
      <span class="text-xs text-muted-foreground">{{ ativas }} ativa(s) de {{ regras.length }}</span>
      <div class="ml-auto flex items-center gap-2">
        <select v-model="filtroPlataforma" class="h-8 rounded-md border bg-background px-2 text-xs" aria-label="filtrar por plataforma">
          <option value="">todas as regras</option>
          <option v-for="p in PLATAFORMAS_COM_CANAL" :key="p.value" :value="p.value">valem para {{ p.nome }}</option>
        </select>
        <Button size="sm" variant="outline" class="h-8" :disabled="carregando" title="recarregar o manual" aria-label="recarregar o manual" @click="carregar()">
          <RotateCcw class="size-4" :class="{ 'animate-spin': carregando }" />
        </Button>
        <Button v-if="props.canEdit && !novaAberta" size="sm" class="h-8" @click="novaAberta = true">
          <Plus class="mr-1.5 size-4" /> nova regra
        </Button>
      </div>
    </div>
    <p class="border-b px-3 py-2 text-xs text-muted-foreground">
      A IA lê estas regras antes de cada sugestão, nesta ordem: <b>Segurança</b> (valem para toda mensagem), <b>Por assunto</b> (só quando a mensagem é
      daquele assunto) e <b>Estilo</b> (tom e assinatura). Escreva como falaria com uma pessoa — <b>quando</b> acontecer isso, <b>faça</b> isso. Nada aqui
      libera a IA a prometer prazo, valor, troca ou reembolso: isso continua indo para uma pessoa.
    </p>

    <!-- regras que já batem entre si -->
    <div v-if="emConflito" class="flex flex-wrap items-center gap-2 border-b border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-700 dark:text-red-300" role="alert">
      <TriangleAlert class="size-4 shrink-0" />
      <span class="min-w-0 flex-1">
        <b>{{ emConflito }} regra{{ emConflito > 1 ? 's' : '' }} em conflito</b> — regras de assunto ativas para o mesmo assunto, plataforma e caixa se
        contradizem, e a IA não sabe qual seguir. Desative uma ou mude o assunto, a plataforma ou a caixa dela.
      </span>
      <button type="button" class="shrink-0 underline" :aria-pressed="soConflitos" @click="soConflitos = !soConflitos">
        {{ soConflitos ? 'mostrar todas' : 'mostrar só essas' }}
      </button>
    </div>

    <div v-if="erro" class="m-3 rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-600 dark:text-red-400">
      {{ erro }} <button type="button" class="ml-2 text-xs underline" @click="carregar()">tentar de novo</button>
    </div>

    <div v-if="novaAberta" class="border-b bg-muted/30 px-3 py-3">
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

    <div v-if="carregando && !regras.length" class="px-3 py-6 text-center text-sm text-muted-foreground">
      <Loader2 class="mr-1.5 inline size-4 animate-spin" />carregando…
    </div>
    <div v-else-if="!regras.length && !novaAberta && !erro" class="px-3 py-8 text-center text-sm text-muted-foreground">
      Nenhuma regra ainda. Comece pela pergunta que mais aparece (rastreio, nota fiscal, prazo de envio).
    </div>
    <div v-else-if="!visiveis.length && regras.length" class="px-3 py-8 text-center text-sm text-muted-foreground">
      Nenhuma regra vale para essa plataforma.
    </div>

    <!-- por tipo (na ordem em que a IA lê) e, no "Por assunto", por assunto -->
    <template v-if="visiveis.length">
      <section v-for="s in secoes" :key="s.tipo" class="border-b last:border-b-0">
        <header class="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 bg-muted/40 px-3 py-1.5">
          <component :is="ICONE_TIPO[s.tipo]" class="size-3.5 shrink-0 self-center text-muted-foreground" aria-hidden="true" />
          <h3 class="text-xs font-semibold uppercase tracking-wide">{{ s.label }}</h3>
          <span class="text-[11px] text-muted-foreground">{{ s.hint }}</span>
          <span class="ml-auto text-[11px] tabular-nums text-muted-foreground">{{ s.total }}</span>
        </header>
        <p v-if="!s.grupos.length" class="px-3 py-2 text-xs text-muted-foreground">
          {{ filtroPlataforma || soConflitos ? 'Nenhuma com esse filtro.' : 'Nenhuma regra deste tipo.' }}
        </p>
        <div v-for="g in s.grupos" :key="g.chave">
          <div v-if="g.titulo" class="flex flex-wrap items-center gap-1.5 border-t px-3 pb-0.5 pt-2" :title="g.hint">
            <span class="text-xs font-semibold">{{ g.titulo }}</span>
            <span v-if="g.soHumano" class="rounded bg-amber-500/20 px-1.5 text-[10px] text-amber-800 dark:text-amber-300" title="assunto que nunca sai sozinho — a IA só sugere, uma pessoa responde">só pessoa</span>
            <span v-if="g.hint" class="min-w-0 truncate text-[11px] text-muted-foreground">{{ g.hint }}</span>
          </div>
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
              <!-- Nº + texto de um lado, selos e botões do outro. Os dois só
                   dividem a linha quando sobram 18rem para o nº + texto; em
                   espaço curto (celular, tablet com o menu aberto) os selos
                   descem para baixo do texto. O `flex-wrap` mede o espaço do
                   CARD, não a largura da tela (o menu lateral e o padding
                   comem boa parte dela) — breakpoint `sm:` não resolveria. -->
              <div v-else class="flex flex-wrap items-start gap-x-3 gap-y-1.5">
                <div class="flex min-w-0 grow basis-72 items-start gap-3">
                  <span class="mt-0.5 w-6 shrink-0 text-right text-xs font-semibold tabular-nums" :class="temConflito(r.id) ? 'text-red-600 dark:text-red-400' : 'text-muted-foreground'">#{{ numeros.get(r.id) }}</span>
                  <div class="min-w-0 flex-1 space-y-1">
                    <div class="grid gap-1 lg:grid-cols-2 lg:gap-4">
                      <div class="whitespace-pre-line text-sm"><span class="text-[11px] font-semibold uppercase text-muted-foreground">Quando </span>{{ r.quando }}</div>
                      <div class="whitespace-pre-line text-sm"><span class="text-[11px] font-semibold uppercase text-muted-foreground">Faça </span>{{ r.faca }}</div>
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
                      <span class="text-red-700/80 dark:text-red-300/80">— a IA não sabe qual seguir; desative uma ou mude o assunto/loja/caixa.</span>
                    </div>
                  </div>
                </div>
                <!-- sem `shrink-0`: sozinha na linha, a coluna encolhe e os selos quebram de linha -->
                <div class="ml-auto flex max-w-full flex-col items-end gap-1">
                  <div class="flex flex-wrap items-center justify-end gap-1">
                    <span class="rounded-full bg-muted px-2 py-0.5 text-[11px]">{{ alcance(r) }}</span>
                    <span class="rounded-full border px-2 py-0.5 text-[11px] tabular-nums text-muted-foreground" title="menor número = lida antes dentro do grupo">prioridade {{ prioridadeDe(r) }}</span>
                    <span v-if="!r.ativa" class="rounded-full bg-gray-500/15 px-2 py-0.5 text-[11px] text-muted-foreground">desativada</span>
                  </div>
                  <div v-if="props.canEdit" class="flex items-center gap-0.5">
                    <Button size="sm" variant="ghost" class="h-7 px-2 text-xs" :disabled="salvando" @click="alternar(r)">{{ r.ativa ? 'desativar' : 'ativar' }}</Button>
                    <Button size="sm" variant="ghost" class="h-7 px-2" title="editar" aria-label="editar a regra" @click="editar(r)"><Pencil class="size-3.5" /></Button>
                    <Button v-if="props.canDelete" size="sm" variant="ghost" class="h-7 px-2 text-red-600" title="apagar" aria-label="apagar a regra" :disabled="salvando" @click="apagar(r)"><Trash2 class="size-3.5" /></Button>
                  </div>
                  <span v-if="r.updated_at" class="text-[10px] text-muted-foreground">{{ fmtDataHora(r.updated_at) }}</span>
                </div>
              </div>
            </li>
          </ol>
        </div>
      </section>
    </template>
  </section>
</template>
