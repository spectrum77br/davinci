<script setup lang="ts">
// Marketing › Legendas (Eduardo, 16/09/2026): a biblioteca de variações que o
// robô de postagem consome. A cascata é
//
//   legenda da postagem → legenda do criativo → modelo do PRODUTO
//                       → modelo da MARCA → recusa
//
// e ela só tem onde cair se ALGUÉM cadastrar as variações. Sem nenhuma linha
// aqui a cascata não acha modelo, o modal de publicar abre "sem legenda" e o
// robô automático recusa agendar (`sem_legenda`) — Reel mudo é criativo
// queimado: a legenda é o único texto que a busca do Instagram lê do vídeo.
// Esta tela é o único lugar onde essas variações nascem.
//
// Cadastro DA MARCA, não do criativo: a variação não tem equipe (é como os
// padrões de e-mail) e a permissão é a da tela dos criativos —
// `marketing_criativos`, view pra ler e edit pra escrever
// (app/routers/marketing_legendas.py).
//
// A PRÉVIA é o coração da tela, e não é enfeite: o texto é Jinja, e o erro
// que mais dói não é de sintaxe, é de concordância — "uso do {{ produto }}"
// renderiza "uso do Cafeteira". Renderizada com os dados REAIS da marca
// (POST /preview, que não grava nada), quem escreve vê o estrago antes de
// salvar. Depois não dá: post não se edita.
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { TABS_CADASTROS } from '~/lib/navGroups'
import { apiErrMsg, MARCAS_ERROS } from '~/lib/apiError'
import {
  AlertCircle, AlertTriangle, Eye, Loader2, Pencil, Plus, RefreshCw, Trash2, X,
} from 'lucide-vue-next'

definePageMeta({
  middleware: ['permission'],
  permission: { resource: 'marketing_criativos', action: 'view' },
})

// ---------- tipos (espelham app/schemas/marketing_legendas.py)

type LegendaModelo = {
  id: string
  marca_id: string
  marca_nome: string
  // NULL = padrão da marca (o degrau que segura o criativo sem produto
  // vinculado); preenchido = legenda daquele produto.
  product_id: string | null
  product_nome: string | null
  product_sku: string | null
  texto: string
  ativo: boolean
  created_at: string
  updated_at: string
}

// Só o que esta tela usa de MarcaOut: o fone/e-mail do SAC entram porque são
// a fonte de `{{ whatsapp }}` e `{{ email_sac }}` — é com eles que a tela
// avisa que o placeholder vai sair vazio.
type Marca = {
  id: string
  nome: string
  slug: string
  ativo: boolean
  sac_fone: string | null
  sac_email: string | null
}

type Produto = { id: string; sku: string; name: string }
type Preview = { texto: string; tamanho: number }
type Form = { product_id: string; texto: string; ativo: boolean }
type Modo = 'create' | 'edit'

// ---------- helpers puros (testados em tests/legendas-sfc.cjs)

// Teto da Meta (services/marketing/legenda.py::LEGENDA_MAX).
const LEGENDA_MAX = 2200

// Debounce da prévia: cada tecla é uma ida ao servidor se não segurar, e a
// prévia renderiza Jinja de verdade do outro lado.
const PREVIEW_DEBOUNCE_MS = 500

// Allowlist de services/marketing/legenda.py::PLACEHOLDERS MENOS `{{ site }}`:
// ele existe na allowlist, mas fica fora dos textos por decisão do Eduardo
// (16/09/2026) — nenhuma das duas marcas manda pra site. Oferecer aqui seria
// convidar a escrever um link que ninguém quer.
const PLACEHOLDERS: { nome: string; vem: string }[] = [
  { nome: 'marca', vem: 'nome da marca' },
  { nome: 'produto', vem: 'nome do produto vinculado — vazio quando não há' },
  { nome: 'whatsapp', vem: 'WhatsApp do SAC da marca' },
  { nome: 'email_sac', vem: 'e-mail do SAC da marca' },
  { nome: 'instagram', vem: '@ da conta que vai receber o post' },
]

// `{{ nome }}` montado em JS: escrito direto no template, o Vue interpolaria
// as chaves e a tela mostraria o valor, não o placeholder.
function chaves(nome: string): string {
  return `{{ ${nome} }}`
}

// Os placeholders que ESTE texto usa (com ou sem `-` de whitespace control e
// com ou sem filtro depois do nome).
function placeholdersUsados(texto: string): string[] {
  const achados: string[] = []
  const re = /\{\{-?\s*([a-z_][a-z0-9_]*)/gi
  let m = re.exec(texto || '')
  while (m) {
    const nome = m[1].toLowerCase()
    if (!achados.includes(nome)) achados.push(nome)
    m = re.exec(texto || '')
  }
  return achados
}

// Placeholder que o backend aceita mas que, PARA ESTA MARCA, renderiza vazio.
// A prévia mostra o buraco; este aviso diz de onde ele vem, senão o operador
// olha um espaço em branco e não sabe o que consertar (nem onde).
function avisosDeVazio(o: {
  texto: string
  marca: { sac_fone?: string | null; sac_email?: string | null } | null
  temProduto: boolean
}): string[] {
  const usados = placeholdersUsados(o.texto)
  const avisos: string[] = []
  if (usados.includes('produto') && !o.temProduto) {
    avisos.push(
      `${chaves('produto')} sai vazio: esta variação é o padrão da marca, e padrão da marca não tem produto.`,
    )
  }
  if (usados.includes('whatsapp') && o.marca && !(o.marca.sac_fone || '').trim()) {
    avisos.push(`${chaves('whatsapp')} sai vazio: a marca não tem fone do SAC (Cadastros › Redes Sociais).`)
  }
  if (usados.includes('email_sac') && o.marca && !(o.marca.sac_email || '').trim()) {
    avisos.push(`${chaves('email_sac')} sai vazio: a marca não tem e-mail do SAC (Cadastros › Redes Sociais).`)
  }
  return avisos
}

// Códigos do backend em português. O template inválido chega por DOIS
// caminhos e os dois têm que virar a mesma frase: o 422 do router
// (`detail: {code, erro}`, quando o render falha) e o 422 do Pydantic
// (`detail: [{loc, msg}]`, quando o schema barra no salvamento). Mostrar
// "artigo_colado_no_produto" cru pro operador é mostrar nada.
const LEGENDA_ERROS: Record<string, string> = {
  texto_required: 'Escreva o texto da variação.',
  legenda_muito_longa: `Passou de ${LEGENDA_MAX} caracteres — o Instagram corta.`,
  artigo_colado_no_produto:
    `Tire o artigo antes do ${chaves('produto')}: o nome do produto muda de gênero sozinho e sai "do Cafeteira".`,
  placeholder_desconhecido:
    'Esse placeholder não existe. Use só marca, produto, whatsapp, email_sac e instagram.',
  filtro_nao_permitido: 'Esse filtro não é permitido na legenda.',
  template_bloco_nao_permitido: `Use só ${chaves('placeholders')} — blocos {% %} não são permitidos.`,
  template_invalido: 'Template inválido — confira as chaves.',
  marca_not_found: 'Marca não encontrada — recarregue a tela.',
  product_not_found: 'Produto não encontrado — recarregue a lista.',
  legenda_modelo_not_found: 'Essa variação não existe mais — recarregue a tela.',
  forbidden: 'Sem permissão: a biblioteca de legendas usa a permissão de Marketing › Criativos.',
}

// {code, detalhe} de qualquer um dos dois formatos. O detalhe é a parte útil
// (a palavra colada, o nome do placeholder errado) e vem depois do ":".
function extraiErro(e: any): { code: string; detalhe: string } {
  const det = e?.data?.detail
  let bruto = ''
  if (Array.isArray(det)) {
    const item = det.find((x: any) => String(x?.msg || '').trim())
    bruto = String(item?.msg ?? '').replace(/^Value error,\s*/i, '')
  } else if (det && typeof det === 'object') {
    // `erro` é a mensagem inteira do Jinja; `code` é só o prefixo dela.
    bruto = String(det.erro || det.code || '')
  } else if (typeof det === 'string') {
    bruto = det
  }
  const i = bruto.indexOf(':')
  return {
    code: (i >= 0 ? bruto.slice(0, i) : bruto).trim(),
    detalhe: i >= 0 ? bruto.slice(i + 1).trim() : '',
  }
}

function legendaErrMsg(e: any): string {
  const { code, detalhe } = extraiErro(e)
  const base = LEGENDA_ERROS[code]
  if (base) return detalhe ? `${base} (${detalhe})` : base
  // Endpoint ausente = API mais velha que a tela. Vale pro 404 sem código
  // NOSSO — inclusive o `detail: "Not Found"` seco do FastAPI, que chegaria
  // na tela como "Not Found" e não diz nada pra quem está olhando. Os 404
  // de verdade (`legenda_modelo_not_found` e companhia) saem no `base` acima.
  const status = Number(e?.status ?? e?.statusCode ?? e?.response?.status ?? 0)
  if (status === 404) return 'A API ainda não tem a biblioteca de legendas (404).'
  return apiErrMsg(e, MARCAS_ERROS)
}

// Corpo do POST/PATCH. `marca_id` só no POST: a coluna é NOT NULL e o
// LegendaModeloPatch nem aceita o campo — quem errou a marca apaga e cria
// (schemas/marketing_legendas.py). `product_id` vai SEMPRE, inclusive null
// explícito: null é o comando "esta variação passa a ser o padrão da marca".
function montaBody(f: Form, modo: Modo, marcaId: string): Record<string, unknown> {
  const body: Record<string, unknown> = {
    product_id: f.product_id || null,
    texto: f.texto.trim(),
    ativo: f.ativo,
  }
  if (modo === 'create') body.marca_id = marcaId
  return body
}

type GrupoProduto = { key: string; label: string; itens: LegendaModelo[] }

// "Cafeteira Elétrica (dg017)" — sem isso o operador escolheria entre UUIDs.
function produtoLabel(m: { product_nome?: string | null; product_sku?: string | null }): string {
  const nome = (m.product_nome || '').trim()
  const sku = (m.product_sku || '').trim()
  if (nome && sku) return `${nome} (${sku})`
  if (nome) return nome
  if (sku) return sku
  // Produto apagado depois (FK SET NULL não chega aqui: a variação continua
  // com o id, só o join não acha o nome).
  return 'produto sem cadastro'
}

// Padrão da marca em cima, depois um bloco por produto — é a cascata lida de
// trás pra frente, e é assim que o operador confere "o que sai quando o
// criativo não tem produto vinculado".
function separaVariacoes(lista: LegendaModelo[]): { padrao: LegendaModelo[]; produtos: GrupoProduto[] } {
  const padrao: LegendaModelo[] = []
  const grupos: GrupoProduto[] = []
  for (const m of lista) {
    if (!m.product_id) {
      padrao.push(m)
      continue
    }
    const atual = grupos.find((g) => g.key === m.product_id)
    if (atual) atual.itens.push(m)
    else grupos.push({ key: m.product_id, label: produtoLabel(m), itens: [m] })
  }
  grupos.sort((a, b) => a.label.localeCompare(b.label, 'pt-BR'))
  return { padrao, produtos: grupos }
}

// Exemplo do campo vazio. Vive aqui, e não no atributo `placeholder` do
// template, porque `{{ }}` dentro de atributo é erro de compilação no Vue 3
// ("Interpolation inside attributes has been removed").
const EXEMPLO_TEXTO = `Chegou novidade ☕\n\nChama no WhatsApp ${chaves('whatsapp')} ou no ${chaves('instagram')}`

// Cartão da lista: só o começo do texto, numa linha.
function resumoTexto(texto: string, max = 220): string {
  const s = (texto || '').replace(/\s+/g, ' ').trim()
  return s.length > max ? `${s.slice(0, max)}…` : s
}

// Texto da caixa da prévia quando não há prévia nenhuma. Com erro na tela,
// "escreva o texto" seria mentira: o texto está escrito — o que faltou foi a
// resposta do servidor.
function previaVazioTexto(o: { loading: boolean; erro: string }): string {
  if (o.loading) return 'Renderizando…'
  if (o.erro) return 'Prévia indisponível — conserte o erro acima e tente de novo.'
  return 'Escreva o texto para ver a prévia com os dados reais da marca.'
}

function contador(tamanho: number): string {
  return `${tamanho}/${LEGENDA_MAX}`
}

// Âmbar quando o texto renderizado bateu no teto: o `renderizar` corta seco
// em 2200, então "2200/2200" quase sempre quer dizer "foi cortado".
function contadorClass(tamanho: number): string {
  return tamanho >= LEGENDA_MAX ? 'text-amber-600' : 'text-muted-foreground'
}

// Inserção do placeholder no ponto do cursor (o clique no chip).
function insereEm(texto: string, ini: number, fim: number, trecho: string): string {
  const base = texto || ''
  const a = Math.max(0, Math.min(ini, base.length))
  const b = Math.max(a, Math.min(fim, base.length))
  return base.slice(0, a) + trecho + base.slice(b)
}

// Fallback do seletor de marca: quem tem `marketing_criativos` mas não tem
// `marcas:view` leva 403 em /api/marcas. As variações já cadastradas trazem
// marca_id + marca_nome, então dá pra continuar trabalhando nas marcas que já
// têm legenda — só não dá pra começar uma marca nova.
function marcasDasVariacoes(lista: LegendaModelo[]): Marca[] {
  const out: Marca[] = []
  for (const m of lista) {
    if (!m.marca_id || out.some((x) => x.id === m.marca_id)) continue
    out.push({ id: m.marca_id, nome: m.marca_nome || m.marca_id, slug: '', ativo: true, sac_fone: null, sac_email: null })
  }
  out.sort((a, b) => a.nome.localeCompare(b.nome, 'pt-BR'))
  return out
}

// ---------- fim helpers puros

const { api } = useApi()
const toasts = useToasts()
const canEdit = useCan('marketing_criativos', 'edit')

const marcas = ref<Marca[]>([])
const marcaSel = ref('')
// /api/marcas é de outra permissão (`marcas:view`): quando ele recusa, a tela
// não pode morrer — cai no fallback e explica o que ficou de fora.
const marcasDegradado = ref(false)
const lista = ref<LegendaModelo[]>([])
const loading = ref(false)
const loaded = ref(false)
const erro = ref('')

const marcaAtual = computed<Marca | null>(() => marcas.value.find((m) => m.id === marcaSel.value) ?? null)
const separadas = computed(() => separaVariacoes(lista.value))
const totalAtivas = computed(() => lista.value.filter((m) => m.ativo).length)

async function carregarMarcas() {
  try {
    marcas.value = await api<Marca[]>('/api/marcas?ativo=true')
    marcasDegradado.value = false
  } catch {
    // Sem /api/marcas a tela ainda serve: as marcas saem das próprias
    // variações (uma chamada só, sem filtro).
    marcasDegradado.value = true
    try {
      marcas.value = marcasDasVariacoes(await api<LegendaModelo[]>('/api/marketing/legendas'))
    } catch (e: any) {
      marcas.value = []
      erro.value = legendaErrMsg(e)
    }
  }
  if (!marcaSel.value && marcas.value.length) marcaSel.value = marcas.value[0].id
}

async function carregarLista() {
  if (!marcaSel.value) {
    lista.value = []
    loaded.value = true
    return
  }
  loading.value = true
  erro.value = ''
  try {
    lista.value = await api<LegendaModelo[]>(
      `/api/marketing/legendas?marca_id=${encodeURIComponent(marcaSel.value)}`,
    )
    loaded.value = true
  } catch (e: any) {
    lista.value = []
    erro.value = legendaErrMsg(e)
  } finally {
    loading.value = false
  }
}

async function recarregar() {
  await carregarMarcas()
  await carregarLista()
}

onMounted(() => { void recarregar() })
watch(marcaSel, () => { void carregarLista() })

// ============================================================ produtos
// Só pra ESCOLHER o produto de uma variação nova. `produtos:view` é outra
// permissão: sem ela dá pra cadastrar o padrão da marca (que é o degrau que
// segura tudo) e editar as variações de produto que já existem.

const produtos = ref<Produto[]>([])
const produtoBusca = ref('')
const produtosLoading = ref(false)
const produtosBloqueado = ref(false)
let produtoSeq = 0

async function buscarProdutos() {
  const seq = ++produtoSeq
  produtosLoading.value = true
  try {
    const q = new URLSearchParams({ page_size: '30' })
    const termo = produtoBusca.value.trim()
    if (termo) q.set('search', termo)
    const r = await api<{ items: Produto[] }>(`/api/products?${q.toString()}`)
    if (seq !== produtoSeq) return
    produtos.value = Array.isArray(r?.items) ? r.items : []
    produtosBloqueado.value = false
  } catch {
    if (seq !== produtoSeq) return
    produtos.value = []
    produtosBloqueado.value = true
  } finally {
    if (seq === produtoSeq) produtosLoading.value = false
  }
}

// ============================================================ modal
// Criar e editar usam o mesmo formulário; `editando = null` é criação.

const modalAberto = ref(false)
const editando = ref<LegendaModelo | null>(null)
const form = ref<Form>({ product_id: '', texto: '', ativo: true })
const saving = ref(false)
const modalErr = ref('')
const textoRef = ref<HTMLTextAreaElement | null>(null)

const modo = computed<Modo>(() => (editando.value ? 'edit' : 'create'))

// Opções do select de produto. A variação que está aberta entra mesmo que a
// busca não a traga (ou que /api/products recuse): senão editar o texto
// mudaria o produto por acidente.
const produtoOptions = computed<{ id: string; label: string }[]>(() => {
  const opts = produtos.value.map((p) => ({ id: p.id, label: `${p.name} (${p.sku})` }))
  const atual = editando.value
  if (atual?.product_id && !opts.some((o) => o.id === atual.product_id)) {
    opts.unshift({ id: atual.product_id, label: produtoLabel(atual) })
  }
  return opts
})

const avisos = computed(() => avisosDeVazio({
  texto: form.value.texto,
  marca: marcaAtual.value,
  temProduto: !!form.value.product_id,
}))

function abrirCriar(productId: string | null = null) {
  if (!canEdit.value || !marcaSel.value) return
  editando.value = null
  form.value = { product_id: productId || '', texto: '', ativo: true }
  abrirModal()
}

function abrirEditar(m: LegendaModelo) {
  editando.value = m
  form.value = { product_id: m.product_id || '', texto: m.texto, ativo: m.ativo }
  abrirModal()
}

function abrirModal() {
  modalErr.value = ''
  previa.value = null
  previaErro.value = ''
  previaSnapshot.value = ''
  modalAberto.value = true
  if (!produtos.value.length && !produtosBloqueado.value) void buscarProdutos()
  void atualizaPrevia()
}

function fecharModal() {
  if (saving.value) return
  modalAberto.value = false
  editando.value = null
  cancelaPrevia()
  previa.value = null
  previaErro.value = ''
  previaLoading.value = false
}

function inserePlaceholder(nome: string) {
  const trecho = chaves(nome)
  const el = textoRef.value
  if (!el) {
    form.value.texto = `${form.value.texto}${trecho}`
  } else {
    const ini = el.selectionStart ?? el.value.length
    const fim = el.selectionEnd ?? ini
    form.value.texto = insereEm(form.value.texto, ini, fim, trecho)
    void nextTick(() => {
      el.focus()
      const pos = Math.min(ini, form.value.texto.length) + trecho.length
      el.setSelectionRange?.(pos, pos)
    })
  }
  agendaPrevia()
}

// ============================================================ prévia
// Renderiza com os dados REAIS da marca e não grava nada (POST /preview).
// Cada consulta leva um número: digitar depressa dispara duas idas ao
// servidor e a que volta por último não é necessariamente a mais nova —
// escrever a resposta velha por cima mostraria a prévia de um texto que não
// está mais na caixa.

const previa = ref<Preview | null>(null)
const previaLoading = ref(false)
const previaErro = ref('')
const previaSnapshot = ref('')
let previaSeq = 0
let previaTimer: ReturnType<typeof setTimeout> | null = null

function snapshotForm(): string {
  return JSON.stringify([marcaSel.value, form.value.product_id, form.value.texto])
}

// A prévia na tela é de um texto que já mudou desde então.
const previaStale = computed(() => !!previa.value && previaSnapshot.value !== snapshotForm())

function cancelaPrevia() {
  if (previaTimer !== null) {
    clearTimeout(previaTimer)
    previaTimer = null
  }
}

function agendaPrevia() {
  cancelaPrevia()
  previaTimer = setTimeout(() => { void atualizaPrevia() }, PREVIEW_DEBOUNCE_MS)
}

async function atualizaPrevia() {
  cancelaPrevia()
  const seq = ++previaSeq
  const marcaId = marcaSel.value
  const texto = form.value.texto
  const snapshot = snapshotForm()
  // Texto vazio não vai pro servidor: o schema recusaria com texto_required e
  // o operador veria um erro vermelho por ainda não ter digitado nada.
  if (!marcaId || !texto.trim()) {
    previa.value = null
    previaErro.value = ''
    previaLoading.value = false
    return
  }
  previaLoading.value = true
  previaErro.value = ''
  try {
    const r = await api<Preview>('/api/marketing/legendas/preview', {
      method: 'POST',
      body: { texto, marca_id: marcaId, product_id: form.value.product_id || null },
    })
    if (seq !== previaSeq) return
    const renderizado = typeof r?.texto === 'string' ? r.texto : ''
    previa.value = {
      texto: renderizado,
      // O tamanho é do backend porque o teto vale pro texto RENDERIZADO;
      // se vier lixo, contar aqui é melhor que mostrar NaN.
      tamanho: Number.isFinite(r?.tamanho) ? Math.trunc(r.tamanho) : renderizado.length,
    }
    previaSnapshot.value = snapshot
  } catch (e: any) {
    if (seq !== previaSeq) return
    previa.value = null
    previaErro.value = legendaErrMsg(e)
  } finally {
    if (seq === previaSeq) previaLoading.value = false
  }
}

// ============================================================ gravar / apagar

async function salvar() {
  if (!canEdit.value || saving.value) return
  if (!marcaSel.value) {
    modalErr.value = 'Escolha a marca.'
    return
  }
  if (!form.value.texto.trim()) {
    modalErr.value = LEGENDA_ERROS.texto_required
    return
  }
  saving.value = true
  modalErr.value = ''
  const alvo = editando.value
  try {
    const body = montaBody(form.value, modo.value, marcaSel.value)
    const salvo = alvo
      ? await api<LegendaModelo>(`/api/marketing/legendas/${alvo.id}`, { method: 'PATCH', body })
      : await api<LegendaModelo>('/api/marketing/legendas', { method: 'POST', body })
    const i = lista.value.findIndex((x) => x.id === salvo.id)
    if (i >= 0) lista.value[i] = salvo
    else lista.value = [...lista.value, salvo]
    toasts.success(alvo ? 'Variação atualizada' : 'Variação cadastrada')
    saving.value = false
    fecharModal()
  } catch (e: any) {
    modalErr.value = legendaErrMsg(e)
    saving.value = false
  }
}

async function remover(m: LegendaModelo) {
  if (!canEdit.value) return
  const onde = m.product_id ? produtoLabel(m) : 'padrão da marca'
  if (!window.confirm(
    `Apagar esta variação (${onde})?\n\nOs posts que já saíram não mudam — a legenda publicada fica gravada na postagem. `
    + 'Pra parar de usar sem perder o texto, desmarque "ativa".',
  )) return
  try {
    await api(`/api/marketing/legendas/${m.id}`, { method: 'DELETE' })
    lista.value = lista.value.filter((x) => x.id !== m.id)
    toasts.success('Variação apagada')
  } catch (e: any) {
    toasts.error('Não deu pra apagar', legendaErrMsg(e))
  }
}
</script>

<template>
  <div class="space-y-4">
    <RouteTabs :tabs="TABS_CADASTROS" />
    <PageHeader
      title="Legendas"
      description="Biblioteca de legendas do robô de postagem: um padrão por marca e, quando vale a pena, variações por produto."
    >
      <template #actions>
        <select
          v-model="marcaSel"
          class="h-9 rounded-md border bg-background px-2 text-sm"
          aria-label="Marca"
          :disabled="!marcas.length"
        >
          <option v-if="!marcas.length" value="">sem marcas</option>
          <option v-for="m in marcas" :key="m.id" :value="m.id">{{ m.nome }}</option>
        </select>
        <Button size="sm" variant="ghost" :disabled="loading" @click="recarregar">
          <RefreshCw class="size-4 mr-1" :class="{ 'animate-spin': loading }" /> recarregar
        </Button>
        <Button v-if="canEdit" size="sm" :disabled="!marcaSel" @click="abrirCriar(null)">
          <Plus class="size-4 mr-1" /> Nova variação
        </Button>
      </template>
    </PageHeader>

    <p v-if="erro" role="alert" class="text-sm text-destructive flex items-center gap-2">
      <AlertCircle class="size-4 shrink-0" /> {{ erro }}
    </p>
    <p v-if="marcasDegradado" class="text-xs text-amber-600 flex items-center gap-2">
      <AlertTriangle class="size-4 shrink-0" />
      Sem permissão em Cadastros › Marcas: aparecem só as marcas que já têm alguma legenda.
    </p>

    <p v-if="loaded && marcaSel" class="text-xs text-muted-foreground">
      {{ lista.length }} variação(ões) cadastrada(s) · {{ totalAtivas }} ativa(s).
      O robô sorteia entre as ativas a que saiu há mais tempo naquela conta, pra o mesmo texto não repetir.
    </p>

    <div v-if="loading && !lista.length" class="rounded-lg border p-8 text-center text-sm text-muted-foreground">
      Carregando legendas…
    </div>

    <!-- Vazio: é o estado que a feature inteira existe pra sair. Sem uma
         variação sequer o robô recusa agendar (sem_legenda). -->
    <EmptyState
      v-else-if="loaded && marcaSel && !lista.length && !erro"
      :icon="AlertTriangle"
      title="Nenhuma legenda cadastrada para esta marca"
      description="Enquanto não existir pelo menos o padrão da marca, a cascata não acha modelo: o robô recusa agendar (sem_legenda) e todo criativo abre “sem legenda” no modal de publicar. Reel sem legenda é criativo queimado — é o único texto que a busca do Instagram lê do vídeo."
    >
      <Button v-if="canEdit" @click="abrirCriar(null)">
        <Plus class="size-4 mr-1" /> Criar o padrão da marca
      </Button>
      <p v-else class="text-xs text-muted-foreground">Você não tem permissão pra cadastrar (marketing_criativos: edit).</p>
    </EmptyState>

    <!-- `!erro` junto: com a lista vazia POR FALHA, o bloco abaixo diria
         "Sem padrão da marca" e mandaria cadastrar o que talvez já exista. -->
    <template v-else-if="marcaSel && !erro">
      <!-- Padrão da marca primeiro: é a cascata lida de trás pra frente. -->
      <section class="rounded-lg border">
        <header class="flex flex-wrap items-center gap-2 border-b bg-muted/40 px-3 py-2">
          <h2 class="text-sm font-medium">Padrão da marca</h2>
          <span class="text-xs text-muted-foreground">
            vale pra todo criativo desta marca que não tem produto vinculado
          </span>
          <Button v-if="canEdit" size="sm" variant="outline" class="ml-auto" @click="abrirCriar(null)">
            <Plus class="size-4 mr-1" /> variação
          </Button>
        </header>
        <p v-if="!separadas.padrao.length" class="px-3 py-4 text-sm text-amber-600">
          Sem padrão da marca. Criativo sem produto vinculado fica sem legenda e o robô recusa agendar.
        </p>
        <ul v-else class="divide-y">
          <li v-for="m in separadas.padrao" :key="m.id" class="flex items-start gap-3 px-3 py-3">
            <div class="min-w-0 flex-1">
              <p class="text-sm whitespace-pre-wrap break-words" :class="{ 'opacity-50': !m.ativo }">{{ resumoTexto(m.texto) }}</p>
              <p class="mt-1 text-xs text-muted-foreground">
                {{ contador(m.texto.length) }} no editor
                <span v-if="!m.ativo" class="ml-2 rounded bg-muted px-1.5 py-0.5">fora do rodízio</span>
              </p>
            </div>
            <div class="flex shrink-0 items-center gap-1">
              <Button size="sm" variant="ghost" :aria-label="canEdit ? 'Editar variação' : 'Ver variação'" @click="abrirEditar(m)">
                <Pencil v-if="canEdit" class="size-4" /><Eye v-else class="size-4" />
              </Button>
              <Button v-if="canEdit" size="sm" variant="ghost" aria-label="Apagar variação" @click="remover(m)">
                <Trash2 class="size-4 text-destructive" />
              </Button>
            </div>
          </li>
        </ul>
      </section>

      <section v-if="separadas.produtos.length" class="space-y-3">
        <h2 class="text-sm font-medium">Por produto</h2>
        <div v-for="g in separadas.produtos" :key="g.key" class="rounded-lg border">
          <header class="flex flex-wrap items-center gap-2 border-b bg-muted/40 px-3 py-2">
            <h3 class="text-sm font-medium">{{ g.label }}</h3>
            <span class="text-xs text-muted-foreground">ganha do padrão da marca quando o criativo é deste produto</span>
            <Button v-if="canEdit" size="sm" variant="outline" class="ml-auto" @click="abrirCriar(g.key)">
              <Plus class="size-4 mr-1" /> variação
            </Button>
          </header>
          <ul class="divide-y">
            <li v-for="m in g.itens" :key="m.id" class="flex items-start gap-3 px-3 py-3">
              <div class="min-w-0 flex-1">
                <p class="text-sm whitespace-pre-wrap break-words" :class="{ 'opacity-50': !m.ativo }">{{ resumoTexto(m.texto) }}</p>
                <p class="mt-1 text-xs text-muted-foreground">
                  {{ contador(m.texto.length) }} no editor
                  <span v-if="!m.ativo" class="ml-2 rounded bg-muted px-1.5 py-0.5">fora do rodízio</span>
                </p>
              </div>
              <div class="flex shrink-0 items-center gap-1">
                <Button size="sm" variant="ghost" :aria-label="canEdit ? 'Editar variação' : 'Ver variação'" @click="abrirEditar(m)">
                  <Pencil v-if="canEdit" class="size-4" /><Eye v-else class="size-4" />
                </Button>
                <Button v-if="canEdit" size="sm" variant="ghost" aria-label="Apagar variação" @click="remover(m)">
                  <Trash2 class="size-4 text-destructive" />
                </Button>
              </div>
            </li>
          </ul>
        </div>
      </section>
    </template>

    <p v-else-if="loaded && !marcas.length && !erro" class="text-sm text-muted-foreground">
      Nenhuma marca disponível. Cadastre em
      <NuxtLink to="/marcas" class="underline">Cadastros › Marcas</NuxtLink>.
    </p>

    <!-- ===================================================== modal -->
    <div
      v-if="modalAberto"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      @click.self="fecharModal"
      @keydown.esc="fecharModal"
    >
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="legenda-title"
        class="w-full max-w-5xl max-h-[90vh] overflow-auto rounded-lg border bg-background p-5 space-y-4"
      >
        <div class="flex items-start justify-between gap-3">
          <div>
            <h2 id="legenda-title" class="text-lg font-semibold">
              {{ modo === 'edit' ? 'Editar variação' : 'Nova variação' }} · {{ marcaAtual?.nome || '—' }}
            </h2>
            <p class="text-sm text-muted-foreground">
              Editar aqui não reescreve post nenhum: a legenda que foi pro ar fica em snapshot na postagem. Vale da próxima vez.
            </p>
          </div>
          <Button variant="ghost" size="sm" :disabled="saving" aria-label="Fechar" @click="fecharModal">
            <X class="size-4" />
          </Button>
        </div>

        <div class="grid gap-5 lg:grid-cols-2">
          <div class="space-y-3">
            <div>
              <label for="legenda-produto" class="text-sm font-medium">Vale para</label>
              <select
                id="legenda-produto"
                v-model="form.product_id"
                class="mt-1 h-9 w-full rounded-md border bg-background px-2 text-sm"
                :disabled="!canEdit || saving"
                @change="agendaPrevia"
              >
                <option value="">Padrão da marca (todo criativo sem produto)</option>
                <option v-for="o in produtoOptions" :key="o.id" :value="o.id">{{ o.label }}</option>
              </select>
              <div v-if="canEdit && !produtosBloqueado" class="mt-2 flex gap-2">
                <Input
                  v-model="produtoBusca"
                  placeholder="buscar produto por nome ou SKU…"
                  class="h-8 text-sm"
                  @keydown.enter.prevent="buscarProdutos"
                />
                <Button size="sm" variant="outline" :disabled="produtosLoading" @click="buscarProdutos">
                  <Loader2 v-if="produtosLoading" class="size-4 animate-spin" /><span v-else>buscar</span>
                </Button>
              </div>
              <p v-if="produtosBloqueado" class="mt-1 text-xs text-amber-600">
                Sem permissão em Produtos: dá pra cadastrar o padrão da marca e editar as variações de produto que já existem.
              </p>
            </div>

            <div>
              <div class="flex items-end justify-between gap-2">
                <label for="legenda-texto" class="text-sm font-medium">Texto da variação</label>
                <span class="text-xs" :class="contadorClass(form.texto.length)">{{ contador(form.texto.length) }} no editor</span>
              </div>
              <textarea
                id="legenda-texto"
                ref="textoRef"
                v-model="form.texto"
                :disabled="!canEdit || saving"
                rows="12"
                class="mt-1 w-full rounded-md border bg-background p-3 text-sm font-mono"
                :placeholder="EXEMPLO_TEXTO"
                @input="agendaPrevia"
              />
              <label class="mt-2 flex items-center gap-2 text-sm">
                <input v-model="form.ativo" type="checkbox" :disabled="!canEdit || saving" />
                Ativa — entra no rodízio do robô
              </label>
            </div>

            <div class="rounded-md border bg-muted/20 p-3 space-y-2">
              <p class="text-xs font-medium">Placeholders (clique para inserir)</p>
              <ul class="space-y-1">
                <li v-for="p in PLACEHOLDERS" :key="p.nome" class="flex flex-wrap items-baseline gap-2 text-xs">
                  <button
                    type="button"
                    class="rounded border bg-background px-1.5 py-0.5 font-mono hover:bg-accent disabled:opacity-50"
                    :disabled="!canEdit || saving"
                    @click="inserePlaceholder(p.nome)"
                  >{{ chaves(p.nome) }}</button>
                  <span class="text-muted-foreground">{{ p.vem }}</span>
                </li>
              </ul>
              <p class="text-xs text-amber-600">
                Nunca cole artigo antes do placeholder: "do {{ chaves('produto') }}" renderiza "do Cafeteira".
                O nome do produto muda de gênero sozinho — use como rótulo ("{{ chaves('produto') }} — …"), nunca dentro de concordância.
              </p>
            </div>
          </div>

          <div class="space-y-2">
            <div class="flex flex-wrap items-center justify-between gap-2">
              <h3 class="text-sm font-medium">Prévia — é isto que vai pro Instagram</h3>
              <Button size="sm" variant="outline" :disabled="previaLoading || !form.texto.trim()" @click="atualizaPrevia">
                <Loader2 v-if="previaLoading" class="size-4 mr-1 animate-spin" /><RefreshCw v-else class="size-4 mr-1" />
                Atualizar prévia
              </Button>
            </div>
            <p v-if="previaErro" role="alert" class="text-sm text-destructive flex items-start gap-2">
              <AlertCircle class="size-4 shrink-0 mt-0.5" /> {{ previaErro }}
            </p>
            <p v-else-if="previaStale" class="text-xs text-amber-600">O texto mudou — a prévia é do texto anterior.</p>
            <pre
              v-if="previa"
              class="min-h-[240px] max-h-[380px] overflow-auto whitespace-pre-wrap break-words rounded border bg-muted/20 p-3 text-sm"
            >{{ previa.texto || '(a variação renderizou vazia — confira os placeholders)' }}</pre>
            <div v-else class="flex min-h-[240px] items-center justify-center rounded border p-3 text-center text-sm text-muted-foreground">
              {{ previaVazioTexto({ loading: previaLoading, erro: previaErro }) }}
            </div>
            <p v-if="previa" class="text-xs" :class="contadorClass(previa.tamanho)">
              {{ contador(previa.tamanho) }} renderizado
              <span v-if="previa.tamanho >= LEGENDA_MAX"> — o texto foi cortado no teto do Instagram.</span>
            </p>
            <ul v-if="avisos.length" class="space-y-1 text-xs text-amber-600">
              <li v-for="a in avisos" :key="a">{{ a }}</li>
            </ul>
          </div>
        </div>

        <p v-if="modalErr" role="alert" class="text-sm text-destructive">{{ modalErr }}</p>
        <div class="flex items-center justify-end gap-3 border-t pt-4">
          <Button variant="ghost" :disabled="saving" @click="fecharModal">{{ canEdit ? 'Cancelar' : 'Fechar' }}</Button>
          <Button v-if="canEdit" :disabled="saving || !form.texto.trim()" @click="salvar">
            <Loader2 v-if="saving" class="size-4 mr-1 animate-spin" /> Salvar variação
          </Button>
        </div>
      </section>
    </div>
  </div>
</template>
