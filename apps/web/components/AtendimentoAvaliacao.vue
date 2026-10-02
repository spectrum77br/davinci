<script lang="ts">
// Cartão da AVALIAÇÃO DE VENDA no topo da conversa (Atendimento, RF8,
// 02/10/2026) — como o da reclamação. As avaliações do PEDIDO da conversa
// (pela conversa ou pelo pedido/pack: o chat do pedido e a conversa da
// avaliação mostram o mesmo cartão) e as anteriores do MESMO comprador.
// Vem de GET /api/atendimento/conversas/{id}/avaliacoes
// (routers/atendimento_avaliacoes.py); quem lê as plataformas e decide a
// pendência é services/atendimento/avaliacoes.py (Shopee e Mercado Livre;
// TikTok, Magalu e Amazon não têm API de avaliação para o vendedor).
//
// O que o cartão mostra: estrelas (1–3 em destaque), título e texto do
// comprador, fotos/vídeos, produto, data, "respondida em …" com a resposta
// da loja, o prazo INTERNO da pendência (a plataforma não dá prazo: 24 h a
// partir de quando ficou pendente) e o "Abrir na plataforma".
//
// RESPONDER É PÚBLICO (aparece no anúncio, para qualquer comprador): a caixa
// "Responder em público" diz isso sempre, pede confirmação e sai por
// POST /avaliacoes/{id}/responder (o mesmo caminho único do envio). Com o
// envio desligado (ATENDIMENTO_ENVIO_ATIVO=false, o caso de hoje) a caixa
// aparece DESABILITADA com o porquê que o backend manda. Onde a plataforma
// não deixa responder (Mercado Livre: a opinião não tem resposta pela API),
// o motivo fica no lugar da caixa e a pendência sai com "Marcar como
// tratada" (POST /avaliacoes/{id}/tratada — nada vai para a plataforma).
// Na própria conversa da avaliação (canal `avaliacao`) quem responde é a
// caixa de resposta de baixo — o cartão não abre uma segunda caixa.
//
// Fotos: só endereço https da plataforma, miniatura `lazy` sem referrer (o
// mesmo jeito das fotos do chat); o clique abre grande na conversa. Vídeo
// não toca aqui: abre na plataforma, em outra aba.
//
// Este bloco (não o setup) é o módulo dos ajudantes puros, testados em
// tests/atendimento-avaliacao.cjs — por isso só importa TIPO.
import type { AvaliacaoContexto } from '~/components/AtendimentoPlataforma.vue'

export type MidiaAvaliacao = { tipo: string; url: string; miniatura?: string | null }

// O `AvaliacaoLojaOut` do backend.
export interface AvaliacaoLoja {
  id: string
  plataforma: string
  plataforma_nome: string
  // Id da avaliação NA PLATAFORMA (Shopee `comment_id`, ML id da opinião).
  comentario_id: string
  pedido: string | null
  item_id: string | null
  anuncio_titulo?: string | null
  estrelas: number
  nota_baixa: boolean
  titulo: string | null
  texto: string | null
  midia: MidiaAvaliacao[]
  criado_em: string | null
  // O comprador mudou a nota (Shopee `HAVE_EDITED_ONCE`).
  editada: boolean
  respondida: boolean
  resposta_loja: string | null
  resposta_em: string | null
  resposta_oculta: boolean | null
  // Dá para responder AGORA pelo DaVinci; senão, o porquê em `motivo_sem_resposta`.
  pode_responder: boolean
  motivo_sem_resposta: string | null
  pendente: boolean
  pendente_desde: string | null
  tratada_em: string | null
  tratada_por_nome: string | null
  conversa_id: string | null
  // true = do pedido desta conversa; false = anterior do mesmo comprador.
  do_pedido: boolean
  url_plataforma: string | null
}

export interface AvaliacoesResposta {
  itens: AvaliacaoLoja[]
  pendentes: number
  pior_pendente: number | null
  envio_ativo: boolean
  aviso: string
}

// As mesmas réguas do backend (o teste confere com constantes.py e avaliacoes.py):
// nota baixa = 1–3; quem deixa a loja responder pela API; o limite da resposta.
export const NOTA_BAIXA = 3
export const PLATAFORMAS_RESPONDEM = ['shopee']
export const LIMITE_RESPOSTA: Record<string, number> = { shopee: 500 }
// O prazo da conversa `avaliacao` (avaliacoes.PRAZO_RESPOSTA): interno, contado
// de quando a avaliação ficou pendente. "Vencendo" = menos de 2 h (como a lista).
export const PRAZO_INTERNO_MS = 24 * 3600 * 1000
export const VENCENDO_MS = 2 * 3600 * 1000
// O aviso da caixa (o backend manda o dele em `aviso`; este é a rede).
export const AVISO_RESPOSTA_PUBLICA = 'A resposta à avaliação é PÚBLICA: aparece no anúncio, para qualquer comprador.'
// A pergunta antes de publicar — a MESMA no cartão e na caixa de baixo da
// conversa `avaliacao` (AtendimentoConversa): a resposta sai no anúncio.
export function perguntaRespostaPublica(aviso: string | null | undefined, texto: string): string {
  return `Responder em PÚBLICO?\n\n${aviso || AVISO_RESPOSTA_PUBLICA}\n\nA resposta:\n${texto}`
}

export function respondePelaApi(plataforma: string | null | undefined): boolean {
  return PLATAFORMAS_RESPONDEM.includes((plataforma || '').trim().toLowerCase())
}
// Tratada = tem a marca (no resumo do contexto ela vem sem a hora: '').
export function foiTratada(a: Pick<AvaliacaoLoja, 'tratada_em'>): boolean {
  return a.tratada_em !== null && a.tratada_em !== undefined
}

// "★★☆☆☆"; fora de 1–5, ''.
export function estrelasDe(n: number | null | undefined): string {
  const v = Number(n)
  if (!Number.isInteger(v) || v < 1 || v > 5) return ''
  return '★'.repeat(v) + '☆'.repeat(5 - v)
}
export function ehNotaBaixa(n: number | null | undefined): boolean {
  return !!estrelasDe(n) && Number(n) <= NOTA_BAIXA
}
// A cor das estrelas: 1–3 vermelho (o destaque), 4–5 amarelo.
export function corDaNota(n: number | null | undefined): string {
  return ehNotaBaixa(n) ? 'text-red-600 dark:text-red-400' : 'text-yellow-500 dark:text-yellow-400'
}
// A borda do cartão: amarela; pendente com nota baixa, vermelha.
export function bordaDaAvaliacao(a: Pick<AvaliacaoLoja, 'estrelas' | 'pendente'>): string {
  return a.pendente && ehNotaBaixa(a.estrelas) ? 'border-red-500/50 bg-yellow-400/10' : 'border-yellow-400/60 bg-yellow-400/5'
}

// "45 min", "3 h 20 min", "2 d 4 h" (o mesmo jeito da lista e da reclamação).
export function tempo(min: number): string {
  const m = Math.max(0, Math.round(min))
  if (m < 60) return `${m} min`
  const h = Math.floor(m / 60)
  if (h < 24) return m % 60 ? `${h} h ${m % 60} min` : `${h} h`
  const d = Math.floor(h / 24)
  return h % 24 ? `${d} d ${h % 24} h` : `${d} d`
}
function quando(iso: string | null | undefined): number {
  const t = iso ? new Date(iso).getTime() : Number.NaN
  return Number.isNaN(t) ? 0 : t
}

export type PrazoAvaliacao = { nivel: 'vencida' | 'vencendo' | 'ok'; texto: string; titulo: string; cls: string }
// A contagem do prazo INTERNO da pendência; null se não está pendente.
export function prazoAvaliacao(
  a: Pick<AvaliacaoLoja, 'pendente' | 'pendente_desde' | 'respondida' | 'tratada_em'>,
  agora = Date.now(),
): PrazoAvaliacao | null {
  if (!a.pendente || a.respondida || foiTratada(a) || !a.pendente_desde) return null
  const ini = new Date(a.pendente_desde).getTime()
  if (Number.isNaN(ini)) return null
  const fim = ini + PRAZO_INTERNO_MS
  const falta = fim - agora
  const ate = new Date(fim).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
  const titulo = `prazo interno do DaVinci (a plataforma não dá prazo para responder avaliação): até ${ate}`
  if (falta <= 0) return { nivel: 'vencida', texto: `prazo interno vencido há ${tempo(-falta / 60000)}`, titulo, cls: 'bg-red-600 text-white' }
  if (falta < VENCENDO_MS) return { nivel: 'vencendo', texto: `faltam ${tempo(falta / 60000)}`, titulo, cls: 'bg-amber-500/20 text-amber-800 dark:text-amber-300' }
  return { nivel: 'ok', texto: `faltam ${tempo(falta / 60000)}`, titulo, cls: 'bg-muted text-muted-foreground' }
}

// Em que pé está a avaliação, numa palavra (o selo do cartão e do painel).
export function situacaoAvaliacao(
  a: Pick<AvaliacaoLoja, 'plataforma' | 'estrelas' | 'respondida' | 'resposta_oculta' | 'tratada_em' | 'pendente'>,
): { rotulo: string; dica: string; cls: string } {
  if (a.respondida) {
    return {
      rotulo: a.resposta_oculta ? 'Respondida (resposta oculta)' : 'Respondida',
      dica: 'a loja já respondeu na plataforma (pelo DaVinci, pelo robô da loja ou à mão)',
      cls: 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300',
    }
  }
  if (foiTratada(a)) {
    return { rotulo: 'Tratada', dica: 'marcada como tratada no DaVinci (nada foi enviado à plataforma)', cls: 'bg-sky-500/15 text-sky-700 dark:text-sky-300' }
  }
  if (a.pendente) {
    return {
      rotulo: 'Sem resposta',
      dica: 'pendente: sem resposta da loja depois da carência — fica com a etiqueta Avaliação até ser respondida ou tratada',
      cls: ehNotaBaixa(a.estrelas) ? 'bg-red-500/15 text-red-700 dark:text-red-300' : 'bg-amber-500/20 text-amber-800 dark:text-amber-300',
    }
  }
  if (respondePelaApi(a.plataforma)) {
    return {
      rotulo: 'Sem resposta',
      dica: 'ainda não é pendência: o robô da loja costuma responder em 1 a 3 h (carência de 1 h na nota 1–3 e de 24 h na 4–5); avaliação com mais de 30 dias não entra na fila',
      cls: 'bg-muted text-muted-foreground',
    }
  }
  return { rotulo: 'Só informação', dica: 'nota 4–5 no Mercado Livre: não vira pendência (e a opinião não tem resposta pela API)', cls: 'bg-muted text-muted-foreground' }
}

// Os três grupos do cartão: as ABERTAS (do pedido, pendentes ou ainda sem
// resposta onde dá para responder) em cartão inteiro, a pendente e a pior
// nota primeiro; as RESOLVIDAS do pedido numa linha cada; as ANTERIORES do
// comprador recolhidas. O backend já manda nessa ordem; a tela não confia.
export function separarAvaliacoes(itens: AvaliacaoLoja[]): { abertas: AvaliacaoLoja[]; resolvidas: AvaliacaoLoja[]; anteriores: AvaliacaoLoja[] } {
  const recentes = (a: AvaliacaoLoja, b: AvaliacaoLoja) => quando(b.criado_em) - quando(a.criado_em)
  const aberta = (a: AvaliacaoLoja) => a.pendente || (respondePelaApi(a.plataforma) && !a.respondida && !foiTratada(a))
  const doPedido = itens.filter((a) => a.do_pedido)
  return {
    abertas: doPedido.filter(aberta).sort((a, b) => Number(b.pendente) - Number(a.pendente) || a.estrelas - b.estrelas || recentes(a, b)),
    resolvidas: doPedido.filter((a) => !aberta(a)).sort(recentes),
    anteriores: itens.filter((a) => !a.do_pedido).sort(recentes),
  }
}

// Só o que é endereço https da plataforma vira miniatura/link (nada de
// `javascript:`, `data:` ou http solto); tipo desconhecido vira imagem só se
// a URL parecer imagem.
export type MidiaTela = { tipo: 'imagem' | 'video'; url: string; miniatura: string | null }
const RE_HTTPS = /^https:\/\/[^\s"'<>]+$/i
export function midiaSegura(midia: unknown): MidiaTela[] {
  if (!Array.isArray(midia)) return []
  const out: MidiaTela[] = []
  for (const m of midia) {
    if (!m || typeof m !== 'object') continue
    const o = m as Record<string, unknown>
    const url = typeof o.url === 'string' ? o.url.trim() : ''
    if (!RE_HTTPS.test(url)) continue
    const mini = typeof o.miniatura === 'string' && RE_HTTPS.test(o.miniatura.trim()) ? o.miniatura.trim() : null
    const tipo = String(o.tipo || '').toLowerCase()
    out.push({ tipo: tipo.startsWith('vid') ? 'video' : 'imagem', url, miniatura: mini })
  }
  return out
}

export type CaixaAvaliacao = { modo: 'caixa' | 'desligada' | 'abaixo' | 'motivo' | 'nenhuma'; motivo: string }
// A caixa "Responder em público" desta avaliação:
//  - caixa: dá para responder agora;
//  - desligada: a plataforma deixa, mas não agora (envio desligado, sem permissão) — caixa cinza com o porquê;
//  - abaixo: é a avaliação da conversa aberta (canal `avaliacao`) — responde-se na caixa de baixo;
//  - motivo: a plataforma não deixa responder (ML) — o motivo no lugar da caixa;
//  - nenhuma: já respondida.
export function caixaDaAvaliacao(
  a: Pick<AvaliacaoLoja, 'plataforma' | 'respondida' | 'pode_responder' | 'motivo_sem_resposta' | 'conversa_id'>,
  { canEdit, conversaId, canalConversa }: { canEdit: boolean; conversaId?: string | null; canalConversa?: string | null },
): CaixaAvaliacao {
  if (a.respondida) return { modo: 'nenhuma', motivo: '' }
  if (!respondePelaApi(a.plataforma)) {
    return { modo: 'motivo', motivo: a.motivo_sem_resposta || 'Esta plataforma não deixa responder a avaliação pela API.' }
  }
  if (canalConversa === 'avaliacao' && !!a.conversa_id && a.conversa_id === conversaId) {
    return {
      modo: 'abaixo',
      motivo: a.pode_responder ? 'Responda na caixa de resposta abaixo — a resposta é pública.' : (a.motivo_sem_resposta || 'A resposta não pode sair pelo DaVinci agora.'),
    }
  }
  if (!a.pode_responder) return { modo: 'desligada', motivo: a.motivo_sem_resposta || 'A resposta não pode sair pelo DaVinci agora.' }
  if (!canEdit) return { modo: 'desligada', motivo: 'Você pode ler, mas não responder: falta a permissão de editar o Atendimento.' }
  return { modo: 'caixa', motivo: '' }
}

// "Marcar como tratada": a pendência sem resposta (o caminho do ML; vale
// para toda pendente). Nada vai para a plataforma.
export function podeMarcarTratada(a: Pick<AvaliacaoLoja, 'pendente' | 'respondida' | 'tratada_em'>, canEdit: boolean): boolean {
  return canEdit && a.pendente && !a.respondida && !foiTratada(a)
}

// O painel do pedido sem o cartão (a rota falhou ou ainda não respondeu): o
// resumo do contexto (nota e estado, sem texto nem foto) no mesmo formato.
// O contexto manda o NOME da plataforma ("Shopee", "Mercado Livre"); a tela
// decide pelo código.
const CODIGO_DO_NOME: Record<string, string> = { shopee: 'shopee', 'mercado livre': 'ml', ml: 'ml' }
export function doContexto(c: AvaliacaoContexto): AvaliacaoLoja {
  const estrelas = Number(c.estrelas) || 0
  const nome = String(c.plataforma || '').trim()
  return {
    id: String(c.id),
    plataforma: CODIGO_DO_NOME[nome.toLowerCase()] || nome.toLowerCase(),
    plataforma_nome: nome,
    comentario_id: '',
    pedido: c.pedido ?? null,
    item_id: null,
    anuncio_titulo: null,
    estrelas,
    nota_baixa: ehNotaBaixa(estrelas),
    titulo: null,
    texto: null,
    midia: [],
    criado_em: c.criado_em ?? null,
    editada: false,
    respondida: !!c.respondida,
    resposta_loja: null,
    resposta_em: null,
    resposta_oculta: null,
    pode_responder: !!c.pode_responder,
    motivo_sem_resposta: null,
    pendente: !!c.pendente,
    pendente_desde: null,
    // O contexto diz SE foi tratada, não quando.
    tratada_em: c.tratada ? '' : null,
    tratada_por_nome: null,
    conversa_id: null,
    do_pedido: !!c.do_pedido,
    url_plataforma: null,
  }
}
</script>

<script setup lang="ts">
import { ChevronDown, ChevronRight, CircleCheck, ExternalLink, Globe, ImageOff, Loader2, Lock, MessageSquare, Play, Send, Star, X } from 'lucide-vue-next'
import type { PerfilAdsPower } from '~/components/AtendimentoAdsPower.vue'
import { abrirEm } from '~/components/AtendimentoReclamacao.vue'
import { erroDaApi, erroEnvioLegivel, fmtDataHora, statusDoErro, tamanhoDoEnvio, useRelogio, type Mensagem } from '~/components/AtendimentoPlataforma.vue'

const props = withDefaults(defineProps<{
  conversaId: string
  // O canal da conversa aberta: na conversa `avaliacao`, quem responde é a
  // caixa de baixo (o cartão não abre outra).
  canalConversa?: string | null
  canEdit?: boolean
  // O perfil do AdsPower da loja: "Abrir no …" abre nele.
  perfil?: PerfilAdsPower | null
}>(), { canalConversa: null, canEdit: false, perfil: null })
const emit = defineEmits<{
  // Para quem monta a conversa: o que veio (a faixa só aparece com avaliação
  // do pedido; o painel do pedido usa a mesma resposta).
  (e: 'carregado', r: AvaliacoesResposta): void
  // Respondeu ou marcou como tratada: a etiqueta e a fila mudaram.
  (e: 'mudou'): void
  (e: 'abrirImagem', i: { url: string; nome: string }): void
  // A conversa da avaliação (canal `avaliacao`), quando é outra.
  (e: 'abrirConversa', id: string): void
}>()
const { api } = useApi()
const toasts = useToasts()
const agora = useRelogio()

const dados = ref<AvaliacoesResposta | null>(null)
const erro = ref(false)
const grupos = computed(() => separarAvaliacoes(dados.value?.itens || []))
const doPedido = computed(() => grupos.value.abertas.length + grupos.value.resolvidas.length)
const aviso = computed(() => dados.value?.aviso || AVISO_RESPOSTA_PUBLICA)
// As resolvidas e as anteriores abertas por inteiro (clique na linha).
const abertos = ref<Set<string>>(new Set())
const verAnteriores = ref(false)
function alternar(id: string) {
  const s = new Set(abertos.value)
  if (s.has(id)) s.delete(id)
  else s.add(id)
  abertos.value = s
}

type Linha =
  | { k: 'cartao'; a: AvaliacaoLoja; recolhivel: boolean }
  | { k: 'linha'; a: AvaliacaoLoja }
  | { k: 'grupo' }
const linhas = computed<Linha[]>(() => {
  const g = grupos.value
  const out: Linha[] = g.abertas.map((a) => ({ k: 'cartao', a, recolhivel: false }))
  const item = (a: AvaliacaoLoja): Linha => (abertos.value.has(a.id) ? { k: 'cartao', a, recolhivel: true } : { k: 'linha', a })
  out.push(...g.resolvidas.map(item))
  if (g.anteriores.length) {
    out.push({ k: 'grupo' })
    if (verAnteriores.value) out.push(...g.anteriores.map(item))
  }
  return out
})

let pedidoAtual = 0
async function carregar() {
  const id = props.conversaId
  if (!id) return
  const meu = ++pedidoAtual
  erro.value = false
  try {
    const r = await api<AvaliacoesResposta>(`/api/atendimento/conversas/${encodeURIComponent(id)}/avaliacoes`)
    if (meu !== pedidoAtual) return // trocou de conversa no meio
    dados.value = { ...r, itens: Array.isArray(r?.itens) ? r.itens : [] }
    emit('carregado', dados.value)
  } catch {
    if (meu !== pedidoAtual) return
    // O cartão é complemento da conversa: sem ele, a conversa segue (e o
    // painel fica com o resumo do contexto).
    dados.value = null
    erro.value = true
  }
}

// A avaliação que voltou da ação entra no lugar da antiga (o "do pedido" é
// o desta conversa, não o da rota) e o resumo acompanha.
function substituir(nova: AvaliacaoLoja | null | undefined) {
  const d = dados.value
  if (!d || !nova) return
  const itens = d.itens.map((x) => (x.id === nova.id ? { ...nova, do_pedido: x.do_pedido } : x))
  const pendentes = itens.filter((x) => x.pendente).map((x) => x.estrelas)
  dados.value = { ...d, itens, pendentes: pendentes.length, pior_pendente: pendentes.length ? Math.min(...pendentes) : null }
  emit('carregado', dados.value)
}

function caixa(a: AvaliacaoLoja): CaixaAvaliacao {
  return caixaDaAvaliacao(a, { canEdit: props.canEdit, conversaId: props.conversaId, canalConversa: props.canalConversa })
}

// ─── responder em público ───────────────────────────────────────────────────
// O rascunho de cada avaliação fica na memória enquanto a página está aberta.
const textos = reactive<Record<string, string>>({})
const enviandoId = ref<string | null>(null)
function limiteDaResposta(a: AvaliacaoLoja): number {
  return LIMITE_RESPOSTA[a.plataforma] || 500
}
// Conta como o backend conta (o texto normalizado que sai).
function tamanhoDaResposta(a: AvaliacaoLoja): number {
  return tamanhoDoEnvio(textos[a.id] || '', a.plataforma)
}
function podeEnviar(a: AvaliacaoLoja): boolean {
  const n = tamanhoDaResposta(a)
  return caixa(a).modo === 'caixa' && !enviandoId.value && n > 0 && n <= limiteDaResposta(a)
}
// Recusas que mudam o estado da avaliação: relê para mostrar o de agora.
const RELER = new Set(['ja_respondida', 'envio_desligado', 'somente_leitura', 'canal_em_observacao', 'envio_em_andamento', 'envio_a_conferir', 'avaliacao_nao_encontrada'])
// Nestas, a frase do backend é a certa (traz o "resposta pública" e o porquê da plataforma).
const FRASE_DO_BACKEND = new Set(['envio_desligado', 'somente_leitura'])

async function responder(a: AvaliacaoLoja, confirmado = false) {
  if (!podeEnviar(a)) return
  const texto = (textos[a.id] || '').trim()
  if (!confirmado && !confirm(perguntaRespostaPublica(aviso.value, texto))) return
  enviandoId.value = a.id
  let deNovo = false
  try {
    const r = await api<{ mensagem: Mensagem | null; avaliacao: AvaliacaoLoja | null }>(
      `/api/atendimento/avaliacoes/${encodeURIComponent(a.id)}/responder`,
      { method: 'POST', body: confirmado ? { texto, confirmar: true } : { texto } },
    )
    substituir(r?.avaliacao)
    const m = r?.mensagem
    if (m?.status === 'falhou') {
      // O texto fica na caixa para tentar de novo.
      toasts.error('A plataforma recusou a resposta — ela NÃO foi publicada', erroEnvioLegivel(m.erro))
    } else {
      delete textos[a.id]
      if (m?.status === 'revisar') toasts.warning('Não deu para confirmar a publicação', 'Pode ter saído. Confira no anúncio antes de responder de novo — o DaVinci não manda outra sozinho.')
      else toasts.success('Resposta pública enviada', 'Aparece no anúncio, junto da avaliação.')
    }
    emit('mudou')
  } catch (e: any) {
    const d = e?.data?.detail
    const code = typeof d?.code === 'string' ? d.code : ''
    const st = statusDoErro(e)
    if (!code && (!st || st >= 500)) {
      toasts.warning('Não deu para confirmar a resposta — pode ter saído', 'Confira no anúncio antes de responder de novo.')
      void carregar()
      emit('mudou')
    } else {
      const er = erroDaApi(e, 'Não consegui responder a avaliação')
      if (code === 'conversa_mudou' && confirm(`${er.texto}\n\n${er.motivos.join('\n')}\n\nResponder mesmo assim?`)) {
        deNovo = true
      } else {
        const frase = FRASE_DO_BACKEND.has(code) && typeof d?.detail === 'string' && d.detail ? d.detail : er.texto
        toasts.error(frase, er.motivos)
        if (RELER.has(code)) void carregar()
      }
    }
  } finally {
    enviandoId.value = null
  }
  if (deNovo) await responder(a, true)
}

// ─── marcar como tratada ────────────────────────────────────────────────────
const tratarAberto = ref<string | null>(null)
const motivoTratada = ref('')
const tratandoId = ref<string | null>(null)
function abrirTratar(a: AvaliacaoLoja) {
  motivoTratada.value = ''
  tratarAberto.value = tratarAberto.value === a.id ? null : a.id
}
async function marcarTratada(a: AvaliacaoLoja) {
  if (!podeMarcarTratada(a, props.canEdit) || tratandoId.value) return
  tratandoId.value = a.id
  try {
    const r = await api<{ avaliacao: AvaliacaoLoja | null }>(
      `/api/atendimento/avaliacoes/${encodeURIComponent(a.id)}/tratada`,
      { method: 'POST', body: { motivo: motivoTratada.value.trim() || null } },
    )
    substituir(r?.avaliacao)
    tratarAberto.value = null
    toasts.success('Avaliação marcada como tratada', 'Saiu da pendência e a etiqueta volta ao status anterior. Nada foi enviado à plataforma.')
    emit('mudou')
  } catch (e: any) {
    const er = erroDaApi(e, 'Não consegui marcar como tratada')
    toasts.error(er.texto, er.motivos)
    if (RELER.has(e?.data?.detail?.code)) void carregar()
  } finally {
    tratandoId.value = null
  }
}

// ─── fotos ──────────────────────────────────────────────────────────────────
const fotosFalhas = ref<Set<string>>(new Set())
function falhou(url: string) {
  fotosFalhas.value = new Set(fotosFalhas.value).add(url)
}
function nomeDaFoto(a: AvaliacaoLoja, i: number) {
  return `foto ${i + 1} da avaliação ${a.estrelas}★`
}

// Troca de conversa: o que estava aberto era da anterior. Fica no fim do
// setup porque roda na hora (immediate) e mexe em estado declarado acima.
watch(() => props.conversaId, () => {
  dados.value = null
  abertos.value = new Set()
  verAnteriores.value = false
  tratarAberto.value = null
  void carregar()
}, { immediate: true })
defineExpose({ carregar })
</script>

<template>
  <div v-if="doPedido" class="space-y-2" aria-label="Avaliações de venda">
    <template v-for="l in linhas" :key="l.k === 'grupo' ? 'grupo-anteriores' : `${l.k}-${l.a.id}`">
      <!-- cartão inteiro -->
      <article
        v-if="l.k === 'cartao'"
        class="rounded-md border p-3 text-[13px] leading-5"
        :class="bordaDaAvaliacao(l.a)"
        :aria-label="`Avaliação ${l.a.estrelas} de 5 — ${l.a.plataforma_nome}`"
        data-avaliacao
      >
        <div class="flex flex-wrap items-center gap-1.5">
          <span class="inline-flex items-center gap-1 rounded bg-yellow-400/25 px-1.5 py-px text-xs font-semibold text-yellow-800 dark:text-yellow-300">
            <Star class="size-3.5" aria-hidden="true" /> Avaliação
          </span>
          <span class="text-base leading-5 tracking-tight" :class="corDaNota(l.a.estrelas)" :aria-label="`nota ${l.a.estrelas} de 5`">{{ estrelasDe(l.a.estrelas) }}</span>
          <span class="font-semibold tabular-nums">{{ l.a.estrelas }}/5</span>
          <span v-if="l.a.nota_baixa" class="rounded bg-red-600 px-1.5 py-px text-[10px] font-semibold text-white" title="nota 1–3: atenção ao tom">nota baixa</span>
          <span class="text-xs text-muted-foreground">· {{ l.a.plataforma_nome }}<template v-if="!l.a.do_pedido && l.a.pedido"> · pedido {{ l.a.pedido }}</template></span>
          <span
            v-if="prazoAvaliacao(l.a, agora)"
            class="ml-auto rounded px-1.5 py-px text-xs font-medium tabular-nums"
            :class="prazoAvaliacao(l.a, agora)!.cls"
            :title="prazoAvaliacao(l.a, agora)!.titulo"
          >{{ prazoAvaliacao(l.a, agora)!.texto }}</span>
          <button
            v-if="l.recolhivel"
            type="button"
            class="rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
            :class="prazoAvaliacao(l.a, agora) ? '' : 'ml-auto'"
            title="recolher"
            aria-label="recolher a avaliação"
            @click="alternar(l.a.id)"
          >
            <ChevronDown class="size-3.5" />
          </button>
        </div>

        <div v-if="l.a.anuncio_titulo || l.a.item_id" class="mt-0.5 truncate text-xs text-muted-foreground" :title="l.a.anuncio_titulo || ''">
          Produto: {{ l.a.anuncio_titulo || `anúncio ${l.a.item_id}` }}
        </div>
        <div v-if="l.a.titulo" class="mt-1 font-medium">{{ l.a.titulo }}</div>
        <div v-if="l.a.texto" class="mt-0.5 whitespace-pre-wrap break-words">{{ l.a.texto }}</div>
        <div v-else-if="!l.a.titulo" class="mt-0.5 italic text-muted-foreground">Sem comentário — só a nota.</div>

        <!-- fotos e vídeos do comprador (só https da plataforma) -->
        <div v-if="midiaSegura(l.a.midia).length" class="mt-2 flex flex-wrap gap-1.5">
          <template v-for="(m, i) in midiaSegura(l.a.midia)" :key="m.url">
            <button
              v-if="m.tipo === 'imagem' && !fotosFalhas.has(m.url)"
              type="button"
              class="overflow-hidden rounded-md border bg-background"
              :title="`abrir ${nomeDaFoto(l.a, i)}`"
              @click="emit('abrirImagem', { url: m.url, nome: nomeDaFoto(l.a, i) })"
            >
              <img :src="m.url" :alt="nomeDaFoto(l.a, i)" loading="lazy" referrerpolicy="no-referrer" class="size-16 object-cover" @error="falhou(m.url)" />
            </button>
            <a
              v-else
              :href="m.url"
              target="_blank"
              rel="noopener noreferrer"
              class="relative flex size-16 items-center justify-center overflow-hidden rounded-md border bg-muted text-muted-foreground hover:text-foreground"
              :title="m.tipo === 'video' ? 'abrir o vídeo do comprador (outra aba)' : 'abrir a foto (outra aba)'"
            >
              <img v-if="m.miniatura && !fotosFalhas.has(m.miniatura)" :src="m.miniatura" alt="" loading="lazy" referrerpolicy="no-referrer" class="absolute inset-0 size-full object-cover opacity-70" @error="falhou(m.miniatura!)" />
              <!-- Vídeo: abre na plataforma. Foto que não carregou aqui: o link para a original. -->
              <Play v-if="m.tipo === 'video'" class="relative size-5" aria-hidden="true" />
              <ImageOff v-else class="relative size-5" aria-hidden="true" />
              <span class="sr-only">{{ m.tipo === 'video' ? 'vídeo' : 'foto' }}</span>
            </a>
          </template>
        </div>

        <div class="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-muted-foreground">
          <span v-if="l.a.criado_em">avaliada em {{ fmtDataHora(l.a.criado_em) }}</span>
          <span v-if="l.a.editada" title="a Shopee deixa o comprador mudar a nota uma vez">· o comprador editou a nota</span>
          <span class="rounded px-1.5 py-px" :class="situacaoAvaliacao(l.a).cls" :title="situacaoAvaliacao(l.a).dica">{{ situacaoAvaliacao(l.a).rotulo }}</span>
          <span v-if="l.a.pendente && l.a.pendente_desde">pendente desde {{ fmtDataHora(l.a.pendente_desde) }}</span>
        </div>

        <!-- a resposta da loja -->
        <div v-if="l.a.respondida" class="mt-2 rounded-md border-l-2 border-emerald-500 bg-emerald-500/5 px-2 py-1.5" data-resposta-loja>
          <div class="text-xs font-medium text-emerald-700 dark:text-emerald-300">
            Respondida<template v-if="l.a.resposta_em"> em {{ fmtDataHora(l.a.resposta_em) }}</template> · resposta pública da loja
          </div>
          <div v-if="l.a.resposta_loja" class="whitespace-pre-wrap break-words">{{ l.a.resposta_loja }}</div>
          <div v-if="l.a.resposta_oculta" class="mt-0.5 text-xs text-amber-800 dark:text-amber-300">A resposta está oculta na plataforma.</div>
        </div>
        <div v-else-if="foiTratada(l.a)" class="mt-2 flex items-center gap-1 text-xs text-sky-700 dark:text-sky-300">
          <CircleCheck class="size-3.5" aria-hidden="true" />
          Marcada como tratada<template v-if="l.a.tratada_em"> em {{ fmtDataHora(l.a.tratada_em) }}</template><template v-if="l.a.tratada_por_nome"> por {{ l.a.tratada_por_nome }}</template> — nada foi enviado à plataforma.
        </div>

        <!-- responder em público -->
        <div v-if="caixa(l.a).modo === 'caixa' || caixa(l.a).modo === 'desligada'" class="mt-2 space-y-1" data-caixa-publica>
          <div class="flex items-start gap-1.5 text-[11px] leading-4 text-amber-900 dark:text-amber-200">
            <Globe class="mt-px size-3.5 shrink-0" aria-hidden="true" />
            <span>{{ aviso }}</span>
          </div>
          <textarea
            v-model="textos[l.a.id]"
            rows="2"
            :disabled="caixa(l.a).modo !== 'caixa' || enviandoId === l.a.id"
            class="block min-h-[56px] w-full resize-y rounded-md border bg-background px-2.5 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary disabled:cursor-not-allowed disabled:opacity-60"
            :placeholder="caixa(l.a).modo === 'caixa' ? 'Responder em público… (aparece no anúncio)' : 'Responder em público — desabilitado agora'"
            aria-label="resposta pública à avaliação"
          />
          <div v-if="caixa(l.a).modo === 'desligada'" class="flex items-start gap-1.5 text-[11px] leading-4 text-muted-foreground" data-motivo-sem-resposta>
            <Lock class="mt-px size-3.5 shrink-0" aria-hidden="true" />
            <span>{{ caixa(l.a).motivo }}</span>
          </div>
          <div class="flex items-center justify-end gap-2">
            <span
              v-if="caixa(l.a).modo === 'caixa'"
              class="text-[11px] tabular-nums"
              :class="tamanhoDaResposta(l.a) > limiteDaResposta(l.a) ? 'font-semibold text-red-600 dark:text-red-400' : 'text-muted-foreground'"
              :title="`limite de ${limiteDaResposta(l.a)} caracteres na resposta da avaliação`"
            >{{ tamanhoDaResposta(l.a) }}/{{ limiteDaResposta(l.a) }}</span>
            <button
              type="button"
              class="inline-flex items-center gap-1 rounded-md bg-primary px-2.5 py-1 text-xs font-medium text-primary-foreground disabled:cursor-not-allowed disabled:opacity-50"
              :disabled="!podeEnviar(l.a)"
              :title="caixa(l.a).modo === 'caixa' ? 'publicar a resposta no anúncio (pede confirmação)' : caixa(l.a).motivo"
              @click="responder(l.a)"
            >
              <Loader2 v-if="enviandoId === l.a.id" class="size-3.5 animate-spin" />
              <Send v-else class="size-3.5" />
              Responder em público
            </button>
          </div>
        </div>
        <div
          v-else-if="caixa(l.a).modo === 'motivo' || caixa(l.a).modo === 'abaixo'"
          class="mt-2 flex items-start gap-1.5 rounded-md bg-muted/60 px-2 py-1 text-xs text-muted-foreground"
          data-motivo-sem-resposta
        >
          <Lock v-if="caixa(l.a).modo === 'motivo'" class="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
          <Globe v-else class="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
          <span>{{ caixa(l.a).motivo }}</span>
        </div>

        <!-- ações -->
        <div class="mt-2 flex flex-wrap items-center gap-1.5 text-xs">
          <button
            v-if="podeMarcarTratada(l.a, canEdit)"
            type="button"
            class="inline-flex items-center gap-1 rounded border bg-background px-2 py-0.5 hover:bg-muted disabled:opacity-50"
            :disabled="!!tratandoId"
            title="tira a avaliação da pendência (a etiqueta volta ao status anterior) — nada vai para a plataforma"
            :aria-expanded="tratarAberto === l.a.id"
            @click="abrirTratar(l.a)"
          >
            <CircleCheck class="size-3.5" aria-hidden="true" /> Marcar como tratada
          </button>
          <button
            v-if="l.a.conversa_id && l.a.conversa_id !== conversaId"
            type="button"
            class="inline-flex items-center gap-1 rounded border bg-background px-2 py-0.5 hover:bg-muted"
            title="abrir a conversa desta avaliação (a pendência mora lá)"
            @click="emit('abrirConversa', l.a.conversa_id)"
          >
            <MessageSquare class="size-3.5" aria-hidden="true" /> abrir a conversa da avaliação
          </button>
          <AtendimentoAbrirPlataforma
            v-if="l.a.url_plataforma"
            :href="l.a.url_plataforma"
            :perfil="perfil"
            :conversa-id="conversaId"
            titulo="abre a venda na plataforma (outra aba)"
            class="ml-auto inline-flex items-center gap-1 rounded border bg-background px-2 py-0.5 hover:bg-muted"
          >
            <ExternalLink class="size-3.5" aria-hidden="true" />
            {{ abrirEm(l.a.plataforma, l.a.plataforma_nome) }}
          </AtendimentoAbrirPlataforma>
        </div>
        <div v-if="tratarAberto === l.a.id" class="mt-1.5 flex flex-wrap items-center gap-1.5 text-xs" data-tratar>
          <input
            v-model="motivoTratada"
            maxlength="300"
            class="h-7 min-w-[200px] flex-1 rounded-md border bg-background px-2 text-xs"
            placeholder="o que foi feito? (opcional — fica só no DaVinci)"
            aria-label="o que foi feito com a avaliação"
            @keydown.enter.prevent="marcarTratada(l.a)"
          />
          <button
            type="button"
            class="inline-flex items-center gap-1 rounded-md bg-primary px-2 py-1 text-primary-foreground disabled:opacity-50"
            :disabled="!!tratandoId"
            @click="marcarTratada(l.a)"
          >
            <Loader2 v-if="tratandoId === l.a.id" class="size-3.5 animate-spin" /> marcar
          </button>
          <button type="button" class="rounded p-1 text-muted-foreground hover:bg-muted" title="cancelar" aria-label="cancelar" @click="tratarAberto = null"><X class="size-3.5" /></button>
        </div>
      </article>

      <!-- uma linha (resolvida do pedido ou anterior do comprador): clique abre -->
      <button
        v-else-if="l.k === 'linha'"
        type="button"
        class="flex w-full flex-wrap items-center gap-1.5 rounded-md border px-2 py-1.5 text-left text-xs hover:bg-muted"
        :aria-expanded="false"
        :title="`ver a avaliação inteira${l.a.texto ? '' : ' (sem comentário)'}`"
        @click="alternar(l.a.id)"
      >
        <ChevronRight class="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
        <Star class="size-3.5 shrink-0 text-yellow-500" aria-hidden="true" />
        <span class="tracking-tight" :class="corDaNota(l.a.estrelas)" :aria-label="`nota ${l.a.estrelas} de 5`">{{ estrelasDe(l.a.estrelas) }}</span>
        <span class="rounded px-1 py-px" :class="situacaoAvaliacao(l.a).cls" :title="situacaoAvaliacao(l.a).dica">{{ situacaoAvaliacao(l.a).rotulo }}</span>
        <span v-if="!l.a.do_pedido && l.a.pedido" class="font-mono text-muted-foreground">{{ l.a.pedido }}</span>
        <span v-if="l.a.criado_em" class="ml-auto tabular-nums text-muted-foreground">{{ fmtDataHora(l.a.criado_em) }}</span>
      </button>

      <!-- as anteriores do mesmo comprador (recolhidas) -->
      <button
        v-else
        type="button"
        class="flex w-full items-center gap-1 rounded-md px-1 py-0.5 text-left text-xs text-muted-foreground hover:bg-muted"
        :aria-expanded="verAnteriores"
        @click="verAnteriores = !verAnteriores"
      >
        <ChevronDown v-if="verAnteriores" class="size-3.5" />
        <ChevronRight v-else class="size-3.5" />
        {{ grupos.anteriores.length === 1 ? '1 avaliação anterior deste comprador' : `${grupos.anteriores.length} avaliações anteriores deste comprador` }}
      </button>
    </template>
  </div>
  <div v-else-if="erro" class="text-xs text-muted-foreground">Não deu para carregar as avaliações agora.</div>
</template>
