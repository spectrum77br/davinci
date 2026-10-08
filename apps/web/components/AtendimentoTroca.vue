<script lang="ts">
// Diálogo da TROCA DE PRODUTO do pedido em "Aguardando Cancelamento" por
// falta de estoque (Atendimento, item 4, fase 4c, 08/10/2026). Abre pelo
// botão "Trocar" de cada sugestão (AtendimentoTrocaSugestoes, dentro do
// cartão do porquê e na lista Ag. cancelamento) e pelo "Retomar" da troca
// aberta do pedido.
//
// O caminho (services/atendimento/troca.py, routers/atendimento_troca.py):
//   1. PRÉVIA — POST /pedidos/{n}/troca/previa: só GETs no Bling e na Shopee,
//      nada é gravado. O antes e o depois, o valor (o mesmo: o cliente paga
//      o mesmo), a linha que vai para as Observações, os passos e cada trava
//      com o texto do backend.
//   2. ACEITE (Eduardo, 07/10/2026) — nos níveis 1 e 2 (outra cor, outro
//      modelo) a CAIXINHA "O cliente aceitou a troca" é obrigatória
//      (`confirmar`); a PROVA é opcional: uma mensagem do cliente da mesma
//      conversa (`aceites_possiveis`, fonte davinci) OU a resposta colada do
//      Duoke com a data e a hora (fonte duoke). Sem prova, fica o nome de
//      quem clicou (declarado). Nível 0 (o mesmo produto em outro lote):
//      "Trocar lote", sem aceite — é o que o robô de lote faria.
//   3. CLIQUE — POST /pedidos/{n}/troca com o `previa_hash` e a `idem_key`.
//      A chave nasce com a abertura do diálogo: o clique repetido, ou a
//      resposta que se perdeu na rede, devolve a MESMA troca — nunca duas.
//      Depois de uma recusa (409/422), a próxima tentativa é outra decisão:
//      pede a prévia de novo e ganha outra chave. 200 mesmo parada no meio
//      (o `estado` diz onde); 409 = uma trava recusou.
//   4. RESULTADO com os passos; parada no meio (estado aberto), "Retomar" —
//      POST /trocas/{id}/retomar (GET primeiro, só para frente, nunca PUT).
// O backend confere tudo de novo no clique (nunca confia na tela): a tela só
// não deixa clicar o que a prévia já disse que não passa. Nada aqui manda
// mensagem ao comprador (a oferta é o "enviar oferta" da sugestão, fase 4d).
//
// QUEM MEXE (fase de observação, 07/10/2026): trocar escreve no Bling e
// APROVA a Margem — só quem o /me traz com `atendimento_mexe` e tem
// atendimento.edit e margem.edit (`acessoDaTroca`); quem só lê vê o cartão
// e o estado da troca, sem os botões. A API recusa do mesmo jeito.
//
// Este bloco (não o setup) é o módulo dos ajudantes puros, testados em
// tests/atendimento-troca.cjs — por isso só importa TIPO (o
// `useAcessoDaTroca` usa as auto-importações do Nuxt só quando chamado).

const BASE = '/api/atendimento'
// GET /trocas?abertas=&pedido= (quem só lê também lê).
export const URL_TROCAS = `${BASE}/trocas`
export function urlTroca(numero: string): string {
  return `${BASE}/pedidos/${encodeURIComponent(numero)}/troca`
}
export function urlPrevia(numero: string): string {
  return `${urlTroca(numero)}/previa`
}
export function urlRetomar(id: string): string {
  return `${URL_TROCAS}/${encodeURIComponent(id)}/retomar`
}

// `TravaTrocaOut`.
export type TravaTroca = {
  code: string
  ok: boolean
  texto: string
}
// `ProdutoTrocaOut` — o custo só vem para quem vê a Margem.
export type ProdutoTroca = {
  sku: string
  nome: string | null
  quantidade: number | null
  produto_id: number | null
  custo: number | null
  saldo_ao_vivo: number | null
}
// `AceitePossivelOut`: uma mensagem do cliente que vale como prova.
export type AceitePossivel = {
  id: string
  texto: string
  enviada_em: string | null
  // Só a dica (veio depois da 1ª fala da loja): vale de um jeito ou de outro.
  depois_da_oferta: boolean
}
// `TrocaAbertaOut`: o resumo da troca aberta (sem custo), no cartão e na lista.
export type TrocaAberta = {
  id: string
  estado: string
  sku_antigo: string
  sku_novo: string
  nivel: number
  automatica: boolean
  criado_por_nome: string | null
  created_at: string | null
  codigo_erro: string | null
  erro: string | null
  pode_retomar: boolean
}
// `PreviaTrocaOut`.
export type PreviaTroca = {
  pode: boolean
  travas: TravaTroca[]
  ao_vivo: boolean
  numero_bling: string
  bling_id: number | null
  numeroloja: string | null
  plataforma: string | null
  conversa_id: string | null
  nivel: number | null
  mesmo_produto: boolean
  exige_aceite: boolean
  antes: ProdutoTroca
  depois: ProdutoTroca
  valor_unitario: number | null
  observacao: string
  passos_previstos: string[]
  aviso_nf: string | null
  aviso_prazo: string | null
  dif_custo_pct: number | null
  aceites_possiveis: AceitePossivel[]
  aceite_max_dias: number
  previa_hash: string | null
  troca_aberta: TrocaAberta | null
  lido_em: string
}
// `PassoTrocaOut`.
export type PassoTroca = {
  passo: string
  em: string | null
  ok: boolean
  detalhe: string | null
}
// `TrocaOut`.
export type Troca = {
  id: string
  pedido_bling: string
  bling_id: number
  numeroloja: string | null
  plataforma: string | null
  conversa_id: string | null
  motivo_codigo: string
  sku_antigo: string
  sku_novo: string
  produto_novo_id: number
  descricao_nova: string | null
  quantidade: number
  valor_unitario: number | null
  nivel: number
  automatica: boolean
  custo_antigo: number | null
  custo_novo: number | null
  saldo_ao_vivo: number | null
  aceite_fonte: string | null
  mensagem_aceite_id: string | null
  aceite_texto: string | null
  aceite_em: string | null
  estado: string
  aberta: boolean
  pode_retomar: boolean
  codigo_erro: string | null
  erro: string | null
  passos: PassoTroca[]
  criado_por_nome: string
  created_at: string | null
  concluida_em: string | null
}
// `PreviaTrocaIn`.
export type PreviaTrocaIn = {
  sku_antigo: string
  sku_novo: string
  conversa_id?: string | null
}
// `AceiteIn`: a PROVA (opcional) — a mensagem do cliente OU o Duoke.
export type AceiteIn = {
  mensagem_aceite_id?: string | null
  fonte?: 'davinci' | 'duoke' | null
  texto?: string | null
  // "AAAA-MM-DDTHH:MM", sem fuso: o backend lê como horário de Brasília.
  em?: string | null
}
// `TrocaIn` (herda o `PreviaTrocaIn`).
export type TrocaIn = {
  sku_antigo: string
  sku_novo: string
  conversa_id?: string | null
  aceite?: AceiteIn | null
  previa_hash: string
  idem_key: string
  confirmar: boolean
}

// A sugestão que a pessoa escolheu ("Trocar"), da sugestão da 4b.
export type EscolhaTroca = {
  sku_antigo: string
  sku_novo: string
  nome: string | null
  nivel: number
  mesmo_produto: boolean
}
// A prova do aceite que a pessoa escolheu no diálogo.
export type Prova = 'nenhuma' | 'mensagem' | 'duoke'

// O teto do texto colado do Duoke (`troca.ACEITE_TEXTO_MAX`).
export const DUOKE_MAX = 2000
// A folga do relógio para a hora do Duoke (`troca._FOLGA_RELOGIO`).
const FOLGA_FUTURO_MS = 5 * 60_000

// Cada estado da troca (`troca.ESTADOS`) numa frase curta. Aberta = tudo
// menos concluída e abortada (o backend manda `aberta`).
export const ESTADO_TROCA: Record<string, string> = {
  iniciada: 'Iniciada',
  item_trocado: 'Item trocado; ainda em Ag. Cancelamento',
  em_atendido: 'Item trocado; parado em Atendido (9)',
  em_aberto: 'Em aberto (6); a NF ainda não voltou à fila',
  nf_liberada: 'Em aberto e NF de volta à fila; falta o fechamento',
  concluida: 'Concluída',
  abortada: 'Abortada',
  incerta: 'Incerta: não sei se o Bling trocou o item',
}
export function rotuloEstado(estado: string | null | undefined): string {
  const e = (estado || '').trim()
  return ESTADO_TROCA[e] || e || 'sem estado'
}
// O tom do resultado: concluída verde, abortada vermelha, o resto parado (âmbar).
export function tomEstado(estado: string | null | undefined): 'ok' | 'erro' | 'parada' {
  if (estado === 'concluida') return 'ok'
  if (estado === 'abortada') return 'erro'
  return 'parada'
}

// Cada passo que o serviço grava em `passos` (o `passo` de `_passo`,
// `_parar` e `_erro_interno`). Passo novo que a tela não conhece: o código.
export const ROTULO_PASSO: Record<string, string> = {
  iniciada: 'Troca registrada',
  conferencia: 'Conferência ao vivo',
  put: 'Item e observação no Bling',
  item_trocado: 'Espelho, trilha e Margem no DaVinci',
  situacao: 'Situação no Bling',
  situacao_9: 'Ag. Cancelamento → Atendido (9)',
  situacao_6: 'Em aberto (6)',
  nf_liberada: 'NF de volta à fila',
  concluida: 'Nota na conversa e etiqueta',
  abortada: 'Abortada',
  retomar: 'Retomada',
  executar: 'Erro do DaVinci',
}
export function rotuloPasso(passo: string | null | undefined): string {
  const p = (passo || '').trim()
  return ROTULO_PASSO[p] || p
}

// O que a recusa diz quando o backend manda só o código (o 404 do escopo).
export const TEXTO_SEM_DETALHE: Record<string, string> = {
  pedido_nao_encontrado: 'Pedido não encontrado no DaVinci (ou de uma loja fora da sua equipe).',
  troca_nao_encontrada: 'Troca não encontrada (pode ter sido de outro pedido).',
}

// O botão do clique: o lote irmão é "Trocar lote" (sem aceite).
export function rotuloBotao(nivel: number | null | undefined): string {
  return nivel === 0 ? 'Trocar lote e voltar para Em aberto' : 'Trocar e voltar para Em aberto'
}
// O título do diálogo.
export function tituloDialogo(nivel: number | null | undefined, numero: string): string {
  return `${nivel === 0 ? 'Trocar lote' : 'Trocar produto'} do pedido ${numero}`
}

// Quem pode trocar e quem pode mandar a oferta: a MESMA regra (decisão (g)
// do Eduardo, 07/10/2026) — quem mexe na caixa (`atendimento_mexe`) com
// atendimento.edit e margem.edit. Trocar aprova a Margem; a oferta promete a
// troca ao comprador (quem não pode trocar não oferece). A API recusa igual
// (troca.pode_escrever).
export function acessoDaTroca(a: { mexe: boolean; editaAtendimento: boolean; editaMargem: boolean }): { trocar: boolean; ofertar: boolean } {
  const escreve = a.mexe === true && a.editaAtendimento === true && a.editaMargem === true
  return { trocar: escreve, ofertar: escreve }
}
// O mesmo critério da página (pages/atendimento.vue: `mexe` + useCan), para
// o cartão e a lista, que não recebem o `canEdit` da caixa.
export function useAcessoDaTroca() {
  const auth = useAuthStore()
  const editaAtendimento = useCan('atendimento', 'edit')
  const editaMargem = useCan('margem', 'edit')
  return computed(() => acessoDaTroca({
    mexe: auth.user?.atendimento_mexe === true,
    editaAtendimento: editaAtendimento.value,
    editaMargem: editaMargem.value,
  }))
}

// Uma chave por abertura do diálogo (o `idem_key` do TrocaIn). Fora de HTTPS
// (o IP da rede local) o `randomUUID` não existe: monta o v4 na mão.
export function novaIdemKey(): string {
  const c: any = (globalThis as any).crypto
  if (c && typeof c.randomUUID === 'function') return c.randomUUID()
  const b = new Uint8Array(16)
  if (c && typeof c.getRandomValues === 'function') c.getRandomValues(b)
  else for (let i = 0; i < 16; i++) b[i] = Math.floor(Math.random() * 256)
  b[6] = (b[6] & 0x0f) | 0x40
  b[8] = (b[8] & 0x3f) | 0x80
  const h = [...b].map((x) => x.toString(16).padStart(2, '0')).join('')
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`
}

const BRT = 'America/Sao_Paulo'
// "dd/mm HH:MM" no horário de Brasília; '' sem data.
export function dataHora(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const dia = d.toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit', timeZone: BRT })
  const hora = d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit', timeZone: BRT })
  return `${dia} ${hora}`
}
// "R$ 89,90"; '' sem valor.
export function moeda(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return ''
  return Number(v).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })
}
// O "agora" de Brasília no formato do <input type="datetime-local"> (o teto).
export function agoraLocal(agora: number): string {
  const p = Object.fromEntries(
    new Intl.DateTimeFormat('en-GB', { timeZone: BRT, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
      .formatToParts(new Date(agora))
      .map((x) => [x.type, x.value]),
  )
  return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}`
}
// O valor do datetime-local ("AAAA-MM-DDTHH:MM", horário de Brasília) em ms.
// Brasília não tem horário de verão desde 2019: −03:00 fixo.
export function instanteLocal(v: string | null | undefined): number | null {
  const m = (v || '').trim().match(/^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})(:\d{2})?$/)
  if (!m) return null
  const t = Date.parse(`${m[1]}${m[2] || ':00'}-03:00`)
  return Number.isNaN(t) ? null : t
}

// A resposta colada do Duoke: o que o backend recusaria, dito antes. null = ok.
export function problemaDuoke(texto: string, em: string, agora: number, maxDias: number): string | null {
  const t = (texto || '').trim()
  if (!t) return 'Cole a resposta do cliente no Duoke.'
  if (t.length > DUOKE_MAX) return `A resposta colada passa de ${DUOKE_MAX} caracteres.`
  if (!(em || '').trim()) return 'Informe a data e a hora da resposta no Duoke.'
  const quando = instanteLocal(em)
  if (quando === null) return 'A data e a hora da resposta não estão certas.'
  if (quando > agora + FOLGA_FUTURO_MS) return 'A data da resposta no Duoke está no futuro.'
  if (quando < agora - maxDias * 86_400_000) return `A resposta tem mais de ${maxDias} dias: não vale como prova.`
  return null
}

export type EstadoClique = {
  previa: PreviaTroca | null
  carregando: boolean
  // Uma recusa depois da prévia: a próxima tentativa pede a prévia de novo.
  precisaNovaPrevia: boolean
  confirmar: boolean
  prova: Prova
  mensagemId: string | null
  duokeTexto: string
  duokeEm: string
  agora: number
}
// Por que o botão do clique está desligado (null = pode clicar). PURA.
export function motivoSemClique(e: EstadoClique): string | null {
  const p = e.previa
  if (e.carregando) return 'Conferindo a troca no Bling…'
  if (!p) return 'Sem a prévia da troca: confira de novo.'
  if (e.precisaNovaPrevia) return 'Confira a prévia de novo antes de trocar.'
  if (!p.pode) return p.travas.find((t) => !t.ok)?.texto || 'A troca não pode ser feita agora.'
  if (!p.previa_hash) return 'Sem a conferência ao vivo: confira de novo.'
  if (!p.exige_aceite) return null
  if (!e.confirmar) return 'Marque que o cliente aceitou a troca.'
  if (e.prova === 'mensagem' && !p.aceites_possiveis.some((a) => a.id === e.mensagemId)) return 'Escolha a mensagem do cliente (ou deixe sem prova).'
  if (e.prova === 'duoke') return problemaDuoke(e.duokeTexto, e.duokeEm, e.agora, p.aceite_max_dias)
  return null
}

// A prova que vai no corpo (null = sem prova: `declarado`, com o nome de quem clicou).
export function aceiteDe(prova: Prova, mensagemId: string | null, duokeTexto: string, duokeEm: string): AceiteIn | null {
  if (prova === 'mensagem' && mensagemId) return { mensagem_aceite_id: mensagemId, fonte: 'davinci' }
  if (prova === 'duoke') return { fonte: 'duoke', texto: duokeTexto.trim(), em: duokeEm.trim() }
  return null
}
// O corpo do clique "Trocar" (`TrocaIn`). PURA. A caixinha só vale nos
// níveis que pedem aceite; no lote irmão vai sem aceite e sem caixinha.
export function corpoDaTroca(a: {
  escolha: EscolhaTroca
  previa: PreviaTroca
  conversaId: string | null
  idemKey: string
  confirmar: boolean
  prova: Prova
  mensagemId: string | null
  duokeTexto: string
  duokeEm: string
}): TrocaIn {
  const exige = a.previa.exige_aceite
  return {
    sku_antigo: a.escolha.sku_antigo,
    sku_novo: a.escolha.sku_novo,
    // A conversa que a prévia usou: a das provas do aceite.
    conversa_id: a.previa.conversa_id || a.conversaId || null,
    aceite: exige ? aceiteDe(a.prova, a.mensagemId, a.duokeTexto, a.duokeEm) : null,
    previa_hash: a.previa.previa_hash || '',
    idem_key: a.idemKey,
    confirmar: exige ? a.confirmar === true : false,
  }
}

// O aceite registrado, numa frase (o resultado e o cartão).
export function textoAceite(t: Pick<Troca, 'aceite_fonte' | 'aceite_em' | 'criado_por_nome' | 'nivel'>): string | null {
  const em = dataHora(t.aceite_em)
  const quando = em ? ` (${em})` : ''
  if (t.aceite_fonte === 'davinci') return `Aceite: mensagem do cliente no DaVinci${quando}`
  if (t.aceite_fonte === 'duoke') return `Aceite: resposta do cliente no Duoke${quando}`
  if (t.aceite_fonte === 'declarado') return `Aceite declarado por ${t.criado_por_nome}${quando}`
  return t.nivel === 0 ? 'Mesmo produto, outro lote: sem aceite.' : null
}

// Quem conduz a troca aberta (o cartão e a lista).
export function quemTroca(t: Pick<TrocaAberta, 'automatica' | 'criado_por_nome' | 'created_at'>): string {
  const quem = t.automatica ? 'robô de lote' : (t.criado_por_nome || 'alguém da equipe')
  const em = dataHora(t.created_at)
  return em ? `${quem}, ${em}` : quem
}

export type ErroTroca = {
  code: string | null
  texto: string
  motivos: string[]
  // A troca que a recusa deixou `abortada` (o 409 traz o id).
  trocaId: string | null
  // O backend respondeu (4xx): a decisão foi dada — a próxima é outra troca.
  definitiva: boolean
}
// O erro da API em ErroTroca. `lido` é o `erroDaApi` da caixa (as frases);
// o código e o `troca_id` vêm do `detail`. PURA.
export function erroDaTroca(e: any, lido: { texto: string; motivos: string[] }, status: number): ErroTroca {
  const d = e?.data?.detail
  const obj = d && typeof d === 'object' && !Array.isArray(d) ? d : null
  const code = obj && typeof obj.code === 'string' ? obj.code : null
  const texto = code && lido.texto === code && TEXTO_SEM_DETALHE[code] ? TEXTO_SEM_DETALHE[code] : lido.texto
  return {
    code,
    texto,
    motivos: lido.motivos,
    trocaId: obj && typeof obj.troca_id === 'string' ? obj.troca_id : null,
    definitiva: status >= 400 && status < 500,
  }
}
</script>

<script setup lang="ts">
import { ArrowRight, Check, CheckCircle2, Loader2, RotateCcw, Shuffle, TriangleAlert, X, XCircle } from 'lucide-vue-next'
import { erroDaApi, statusDoErro } from '~/components/AtendimentoPlataforma.vue'
import { textoCusto } from '~/components/AtendimentoTrocaSugestoes.vue'

const props = withDefaults(defineProps<{
  // O nº do pedido no Bling.
  numero: string
  // A sugestão escolhida no "Trocar"; null = abrir só a troca aberta (Retomar).
  escolha?: EscolhaTroca | null
  // A conversa de onde se abriu (as provas do aceite saem dela).
  conversaId?: string | null
  // A troca aberta do pedido (o resumo do cartão/lista): abre no resultado.
  trocaAberta?: TrocaAberta | null
}>(), { escolha: null, conversaId: null, trocaAberta: null })
const aberto = defineModel<boolean>('aberto', { required: true })
// `mudou` só ao FECHAR, se algo foi escrito: reler o painel antes faria o
// cartão (e este diálogo com o resultado) sumir — o pedido sai de 83955.
const emit = defineEmits<{
  (e: 'mudou'): void
}>()

const { api } = useApi()
const previa = ref<PreviaTroca | null>(null)
const carregando = ref(false)
const erroPrevia = ref<ErroTroca | null>(null)
const precisaNovaPrevia = ref(false)
const confirmar = ref(false)
const prova = ref<Prova>('nenhuma')
const mensagemId = ref<string | null>(null)
const duokeTexto = ref('')
const duokeEm = ref('')
const agora = ref(Date.now())
const idemKey = ref(novaIdemKey())
const enviando = ref(false)
const retomando = ref(false)
const troca = ref<Troca | null>(null)
const erroTroca = ref<ErroTroca | null>(null)
// Algo foi escrito (a troca, o retomar, a recusa que abortou a linha).
let houveEscrita = false

const nivel = computed(() => previa.value?.nivel ?? props.escolha?.nivel ?? troca.value?.nivel ?? props.trocaAberta?.nivel ?? null)
const titulo = computed(() => tituloDialogo(nivel.value, props.numero))
const semClique = computed(() => motivoSemClique({
  previa: previa.value,
  carregando: carregando.value,
  precisaNovaPrevia: precisaNovaPrevia.value,
  confirmar: confirmar.value,
  prova: prova.value,
  mensagemId: mensagemId.value,
  duokeTexto: duokeTexto.value,
  duokeEm: duokeEm.value,
  agora: agora.value,
}))
const travasFalhas = computed(() => (previa.value?.travas ?? []).filter((t) => !t.ok))
const custo = computed(() => textoCusto(previa.value?.dif_custo_pct))
const maxEm = computed(() => agoraLocal(agora.value))
const ocupado = computed(() => enviando.value || retomando.value)

let geracao = 0
async function lerPrevia() {
  const esc = props.escolha
  if (!esc) return
  const g = ++geracao
  carregando.value = true
  erroPrevia.value = null
  agora.value = Date.now()
  try {
    const body: PreviaTrocaIn = { sku_antigo: esc.sku_antigo, sku_novo: esc.sku_novo, conversa_id: props.conversaId || null }
    const r = await api<PreviaTroca>(urlPrevia(props.numero), { method: 'POST', body })
    if (g !== geracao) return
    previa.value = r
    precisaNovaPrevia.value = false
    // A recusa de antes era da prévia velha.
    erroTroca.value = null
    // A mensagem escolhida que saiu da janela não vale mais.
    if (mensagemId.value && !r.aceites_possiveis.some((a) => a.id === mensagemId.value)) mensagemId.value = null
  } catch (e: any) {
    if (g !== geracao) return
    previa.value = null
    erroPrevia.value = erroDaTroca(e, erroDaApi(e, 'Não consegui conferir a troca agora'), statusDoErro(e))
  } finally {
    if (g === geracao) carregando.value = false
  }
}

// A troca aberta do pedido, inteira (com os passos): GET /trocas?pedido=.
async function lerTroca(id: string | null) {
  carregando.value = true
  erroTroca.value = null
  try {
    const r = await api<{ itens: Troca[] }>(URL_TROCAS, { query: { abertas: false, pedido: props.numero } })
    const achada = (r?.itens ?? []).find((t) => (id ? t.id === id : t.aberta)) ?? null
    if (achada) troca.value = achada
    else erroTroca.value = { code: 'troca_nao_encontrada', texto: TEXTO_SEM_DETALHE.troca_nao_encontrada, motivos: [], trocaId: null, definitiva: true }
  } catch (e: any) {
    erroTroca.value = erroDaTroca(e, erroDaApi(e, 'Não consegui ler a troca agora'), statusDoErro(e))
  } finally {
    carregando.value = false
  }
}

async function trocar() {
  const esc = props.escolha
  const p = previa.value
  if (!esc || !p || semClique.value || ocupado.value) return
  enviando.value = true
  erroTroca.value = null
  try {
    const body = corpoDaTroca({
      escolha: esc,
      previa: p,
      conversaId: props.conversaId || null,
      idemKey: idemKey.value,
      confirmar: confirmar.value,
      prova: prova.value,
      mensagemId: mensagemId.value,
      duokeTexto: duokeTexto.value,
      duokeEm: duokeEm.value,
    })
    troca.value = await api<Troca>(urlTroca(props.numero), { method: 'POST', body })
    houveEscrita = true
  } catch (e: any) {
    const er = erroDaTroca(e, erroDaApi(e, 'Não consegui fazer a troca agora'), statusDoErro(e))
    erroTroca.value = er
    // O backend recusou: a próxima tentativa é outra decisão (prévia nova,
    // chave nova). Sem resposta (rede, 5xx), a MESMA chave: repetir o clique
    // devolve a troca que talvez já exista, nunca uma segunda.
    if (er.definitiva) {
      idemKey.value = novaIdemKey()
      precisaNovaPrevia.value = true
    }
    if (er.trocaId) houveEscrita = true
  } finally {
    enviando.value = false
  }
}

async function retomar() {
  const id = troca.value?.id
  if (!id || ocupado.value) return
  retomando.value = true
  erroTroca.value = null
  try {
    troca.value = await api<Troca>(urlRetomar(id), { method: 'POST' })
    houveEscrita = true
  } catch (e: any) {
    erroTroca.value = erroDaTroca(e, erroDaApi(e, 'Não consegui retomar a troca agora'), statusDoErro(e))
  } finally {
    retomando.value = false
  }
}

// Aberto: chave nova, e a prévia (ou a troca aberta, no Retomar). O Esc fecha.
const caixa = ref<HTMLElement | null>(null)
watch(aberto, (v) => {
  if (!v) return
  idemKey.value = novaIdemKey()
  houveEscrita = false
  troca.value = null
  previa.value = null
  erroPrevia.value = null
  erroTroca.value = null
  precisaNovaPrevia.value = false
  confirmar.value = false
  prova.value = 'nenhuma'
  mensagemId.value = null
  duokeTexto.value = ''
  duokeEm.value = ''
  if (props.escolha) void lerPrevia()
  else if (props.trocaAberta) void lerTroca(props.trocaAberta.id)
  void nextTick(() => caixa.value?.focus())
}, { immediate: true })

function fechar() {
  // No meio de um clique o diálogo não fecha: o resultado é o que diz o que mudou.
  if (ocupado.value) return
  aberto.value = false
  if (houveEscrita) emit('mudou')
}
</script>

<template>
  <div
    v-if="aberto"
    class="fixed inset-0 z-[80] flex items-stretch justify-center bg-black/50 sm:items-center sm:p-4"
    data-dialogo-troca
    @click.self="fechar"
    @keydown.esc.stop="fechar"
  >
    <div
      ref="caixa"
      role="dialog"
      aria-modal="true"
      :aria-label="titulo"
      tabindex="-1"
      class="flex max-h-full w-full flex-col bg-background text-xs shadow-2xl sm:max-h-[88vh] sm:max-w-xl sm:rounded-lg sm:border"
    >
      <header class="flex shrink-0 items-start gap-2 border-b px-4 py-3">
        <Shuffle class="mt-0.5 size-4 shrink-0 text-muted-foreground" />
        <div class="min-w-0 flex-1">
          <h2 class="text-sm font-semibold">{{ titulo }}</h2>
          <p class="text-[11px] text-muted-foreground">O cliente paga o mesmo. Tudo é conferido ao vivo no Bling antes de mudar o pedido.</p>
        </div>
        <button type="button" class="rounded p-1 hover:bg-muted disabled:opacity-50" aria-label="fechar" :disabled="ocupado" @click="fechar">
          <X class="size-4" />
        </button>
      </header>

      <div class="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-3">
        <div v-if="carregando && !previa && !troca" class="flex items-center gap-2 text-muted-foreground" aria-busy="true">
          <Loader2 class="size-4 animate-spin" /> {{ escolha ? 'conferindo ao vivo no Bling e na Shopee…' : 'lendo a troca…' }}
        </div>
        <div v-if="erroPrevia" class="rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-red-700 dark:text-red-300" data-erro-previa :data-code="erroPrevia.code">
          <p>{{ erroPrevia.texto }}</p>
          <p v-for="(m, i) in erroPrevia.motivos" :key="i">{{ m }}</p>
        </div>

        <!-- ─── o resultado (depois do clique, ou a troca aberta no Retomar) ─── -->
        <section v-if="troca" class="space-y-2" data-resultado-troca :data-estado="troca.estado">
          <div
            class="flex items-start gap-1.5 rounded-md border px-3 py-2"
            :class="tomEstado(troca.estado) === 'ok' ? 'border-emerald-500/40 bg-emerald-500/10' : tomEstado(troca.estado) === 'erro' ? 'border-red-500/40 bg-red-500/10' : 'border-amber-500/40 bg-amber-500/10'"
          >
            <CheckCircle2 v-if="tomEstado(troca.estado) === 'ok'" class="mt-px size-4 shrink-0 text-emerald-600 dark:text-emerald-400" />
            <XCircle v-else-if="tomEstado(troca.estado) === 'erro'" class="mt-px size-4 shrink-0 text-red-600 dark:text-red-400" />
            <TriangleAlert v-else class="mt-px size-4 shrink-0 text-amber-600 dark:text-amber-400" />
            <div class="min-w-0 flex-1 space-y-0.5">
              <p class="font-medium">{{ rotuloEstado(troca.estado) }}</p>
              <p class="break-words">
                <span class="font-mono">{{ troca.sku_antigo }}</span> → <span class="font-mono">{{ troca.sku_novo }}</span><template v-if="troca.quantidade > 1"> ×{{ troca.quantidade }}</template>
                <span class="text-muted-foreground"> · {{ troca.automatica ? 'robô de lote' : troca.criado_por_nome }}</span>
              </p>
              <p v-if="textoAceite(troca)" class="text-muted-foreground">{{ textoAceite(troca) }}</p>
              <p v-if="troca.erro" class="break-words" data-erro-da-troca>{{ troca.erro }}</p>
            </div>
          </div>
          <ol class="space-y-0.5" aria-label="passos da troca">
            <li v-for="(p, i) in troca.passos" :key="i" class="flex items-start gap-1.5" data-passo :data-ok="p.ok">
              <Check v-if="p.ok" class="mt-px size-3.5 shrink-0 text-emerald-600" />
              <X v-else class="mt-px size-3.5 shrink-0 text-red-600" />
              <span class="min-w-0 flex-1 break-words">
                <span class="font-medium">{{ rotuloPasso(p.passo) }}</span><template v-if="p.detalhe">: {{ p.detalhe }}</template>
              </span>
              <span class="shrink-0 tabular-nums text-[10px] text-muted-foreground">{{ dataHora(p.em) }}</span>
            </li>
          </ol>
          <p v-if="troca.aberta && !troca.pode_retomar" class="text-muted-foreground" data-conduzindo>
            Alguém está conduzindo esta troca agora — espere um pouco e leia de novo.
          </p>
          <p v-else-if="troca.aberta && troca.estado === 'incerta'" class="text-muted-foreground">
            O Retomar confere por GET no Bling e decide; nunca repete a troca do item.
          </p>
          <p v-else-if="troca.aberta" class="text-muted-foreground">
            Parada no meio: o Retomar segue de onde parou (lê o Bling primeiro e só anda para frente).
          </p>
        </section>

        <!-- ─── a prévia ─── -->
        <section v-else-if="previa" class="space-y-3" data-previa :data-nivel="previa.nivel">
          <div class="grid gap-2 sm:grid-cols-[1fr_auto_1fr] sm:items-stretch">
            <div class="min-w-0 rounded-md border bg-muted/30 px-2.5 py-2" data-antes>
              <p class="text-[10px] uppercase tracking-wide text-muted-foreground">sai</p>
              <p class="break-words font-mono">{{ previa.antes.sku }}<template v-if="(previa.antes.quantidade ?? 1) > 1"> ×{{ previa.antes.quantidade }}</template></p>
              <p v-if="previa.antes.nome" class="break-words">{{ previa.antes.nome }}</p>
              <p v-if="previa.antes.custo !== null" class="text-[10px] text-muted-foreground">custo {{ moeda(previa.antes.custo) }}</p>
            </div>
            <ArrowRight class="mx-auto hidden size-4 self-center text-muted-foreground sm:block" />
            <div class="min-w-0 rounded-md border border-emerald-500/40 bg-emerald-500/5 px-2.5 py-2" data-depois>
              <p class="text-[10px] uppercase tracking-wide text-muted-foreground">entra</p>
              <p class="break-words font-mono">{{ previa.depois.sku }}<template v-if="(previa.depois.quantidade ?? 1) > 1"> ×{{ previa.depois.quantidade }}</template></p>
              <p v-if="previa.depois.nome" class="break-words">{{ previa.depois.nome }}</p>
              <p class="text-[10px] text-muted-foreground">
                <template v-if="previa.depois.saldo_ao_vivo !== null">saldo no Bling agora: {{ previa.depois.saldo_ao_vivo }}</template>
                <template v-if="previa.depois.custo !== null"> · custo {{ moeda(previa.depois.custo) }}</template>
              </p>
            </div>
          </div>
          <p data-valor>
            <template v-if="previa.valor_unitario !== null">Valor mantido: <span class="font-medium">{{ moeda(previa.valor_unitario) }}</span> por unidade — o cliente paga o mesmo.</template>
            <template v-else>Valor mantido: o item novo entra com o mesmo valor do antigo.</template>
            <span v-if="custo" class="text-muted-foreground"> Nosso custo: {{ custo }}.</span>
          </p>
          <p v-if="previa.nivel === 0" class="text-muted-foreground">Mesmo produto, outro lote: a troca não pede o aceite do cliente.</p>

          <div class="space-y-0.5">
            <p class="text-[11px] text-muted-foreground">Vai nas Observações do Bling:</p>
            <p class="whitespace-pre-wrap break-words rounded bg-muted/50 px-2 py-1 font-mono text-[11px]" data-observacao>{{ previa.observacao }}</p>
          </div>

          <div class="space-y-0.5">
            <p class="text-[11px] text-muted-foreground">O que vai acontecer, nesta ordem:</p>
            <ol class="list-decimal space-y-0.5 pl-5">
              <li v-for="(p, i) in previa.passos_previstos" :key="i" data-passo-previsto>{{ p }}</li>
            </ol>
          </div>
          <p v-if="previa.aviso_nf" class="text-amber-800 dark:text-amber-300" data-aviso-nf>{{ previa.aviso_nf }}</p>
          <p v-if="previa.aviso_prazo" class="text-amber-800 dark:text-amber-300" data-aviso-prazo>{{ previa.aviso_prazo }}</p>

          <div class="space-y-0.5">
            <p class="text-[11px] text-muted-foreground">
              Conferência<template v-if="previa.lido_em"> ({{ dataHora(previa.lido_em) }})</template>:
              <template v-if="!previa.ao_vivo"> a parte ao vivo só roda com as travas do DaVinci em ordem.</template>
            </p>
            <ul class="space-y-0.5">
              <li v-for="t in previa.travas" :key="t.code" class="flex items-start gap-1.5" data-trava :data-code="t.code" :data-ok="t.ok">
                <Check v-if="t.ok" class="mt-px size-3.5 shrink-0 text-emerald-600" />
                <X v-else class="mt-px size-3.5 shrink-0 text-red-600" />
                <span class="min-w-0 break-words" :class="t.ok ? 'text-muted-foreground' : 'font-medium text-red-700 dark:text-red-300'">{{ t.texto }}</span>
              </li>
            </ul>
          </div>
          <div v-if="previa.troca_aberta" class="flex flex-wrap items-center gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2">
            <span class="min-w-0 flex-1">Já há uma troca em andamento neste pedido: {{ rotuloEstado(previa.troca_aberta.estado) }}.</span>
            <button type="button" class="rounded-md border px-2 py-1 hover:bg-muted" data-ver-troca-aberta @click="lerTroca(previa.troca_aberta.id)">ver a troca</button>
          </div>

          <!-- O aceite (níveis 1 e 2): a caixinha é obrigatória; a prova, opcional. -->
          <fieldset v-if="previa.exige_aceite && previa.pode" class="space-y-2 rounded-md border px-3 py-2" data-aceite>
            <legend class="px-1 text-[11px] font-medium">Aceite do cliente</legend>
            <label class="flex items-start gap-2 font-medium">
              <input v-model="confirmar" type="checkbox" class="mt-0.5" required data-caixinha-aceite>
              <span>O cliente aceitou a troca <span class="font-normal text-muted-foreground">(obrigatório)</span></span>
            </label>
            <div class="space-y-1">
              <p class="text-[11px] text-muted-foreground">Prova do aceite (opcional):</p>
              <label class="flex items-center gap-2">
                <input v-model="prova" type="radio" value="nenhuma" name="prova-aceite" data-prova="nenhuma">
                <span>Sem prova: fica registrado o seu nome</span>
              </label>
              <label class="flex items-center gap-2" :class="!previa.aceites_possiveis.length && 'opacity-60'">
                <input v-model="prova" type="radio" value="mensagem" name="prova-aceite" :disabled="!previa.aceites_possiveis.length" data-prova="mensagem">
                <span>Uma mensagem do cliente nesta conversa<template v-if="!previa.aceites_possiveis.length"> (nenhuma nos últimos {{ previa.aceite_max_dias }} dias)</template></span>
              </label>
              <ul v-if="prova === 'mensagem'" class="ml-5 space-y-1">
                <li v-for="a in previa.aceites_possiveis" :key="a.id">
                  <label class="flex items-start gap-2 rounded border px-2 py-1" :class="mensagemId === a.id && 'border-primary bg-primary/5'" data-aceite-possivel :data-id="a.id">
                    <input v-model="mensagemId" type="radio" :value="a.id" name="mensagem-aceite" class="mt-0.5">
                    <span class="min-w-0 flex-1">
                      <span class="block break-words">{{ a.texto }}</span>
                      <span class="text-[10px] text-muted-foreground">{{ dataHora(a.enviada_em) }}<template v-if="a.depois_da_oferta"> · depois da oferta da loja</template></span>
                    </span>
                  </label>
                </li>
              </ul>
              <label class="flex items-center gap-2">
                <input v-model="prova" type="radio" value="duoke" name="prova-aceite" data-prova="duoke">
                <span>A resposta do cliente no Duoke</span>
              </label>
              <div v-if="prova === 'duoke'" class="ml-5 space-y-1" data-duoke>
                <textarea
                  v-model="duokeTexto"
                  :maxlength="DUOKE_MAX"
                  rows="3"
                  class="block w-full resize-y rounded-md border bg-background px-2 py-1"
                  placeholder="Cole aqui a resposta do cliente, como está no Duoke"
                  aria-label="resposta do cliente no Duoke"
                />
                <label class="flex flex-wrap items-center gap-2">
                  <span class="text-[11px] text-muted-foreground">Data e hora da resposta (Brasília):</span>
                  <input v-model="duokeEm" type="datetime-local" :max="maxEm" class="rounded-md border bg-background px-1.5 py-0.5" aria-label="data e hora da resposta no Duoke">
                </label>
              </div>
            </div>
          </fieldset>
        </section>

        <div v-if="erroTroca" class="rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-red-700 dark:text-red-300" data-erro-troca :data-code="erroTroca.code">
          <p>{{ erroTroca.texto }}</p>
          <p v-for="(m, i) in erroTroca.motivos" :key="i">{{ m }}</p>
          <p v-if="precisaNovaPrevia && escolha" class="text-[11px]">Nada foi feito com este clique. Confira a prévia de novo para tentar outra vez.</p>
          <!-- A recusa aponta uma troca (a que abortou, ou a que já estava aberta): os passos dela. -->
          <button
            v-if="erroTroca.trocaId && !troca"
            type="button"
            class="mt-1 rounded-md border border-current/30 px-2 py-0.5 text-[11px] hover:bg-red-500/10"
            data-ver-troca-do-erro
            @click="lerTroca(erroTroca.trocaId)"
          >
            ver a troca
          </button>
        </div>
      </div>

      <footer class="flex shrink-0 flex-wrap items-center justify-end gap-2 border-t px-4 py-3">
        <template v-if="troca">
          <button
            v-if="troca.aberta && !troca.pode_retomar"
            type="button"
            class="inline-flex items-center gap-1 rounded-md border px-2.5 py-1.5 hover:bg-muted disabled:opacity-60"
            :disabled="carregando"
            data-ler-de-novo
            @click="lerTroca(troca.id)"
          >
            <RotateCcw class="size-3.5" /> Ler de novo
          </button>
          <button
            v-if="troca.aberta && troca.pode_retomar"
            type="button"
            class="inline-flex items-center gap-1 rounded-md bg-primary px-3 py-1.5 font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-60"
            :disabled="ocupado"
            data-botao-retomar
            @click="retomar"
          >
            <Loader2 v-if="retomando" class="size-3.5 animate-spin" />
            <RotateCcw v-else class="size-3.5" /> Retomar
          </button>
          <button type="button" class="rounded-md border px-2.5 py-1.5 hover:bg-muted disabled:opacity-60" :disabled="ocupado" @click="fechar">Fechar</button>
        </template>
        <template v-else-if="escolha">
          <p v-if="semClique && previa && !carregando" class="mr-auto min-w-0 flex-1 text-[11px] text-muted-foreground" data-motivo-sem-clique>{{ semClique }}</p>
          <button
            type="button"
            class="inline-flex items-center gap-1 rounded-md border px-2.5 py-1.5 hover:bg-muted disabled:opacity-60"
            :disabled="carregando || ocupado"
            data-conferir-de-novo
            @click="lerPrevia"
          >
            <Loader2 v-if="carregando" class="size-3.5 animate-spin" />
            <RotateCcw v-else class="size-3.5" /> Conferir de novo
          </button>
          <button
            type="button"
            class="inline-flex items-center gap-1 rounded-md bg-primary px-3 py-1.5 font-medium text-primary-foreground hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
            :disabled="!!semClique || ocupado"
            :title="semClique || ''"
            data-botao-trocar
            @click="trocar"
          >
            <Loader2 v-if="enviando" class="size-3.5 animate-spin" />
            <Shuffle v-else class="size-3.5" /> {{ rotuloBotao(nivel) }}
          </button>
        </template>
        <button v-else type="button" class="rounded-md border px-2.5 py-1.5 hover:bg-muted" @click="fechar">Fechar</button>
      </footer>
    </div>
  </div>
</template>
