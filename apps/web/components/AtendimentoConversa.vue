<script lang="ts">
// Por que a plataforma não deixa responder (`conversa.bloqueio_motivo`): o ML
// manda o código cru do `conversation_status.substatus` ("blocked_by_…") —
// bom para o suporte, ruim para quem atende. Valores vistos em produção em
// 01/10/2026 (cancelled_order, claim, mediation, conversation_initiated_by_
// seller_limited) + os da documentação de mensagens pós-venda do ML.
export const MOTIVOS_BLOQUEIO: Record<string, string> = {
  blocked_by_cancelled_order: 'o pedido foi cancelado — o Mercado Livre fecha a conversa',
  blocked_by_claim: 'há uma reclamação aberta — a conversa com o comprador passa pela reclamação',
  blocked_by_mediation: 'a reclamação está em mediação — quem conversa agora é o mediador do Mercado Livre',
  blocked_by_time: 'acabou o prazo para mandar mensagens neste pedido',
  blocked_by_buyer: 'o comprador bloqueou as mensagens',
  blocked_by_payment: 'o pagamento do pedido ainda não foi aprovado',
  blocked_by_fulfillment: 'pedido do Full — o Mercado Livre não deixa mandar mensagem por aqui',
  blocked_by_conversation_initiated_by_seller_limited: 'a loja já mandou as mensagens que pode sem o comprador responder — espere ele escrever',
  blocked: 'a plataforma bloqueou a conversa',
  deleted: 'a pergunta foi apagada no Mercado Livre',
}
export function bloqueioLegivel(motivo: string | null | undefined): string {
  const m = (motivo || '').trim()
  if (!m) return ''
  const frase = MOTIVOS_BLOQUEIO[m.toLowerCase()]
  if (frase) return frase
  if (/^blocked_by_[a-z_]+$/i.test(m)) return `a plataforma bloqueou a conversa (${m})`
  return m
}

// A mudança de etiqueta na linha do tempo da conversa (RF1, 01/10/2026):
// "Etiqueta mudou de Pós-venda para Reclamação" e, embaixo, o porquê que o
// backend gravou (o acontecimento, ou "Trocada à mão: …") e quem trocou.
// A primeira classificação não vira linha no histórico (não é mudança);
// sem `de`, a frase não inventa um "de".
export type MudancaDeEtiqueta = {
  de_rotulo: string | null
  para_rotulo: string
  motivo: string | null
  por_nome: string | null
}
export function frasesDaEtiqueta(h: MudancaDeEtiqueta): { titulo: string; detalhe: string } {
  const para = (h.para_rotulo || '').trim() || 'sem etiqueta'
  const de = (h.de_rotulo || '').trim()
  const titulo = de ? `Etiqueta mudou de ${de} para ${para}` : `Etiqueta: ${para}`
  const detalhe = [(h.motivo || '').trim(), h.por_nome ? `por ${h.por_nome}` : ''].filter(Boolean).join(' · ')
  return { titulo, detalhe }
}
</script>

<script setup lang="ts">
// Coluna do meio da Caixa (Atendimento, 25/09/2026): a conversa com o
// comprador e a caixa de resposta — e, à direita, o pedido (AtendimentoPedido,
// recolhível; em tela menor abre por cima, como gaveta).
//
// Regras que moldam esta tela:
// - Balões como no WhatsApp (mesmo desenho do histórico de Chamados): cliente à
//   esquerda, loja à direita, e em cada resposta da loja POR ONDE ela saiu —
//   Equipe (DaVinci), IA ou Fora do DaVinci (Duoke, central da loja). Sistema
//   no meio.
// - A sugestão da IA já vem escrita na caixa de resposta. Enquanto a pessoa
//   não mexe, sugestão nova substitui a velha; depois que ela editou, o texto
//   dela manda e a sugestão nova só aparece como "usar sugestão".
// - Quando a resposta partiu da sugestão, o envio leva o `rascunho_id` — é
//   assim que o backend sabe se a pessoa enviou igual, editou ou escreveu do
//   zero (o material do aprendizado). Apagou tudo e escreveu outra coisa =
//   do zero: o id não vai.
// - `envio.pode_enviar=false` (loja em Observar, envio desligado, conversa
//   bloqueada…) desabilita a caixa e diz o porquê em linguagem simples. A
//   sugestão continua visível e copiável — dá para colar no Duoke.
// - Envio "revisar" = a plataforma não confirmou; pode ter saído. O DaVinci
//   nunca reenvia sozinho: o balão pergunta "Saiu" / "Não saiu" e alguém
//   marca depois de olhar na plataforma.
// - Cara do Duoke (28/09/2026): cliente à ESQUERDA em branco, loja à DIREITA
//   em azul-claro, com o rótulo de por onde saiu em cima; cartão de pedido e
//   de produto preenchidos pela API da loja; foto do cliente em miniatura que
//   abre grande.
// - MODO OBSERVAÇÃO (`envio.modo_observacao`: loja em Observar ou envio
//   desligado): no lugar da caixa de envio, o painel "O que a IA responderia"
//   (copiar para o Duoke, 👍/👎 com correção). Onde a equipe já respondeu por
//   fora, logo abaixo da resposta real, o cartão recolhível "A IA teria
//   respondido" — é o "IA × equipe" do primeiro teste em produção.
// - Cliente (parte 2, P5): os selos do comprador (reclamação aberta, avaliou
//   mal, já pediu devolução, recorrente, primeira compra) no cabeçalho; o
//   cartão com os números e a linha do tempo fica no topo do painel da
//   direita, e o clique num item da linha do tempo volta para cá (rolar até
//   a mensagem, abrir a outra conversa, abrir o pedido em Logística).
// - Temu e AliExpress (30/09/2026): lidos pelo robô do Mac mini, nunca
//   respondidos por aqui. A conversa fica SEMPRE no painel de observação (sem
//   caixa de envio, seja qual for o `envio` da API) e o cabeçalho ganha
//   "Abrir no Seller Center" — a tela de chat da plataforma, onde a pessoa
//   responde.
// - Magalu (30/09/2026): respondida por API, com três caixas (Pergunta, Chat,
//   SAC) e o limite de cada uma. Toda resposta passa pela moderação da Magalu
//   (a caixa de envio avisa, e o aviso depois do envio diz que a Magalu
//   recebeu, não que o comprador já vê); o cabeçalho ganha "Abrir no portal"
//   — a tela da caixa no Portal do Seller.
// - O componente NÃO é remontado ao trocar de conversa. Tudo que termina
//   depois de um await (envio de até 90 s, sugestão, descarte, PATCH) guarda o
//   id da conversa de origem e só mexe na tela se ela ainda estiver aberta;
//   senão o resultado vai para o que fica guardado daquela conversa.
// - Item 3 do Comunicador (01/10/2026):
//   · o PAINEL do pedido (GET /conversas/{id}/painel: estoque, margem,
//     Observações do Bling, links, perfil do AdsPower) é buscado aqui, ao
//     abrir a conversa, e vai para o AtendimentoPedido; o cabeçalho ganha o
//     botão AdsPower (AtendimentoAdsPower);
//   · a caixa ganha a aba NOTA INTERNA (AtendimentoNota: amarela, só a equipe
//     vê, funciona até no modo observação) e o botão FOTO (uma imagem pelo
//     POST /conversas/{id}/foto; desligado, com o porquê, quando o envio não
//     pode sair);
//   · resposta que a plataforma recusou ganha "tentar de novo" (só com o
//     envio ligado), e o bloqueio do ML ("blocked_by_…") vira frase.
// - Itens 1 e 2 do Comunicador (01/10/2026):
//   · a ETIQUETA (status atual) no cabeçalho, logo depois do nome
//     (AtendimentoEtiqueta): clicar troca à mão (POST /conversas/{id}/etiqueta,
//     vale até o próximo acontecimento automático); a resposta volta para o
//     detalhe e para a linha da lista. Cada mudança de etiqueta entra na
//     linha do tempo das mensagens, na hora em que aconteceu;
//   · o CARTÃO DA RECLAMAÇÃO (AtendimentoReclamacao) entre as faixas e as
//     mensagens, só quando a conversa (ou o pedido dela) tem reclamação,
//     mediação ou devolução da plataforma — só leitura. Relido no "atualizar"
//     e junto com o painel (a cada 2 min).
import {
  Archive,
  ArrowLeft,
  Bot,
  Check,
  CheckCheck,
  ChevronDown,
  Copy,
  ExternalLink,
  Hash,
  ImagePlus,
  Loader2,
  Lock,
  MailX,
  MessageSquareText,
  PanelRightClose,
  PanelRightOpen,
  Pause,
  Play,
  RotateCcw,
  Send,
  ShieldCheck,
  Sparkles,
  StickyNote,
  ThumbsDown,
  ThumbsUp,
  Trash2,
  TriangleAlert,
  Undo2,
  UserMinus,
  UserPlus,
  X,
} from 'lucide-vue-next'
import { eNota } from '~/components/AtendimentoNota.vue'
import { etiquetaInfo } from '~/components/AtendimentoEtiqueta.vue'
import type { Painel } from '~/components/AtendimentoPedido.vue'
import type { ReclamacoesResposta } from '~/components/AtendimentoReclamacao.vue'
import { PopoverContent, PopoverPortal, PopoverRoot, PopoverTrigger } from 'reka-ui'
import { onKeyStroke, useMediaQuery } from '@vueuse/core'
import {
  AVISO_MODERACAO_MAGALU,
  CHAVE_CORRECOES,
  ERROS,
  MODERACAO_MAGALU_ENVIADA,
  canalLabel,
  SINAIS_CLIENTE,
  cartaoPedidoDe,
  cartaoProdutoDe,
  categoriaLabel,
  clienteDe,
  comoObjeto,
  copiar,
  duracao,
  envioSimulado,
  erroDaApi,
  erroEnvioLegivel,
  fmtData,
  fmtDataHora,
  fmtDiaHora,
  lacunasAbertas,
  limiteDe,
  linksAmazon,
  modoInfo,
  motivoLegivel,
  normalizarTexto,
  origemLabel,
  passaPelaModeracao,
  pedidoMktDe,
  plataformaInfo,
  portalMagaluDe,
  prazoDe,
  preencherLacunas,
  motivoSellerCenter,
  respondidaNoPortalMagalu,
  respondidaNoSellerCenter,
  respondidaNoSellerCentral,
  rotuloDia,
  sellerCenterDe,
  statusDoErro,
  tamanhoDoEnvio,
  usePollingVisivel,
  useRelogio,
  variasCaixas,
  type Anexo,
  type AvaliacaoIa,
  type CartaoPedido,
  type CartaoProduto,
  type ConversaDetalhe,
  type EventoCliente,
  type ConversaResumo,
  type CorrecaoEmCurso,
  type Detalhe,
  type EtiquetaHistorico,
  type EtiquetaTroca,
  type Flags,
  type Mensagem,
  type Modelo,
  type PedidoMkt,
  type Rascunho,
  type ResumoLoja,
  type SugestaoIa,
} from '~/components/AtendimentoPlataforma.vue'

const props = defineProps<{
  conversaId: string
  canEdit: boolean
  modelos: Modelo[]
  flags: Flags | null
  meuId: string | null
  // As lojas do /resumo — daqui sai a lista de contas Amazon para ligar uma
  // conversa que chegou sem conta.
  lojas?: ResumoLoja[]
  // A Caixa fica montada (v-show) enquanto a pessoa está em outra aba da
  // página — aí a conversa não precisa ficar se relendo.
  ativa?: boolean
}>()
const emit = defineEmits<{
  (e: 'mudou', c: ConversaResumo): void
  (e: 'voltar'): void
  (e: 'abrirAba', aba: string): void
  // A linha do tempo do cliente apontou outra conversa (a pergunta de
  // pré-venda do mesmo comprador): a página abre.
  (e: 'abrirConversa', id: string): void
}>()

const { api } = useApi()
const toasts = useToasts()
const agora = useRelogio()

const detalhe = ref<Detalhe | null>(null)
const carregando = ref(false)
const erro = ref<string | null>(null)
let geracao = 0

const conversa = computed(() => detalhe.value?.conversa ?? null)
const rascunho = computed(() => detalhe.value?.rascunho ?? null)

// ─── caixa de resposta ──────────────────────────────────────────────────────
const texto = ref('')
// O texto que a TELA pôs na caixa (sugestão da IA). Igual ao texto = a pessoa
// ainda não mexeu; aí sugestão nova pode entrar por cima.
const prefill = ref('')
// De qual sugestão a resposta partiu (vai como rascunho_id no envio).
const baseRascunhoId = ref<string | null>(null)
// A sugestão que a pessoa APAGOU da caixa (ou trocou por uma resposta
// pronta). Ela não volta sozinha no próximo tique: só uma sugestão NOVA
// (outro id) ou o clique em "usar sugestão" põem texto de volta. Sem isso,
// apagar para escrever outra coisa e ir conferir o pedido fazia a sugestão
// recusada reaparecer em 15 s.
const rejeitadaId = ref<string | null>(null)
const caixa = ref<HTMLTextAreaElement | null>(null)
// Rascunho de cada conversa enquanto a página está aberta: trocar de conversa
// no meio de uma resposta não perde o que foi escrito (fica só na memória).
type Guardado = { texto: string; prefill: string; base: string | null; rejeitada: string | null }
const guardados = new Map<string, Guardado>()

watch(texto, (t) => {
  // Apagou tudo: o que vier agora é escrito do zero, não edição da sugestão
  // — e a sugestão apagada fica marcada como recusada.
  if (!t.trim()) {
    if (baseRascunhoId.value) rejeitadaId.value = baseRascunhoId.value
    baseRascunhoId.value = null
  }
})

function aplicarRascunho(r: Rascunho | null, forcar = false) {
  if (!r || !r.texto) return
  // Modo observação: não há caixa de resposta (a sugestão fica no painel "O
  // que a IA responderia"). Pôr o texto numa caixa escondida fazia toda
  // resposta do Duoke parecer "respondeu enquanto você escrevia".
  if (observacao.value) return
  if (!forcar && (r.id === baseRascunhoId.value || r.id === rejeitadaId.value)) return
  // Conversa que não espera resposta (já respondida, "não precisa de
  // resposta"): a sugestão NÃO entra sozinha na caixa — num Enter ela sairia
  // como segunda resposta. Fica o botão "usar sugestão".
  if (!forcar && detalhe.value?.conversa.aguardando_resposta === false) return
  const intocado = !texto.value.trim() || texto.value === prefill.value
  if (intocado) {
    texto.value = r.texto
    prefill.value = r.texto
    baseRascunhoId.value = r.id
    if (rejeitadaId.value === r.id) rejeitadaId.value = null
  }
}
function usarSugestao() {
  const r = rascunho.value
  if (!r?.texto) return
  if (texto.value.trim() && texto.value !== prefill.value && !confirm('Trocar o que você escreveu pela sugestão da IA?')) return
  texto.value = r.texto
  prefill.value = r.texto
  baseRascunhoId.value = r.id
  rejeitadaId.value = null
  nextTick(() => caixa.value?.focus())
}
const usandoSugestao = computed(() => !!rascunho.value && baseRascunhoId.value === rascunho.value.id)

// ─── carregar ───────────────────────────────────────────────────────────────
const rolagem = ref<HTMLElement | null>(null)
function pertoDoFim() {
  const el = rolagem.value
  return !el || el.scrollHeight - el.scrollTop - el.clientHeight < 140
}
function rolarProFim() {
  nextTick(() => {
    const el = rolagem.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

async function carregar(id: string, silencioso = false) {
  const g = ++geracao
  if (!silencioso) {
    carregando.value = true
    erro.value = null
  }
  try {
    const d = await api<Detalhe>(`/api/atendimento/conversas/${encodeURIComponent(id)}`)
    if (g !== geracao || id !== props.conversaId) return
    const antes = detalhe.value
    const primeira = !antes || antes.conversa.id !== d.conversa.id
    const novas = !primeira && (d.mensagens?.length || 0) !== (antes?.mensagens.length || 0)
    // Alguém (outra pessoa, a IA, o Duoke) respondeu enquanto a pessoa
    // escrevia: avisa já, no tique — sem esperar o 409 do Enviar. Só quando
    // ela ESCREVEU (a sugestão intocada não é "você escrevia" — e sai da
    // caixa logo abaixo, porque virou substituída) e havia caixa de resposta
    // na tela (no modo observação não há: quem responde é o Duoke).
    if (!primeira && antes && !observacao.value && texto.value.trim() && texto.value !== prefill.value) avisarRespostaAlheia(antes, d)
    const estavaNoFim = pertoDoFim()
    detalhe.value = { ...d, mensagens: d.mensagens || [], contexto: d.contexto ?? null }
    erro.value = null
    // Envio que ficou sem resposta do servidor: agora a conversa diz se saiu.
    resolverIncerto(detalhe.value)
    // A sugestão que a pessoa estava usando sumiu (foi enviada, substituída
    // por resposta de fora, descartada): o envio não pode citar um id velho.
    if (baseRascunhoId.value && d.rascunho?.id !== baseRascunhoId.value) {
      if (texto.value === prefill.value) {
        texto.value = ''
        prefill.value = ''
      }
      baseRascunhoId.value = null
    }
    aplicarRascunho(d.rascunho)
    emit('mudou', d.conversa)
    if (primeira || (novas && estavaNoFim)) rolarProFim()
  } catch (e: any) {
    if (g !== geracao || id !== props.conversaId) return
    // No automático a falha não apaga a conversa da tela — só avisa embaixo.
    if (!silencioso || !detalhe.value) erro.value = erroDaApi(e, 'Não consegui abrir a conversa').texto
    else atualizacaoFalhou.value = true
  } finally {
    if (g === geracao) carregando.value = false
  }
}
const atualizacaoFalhou = ref(false)

function avisarRespostaAlheia(antes: Detalhe, d: Detalhe) {
  const vistas = new Set(antes.mensagens.map((m) => m.id))
  const minha = normalizarTexto(texto.value, d.conversa.plataforma)
  const alheias = (d.mensagens || []).filter((m) =>
    !vistas.has(m.id) && m.autor === 'loja' && m.status !== 'falhou'
    // o próprio envio (incerto/atrasado) desta pessoa não é "de outro"
    && normalizarTexto(m.texto || '', d.conversa.plataforma) !== minha)
  if (!alheias.length) return
  const ultima = alheias[alheias.length - 1]
  const quem = ultima.origem === 'davinci_ia'
    ? 'A IA'
    : respondidaNoSellerCentral(ultima, d.conversa.plataforma)
      ? 'Alguém pelo Seller Central'
      : respondidaNoSellerCenter(ultima, d.conversa.plataforma)
        ? 'Alguém pelo Seller Center'
        : ultima.origem === 'externo' ? 'Alguém fora do DaVinci' : (ultima.autor_nome || 'Outra pessoa')
  toasts.warning(`${quem} respondeu esta conversa enquanto você escrevia`, ['Confira a resposta antes de enviar a sua — o comprador pode receber duas.'])
}

function guardar(id: string) {
  // A sugestão recusada também fica guardada: voltar para a conversa com a
  // caixa vazia não pode trazê-la de volta.
  if (texto.value.trim() || rejeitadaId.value) {
    guardados.set(id, { texto: texto.value, prefill: prefill.value, base: baseRascunhoId.value, rejeitada: rejeitadaId.value })
  } else {
    guardados.delete(id)
  }
}


// Conversa aberta: relê a cada 15 s (parado com a aba escondida ou no meio
// de um envio).
usePollingVisivel(async () => {
  if (!props.conversaId || enviando.value || props.ativa === false) return
  atualizacaoFalhou.value = false
  await carregar(props.conversaId, true)
}, 15_000)

// ─── linha do tempo ─────────────────────────────────────────────────────────
// As mensagens e as MUDANÇAS DE ETIQUETA (`detalhe.etiqueta_historico`), pela
// hora; no empate, a mensagem antes (quase sempre foi ela que mudou o status).
type Linha =
  | { tipo: 'dia'; chave: string; texto: string }
  | { tipo: 'msg'; chave: string; m: Mensagem }
  | { tipo: 'etiqueta'; chave: string; h: EtiquetaHistorico }
function tsIso(iso: string | null | undefined) {
  const t = iso ? new Date(iso).getTime() : NaN
  return Number.isNaN(t) ? Number.POSITIVE_INFINITY : t
}
function ts(m: Mensagem) {
  return tsIso(m.enviada_em)
}
const linhas = computed<Linha[]>(() => {
  type Item = { t: number; iso: string | null; linha: Linha }
  const itens: Item[] = [
    ...(detalhe.value?.mensagens || []).map((m): Item => ({ t: ts(m), iso: m.enviada_em, linha: { tipo: 'msg', chave: m.id, m } })),
    ...(detalhe.value?.etiqueta_historico || []).map((h): Item => ({ t: tsIso(h.em), iso: h.em, linha: { tipo: 'etiqueta', chave: `etiqueta-${h.id}`, h } })),
  ]
  // `sort` é estável: no empate fica a ordem acima (mensagens primeiro).
  itens.sort((a, b) => (a.t === b.t ? 0 : a.t - b.t))
  const out: Linha[] = []
  let dia = ''
  for (const it of itens) {
    if (it.iso && Number.isFinite(it.t)) {
      const d = new Date(it.iso).toDateString()
      if (d !== dia) {
        out.push({ tipo: 'dia', chave: `dia-${d}`, texto: rotuloDia(it.iso, agora.value) })
        dia = d
      }
    }
    out.push(it.linha)
  }
  return out
})
// A bolinha da mudança de etiqueta: a cor da etiqueta nova (Pós-venda, cinza).
function pontoDaEtiqueta(h: EtiquetaHistorico) {
  return etiquetaInfo(h.para)?.ponto || 'bg-muted-foreground/50'
}

function lado(m: Mensagem): 'cliente' | 'loja' | 'sistema' | 'nota' | 'mediador' {
  // Nota interna primeiro: o autor dela é `equipe`, que cairia em "loja".
  if (eNota(m)) return 'nota'
  // O mediador da plataforma na reclamação (o "Com Meli"; autor `mediador`):
  // à esquerda, com o rótulo — não é o comprador nem a loja.
  if (m.autor === 'mediador') return 'mediador'
  if (m.autor === 'sistema' || m.origem === 'sistema') return 'sistema'
  return m.autor === 'cliente' ? 'cliente' : 'loja'
}
// Como o Duoke: cliente em branco à esquerda, loja em azul-claro à direita
// (qualquer origem — quem diferencia é o rótulo em cima do balão). Cartão
// sozinho do cliente ganha fundo cinza para o cartão branco aparecer.
function balaoCls(m: Mensagem) {
  const falhou = m.status === 'falhou' ? ' ring-1 ring-red-400/80' : ''
  if (lado(m) === 'cliente') {
    return (soCartao(m) ? 'rounded-tl-sm bg-muted/80' : 'rounded-tl-sm border border-border/70 bg-background') + falhou
  }
  if (lado(m) === 'mediador') return 'rounded-tl-sm border border-violet-300/70 bg-violet-50 dark:border-violet-800/60 dark:bg-violet-900/25'
  return 'rounded-tr-sm bg-sky-100 dark:bg-sky-900/45' + falhou
}
function autorCls(m: Mensagem) {
  if (m.origem === 'davinci_ia') return 'text-violet-700 dark:text-violet-300'
  if (m.origem === 'davinci_humano') return 'text-emerald-700 dark:text-emerald-300'
  return 'text-muted-foreground'
}
const ORIGEM_HINT: Record<string, string> = {
  davinci_humano: 'enviada por alguém da equipe pelo DaVinci',
  davinci_ia: 'enviada pela IA do DaVinci',
  externo: 'respondida fora do DaVinci (Duoke, central da loja, celular)',
}
function origemHint(m: Mensagem): string {
  if (respondidaNoSellerCentral(m, conversa.value?.plataforma)) {
    return 'respondida no Seller Central — a Amazon mandou a cópia por e-mail'
  }
  const sc = sellerCenterDe(conversa.value?.plataforma)
  if (sc && respondidaNoSellerCenter(m, conversa.value?.plataforma)) {
    return `respondida no ${sc.nome} — o robô do Mac mini leu de lá`
  }
  // O Duoke não lê a Magalu: por fora, é o portal (ou outro sistema da loja).
  if (respondidaNoPortalMagalu(m, conversa.value?.plataforma)) {
    return 'respondida fora do DaVinci — no Portal do Seller da Magalu (ou em outro sistema ligado à loja)'
  }
  return ORIGEM_HINT[m.origem] || ''
}
const STATUS_MSG: Record<string, { label: string; cls: string; hint: string }> = {
  enviando: { label: 'enviando…', cls: 'bg-amber-500/15 text-amber-700 dark:text-amber-300', hint: 'saindo para a plataforma agora' },
  revisar: {
    label: 'a conferir',
    cls: 'bg-amber-500/20 text-amber-800 dark:text-amber-300',
    hint: 'a plataforma não confirmou — pode ter saído. Confira na plataforma e marque se saiu; o DaVinci não tenta de novo sozinho',
  },
  falhou: { label: 'não saiu', cls: 'bg-red-500/15 text-red-700 dark:text-red-300', hint: 'a plataforma recusou — a mensagem NÃO chegou ao comprador' },
}
const TIPO_LABEL: Record<string, string> = {
  imagem: 'imagem', video: 'vídeo', produto: 'produto', pedido: 'cartão do pedido', arquivo: 'arquivo', outro: 'conteúdo',
}

// Links no texto viram clicáveis (igual Chamados, sem v-html: o texto vem da
// plataforma e é sempre renderizado como texto; só a URL vira <a>).
const RE_URL = /(https?:\/\/[^\s|]+)/g
function partesComLink(t: string | null): { t: 'txt' | 'url'; v: string }[] {
  const s = t || ''
  const out: { t: 'txt' | 'url'; v: string }[] = []
  let ultimo = 0
  for (const m of s.matchAll(RE_URL)) {
    const i = m.index ?? 0
    if (i > ultimo) out.push({ t: 'txt', v: s.slice(ultimo, i) })
    out.push({ t: 'url', v: m[0] })
    ultimo = i + m[0].length
  }
  if (ultimo < s.length) out.push({ t: 'txt', v: s.slice(ultimo) })
  return out
}

// Anexo: cada plataforma manda um formato. O cartão de pedido/produto que o
// sync preenche pela API da loja vira o cartão do Duoke; o resto, pega o que
// der (url/nome/tipo) e só aceita link http(s) — imagem vira miniatura que
// abre grande; o resto, link com o nome.
type Peca =
  | { chave: string; tipo: 'pedido'; cartao: CartaoPedido }
  | { chave: string; tipo: 'produto'; cartao: CartaoProduto }
  | { chave: string; tipo: 'imagem'; url: string; nome: string }
  | { chave: string; tipo: 'link'; url: string; nome: string }
  | { chave: string; tipo: 'nome'; nome: string }
function pecasDe(m: Mensagem): Peca[] {
  return (m.anexos || []).filter((a): a is Anexo => !!a && typeof a === 'object').map((a, i): Peca => {
    const chave = `${m.id}-${i}`
    const s = (k: string) => (typeof a[k] === 'string' ? (a[k] as string) : '')
    const tipo = (s('tipo') || s('type') || s('content_type')).toLowerCase()
    if (tipo === 'pedido') {
      const cartao = cartaoPedidoDe(a)
      if (cartao) return { chave, tipo: 'pedido', cartao }
    }
    if (tipo === 'produto') {
      const cartao = cartaoProdutoDe(a)
      if (cartao) return { chave, tipo: 'produto', cartao }
    }
    const bruta = s('url') || s('link') || s('src') || s('image_url') || s('thumb_url') || s('url_imagem')
    const url = /^https?:\/\//i.test(bruta) ? bruta : null
    // Sem nome, o tipo em português ("vídeo"), não o código cru da API ("video").
    const nome = s('nome') || s('name') || s('filename') || s('titulo') || TIPO_LABEL[tipo] || tipo || 'anexo'
    const imagem = !!url && (tipo.includes('imag') || tipo.startsWith('image') || /\.(png|jpe?g|webp|gif)(\?|$)/i.test(url))
    if (url && imagem) return { chave, tipo: 'imagem', url, nome }
    if (url) return { chave, tipo: 'link', url, nome }
    return { chave, tipo: 'nome', nome }
  })
}
// Uma vez por leitura da conversa, não a cada desenho do balão.
const pecas = computed(() => {
  const mapa = new Map<string, Peca[]>()
  for (const m of detalhe.value?.mensagens || []) mapa.set(m.id, pecasDe(m))
  return mapa
})
function pecasDaMsg(m: Mensagem): Peca[] {
  return pecas.value.get(m.id) ?? pecasDe(m)
}
function temCartao(m: Mensagem) {
  return pecasDaMsg(m).some((p) => p.tipo === 'pedido' || p.tipo === 'produto')
}
function soCartao(m: Mensagem) {
  return !m.texto && temCartao(m)
}

// Foto do cliente aberta grande (Esc ou clique fora fecha).
const imagemAberta = ref<{ url: string; nome: string } | null>(null)
onKeyStroke('Escape', () => { imagemAberta.value = null })

// ─── retrato do pedido, produtos e sugestões (spec Duoke 2.4) ───────────────
const pedidoMkt = computed(() => pedidoMktDe(detalhe.value))
const produtoAnuncio = computed(() => cartaoProdutoDe((detalhe.value?.produto ?? null) as Anexo | null))
// Os produtos que apareceram na conversa, para a aba Produto do painel.
const produtosConversa = computed<CartaoProduto[]>(() =>
  (detalhe.value?.mensagens || []).flatMap((m) => pecasDaMsg(m)).flatMap((p) => (p.tipo === 'produto' ? [p.cartao] : [])),
)
// O "atualizar" do painel devolve o retrato (e o cartão do anúncio) de agora.
function aoAtualizarPedido(r: { pedido_mkt: PedidoMkt | null; produto: CartaoProduto | null }) {
  const d = detalhe.value
  if (!d) return
  detalhe.value = { ...d, pedido_mkt: r.pedido_mkt ?? d.pedido_mkt ?? null, produto: r.produto ?? d.produto ?? null }
}

// ─── cliente (parte 2, P5) ──────────────────────────────────────────────────
const cliente = computed(() => clienteDe(detalhe.value))
// A mensagem que a linha do tempo mandou mostrar pisca por um instante — no
// meio de uma conversa longa, só rolar não diz qual era.
const destacadaId = ref<string | null>(null)
let tiraDestaque: ReturnType<typeof setTimeout> | null = null
onBeforeUnmount(() => { if (tiraDestaque) clearTimeout(tiraDestaque) })
function mostrarMensagem(id: string) {
  const el = rolagem.value?.querySelector<HTMLElement>(`[data-msg-id="${CSS.escape(id)}"]`)
  if (!el) return false
  let suave = true
  try {
    suave = !window.matchMedia('(prefers-reduced-motion: reduce)').matches
  } catch {
    // sem matchMedia — rola suave
  }
  el.scrollIntoView({ block: 'center', behavior: suave ? 'smooth' : 'auto' })
  destacadaId.value = id
  if (tiraDestaque) clearTimeout(tiraDestaque)
  tiraDestaque = setTimeout(() => { destacadaId.value = null }, 2500)
  return true
}
// Clique num item da linha do tempo que não é o pedido desta conversa (esse
// o painel resolve sozinho). O `ref` é o id da mensagem (desta conversa), o
// id de outra conversa (a pergunta de pré-venda) ou o nº de outro pedido.
function irPara(ev: EventoCliente) {
  const ref = (ev.ref || '').trim()
  const d = detalhe.value
  if (!ref || !d) return
  // Em tela menor o painel é gaveta por cima da conversa: sai da frente.
  if (!telaLarga.value) gaveta.value = false
  if (d.mensagens.some((m) => m.id === ref)) {
    void nextTick(() => mostrarMensagem(ref))
    return
  }
  if (ref === d.conversa.id) {
    rolarProFim()
    return
  }
  if (RE_UUID.test(ref) || ref.startsWith('ig:')) {
    emit('abrirConversa', ref)
    return
  }
  if (ev.tipo === 'pergunta' || ev.tipo === 'mensagem') {
    toasts.info('Essa conversa ainda não está no DaVinci', 'Ela aparece aqui quando a leitura da loja trouxer.')
    return
  }
  // Outro pedido do mesmo comprador: abre na Logística (outra aba), que
  // busca pelo nº da plataforma — é o mesmo link do painel "No DaVinci".
  const p = new URLSearchParams()
  if (['ml', 'shopee', 'amazon', 'tiktok'].includes(d.conversa.plataforma)) p.set('tab', d.conversa.plataforma)
  p.set('q', ref)
  window.open(`/logistica?${p.toString()}`, '_blank', 'noopener')
}

// Modo observação: o Duoke responde; aqui só se lê e se avalia a IA. A API
// diz (`envio.modo_observacao`); a antiga, que não diz, cai na mesma regra
// do backend — loja em Observar ou envio desligado no servidor.
// Temu/AliExpress: sempre — o robô só lê, a resposta é no Seller Center. Vem
// antes de tudo (até do `somente_leitura`): a caixa de envio nunca aparece
// nessas lojas, e o que a IA responderia continua à mão para copiar.
const sellerCenter = computed(() => sellerCenterDe(conversa.value?.plataforma))
// Magalu: a tela da caixa (pergunta, chat ou SAC) no Portal do Seller.
const portal = computed(() => portalMagaluDe(conversa.value?.plataforma, conversa.value?.canal))
const moderacao = computed(() => passaPelaModeracao(conversa.value?.plataforma))
const observacao = computed(() => {
  const d = detalhe.value
  if (!d) return false
  if (sellerCenterDe(d.conversa.plataforma)) return true
  if (d.conversa.somente_leitura) return false
  if (typeof d.envio.modo_observacao === 'boolean') return d.envio.modo_observacao
  return d.envio.modo === 'observar' || props.flags?.envio_ativo === false
})
const sugestoes = computed<SugestaoIa[]>(() => detalhe.value?.sugestoes || [])
function quando(iso: string | null | undefined) {
  const t = iso ? new Date(iso).getTime() : NaN
  return Number.isNaN(t) ? 0 : t
}
// Correção do 👎 em curso, por id de sugestão (o AtendimentoAvaliarIa grava a
// cada tecla). Mora aqui, na conversa, e não no botão: quando o Duoke
// responde, a sugestão sai do painel e vira o cartão "A IA teria respondido"
// (outro componente) — o texto digitado ia embora com o velho. Só na memória
// da página, como os `guardados`; salvar a avaliação apaga.
const correcoes = reactive(new Map<string, CorrecaoEmCurso>())
provide(CHAVE_CORRECOES, correcoes)
function corrigindo(id: string | null | undefined) {
  return !!id && !!correcoes.get(id)?.aberto
}
// A sugestão "da vez": a mais nova ainda sem resposta real (pendente ou
// bloqueada). API antiga sem `sugestoes`: o `rascunho` pendente de sempre.
const sugestaoDaVez = computed<SugestaoIa | null>(() => {
  const abertas = sugestoes.value
    .filter((x) => !x.resposta_real && (x.status === 'pendente' || x.status === 'bloqueado'))
    .slice()
    .sort((a, b) => quando(b.created_at) - quando(a.created_at))
  if (abertas[0]) return abertas[0]
  const r = rascunho.value
  if (!r) return null
  return {
    id: r.id,
    texto: r.texto,
    categoria: r.categoria,
    confianca: r.confianca,
    status: r.status || 'pendente',
    created_at: r.created_at,
    precisa_humano: r.precisa_humano,
    validador_erros: r.validador_erros,
    avaliacao: null,
    resposta_real: null,
  }
})
// A que está no painel. Com a correção do 👎 ABERTA, ela fica no painel até a
// pessoa salvar ou fechar a caixa — mesmo que o Duoke responda (vira
// substituída) ou chegue sugestão mais nova: trocar a sugestão debaixo de quem
// está digitando tirava a caixa da tela no meio da frase. Depois, ela desce
// para o cartão "A IA teria respondido" e a da vez entra.
const noPainelId = ref<string | null>(null)
const sugestaoAtual = computed<SugestaoIa | null>(() => {
  const vez = sugestaoDaVez.value
  const presa = noPainelId.value
  if (presa && presa !== vez?.id && corrigindo(presa)) {
    const x = sugestoes.value.find((y) => y.id === presa)
    if (x) return x
  }
  return vez
})
watch(() => sugestaoAtual.value?.id ?? null, (id) => { noPainelId.value = id }, { immediate: true })
// Presa no painel com outra (mais nova) esperando a vez.
const temSugestaoMaisNova = computed(() => {
  const vez = sugestaoDaVez.value
  return !!vez && !!sugestaoAtual.value && vez.id !== sugestaoAtual.value.id
})
// Descartada sem resposta real depois: é a sugestão para a última mensagem do
// comprador, e alguém a descartou. Aparece no fim da conversa (abaixo); o
// painel não pode dizer que a IA "não sugeriu nada".
const descartadaAberta = computed(() => sugestoes.value.some((x) => x.status === 'descartado' && !x.resposta_real))
const semSugestaoMotivo = computed(() => {
  const c = conversa.value
  if (!c) return null
  const iaDesligada = !!props.flags && !props.flags.ia_ativa
  if (descartadaAberta.value) {
    const depois = iaDesligada ? 'A IA está desligada no servidor — não sugere outra.' : c.ia_pausada ? 'A IA está pausada nesta conversa.' : ''
    return `A IA sugeriu uma resposta, mas ela foi descartada — está no fim da conversa ("Sugestão da IA descartada").${depois ? ` ${depois}` : ''}`
  }
  if (iaDesligada) return ERROS.ia_desligada
  if (c.ia_pausada) return 'A IA está pausada nesta conversa.'
  if (!c.aguardando_resposta) return 'Nada esperando resposta agora — a última mensagem é da loja (ou a conversa não precisa de resposta).'
  return null
})
// "A IA teria respondido" embaixo da resposta REAL: a `resposta_real` diz o
// id da mensagem (é a primeira da loja depois do gatilho); sem id que esteja
// na tela, acha pela hora ou pelo texto. A que não achar par vai para o fim
// da conversa — não some. Sem resposta real, a que não está em destaque (no
// painel da observação ou na caixa de envio) também vai para o fim: a
// descartada, a barrada com o envio ligado, a pendente que uma mais nova
// passou — o "IA × equipe" conta com todas.
const emDestaqueId = computed(() => (observacao.value ? sugestaoAtual.value?.id : rascunho.value?.id) ?? null)
const comparacoes = computed(() => {
  const mapa = new Map<string, SugestaoIa[]>()
  const soltas: SugestaoIa[] = []
  const daLoja = (detalhe.value?.mensagens || []).filter((m) => lado(m) === 'loja')
  const destaque = emDestaqueId.value
  const ordenadas = sugestoes.value.slice().sort((a, b) => quando(a.created_at) - quando(b.created_at))
  for (const x of ordenadas) {
    // A do painel não aparece duas vezes (presa lá com a correção aberta,
    // mesmo já tendo resposta real).
    if (x.id === destaque) continue
    const rr = x.resposta_real
    if (!rr) {
      soltas.push(x)
      continue
    }
    const t = quando(rr.enviada_em)
    let alvo = rr.mensagem_id ? daLoja.find((m) => m.id === rr.mensagem_id) : undefined
    if (!alvo && t) alvo = daLoja.find((m) => Math.abs(quando(m.enviada_em) - t) < 2000)
    const texto = (rr.texto || '').trim()
    if (!alvo && texto) alvo = daLoja.find((m) => (m.texto || '').trim() === texto)
    if (alvo) mapa.set(alvo.id, [...(mapa.get(alvo.id) || []), x])
    else soltas.push(x)
  }
  return { mapa, soltas }
})
function aoAvaliar(id: string, a: AvaliacaoIa) {
  const d = detalhe.value
  if (!d?.sugestoes) return
  d.sugestoes = d.sugestoes.map((x) => (x.id === id ? { ...x, avaliacao: a } : x))
}

// ─── cabeçalho / faixas ─────────────────────────────────────────────────────
const prazo = computed(() => (conversa.value ? prazoDe(conversa.value, agora.value) : null))
const modo = computed(() => modoInfo(detalhe.value?.envio.modo))
const janela = computed(() => {
  const c = conversa.value
  if (!c?.pode_enviar_ate) return null
  const fim = new Date(c.pode_enviar_ate).getTime()
  if (Number.isNaN(fim)) return null
  const falta = fim - agora.value
  if (falta <= 0) return { cls: 'border-red-500/40 bg-red-500/10 text-red-700 dark:text-red-300', texto: `A janela para responder acabou em ${fmtDataHora(c.pode_enviar_ate)}.` }
  if (falta < 48 * 3600 * 1000) return { cls: 'border-amber-500/40 bg-amber-500/10 text-amber-900 dark:text-amber-200', texto: `Dá para responder até ${fmtDataHora(c.pode_enviar_ate)} (faltam ${duracao(falta / 60000)}).` }
  return null
})

// ML pergunta: cada pergunta é uma conversa (uma resposta responde aquela
// pergunta). As outras do mesmo comprador no mesmo anúncio aparecem no painel
// da direita (AtendimentoPedido), só como contexto — a tela não deduz mais
// "perguntas abertas" de `nao_lidas`, que só o sync atualiza e mandava
// responder de novo uma pergunta que já tinha resposta.

// Amazon que chegou por e-mail sem dar para saber a conta: sem loja não há
// modo nem por onde responder — alguém diz qual é a conta.
const semConta = computed(() => {
  const c = conversa.value
  return !!c && c.plataforma === 'amazon' && !c.integration_id && !c.somente_leitura
})
const contasAmazon = computed(() =>
  (props.lojas || [])
    .filter((l): l is ResumoLoja & { integration_id: string } => l.plataforma === 'amazon' && !!l.integration_id)
    .slice()
    .sort((a, b) => (a.conta || '').localeCompare(b.conta || '', 'pt-BR')),
)

// Por que não dá para responder, em linguagem simples.
const bloqueioEnvio = computed(() => {
  const d = detalhe.value
  if (!d) return ''
  // Temu/AliExpress: a caixa nem aparece (observação); isto trava o envio
  // também por qualquer outro caminho (Ctrl+Enter, "enviar mesmo assim").
  if (sellerCenterDe(d.conversa.plataforma)) return motivoSellerCenter(d.conversa.plataforma)
  if (d.conversa.somente_leitura) {
    return d.conversa.plataforma === 'instagram'
      ? 'Direct do Instagram: aqui é só leitura — responda pela caixa de entrada do Instagram.'
      : ERROS.somente_leitura
  }
  if (!props.canEdit) return 'Você pode ler, mas não responder: falta a permissão de editar o Atendimento.'
  if (d.envio.pode_enviar) return ''
  const codigo = d.envio.codigo || ''
  // Bloqueio: a frase do backend traz o porquê do caso (janela fechou,
  // comprador bloqueou…). Nos outros, a frase simples da tela.
  if (codigo === 'conversa_bloqueada') {
    // O motivo do ML vem cru ("blocked_by_cancelled_order"): vira frase.
    const porque = bloqueioLegivel(d.envio.motivo)
    return porque && porque !== d.envio.motivo ? `${ERROS.conversa_bloqueada.replace(/\.$/, '')}: ${porque}.` : (d.envio.motivo || ERROS.conversa_bloqueada)
  }
  // Envio desligado no servidor vem antes: escolher a conta não resolveria.
  if (codigo !== 'envio_desligado' && semConta.value) return 'Esta conversa da Amazon ainda não está ligada a uma conta. Escolha a conta acima para poder responder.'
  if (codigo && ERROS[codigo]) return ERROS[codigo]
  if (d.envio.motivo) return motivoLegivel(d.envio.motivo)
  if (d.conversa.situacao === 'bloqueada') return ERROS.conversa_bloqueada
  if (d.envio.modo === 'observar') return ERROS.canal_em_observacao
  if (props.flags && !props.flags.envio_ativo) return ERROS.envio_desligado
  return 'O envio não está liberado para esta conversa.'
})
const podeDigitar = computed(() => !!detalhe.value && !bloqueioEnvio.value)
// O da API manda; sem ele (API antiga, canal novo), a cópia da tela — a
// Magalu chat/SAC tem o limite da documentação, a pergunta não tem nenhum.
const limite = computed(() => detalhe.value?.envio.limite_caracteres || limiteDe(conversa.value?.plataforma ?? null, conversa.value?.canal ?? null) || 0)
// Conta como o backend conta (texto normalizado, em code points) — o
// limite é do texto que SAI, não do que está na caixa.
const tamanho = computed(() => tamanhoDoEnvio(texto.value, conversa.value?.plataforma))
const acimaDoLimite = computed(() => !!limite.value && tamanho.value > limite.value)
const lacunas = computed(() => lacunasAbertas(texto.value))
const podeEnviar = computed(() =>
  podeDigitar.value && !!tamanho.value && !acimaDoLimite.value && !lacunas.value.length && !enviando.value && !incertos.has(props.conversaId))

function titulo(c: ConversaDetalhe) {
  return c.comprador_nome || (c.pedido_marketplace ? `Pedido ${c.pedido_marketplace}` : 'Comprador')
}

async function copiarTexto(t: string | null | undefined, rotulo: string) {
  if (!t) return
  if (await copiar(t)) toasts.success(`${rotulo} copiado`)
  else window.prompt(rotulo, t)
}

// ─── enviar ─────────────────────────────────────────────────────────────────
// Tudo aqui é POR CONVERSA: a pessoa pode apertar Enviar na A e ir para a B
// enquanto a plataforma demora (até 90 s). O resultado da A não pode limpar a
// caixa da B, perguntar "a sugestão estava boa?" na B nem pôr o erro da A
// embaixo da B — e o Enviar da B não fica travado pelo envio da A.
// `confirmar` = a recusa foi `conversa_mudou`: a pessoa pode, depois de ver
// o que mudou, enviar mesmo assim.
type ErroEnvio = { texto: string; motivos: string[]; confirmar?: boolean }
type Sugestao = { id: string; usada: boolean }
const enviandoIds = reactive(new Set<string>())
const enviando = computed(() => enviandoIds.has(props.conversaId))
const erroEnvio = ref<ErroEnvio | null>(null)
const errosGuardados = new Map<string, ErroEnvio>()
const avaliacoesGuardadas = new Map<string, Sugestao>()
// Envio sem resposta do servidor (a rede caiu, o proxy devolveu 502/504): a
// linha pode ter sido gravada e a mensagem pode ter saído. Até a conversa
// recarregada mostrar o que houve, o Enviar dela fica travado — senão a
// pessoa aperta de novo e o comprador recebe a mesma resposta duas vezes.
type Incerto = { antes: Set<string>; texto: string; sugestao: Sugestao | null; nome: string }
const incertos = shallowReactive(new Map<string, Incerto>())
// Recusas que mudam o estado da conversa: relê para mostrar o de agora.
const RELER_APOS_RECUSA = new Set(['envio_em_andamento', 'envio_repetido', 'envio_a_conferir', 'conversa_mudou', 'canal_em_observacao', 'envio_desligado', 'conversa_bloqueada', 'sem_integracao'])

// A última mensagem que a pessoa tinha na tela ao apertar Enviar. O backend
// recusa (`conversa_mudou`) se a loja respondeu depois dela — outra pessoa,
// a IA ou o Duoke —, para o comprador não receber duas respostas. Só id de
// mensagem do banco (uuid); outro formato o backend nem aceitaria.
const RE_UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
function ultimaVista(d: Detalhe): string | null {
  const ms = [...d.mensagens].sort((a, b) => ts(a) - ts(b))
  for (let i = ms.length - 1; i >= 0; i--) if (RE_UUID.test(ms[i].id)) return ms[i].id
  return null
}

function aberta(id: string) {
  return props.conversaId === id
}
function mostrarErroEnvio(id: string, er: ErroEnvio | null) {
  if (aberta(id)) erroEnvio.value = er
  else if (er) errosGuardados.set(id, er)
  else errosGuardados.delete(id)
}
// A resposta saiu: caixa vazia. A sugestão que estava na tela já foi usada
// (ou trocada pela resposta da pessoa) — ela não pode voltar sozinha se uma
// leitura atrasada ainda a trouxer como pendente.
function limparCaixa(id: string, sugestaoId: string | null) {
  if (aberta(id)) {
    texto.value = ''
    prefill.value = ''
    baseRascunhoId.value = null
    rejeitadaId.value = sugestaoId
    guardados.delete(id)
  } else if (sugestaoId) {
    guardados.set(id, { texto: '', prefill: '', base: null, rejeitada: sugestaoId })
  } else {
    guardados.delete(id)
  }
}
function pedirAvaliacao(id: string, s: Sugestao) {
  if (aberta(id)) {
    limparAval()
    aval.rascunhoId = s.id
    aval.usada = s.usada
  } else {
    avaliacoesGuardadas.set(id, s)
  }
}

// O que fazer com a mensagem que o envio devolveu (ou que a conversa
// recarregada mostrou, depois de um envio sem resposta).
function concluirEnvio(id: string, m: Mensagem | null | undefined, sugestao: Sugestao | null, nome: string, plataforma: string) {
  const onde = aberta(id) ? '' : ` (${nome})`
  if (m?.status === 'falhou') {
    // A plataforma recusou: NADA chegou ao comprador. A caixa fica como a
    // pessoa deixou — o texto editado e a sugestão de origem. Limpar aqui
    // fazia o próximo tique pôr de volta a sugestão ORIGINAL (a caixa vazia
    // conta como intocada), e a pessoa reenviava sem a correção dela.
    const motivo = erroEnvioLegivel(m.erro)
    mostrarErroEnvio(id, { texto: 'A plataforma recusou — a resposta NÃO chegou ao comprador. O texto continua na caixa.', motivos: [motivo] })
    toasts.error(`A plataforma recusou a resposta${onde}`, motivo)
    return
  }
  limparCaixa(id, sugestao?.id ?? null)
  mostrarErroEnvio(id, null)
  if (sugestao) pedirAvaliacao(id, sugestao)
  // Pelo que o envio gravou: com a Amazon na exceção do simulador, a
  // resposta dela CHEGOU ao comprador — dizer o contrário aqui é o erro caro.
  const sim = envioSimulado(m, props.flags, plataforma) ? ' (simulador: não chegou ao comprador)' : ''
  // Magalu: o "aceito" da API é a entrada na moderação, não a publicação.
  const detalheEnvio = sim || (passaPelaModeracao(plataforma) ? MODERACAO_MAGALU_ENVIADA : '')
  if (!m || m.status === 'enviada') toasts.success(`Resposta enviada${onde}`, detalheEnvio || undefined)
  else if (m.status === 'revisar') toasts.warning(`Não deu para confirmar o envio${onde}`, 'Pode ter saído. Confira na plataforma e marque no balão se saiu — o DaVinci não tenta de novo sozinho.')
  else toasts.info(`Resposta saindo${onde}`, 'Acompanhe o status no balão.')
}

// A conversa recarregada depois de um envio sem resposta: a linha nossa nova
// com o mesmo texto diz o que houve; sem ela, o pedido não chegou a gravar
// nada (a trava do backend grava a linha ANTES de falar com a plataforma).
function resolverIncerto(d: Detalhe) {
  const id = d.conversa.id
  const inc = incertos.get(id)
  if (!inc) return
  incertos.delete(id)
  const alvo = normalizarTexto(inc.texto, d.conversa.plataforma)
  const m = d.mensagens.find((x) =>
    !inc.antes.has(x.id) && x.autor === 'loja' && x.origem === 'davinci_humano'
    && ((x.texto || '').trim() === alvo || (x.texto || '').trim() === inc.texto))
  if (m) {
    concluirEnvio(id, m, inc.sugestao, inc.nome, d.conversa.plataforma)
    return
  }
  mostrarErroEnvio(id, { texto: 'A resposta não saiu', motivos: ['O envio não chegou ao servidor (ela não aparece na conversa). O texto continua na caixa — pode enviar de novo.'] })
}

// `opcoes` (e não um booleano solto) porque o @click passa o evento como
// primeiro argumento — um evento não pode virar "confirmar".
async function enviar(opcoes?: { confirmar?: boolean }) {
  const d = detalhe.value
  if (!d || !podeEnviar.value) {
    if (d && lacunas.value.length) erroEnvio.value = { texto: `Troque ${lacunas.value.join(', ')} pelo dado antes de enviar.`, motivos: [] }
    return
  }
  const id = d.conversa.id
  const nome = titulo(d.conversa)
  const body: { texto: string; rascunho_id?: string; ultima_vista_id?: string; confirmar?: boolean } = { texto: texto.value.trim() }
  if (baseRascunhoId.value && d.rascunho?.id === baseRascunhoId.value) body.rascunho_id = baseRascunhoId.value
  const vista = ultimaVista(d)
  if (vista) body.ultima_vista_id = vista
  if (opcoes?.confirmar === true) body.confirmar = true
  // Depois do envio a pergunta "a sugestão estava boa?" é sobre a sugestão
  // que estava na tela — usada (foi como rascunho_id) ou não.
  const sugestao: Sugestao | null = d.rascunho?.texto ? { id: d.rascunho.id, usada: !!body.rascunho_id } : null
  const antes = new Set(d.mensagens.map((x) => x.id))
  enviandoIds.add(id)
  erroEnvio.value = null
  try {
    const r = await api<{ mensagem: Mensagem }>(`/api/atendimento/conversas/${encodeURIComponent(id)}/responder`, { method: 'POST', body })
    const m = r?.mensagem
    if (m && detalhe.value?.conversa.id === id && !detalhe.value.mensagens.some((x) => x.id === m.id)) {
      detalhe.value.mensagens = [...detalhe.value.mensagens, m]
      rolarProFim()
    }
    concluirEnvio(id, m, sugestao, nome, d.conversa.plataforma)
    // Só relê a conversa que continua aberta: reler a A com a B na tela
    // descartava o carregamento da B (a geração é uma só).
    if (aberta(id)) await carregar(id, true)
  } catch (e: any) {
    const code = e?.data?.detail?.code
    const st = statusDoErro(e)
    if (!code && (!st || st >= 500)) {
      // Sem resposta que diga o que houve: pode ter saído.
      incertos.set(id, { antes, texto: body.texto, sugestao, nome })
      mostrarErroEnvio(id, {
        texto: 'Não deu para confirmar o envio — pode ter saído.',
        motivos: ['Não envie de novo ainda: assim que a conversa atualizar, a tela mostra se a resposta saiu (o Enviar fica travado até lá).'],
      })
      if (aberta(id)) await carregar(id, true)
      else toasts.warning(`Não deu para confirmar o envio (${nome})`, 'Abra a conversa para ver se a resposta saiu antes de mandar de novo.')
      return
    }
    const er: ErroEnvio = { ...erroDaApi(e, 'Não consegui enviar'), confirmar: code === 'conversa_mudou' }
    mostrarErroEnvio(id, er)
    if (!aberta(id)) toasts.error(`Não consegui enviar (${nome})`, [er.texto, ...er.motivos])
    else if (RELER_APOS_RECUSA.has(code)) await carregar(id, true)
  } finally {
    enviandoIds.delete(id)
  }
}
function aoTeclar(e: KeyboardEvent) {
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
    e.preventDefault()
    void enviar()
  }
}

// ─── ações da conversa (PATCH) ──────────────────────────────────────────────
// Por conversa, como o envio: a ação da A não trava os botões da B.
const acoes = reactive(new Map<string, string>())
const acao = computed(() => acoes.get(props.conversaId) ?? null)
async function patch(body: Record<string, unknown>, chave: string, ok?: string) {
  const d = detalhe.value
  if (!d || acao.value) return
  const id = d.conversa.id
  acoes.set(id, chave)
  try {
    const r = await api<unknown>(`/api/atendimento/conversas/${encodeURIComponent(id)}`, { method: 'PATCH', body })
    const c = comoObjeto<ConversaDetalhe>(r, 'conversa')
    if (c && detalhe.value?.conversa.id === c.id) {
      detalhe.value.conversa = { ...detalhe.value.conversa, ...c }
      emit('mudou', detalhe.value.conversa)
    } else if (c) {
      // A pessoa já está em outra conversa: a linha da lista acompanha mesmo assim.
      emit('mudou', c)
    }
    if (ok) toasts.success(aberta(id) ? ok : `${ok} (${titulo(d.conversa)})`)
    if (aberta(id)) await carregar(id, true)
  } catch (e: any) {
    const er = erroDaApi(e, 'Não consegui salvar')
    toasts.error(er.texto, er.motivos)
  } finally {
    acoes.delete(id)
  }
}

const atribuidaAMim = computed(() => !!props.meuId && conversa.value?.atribuido_a === props.meuId)
function atribuir() {
  const c = conversa.value
  if (!c || !props.meuId) return
  if (atribuidaAMim.value) return patch({ atribuido_a: null }, 'atribuir', 'Conversa solta — volta para a fila de todos')
  if (c.atribuido_a && !confirm(`Esta conversa está com ${c.atribuido_a_nome || 'outra pessoa'}. Assumir mesmo assim?`)) return
  return patch({ atribuido_a: props.meuId }, 'atribuir', 'Conversa atribuída a você')
}
function pausarIa() {
  const c = conversa.value
  if (!c) return
  return patch({ ia_pausada: !c.ia_pausada }, 'ia', c.ia_pausada ? 'IA volta a sugerir nesta conversa' : 'IA pausada nesta conversa')
}
// Fechar sempre confirma: o botão fica ao lado de ícones de painel, e fechar
// tira a conversa da fila de todos.
function fecharOuReabrir() {
  const c = conversa.value
  if (!c) return
  if (c.situacao === 'fechada') return patch({ situacao: 'aberta' }, 'fechar', 'Conversa reaberta')
  const aviso = c.aguardando_resposta
    ? 'O comprador ainda está esperando resposta. Fechar mesmo assim?'
    : 'Fechar esta conversa?'
  if (!confirm(`${aviso}\n\nA conversa sai da fila; se o comprador escrever de novo, ela volta. Dá para reabrir em "Fechadas".`)) return
  return patch({ situacao: 'fechada' }, 'fechar', 'Conversa fechada')
}
// Amazon: mensagem que não pede resposta ("obrigado", aviso automático) conta
// como atrasada se ninguém clicar "Não é necessária resposta" no Seller
// Central. Por e-mail esse botão não existe; o que existe é o link "Solucionar
// o caso" do rodapé (`amazon.semResposta`). Com ele, o "Não precisa de
// resposta" abre um balão com o link de verdade: o clique abre a Amazon numa
// aba nova E faz o PATCH daqui. Tem de ser um <a target=_blank> clicado pela
// pessoa — um window.open depois do confirm() cai no bloqueador de pop-up se
// ela demorar para confirmar. Sem o link, só tira da fila e lembra de marcar lá.
const amazon = computed(() => linksAmazon(conversa.value))
const balaoNaoPrecisa = ref(false)
function naoPrecisa() {
  const c = conversa.value
  if (!c) return
  const marcar = !c.sem_resposta_necessaria
  if (marcar && !confirm('Marcar como "não precisa de resposta"?\n\nA conversa sai da fila de aguardando. Na Amazon, clique também em "Não é necessária resposta" no Seller Central — senão a Amazon conta como atrasada.')) return
  return patch({ sem_resposta_necessaria: marcar }, 'nao_precisa', marcar ? 'Marcada: não precisa de resposta' : 'Voltou para a fila')
}
// O <a> do balão segue o caminho normal do navegador (abre a Amazon); aqui só
// a parte do DaVinci. Se o PATCH falhar, a aba da Amazon já abriu: o erro
// aparece e o "só marcar aqui" tenta de novo sem abrir outra aba.
function naoPrecisaComLink() {
  balaoNaoPrecisa.value = false
  return patch({ sem_resposta_necessaria: true }, 'nao_precisa', 'Marcada: não precisa de resposta — confira na aba da Amazon')
}
// O balão já foi a confirmação: sem segundo confirm().
function naoPrecisaSoAqui() {
  balaoNaoPrecisa.value = false
  return patch({ sem_resposta_necessaria: true }, 'nao_precisa', 'Marcada: não precisa de resposta — marque também no Seller Central')
}

// Amazon sem conta: a pessoa diz de qual conta é (as integrações Amazon do
// /resumo). A partir daí a conversa entra no modo daquela loja.
const contaEscolhida = ref('')
async function escolherConta() {
  const iid = contaEscolhida.value
  const loja = contasAmazon.value.find((l) => l.integration_id === iid)
  if (!iid || !loja) return
  if (!confirm(`Esta conversa é da conta Amazon "${loja.conta || 'sem nome'}"?\n\nAs respostas passam a sair por essa conta, no modo dela (Lojas e modo).`)) {
    contaEscolhida.value = ''
    return
  }
  await patch({ integration_id: iid }, 'conta', `Conversa ligada à conta ${loja.conta || 'Amazon'}`)
  contaEscolhida.value = ''
}

// ─── resposta sem confirmação: saiu ou não saiu? ────────────────────────────
// A plataforma não confirmou (status `revisar`): só quem olha na plataforma
// sabe. "Saiu" tira da fila de conferência; "Não saiu" libera responder de
// novo — é o único jeito de uma resposta "a conferir" virar reenvio, sempre
// por decisão de uma pessoa.
const conferindo = ref<string | null>(null)
async function conferir(m: Mensagem, saiu: boolean) {
  const d = detalhe.value
  if (!d || conferindo.value) return
  if (!saiu && !confirm('Marcar que esta resposta NÃO chegou ao comprador?\n\nConfira na plataforma antes: se ela saiu e alguém mandar de novo, o comprador recebe duas vezes.')) return
  const id = d.conversa.id
  conferindo.value = m.id
  try {
    const r = await api<unknown>(`/api/atendimento/mensagens/${encodeURIComponent(m.id)}/conferir`, { method: 'POST', body: { saiu } })
    const nova = comoObjeto<Mensagem>(r, 'mensagem')
    if (nova && detalhe.value?.conversa.id === id) {
      detalhe.value.mensagens = detalhe.value.mensagens.map((x) => (x.id === nova.id ? { ...x, ...nova } : x))
    }
    // A resposta traz a conversa (sem o "a conferir"): a linha da lista e o
    // contador acompanham mesmo se a pessoa já trocou de conversa.
    const c = comoObjeto<ConversaDetalhe>(r, 'conversa')
    if (c) emit('mudou', c)
    toasts.success(saiu ? 'Marcada: a resposta chegou ao comprador' : 'Marcada: a resposta não saiu', saiu ? undefined : 'Dá para responder de novo.')
    if (aberta(id)) await carregar(id, true)
  } catch (e: any) {
    const er = erroDaApi(e, 'Não consegui marcar')
    toasts.error(er.texto, er.motivos)
    if (aberta(id)) await carregar(id, true)
  } finally {
    conferindo.value = null
  }
}

// ─── sugestão da IA: gerar, descartar, avaliar ──────────────────────────────
const gerandoIds = reactive(new Set<string>())
const gerando = computed(() => gerandoIds.has(props.conversaId))
// Fechada/bloqueada: o backend não gera (e o botão só levaria a um "não
// sugeriu nada" sem explicação).
const podeSugerir = computed(() => {
  const c = conversa.value
  return !!c && props.canEdit && !c.somente_leitura && !c.ia_pausada && c.situacao !== 'fechada' && c.situacao !== 'bloqueada'
    && props.flags?.ia_ativa !== false && !rascunho.value
})
// Por que não veio sugestão, quando a API diz (`motivo`). Sem motivo, a frase
// não culpa a conversa: pode ser falha do provedor ou configuração.
const SEM_SUGESTAO: Record<string, string> = {
  sem_chave: 'A IA não está configurada no servidor (falta a chave do provedor) — avise o admin.',
  provedor_falhou: 'O provedor da IA não respondeu agora — tente de novo em instantes.',
  conversa_fechada: 'Conversa fechada: a IA não sugere aqui.',
  conversa_bloqueada: 'A plataforma não deixa mais responder esta conversa.',
  ia_pausada: 'A IA está pausada nesta conversa.',
  sem_mensagem_do_cliente: 'Não há mensagem do comprador para responder.',
}
async function sugerir() {
  const d = detalhe.value
  if (!d || gerando.value) return
  const id = d.conversa.id
  gerandoIds.add(id)
  try {
    const r = await api<{ rascunho: Rascunho | null; motivo?: string | null }>(`/api/atendimento/conversas/${encodeURIComponent(id)}/rascunho`, { method: 'POST' })
    const onde = aberta(id) ? '' : ` (${titulo(d.conversa)})`
    if (!r?.rascunho) {
      const mot = r?.motivo || ''
      toasts.info(`A IA não sugeriu nada${onde}`, SEM_SUGESTAO[mot] || motivoLegivel(mot) || 'Pode ter sido falha passageira do provedor da IA — tente de novo em instantes. Se repetir, responda você.')
    } else if (!r.rascunho.texto || (r.rascunho.status && r.rascunho.status !== 'pendente')) {
      // Bloqueada: sem texto, ou com texto que uma regra SÓ da IA barrou
      // (prazo, valor, frete grátis inventado). Esse texto fica só para
      // auditoria — na caixa, sairia num clique. O porquê vai no aviso.
      toasts.warning(`A IA não chegou a uma resposta que possa sair${onde}`, [r.rascunho.motivo || '', ...(r.rascunho.validador_erros || [])].filter(Boolean))
    } else if (aberta(id) && detalhe.value?.conversa.id === id) {
      detalhe.value.rascunho = r.rascunho
      aplicarRascunho(r.rascunho, true)
    }
    // Fora da conversa, a sugestão entra na caixa quando a pessoa voltar
    // (o carregamento aplica se a caixa estiver intocada).
    if (aberta(id)) await carregar(id, true)
  } catch (e: any) {
    const er = erroDaApi(e, 'Não consegui pedir a sugestão')
    toasts.error(er.texto, er.motivos)
  } finally {
    gerandoIds.delete(id)
  }
}

const MOTIVOS_DESCARTE = ['informação errada', 'não respondeu o que o cliente perguntou', 'tom inadequado', 'assunto para pessoa', 'cliente já foi atendido']
// Os mesmos limites do backend (DescartarIn / AvaliacaoIn): o campo não deixa
// passar, em vez de o 422 voltar depois.
const MAX_MOTIVO = 2000
const MAX_CORRECAO = 4000
// `salvando` = id da sugestão sendo descartada.
const descarte = reactive({ aberto: false, motivo: '', salvando: null as string | null })
async function confirmarDescarte() {
  const r = rascunho.value
  const d = detalhe.value
  if (!r || !d || !descarte.motivo.trim() || descarte.salvando) return
  const id = d.conversa.id
  descarte.salvando = r.id
  try {
    await api(`/api/atendimento/rascunhos/${encodeURIComponent(r.id)}/descartar`, { method: 'POST', body: { motivo: descarte.motivo.trim() } })
    if (aberta(id)) {
      if (baseRascunhoId.value === r.id) {
        if (texto.value === prefill.value) {
          texto.value = ''
          prefill.value = ''
        }
        baseRascunhoId.value = null
      }
      if (detalhe.value?.rascunho?.id === r.id) detalhe.value.rascunho = null
      descarte.aberto = false
      descarte.motivo = ''
    } else {
      // Trocou de conversa no meio: acerta o que ficou guardado daquela.
      const g = guardados.get(id)
      if (g?.base === r.id) {
        if (g.texto === g.prefill) guardados.delete(id)
        else guardados.set(id, { ...g, base: null })
      }
    }
    toasts.success('Sugestão descartada', 'O motivo fica guardado para a IA aprender.')
    if (aberta(id)) await carregar(id, true)
  } catch (e: any) {
    const er = erroDaApi(e, 'Não consegui descartar')
    toasts.error(er.texto, er.motivos)
  } finally {
    descarte.salvando = null
  }
}

// ✓/✗ na sugestão DEPOIS do fato (mesmo gesto da IA de Chamado): com o envio
// ligado, a pergunta aparece logo depois do envio: usou a sugestão → "estava
// boa?"; escreveu outra → "o que ela errou?". O ✗ pede o que era o certo — é
// o que ensina. (No modo observação a nota vai pelo painel "O que a IA
// responderia", na sugestão ainda pendente; o backend guarda como `observou`
// e promove a ação quando a sugestão sai da caixa.)
const aval = reactive({
  rascunhoId: null as string | null,
  usada: false,
  nota: null as 'ok' | 'erro' | null,
  abrindo: false,
  correcao: '',
  salvando: false,
})
function limparAval() {
  aval.rascunhoId = null
  aval.usada = false
  aval.nota = null
  aval.abrindo = false
  aval.correcao = ''
}
watch(() => rascunho.value?.id, () => {
  descarte.aberto = false
})
async function avaliar(nota: 'ok' | 'erro') {
  const id = aval.rascunhoId
  if (!id) return
  if (nota === 'erro' && !aval.correcao.trim()) {
    aval.abrindo = true
    return
  }
  aval.salvando = true
  try {
    await api(`/api/atendimento/rascunhos/${encodeURIComponent(id)}/avaliacao`, {
      method: 'POST',
      body: nota === 'ok' ? { nota } : { nota, correcao: aval.correcao.trim() },
    })
    // Trocou de conversa enquanto salvava: a caixinha na tela já é de outra.
    if (aval.rascunhoId === id) {
      aval.nota = nota
      aval.abrindo = false
    }
  } catch (e: any) {
    const er = erroDaApi(e, 'Não consegui salvar a avaliação')
    toasts.error(er.texto, er.motivos)
  } finally {
    aval.salvando = false
  }
}

// ─── respostas prontas ──────────────────────────────────────────────────────
const menuModelos = ref(false)
const buscaModelo = ref('')
const modelosDaConversa = computed(() => {
  const c = conversa.value
  if (!c) return []
  const q = buscaModelo.value.trim().toLowerCase()
  return props.modelos
    .filter((m) => m.ativo && (!m.plataforma || m.plataforma === c.plataforma) && (!m.canal || m.canal === c.canal))
    .filter((m) => !q || m.titulo.toLowerCase().includes(q) || m.texto.toLowerCase().includes(q))
    .slice()
    .sort((a, b) => (a.ordem ?? 0) - (b.ordem ?? 0) || a.titulo.localeCompare(b.titulo, 'pt-BR'))
})
// Primeiro nome para {comprador} — só quando parece nome de gente; apelido de
// plataforma ("joao_123") fica como lacuna para a pessoa trocar.
function primeiroNome(n: string | null | undefined) {
  const p = (n || '').trim().split(/\s+/)[0] || ''
  if (!p || /[\d_@.]/.test(p)) return ''
  return p.charAt(0).toUpperCase() + p.slice(1).toLowerCase()
}
const dadosLacunas = computed<Record<string, string | null | undefined>>(() => {
  const c = conversa.value
  const ctx = detalhe.value?.contexto
  // Data de envio: a postagem da logística; sem ela, o "Em andamento" do Bling
  // (a mesma ordem de `ia.valores_das_lacunas` no backend).
  const envio = ctx?.logistica?.data_envio || ctx?.pedido?.enviado_em
  return {
    numero_pedido: c?.pedido_marketplace || ctx?.pedido?.numeroloja || '',
    rastreio: ctx?.logistica?.rastreio,
    transportadora: ctx?.logistica?.transportadora,
    previsao_entrega: ctx?.logistica?.previsao ? fmtData(ctx.logistica.previsao) : '',
    data_envio: envio ? fmtData(envio) : '',
    nf_numero: ctx?.nota_fiscal?.numero,
    comprador: primeiroNome(c?.comprador_nome),
  }
})
function inserirModelo(m: Modelo) {
  const t = preencherLacunas(m.texto, dadosLacunas.value)
  const el = caixa.value
  if (!texto.value.trim() || texto.value === prefill.value) {
    // Caixa vazia ou só com a sugestão intocada: a resposta pronta ocupa tudo
    // (a sugestão continua a um clique em "usar sugestão", mas não volta
    // sozinha por cima se a pessoa apagar a resposta pronta).
    if (baseRascunhoId.value) rejeitadaId.value = baseRascunhoId.value
    texto.value = t
    prefill.value = ''
    baseRascunhoId.value = null
    nextTick(() => {
      caixa.value?.focus()
      caixa.value?.setSelectionRange(t.length, t.length)
    })
  } else {
    const ini = el?.selectionStart ?? texto.value.length
    const fim = el?.selectionEnd ?? ini
    texto.value = texto.value.slice(0, ini) + t + texto.value.slice(fim)
    nextTick(() => {
      caixa.value?.focus()
      caixa.value?.setSelectionRange(ini + t.length, ini + t.length)
    })
  }
  menuModelos.value = false
  buscaModelo.value = ''
}

// ─── etiqueta: troca à mão (item 2) ─────────────────────────────────────────
// O AtendimentoEtiqueta faz o POST e devolve a conversa e a linha do tempo
// novas: entram no detalhe (se a conversa ainda for esta) e na linha da lista
// (a página relê as contagens do menu Filtrar).
function aoTrocarEtiqueta(r: EtiquetaTroca) {
  const c = r?.conversa
  if (!c) return
  const d = detalhe.value
  if (d && d.conversa.id === c.id) {
    detalhe.value = {
      ...d,
      conversa: { ...d.conversa, ...c },
      etiqueta_historico: r.etiqueta_historico ?? d.etiqueta_historico,
    }
    emit('mudou', detalhe.value.conversa)
  } else {
    emit('mudou', c)
  }
  const nova = etiquetaInfo(c.etiqueta)
  toasts.success(nova ? `Etiqueta: ${nova.label}` : 'Etiqueta trocada', 'Vale até o próximo acontecimento automático (reclamação, devolução, Bling).')
}

// ─── reclamação da plataforma (item 1) ──────────────────────────────────────
// O cartão (AtendimentoReclamacao) busca sozinho o GET /conversas/{id}/reclamacoes
// e avisa quantas há (`carregado`); a faixa só aparece quando há alguma.
const reclamacaoRef = ref<{ carregar: () => Promise<void> } | null>(null)
const reclamacoesQtd = ref(0)
function aoCarregarReclamacoes(r: ReclamacoesResposta) {
  reclamacoesQtd.value = r?.itens?.length || 0
}
const temCartaoReclamacao = computed(() => {
  const c = conversa.value
  return !!c && !c.id.startsWith('ig:') && !c.somente_leitura
})

// ─── painel do pedido: estoque, margem, observações, links, AdsPower ────────
// GET /conversas/{id}/painel (item 3, 01/10/2026). Separado do detalhe: o
// Bling (Observações ao vivo) pode demorar, e a conversa não espera por ele.
// Relê ao abrir a conversa, no "atualizar" do cabeçalho e a cada 2 min.
const painelDados = ref<Painel | null>(null)
const painelCarregando = ref(false)
const painelErro = ref<string | null>(null)
let geracaoPainel = 0
async function carregarPainel(id: string, atualizar = false) {
  if (!id || id.startsWith('ig:')) {
    painelDados.value = null
    return
  }
  const g = ++geracaoPainel
  painelCarregando.value = true
  try {
    const p = await api<Painel>(`/api/atendimento/conversas/${encodeURIComponent(id)}/painel${atualizar ? '?atualizar=1' : ''}`)
    if (g !== geracaoPainel || id !== props.conversaId) return
    painelDados.value = p
    painelErro.value = null
  } catch (e: any) {
    if (g !== geracaoPainel || id !== props.conversaId) return
    // 404 sem código = a rota ainda não está no servidor: o painel some quieto.
    const semRota = statusDoErro(e) === 404 && !e?.data?.detail?.code
    painelErro.value = semRota ? null : erroDaApi(e, 'Não consegui ler estoque, margem e observações agora').texto
  } finally {
    if (g === geracaoPainel) painelCarregando.value = false
  }
}
usePollingVisivel(async () => {
  if (!props.conversaId || props.ativa === false) return
  // As reclamações mudam no ritmo do cron (10 min): relidas junto com o painel.
  void reclamacaoRef.value?.carregar()
  await carregarPainel(props.conversaId)
}, 120_000)
function atualizarTudo() {
  void carregar(props.conversaId)
  void carregarPainel(props.conversaId, true)
  void reclamacaoRef.value?.carregar()
}

// ─── caixa: Responder × Nota interna ────────────────────────────────────────
// A nota interna funciona SEMPRE (até no modo observação): ela não sai para
// ninguém. A escolha volta para "Responder" ao trocar de conversa.
const modoCaixa = ref<'responder' | 'nota'>('responder')
function aoCriarNota(m: Mensagem) {
  const d = detalhe.value
  if (d && !d.mensagens.some((x) => x.id === m.id)) {
    d.mensagens = [...d.mensagens, m]
    rolarProFim()
  }
}

// ─── foto na resposta (Shopee, TikTok, pós-venda do ML) ────────────────────
// Uma imagem por vez, pelo POST /conversas/{id}/foto (caminho único de saída
// no backend: as mesmas travas do texto). A foto só sobe para a plataforma no
// envio; escolher e desistir não manda nada a lugar nenhum. No ML ela vai
// junto de um texto (obrigatório lá); na Shopee e no TikTok, sozinha.
const envioFoto = computed(() => painelDados.value?.envio_foto ?? null)
const TIPOS_FOTO = ['image/jpeg', 'image/png']
const MAX_FOTO = 10 * 1024 * 1024
const motivoSemFoto = computed(() => {
  const d = detalhe.value
  if (!d) return ''
  if (!props.canEdit) return 'Falta a permissão de editar o Atendimento.'
  if (d.conversa.somente_leitura || sellerCenterDe(d.conversa.plataforma)) return bloqueioEnvio.value || 'Esta conversa é só de leitura aqui.'
  const ef = envioFoto.value
  if (!ef) return painelCarregando.value ? 'Conferindo se a foto pode sair…' : 'Não consegui conferir se a foto pode sair agora.'
  if (ef.pode) return ''
  if (ef.codigo && ERROS[ef.codigo]) return ERROS[ef.codigo]
  return ef.motivo ? bloqueioLegivel(motivoLegivel(ef.motivo)) : 'A foto não pode sair por aqui agora.'
})
const fotoInput = ref<HTMLInputElement | null>(null)
const foto = ref<{ arquivo: File; previa: string } | null>(null)
const legendaFoto = ref('')
const enviandoFoto = ref(false)
function descartarFoto() {
  if (foto.value) URL.revokeObjectURL(foto.value.previa)
  foto.value = null
  legendaFoto.value = ''
}
onBeforeUnmount(descartarFoto)
function escolherFoto() {
  if (motivoSemFoto.value) return
  fotoInput.value?.click()
}
function aoEscolherFoto(ev: Event) {
  const alvo = ev.target as HTMLInputElement
  const f = alvo.files?.[0]
  alvo.value = ''
  if (!f) return
  const max = envioFoto.value?.max_bytes || MAX_FOTO
  if (!TIPOS_FOTO.includes(f.type)) {
    toasts.error('Só dá para mandar foto JPG ou PNG.')
    return
  }
  if (f.size > max) {
    toasts.error(`A foto passa de ${Math.round(max / (1024 * 1024))} MB — escolha uma menor.`)
    return
  }
  descartarFoto()
  foto.value = { arquivo: f, previa: URL.createObjectURL(f) }
}
async function enviarFoto(opcoes?: { confirmar?: boolean }) {
  const d = detalhe.value
  const f = foto.value
  if (!d || !f || enviandoFoto.value || motivoSemFoto.value) return
  const legenda = legendaFoto.value.trim()
  if (envioFoto.value?.legenda_obrigatoria && !legenda) {
    toasts.error('No Mercado Livre a foto vai junto de um texto', 'Escreva uma frase para acompanhar a foto.')
    return
  }
  const id = d.conversa.id
  const fd = new FormData()
  fd.append('arquivo', f.arquivo, f.arquivo.name)
  if (legenda && envioFoto.value?.legenda_permitida) fd.append('legenda', legenda)
  const vista = ultimaVista(d)
  if (vista) fd.append('ultima_vista_id', vista)
  if (opcoes?.confirmar === true) fd.append('confirmar', 'true')
  enviandoFoto.value = true
  enviandoIds.add(id)
  try {
    const r = await api<{ mensagem: Mensagem }>(`/api/atendimento/conversas/${encodeURIComponent(id)}/foto`, { method: 'POST', body: fd })
    const m = r?.mensagem
    if (m && detalhe.value?.conversa.id === id && !detalhe.value.mensagens.some((x) => x.id === m.id)) {
      detalhe.value.mensagens = [...detalhe.value.mensagens, m]
      rolarProFim()
    }
    if (m?.status === 'falhou') {
      toasts.error('A plataforma recusou a foto — ela NÃO chegou ao comprador', erroEnvioLegivel(m.erro))
    } else {
      if (aberta(id)) descartarFoto()
      if (m?.status === 'revisar') toasts.warning('Não deu para confirmar o envio da foto', 'Pode ter saído. Confira na plataforma e marque no balão se saiu.')
      else toasts.success('Foto enviada', envioSimulado(m, props.flags, d.conversa.plataforma) ? 'simulador: não chegou ao comprador' : undefined)
    }
    if (aberta(id)) await carregar(id, true)
  } catch (e: any) {
    const code = e?.data?.detail?.code
    const st = statusDoErro(e)
    if (!code && (!st || st >= 500)) {
      toasts.warning('Não deu para confirmar o envio da foto — pode ter saído', 'Espere a conversa atualizar antes de mandar de novo.')
    } else {
      const er = erroDaApi(e, 'Não consegui mandar a foto')
      if (code === 'conversa_mudou' && aberta(id) && confirm(`${er.texto}\n\n${er.motivos.join('\n')}\n\nMandar a foto mesmo assim?`)) {
        enviandoFoto.value = false
        enviandoIds.delete(id)
        await enviarFoto({ confirmar: true })
        return
      }
      toasts.error(er.texto, er.motivos)
    }
    if (aberta(id)) await carregar(id, true)
  } finally {
    enviandoFoto.value = false
    enviandoIds.delete(id)
  }
}

// ─── "tentar de novo" na resposta que a plataforma recusou ──────────────────
// Só texto, só com o envio ligado (a mesma caixa que enviaria agora), e só na
// última tentativa: se depois dela saiu outra resposta da loja, não aparece.
// Foto recusada: a pessoa anexa de novo (a imagem não fica guardada).
const tentandoId = ref<string | null>(null)
function podeTentarDeNovo(m: Mensagem): boolean {
  const d = detalhe.value
  if (!d || !props.canEdit || observacao.value || !d.envio.pode_enviar) return false
  if (m.status !== 'falhou' || lado(m) !== 'loja' || !m.texto || m.tipo === 'imagem') return false
  if (m.origem !== 'davinci_humano' && m.origem !== 'davinci_ia') return false
  if (enviando.value || incertos.has(d.conversa.id)) return false
  const t = ts(m)
  return !d.mensagens.some((x) => x.id !== m.id && lado(x) === 'loja' && x.status !== 'falhou' && ts(x) >= t)
}
async function tentarDeNovo(m: Mensagem) {
  const d = detalhe.value
  if (!d || !m.texto || tentandoId.value || !podeTentarDeNovo(m)) return
  if (!confirm('Mandar de novo esta resposta?\n\nA plataforma recusou da primeira vez — ela NÃO chegou ao comprador.')) return
  const id = d.conversa.id
  const body: Record<string, unknown> = { texto: m.texto }
  const vista = ultimaVista(d)
  if (vista) body.ultima_vista_id = vista
  tentandoId.value = m.id
  enviandoIds.add(id)
  try {
    const r = await api<{ mensagem: Mensagem }>(`/api/atendimento/conversas/${encodeURIComponent(id)}/responder`, { method: 'POST', body })
    const nova = r?.mensagem
    if (nova?.status === 'falhou') toasts.error('A plataforma recusou de novo', erroEnvioLegivel(nova.erro))
    else if (nova?.status === 'revisar') toasts.warning('Não deu para confirmar o envio', 'Pode ter saído. Confira na plataforma e marque no balão se saiu.')
    else toasts.success('Resposta enviada')
  } catch (e: any) {
    const code = e?.data?.detail?.code
    const st = statusDoErro(e)
    if (!code && (!st || st >= 500)) toasts.warning('Não deu para confirmar o envio — pode ter saído', 'Espere a conversa atualizar antes de tentar de novo.')
    else {
      const er = erroDaApi(e, 'Não consegui mandar de novo')
      toasts.error(er.texto, er.motivos)
    }
  } finally {
    enviandoIds.delete(id)
    tentandoId.value = null
  }
  if (aberta(id)) await carregar(id, true)
}

// ─── painel do pedido ───────────────────────────────────────────────────────
// Tela larga (≥ 1400 px): coluna fixa, recolhível (lembrado no navegador).
// Tela menor: abre por cima, como gaveta.
// 1400 px: com a barra de lojas e a lista ao lado, abaixo disso a conversa
// ficava estreita demais com o painel fixo.
const telaLarga = useMediaQuery('(min-width: 1400px)')
const RECOLHIDO_KEY = 'davinci.atendimento.pedidoRecolhido'
const recolhido = ref(false)
const gaveta = ref(false)
onMounted(() => {
  try {
    recolhido.value = localStorage.getItem(RECOLHIDO_KEY) === '1'
  } catch {
    // sem localStorage — começa aberto
  }
})
function alternarPedido() {
  if (telaLarga.value) {
    recolhido.value = !recolhido.value
    try {
      localStorage.setItem(RECOLHIDO_KEY, recolhido.value ? '1' : '0')
    } catch {
      // só não lembra
    }
  } else {
    gaveta.value = !gaveta.value
  }
}
const pedidoVisivel = computed(() => (telaLarga.value ? !recolhido.value : gaveta.value))

// Troca de conversa: guarda o que estava escrito na anterior, limpa o estado
// da tela e abre a nova. Fica no fim do setup porque roda na hora (immediate)
// e mexe em estado declarado lá embaixo.
watch(() => props.conversaId, (novo, velho) => {
  if (velho) {
    guardar(velho)
    // O aviso de envio e a pergunta "a sugestão estava boa?" ficam com a
    // conversa em que apareceram — voltam quando ela for aberta de novo.
    if (erroEnvio.value) errosGuardados.set(velho, erroEnvio.value)
    if (aval.rascunhoId && !aval.nota) avaliacoesGuardadas.set(velho, { id: aval.rascunhoId, usada: aval.usada })
  }
  detalhe.value = null
  erro.value = null
  erroEnvio.value = errosGuardados.get(novo) ?? null
  errosGuardados.delete(novo)
  atualizacaoFalhou.value = false
  descarte.aberto = false
  descarte.motivo = ''
  menuModelos.value = false
  balaoNaoPrecisa.value = false
  gaveta.value = false
  contaEscolhida.value = ''
  imagemAberta.value = null
  destacadaId.value = null
  // Item 3: o painel e a foto são DESTA conversa; a nota em escrita fica
  // guardada no AtendimentoNota (por conversa).
  painelDados.value = null
  painelErro.value = null
  // O cartão da reclamação relê sozinho (watch do conversaId dele).
  reclamacoesQtd.value = 0
  modoCaixa.value = 'responder'
  descartarFoto()
  limparAval()
  const av = avaliacoesGuardadas.get(novo)
  if (av) {
    aval.rascunhoId = av.id
    aval.usada = av.usada
    avaliacoesGuardadas.delete(novo)
  }
  const g = guardados.get(novo)
  texto.value = g?.texto || ''
  prefill.value = g?.prefill || ''
  baseRascunhoId.value = g?.base || null
  rejeitadaId.value = g?.rejeitada || null
  // Só no navegador: no servidor a resposta chegaria depois de a página já
  // ter sido desenhada (e o navegador busca de novo de qualquer jeito).
  if (import.meta.client) {
    void carregar(novo)
    void carregarPainel(novo)
  }
}, { immediate: true })
</script>

<template>
  <div class="relative flex min-h-0 min-w-0 flex-1">
    <!-- conversa -->
    <div class="flex min-h-0 min-w-0 flex-1 flex-col">
      <div v-if="erro && !detalhe" class="m-3 space-y-2 rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-600 dark:text-red-400">
        <div>{{ erro }}</div>
        <div class="flex gap-3 text-xs">
          <button type="button" class="underline" @click="carregar(conversaId)">tentar de novo</button>
          <button type="button" class="underline lg:hidden" @click="emit('voltar')">voltar para a lista</button>
        </div>
      </div>

      <div v-else-if="!detalhe" class="flex flex-1 items-center justify-center gap-2 text-sm text-muted-foreground">
        <Loader2 class="size-4 animate-spin" /> abrindo a conversa…
      </div>

      <template v-else-if="conversa">
        <!-- cabeçalho: como o Duoke — quem mandou, a loja com o ícone da plataforma e o nº do pedido -->
        <div class="shrink-0 border-b px-3 py-2">
          <!-- Quebra em duas linhas quando não cabe (tela estreita): os botões
               descem, o nome do comprador nunca some. -->
          <div class="flex flex-wrap items-center gap-x-2.5 gap-y-1.5">
            <button type="button" class="-ml-1 rounded p-1 hover:bg-muted lg:hidden" title="voltar para a lista" aria-label="voltar para a lista" @click="emit('voltar')">
              <ArrowLeft class="size-4" />
            </button>
            <AtendimentoAvatar :nome="conversa.comprador_nome || conversa.pedido_marketplace" :foto="conversa.comprador_avatar" :tamanho="36" />
            <div class="min-w-[12rem] flex-1">
              <div class="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                <h2 class="min-w-0 truncate text-[15px] font-semibold leading-6">{{ titulo(conversa) }}</h2>
                <!-- Etiqueta = status atual (item 2): clicar troca à mão. O Instagram
                     (só leitura) não tem etiqueta. -->
                <AtendimentoEtiqueta
                  v-if="!conversa.somente_leitura"
                  :etiqueta="conversa.etiqueta"
                  :secundarias="conversa.etiquetas_secundarias"
                  :manual="conversa.etiqueta_manual"
                  :desde="conversa.etiqueta_desde"
                  :editavel="canEdit"
                  :conversa-id="conversa.id"
                  :historico="detalhe.etiqueta_historico"
                  @trocada="aoTrocarEtiqueta"
                />
                <span class="hidden h-4 w-px bg-border sm:block" aria-hidden="true" />
                <span class="inline-flex min-w-0 items-center gap-1 text-[13px]" :title="`${plataformaInfo(conversa.plataforma).nome} · ${conversa.conta || ''}`">
                  <AtendimentoIconePlataforma :plataforma="conversa.plataforma" :tamanho="15" />
                  <span class="truncate">{{ conversa.conta || plataformaInfo(conversa.plataforma).nome }}</span>
                  <span v-if="variasCaixas(conversa.plataforma) || conversa.plataforma === 'amazon'" class="rounded bg-muted px-1 text-[10px] text-muted-foreground">{{ canalLabel(conversa.canal) }}</span>
                </span>
                <button
                  v-if="conversa.pedido_marketplace"
                  type="button"
                  class="inline-flex items-center gap-0.5 rounded px-1 font-mono text-xs text-muted-foreground hover:bg-muted"
                  title="copiar nº do pedido"
                  @click="copiarTexto(conversa.pedido_marketplace, 'Nº do pedido')"
                >
                  <Hash class="size-3" />{{ conversa.pedido_marketplace }}<Copy class="ml-0.5 size-3 opacity-60" />
                </button>
              </div>
              <div class="mt-0.5 flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
                <!-- quem é o comprador para a loja: o alerta vem primeiro (muda o tom da resposta) -->
                <span
                  v-for="sn in cliente?.sinais || []"
                  :key="sn"
                  class="inline-flex items-center gap-0.5 rounded px-1.5 py-px text-[10px] font-medium"
                  :class="SINAIS_CLIENTE[sn].cls"
                  :title="SINAIS_CLIENTE[sn].hint"
                >
                  <TriangleAlert v-if="SINAIS_CLIENTE[sn].alerta" class="size-3" aria-hidden="true" />{{ SINAIS_CLIENTE[sn].label }}
                </span>
                <!-- Sem loja (Amazon sem conta) não há modo: nada de "Observar" inventado. -->
                <span v-if="!conversa.somente_leitura && detalhe.envio.modo" class="rounded px-1.5 py-px text-[10px] font-medium" :class="modo.cls" :title="`modo da loja: ${modo.hint}`">{{ modo.label }}</span>
                <span v-if="prazo" class="rounded px-1.5 py-px font-medium" :class="prazo.cls" :title="prazo.titulo">{{ prazo.texto }}</span>
                <!-- "Não precisa" não é "respondida": ninguém respondeu, alguém decidiu que não precisava. -->
                <span v-else-if="conversa.sem_resposta_necessaria" class="inline-flex items-center gap-0.5 rounded bg-muted px-1.5 py-px"><MailX class="size-3.5" /> não precisa de resposta</span>
                <span v-else-if="conversa.situacao === 'respondida'" class="inline-flex items-center gap-0.5 text-emerald-700 dark:text-emerald-300"><CheckCheck class="size-3.5" /> respondida</span>
                <span v-if="conversa.atribuido_a" class="rounded bg-sky-500/15 px-1.5 py-px text-sky-700 dark:text-sky-300">com {{ atribuidaAMim ? 'você' : (conversa.atribuido_a_nome || 'alguém') }}</span>
                <span v-if="conversa.ia_pausada" class="inline-flex items-center gap-1 rounded bg-muted px-1.5 py-px"><Bot class="size-3" /> IA pausada</span>
                <span v-if="conversa.anuncio_titulo" class="min-w-0 max-w-full truncate" :title="conversa.anuncio_titulo">Anúncio: {{ conversa.anuncio_titulo }}</span>
              </div>
            </div>
            <div class="ml-auto flex max-w-full flex-wrap items-center justify-end gap-1">
              <template v-if="!conversa.somente_leitura && canEdit">
                <Button
                  size="sm"
                  variant="outline"
                  class="h-7 px-2 text-xs"
                  :disabled="!!acao || !meuId"
                  :title="atribuidaAMim ? 'soltar: a conversa volta para a fila de todos' : conversa.atribuido_a ? `está com ${conversa.atribuido_a_nome || 'outra pessoa'} — assumir` : 'atribuir a mim'"
                  :aria-label="atribuidaAMim ? 'soltar a conversa' : 'atribuir a mim'"
                  @click="atribuir"
                >
                  <Loader2 v-if="acao === 'atribuir'" class="size-3.5 animate-spin" />
                  <UserMinus v-else-if="atribuidaAMim" class="size-3.5" />
                  <UserPlus v-else class="size-3.5" />
                  <span class="ml-1 hidden 2xl:inline">{{ atribuidaAMim ? 'Soltar' : 'Atribuir a mim' }}</span>
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  class="h-7 px-2 text-xs"
                  :disabled="!!acao"
                  :title="conversa.ia_pausada ? 'a IA está pausada nesta conversa — voltar a sugerir' : 'pausar a IA nesta conversa (ela para de sugerir e de enviar aqui)'"
                  :aria-label="conversa.ia_pausada ? 'retomar a IA nesta conversa' : 'pausar a IA nesta conversa'"
                  @click="pausarIa"
                >
                  <Loader2 v-if="acao === 'ia'" class="size-3.5 animate-spin" />
                  <Play v-else-if="conversa.ia_pausada" class="size-3.5" />
                  <Pause v-else class="size-3.5" />
                  <span class="ml-1 hidden 2xl:inline">{{ conversa.ia_pausada ? 'Retomar IA' : 'Pausar IA' }}</span>
                </Button>
                <!-- Amazon com o link "Solucionar o caso": o balão explica, e o
                     link de verdade abre a Amazon e marca aqui (naoPrecisaComLink). -->
                <PopoverRoot v-if="conversa.plataforma === 'amazon' && amazon.semResposta && !conversa.sem_resposta_necessaria" v-model:open="balaoNaoPrecisa">
                  <PopoverTrigger as-child>
                    <Button
                      size="sm"
                      variant="outline"
                      class="h-7 px-2 text-xs"
                      :disabled="!!acao"
                      title="não precisa de resposta — marca aqui e marca o caso como resolvido na Amazon"
                      aria-label="não precisa de resposta"
                    >
                      <Loader2 v-if="acao === 'nao_precisa'" class="size-3.5 animate-spin" />
                      <MailX v-else class="size-3.5" />
                      <span class="ml-1 hidden 2xl:inline">Não precisa de resposta</span>
                    </Button>
                  </PopoverTrigger>
                  <PopoverPortal>
                    <PopoverContent
                      side="bottom"
                      align="end"
                      :side-offset="4"
                      :collision-padding="8"
                      class="z-[70] w-[320px] max-w-[calc(100vw-16px)] rounded-md border bg-background p-3 text-xs shadow-lg"
                    >
                      <p class="text-sm font-medium">Não precisa de resposta?</p>
                      <p class="mt-1 text-muted-foreground">
                        A conversa sai da fila de aguardando. O link abre a Amazon numa aba nova e marca o caso como resolvido na Amazon
                        ("Não é necessária resposta") — sem isso a Amazon conta a mensagem como atrasada.
                      </p>
                      <div class="mt-2.5 flex flex-wrap items-center justify-end gap-1.5">
                        <button type="button" class="rounded px-1.5 py-1 text-muted-foreground underline hover:text-foreground" @click="naoPrecisaSoAqui">só marcar aqui</button>
                        <Button as="a" size="sm" class="h-7 px-2 text-xs" :href="amazon.semResposta" target="_blank" rel="noopener noreferrer" @click="naoPrecisaComLink">
                          <ExternalLink class="mr-1 size-3.5" /> Marcar e abrir na Amazon
                        </Button>
                      </div>
                    </PopoverContent>
                  </PopoverPortal>
                </PopoverRoot>
                <Button
                  v-else-if="conversa.plataforma === 'amazon'"
                  size="sm"
                  variant="outline"
                  class="h-7 px-2 text-xs"
                  :disabled="!!acao"
                  :title="conversa.sem_resposta_necessaria ? 'desfazer: volta para a fila' : 'não precisa de resposta (lembre de marcar também no Seller Central)'"
                  :aria-label="conversa.sem_resposta_necessaria ? 'precisa de resposta' : 'não precisa de resposta'"
                  @click="naoPrecisa"
                >
                  <Loader2 v-if="acao === 'nao_precisa'" class="size-3.5 animate-spin" />
                  <Undo2 v-else-if="conversa.sem_resposta_necessaria" class="size-3.5" />
                  <MailX v-else class="size-3.5" />
                  <span class="ml-1 hidden 2xl:inline">{{ conversa.sem_resposta_necessaria ? 'Precisa de resposta' : 'Não precisa de resposta' }}</span>
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  class="h-7 px-2 text-xs"
                  :disabled="!!acao"
                  :title="conversa.situacao === 'fechada' ? 'reabrir a conversa' : 'fechar a conversa (sai da fila; pede confirmação)'"
                  @click="fecharOuReabrir"
                >
                  <!-- Rótulo sempre à mostra: só o ícone (um X num círculo) parecia
                       "fechar o painel" e tirava a conversa da fila. -->
                  <Loader2 v-if="acao === 'fechar'" class="size-3.5 animate-spin" />
                  <Undo2 v-else-if="conversa.situacao === 'fechada'" class="size-3.5" />
                  <Archive v-else class="size-3.5" />
                  <span class="ml-1">{{ conversa.situacao === 'fechada' ? 'Reabrir' : 'Fechar conversa' }}</span>
                </Button>
              </template>
              <!-- Só abre o caso na Amazon (outra aba): vale até para quem só lê. -->
              <Button
                v-if="amazon.caso"
                as="a"
                size="sm"
                variant="outline"
                class="h-7 px-2 text-xs"
                :href="amazon.caso"
                target="_blank"
                rel="noopener noreferrer"
                title="abrir este caso no Seller Central (outra aba)"
                aria-label="Abrir no Seller Central"
              >
                <ExternalLink class="size-3.5" />
                <span class="ml-1 2xl:hidden">Seller Central</span>
                <span class="ml-1 hidden 2xl:inline">Abrir no Seller Central</span>
              </Button>
              <!-- Temu/AliExpress: a tela de chat do Seller Center (a lista de
                   conversas — não há link por conversa), onde se responde. -->
              <Button
                v-else-if="sellerCenter"
                as="a"
                size="sm"
                variant="outline"
                class="h-7 px-2 text-xs"
                :href="sellerCenter.url"
                target="_blank"
                rel="noopener noreferrer"
                :title="`responder no ${sellerCenter.nome}: abre a tela de chat de lá (outra aba; se pedir login, abra pelo perfil da loja no AdsPower)`"
                aria-label="Abrir no Seller Center"
              >
                <ExternalLink class="size-3.5" />
                <span class="ml-1 2xl:hidden">Seller Center</span>
                <span class="ml-1 hidden 2xl:inline">Abrir no Seller Center</span>
              </Button>
              <!-- Magalu: a tela da caixa no Portal do Seller (a lista daquela
                   caixa — não há link por conversa). Vale até para quem só lê. -->
              <Button
                v-else-if="portal"
                as="a"
                size="sm"
                variant="outline"
                class="h-7 px-2 text-xs"
                :href="portal.url"
                target="_blank"
                rel="noopener noreferrer"
                :title="`abrir a caixa ${canalLabel(conversa.canal) || ''} no ${portal.nome} (outra aba) — lá a conversa se acha pelo comprador`"
                aria-label="Abrir no portal"
              >
                <ExternalLink class="size-3.5" />
                <span class="ml-1 2xl:hidden">Portal</span>
                <span class="ml-1 hidden 2xl:inline">Abrir no portal</span>
              </Button>
              <!-- 🖥 AdsPower: o perfil da loja desta conversa, no computador de quem
                   clicou (RF11). Desligado com o porquê quando não há perfil. -->
              <AtendimentoAdsPower
                v-if="!conversa.somente_leitura"
                :conversa-id="conversa.id"
                :perfil="painelDados?.adspower ?? null"
                :carregando="painelCarregando"
                :can-edit="canEdit"
              />
              <Button size="sm" variant="ghost" class="h-7 px-2" :disabled="carregando" title="atualizar a conversa (e o painel do pedido)" aria-label="atualizar a conversa" @click="atualizarTudo">
                <RotateCcw class="size-3.5" :class="{ 'animate-spin': carregando }" />
              </Button>
              <Button size="sm" :variant="pedidoVisivel ? 'secondary' : 'outline'" class="h-7 px-2 text-xs" :title="pedidoVisivel ? 'esconder o pedido' : 'mostrar o pedido'" @click="alternarPedido">
                <PanelRightClose v-if="pedidoVisivel" class="size-3.5" />
                <PanelRightOpen v-else class="size-3.5" />
                <span class="ml-1">Pedido</span>
              </Button>
            </div>
          </div>
        </div>

        <!-- faixas: bloqueio, janela, fechada, não precisa -->
        <div v-if="conversa.situacao === 'bloqueada'" class="shrink-0 border-b border-red-500/30 bg-red-500/10 px-3 py-1.5 text-xs text-red-700 dark:text-red-300">
          <Lock class="mr-1 inline size-3.5" />A plataforma não deixa mais responder esta conversa<template v-if="conversa.bloqueio_motivo">: <span :title="conversa.bloqueio_motivo">{{ bloqueioLegivel(conversa.bloqueio_motivo) }}</span></template>.
          <!-- Reabrir à mão vale para a bloqueada também (a plataforma liberou). -->
          <button v-if="canEdit && !conversa.somente_leitura" type="button" class="ml-1 underline disabled:opacity-50" :disabled="!!acao" @click="patch({ situacao: 'aberta' }, 'fechar', 'Conversa reaberta')">a plataforma liberou? reabrir</button>
        </div>
        <div v-else-if="janela" class="shrink-0 border-b px-3 py-1.5 text-xs" :class="janela.cls">{{ janela.texto }}</div>
        <div v-if="conversa.situacao === 'fechada'" class="shrink-0 border-b bg-muted/50 px-3 py-1.5 text-xs text-muted-foreground">
          Conversa fechada por alguém da equipe. Se o comprador escrever de novo, ela volta para a fila.
        </div>
        <!-- Amazon sem conta: sem ela não há por onde responder nem modo. -->
        <div v-if="semConta" class="shrink-0 border-b border-amber-500/40 bg-amber-500/10 px-3 py-1.5 text-xs text-amber-900 dark:text-amber-200">
          <div class="flex flex-wrap items-center gap-1.5">
            <TriangleAlert class="size-3.5 shrink-0" />
            <span>Qual conta Amazon é esta? O e-mail chegou sem dizer de qual loja é.</span>
            <template v-if="canEdit">
              <select
                v-model="contaEscolhida"
                class="h-7 min-w-[180px] rounded-md border bg-background px-1.5 text-xs text-foreground disabled:opacity-60"
                aria-label="Qual conta Amazon é esta?"
                :disabled="!!acao || !contasAmazon.length"
                @change="escolherConta"
              >
                <option value="">{{ contasAmazon.length ? 'escolher a conta…' : 'nenhuma conta Amazon integrada' }}</option>
                <option v-for="l in contasAmazon" :key="l.integration_id" :value="l.integration_id">{{ l.conta || 'sem nome' }}</option>
              </select>
              <Loader2 v-if="acao === 'conta'" class="size-3.5 animate-spin" />
            </template>
            <span v-else class="text-muted-foreground">(peça a quem pode editar o Atendimento)</span>
          </div>
        </div>
        <!-- Amazon: a cópia de uma resposta do Seller Central empatou entre esta e
             outra conversa do mesmo nome. O DaVinci não chuta (nenhuma saiu da
             fila) — a pessoa confere na Amazon. Só vem enquanto esta aguarda. -->
        <div v-if="conversa.plataforma === 'amazon' && conversa.amazon_copia_a_conferir_em" class="shrink-0 border-b border-amber-500/40 bg-amber-500/10 px-3 py-1.5 text-xs text-amber-900 dark:text-amber-200">
          <TriangleAlert class="mr-1 inline size-3.5" />O Seller Central respondeu alguém com este nome em {{ fmtDataHora(conversa.amazon_copia_a_conferir_em) }}, mas há outra conversa com o mesmo nome e o DaVinci não sabe qual foi.
          Confira<template v-if="amazon.caso"> <a :href="amazon.caso" target="_blank" rel="noopener noreferrer" class="inline-flex items-center gap-0.5 underline hover:text-foreground">no Seller Central<ExternalLink class="size-3" /></a></template><template v-else> no Seller Central</template>: se esta já foi respondida, marque "não precisa de resposta".
        </div>
        <div v-if="conversa.sem_resposta_necessaria" class="shrink-0 border-b bg-muted/50 px-3 py-1.5 text-xs text-muted-foreground">
          Marcada como "não precisa de resposta".<template v-if="conversa.plataforma === 'amazon'">
            <!-- O link fica aqui também: a aba pode ter sido fechada antes de a Amazon confirmar. -->
            <template v-if="amazon.semResposta"> Se ainda não marcou na Amazon:
              <a :href="amazon.semResposta" target="_blank" rel="noopener noreferrer" class="inline-flex items-center gap-0.5 underline hover:text-foreground">abrir "Não é necessária resposta"<ExternalLink class="size-3" /></a>
              (marca o caso como resolvido na Amazon).</template>
            <template v-else> Na Amazon, marque também "Não é necessária resposta" no Seller Central.</template>
          </template>
        </div>

        <!-- Cartão da reclamação/mediação/devolução da plataforma (item 1): nº,
             motivo, status e prazo; só leitura. Montado sempre (ele mesmo busca);
             a faixa só aparece quando há alguma. Rola sozinho se forem várias. -->
        <div
          v-if="temCartaoReclamacao"
          v-show="reclamacoesQtd > 0"
          class="max-h-[38vh] shrink-0 overflow-y-auto border-b px-3 py-2"
        >
          <AtendimentoReclamacao ref="reclamacaoRef" :conversa-id="conversa.id" @carregado="aoCarregarReclamacoes" />
        </div>

        <!-- mensagens -->
        <div ref="rolagem" class="min-h-0 flex-1 space-y-3 overflow-y-auto overscroll-contain bg-muted/40 px-3 py-3 dark:bg-muted/20">
          <div v-if="!linhas.length" class="py-10 text-center text-sm text-muted-foreground">Sem mensagens gravadas ainda.</div>
          <template v-for="l in linhas" :key="l.chave">
            <div v-if="l.tipo === 'dia'" class="flex justify-center py-1">
              <span class="rounded-full bg-background px-2.5 py-0.5 text-[10px] font-medium text-muted-foreground shadow-sm">{{ l.texto }}</span>
            </div>
            <!-- mudança de etiqueta: no meio, como o aviso do sistema, com a cor da nova -->
            <div v-else-if="l.tipo === 'etiqueta'" class="flex justify-center" data-etiqueta-mudou>
              <div class="max-w-[85%] px-3 py-0.5 text-center text-[11px] text-muted-foreground" :title="fmtDataHora(l.h.em)">
                <div class="inline-flex items-center gap-1.5 font-medium text-foreground/80">
                  <span class="inline-block size-2 shrink-0 rounded-full" :class="pontoDaEtiqueta(l.h)" aria-hidden="true" />
                  {{ frasesDaEtiqueta(l.h).titulo }}
                  <span class="font-normal text-muted-foreground">· {{ fmtDiaHora(l.h.em) }}</span>
                </div>
                <div v-if="frasesDaEtiqueta(l.h).detalhe" class="break-words">{{ frasesDaEtiqueta(l.h).detalhe }}</div>
              </div>
            </div>
            <!-- nota interna: amarela, só a equipe vê -->
            <div v-else-if="lado(l.m) === 'nota'" :data-msg-id="l.m.id">
              <AtendimentoNota :mensagem="l.m" />
            </div>
            <div
              v-else-if="lado(l.m) === 'sistema'"
              class="flex justify-center"
              :data-msg-id="l.m.id"
            >
              <div class="max-w-[85%] whitespace-pre-wrap break-words px-3 py-1 text-center text-xs text-muted-foreground" :title="fmtDataHora(l.m.enviada_em)">{{ l.m.texto || TIPO_LABEL[l.m.tipo] || '' }}</div>
            </div>
            <div v-else class="flex flex-col" :class="lado(l.m) === 'cliente' || lado(l.m) === 'mediador' ? 'items-start' : 'items-end'" :data-msg-id="l.m.id">
              <div v-if="lado(l.m) === 'mediador'" class="mb-0.5 px-1 text-[11px] font-medium text-violet-700 dark:text-violet-300" title="a plataforma falando na reclamação (mediação)">Mediador · {{ plataformaInfo(conversa.plataforma).nome }}</div>
              <!-- por onde a resposta da loja saiu (o Duoke mostra o atendente aqui) -->
              <div v-else-if="lado(l.m) === 'loja'" class="mb-0.5 flex max-w-[85%] items-center gap-1 px-1 text-[11px] md:max-w-[75%]" :class="autorCls(l.m)" :title="origemHint(l.m)">
                <Bot v-if="l.m.origem === 'davinci_ia'" class="size-3 shrink-0" />
                <span class="truncate">{{ origemLabel(l.m, conversa.plataforma) }}</span>
              </div>
              <div class="max-w-[85%] rounded-lg px-3 py-2 transition-shadow md:max-w-[75%]" :class="[balaoCls(l.m), destacadaId === l.m.id ? 'ring-2 ring-primary/70' : '']">
                <div v-if="STATUS_MSG[l.m.status]" class="mb-1">
                  <span class="inline-flex items-center gap-1 rounded px-1.5 py-px text-[11px]" :class="STATUS_MSG[l.m.status].cls" :title="STATUS_MSG[l.m.status].hint">
                    <Loader2 v-if="l.m.status === 'enviando'" class="size-3 animate-spin" />
                    <TriangleAlert v-else class="size-3" />
                    {{ STATUS_MSG[l.m.status].label }}
                  </span>
                </div>
                <div v-if="l.m.texto" class="whitespace-pre-wrap break-words text-sm leading-relaxed"><template v-for="(p, i) in partesComLink(l.m.texto)" :key="i"><a v-if="p.t === 'url'" :href="p.v" target="_blank" rel="noopener noreferrer" class="break-all underline">{{ p.v }}</a><template v-else>{{ p.v }}</template></template></div>
                <div v-else-if="!pecasDaMsg(l.m).length" class="text-sm italic text-muted-foreground">[{{ TIPO_LABEL[l.m.tipo] || 'sem texto' }}]</div>
                <div v-if="pecasDaMsg(l.m).length" class="flex flex-wrap gap-2" :class="l.m.texto ? 'mt-2' : ''">
                  <template v-for="pc in pecasDaMsg(l.m)" :key="pc.chave">
                    <AtendimentoCartaoPedido v-if="pc.tipo === 'pedido'" :cartao="pc.cartao" :plataforma="conversa.plataforma" :retrato="pedidoMkt" />
                    <AtendimentoCartaoProduto v-else-if="pc.tipo === 'produto'" :cartao="pc.cartao" />
                    <button
                      v-else-if="pc.tipo === 'imagem'"
                      type="button"
                      class="overflow-hidden rounded-md border bg-background"
                      :title="`abrir ${pc.nome}`"
                      @click="imagemAberta = { url: pc.url, nome: pc.nome }"
                    >
                      <img :src="pc.url" :alt="pc.nome" loading="lazy" referrerpolicy="no-referrer" class="size-28 object-cover" />
                    </button>
                    <a v-else-if="pc.tipo === 'link'" :href="pc.url" target="_blank" rel="noopener noreferrer" class="max-w-[220px] truncate rounded border bg-background px-2 py-0.5 text-xs underline">{{ pc.nome }}</a>
                    <span v-else class="rounded border bg-background px-2 py-0.5 text-xs text-muted-foreground">{{ pc.nome }}</span>
                  </template>
                </div>
                <!-- A frase diz o que fazer; o código cru (para o suporte) fica no title. -->
                <div v-if="l.m.status === 'falhou'" class="mt-1 rounded bg-red-500/10 px-1.5 py-0.5 text-[11px] text-red-700 dark:text-red-300" :title="l.m.erro || ''">
                  {{ erroEnvioLegivel(l.m.erro) }}
                  <!-- Só com o envio ligado e só na última tentativa (não reenvia sozinho). -->
                  <button
                    v-if="podeTentarDeNovo(l.m)"
                    type="button"
                    class="ml-1 inline-flex items-center gap-0.5 rounded border border-red-500/40 bg-background px-1.5 py-px font-medium hover:bg-red-500/10 disabled:opacity-50"
                    :disabled="!!tentandoId"
                    title="mandar o mesmo texto de novo (pede confirmação)"
                    @click="tentarDeNovo(l.m)"
                  >
                    <Loader2 v-if="tentandoId === l.m.id" class="size-3 animate-spin" /><RotateCcw v-else class="size-3" /> tentar de novo
                  </button>
                  <span v-else-if="l.m.tipo === 'imagem' && lado(l.m) === 'loja' && l.m.origem === 'davinci_humano'" class="ml-1 opacity-80">— anexe a foto de novo para tentar outra vez.</span>
                </div>
                <div v-if="l.m.status === 'revisar' && lado(l.m) === 'loja'" class="mt-1 space-y-1 rounded bg-amber-500/10 px-1.5 py-1 text-[11px] text-amber-900 dark:text-amber-200">
                  <div class="font-medium">Não sabemos se chegou ao comprador.</div>
                  <div>A plataforma não confirmou o envio. Confira lá e marque aqui — o DaVinci não manda de novo sozinho.</div>
                  <div v-if="canEdit" class="flex flex-wrap gap-1 pt-0.5">
                    <button
                      type="button"
                      class="inline-flex items-center gap-1 rounded border bg-background px-1.5 py-0.5 text-emerald-700 hover:bg-emerald-500/10 disabled:opacity-50 dark:text-emerald-300"
                      title="a resposta aparece na plataforma: chegou ao comprador"
                      :disabled="!!conferindo"
                      @click="conferir(l.m, true)"
                    >
                      <Loader2 v-if="conferindo === l.m.id" class="size-3 animate-spin" /><Check v-else class="size-3" /> Saiu
                    </button>
                    <button
                      type="button"
                      class="inline-flex items-center gap-1 rounded border bg-background px-1.5 py-0.5 text-red-700 hover:bg-red-500/10 disabled:opacity-50 dark:text-red-300"
                      title="a resposta NÃO aparece na plataforma: libera responder de novo"
                      :disabled="!!conferindo"
                      @click="conferir(l.m, false)"
                    >
                      <X class="size-3" /> Não saiu
                    </button>
                  </div>
                </div>
              </div>
              <div class="mt-0.5 flex items-center gap-1 px-1 text-[10px] text-muted-foreground" :title="fmtDataHora(l.m.enviada_em)">
                {{ fmtDiaHora(l.m.enviada_em) }}
                <CheckCheck v-if="l.m.status === 'enviada' && lado(l.m) === 'loja'" class="size-3" />
              </div>
              <!-- IA × equipe: o que a IA teria dito no lugar desta resposta -->
              <AtendimentoIaTeria
                v-for="sg in comparacoes.mapa.get(l.m.id) || []"
                :key="sg.id"
                class="mt-1"
                :sugestao="sg"
                :can-edit="canEdit"
                @avaliada="(a: AvaliacaoIa) => aoAvaliar(sg.id, a)"
              />
            </div>
          </template>
          <!-- comparações cuja resposta real não está na tela (não somem) -->
          <div v-if="comparacoes.soltas.length" class="flex flex-col items-end gap-1">
            <AtendimentoIaTeria
              v-for="sg in comparacoes.soltas"
              :key="sg.id"
              :sugestao="sg"
              :can-edit="canEdit"
              @avaliada="(a: AvaliacaoIa) => aoAvaliar(sg.id, a)"
            />
          </div>
        </div>

        <!-- resposta -->
        <div class="shrink-0 space-y-2 border-t bg-background p-2">
          <div v-if="atualizacaoFalhou" class="text-[11px] text-muted-foreground">Não consegui atualizar agora — tento de novo em instantes.</div>

          <!-- Responder × Nota interna (a nota vale até no modo observação: não sai
               para ninguém) e o botão de foto -->
          <div v-if="!conversa.somente_leitura" class="flex items-center gap-1 text-xs" role="tablist" aria-label="caixa de resposta">
            <button
              type="button"
              role="tab"
              :aria-selected="modoCaixa === 'responder'"
              class="rounded-md px-2 py-1"
              :class="modoCaixa === 'responder' ? 'bg-muted font-medium text-foreground' : 'text-muted-foreground hover:bg-muted/60'"
              @click="modoCaixa = 'responder'"
            >Responder</button>
            <button
              type="button"
              role="tab"
              :aria-selected="modoCaixa === 'nota'"
              class="inline-flex items-center gap-1 rounded-md px-2 py-1"
              :class="modoCaixa === 'nota' ? 'bg-amber-100 font-medium text-amber-900 dark:bg-amber-900/30 dark:text-amber-200' : 'text-muted-foreground hover:bg-muted/60'"
              title="recado para a equipe — não vai para o comprador"
              @click="modoCaixa = 'nota'"
            ><StickyNote class="size-3.5" /> Nota interna</button>
            <template v-if="modoCaixa === 'responder'">
              <input ref="fotoInput" type="file" accept="image/jpeg,image/png" class="hidden" aria-hidden="true" tabindex="-1" @change="aoEscolherFoto" />
              <Button
                size="sm"
                variant="outline"
                class="ml-auto h-7 px-2 text-xs"
                :disabled="!!motivoSemFoto || enviandoFoto || !!foto"
                :title="motivoSemFoto || (foto ? 'já há uma foto anexada — envie ou descarte' : 'anexar uma foto (JPG ou PNG) para mandar ao comprador')"
                aria-label="anexar foto"
                @click="escolherFoto"
              >
                <ImagePlus class="size-3.5" /><span class="ml-1">Foto</span>
              </Button>
            </template>
          </div>

          <!-- foto anexada: prévia, texto (ML) e enviar/descartar -->
          <div v-if="modoCaixa === 'responder' && foto" class="flex items-start gap-2 rounded-md border bg-muted/40 p-2 text-xs">
            <img :src="foto.previa" alt="foto anexada" class="size-16 shrink-0 rounded border object-cover" />
            <div class="min-w-0 flex-1 space-y-1">
              <div class="truncate font-medium" :title="foto.arquivo.name">{{ foto.arquivo.name }}</div>
              <div class="text-muted-foreground">{{ (foto.arquivo.size / 1024).toFixed(0) }} KB · {{ envioFoto?.legenda_permitida ? 'vai junto do texto abaixo' : 'vai sozinha (o texto vai pela caixa, separado)' }}</div>
              <input
                v-if="envioFoto?.legenda_permitida"
                v-model="legendaFoto"
                :maxlength="limite || 350"
                class="h-7 w-full rounded-md border bg-background px-2 text-xs"
                :placeholder="envioFoto?.legenda_obrigatoria ? 'Texto que vai junto da foto (obrigatório no Mercado Livre)' : 'Texto que vai junto da foto'"
                aria-label="texto que vai junto da foto"
              />
              <div v-if="motivoSemFoto" class="text-amber-800 dark:text-amber-300">{{ motivoSemFoto }}</div>
            </div>
            <div class="flex shrink-0 flex-col gap-1">
              <Button size="sm" class="h-7 px-2 text-xs" :disabled="!!motivoSemFoto || enviandoFoto || (!!envioFoto?.legenda_obrigatoria && !legendaFoto.trim())" title="mandar a foto ao comprador" @click="enviarFoto()">
                <Loader2 v-if="enviandoFoto" class="mr-1 size-3.5 animate-spin" /><Send v-else class="mr-1 size-3.5" /> Enviar foto
              </Button>
              <Button size="sm" variant="ghost" class="h-7 px-2 text-xs" :disabled="enviandoFoto" title="descartar a foto (nada foi enviado)" @click="descartarFoto"><X class="mr-1 size-3.5" /> Descartar</Button>
            </div>
          </div>

          <!-- nota interna (só a equipe vê) -->
          <AtendimentoNota
            v-if="modoCaixa === 'nota' && !conversa.somente_leitura"
            caixa
            :conversa-id="conversa.id"
            :can-edit="canEdit"
            @criada="aoCriarNota"
          />

          <!-- modo observação: quem responde é o Duoke; aqui, o que a IA responderia -->
          <AtendimentoObservacao
            v-else-if="observacao"
            :sugestao="sugestaoAtual"
            :can-edit="canEdit"
            :pode-sugerir="podeSugerir"
            :gerando="gerando"
            :sem-sugestao-motivo="semSugestaoMotivo"
            :tem-mais-nova="temSugestaoMaisNova"
            :agora="agora"
            :plataforma="conversa?.plataforma"
            :canal="conversa?.canal"
            @sugerir="sugerir"
            @avaliada="aoAvaliar"
          />

          <template v-else>
          <div v-if="bloqueioEnvio" class="flex items-start gap-1.5 rounded-md border border-amber-500/40 bg-amber-500/10 px-2.5 py-1.5 text-xs text-amber-900 dark:text-amber-200">
            <Lock class="mt-0.5 size-3.5 shrink-0" />
            <span class="flex-1">{{ bloqueioEnvio }}</span>
            <!-- Só quando o motivo mostrado É o modo da loja: com o envio
                 desligado no servidor (ou a conversa bloqueada), trocar o modo
                 não resolve e só tira a loja de Observar à toa. -->
            <button v-if="detalhe.envio.codigo === 'canal_em_observacao' && !conversa.somente_leitura && canEdit" type="button" class="shrink-0 underline" @click="emit('abrirAba', 'lojas')">abrir Lojas e modo</button>
          </div>

          <!-- sugestão da IA -->
          <div v-if="rascunho" class="space-y-1 rounded-md border border-violet-300/60 bg-violet-50/60 px-2.5 py-1.5 text-xs dark:border-violet-800/60 dark:bg-violet-900/15">
            <div class="flex flex-wrap items-center gap-x-1.5 gap-y-1">
              <span class="inline-flex items-center gap-1 font-medium text-violet-700 dark:text-violet-300"><Sparkles class="size-3.5" /> Sugestão da IA</span>
              <span v-if="rascunho.categoria" class="text-muted-foreground">· {{ categoriaLabel(rascunho.categoria) }}</span>
              <span v-if="rascunho.confianca !== null && rascunho.confianca !== undefined" class="text-muted-foreground">· {{ Math.round(rascunho.confianca * 100) }}% de confiança</span>
              <span
                v-if="rascunho.precisa_humano"
                class="rounded bg-amber-500/20 px-1.5 text-amber-800 dark:text-amber-300"
                title="a IA marcou que este assunto precisa de uma pessoa (dinheiro, troca, reclamação, pedido sem dado…) — confira com cuidado"
              >precisa de pessoa</span>
              <span class="ml-auto flex flex-wrap items-center gap-1">
                <button v-if="rascunho.texto && !usandoSugestao" type="button" class="rounded border border-violet-300/70 bg-background px-1.5 py-0.5 hover:bg-muted" @click="usarSugestao">usar sugestão</button>
                <button v-if="rascunho.texto" type="button" class="rounded p-1 hover:bg-muted" title="copiar o texto da sugestão (para colar no Duoke, por exemplo)" @click="copiarTexto(rascunho.texto, 'Texto da sugestão')"><Copy class="size-3.5" /></button>
                <template v-if="canEdit">
                  <button type="button" class="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-muted-foreground hover:bg-red-500/10 hover:text-red-700" title="descartar a sugestão (pede o motivo)" @click="descarte.aberto = !descarte.aberto">
                    <Trash2 class="size-3.5" /> Descartar sugestão
                  </button>
                </template>
              </span>
            </div>
            <div v-if="!rascunho.texto" class="italic text-muted-foreground">A IA não escreveu uma resposta que possa sair — responda você.</div>
            <div v-if="rascunho.motivo" class="text-muted-foreground"><span class="font-medium">Por quê:</span> {{ rascunho.motivo }}</div>
            <ul v-if="rascunho.validador_erros?.length" class="list-inside list-disc text-amber-800 dark:text-amber-300">
              <li v-for="(v, i) in rascunho.validador_erros" :key="i">{{ v }}</li>
            </ul>
            <div v-if="descarte.aberto" class="space-y-1 pt-1">
              <div class="flex flex-wrap gap-1">
                <button v-for="m in MOTIVOS_DESCARTE" :key="m" type="button" class="rounded-full border bg-background px-2 py-0.5 hover:bg-muted" :class="descarte.motivo === m ? 'border-primary text-primary' : ''" @click="descarte.motivo = m">{{ m }}</button>
              </div>
              <div class="flex flex-wrap items-end gap-1.5">
                <input v-model="descarte.motivo" :maxlength="MAX_MOTIVO" class="h-7 min-w-[220px] flex-1 rounded-md border bg-background px-2 text-xs" placeholder="por que a sugestão não serve?" aria-label="motivo do descarte da sugestão" @keydown.enter.prevent="confirmarDescarte" />
                <Button size="sm" variant="destructive" class="h-7 text-xs" :disabled="!descarte.motivo.trim() || !!descarte.salvando" @click="confirmarDescarte">
                  <Loader2 v-if="descarte.salvando === rascunho.id" class="mr-1 size-3.5 animate-spin" /> descartar
                </Button>
                <Button size="sm" variant="ghost" class="h-7 px-2" @click="descarte.aberto = false"><X class="size-3.5" /></Button>
              </div>
            </div>
          </div>

          <!-- depois do envio: a sugestão estava boa? -->
          <div v-if="aval.rascunhoId && canEdit" class="space-y-1 rounded-md border bg-muted/40 px-2.5 py-1.5 text-xs">
            <div class="flex flex-wrap items-center gap-1.5">
              <Sparkles class="size-3.5 text-violet-600 dark:text-violet-400" />
              <template v-if="aval.nota">
                <span class="text-muted-foreground">Guardado — a IA aprende com isso.</span>
              </template>
              <template v-else>
                <span>{{ aval.usada ? 'Você usou a sugestão da IA. Ela estava boa?' : 'Você respondeu sem a sugestão da IA. O que ela errou?' }}</span>
                <span class="ml-auto flex items-center gap-1">
                  <button v-if="aval.usada" type="button" class="inline-flex items-center gap-1 rounded border bg-background px-1.5 py-0.5 text-emerald-700 hover:bg-emerald-500/10 disabled:opacity-50 dark:text-emerald-300" title="a sugestão estava boa" :disabled="aval.salvando" @click="avaliar('ok')"><ThumbsUp class="size-3.5" /> boa</button>
                  <button type="button" class="inline-flex items-center gap-1 rounded border bg-background px-1.5 py-0.5 text-red-700 hover:bg-red-500/10 disabled:opacity-50 dark:text-red-300" title="a sugestão errou — dizer o que era o certo" :disabled="aval.salvando" @click="aval.abrindo = !aval.abrindo"><ThumbsDown class="size-3.5" /> errou</button>
                </span>
              </template>
              <button type="button" class="rounded p-0.5 text-muted-foreground hover:bg-muted" title="dispensar" aria-label="dispensar" @click="limparAval"><X class="size-3.5" /></button>
            </div>
            <div v-if="aval.abrindo && !aval.nota" class="flex flex-wrap items-end gap-1.5">
              <textarea v-model="aval.correcao" rows="2" :maxlength="MAX_CORRECAO" class="min-w-[220px] flex-1 rounded-md border bg-background px-2 py-1 text-xs" placeholder="o que estava errado — e como seria o certo?" aria-label="o que a sugestão errou e como seria o certo" />
              <Button size="sm" class="h-7 text-xs" :disabled="!aval.correcao.trim() || aval.salvando" @click="avaliar('erro')">
                <Loader2 v-if="aval.salvando" class="mr-1 size-3.5 animate-spin" /><Check v-else class="mr-1 size-3.5" /> salvar correção
              </Button>
            </div>
          </div>

          <!-- Magalu: a resposta não chega direto — a moderação decide. -->
          <div v-if="moderacao && podeDigitar" class="flex items-start gap-1.5 px-0.5 text-[11px] leading-4 text-sky-800 dark:text-sky-300">
            <ShieldCheck class="mt-px size-3.5 shrink-0" aria-hidden="true" />
            <span>{{ AVISO_MODERACAO_MAGALU }}</span>
          </div>

          <textarea
            ref="caixa"
            v-model="texto"
            rows="3"
            :disabled="!podeDigitar || enviando"
            class="block max-h-[40vh] min-h-[76px] w-full resize-y rounded-md border bg-background px-2.5 py-2 text-sm [field-sizing:content] focus:outline-none focus:ring-1 focus:ring-primary disabled:cursor-not-allowed disabled:opacity-60"
            :placeholder="podeDigitar ? 'Escreva a resposta… (Ctrl+Enter envia)' : 'Resposta desabilitada nesta conversa'"
            aria-label="resposta ao comprador"
            @keydown="aoTeclar"
          />

          <div v-if="erroEnvio" class="rounded-md border border-red-500/40 bg-red-500/10 px-2.5 py-1.5 text-xs text-red-700 dark:text-red-300">
            <div class="flex items-start gap-2">
              <span class="flex-1 font-medium">{{ erroEnvio.texto }}<template v-if="erroEnvio.motivos.length">:</template></span>
              <button type="button" class="shrink-0 opacity-70 hover:opacity-100" title="fechar" @click="erroEnvio = null"><X class="size-3.5" /></button>
            </div>
            <ul v-if="erroEnvio.motivos.length" class="mt-0.5 list-inside list-disc">
              <li v-for="(mo, i) in erroEnvio.motivos" :key="i">{{ mo }}</li>
            </ul>
            <!-- Alguém respondeu enquanto a pessoa escrevia: a conversa já foi
                 relida acima; ela confere e decide. -->
            <button
              v-if="erroEnvio.confirmar && podeEnviar"
              type="button"
              class="mt-1 rounded border border-red-500/40 bg-background px-1.5 py-0.5 font-medium hover:bg-red-500/10"
              title="você viu a resposta que já saiu e quer mandar a sua também"
              @click="enviar({ confirmar: true })"
            >Conferi — enviar mesmo assim</button>
          </div>
          <div v-else-if="lacunas.length && podeDigitar" class="text-[11px] text-amber-800 dark:text-amber-300">
            Troque {{ lacunas.join(', ') }} pelo dado antes de enviar.
          </div>

          <div class="flex flex-wrap items-center gap-1.5">
            <!-- Ao fechar, o foco fica na caixa de resposta (onde a resposta
                 pronta entrou), não volta para o botão do menu. -->
            <PopoverRoot v-model:open="menuModelos">
              <PopoverTrigger as-child>
                <Button size="sm" variant="outline" class="h-8 px-2 text-xs" :disabled="!podeDigitar">
                  <MessageSquareText class="mr-1 size-3.5" /> Respostas prontas <ChevronDown class="ml-0.5 size-3.5" />
                </Button>
              </PopoverTrigger>
              <PopoverPortal>
                <PopoverContent
                  side="top"
                  align="start"
                  :side-offset="4"
                  :collision-padding="8"
                  class="z-[70] w-[380px] max-w-[calc(100vw-16px)] rounded-md border bg-background p-2 shadow-lg"
                  @close-auto-focus="(e: Event) => e.preventDefault()"
                >
                  <input v-model="buscaModelo" class="mb-1.5 h-8 w-full rounded-md border bg-background px-2 text-sm" placeholder="buscar resposta pronta…" aria-label="buscar resposta pronta" />
                  <ul v-if="modelosDaConversa.length" class="max-h-72 space-y-0.5 overflow-y-auto">
                    <li v-for="m in modelosDaConversa" :key="m.id">
                      <button type="button" class="w-full rounded px-2 py-1.5 text-left hover:bg-muted" @click="inserirModelo(m)">
                        <div class="text-xs font-medium">{{ m.titulo }}</div>
                        <div class="line-clamp-2 text-[11px] text-muted-foreground">{{ m.texto }}</div>
                      </button>
                    </li>
                  </ul>
                  <div v-else class="px-2 py-3 text-center text-xs text-muted-foreground">
                    {{ buscaModelo ? 'Nada com essa busca.' : `Nenhuma resposta pronta para ${plataformaInfo(conversa.plataforma).nome}.` }}
                    <button type="button" class="mt-1 block w-full underline" @click="menuModelos = false; emit('abrirAba', 'modelos')">cadastrar em Respostas prontas</button>
                  </div>
                </PopoverContent>
              </PopoverPortal>
            </PopoverRoot>
            <Button v-if="podeSugerir" size="sm" variant="outline" class="h-8 px-2 text-xs" :disabled="gerando" title="pedir uma sugestão de resposta à IA agora" @click="sugerir">
              <Loader2 v-if="gerando" class="mr-1 size-3.5 animate-spin" />
              <Sparkles v-else class="mr-1 size-3.5" />
              Sugerir resposta
            </Button>
            <span
              v-if="limite && podeDigitar"
              class="ml-auto text-[11px] tabular-nums"
              :class="acimaDoLimite ? 'font-semibold text-red-600 dark:text-red-400' : tamanho > limite * 0.9 ? 'text-amber-700 dark:text-amber-300' : 'text-muted-foreground'"
              :title="`limite de ${limite} caracteres neste canal${variasCaixas(conversa.plataforma) ? ` (${canalLabel(conversa.canal)})` : ''}, contados como a plataforma conta (espaços repetidos não contam${conversa.plataforma === 'ml' ? '; emoji sai e … vira ...' : ''})`"
            >{{ tamanho }}/{{ limite }}</span>
            <Button
              size="sm"
              class="h-8"
              :class="limite && podeDigitar ? '' : 'ml-auto'"
              :disabled="!podeEnviar"
              :title="incertos.has(conversaId) ? 'travado até a conversa mostrar se o último envio saiu' : 'enviar (Ctrl+Enter)'"
              @click="enviar()"
            >
              <Loader2 v-if="enviando" class="mr-1.5 size-4 animate-spin" />
              <Send v-else class="mr-1.5 size-4" />
              Enviar
            </Button>
          </div>
          </template>
        </div>
      </template>
    </div>

    <!-- pedido -->
    <div v-if="gaveta && !telaLarga" class="fixed inset-0 z-40 bg-black/40" @click="gaveta = false" />
    <aside
      v-if="detalhe && pedidoVisivel"
      class="flex min-h-0 flex-col border-l bg-card"
      :class="telaLarga ? 'w-[340px] shrink-0' : 'fixed inset-y-0 right-0 z-50 w-[min(380px,92vw)] shadow-2xl'"
    >
      <AtendimentoPedido
        :key="detalhe.conversa.id"
        :conversa="detalhe.conversa"
        :contexto="detalhe.contexto"
        :pedido-mkt="pedidoMkt"
        :produto="produtoAnuncio"
        :produtos-conversa="produtosConversa"
        :can-edit="canEdit"
        :cliente="cliente"
        :leitura-ativa="flags?.leitura_ativa !== false"
        :atualizavel="detalhe.pedido_atualizavel ?? null"
        :painel="painelDados"
        :painel-carregando="painelCarregando"
        :painel-erro="painelErro"
        @fechar="alternarPedido"
        @atualizado="aoAtualizarPedido"
        @ir-para="irPara"
        @recarregar-painel="(atualizar: boolean) => carregarPainel(detalhe!.conversa.id, atualizar)"
      />
    </aside>

    <!-- foto do cliente, grande -->
    <div
      v-if="imagemAberta"
      class="fixed inset-0 z-[80] flex items-center justify-center bg-black/80 p-4"
      role="dialog"
      aria-modal="true"
      :aria-label="imagemAberta.nome"
      @click.self="imagemAberta = null"
    >
      <img :src="imagemAberta.url" :alt="imagemAberta.nome" referrerpolicy="no-referrer" class="max-h-[88vh] max-w-full rounded-md object-contain shadow-2xl" />
      <div class="absolute right-3 top-3 flex items-center gap-2">
        <a :href="imagemAberta.url" target="_blank" rel="noopener noreferrer" class="rounded-md bg-white/15 px-2 py-1 text-xs text-white hover:bg-white/25">abrir original</a>
        <button type="button" class="rounded-md bg-white/15 p-1.5 text-white hover:bg-white/25" title="fechar (Esc)" aria-label="fechar" @click="imagemAberta = null"><X class="size-4" /></button>
      </div>
    </div>
  </div>
</template>
