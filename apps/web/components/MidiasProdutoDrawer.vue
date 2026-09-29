<script setup lang="ts">
// Painel lateral de mídias de UM produto (Tabela de Preços › Produtos).
//
// Pedido do Eduardo (29/09/2026): a coluna Fotos estava "muito simples e
// desorganizada" — câmera, lápis e upload de 10px escondidos no hover, um
// contador "0 0" sem legenda e um campo que gravava o link ao sair dele (e
// apagava o link se ficasse vazio). Tudo que se faz com as mídias do produto
// mora aqui agora: ver as fotos de verdade (miniaturas), enviar arrastando,
// baixar, trocar ou desligar a pasta — e as EMBALAGENS (caixa, arte em PDF…),
// que ficam na subpasta "Embalagens" DENTRO da pasta de fotos no MEGA e nunca
// aparecem para as agências.
import {
  X, Loader2, Upload, ExternalLink, Copy, FolderOpen, FolderSync, FolderX,
  Image as ImageIcon, Film, Package, FileText, Download, ChevronLeft,
  ChevronRight, RefreshCw, AlertCircle, Search,
} from 'lucide-vue-next'

type Aba = 'fotos' | 'videos' | 'embalagens'

// Só o que o painel lê do produto — a linha inteira da tabela (PricingProduct
// da página) encaixa aqui sem conversão.
type ProdutoMidias = {
  id: string
  sku: string
  name: string
  department: string
  fotos_url: string | null
  fotos_path?: string | null
  embalagens_url?: string | null
  embalagens_path?: string | null
  fotos_count: number | null
  videos_count: number | null
  embalagens_count?: number | null
  midias_contadas_em?: string | null
}

// Campos que a API devolve depois de enviar/recontar/trocar pasta. A página
// aplica na linha aberta (e nas irmãs da mesma pasta) sem recarregar tudo.
type CamposMidias = {
  fotos_url?: string | null
  fotos_path?: string | null
  embalagens_url?: string | null
  embalagens_path?: string | null
  fotos_count?: number | null
  videos_count?: number | null
  embalagens_count?: number | null
  midias_contadas_em?: string | null
}
const CHAVES_MIDIAS = [
  'fotos_url', 'fotos_path', 'embalagens_url', 'embalagens_path',
  'fotos_count', 'videos_count', 'embalagens_count', 'midias_contadas_em',
] as const

type Arquivo = { nome: string; ext: string; imagem: boolean }
type Lista = { pasta: string | null; url: string | null; arquivos: Arquivo[] }
type Pasta = { path: string; name: string }

const props = defineProps<{
  produto: ProdutoMidias
  abaInicial: Aba
  podeEditar: boolean
  // Outras linhas que apontam pra MESMA pasta (sem o próprio produto) —
  // o que for enviado aqui aparece nelas também, e o painel avisa.
  irmaos: { id: string; sku: string; name: string }[]
  // Aplica na tabela o que a API devolveu (envio, recontagem, troca de
  // pasta). É prop-função, e não emit, de propósito: fechar o painel no meio
  // de um envio desmonta o componente, e o Vue descarta emit de instância
  // desmontada — o lote que terminava depois (inclusive o 1º, que cria a
  // pasta) nunca chegava na linha, que ficava "+ Adicionar" até o F5. A
  // prop-função continua valendo depois do unmount. `id` é o produto do
  // INÍCIO da operação; `irmas` diz o que copiar para as linhas da mesma
  // pasta (omitido = o mesmo que a linha; false = não mexer nelas).
  aplicar: (id: string, campos: CamposMidias, irmas?: CamposMidias | false) => void
}>()

const emit = defineEmits<{
  // A célula da tabela mostra "Enviando…" enquanto isto for true.
  (e: 'enviando', ativo: boolean): void
  // A API disse "not logged in": a página reconfere o MEGA para a pílula do
  // topo virar "MEGA desconectado — Conectar", que é o que a mensagem manda
  // clicar (ela é carregada uma vez só e podia continuar "conectado").
  (e: 'mega-desconectado'): void
  (e: 'fechar'): void
}>()

const { api } = useApi()
const toast = useToasts()

// Onde a API cria a pasta de um produto que ainda não tem (espelho de
// RAIZ_POR_DEPARTAMENTO em services/mega_midias.py) — só pra dizer ONDE o 1º
// envio vai cair. Eletro fica em /uranyx, onde as linhas de eletro já estão.
const PASTA_POR_DEPT: Record<string, string> = {
  celular: 'Celular',
  mala: 'Malas',
  eletro: 'uranyx',
}

// Extensões espelhadas do sidecar (infra/megacmd: _IMG_EXT/_VID_EXT) + as de
// arte aceitas nas embalagens. A API é quem manda (400 tipo_nao_aceito); a
// checagem aqui só evita subir 4 lotes e descobrir no 5º que tinha um PDF.
const EXT_IMAGEM = new Set(['jpg', 'jpeg', 'png', 'webp', 'gif', 'heic', 'heif', 'bmp', 'tif', 'tiff', 'avif', 'jfif'])
const EXT_VIDEO = new Set(['mp4', 'mov', 'm4v', 'avi', 'mkv', 'webm', '3gp', 'mpg', 'mpeg', 'wmv', 'flv'])
const EXT_ARTE = new Set(['pdf', 'ai', 'psd', 'eps', 'cdr', 'svg', 'zip', 'af', 'afdesign', 'afphoto', 'afpub'])
// Arte que ganha PRÉVIA (JPEG gerado no servidor: 1ª página do PDF/AI, a
// prévia gravada no .af do Affinity, a imagem do PSD). Eduardo 29/09: a caixa
// aparecia só como ícone "PDF". Espelha EXT_COM_PREVIA da API.
const EXT_COM_PREVIA = new Set(['pdf', 'ai', 'psd', 'af', 'afdesign', 'afphoto', 'afpub'])

// 5 arquivos por requisição: vídeo de celular passa fácil de 100 MB, e um
// lote pequeno deixa o progresso andar e não perde tudo se um lote falhar.
const LOTE = 5
const POR_PAGINA = 24

const ABAS: { key: Aba; label: string; icon: any }[] = [
  { key: 'fotos', label: 'Fotos', icon: ImageIcon },
  { key: 'videos', label: 'Vídeos', icon: Film },
  { key: 'embalagens', label: 'Embalagens', icon: Package },
]

const aba = ref<Aba>(props.abaInicial)

// ------------------------------------------------------------ cabeçalho
const skus = computed(() =>
  (props.produto.sku || '').split(',').map((s) => s.trim()).filter(Boolean),
)

// Nome da pasta que o 1º envio cria (mesma regra da API: nome do produto,
// sem "/" que viraria subpasta).
const pastaNova = computed(() => {
  const nome = (props.produto.name || props.produto.sku || '').trim().replace(/\//g, '-')
  const dept = PASTA_POR_DEPT[props.produto.department]
  return dept ? `/${dept}/${nome}` : `/${nome}`
})

const temPasta = computed(() => !!props.produto.fotos_path || !!props.produto.fotos_url)

// Na aba Embalagens o "Abrir no MEGA" vai direto pra subpasta, se ela já
// tiver link; senão abre a pasta do produto (a subpasta aparece lá dentro).
const linkAtual = computed(() =>
  aba.value === 'embalagens' && props.produto.embalagens_url
    ? props.produto.embalagens_url
    : props.produto.fotos_url,
)

const destinoEnvio = computed(() => {
  if (aba.value === 'embalagens')
    return props.produto.embalagens_path || `${props.produto.fotos_path || pastaNova.value}/Embalagens`
  return props.produto.fotos_path || pastaNova.value
})

function numeroAba(a: Aba): string {
  const v = a === 'fotos'
    ? props.produto.fotos_count
    : a === 'videos'
      ? props.produto.videos_count
      : props.produto.embalagens_count
  return typeof v === 'number' ? String(v) : '–'
}

// "29/09 14:02" no fuso de São Paulo — o servidor grava em UTC.
const _FMT_CONTADO = new Intl.DateTimeFormat('pt-BR', {
  timeZone: 'America/Sao_Paulo',
  day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
})
const contadoEm = computed(() => {
  const iso = props.produto.midias_contadas_em
  if (!iso) return null
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return null
  const p = Object.fromEntries(_FMT_CONTADO.formatToParts(d).map((x) => [x.type, x.value]))
  return `${p.day}/${p.month} ${p.hour}:${p.minute}`
})

function irmaoTexto(i: { sku: string; name: string }): string {
  const sku = (i.sku || '').split(',')[0].trim()
  return i.name ? `${sku} (${i.name})` : sku
}

async function copiarLink() {
  const url = linkAtual.value
  if (!url) return
  try {
    await navigator.clipboard.writeText(url)
    toast.success('Link copiado', [url])
  } catch {
    // Navegador sem permissão de área de transferência: mostra pra copiar à mão.
    window.prompt('Copie o link:', url)
  }
}

// ------------------------------------------------------------ erros da API
function statusDe(e: any): number | undefined {
  return e?.statusCode ?? e?.status ?? e?.response?.status
}

function mensagemErro(e: any): string {
  const d = e?.data?.detail
  const code = typeof d === 'object' ? d?.code : undefined
  const st = statusDe(e)
  // Antes TODO erro do MEGA virava "desconectado — use 'Conectar MEGA'", um
  // botão que não existe, e a mensagem da API ia fora. O caso mais comum
  // nem é conexão: pasta renomeada ou apagada direto no MEGA ("o MEGA não
  // abriu a pasta X"), que se resolve em "Trocar pasta…".
  if (code === 'mega_sidecar' || st === 502 || st === 503) {
    const msg = code === 'mega_sidecar' && d?.message ? String(d.message) : ''
    if (/not logged/i.test(msg)) {
      emit('mega-desconectado')
      return 'Conta MEGA não conectada — clique em "MEGA desconectado — Conectar" no topo da página'
    }
    // 503 = o serviço do MEGA não respondeu; sem mensagem, nem dá pra dizer mais.
    if (st === 503 || !msg) return 'MEGA fora do ar — tente de novo em instantes'
    if (msg.includes('não abriu a pasta')) {
      const saida = props.podeEditar
        ? "use 'Trocar pasta…'"
        : 'peça a quem edita a tabela para trocar a pasta'
      return `MEGA: ${msg} — a pasta pode ter sido renomeada ou apagada no MEGA; ${saida}`
    }
    return `MEGA: ${msg}`
  }
  if (code === 'pasta_invalida')
    return 'Essa pasta não está entre as pastas de produto do MEGA (/Celular, /Malas, /uranyx) — pode ter sido renomeada ou apagada'
  if (code === 'sem_pasta') return 'Este produto ainda não tem pasta no MEGA'
  if (code === 'arquivo_nao_encontrado')
    return "Arquivo não encontrado no MEGA — pode ter sido apagado ou renomeado; use 'Recontar esta pasta'"
  if (code === 'nome_invalido') return 'Nome de arquivo inválido'
  if (code === 'tipo_nao_aceito') return textoRecusados(d?.arquivos ?? [])
  if (code === 'too_many_files') return 'Arquivos demais de uma vez'
  if (code === 'no_files') return 'Nenhum arquivo chegou ao servidor — tente de novo'
  if (code === 'not_found') return 'Produto não encontrado — recarregue a página'
  if (st === 403) return 'Você não tem permissão para esta ação'
  if (st === 413) return 'Arquivo grande demais para passar pelo DaVinci — suba direto pelo MEGA'
  return String(d?.message || code || e?.message || 'erro desconhecido')
}

function textoRecusados(nomes: string[]): string {
  const lista = nomes.slice(0, 8).join(', ') + (nomes.length > 8 ? ` e mais ${nomes.length - 8}` : '')
  const dica = aba.value === 'embalagens'
    ? 'Embalagens aceita fotos, PDF, AI, PSD, EPS, CDR, SVG, Affinity (.af) e ZIP. Vídeo vai na aba Vídeos.'
    : 'Aqui entram fotos e vídeos. PDF, arte e ZIP vão na aba Embalagens.'
  return `Não aceito aqui: ${lista}. ${dica}`
}

// ------------------------------------------------------------ listas por aba
// Carrega só a aba aberta (listar a pasta passa pelo MEGA e demora); guarda o
// resultado até algo mudar a pasta (envio, recontagem, troca).
const listas = reactive<Record<Aba, Lista | null>>({ fotos: null, videos: null, embalagens: null })
const carregando = reactive<Record<Aba, boolean>>({ fotos: false, videos: false, embalagens: false })
const erros = reactive<Record<Aba, string | null>>({ fotos: null, videos: null, embalagens: null })
const limite = ref(POR_PAGINA)
// Miniatura que o navegador não desenha (.heic, .tif) vira cartão de arquivo.
const falhas = reactive(new Set<string>())

async function carregarAba(a: Aba, forcar = false) {
  if (!forcar && (listas[a] || carregando[a])) return
  const produtoId = props.produto.id
  // Sem caminho de pasta não há o que listar (link colado à mão não dá pra
  // abrir por dentro) — nem gasta uma ida ao MEGA.
  if (!props.produto.fotos_path) {
    listas[a] = { pasta: null, url: null, arquivos: [] }
    erros[a] = null
    return
  }
  carregando[a] = true
  erros[a] = null
  try {
    const res = await api<Lista>(
      `/api/pricing/mega/products/${produtoId}/midias?tipo=${a}`,
    )
    // Troca de produto no meio da carga: descarta a resposta velha.
    if (produtoId !== props.produto.id) return
    listas[a] = {
      pasta: res?.pasta ?? null,
      url: res?.url ?? null,
      arquivos: Array.isArray(res?.arquivos) ? res.arquivos : [],
    }
  } catch (e: any) {
    if (produtoId === props.produto.id) erros[a] = mensagemErro(e)
  } finally {
    carregando[a] = false
  }
}

// Operação que termina depois de o painel fechar (envio, troca, recontagem)
// ainda aplica o resultado na tabela, mas não recarrega lista de um painel
// que ninguém está vendo — seria uma ida ao MEGA à toa.
let desmontado = false

function invalidarListas() {
  if (desmontado) return
  listas.fotos = listas.videos = listas.embalagens = null
  falhas.clear()
  void carregarAba(aba.value, true)
}

watch(aba, (a) => {
  limite.value = POR_PAGINA
  visor.value = null
  void carregarAba(a)
})

watch(() => props.produto.id, () => {
  listas.fotos = listas.videos = listas.embalagens = null
  falhas.clear()
  trocando.value = false
  visor.value = null
  erroEnvio.value = null
  aba.value = props.abaInicial
  void carregarAba(aba.value, true)
})

const _COLLATOR = new Intl.Collator('pt-BR', { numeric: true, sensitivity: 'base' })

function normalizar(s: string): string {
  return s.toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '')
}

function nomeCurto(nome: string): string {
  const i = nome.lastIndexOf('/')
  return i >= 0 ? nome.slice(i + 1) : nome
}

// Caminho do arquivo dentro da pasta do produto, sem o "Embalagens/" da aba
// Embalagens (lá tudo mora dentro dela).
function relativo(nome: string): string {
  if (aba.value !== 'embalagens') return nome
  const [primeiro, ...resto] = nome.split('/')
  return resto.length && ['embalagens', 'embalagem'].includes(normalizar(primeiro)) ? resto.join('/') : nome
}

// Grupo = só o PRIMEIRO nível de subpasta ("M1 listrada/b005 M1 listrada
// preto/b005.12.jpg" → "M1 listrada"). Com o caminho inteiro, cada cor de mala
// virava um grupo de UMA foto e a aba das malas era uma coluna comprida (visto
// no teste de tela de 29/09/2026); o nível de baixo vai para a legenda.
function subpasta(nome: string): string {
  const r = relativo(nome)
  const i = r.indexOf('/')
  return i >= 0 ? r.slice(0, i) : ''
}

// Pasta intermediária entre o grupo e o arquivo ("b005 M1 listrada preto"),
// mostrada acima do nome na legenda da miniatura. Vazio quando não há.
function pastaDoMeio(nome: string): string {
  const partes = relativo(nome).split('/')
  return partes.length > 2 ? partes.slice(1, -1).join('/') : ''
}

const arquivosAba = computed<Arquivo[]>(() => {
  const l = listas[aba.value]
  if (!l) return []
  return [...l.arquivos].sort((a, b) =>
    _COLLATOR.compare(subpasta(a.nome), subpasta(b.nome))
    || _COLLATOR.compare(pastaDoMeio(a.nome), pastaDoMeio(b.nome))
    || _COLLATOR.compare(nomeCurto(a.nome), nomeCurto(b.nome)),
  )
})

const grupos = computed(() => {
  const m = new Map<string, Arquivo[]>()
  for (const a of arquivosAba.value.slice(0, limite.value)) {
    const k = subpasta(a.nome)
    if (!m.has(k)) m.set(k, [])
    m.get(k)!.push(a)
  }
  return Array.from(m.entries()).map(([titulo, itens]) => ({ titulo, itens }))
})

const faltamMostrar = computed(() => Math.max(0, arquivosAba.value.length - limite.value))

function extDe(a: Arquivo): string {
  const e = (a.ext || nomeCurto(a.nome).split('.').pop() || '').replace(/^\./, '').toLowerCase()
  return e
}

function ehVideo(a: Arquivo): boolean {
  return EXT_VIDEO.has(extDe(a))
}

// Arquivo de gráfica com prévia: aparece como imagem na grade e abre no
// visor, mas o Baixar entrega o arquivo original. Se a prévia não sair
// (PDF que não abre, .af sem prévia embutida), `falhas` devolve o ícone.
function temPrevia(a: Arquivo): boolean {
  return !a.imagem && EXT_COM_PREVIA.has(extDe(a))
}

function mostraMiniatura(a: Arquivo): boolean {
  return (a.imagem || temPrevia(a)) && !falhas.has(a.nome)
}

// O visor abre a versão de 1600 px (JPEG gerado e guardado no servidor), não
// o original: foto de fornecedor e imagem de IA têm de 1 a 10 MB, e o visor
// ficava segundos desenhando aos pedaços (Eduardo 29/09: "demoram muito para
// renderizar"). O Baixar continua entregando o arquivo original. Arte (PDF,
// .af…) abre sempre pela prévia — o PDF em si nunca é aberto na tela.
function urlVisor(a: Arquivo): string {
  return `${urlArquivo(a.nome, false, true)}&grande=1`
}

// Mesma origem da página: o cookie de sessão vai junto, sem token na URL.
// `miniatura`: a grade pede o JPEG de 320 px que o servidor gera (a foto
// original do fornecedor tem até 1,5 MB); o visor grande e o Baixar pedem o
// arquivo original.
function urlArquivo(nome: string, baixar = false, miniatura = false): string {
  return `/api/pricing/mega/products/${props.produto.id}/midias/arquivo?nome=${encodeURIComponent(nome)}${baixar ? '&baixar=1' : ''}${miniatura ? '&miniatura=1' : ''}`
}

const textoVazio = computed(() => {
  if (aba.value === 'embalagens')
    return props.podeEditar
      ? 'Nenhuma embalagem ainda. Arraste as fotos da caixa, o PDF da arte etc.'
      : 'Nenhuma embalagem ainda.'
  if (!props.produto.fotos_path)
    return props.podeEditar
      ? 'Este produto ainda não tem pasta no MEGA. Arraste os arquivos acima — a pasta é criada no 1º envio.'
      : 'Este produto ainda não tem pasta no MEGA.'
  if (aba.value === 'videos') return 'Nenhum vídeo nesta pasta ainda.'
  return 'Nenhuma foto nesta pasta ainda.'
})

// ------------------------------------------------------------ visor de imagem
const imagensAba = computed(() => arquivosAba.value.filter((a) => mostraMiniatura(a)))
// O visor guarda o NOME do arquivo aberto, não a posição: quando uma
// miniatura falha (prévia que não sai), ela sai da lista e a posição passava a
// apontar para outro arquivo. `visor` segue sendo a posição, calculada.
const visorNome = ref<string | null>(null)
const visor = computed<number | null>({
  get: () => {
    if (visorNome.value == null) return null
    const i = imagensAba.value.findIndex((x) => x.nome === visorNome.value)
    return i >= 0 ? i : null
  },
  set: (i) => {
    visorNome.value = i == null ? null : (imagensAba.value[i]?.nome ?? null)
  },
})
const visorArquivo = computed(() => (visor.value == null ? null : imagensAba.value[visor.value] ?? null))

function abrirVisor(a: Arquivo) {
  const i = imagensAba.value.findIndex((x) => x.nome === a.nome)
  visor.value = i >= 0 ? i : null
}
// Enquanto a imagem grande não chega, o visor mostra "Carregando…" em vez
// de desenhar aos pedaços; e a anterior/próxima já vêm baixando, para o
// Anterior/Próxima trocar na hora.
const visorCarregando = ref(false)
const visorErro = ref(false)
const _preCarregadas = new Set<string>()
function preCarregar(a: Arquivo | undefined) {
  if (!a) return
  const url = urlVisor(a)
  if (_preCarregadas.has(url)) return
  _preCarregadas.add(url)
  const img = new Image()
  img.src = url
}
watch(visorArquivo, (a) => {
  if (!a) return
  visorCarregando.value = true
  visorErro.value = false
  const i = visor.value ?? 0
  preCarregar(imagensAba.value[i + 1])
  preCarregar(imagensAba.value[i - 1])
})

// O <img> de uma foto que já saiu do visor ainda dispara load/error (o
// navegador não cancela o download): sem esta checagem, trocar rápido de foto
// tirava o "Carregando…" da atual ou mostrava o erro da anterior nela.
function visorFim(ev: Event, erro: boolean) {
  if (!(ev.target as HTMLImageElement | null)?.isConnected) return
  visorCarregando.value = false
  visorErro.value = erro
}

function visorAnterior() {
  if (visor.value != null && visor.value > 0) visor.value--
}
function visorProxima() {
  if (visor.value != null && visor.value < imagensAba.value.length - 1) visor.value++
}

// ------------------------------------------------------------ envio
const inputArquivos = ref<HTMLInputElement | null>(null)
const enviando = ref(false)
const progresso = ref<{ ate: number; total: number } | null>(null)
const erroEnvio = ref<string | null>(null)
const arrastes = ref(0) // contador: dragleave dispara ao passar por cima dos filhos
let cancelado = false

const acceptAba = computed(() =>
  aba.value === 'embalagens'
    ? 'image/*,.pdf,.ai,.psd,.eps,.cdr,.svg,.zip,.af,.afdesign,.afphoto,.afpub'
    : aba.value === 'videos' ? 'video/*' : 'image/*',
)

const aceitaTexto = computed(() =>
  aba.value === 'embalagens'
    ? 'Fotos da caixa, PDF, AI, PSD, EPS, CDR, SVG, Affinity (.af) ou ZIP'
    : aba.value === 'videos'
      ? 'Vídeos (MP4, MOV…) — vídeo grande demora para subir'
      : 'Fotos (JPG, PNG, WEBP…)',
)

function aceito(f: File, tipo: 'fotos' | 'embalagens'): boolean {
  if (!f.name.includes('.')) return false
  const ext = (f.name.split('.').pop() || '').toLowerCase()
  if (EXT_IMAGEM.has(ext)) return true
  return tipo === 'fotos' ? EXT_VIDEO.has(ext) : EXT_ARTE.has(ext)
}

function escolherArquivos() {
  inputArquivos.value?.click()
}

function onEscolhidos(ev: Event) {
  const input = ev.target as HTMLInputElement
  const files = Array.from(input.files ?? [])
  input.value = ''
  if (files.length) void enviar(files)
}

function onDrop(ev: DragEvent) {
  arrastes.value = 0
  // Com a pasta sendo trocada/desligada, o envio iria para a pasta que está
  // deixando de ser a do produto (ou religaria a que acabou de ser desligada).
  if (!props.podeEditar || enviando.value || salvandoPasta.value) return
  const dt = ev.dataTransfer
  if (!dt) return
  // Pasta arrastada chega como "arquivo" vazio sem extensão — avisa em vez
  // de mandar pro servidor e voltar um erro confuso.
  const pastas: string[] = []
  for (const it of Array.from(dt.items ?? [])) {
    const entry = (it as any).webkitGetAsEntry?.()
    if (entry?.isDirectory) pastas.push(entry.name)
  }
  const files = Array.from(dt.files ?? []).filter((f) => !pastas.includes(f.name))
  if (pastas.length && !files.length) {
    erroEnvio.value = `Arrastar uma pasta inteira não funciona (${pastas.join(', ')}). Abra a pasta e arraste os arquivos de dentro dela.`
    return
  }
  if (files.length) void enviar(files)
}

async function enviar(files: File[]) {
  if (enviando.value || salvandoPasta.value) return
  // O produto do INÍCIO: é nele que os lotes caem e é nele que o resultado
  // é aplicado, mesmo que o painel feche no meio (ver prop `aplicar`).
  const id = props.produto.id
  const tipo: 'fotos' | 'embalagens' = aba.value === 'embalagens' ? 'embalagens' : 'fotos'
  const aceitos = files.filter((f) => aceito(f, tipo))
  const recusados = files.filter((f) => !aceito(f, tipo)).map((f) => f.name)
  erroEnvio.value = null
  if (!aceitos.length) {
    erroEnvio.value = textoRecusados(recusados)
    return
  }

  // Guardados no início: se a pessoa trocar de aba no meio do envio, o aviso
  // final continua dizendo a pasta certa. A resposta da API, se trouxer o
  // caminho, vale mais que a previsão (1º envio de produto sem pasta).
  let destino = destinoEnvio.value
  const avisoRecusados = recusados.length ? textoRecusados(recusados) : null
  enviando.value = true
  cancelado = false
  emit('enviando', true)
  const total = aceitos.length
  let feitos = 0
  try {
    for (let i = 0; i < total; i += LOTE) {
      if (cancelado) break
      const lote = aceitos.slice(i, i + LOTE)
      progresso.value = { ate: Math.min(i + lote.length, total), total }
      const fd = new FormData()
      for (const f of lote) fd.append('files', f, f.name)
      const res = await api<Record<string, any>>(
        `/api/pricing/mega/products/${id}/${tipo}/upload`,
        { method: 'POST', body: fd },
      )
      feitos += lote.length
      const caminho = tipo === 'embalagens' ? res?.embalagens_path : res?.fotos_path
      if (typeof caminho === 'string' && caminho) destino = caminho
      // A cada lote a linha da tabela já mostra a contagem nova.
      props.aplicar(id, camposDe(res))
    }
    if (cancelado) {
      toast.warning('Envio interrompido', [`${feitos} de ${total} arquivo(s) chegaram ao MEGA`])
    } else {
      const linhas = [`${feitos} arquivo(s) em ${destino}`]
      if (avisoRecusados) {
        linhas.push(avisoRecusados)
        toast.warning('Enviado para o MEGA', linhas)
        erroEnvio.value = avisoRecusados
      } else {
        toast.success('Enviado para o MEGA', linhas)
      }
    }
  } catch (e: any) {
    const msg = mensagemErro(e)
    erroEnvio.value = feitos ? `${msg} (${feitos} de ${total} arquivo(s) já tinham sido enviados)` : msg
    toast.error('Envio para o MEGA', [erroEnvio.value])
  } finally {
    enviando.value = false
    progresso.value = null
    emit('enviando', false)
    if (feitos && !cancelado) invalidarListas()
  }
}

function camposDe(res: Record<string, any> | null | undefined): CamposMidias {
  const out: CamposMidias = {}
  if (!res) return out
  for (const k of CHAVES_MIDIAS) if (k in res) (out as any)[k] = res[k]
  return out
}

// ------------------------------------------------------------ recontar
const recontando = ref(false)

async function recontar() {
  if (recontando.value) return
  const id = props.produto.id
  recontando.value = true
  try {
    const res = await api<Record<string, any>>(
      `/api/pricing/mega/products/${id}/recontar`,
      { method: 'POST' },
    )
    props.aplicar(id, camposDe(res))
    const f = res?.fotos_count, v = res?.videos_count, em = res?.embalagens_count
    toast.success('Pasta recontada', [
      `${f ?? '–'} foto(s), ${v ?? '–'} vídeo(s), ${em ?? '–'} embalagem(ns)`,
    ])
    invalidarListas()
  } catch (e: any) {
    toast.error('Recontar pasta', [mensagemErro(e)])
  } finally {
    recontando.value = false
  }
}

// ------------------------------------------------------------ trocar pasta
// Substitui o antigo campo de link que gravava ao sair dele: agora escolhe
// uma pasta que EXISTE no MEGA (a mesma lista que o "Vincular pastas pelo
// nome" usa) e só grava ao clicar em Salvar.
const trocando = ref(false)
const pastas = ref<Pasta[] | null>(null)
const pastasCarregando = ref(false)
const pastasErro = ref<string | null>(null)
const buscaPasta = ref('')
const pastaEscolhida = ref<string | null>(null)
const salvandoPasta = ref(false)
const LIMITE_PASTAS = 200

async function abrirTroca() {
  trocando.value = true
  pastaEscolhida.value = props.produto.fotos_path ?? null
  buscaPasta.value = ''
  if (pastas.value || pastasCarregando.value) return
  pastasCarregando.value = true
  pastasErro.value = null
  try {
    const res = await api<{ pastas: Pasta[] }>('/api/pricing/mega/pastas')
    pastas.value = Array.isArray(res?.pastas) ? res.pastas : []
  } catch (e: any) {
    pastasErro.value = mensagemErro(e)
  } finally {
    pastasCarregando.value = false
  }
}

const pastasFiltradas = computed(() => {
  // A subpasta "Embalagens" de outro produto nunca é pasta de fotos — se a
  // lista trouxer, fica de fora pra ninguém ligar o produto nela por engano.
  const todas = (pastas.value ?? []).filter((p) => {
    const ultimo = p.path.split('/').filter(Boolean).pop() ?? ''
    return !['embalagens', 'embalagem'].includes(normalizar(ultimo))
  })
  const termos = normalizar(buscaPasta.value.trim()).split(/\s+/).filter(Boolean)
  if (!termos.length) return todas
  return todas.filter((p) => {
    const alvo = normalizar(p.path)
    return termos.every((t) => alvo.includes(t))
  })
})

// Campos que o PUT /pasta grava TAMBÉM nas irmãs da pasta nova: a recontagem
// (`_aplicar`) escreve contagens e carimbo em todas as linhas da pasta, mas
// link e caminho de fotos são só da linha aberta.
const CHAVES_CONTAGEM = ['fotos_count', 'videos_count', 'embalagens_count', 'midias_contadas_em'] as const

function camposIrmasDaTroca(campos: CamposMidias): CamposMidias | false {
  // Recontagem que falhou volta sem números (midias_contadas_em nulo): a
  // linha aberta foi zerada, mas as irmãs continuam com os delas no banco.
  // Copiar os nulos apagava na tela contagem e embalagem que o banco tem.
  if (campos.midias_contadas_em == null) return false
  const out: CamposMidias = {}
  for (const k of CHAVES_CONTAGEM) if (k in campos) (out as any)[k] = campos[k]
  // Embalagem vazia com a pasta contada é ambígua: ou a pasta nova não tem
  // "Embalagens" (as irmãs também ficaram sem), ou o link dela falhou (as
  // irmãs ficaram com o delas). Na dúvida, as irmãs mantêm o que têm.
  if (campos.embalagens_path) {
    out.embalagens_path = campos.embalagens_path
    out.embalagens_url = campos.embalagens_url ?? null
  }
  return out
}

async function salvarPasta() {
  const path = pastaEscolhida.value
  // Durante o envio, trocar a pasta dividia os arquivos entre duas pastas
  // (os lotes seguintes iam para a nova).
  if (!path || salvandoPasta.value || enviando.value) return
  const id = props.produto.id
  salvandoPasta.value = true
  try {
    const res = await api<Record<string, any>>(
      `/api/pricing/mega/products/${id}/pasta`,
      { method: 'PUT', body: { path } },
    )
    const campos = camposDe(res)
    props.aplicar(id, campos, camposIrmasDaTroca(campos))
    trocando.value = false
    toast.success('Pasta do produto trocada', [path])
    invalidarListas()
  } catch (e: any) {
    toast.error('Trocar pasta', [mensagemErro(e)])
  } finally {
    salvandoPasta.value = false
  }
}

async function desligarPasta() {
  // Durante o envio, o lote seguinte caía no upload com a pasta nula, a API
  // criava/religava a pasta e o "Desligar" era desfeito sem aviso.
  if (salvandoPasta.value || enviando.value) return
  const id = props.produto.id
  const ok = confirm(
    `Desligar a pasta de "${props.produto.name}"?\n\n`
    + 'O produto fica sem pasta; nada é apagado no MEGA. '
    + 'As fotos continuam lá e você pode ligar de novo depois.',
  )
  if (!ok) return
  salvandoPasta.value = true
  try {
    const res = await api<Record<string, any>>(
      `/api/pricing/mega/products/${id}/pasta`,
      { method: 'DELETE' },
    )
    props.aplicar(id, camposDe(res))
    trocando.value = false
    toast.success('Pasta desligada', ['Nada foi apagado no MEGA'])
    invalidarListas()
  } catch (e: any) {
    toast.error('Desligar pasta', [mensagemErro(e)])
  } finally {
    salvandoPasta.value = false
  }
}

// ------------------------------------------------------------ fechar
function fechar() {
  if (enviando.value) {
    const ok = confirm(
      'O envio ainda está acontecendo. Fechar mesmo assim?\n\n'
      + 'O lote que está subindo termina; os arquivos que faltam não são enviados.',
    )
    if (!ok) return
    cancelado = true
    emit('enviando', false)
  }
  emit('fechar')
}

function onKeydown(ev: KeyboardEvent) {
  if (visorArquivo.value) {
    if (ev.key === 'Escape') { ev.preventDefault(); visor.value = null }
    else if (ev.key === 'ArrowLeft') { ev.preventDefault(); visorAnterior() }
    else if (ev.key === 'ArrowRight') { ev.preventDefault(); visorProxima() }
    return
  }
  if (ev.key === 'Escape') {
    ev.preventDefault()
    fechar()
  }
}

onMounted(() => {
  window.addEventListener('keydown', onKeydown)
  void carregarAba(aba.value)
})
onBeforeUnmount(() => {
  desmontado = true
  window.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <div
    class="fixed inset-0 z-50 flex justify-end bg-black/40"
    @click.self="fechar"
    @dragover.prevent
    @drop.prevent
  >
    <!--
      dragover/drop cancelados no fundo (acima): arquivo solto FORA da caixa
      tracejada (na grade, no cabeçalho, no fundo escuro) fazia o navegador
      abrir o arquivo na própria aba — o DaVinci sumia e o envio em lotes
      parava sem aviso. O evento da caixa sobe até o fundo; o onDrop dela
      continua sendo o único que envia.
    -->
    <div
      class="relative flex h-full w-full max-w-3xl flex-col border-l bg-background shadow-xl"
      role="dialog"
      aria-modal="true"
      :aria-label="`Mídias de ${produto.name}`"
    >
      <!-- ================================================ cabeçalho -->
      <div class="space-y-2 border-b p-4">
        <div class="flex items-start justify-between gap-3">
          <div class="min-w-0 space-y-1">
            <h3 class="text-base font-semibold leading-tight">{{ produto.name }}</h3>
            <div class="flex flex-wrap gap-1">
              <span
                v-for="s in skus.slice(0, 6)"
                :key="s"
                class="rounded bg-muted px-1.5 py-0.5 font-mono text-[11px]"
              >{{ s }}</span>
              <span
                v-if="skus.length > 6"
                class="rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground"
                :title="skus.slice(6).join(', ')"
              >+{{ skus.length - 6 }}</span>
            </div>
          </div>
          <button class="btn btn-sm btn-ghost shrink-0" title="Fechar (Esc)" @click="fechar">
            <X class="h-4 w-4" /> Fechar
          </button>
        </div>

        <p class="text-xs">
          <template v-if="produto.fotos_path">
            <span class="text-muted-foreground">Pasta no MEGA:</span>
            <span class="font-mono break-all">{{ produto.fotos_path }}</span>
          </template>
          <template v-else-if="produto.fotos_url">
            <span class="text-muted-foreground">Pasta no MEGA:</span>
            link colado à mão (sem caminho conhecido) — o 1º envio cria
            <span class="font-mono break-all">{{ pastaNova }}</span>
          </template>
          <template v-else>
            <span class="text-muted-foreground">Sem pasta ainda — o 1º envio cria</span>
            <span class="font-mono break-all">{{ pastaNova }}</span>
          </template>
        </p>

        <div class="flex flex-wrap gap-2">
          <a
            v-if="linkAtual"
            :href="linkAtual"
            target="_blank"
            rel="noopener"
            class="btn btn-sm"
          >
            <ExternalLink class="h-3.5 w-3.5 mr-1" /> Abrir no MEGA
          </a>
          <button v-if="linkAtual" class="btn btn-sm" @click="copiarLink">
            <Copy class="h-3.5 w-3.5 mr-1" /> Copiar link
          </button>
          <button
            v-if="podeEditar"
            class="btn btn-sm"
            :disabled="enviando"
            @click="trocando ? (trocando = false) : abrirTroca()"
          >
            <FolderSync class="h-3.5 w-3.5 mr-1" />
            {{ temPasta ? 'Trocar pasta…' : 'Escolher pasta existente…' }}
          </button>
        </div>

        <div
          v-if="irmaos.length"
          class="rounded border border-blue-200 bg-blue-50 px-3 py-2 text-xs text-blue-900 dark:border-blue-800 dark:bg-blue-900/20 dark:text-blue-100"
        >
          Esta pasta também é usada por:
          <b>{{ irmaos.slice(0, 4).map(irmaoTexto).join('; ') }}</b>
          <span v-if="irmaos.length > 4" :title="irmaos.slice(4).map(irmaoTexto).join('; ')">
            e mais {{ irmaos.length - 4 }}
          </span>
          — o que você enviar aparece em todos.
        </div>

        <!-- abas -->
        <div class="flex w-fit flex-wrap gap-1 rounded-md bg-muted/40 p-1">
          <button
            v-for="t in ABAS"
            :key="t.key"
            type="button"
            class="inline-flex items-center gap-1.5 rounded px-3 py-1.5 text-sm transition-colors"
            :class="aba === t.key ? 'bg-background shadow-sm font-medium' : 'text-muted-foreground hover:text-foreground'"
            @click="aba = t.key"
          >
            <component :is="t.icon" class="h-4 w-4" />
            {{ t.label }}
            <span class="rounded bg-muted px-1.5 text-xs tabular-nums">{{ numeroAba(t.key) }}</span>
          </button>
        </div>
      </div>

      <!-- ================================================ corpo (rola) -->
      <div class="flex-1 space-y-4 overflow-y-auto p-4">
        <!-- trocar pasta -->
        <div v-if="trocando && podeEditar" class="space-y-2 rounded-lg border p-3">
          <div class="flex items-center gap-2 text-sm font-semibold">
            <FolderOpen class="h-4 w-4" /> Escolher a pasta deste produto
          </div>
          <p class="text-xs text-muted-foreground">
            Escolha a pasta do MEGA onde estão as fotos deste produto. As embalagens
            passam a ser lidas da subpasta "Embalagens" dentro dela. Só muda este
            produto — os outros que usam a pasta atual continuam como estão.
          </p>
          <div class="relative">
            <Search class="absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <input
              v-model="buscaPasta"
              type="text"
              placeholder="buscar pasta (ex.: fossibot s7)"
              class="w-full rounded border bg-background py-1.5 pl-7 pr-2 text-sm"
            />
          </div>
          <div v-if="pastasCarregando" class="py-3 text-center text-xs text-muted-foreground">
            <Loader2 class="inline h-4 w-4 animate-spin" /> Carregando pastas do MEGA…
          </div>
          <p v-else-if="pastasErro" class="text-xs text-destructive">{{ pastasErro }}</p>
          <template v-else>
            <div class="max-h-64 overflow-y-auto rounded border">
              <button
                v-for="p in pastasFiltradas.slice(0, LIMITE_PASTAS)"
                :key="p.path"
                type="button"
                class="block w-full border-b border-border/50 px-2 py-1.5 text-left text-xs last:border-0"
                :class="pastaEscolhida === p.path ? 'bg-blue-100 font-medium dark:bg-blue-900/40' : 'hover:bg-muted'"
                @click="pastaEscolhida = p.path"
              >
                <span class="font-mono">{{ p.path }}</span>
                <span v-if="p.path === produto.fotos_path" class="ml-1 text-[10px] text-muted-foreground">(atual)</span>
              </button>
              <p v-if="!pastasFiltradas.length" class="px-2 py-3 text-center text-xs text-muted-foreground">
                Nenhuma pasta com esse nome.
              </p>
            </div>
            <p v-if="pastasFiltradas.length > LIMITE_PASTAS" class="text-[11px] text-muted-foreground">
              Mostrando {{ LIMITE_PASTAS }} de {{ pastasFiltradas.length }} — digite para achar mais rápido.
            </p>
          </template>
          <div class="flex flex-wrap items-center justify-between gap-2 pt-1">
            <!-- Travados durante o envio: desligar seria desfeito pelo lote
                 seguinte, e trocar dividiria os arquivos entre duas pastas. -->
            <button
              v-if="temPasta"
              class="btn btn-sm text-destructive"
              :disabled="salvandoPasta || enviando"
              :title="enviando ? 'Espere o envio terminar' : undefined"
              @click="desligarPasta"
            >
              <FolderX class="h-3.5 w-3.5 mr-1" /> Desligar a pasta deste produto
            </button>
            <span v-else></span>
            <div class="flex gap-2">
              <button class="btn btn-sm" :disabled="salvandoPasta" @click="trocando = false">Cancelar</button>
              <button
                class="btn btn-sm btn-primary"
                :disabled="salvandoPasta || enviando || !pastaEscolhida || pastaEscolhida === produto.fotos_path"
                :title="enviando ? 'Espere o envio terminar' : undefined"
                @click="salvarPasta"
              >
                <Loader2 v-if="salvandoPasta" class="h-3.5 w-3.5 mr-1 animate-spin" /> Salvar
              </button>
            </div>
          </div>
        </div>

        <!-- área de envio -->
        <div
          v-if="podeEditar"
          class="rounded-lg border-2 border-dashed p-4 text-center transition-colors"
          :class="arrastes > 0 ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20' : 'border-muted-foreground/30'"
          @dragenter.prevent="arrastes++"
          @dragover.prevent
          @dragleave.prevent="arrastes = Math.max(0, arrastes - 1)"
          @drop.prevent="onDrop"
        >
          <div v-if="enviando" class="flex items-center justify-center gap-2 text-sm text-blue-700 dark:text-blue-300">
            <Loader2 class="h-4 w-4 animate-spin" />
            Enviando {{ progresso?.ate ?? 0 }} de {{ progresso?.total ?? 0 }}…
          </div>
          <template v-else>
            <div class="flex flex-wrap items-center justify-center gap-2 text-sm">
              <Upload class="h-4 w-4 text-muted-foreground" />
              Arraste os arquivos aqui ou
              <button
                type="button"
                class="btn btn-sm"
                :disabled="salvandoPasta"
                :title="salvandoPasta ? 'Espere a troca de pasta terminar' : undefined"
                @click="escolherArquivos"
              >Escolher arquivos</button>
            </div>
            <p class="mt-1 text-[11px] text-muted-foreground">
              vão para <span class="font-mono break-all">{{ destinoEnvio }}</span>
            </p>
            <p class="text-[11px] text-muted-foreground">{{ aceitaTexto }}</p>
          </template>
          <input
            ref="inputArquivos"
            type="file"
            multiple
            :accept="acceptAba"
            class="hidden"
            @change="onEscolhidos"
          />
        </div>

        <div
          v-if="erroEnvio"
          class="flex items-start gap-2 rounded border border-destructive/50 bg-destructive/10 px-3 py-2 text-xs text-destructive"
        >
          <AlertCircle class="mt-0.5 h-4 w-4 shrink-0" />
          <span class="flex-1">{{ erroEnvio }}</span>
          <button class="shrink-0 underline" @click="erroEnvio = null">ok</button>
        </div>

        <!-- lista da aba -->
        <div v-if="carregando[aba] && !listas[aba]" class="py-8 text-center text-sm text-muted-foreground">
          <Loader2 class="inline h-4 w-4 animate-spin" /> Carregando…
        </div>
        <div
          v-else-if="erros[aba]"
          class="space-y-2 rounded border border-destructive/50 bg-destructive/10 px-3 py-3 text-sm text-destructive"
        >
          <p>{{ erros[aba] }}</p>
          <button class="btn btn-sm" @click="carregarAba(aba, true)">Tentar de novo</button>
        </div>
        <p v-else-if="listas[aba] && !arquivosAba.length" class="py-8 text-center text-sm text-muted-foreground">
          {{ textoVazio }}
        </p>

        <!-- vídeos: só a lista — vídeo não passa pelo DaVinci, abre no MEGA -->
        <div v-else-if="aba === 'videos' && listas.videos" class="space-y-3">
          <div v-for="g in grupos" :key="g.titulo" class="space-y-1">
            <h4 v-if="g.titulo" class="text-xs font-semibold text-muted-foreground">{{ g.titulo }}</h4>
            <div class="divide-y rounded border">
              <div
                v-for="a in g.itens"
                :key="a.nome"
                class="flex items-center gap-2 px-3 py-2 text-sm"
              >
                <Film class="h-4 w-4 shrink-0 text-muted-foreground" />
                <span class="min-w-0 flex-1 truncate" :title="a.nome">{{ nomeCurto(a.nome) }}</span>
                <span class="rounded bg-muted px-1.5 text-[10px] font-semibold uppercase">{{ extDe(a) }}</span>
                <a
                  v-if="produto.fotos_url"
                  :href="produto.fotos_url"
                  target="_blank"
                  rel="noopener"
                  class="btn btn-xs"
                ><ExternalLink class="h-3 w-3" /> Abrir no MEGA</a>
              </div>
            </div>
          </div>
        </div>

        <!-- fotos / embalagens: grade de miniaturas + cartões de arquivo -->
        <div v-else-if="listas[aba]" class="space-y-4">
          <div v-for="g in grupos" :key="g.titulo" class="space-y-1.5">
            <h4 v-if="g.titulo" class="text-xs font-semibold text-muted-foreground">{{ g.titulo }}</h4>
            <div class="grid grid-cols-[repeat(auto-fill,minmax(112px,1fr))] gap-2">
              <template v-for="a in g.itens" :key="a.nome">
                <button
                  v-if="mostraMiniatura(a)"
                  type="button"
                  class="group block min-w-0 text-left"
                  :title="a.nome"
                  @click="abrirVisor(a)"
                >
                  <div
                    class="relative aspect-square w-full overflow-hidden rounded border"
                    :class="temPrevia(a) ? 'bg-white' : 'bg-muted'"
                  >
                    <img
                      :src="urlArquivo(a.nome, false, true)"
                      :alt="nomeCurto(a.nome)"
                      loading="lazy"
                      class="h-full w-full transition-transform group-hover:scale-105"
                      :class="temPrevia(a) ? 'object-contain p-1' : 'object-cover'"
                      @error="falhas.add(a.nome)"
                    />
                    <span
                      v-if="temPrevia(a)"
                      class="absolute left-1 top-1 rounded bg-foreground/80 px-1 text-[10px] font-semibold uppercase text-background"
                    >{{ extDe(a) }}</span>
                  </div>
                  <div v-if="pastaDoMeio(a.nome)" class="mt-0.5 truncate text-[11px] font-medium">{{ pastaDoMeio(a.nome) }}</div>
                  <div class="truncate text-[11px] text-muted-foreground" :class="{ 'mt-0.5': !pastaDoMeio(a.nome) }">{{ nomeCurto(a.nome) }}</div>
                </button>
                <div v-else class="min-w-0" :title="a.nome">
                  <div class="flex aspect-square w-full flex-col items-center justify-center gap-1 rounded border bg-muted/40 p-2">
                    <component :is="ehVideo(a) ? Film : FileText" class="h-7 w-7 text-muted-foreground" />
                    <span class="rounded bg-background px-1.5 text-[11px] font-semibold uppercase">{{ extDe(a) || 'arquivo' }}</span>
                    <a
                      v-if="ehVideo(a) && produto.fotos_url"
                      :href="produto.fotos_url"
                      target="_blank"
                      rel="noopener"
                      class="btn btn-xs"
                    ><ExternalLink class="h-3 w-3" /> Abrir no MEGA</a>
                    <a
                      v-else-if="!ehVideo(a)"
                      :href="urlArquivo(a.nome, true)"
                      download
                      class="btn btn-xs"
                    ><Download class="h-3 w-3" /> Baixar</a>
                  </div>
                  <div class="mt-0.5 truncate text-[11px] text-muted-foreground">{{ nomeCurto(a.nome) }}</div>
                </div>
              </template>
            </div>
          </div>
        </div>

        <div v-if="faltamMostrar > 0" class="text-center">
          <button class="btn btn-sm" @click="limite += POR_PAGINA">
            Mostrar mais {{ Math.min(POR_PAGINA, faltamMostrar) }}
            <span class="ml-1 text-muted-foreground">(faltam {{ faltamMostrar }})</span>
          </button>
        </div>
      </div>

      <!-- ================================================ rodapé -->
      <div class="flex items-center justify-between gap-2 border-t px-4 py-2 text-xs">
        <span class="text-muted-foreground">
          {{ contadoEm ? `Contado em ${contadoEm}` : 'Ainda não contado' }}
        </span>
        <button
          v-if="podeEditar"
          class="btn btn-sm"
          :disabled="recontando || enviando || !produto.fotos_path"
          :title="produto.fotos_path ? 'Conta de novo as fotos, vídeos e embalagens desta pasta' : 'Este produto ainda não tem pasta'"
          @click="recontar"
        >
          <Loader2 v-if="recontando" class="h-3.5 w-3.5 mr-1 animate-spin" />
          <RefreshCw v-else class="h-3.5 w-3.5 mr-1" />
          Recontar esta pasta
        </button>
      </div>

      <!-- ================================================ visor de imagem -->
      <div
        v-if="visorArquivo"
        class="absolute inset-0 z-10 flex flex-col bg-black/90 text-white"
      >
        <div class="flex items-center justify-between gap-2 p-3">
          <div class="min-w-0">
            <div class="truncate text-sm" :title="visorArquivo.nome">{{ visorArquivo.nome }}</div>
            <div v-if="temPrevia(visorArquivo)" class="text-xs text-white/70">
              prévia — o arquivo é {{ extDe(visorArquivo).toUpperCase() }}; use Baixar para abrir no programa
            </div>
          </div>
          <div class="flex shrink-0 items-center gap-2">
            <span class="text-xs tabular-nums text-white/70">{{ (visor ?? 0) + 1 }} de {{ imagensAba.length }}</span>
            <a :href="urlArquivo(visorArquivo.nome, true)" download class="btn btn-sm text-foreground">
              <Download class="h-3.5 w-3.5 mr-1" /> Baixar
            </a>
            <button class="btn btn-sm text-foreground" title="Fechar (Esc)" @click="visor = null">
              <X class="h-3.5 w-3.5 mr-1" /> Fechar
            </button>
          </div>
        </div>
        <div class="relative flex min-h-0 flex-1 items-center justify-center p-3" @click.self="visor = null">
          <div v-if="visorCarregando" class="absolute flex items-center gap-2 text-sm text-white/80">
            <Loader2 class="h-4 w-4 animate-spin" /> Carregando…
          </div>
          <div v-else-if="visorErro" class="absolute max-w-sm text-center text-sm text-white/80">
            Não deu para abrir a prévia deste arquivo agora. Use <b>Baixar</b> para abrir o original.
          </div>
          <img
            :key="visorArquivo.nome"
            :src="urlVisor(visorArquivo)"
            :alt="nomeCurto(visorArquivo.nome)"
            class="max-h-full max-w-full object-contain transition-opacity"
            :class="{ 'bg-white': temPrevia(visorArquivo), 'opacity-0': visorCarregando || visorErro }"
            @load="visorFim($event, false)"
            @error="visorFim($event, true)"
          />
        </div>
        <div class="flex items-center justify-center gap-3 p-3">
          <button class="btn btn-sm text-foreground" :disabled="!visor" @click="visorAnterior">
            <ChevronLeft class="h-3.5 w-3.5 mr-1" /> Anterior
          </button>
          <button
            class="btn btn-sm text-foreground"
            :disabled="visor == null || visor >= imagensAba.length - 1"
            @click="visorProxima"
          >
            Próxima <ChevronRight class="h-3.5 w-3.5 ml-1" />
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
