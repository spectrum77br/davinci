<script setup lang="ts">
// Lançamento da devolução de UM pedido — o bloco "Lançamento" da janela do painel
// novo (pages/devolucoes-novo.vue, 16/11/2026).
//
// É o fluxo da aba Lançamentos da tela antiga (pages/devolucoes.vue: "adicionar
// pedido" + edição da linha lançada) trazido pra dentro da janela, um cartão por
// peça. As REGRAS são as de lá, sem mudança: mesmos endpoints, mesmos corpos,
// mesmas travas, mesmas perguntas e os mesmos avisos. O que muda é só a forma:
//   · salvar é explícito (botão "salvar" / "lançar"), não mais ao sair do campo;
//   · a condição aparece como Novo / Usado / Perdimento (Perdimento grava `Sucata`)
//     e a Manutenção virou a pergunta "Vai pra manutenção?";
//   · o Status Bling NÃO fica aqui (a janela tem o seletor dela).
import {
  AlertCircle,
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Loader2,
  Plus,
  RotateCcw,
  Trash2,
  Undo2,
  Wrench,
  X,
} from 'lucide-vue-next'
import { erroLinkMega } from '~/lib/linkMega'

const props = defineProps<{
  // Nº do pedido no Bling — a janela é de UM pedido.
  pedido: string
  // Painel do pedido: na Fraude o lançamento só sai com o vídeo (videoFraudeRequired).
  fila: 'acompanhamento' | 'fraude'
  // Vídeo do envio já respondido pela equipe: entra sozinho no Link envio.
  videoLink: string | null
  // Provas do pedido (16/11/2026, Vinicius: "fica duplicado… deixa só em provas;
  // daí no chamado ele leva vídeos e fotos"). Os dois links e as fotos são
  // informados UMA vez no bloco Provas da janela e valem pro pedido inteiro; os
  // cartões não têm mais esses campos. No banco continua igual (link e foto
  // moram no lançamento) — e o chamado já junta os do pedido inteiro.
  linkEnvio?: string
  linkAbertura?: string
  // Fotos escolhidas em Provas antes de existir lançamento: sobem junto com o 1º.
  fotos?: File[]
  // 16/11/2026 ("não tá duplicado o campo observação?"): a observação que vai no
  // texto do chamado mora no bloco Chamado da janela, uma só pro pedido — o
  // cartão não tem mais o campo. No banco segue por lançamento (`observacao`).
  observacao?: string
  canEdit: boolean
  canDelete: boolean
  isAdmin: boolean
}>()

// Depois de QUALQUER criação / alteração / exclusão / estoque / foto que deu certo:
// o pai recarrega a lista dele.
const emit = defineEmits<{
  (e: 'mudou'): void
  // As fotos que esperavam em Provas subiram com o lançamento: o pai esvazia a fila.
  (e: 'fotosEnviadas'): void
}>()

// ── tipos (os mesmos da tela antiga) ─────────────────────────────────────────
type BlingStockResult = {
  ok: boolean
  action: string
  sku: string | null
  bling_product_id: number | null
  message: string
}

type DevolucaoAnexo = {
  id: string
  filename: string
  content_type: string
  size_bytes: number
  ml_file_name: string | null
  created_at: string
}

type DevolutionRow = {
  id: string
  data: string | null
  pedido_bling: string | null
  pedido_marketplace: string | null
  conta: string
  cliente?: string | null
  situacao_bling_id?: number | null
  situacao_bling_nome?: string | null
  sku: string | null
  produtos: string | null
  custo_produto: number | null
  condicao_produto: string | null
  link_abertura: string | null
  reembolso: boolean
  motivo_devolucao: string | null
  link_envio: string | null
  custo_manutencao: number | null
  tecnico: string | null
  devolver_estoque: boolean
  manutencao: boolean
  observacao: string | null
  troca_sku: string | null
  troca_condicao: string | null
  estoque_suffix: string | null
  quantidade: number | null
  estoque_destino_sku: string | null
  estoque_nova_tag: string | null
  manutencao_destino: string | null
  tag: string | null
  data_devolvido_estoque: string | null
  prazo: string | null
  prazo_contestacao: string | null
  estoque_mov_sku: string | null
  estoque_mov_bling_id: number | null
  estoque_mov_action: string | null
  estoque_mov_qty: number | null
  estoque_mov_revertido_at: string | null
  // Resultado da entrada no Bling: ok | falhou | conferindo | sem_confirmacao | null.
  // `data_devolvido_estoque` é a hora do PEDIDO; quem diz se entrou é isto.
  estoque_status?: string | null
  estoque_erro?: string | null
  estoque_tentativa_at?: string | null
  estoque_conferir_sku?: string | null
  // "auto_retry" = o Bling recusou por excesso de chamadas e o DaVinci tenta de
  // novo sozinho a cada ~2 min (até 5 — estoque_auto_tentativas).
  estoque_conferir_action?: string | null
  estoque_auto_tentativas?: number | null
  created_at: string
  updated_at: string
  bling_stock_result?: BlingStockResult | null
  tem_chamado?: boolean
  chamado_numero?: string | null
  chamado_resolvido?: boolean | null
  // Abertura automática na plataforma (pendente | enviada | falhou | registrada) + motivo.
  chamado_ml_status?: string | null
  chamado_ml_erro?: string | null
  chamado_enviada_at?: string | null
  chamado_plataforma?: string | null
  // Só na resposta do PATCH que trocou o motivo com chamado aberto.
  chamado_troca_motivo?: string | null
  // Mensagem ao comprador pedindo a senha (motivo Bloqueado).
  senha_status?: string | null
  senha_enviada_at?: string | null
  senha_erro?: string | null
  anexos?: DevolucaoAnexo[]
}

// Campos extras coletados pelos modais antes de chamar o estoque no Bling.
type StockModalFields = {
  troca_sku: string | null
  troca_condicao: string | null
  estoque_suffix: string | null
  manutencao_destino: string | null
  estoque_destino_sku: string | null
  estoque_nova_tag: string | null
}

type LookupRow = {
  data: string | null
  pedido_bling: string | null
  pedido_marketplace: string | null
  conta: string
  sku: string | null
  produtos: string | null
  quantidade: number | null
  custo_produto: number | null
  nome_destinatario: string | null
  cep_destino: string | null
  ja_devolvido?: boolean
  situacao_bling_id?: number | null
  situacao_bling_nome?: string | null
}

// O que a pessoa edita no cartão. Os dois custos ficam como TEXTO enquanto ela
// digita e só viram número na hora de mandar: na tela antiga o campo era ligado
// direto ao número e uma vírgula ("12,50") apagava o valor no meio da digitação.
const CAMPOS = [
  'condicao_produto',
  'motivo_devolucao',
  'link_envio',
  'link_abertura',
  'custo_produto',
  'custo_manutencao',
  'tecnico',
  'observacao',
] as const
type Campo = (typeof CAMPOS)[number]
type Campos = Record<Campo, string>

type Ocupado = '' | 'salvando' | 'lancando' | 'excluindo' | 'estoque' | 'foto' | 'senha'

// Um cartão = uma peça. `salvo` preenchido = lançamento que já existe (editável);
// `peca` preenchida = peça do pedido que ainda falta lançar (rascunho).
type Cartao = {
  key: string
  peca: LookupRow | null
  salvo: DevolutionRow | null
  f: Campos
  // ── só do rascunho ──
  fotos: File[]
  // "já voltou do técnico" (Manutenção, só pra quem canSeeStockToggle deixa).
  devolver_estoque: boolean
  // Extraviado / Sucata / Manutenção ligam ao escolher a condição (como no seletor antigo).
  reembolso: boolean
  // ── estado de tela ──
  // A pessoa mexeu no Link envio agora (ver linkEnvioLegado).
  linkEnvioTocado: boolean
  // O que estava no seletor antes do "Vai pra manutenção? Sim" — volta no "Não".
  condicaoSemManutencao: string
  erro: string | null
  ocupado: Ocupado
  // Sobe a cada resposta do servidor aplicada: uma releitura que começou antes
  // não passa por cima de uma resposta mais nova.
  rev: number
}

// ── listas ───────────────────────────────────────────────────────────────────
// Decisão do dono (16/11): a tela fala Novo / Usado / Perdimento. "Perdimento" grava
// `Sucata` — Financeiro, Garantia, Conciliação e a situação do Bling leem o NOME
// da condição, então o valor do banco não muda.
const CONDICOES_PRINCIPAIS = [
  { valor: 'Novo', rotulo: 'Novo' },
  { valor: 'Usado', rotulo: 'Usado' },
  { valor: 'Sucata', rotulo: 'Perdimento' },
] as const
// Valores antigos, abaixo do separador: continuam pra não perder função nem
// quebrar lançamento antigo que os usa.
const CONDICOES_OUTROS = ['Extraviado', 'Não devolvido', 'Trocado'] as const
// Manutenção não é opção do seletor: é a pergunta "Vai pra manutenção?".
const CONDICOES_DA_TELA: readonly string[] = ['Novo', 'Usado', 'Sucata', 'Extraviado', 'Não devolvido', 'Trocado', 'Manutenção']
// Valor do seletor enquanto a peça está na manutenção (o seletor fica travado).
const NA_MANUTENCAO = '__manutencao__'

function rotuloCondicao(valor: string | null | undefined): string {
  return valor === 'Sucata' ? 'Perdimento' : valor || ''
}

// Lista final pedida pelo Eduardo (03/09). Linha antiga com motivo retirado
// continua aparecendo no seletor (opção extra, como na condição).
const MOTIVOS_DEVOLUCAO = [
  'Bloqueado',
  'Golpe',
  'Item faltando',
  'Dano funcional / Não funciona',
  'Item Incorreto',
  'Não recebido',
  'Danificado (Outros)',
] as const

const TECNICOS = ['SmarPlay', 'Bogota', 'Shark', 'Cybercell', 'Factor', 'Ozarias'] as const

const PLAT_NOME: Record<string, string> = { ml: 'ML', tiktok: 'TikTok', shopee: 'Shopee', amazon: 'Amazon' }

// ── padrão visual da janela (pages/devolucoes-novo.vue) ──────────────────────
// `truncate`: na grade de 4 colunas o rótulo não pode quebrar em duas linhas (desalinha os campos).
const ROTULO = 'mb-1 block truncate text-[11px] font-semibold'
const CAMPO = 'h-8 w-full rounded-md border bg-background px-2 text-xs disabled:cursor-default disabled:opacity-70'
const CAMPO_ERRO = 'border-red-400 ring-1 ring-red-400'
const CAMPO_LEITURA = 'flex h-8 w-full items-center gap-1 overflow-hidden rounded-md border bg-muted/40 px-2 text-xs'
const BOTAO = 'rounded-md border px-2.5 py-1.5 text-xs font-semibold hover:bg-muted disabled:opacity-50'
const BOTAO_PRINCIPAL = 'rounded-md bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground disabled:opacity-50'
const ETIQUETA = 'rounded px-2 py-0.5 text-[11px] font-semibold'
const COR = {
  ok: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/50 dark:text-emerald-300',
  erro: 'bg-red-100 text-red-800 dark:bg-red-950/50 dark:text-red-300',
  atencao: 'bg-amber-100 text-amber-800 dark:bg-amber-950/50 dark:text-amber-300',
  neutro: 'bg-muted text-muted-foreground',
  tecnico: 'bg-indigo-100 text-indigo-800 dark:bg-indigo-950/50 dark:text-indigo-300',
} as const

const { api } = useApi()

// ── avisos ───────────────────────────────────────────────────────────────────
type TipoAviso = 'success' | 'error' | 'warning' | 'info'
const toasts = useToasts()
// Mesmo tempo da tela antiga (6 s; 0 = fica até alguém fechar — o "Atualizando o
// estoque no Bling…" some quando a resposta chega, não por tempo). O padrão do
// aviso global é 5 s.
function pushToast(t: { kind: TipoAviso; title: string; lines: string[] }, ttl = 6000): number {
  return toasts.push(t, ttl)
}
function dismissToast(id: number) {
  toasts.dismiss(id)
}

function apiError(e: any): string {
  const detail = e?.data?.detail
  // 422 de validator do Pydantic (ex.: link_envio_invalido no schema) vem como lista
  // [{msg, loc}] — mostra a msg em vez do "422 Unprocessable Entity" genérico do fetch.
  if (Array.isArray(detail)) {
    const msgs = detail
      .map((d: any) => String(d?.msg || d?.message || '').replace(/^Value error, /, ''))
      .filter(Boolean)
    if (msgs.length) return msgs.join('; ')
  }
  if (detail && typeof detail === 'object') return detail.message || detail.code || e?.message || 'erro'
  return detail || e?.message || 'erro'
}

// ── formatação ───────────────────────────────────────────────────────────────
function brl(v: number | null | undefined) {
  if (v == null) return '—'
  return v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })
}
function fmtDateTime(v: string | null | undefined) {
  if (!v) return '—'
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return v
  return d.toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit' })
}
// Prazo: só a data (dd/mm/aa). Vencido = data no passado (ignora a hora).
function fmtDate(v: string | null | undefined) {
  if (!v) return '—'
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return v
  return d.toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit', year: '2-digit' })
}
function ddmm(v: string | null | undefined): string {
  if (!v) return ''
  const d = new Date(v)
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' })
}
function prazoOverdue(v: string | null | undefined): boolean {
  if (!v) return false
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return false
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  return d.getTime() < today.getTime()
}
// Dias restantes até o prazo (negativo = vencido). Compara só a data.
function prazoDiasLabel(v: string | null | undefined): string {
  if (!v) return ''
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return ''
  d.setHours(0, 0, 0, 0)
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const dias = Math.round((d.getTime() - today.getTime()) / 86_400_000)
  if (dias > 0) return `faltam ${dias}d`
  if (dias === 0) return 'vence hoje'
  return `vencido há ${-dias}d`
}

// Texto do campo → número. `null` = vazio; `undefined` = não é número (o cartão
// não salva — a tela antiga gravava vazio nesse caso e o custo sumia sem aviso).
// Aceita vírgula decimal ("12,50" e "1.234,56"); com ponto continua como era.
function lerNumero(texto: string): number | null | undefined {
  const t = texto.trim()
  if (t === '') return null
  const norm = t.includes(',') ? t.replace(/\./g, '').replace(',', '.') : t
  const n = Number(norm)
  return Number.isFinite(n) ? n : undefined
}
function numTexto(v: number | null | undefined): string {
  return v == null ? '' : String(v)
}

// ── chamado / senha (texto curto do cartão) ──────────────────────────────────
function platNome(row: DevolutionRow): string {
  const p = (row.chamado_plataforma || 'ml').toLowerCase()
  return PLAT_NOME[p] || p.toUpperCase()
}
// Mensagem ao comprador pedindo a senha (Vinicius 22/09). Shopee pela API e,
// desde 29/09, TikTok pelo robô do Mac dos robôs. No ML a devolução cancela o
// pedido e o chat fecha; a Amazon só tem e-mail — por isso `sem_canal` é um
// estado legítimo, e não erro. Pendente com `erro` = o último recado do robô.
function senhaLabel(row: DevolutionRow): string {
  const st = row.senha_status
  if (!st) return ''
  if (st === 'enviada') {
    const d = row.senha_enviada_at ? new Date(row.senha_enviada_at) : null
    const quando = d ? ` ${d.toLocaleDateString('pt-BR')} ${d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })}` : ''
    return `Senha pedida ao cliente${quando}`
  }
  if (st === 'pendente') return row.senha_erro ? `Senha: na fila — ${row.senha_erro}` : 'Senha: pedido na fila'
  if (st === 'falhou') {
    // A Shopee só deixa a loja puxar conversa se o comprador falou com ela nos
    // últimos 7 dias, comprou nos últimos 30, ou tem devolução em aberto.
    if (row.senha_erro === 'fora_da_janela_shopee') return 'Senha: a Shopee fechou o chat com esse comprador — pedir por fora'
    return `Senha: não saiu — ${row.senha_erro || 'erro'}`
  }
  if (st === 'cancelada') return 'Senha: pedido cancelado (motivo mudou)'
  if (st === 'sem_canal') return `Senha: ${platNome(row)} não deixa falar com o comprador — pedir na mão`
  return ''
}
function senhaClass(row: DevolutionRow): string {
  const st = row.senha_status
  if (st === 'enviada') return 'text-emerald-700 dark:text-emerald-300'
  if (st === 'falhou') return 'text-red-600 dark:text-red-400'
  if (st === 'sem_canal') return 'text-sky-700 dark:text-sky-300'
  if (st === 'cancelada') return 'text-muted-foreground'
  return 'text-amber-700 dark:text-amber-300'
}
// ↻ "pedir de novo" (14/10, 294738): o pedido de senha desistiu depois de 3
// tentativas e não havia como mandar de novo. Só pra `falhou` — a janela
// fechada da Shopee é regra dela.
// O pedido de senha que NÃO saiu (precisa de alguém): o resto do andamento não
// aparece no cartão.
function senhaComProblema(row: DevolutionRow): boolean {
  return row.senha_status === 'falhou' || row.senha_status === 'sem_canal'
}
function podePedirSenhaDeNovo(row: DevolutionRow): boolean {
  return props.canEdit && row.senha_status === 'falhou' && row.senha_erro !== 'fora_da_janela_shopee'
}
async function pedirSenhaDeNovo(c: Cartao) {
  const row = c.salvo
  if (!row || !podePedirSenhaDeNovo(row) || c.ocupado) return
  const plat = platNome(row)
  if (!confirm(`Pedir de novo a senha ao comprador do pedido ${row.pedido_bling || ''} no chat da ${plat}?\n\nResolva antes o que impediu (ex.: loja deslogada no AdsPower).`)) return
  c.ocupado = 'senha'
  try {
    const updated = await api<DevolutionRow>(
      `/api/devolutions/${encodeURIComponent(row.id)}/senha/pedir-de-novo`,
      { method: 'POST' },
    )
    const s = cartaoPorId(row.id)?.salvo
    if (s) {
      s.senha_status = updated.senha_status
      s.senha_erro = updated.senha_erro
      s.senha_enviada_at = updated.senha_enviada_at
    }
    pushToast({ kind: 'success', title: 'Senha', lines: ['Pedido de senha de volta na fila do robô.'] })
    emit('mudou')
  } catch (e: any) {
    pushToast({ kind: 'error', title: 'Não deu pra pedir de novo', lines: [apiError(e)] })
  } finally {
    c.ocupado = ''
  }
}

// Contestação já saiu na plataforma (08/10, 298350): ela só sai uma vez, então
// foto anexada depois — inclusive num lançamento apagado e refeito — fica só
// aqui. Chamado encerrado pelo motivo ("retirar a contestação") não avisa.
function contestacaoJaSaiu(row: DevolutionRow): boolean {
  return row.chamado_ml_status === 'enviada' && row.chamado_ml_erro !== 'retirar_contestacao'
}
function contestacaoSaiuEm(row: DevolutionRow): string {
  return row.chamado_enviada_at ? ` em ${fmtDateTime(row.chamado_enviada_at)}` : ''
}
// O cartão do vídeo (QR) é gerado pela própria contestação — não é foto "depois".
function fotoDepoisDaContestacao(row: DevolutionRow, a: DevolucaoAnexo): boolean {
  if (!contestacaoJaSaiu(row) || !row.chamado_enviada_at) return false
  if (a.content_type.startsWith('video/') || a.filename === 'video-expedicao.png') return false
  return new Date(a.created_at).getTime() > new Date(row.chamado_enviada_at).getTime()
}
function avisoFotosDepois(row: DevolutionRow): string[] {
  const plat = platNome(row)
  return [
    `A contestação na ${plat} já saiu${contestacaoSaiuEm(row)}, antes destas fotos — e ela só sai uma vez.`,
    plat === 'Shopee'
      ? 'As fotos ficam guardadas aqui. Se a Shopee pedir prova extra, elas vão junto.'
      : 'As fotos ficam guardadas aqui. Pra mandar, use a Réplica manual na aba Chamados.',
  ]
}

// Aviso da troca de motivo com chamado aberto (Vinicius 21/09). `encerrado` é o
// caso de 15/09 (motivo deixou de pedir chamado); os outros são a troca entre
// dois motivos que abrem chamado — o que aconteceu depende de onde a contestação
// antiga estava (services/chamados_devolucao.trocar_motivo_chamado).
function showTrocaMotivoToast(desfecho: string | null | undefined, plat: string) {
  if (!desfecho) return
  if (desfecho === 'encerrado') {
    pushToast({
      kind: 'warning',
      title: 'Chamado encerrado',
      lines: [
        'O motivo não pede mais chamado, então o chamado da devolução ficou Encerrado — conclua pela aba Chamados.',
        'Se a contestação já tinha sido enviada na plataforma, desista dela no painel da plataforma.',
      ],
    }, 9000)
  } else if (desfecho === 'atualizado') {
    pushToast({
      kind: 'success',
      title: 'Motivo trocado — mesmo chamado',
      lines: [
        'A contestação ainda não tinha saído: ela sai com o motivo novo (anexe as fotos, se o motivo pedir).',
        'A troca ficou registrada no histórico do chamado.',
      ],
    }, 9000)
  } else if (desfecho === 'atualizado_sem_api') {
    pushToast({
      kind: 'warning',
      title: 'Motivo trocado — plataforma sem API',
      lines: [
        `O ${plat} não tem API pra contestar: abra na mão no painel da plataforma já com o motivo novo.`,
        'A troca ficou registrada no histórico do chamado.',
      ],
    }, 9000)
  } else if (desfecho === 'substituido') {
    pushToast({
      kind: 'warning',
      title: 'Chamado anterior encerrado, outro aberto',
      lines: [
        'O chamado antigo ficou Encerrado (conclua pela aba Chamados) e um novo foi aberto com o motivo novo.',
        `Se o robô já tinha aberto a solicitação no ${plat}, desista dela lá — a plataforma não deixa cancelar pela API.`,
      ],
    }, 12000)
  } else if (desfecho === 'ja_enviada') {
    pushToast({
      kind: 'warning',
      title: 'Contestação anterior já está na plataforma',
      lines: [
        `O ${plat} não aceita uma segunda contestação pela API: o chamado é o mesmo, com o relato novo registrado no histórico.`,
        plat === 'ML'
          ? 'Responda na mediação com o relato novo — pela réplica da aba Chamados ou no painel do ML.'
          : 'Responda você no caso aberto no Seller Center com o relato novo e as fotos.',
      ],
    }, 12000)
  }
}

// ── quem pode devolver a Manutenção ao estoque ───────────────────────────────
// Devolução de estoque é AUTOMÁTICA no lançamento pra Novo/Usado/Trocado. A ÚNICA
// exceção é a Manutenção, que continua manual ("voltou do técnico") — e só estes
// e-mails veem o botão.
const _auth = useAuthStore()
const STOCK_TOGGLE_USERS = [
  'spectrum77@tuta.com',
  'sthevem7@tuta.com',
  'joffer4@tuta.com',
]
function canSeeStockToggle(condicao?: string | null): boolean {
  if (condicao !== 'Manutenção') return false
  const email = _auth.user?.email?.toLowerCase()
  return !!email && STOCK_TOGGLE_USERS.includes(email)
}

// ── estado ───────────────────────────────────────────────────────────────────
const lancados = ref<Cartao[]>([])
const rascunhos = ref<Cartao[]>([])
// Primeiro os lançamentos já feitos, depois as peças que faltam.
const cartoes = computed<Cartao[]>(() => [...lancados.value, ...rascunhos.value])
const carregando = ref(false)
const carregou = ref(false)
const erroPecas = ref<string | null>(null)
const erroLancamentos = ref<string | null>(null)
const lancando = ref(false)
// "2 lançadas · 1 falta lançar" — só as partes que existem.
const resumo = computed(() => {
  const partes: string[] = []
  const l = lancados.value.length
  const r = rascunhos.value.length
  if (l) partes.push(`${l} ${l === 1 ? 'lançada' : 'lançadas'}`)
  if (r) partes.push(`${r} ${r === 1 ? 'falta lançar' : 'faltam lançar'}`)
  return partes.join(' · ')
})

let seqCartao = 0
function camposDe(row: DevolutionRow): Campos {
  return {
    condicao_produto: row.condicao_produto || '',
    motivo_devolucao: row.motivo_devolucao || '',
    link_envio: row.link_envio || '',
    link_abertura: row.link_abertura || '',
    custo_produto: numTexto(row.custo_produto),
    custo_manutencao: numTexto(row.custo_manutencao),
    tecnico: row.tecnico || '',
    observacao: row.observacao || '',
  }
}
function cartaoLancado(row: DevolutionRow): Cartao {
  return {
    key: row.id,
    peca: null,
    salvo: row,
    f: camposDe(row),
    fotos: [],
    devolver_estoque: false,
    reembolso: false,
    linkEnvioTocado: false,
    condicaoSemManutencao: '',
    erro: null,
    ocupado: '',
    rev: 0,
  }
}
function novoRascunho(peca: LookupRow): Cartao {
  return {
    key: `rascunho-${++seqCartao}`,
    peca,
    salvo: null,
    f: {
      condicao_produto: '',
      motivo_devolucao: '',
      // Os links vêm de Provas (o vídeo já enviado pela equipe entra por lá).
      link_envio: (props.linkEnvio ?? props.videoLink ?? '') || '',
      link_abertura: props.linkAbertura || '',
      custo_produto: numTexto(peca.custo_produto),
      custo_manutencao: '',
      tecnico: '',
      observacao: props.observacao || '',
    },
    fotos: [],
    devolver_estoque: false,
    reembolso: false,
    linkEnvioTocado: false,
    condicaoSemManutencao: '',
    erro: null,
    ocupado: '',
    rev: 0,
  }
}
function cartaoPorId(id: string): Cartao | undefined {
  return lancados.value.find((c) => c.salvo?.id === id)
}
function skuDe(c: Cartao): string | null {
  return c.salvo ? c.salvo.sku : c.peca?.sku ?? null
}
function produtoDe(c: Cartao): string | null {
  return c.salvo ? c.salvo.produtos : c.peca?.produtos ?? null
}
function qtdDe(c: Cartao): number {
  return (c.salvo ? c.salvo.quantidade : c.peca?.quantidade) ?? 1
}

// Mudança não salva do lançamento = o que está na tela ≠ o que está no servidor.
function campoMudou(c: Cartao, k: Campo): boolean {
  if (!c.salvo) return false
  if (k === 'custo_produto' || k === 'custo_manutencao') return lerNumero(c.f[k]) !== (c.salvo[k] ?? null)
  return c.f[k] !== (c.salvo[k] || '')
}
function temMudanca(c: Cartao): boolean {
  return !!c.salvo && CAMPOS.some((k) => campoMudou(c, k))
}
function rascunhoMexido(c: Cartao): boolean {
  if (c.salvo) return false
  const f = c.f
  return !!(
    f.condicao_produto || f.motivo_devolucao || f.tecnico || f.custo_manutencao.trim()
  )
}
// Resposta do servidor entra no cartão. `manterEdicao`: o que a pessoa mexeu e
// ainda não salvou fica na tela (releitura, foto anexada); sem ele o cartão
// volta a mostrar exatamente o que está salvo.
function receber(c: Cartao, row: DevolutionRow, manterEdicao = false) {
  // PATCH/GET não devolvem `cliente` (só a listagem preenche) — preserva o do cartão.
  const novo: DevolutionRow = { ...row, cliente: row.cliente ?? c.salvo?.cliente }
  const f = camposDe(novo)
  if (manterEdicao) {
    for (const k of CAMPOS) if (campoMudou(c, k)) f[k] = c.f[k]
  } else {
    c.linkEnvioTocado = false
    c.condicaoSemManutencao = ''
  }
  c.salvo = novo
  c.f = f
  c.rev += 1
}
function desfazer(c: Cartao) {
  if (!c.salvo || c.ocupado) return
  c.f = camposDe(c.salvo)
  c.linkEnvioTocado = false
  c.condicaoSemManutencao = ''
  c.erro = null
}
// Trava de tela do cartão: sem permissão, ocupado, ou rascunho no meio de um lançamento.
function travado(c: Cartao): boolean {
  return !props.canEdit || !!c.ocupado || (!c.salvo && lancando.value)
}
// O toast aparece onde a pessoa está olhando; o texto no cartão é o que sobra
// depois que o toast some (caso 292128 — a operadora saiu achando que tinha lançado).
function bloqueia(c: Cartao, msg: string, titulo = 'Linha não salva') {
  c.erro = msg
  pushToast({ kind: 'error', title: titulo, lines: [msg] })
}

// ── estoque no Bling (15/10, caso 300588) ────────────────────────────────────
function showStockToast(sr: BlingStockResult, rowId?: string, linha?: DevolutionRow) {
  const lines = sr.message ? [sr.message] : []
  // Recusa por excesso de chamadas: o DaVinci tenta de novo sozinho (21/10) —
  // aviso âmbar que fecha sozinho quando a entrada entra.
  if (sr.action === 'error' && rowId && linha && tentandoSozinho(linha)) {
    fecharAvisoConferindo(rowId)
    const id = pushToast({
      kind: 'warning',
      title: 'Bling ocupado · o DaVinci tenta de novo sozinho',
      lines: [...lines, 'Ele tenta de novo em alguns minutos (até 5 vezes). Não precisa fazer nada.'],
    }, 0)
    avisoConferindo.set(rowId, id)
    return
  }
  // O Bling respondeu erro DEPOIS de receber (15/10, 300334/298826): pode ter
  // entrado. Fica na tela até a conferência terminar (ou alguém fechar) —
  // repetir agora dobraria o estoque.
  if (sr.action === 'unconfirmed') {
    if (rowId) fecharAvisoConferindo(rowId)
    const id = pushToast({ kind: 'warning', title: 'Bling · conferindo a entrada', lines }, 0)
    if (rowId) avisoConferindo.set(rowId, id)
    return
  }
  if (sr.action === 'reverse_incerto') {
    pushToast({ kind: 'warning', title: 'Bling · o estorno pode ter saído', lines: [...lines, 'Confira o extrato antes de repetir — use “Estorno já saiu” ou “Tentar de novo” no cartão da peça.'] }, 0)
    return
  }
  if (sr.action === 'unconfirmed_correcao') {
    pushToast({ kind: 'warning', title: 'Bling · confira o extrato', lines }, 0)
    return
  }
  if (sr.ok) {
    const title = sr.action === 'product_created_usado'
      ? 'Bling · produto criado'
      : sr.action === 'stock_reversed'
        ? 'Bling · estoque estornado'
        : 'Bling · estoque atualizado'
    pushToast({ kind: 'success', title, lines })
  } else {
    const titles: Record<string, string> = {
      no_integration: 'Bling não conectado',
      no_sku: 'SKU não informado',
      sku_not_found: 'SKU não encontrado no Bling',
      create_failed: 'Erro ao criar produto no Bling',
      error: 'Estoque NÃO entrou no Bling',
      reverse_error: 'Estorno NÃO saiu no Bling — o estoque continua lá',
    }
    // Erro não some sozinho (caso 300588: o aviso de 6 s sumiu e a data ficou
    // parecendo que tinha entrado). O cartão também fica marcado em vermelho.
    const extra = rowId && sr.action === 'error' ? ['Use “Tentar de novo” no cartão da peça.'] : []
    pushToast(
      { kind: sr.action === 'error' || sr.action === 'reverse_error' ? 'error' : 'warning', title: titles[sr.action] ?? 'Bling · aviso', lines: [...lines, ...extra] },
      0,
    )
  }
}

const ESTOQUE_PENDENTE = new Set(['falhou', 'conferindo', 'sem_confirmacao'])
function estoquePendente(row: DevolutionRow): boolean {
  return ESTOQUE_PENDENTE.has(row.estoque_status || '')
}
// SKU onde a entrada vai/foi: o produto que o Bling respondeu com erro, o
// destino escolhido (ou o z000N já criado). null = z ainda não criado.
// Mesma ordem do back (return_product_to_bling_stock).
function skuDaEntrada(row: DevolutionRow): string | null {
  if (row.estoque_conferir_sku) return row.estoque_conferir_sku
  if (row.estoque_destino_sku) return row.estoque_destino_sku
  if (row.estoque_nova_tag) return null
  const efetivo = (row.condicao_produto === 'Trocado' ? row.troca_sku : row.sku) || ''
  const base = efetivo.includes('.') ? efetivo.slice(0, efetivo.lastIndexOf('.')) : efetivo
  if (row.estoque_suffix) return `${base}.${row.estoque_suffix.replace(/^\./, '')}`
  const condicao = row.condicao_produto === 'Trocado' ? row.troca_condicao
    : row.condicao_produto === 'Manutenção' ? row.manutencao_destino : row.condicao_produto
  if (condicao === 'Usado' && !/^(b[0-9]|u)/i.test(efetivo)) return efetivo ? `${base}.us` : null
  return efetivo || null
}
function temEstornoPendente(row: DevolutionRow): boolean {
  return !!row.estoque_mov_sku && !row.estoque_mov_revertido_at
}
// Linha "conferindo" (o worker confere a cada 2 min, até 10) ou recusada por
// excesso de chamadas (o worker tenta de novo sozinho).
function tentandoSozinho(row: DevolutionRow): boolean {
  return row.estoque_status === 'falhou' && row.estoque_conferir_action === 'auto_retry'
}
function acompanharSozinho(row: DevolutionRow): boolean {
  return row.estoque_status === 'conferindo' || tentandoSozinho(row)
}
// Campos do estoque que o servidor muda: só eles entram no cartão (a releitura
// não pode apagar o que a pessoa está digitando em outro campo).
const CAMPOS_ESTOQUE = [
  'estoque_status', 'estoque_erro', 'estoque_tentativa_at', 'estoque_conferir_sku',
  'estoque_conferir_action', 'estoque_auto_tentativas',
  'estoque_mov_sku', 'estoque_mov_bling_id', 'estoque_mov_action', 'estoque_mov_qty',
  'estoque_mov_revertido_at', 'estoque_destino_sku', 'estoque_nova_tag', 'estoque_suffix',
  'data_devolvido_estoque', 'situacao_bling_id', 'situacao_bling_nome',
] as const
function aplicarEstoque(id: string, r: DevolutionRow) {
  const c = cartaoPorId(id)
  if (!c?.salvo) return
  for (const k of CAMPOS_ESTOQUE) (c.salvo as any)[k] = (r as any)[k]
  c.rev += 1
}
// Aviso âmbar "conferindo" de cada lançamento: fecha quando a conferência termina
// (ou quando o bloco recarrega e o lançamento já não está conferindo / não existe mais).
const avisoConferindo = new Map<string, number>()
function fecharAvisoConferindo(id: string) {
  const t = avisoConferindo.get(id)
  if (t != null) dismissToast(t)
  avisoConferindo.delete(id)
}
function fecharAvisosResolvidos() {
  for (const id of [...avisoConferindo.keys()]) {
    const linha = cartaoPorId(id)?.salvo
    if (!linha || !acompanharSozinho(linha)) fecharAvisoConferindo(id)
  }
}

// Etiqueta do cabeçalho: ok / NÃO entrou / conferindo / não confirmada / tentando sozinho.
function etiquetaEstoque(row: DevolutionRow): { texto: string; cor: string; dica: string; girando: boolean } {
  const st = row.estoque_status
  if (st === 'conferindo') {
    return { texto: 'Conferindo no Bling…', cor: COR.atencao, girando: true, dica: 'O Bling respondeu com erro depois de receber: a entrada pode ter acontecido. O DaVinci confere sozinho em até 10 min — não lance de novo.' }
  }
  if (st === 'falhou') {
    // Recusa por excesso de chamadas: o detalhe (erro + "n de 5") fica no quadro de baixo.
    return tentandoSozinho(row)
      ? { texto: 'tentando sozinho', cor: COR.atencao, girando: true, dica: `Estoque NÃO entrou — o DaVinci tenta de novo sozinho (${row.estoque_auto_tentativas ?? 0} de 5). ${row.estoque_erro || ''}` }
      : { texto: 'Estoque NÃO entrou', cor: COR.erro, girando: false, dica: row.estoque_erro || '' }
  }
  if (st === 'sem_confirmacao') {
    return { texto: temEstornoPendente(row) ? 'Estorno não confirmado' : 'Entrada não confirmada', cor: COR.atencao, girando: false, dica: row.estoque_erro || '' }
  }
  if (row.estoque_mov_sku && !row.estoque_mov_revertido_at) {
    return { texto: 'estoque ok', cor: COR.ok, girando: false, dica: `Estoque devolvido no Bling: +${row.estoque_mov_qty ?? 1} un. em ${row.estoque_mov_sku} · ${fmtDateTime(row.data_devolvido_estoque)}` }
  }
  if (row.estoque_mov_sku && row.estoque_mov_revertido_at) {
    return { texto: 'estoque estornado', cor: COR.neutro, girando: false, dica: `Estoque estornado em ${fmtDateTime(row.estoque_mov_revertido_at)} · baixa de ${row.estoque_mov_qty ?? 1} un. em ${row.estoque_mov_sku}` }
  }
  if (st === 'ok') return { texto: 'estoque ok', cor: COR.ok, girando: false, dica: `Entrada confirmada · ${fmtDateTime(row.data_devolvido_estoque)}` }
  if (row.data_devolvido_estoque) {
    return { texto: `estoque ${ddmm(row.data_devolvido_estoque)}`, cor: COR.neutro, girando: false, dica: `Devolução ao estoque pedida em ${fmtDateTime(row.data_devolvido_estoque)} (sem registro do movimento no Bling)` }
  }
  return { texto: '', cor: '', dica: '', girando: false }
}
// Campo só de leitura "SKU lançado no estoque": onde a unidade entrou (ou por que não entra).
function skuLancado(row: DevolutionRow): { texto: string; riscado: boolean; cor: string } {
  if (row.estoque_mov_sku) {
    return row.estoque_mov_revertido_at
      ? { texto: row.estoque_mov_sku, riscado: true, cor: 'text-muted-foreground' }
      : { texto: row.estoque_mov_sku, riscado: false, cor: 'text-emerald-700 dark:text-emerald-300' }
  }
  if (estoquePendente(row)) return { texto: skuDaEntrada(row) || 'produto z novo', riscado: false, cor: 'text-muted-foreground' }
  const cond = row.condicao_produto
  if (cond === 'Sucata') return { texto: '— perdimento não volta', riscado: false, cor: 'text-muted-foreground' }
  if (cond === 'Extraviado') return { texto: '— extraviado não volta', riscado: false, cor: 'text-muted-foreground' }
  if (cond === 'Manutenção') {
    return row.devolver_estoque && row.manutencao_destino === 'Sucata'
      ? { texto: '— perdimento não volta', riscado: false, cor: 'text-muted-foreground' }
      : { texto: '— com o técnico', riscado: false, cor: 'text-muted-foreground' }
  }
  return { texto: '—', riscado: false, cor: 'text-muted-foreground' }
}

async function acaoEstoque(c: Cartao, url: string, body: Record<string, unknown>) {
  const row = c.salvo
  if (!row) return
  c.ocupado = 'estoque'
  const aviso = pushToast({
    kind: 'info',
    title: 'Atualizando o estoque no Bling…',
    lines: [`Pedido ${row.pedido_bling || '—'} · pode levar alguns segundos, não repita`],
  }, 0)
  try {
    const updated = await api<DevolutionRow>(url, { method: 'POST', body })
    aplicarEstoque(row.id, updated)
    if (updated.bling_stock_result) showStockToast(updated.bling_stock_result, row.id, updated)
    else if (updated.estoque_status === 'ok') pushToast({ kind: 'success', title: 'Entrada registrada', lines: [`${updated.estoque_mov_sku || ''} · pedido ${updated.pedido_bling || '—'}`] })
    agendarConferencia()
    emit('mudou')
  } catch (e: any) {
    if (e?.data?.detail?.code === 'estoque_conferindo') {
      // O DaVinci está tentando agora (worker): relê o lançamento, sem erro fixo.
      try { aplicarEstoque(row.id, await api<DevolutionRow>(`/api/devolutions/${encodeURIComponent(row.id)}`)) } catch { /* a próxima releitura corrige */ }
      pushToast({ kind: 'info', title: 'O DaVinci está tentando agora', lines: ['Aguarde alguns segundos — a linha atualiza sozinha.'] })
      agendarConferencia()
    } else {
      c.erro = apiError(e)
      pushToast({ kind: 'error', title: 'Não deu para registrar o estoque', lines: [apiError(e)] }, 0)
    }
  } finally {
    dismissToast(aviso)
    c.ocupado = ''
  }
}
function linhaComMudanca(c: Cartao): boolean {
  if (!temMudanca(c)) return false
  pushToast({ kind: 'warning', title: 'Salve a linha antes', lines: ['A linha tem mudança não salva — salve ou desfaça antes de mexer no estoque.'] })
  return true
}
async function tentarEstoqueDeNovo(c: Cartao) {
  const row = c.salvo
  if (!props.canEdit || !row || c.ocupado || linhaComMudanca(c)) return
  const sku = skuDaEntrada(row)
  const onde = sku ? `de ${sku}` : 'do produto z criado para este pedido'
  const semConfirmacao = row.estoque_status === 'sem_confirmacao'
  const pergunta = semConfirmacao && temEstornoPendente(row)
    ? `O estorno (saída) da entrada anterior em ${row.estoque_mov_sku} não foi confirmado — ele PODE ter saído.\n\n`
      + `Abra o extrato de ${row.estoque_mov_sku} no Bling e procure a saída "Estorno devolução pedido ${row.pedido_bling || ''}". `
      + 'Se ela existir, use "Estorno já saiu" (estornar de novo daria baixa 2×).\n\n'
      + (row.devolver_estoque ? 'Conferi no Bling e a saída NÃO existe. Estornar e lançar agora?' : 'Conferi no Bling e a saída NÃO existe. Estornar agora?')
    : semConfirmacao
      ? `A entrada ${onde} não foi confirmada — ela PODE ter entrado.\n\n`
        + `Abra o extrato de estoque ${onde} no Bling e procure a entrada `
        + `"Devolução pedido ${row.pedido_bling || ''}". Se ela existir, use "Já entrou" (lançar de novo dobra o estoque).\n\n`
        + 'Conferi no Bling e a entrada NÃO existe. Lançar agora?'
      : temEstornoPendente(row)
        ? `Dar baixa (estorno) de ${row.estoque_mov_qty ?? 1} un. em ${row.estoque_mov_sku}`
          + (row.devolver_estoque ? ` e lançar ${row.quantidade ?? 1} un. ${sku ? `em ${sku}` : '(produto z novo)'}` : '')
          + ' no Bling?'
        : `Lançar de novo a entrada de ${row.quantidade ?? 1} un. ${sku ? `em ${sku}` : '(produto z novo)'} no Bling?`
  if (!confirm(pergunta)) return
  await acaoEstoque(c, `/api/devolutions/${encodeURIComponent(row.id)}/estoque/tentar-de-novo`, { confirmo_conferi_no_bling: semConfirmacao })
}
async function estoqueJaEntrou(c: Cartao) {
  const row = c.salvo
  if (!props.canEdit || !row || c.ocupado || linhaComMudanca(c)) return
  const url = `/api/devolutions/${encodeURIComponent(row.id)}/estoque/ja-entrou`
  if (temEstornoPendente(row)) {
    // O que faltava conferir era o ESTORNO (saída) da entrada anterior.
    const ok = confirm(
      `Você viu no extrato de ${row.estoque_mov_sku} a saída "Estorno devolução pedido ${row.pedido_bling || ''}"?\n\n`
        + 'O DaVinci só registra que o estorno saiu (não lança nada no Bling).',
    )
    if (!ok) return
    c.ocupado = 'estoque'
    try {
      const updated = await api<DevolutionRow>(url, { method: 'POST', body: {} })
      aplicarEstoque(row.id, updated)
      pushToast({
        kind: 'success',
        title: 'Estorno registrado',
        lines: [updated.estoque_status === 'falhou' ? 'Falta lançar a entrada nova: use “Tentar de novo”.' : 'A saída já estava no Bling — nada foi lançado.'],
      })
      emit('mudou')
    } catch (e: any) {
      c.erro = apiError(e)
      pushToast({ kind: 'error', title: 'Não deu para registrar o estorno', lines: [apiError(e)] }, 0)
    } finally {
      c.ocupado = ''
    }
    return
  }
  const sugerido = skuDaEntrada(row) || ''
  const sku = window.prompt(
    `Em qual SKU você viu a entrada "Devolução pedido ${row.pedido_bling || ''}" no extrato do Bling?\n\n`
      + 'O DaVinci só registra (não lança nada no Bling) — e o estorno ao excluir volta a funcionar.',
    sugerido,
  )
  if (!sku || !sku.trim()) return
  c.ocupado = 'estoque'
  try {
    const updated = await api<DevolutionRow>(url, { method: 'POST', body: { sku: sku.trim() } })
    aplicarEstoque(row.id, updated)
    pushToast({ kind: 'success', title: 'Entrada registrada', lines: [`${updated.estoque_mov_sku || ''} · pedido ${updated.pedido_bling || '—'}`] })
    emit('mudou')
  } catch (e: any) {
    if (e?.data?.detail?.code === 'estoque_sku_diferente') {
      if (confirm(`${e.data.detail.message}\n\nSó confirme se a entrada foi lançada à mão nesse SKU.`)) {
        await acaoEstoque(c, url, { sku: sku.trim(), confirmo_outro_sku: true })
      }
      return
    }
    c.erro = apiError(e)
    pushToast({ kind: 'error', title: 'Não deu para registrar o estoque', lines: [apiError(e)] }, 0)
  } finally {
    // acaoEstoque (SKU diferente confirmado) já soltou o cartão; aqui é o caminho comum.
    if (c.ocupado === 'estoque') c.ocupado = ''
  }
}

// Lançamento "conferindo" ou sendo tentado sozinho: o bloco relê só esses a cada
// 60 s, sem recarregar tudo. Para ao desmontar.
let conferenciaTimer: number | null = null
let desmontado = false
function agendarConferencia() {
  if (import.meta.server || desmontado || conferenciaTimer != null) return
  if (!lancados.value.some((c) => c.salvo && acompanharSozinho(c.salvo)) && avisoConferindo.size === 0) return
  conferenciaTimer = window.setTimeout(async () => {
    conferenciaTimer = null
    const ids = [...new Set([
      ...lancados.value.filter((c) => c.salvo && acompanharSozinho(c.salvo)).map((c) => c.key),
      ...avisoConferindo.keys(),
    ])]
    let mudou = false
    for (const id of ids) {
      if (desmontado) return
      try {
        const r = await api<DevolutionRow>(`/api/devolutions/${encodeURIComponent(id)}`)
        if (desmontado) return
        const antes = cartaoPorId(id)?.salvo?.estoque_status
        aplicarEstoque(id, r)
        if (!acompanharSozinho(r)) fecharAvisoConferindo(id)
        if (r.estoque_status === 'ok') {
          pushToast({ kind: 'success', title: 'Bling · entrada confirmada', lines: [`${r.estoque_mov_sku || r.sku || ''} · pedido ${r.pedido_bling || '—'}`] })
        } else if (r.estoque_status === 'sem_confirmacao') {
          pushToast({ kind: 'warning', title: 'Bling · entrada não confirmada', lines: [r.estoque_erro || 'Confira o extrato no Bling.'] }, 0)
        }
        if (antes !== r.estoque_status) mudou = true
      } catch {
        // A próxima volta tenta de novo.
      }
    }
    // O estado do estoque mudou sozinho: a lista do pai mostra esse estado.
    if (mudou) emit('mudou')
    agendarConferencia()
  }, 60_000)
}
function pararConferencia() {
  if (conferenciaTimer != null) window.clearTimeout(conferenciaTimer)
  conferenciaTimer = null
  // O aviso "conferindo" é global e só este bloco sabe fechá-lo: sem isto ele
  // ficaria preso na tela depois de a janela fechar.
  for (const id of [...avisoConferindo.keys()]) fecharAvisoConferindo(id)
}

// ── modais de estoque (abrem ANTES das chamadas ao Bling) ────────────────────
// Condições que disparam estoque ao lançar ou ao marcar "voltou do técnico".
const STOCK_TRIGGER_CONDICOES = ['Novo', 'Usado', 'Trocado', 'Manutenção']
function isStockTrigger(condicao: string | null | undefined) {
  return !!condicao && STOCK_TRIGGER_CONDICOES.includes(condicao)
}
// Mala (b+dígito) e Eletro (u…) voltam direto no próprio SKU — sem modal de tag.
function isMalaOrEletro(sku: string | null | undefined) {
  return /^(b[0-9]|u)/i.test((sku || '').trim())
}

type TrocaResult = { sku: string; condicao: 'Novo' | 'Usado' }
const trocaModal = ref<{ open: boolean; soldSku: string | null; resolve: ((v: TrocaResult | null) => void) | null }>(
  { open: false, soldSku: null, resolve: null },
)
function askTrocaSku(soldSku: string | null): Promise<TrocaResult | null> {
  return new Promise((resolve) => { trocaModal.value = { open: true, soldSku, resolve } })
}
function onTrocaConfirm(payload: TrocaResult) {
  trocaModal.value.resolve?.(payload)
  trocaModal.value = { open: false, soldSku: null, resolve: null }
}
function onTrocaCancel() {
  trocaModal.value.resolve?.(null)
  trocaModal.value = { open: false, soldSku: null, resolve: null }
}

// Modal de Manutenção: Novo / Usado / Sucata.
type ManutencaoResult = { tipo: 'Novo' | 'Usado' | 'Sucata' }
const manutencaoModal = ref<{ open: boolean; sku: string | null; resolve: ((v: ManutencaoResult | null) => void) | null }>(
  { open: false, sku: null, resolve: null },
)
function askManutencao(sku: string | null): Promise<ManutencaoResult | null> {
  return new Promise((resolve) => { manutencaoModal.value = { open: true, sku, resolve } })
}
function onManutencaoConfirm(payload: ManutencaoResult) {
  manutencaoModal.value.resolve?.(payload)
  manutencaoModal.value = { open: false, sku: null, resolve: null }
}
function onManutencaoCancel() {
  manutencaoModal.value.resolve?.(null)
  manutencaoModal.value = { open: false, sku: null, resolve: null }
}

// Modal de destino de estoque: bin existente, tag p/ criar produto novo
// (z000N.<tag>) OU `suffix` p/ manter o SKU base.<sufixo> (ex.: .us).
type EstoqueResult = { destino_sku?: string; nova_tag?: string; suffix?: string }
const estoqueModal = ref<{ open: boolean; sku: string; condicao: string; resolve: ((v: EstoqueResult | null) => void) | null }>(
  { open: false, sku: '', condicao: '', resolve: null },
)
function askEstoque(sku: string, condicao: string): Promise<EstoqueResult | null> {
  return new Promise((resolve) => { estoqueModal.value = { open: true, sku, condicao, resolve } })
}
function onEstoqueConfirm(payload: EstoqueResult) {
  estoqueModal.value.resolve?.(payload)
  estoqueModal.value = { open: false, sku: '', condicao: '', resolve: null }
}
function onEstoqueCancel() {
  estoqueModal.value.resolve?.(null)
  estoqueModal.value = { open: false, sku: '', condicao: '', resolve: null }
}

/**
 * Abre, em sequência, os modais necessários antes de devolver ao estoque.
 * Retorna os campos extras a enviar no corpo, ou `null` se a pessoa cancelar
 * qualquer modal (a operação é abortada — sem chamada ao Bling).
 */
async function resolveStockModals(condicao: string | null, sku: string | null): Promise<StockModalFields | null> {
  const empty: StockModalFields = {
    troca_sku: null, troca_condicao: null, estoque_suffix: null,
    manutencao_destino: null, estoque_destino_sku: null, estoque_nova_tag: null,
  }
  if (!isStockTrigger(condicao)) return empty

  const out: StockModalFields = { ...empty }
  let effSku = (sku || '').trim()
  let effCondicao = condicao as string

  // Manutenção — escolher Novo / Usado / Sucata. Sucata não mexe no estoque.
  if (condicao === 'Manutenção') {
    const m = await askManutencao(sku)
    if (!m) return null
    out.manutencao_destino = m.tipo
    if (m.tipo === 'Sucata') return out
    effCondicao = m.tipo
  }

  // Trocado — escolher o SKU que de fato voltou + condição (Novo/Usado).
  if (condicao === 'Trocado') {
    const r = await askTrocaSku(sku)
    if (!r) return null
    out.troca_sku = r.sku
    out.troca_condicao = r.condicao
    effSku = r.sku.trim()
    effCondicao = r.condicao
  }

  // Mala/Eletro NOVO: volta direto no próprio SKU, sem modal de tag/bin.
  // Usado segue a lógica de usados (modal → cria z000N.<tag>).
  if (effCondicao === 'Novo' && isMalaOrEletro(effSku)) {
    out.estoque_destino_sku = effSku
    return out
  }

  // Destino de estoque: bin existente ou criação de produto novo (z000N.<tag>).
  const dest = await askEstoque(effSku, effCondicao)
  if (!dest) return null
  out.estoque_destino_sku = dest.destino_sku ?? null
  out.estoque_nova_tag = dest.nova_tag ?? null
  out.estoque_suffix = dest.suffix ?? null
  return out
}

// A janela do painel fecha no Esc. Com um modal de estoque aberto, o Esc cancela
// só o modal — senão a janela fecharia no meio de um lançamento.
function aoTeclar(e: KeyboardEvent) {
  if (e.key !== 'Escape') return
  if (manutencaoModal.value.open) onManutencaoCancel()
  else if (trocaModal.value.open) onTrocaCancel()
  else if (estoqueModal.value.open) onEstoqueCancel()
  else return
  e.stopPropagation()
}

// ── travas ───────────────────────────────────────────────────────────────────
function linkRequired(condicao: string | null | undefined) {
  return condicao === 'Extraviado' || condicao === 'Sucata' || condicao === 'Manutenção'
}
// Motivos que abrem chamado automático (espelho de services/chamados.MOTIVOS_ABREM_CHAMADO).
const MOTIVOS_ABREM_CHAMADO = ['bloqueado', 'mudou de ideia', 'golpe', 'item faltando', 'não recebido', 'danificado (outros)']
// Mala (b<dígito>, bp*, acessórios a006/a015/a073-a076) ou eletro (celular dg*, airfryer/eletro u*) —
// espelho de services/chamados_devolucao.produto_mala_ou_eletro; o backend é quem trava (422).
function isMalaOuEletro(sku: string | null | undefined) {
  const partes = (sku || '').toLowerCase().replace(/,/g, '+').split('+')
  return partes.some((p) => {
    const base = p.trim().split('.')[0]
    if (!base) return false
    return /^b\d/.test(base) || /^bp\d/.test(base) || ['a006', 'a015', 'a073', 'a074', 'a075', 'a076'].includes(base) || /^(dg|u)/.test(base)
  })
}
// Trava (Eduardo 04/09): "mala e eletro é obrigatória, desde que esteja nos motivos que abrem chamado".
// Vinicius 21/09: "não recebido" (o pacote de VOLTA não chegou, o vídeo da ida não prova nada)
// e "bloqueado" (nome antigo "mudou de ideia") ficam de fora — espelho de MOTIVOS_SEM_LINK_ENVIO em
// services/chamados_devolucao.link_envio_obrigatorio.
const MOTIVOS_SEM_LINK_ENVIO = ['não recebido', 'bloqueado', 'mudou de ideia']
function linkEnvioRequired(sku: string | null | undefined, motivo: string | null | undefined) {
  const m = (motivo || '').trim().toLowerCase()
  if (MOTIVOS_SEM_LINK_ENVIO.includes(m)) return false
  return MOTIVOS_ABREM_CHAMADO.includes(m) && isMalaOuEletro(sku)
}
// Vinicius 21/09: Link envio, quando preenchido, tem que ser URL http(s) — a operadora
// digitava "nao ha, nao recebido" só pra passar na trava e o texto ia parar no QR do cartão
// da disputa. Espelho do validator do backend (422 link_envio_invalido).
const MSG_LINK_ENVIO_INVALIDO = 'Link de envio precisa ser um endereço http(s) — não digite texto aqui'
function linkEnvioInvalido(link: string | null | undefined) {
  const v = (link || '').trim()
  return !!v && !/^https?:\/\//i.test(v)
}
// A trava de formato vale pro que a pessoa digita AGORA. Texto gravado ANTES dela
// não pode barrar o salvamento: fica de fora do corpo e o lançamento volta a ser
// editável. Senão condição, custo, técnico e observação ficam reféns de um texto
// digitado meses atrás — e o produto nunca volta ao estoque (caso 292128).
function linkEnvioLegado(c: Cartao) {
  return !!c.salvo && linkEnvioInvalido(c.f.link_envio) && !c.linkEnvioTocado
}
// Vinicius 23/09: o vídeo da expedição mora na MEGA — link NOVO no Link envio só
// se for da MEGA com a chave (lib/linkMega). O link que o sistema já tinha continua
// valendo: o do vídeo respondido pela equipe (entra sozinho no lançamento) e o que
// já estava salvo — pedido embalado antes da troca tem o vídeo no Google Drive.
// Espelho de _exigir_link_envio_mega no backend. Mensagem do problema, ou null.
function linkEnvioNaoMega(link: string | null | undefined) {
  const v = (link || '').trim()
  if (!v || linkEnvioInvalido(v)) return null
  if ((props.videoLink || '').trim() === v) return null
  return erroLinkMega(v)
}
// No rascunho vale sempre; no lançamento salvo, só se a pessoa mexeu no campo agora.
function linkEnvioNaoMegaDoCartao(c: Cartao) {
  return !c.salvo || c.linkEnvioTocado ? linkEnvioNaoMega(c.f.link_envio) : null
}
// Trava (Vinicius 18/09): pedido na Fraude (só reembolso — o lançamento responde a
// TikTok com o vídeo) só lança com o vídeo: o link do vídeo entra sozinho no
// "Link envio"; sem vídeo e sem link colado, o backend recusa (422 video_obrigatorio).
const MSG_VIDEO_OBRIGATORIO =
  'Vídeo obrigatório: pedido em Fraude — peça o vídeo do envio à equipe ou cole o link em Provas (Vídeo do envio) antes de lançar'
function videoFraudeRequired(motivo: string | null | undefined) {
  if (!props.pedido || !MOTIVOS_ABREM_CHAMADO.includes((motivo || '').trim().toLowerCase())) return false
  return props.fila === 'fraude' && !props.videoLink
}

// As travas que barram lançar e salvar — as mesmas, na mesma ordem. Mensagem, ou null.
function travaDoCartao(c: Cartao): string | null {
  const f = c.f
  if (props.isAdmin && lerNumero(f.custo_produto) === undefined) return 'Custo do produto: digite só o número (ex.: 12,50)'
  if (lerNumero(f.custo_manutencao) === undefined) return 'Custo de manutenção: digite só o número (ex.: 12,50)'
  if (linkRequired(f.condicao_produto) && !f.link_abertura) return 'Vídeo da abertura obrigatório para Extraviado / Perdimento / Manutenção: cole o link em Provas (Vídeo da abertura)'
  if (linkEnvioRequired(skuDe(c), f.motivo_devolucao) && !f.link_envio) return 'Vídeo do envio obrigatório (mala/eletro com motivo que abre chamado): cole o link em Provas (Vídeo do envio)'
  if (linkEnvioInvalido(f.link_envio) && !linkEnvioLegado(c)) return MSG_LINK_ENVIO_INVALIDO
  const naoMega = linkEnvioNaoMegaDoCartao(c)
  if (naoMega) return `Link envio: ${naoMega}`
  if (videoFraudeRequired(f.motivo_devolucao) && !f.link_envio) return MSG_VIDEO_OBRIGATORIO
  return null
}

// Estoque em conferência / sem confirmação: mexer na condição agora lançaria de
// novo ou deixaria a entrada sem dono. Avisa e devolve o texto pro cartão (ou null).
function estoqueTravado(row: DevolutionRow): string | null {
  if (row.estoque_status === 'conferindo') {
    const linha = 'O DaVinci ainda está conferindo no Bling se a entrada deste item entrou (até 10 min).'
    pushToast({ kind: 'warning', title: 'Espere a conferência do estoque', lines: [linha] })
    return `Espere a conferência do estoque. ${linha}`
  }
  if (row.estoque_status === 'sem_confirmacao') {
    const linha = 'A entrada (ou o estorno) deste item não foi confirmada no Bling. Use “Já entrou” / “Estorno já saiu” ou “Tentar de novo” no cartão da peça antes de mudar a condição.'
    pushToast({ kind: 'warning', title: 'Confira o estoque antes', lines: [linha] })
    return `Confira o estoque antes. ${linha}`
  }
  return null
}

// ── condição na tela ─────────────────────────────────────────────────────────
function vaiPraManutencao(c: Cartao): boolean {
  return c.f.condicao_produto === 'Manutenção'
}
function condicaoDoSeletor(c: Cartao): string {
  return vaiPraManutencao(c) ? NA_MANUTENCAO : c.f.condicao_produto
}
// O que o seletor mostra enquanto a peça está na manutenção.
function rotuloManutencao(c: Cartao): string {
  const s = c.salvo
  if (!s || s.condicao_produto !== 'Manutenção') return 'vai pro técnico'
  if (s.devolver_estoque && s.manutencao_destino) return `voltou do técnico: ${rotuloCondicao(s.manutencao_destino)}`
  return 'com o técnico'
}
// Valor antigo fora das listas (ex.: "Entregue") continua aparecendo como está.
function foraDaLista(lista: readonly string[], ...valores: (string | null | undefined)[]): string[] {
  return [...new Set(valores.filter((v): v is string => !!v && !lista.includes(v)))]
}
function condicoesAntigas(c: Cartao): string[] {
  return foraDaLista(CONDICOES_DA_TELA, c.salvo?.condicao_produto, c.f.condicao_produto)
}
function motivosAntigos(c: Cartao): string[] {
  return foraDaLista(MOTIVOS_DEVOLUCAO, c.salvo?.motivo_devolucao, c.f.motivo_devolucao)
}
function tecnicosAntigos(c: Cartao): string[] {
  return foraDaLista(TECNICOS, c.salvo?.tecnico, c.f.tecnico)
}
// Técnico e Custo manutenção: habilitados com "Vai pra manutenção? Sim" — e também
// em quem já passou pela manutenção ou já tem um dos dois preenchido, pra não
// perder a edição que a tela antiga permitia (inclusive o custo exigido ao sair).
function manutencaoHabilitada(c: Cartao): boolean {
  if (vaiPraManutencao(c)) return true
  const s = c.salvo
  return !!c.f.tecnico || c.f.custo_manutencao.trim() !== ''
    || !!(s && (s.condicao_produto === 'Manutenção' || s.manutencao || s.tecnico || s.custo_manutencao != null))
}
// Peça que JÁ ENTROU no estoque do Bling (movimento registrado e não estornado).
function entrouNoEstoque(row: DevolutionRow): boolean {
  return !!row.estoque_mov_sku && !row.estoque_mov_revertido_at
}
// Duas travas aprovadas pelo Vinicius em 16/11/2026 ("essas duas sugestões que
// deu pode ser"), achadas na revisão — a tela de antes deixava e custava dinheiro:
//   1) peça que já entrou no estoque trocada pra Perdimento / Extraviado / Não
//      devolvido / sem condição: o estoque NÃO era estornado (ficava prejuízo
//      lançado E a unidade no estoque);
//   2) "Vai pra manutenção? Sim" numa peça que já entrou no estoque: o sistema
//      perguntava como ela voltou do técnico e lançava no estoque DE NOVO.
// Nos dois casos o caminho certo é apagar o lançamento (a lixeira estorna o
// estoque) e lançar de novo. Mensagem, ou null se pode trocar.
const CONDICOES_SEM_ESTOQUE = ['', 'Sucata', 'Extraviado', 'Não devolvido']
function travaEstoqueJaEntrou(row: DevolutionRow, nova: string): string | null {
  if (!entrouNoEstoque(row) || nova === (row.condicao_produto || '')) return null
  const sai = CONDICOES_SEM_ESTOQUE.includes(nova) || (nova === 'Manutenção' && row.condicao_produto !== 'Manutenção')
  if (!sai) return null
  const destino = nova === 'Manutenção' ? 'mandar pra manutenção' : `mudar pra ${rotuloCondicao(nova) || 'sem condição'}`
  return `Esta peça já entrou no estoque (${row.estoque_mov_sku}). Pra ${destino}, apague este lançamento na lixeira (ela tira a peça do estoque) e lance de novo.`
}
// Troca a condição no cartão. false = barrada (o seletor volta ao que estava).
function trocarCondicao(c: Cartao, valor: string): boolean {
  if (!props.canEdit) return false
  if (c.salvo && valor !== (c.salvo.condicao_produto || '')) {
    const trava = estoqueTravado(c.salvo)
    if (trava) {
      c.erro = trava
      return false
    }
    const jaEntrou = travaEstoqueJaEntrou(c.salvo, valor)
    if (jaEntrou) {
      c.erro = jaEntrou
      pushToast({ kind: 'warning', title: 'A peça já está no estoque', lines: [jaEntrou] }, 10000)
      return false
    }
  }
  if (valor === 'Manutenção' && c.f.condicao_produto !== 'Manutenção') c.condicaoSemManutencao = c.f.condicao_produto
  c.f.condicao_produto = valor
  // Rascunho: Extraviado / Sucata / Manutenção ligam o reembolso. Vale a
  // condição FINAL: quem marca "Vai pra manutenção? Sim" por engano e volta pro
  // Não não manda reembolso de um produto que voltou Novo. (Na tela antiga o
  // ligado ficava; com o Sim/Não esse engano é fácil demais.) No lançamento
  // salvo isso acontece no salvar.
  if (!c.salvo) c.reembolso = ['Extraviado', 'Sucata', 'Manutenção'].includes(valor)
  c.erro = null
  return true
}
// 17/11/2026 (Vinicius: "ele não deixa clicar em com o técnico"): o seletor fica
// aberto na Manutenção. Escolher Novo/Usado/Perdimento nele = a peça voltou do
// técnico — sai da manutenção (o "Vai pra manutenção?" vira Não sozinho) e o
// salvar segue as mesmas regras: custo de manutenção obrigatório e modal do estoque.
function mudarCondicao(c: Cartao, ev: Event) {
  const el = ev.target as HTMLSelectElement
  const valor = el.value === NA_MANUTENCAO ? 'Manutenção' : el.value
  // Barrada: o seletor já mostra o valor novo — volta ao do cartão.
  if (!trocarCondicao(c, valor)) el.value = condicaoDoSeletor(c)
}
function mudarManutencao(c: Cartao, ev: Event) {
  const el = ev.target as HTMLSelectElement
  const valor = el.value === 'sim' ? 'Manutenção' : c.condicaoSemManutencao
  if (!trocarCondicao(c, valor)) el.value = vaiPraManutencao(c) ? 'sim' : 'nao'
}
// Saindo da Manutenção sem o custo do conserto: o campo fica marcado (o salvar barra).
function faltaCustoManutencao(c: Cartao): boolean {
  return !!c.salvo && c.salvo.condicao_produto === 'Manutenção' && !vaiPraManutencao(c)
    && lerNumero(c.f.custo_manutencao) == null
}

// ── carregar ─────────────────────────────────────────────────────────────────
// Kit `a+b` aberto em peças, com o custo dividido (o back já abre; fica a mesma
// rede da tela antiga).
function expandir(rows: LookupRow[]): LookupRow[] {
  const out: LookupRow[] = []
  for (const row of rows) {
    if (row.sku && row.sku.includes('+')) {
      const skus = row.sku.split('+').map((s) => s.trim()).filter(Boolean)
      const splitCost = row.custo_produto != null ? row.custo_produto / skus.length : null
      for (const s of skus) out.push({ ...row, sku: s, custo_produto: splitCost })
    } else {
      out.push(row)
    }
  }
  return out
}
function mesmoSku(a: string | null | undefined, b: string | null | undefined): boolean {
  return (a || '').trim().toLowerCase() === (b || '').trim().toLowerCase()
}

let cargaSeq = 0
async function carregar() {
  const pedido = (props.pedido || '').trim()
  const seq = ++cargaSeq
  if (!pedido) {
    lancados.value = []
    rascunhos.value = []
    erroPecas.value = null
    erroLancamentos.value = null
    carregou.value = true
    return
  }
  carregando.value = true
  // O que cada cartão tinha quando a leitura começou (ver `rev`).
  const revs = new Map(lancados.value.map((c) => [c.key, c.rev]))
  const [pecas, lista] = await Promise.allSettled([
    api<LookupRow[]>(`/api/devolutions/order-lookup?pedido=${encodeURIComponent(pedido)}`),
    api<{ items: DevolutionRow[] }>(`/api/devolutions?search=${encodeURIComponent(pedido)}&limit=100`),
  ])
  if (seq !== cargaSeq || desmontado) return

  if (lista.status === 'fulfilled') {
    erroLancamentos.value = null
    // A busca da lista é "contém" e olha várias colunas: fica só o que é DESTE pedido.
    const linhas = (lista.value.items || [])
      .filter((r) => (r.pedido_bling || '').trim() === pedido)
      .sort((a, b) => (a.created_at < b.created_at ? -1 : a.created_at > b.created_at ? 1 : 0))
    const ids = new Set(linhas.map((r) => r.id))
    const antigos = new Map(lancados.value.map((c) => [c.key, c]))
    const atualizados = linhas.map((r) => {
      const c = antigos.get(r.id)
      if (!c) return cartaoLancado(r)
      // Cartão ocupado ou que recebeu resposta depois que esta leitura saiu: fica o dele.
      if (!c.ocupado && c.rev === revs.get(c.key)) receber(c, r, true)
      return c
    })
    // Lançado enquanto a leitura estava no ar (ainda não vinha na lista): não some.
    const recentes = lancados.value.filter((c) => !ids.has(c.key) && (!revs.has(c.key) || !!c.ocupado))
    lancados.value = [...atualizados, ...recentes]
    fecharAvisosResolvidos()
    agendarConferencia()
  } else {
    erroLancamentos.value = apiError(lista.reason)
  }

  if (pecas.status === 'fulfilled') {
    erroPecas.value = null
    // A busca também casa por CEP e nº da plataforma: fica só o pedido da janela.
    // `ja_devolvido` = unidade que já tem lançamento (o back conta por unidade, 302146).
    const livres = expandir((pecas.value || []).filter((r) => (r.pedido_bling || '').trim() === pedido))
      .filter((r) => r.ja_devolvido !== true)
    // Rascunho que a pessoa já estava preenchendo continua: casa pelo SKU (e, de
    // preferência, pelo custo — no kit + avulsa é o custo que separa as unidades).
    const antigos = [...rascunhos.value]
    const usados = new Set<string>()
    const pegar = (peca: LookupRow, comCusto: boolean) => antigos.find((c) =>
      !usados.has(c.key) && mesmoSku(c.peca?.sku, peca.sku)
      && (!comCusto || (c.peca?.custo_produto ?? null) === (peca.custo_produto ?? null)))
    const novos = livres.map((peca) => {
      const velho = pegar(peca, true) || pegar(peca, false)
      if (!velho) return novoRascunho(peca)
      usados.add(velho.key)
      // Custo que ninguém mexeu acompanha a peça nova.
      if (velho.f.custo_produto === numTexto(velho.peca?.custo_produto)) velho.f.custo_produto = numTexto(peca.custo_produto)
      velho.peca = peca
      return velho
    })
    // Rascunho no meio do lançamento não some da tela. E se a busca não trouxe
    // NADA do pedido (ele saiu das situações que a busca enxerga, ex.: a 1ª peça
    // foi Perdimento ou alguém mudou o "Bling:"), o que a pessoa já preencheu
    // também fica — não se perde digitação por causa da situação do Bling.
    const pedidoSumiu = !(pecas.value || []).some((r) => (r.pedido_bling || '').trim() === pedido)
    const emUso = antigos.filter((c) => !usados.has(c.key) && (!!c.ocupado || (pedidoSumiu && rascunhoMexido(c))))
    rascunhos.value = [...novos, ...emUso]
  } else {
    erroPecas.value = apiError(pecas.reason)
  }
  carregando.value = false
  carregou.value = true
}
function recarregar() {
  return carregar()
}

// ── lançar (criar) ───────────────────────────────────────────────────────────
function buildPayload(c: Cartao, peca: LookupRow) {
  const f = c.f
  return {
    data: peca.data,
    pedido_bling: peca.pedido_bling,
    pedido_marketplace: peca.pedido_marketplace,
    conta: peca.conta,
    sku: peca.sku,
    produtos: peca.produtos,
    // Só o admin vê e edita o custo; pros outros vai o que veio do pedido.
    custo_produto: props.isAdmin ? (lerNumero(f.custo_produto) ?? null) : peca.custo_produto,
    condicao_produto: f.condicao_produto || null,
    link_abertura: f.link_abertura || null,
    reembolso: c.reembolso,
    motivo_devolucao: f.motivo_devolucao || null,
    link_envio: f.link_envio || null,
    custo_manutencao: lerNumero(f.custo_manutencao) ?? null,
    tecnico: f.tecnico || null,
    devolver_estoque: c.devolver_estoque,
    observacao: f.observacao || null,
    quantidade: peca.quantidade ?? 1,
    troca_sku: null as string | null,
    troca_condicao: null as string | null,
    estoque_suffix: null as string | null,
    manutencao_destino: null as string | null,
    estoque_destino_sku: null as string | null,
    estoque_nova_tag: null as string | null,
  }
}

// Lança as peças pedidas (uma, ou todas). Cada peça pode abrir os modais
// (manutenção / troca / destino de estoque), resolvidos em sequência. Peça que
// falhar ou cujo modal for cancelado continua como rascunho.
async function lancar(lista: Cartao[]) {
  const pecas = lista.filter((c) => !c.salvo && c.peca)
  if (!props.canEdit || !pecas.length || lancando.value) return
  // Os modais de estoque são um só pra tela inteira: com outro cartão salvando
  // (ele pode estar com um modal aberto), o lançar espera.
  if (cartoes.value.some((c) => !!c.ocupado)) {
    pushToast({ kind: 'warning', title: 'Espere terminar', lines: ['Outro produto deste pedido está sendo salvo. Lance em seguida.'] })
    return
  }
  // Condição vazia ("—") é aceita (Vinicius, 17/09): a peça entra sem condição e
  // a pessoa preenche depois. Sem condição nada dispara (estoque, reembolso,
  // situação no Bling) — o pedido só não conta como resolvido.
  for (const d of pecas) d.erro = null
  for (const d of pecas) {
    const msg = travaDoCartao(d)
    if (msg) {
      bloqueia(d, msg, 'Peça não lançada')
      return
    }
  }
  lancando.value = true
  let added = 0
  // Fotos escolhidas em Provas antes de existir lançamento: sobem com o 1º que
  // der certo (o chamado junta as fotos do pedido inteiro, não importa a peça).
  let fotosDeProvas = [...(props.fotos || [])]
  try {
    for (const d of pecas) {
      const peca = d.peca
      if (!peca) continue
      const condicao = d.f.condicao_produto
      // Ao LANÇAR: Novo/Usado/Trocado SEMPRE processam estoque (automático).
      // Manutenção só processa se a pessoa marcou "já voltou do técnico";
      // senão volta ao estoque depois, pelo lançamento salvo. Extraviado,
      // Sucata e as demais não mexem no estoque.
      const processAtAdd = ['Novo', 'Usado', 'Trocado'].includes(condicao)
        || (condicao === 'Manutenção' && d.devolver_estoque)
      let extra: StockModalFields | null = null
      if (processAtAdd) {
        // Abre os modais (manutenção / troca / destino) antes da chamada ao Bling.
        extra = await resolveStockModals(condicao, peca.sku)
        if (extra === null) continue // modal cancelado → a peça continua rascunho
      }
      const body = buildPayload(d, peca)
      if (extra) Object.assign(body, extra, { devolver_estoque: true }) // processou o estoque
      d.ocupado = 'lancando'
      try {
        let created = await api<DevolutionRow>('/api/devolutions', { method: 'POST', body })
        // O resultado do Bling vem só nesta resposta (a das fotos não traz):
        // guarda antes de subir as fotos.
        const estoqueSr = created.bling_stock_result
        const createdId = created.id
        // Fotos escolhidas no rascunho sobem agora (o lançamento precisa existir) —
        // cada foto já re-dispara o chamado se ele estiver esperando foto.
        let fotosSubiram = 0
        let erroFoto: string | null = null
        const fotosDaVez = [...d.fotos, ...fotosDeProvas]
        const eramDeProvas = fotosDeProvas.length > 0
        fotosDeProvas = []
        for (const foto of fotosDaVez) {
          const fd = new FormData()
          fd.append('file', foto)
          try {
            created = await api<DevolutionRow>(`/api/devolutions/${encodeURIComponent(createdId)}/anexos`, { method: 'POST', body: fd })
            fotosSubiram += 1
          } catch (e: any) {
            erroFoto = `foto ${foto.name}: ${apiError(e)}`
          }
        }
        // Lançamento refeito com o chamado do pedido já enviado (298350): as fotos
        // não vão pra contestação — avisa na hora, em vez de parecer que foram.
        if (fotosSubiram && contestacaoJaSaiu(created)) {
          pushToast({
            kind: 'warning',
            title: `Pedido ${created.pedido_bling || ''}: fotos não foram pra contestação`,
            lines: avisoFotosDepois(created),
          }, 12000)
        }
        // O rascunho vira lançamento (se uma releitura já o trouxe, só atualiza).
        rascunhos.value = rascunhos.value.filter((x) => x.key !== d.key)
        let novo = cartaoPorId(createdId)
        if (novo) {
          receber(novo, created)
        } else {
          lancados.value = [...lancados.value, cartaoLancado(created)]
          novo = cartaoPorId(createdId)
        }
        if (erroFoto && novo) bloqueia(novo, erroFoto, 'Foto não subiu')
        if (eramDeProvas) emit('fotosEnviadas')
        added += 1
        if (estoqueSr) showStockToast(estoqueSr, createdId, created)
        agendarConferencia()
      } catch (e: any) {
        // Sem resposta, ou erro do servidor (deploy, rede caiu no meio da chamada
        // ao Bling): a devolução PODE ter sido criada — a API grava a linha antes
        // de falar com o Bling. Relê o pedido antes de liberar o cartão: lançar de
        // novo no escuro criaria a 2ª linha e a 2ª entrada de estoque.
        const status = Number(e?.status ?? e?.statusCode ?? e?.response?.status ?? 0)
        if (!status || status >= 500) {
          d.ocupado = '' // senão a releitura mantém o rascunho "em uso" na tela
          await carregar()
          const ainda = rascunhos.value.some((x) => x.key === d.key)
          pushToast({
            kind: 'warning',
            title: ainda ? 'Peça não lançada' : 'O lançamento entrou — não lance de novo',
            lines: ainda
              ? ['O servidor não respondeu. Conferi o pedido e esta peça continua SEM lançamento: pode lançar de novo.', apiError(e)]
              : ['O servidor demorou a responder, mas conferi o pedido e a peça JÁ está lançada. Confira o estoque no cartão dela.'],
          }, 0)
          if (ainda) d.erro = `Não confirmou: ${apiError(e)}`
          else added += 1
        } else {
          bloqueia(d, apiError(e), 'Peça não lançada')
        }
      } finally {
        d.ocupado = ''
      }
      // A janela fechou no meio do "lançar todas": as próximas peças não entram
      // sozinhas, sem ninguém vendo o modal de destino do estoque.
      if (desmontado) break
    }
    if (added > 0) {
      pushToast({ kind: 'success', title: 'Devoluções adicionadas', lines: [`${added} produto${added === 1 ? '' : 's'}`] })
      emit('mudou')
    }
  } finally {
    lancando.value = false
  }
}


// ── salvar (lançamento que já existe) ────────────────────────────────────────
type PatchExtra = { modal?: StockModalFields; devolver_estoque?: boolean; reembolso?: boolean }
function rowPatchPayload(c: Cartao, salvo: DevolutionRow, extra: PatchExtra) {
  const f = c.f
  const m = extra.modal
  return {
    // Só o admin vê e edita o custo; pros outros vai o que já estava salvo.
    custo_produto: props.isAdmin ? (lerNumero(f.custo_produto) ?? null) : salvo.custo_produto,
    condicao_produto: f.condicao_produto || null,
    link_abertura: f.link_abertura || null,
    reembolso: extra.reembolso ?? salvo.reembolso,
    motivo_devolucao: f.motivo_devolucao || null,
    // Texto legado que a pessoa não tocou: não reenvia — o schema recusaria
    // com 422 `link_envio_invalido` e o lançamento nunca mais salvaria nada.
    ...(linkEnvioLegado(c) ? {} : { link_envio: f.link_envio || null }),
    custo_manutencao: lerNumero(f.custo_manutencao) ?? null,
    tecnico: f.tecnico || null,
    devolver_estoque: extra.devolver_estoque ?? salvo.devolver_estoque,
    observacao: f.observacao || null,
    quantidade: salvo.quantidade ?? 1,
    troca_sku: m ? m.troca_sku : salvo.troca_sku,
    troca_condicao: m ? m.troca_condicao : salvo.troca_condicao,
    estoque_suffix: m ? m.estoque_suffix : salvo.estoque_suffix,
    manutencao_destino: m ? m.manutencao_destino : salvo.manutencao_destino,
    estoque_destino_sku: m ? m.estoque_destino_sku : salvo.estoque_destino_sku,
    estoque_nova_tag: m ? m.estoque_nova_tag : salvo.estoque_nova_tag,
  }
}

// `estoque`: o salvar lança/estorna no Bling — com o Bling limitando chamadas
// leva 15 s ou mais (caso 294247: o z0340 foi criado, mas a operadora achou que
// tinha falhado). Mostra o aviso até responder.
async function enviarPatch(c: Cartao, extra: PatchExtra, estoque: boolean) {
  const salvo = c.salvo
  if (!salvo) return
  c.ocupado = 'salvando'
  const aviso = estoque
    ? pushToast({
        kind: 'info',
        title: 'Atualizando o estoque no Bling…',
        lines: [`Pedido ${salvo.pedido_bling || '—'} · pode levar alguns segundos, não repita`],
      }, 0)
    : null
  try {
    const updated = await api<DevolutionRow>(`/api/devolutions/${encodeURIComponent(salvo.id)}`, {
      method: 'PATCH',
      body: rowPatchPayload(c, salvo, extra),
    })
    receber(c, updated)
    c.erro = null
    // Troca de motivo com chamado aberto (21/09): o backend diz o que fez com o
    // chamado e a pessoa vê na hora — o detalhe fica no histórico do chamado.
    showTrocaMotivoToast(updated.chamado_troca_motivo, platNome(updated))
    if (updated.bling_stock_result) showStockToast(updated.bling_stock_result, salvo.id, updated)
    else pushToast({ kind: 'success', title: 'Lançamento salvo', lines: [`${updated.sku || '—'} · pedido ${updated.pedido_bling || '—'}`] }, 2500)
    agendarConferencia()
    emit('mudou')
  } catch (e: any) {
    bloqueia(c, apiError(e))
    // Estoque travado (conferindo / sem confirmação): a mudança não vale — o
    // cartão volta ao que está salvo, senão os botões partiriam do que só
    // existe na tela (revisão de 15/10).
    const code = e?.data?.detail?.code
    if (code === 'estoque_conferindo' || code === 'estoque_sem_confirmacao') {
      try {
        receber(c, await api<DevolutionRow>(`/api/devolutions/${encodeURIComponent(salvo.id)}`))
        agendarConferencia()
      } catch {
        // fica como está; a próxima releitura corrige
      }
    }
  } finally {
    if (aviso != null) dismissToast(aviso)
    c.ocupado = ''
  }
}

// Botão "salvar": o changeRowCondicao + saveRow da tela antiga, de uma vez.
async function salvar(c: Cartao) {
  const salvo = c.salvo
  if (!props.canEdit || !salvo || c.ocupado || !temMudanca(c)) return
  if (lancando.value) {
    pushToast({ kind: 'warning', title: 'Espere terminar', lines: ['Tem lançamento em andamento neste pedido. Salve em seguida.'] })
    return
  }
  c.erro = null
  const antes = salvo.condicao_produto || ''
  const agora = c.f.condicao_produto
  const condicaoMudou = agora !== antes
  const jaEntrou = condicaoMudou ? travaEstoqueJaEntrou(salvo, agora) : null
  if (jaEntrou) {
    c.erro = jaEntrou
    pushToast({ kind: 'warning', title: 'A peça já está no estoque', lines: [jaEntrou] }, 10000)
    return
  }
  // (6) Saiu da Manutenção ("Vai pra manutenção? Não") sem dizer como a peça
  // voltou: salvar assim gravaria a condição vazia — o prazo do técnico some,
  // nada entra no estoque e o Bling fica parado em Manutenção.
  if (antes === 'Manutenção' && !agora) {
    c.erro = 'Escolha como a peça voltou do técnico (Novo, Usado ou Perdimento) antes de salvar.'
    pushToast({ kind: 'warning', title: 'Falta a condição', lines: [c.erro] })
    return
  }
  if (condicaoMudou) {
    const trava = estoqueTravado(salvo)
    if (trava) {
      c.erro = trava
      return
    }
    // Ao SAIR de Manutenção, o custo de manutenção é obrigatório (registra o
    // reparo antes de marcar que o pedido passou em manutenção).
    if (antes === 'Manutenção' && lerNumero(c.f.custo_manutencao) == null) {
      pushToast({
        kind: 'warning',
        title: 'Custo de manutenção obrigatório',
        lines: ['Preencha o custo de manutenção antes de mudar a condição.'],
      })
      c.erro = 'Custo de manutenção obrigatório: preencha o custo de manutenção antes de mudar a condição.'
      return
    }
  }
  // As travas vêm ANTES dos modais: barrada, a pessoa corrige e salva de novo sem
  // ter que escolher o destino do estoque duas vezes.
  const msg = travaDoCartao(c)
  if (msg) {
    bloqueia(c, msg)
    return
  }
  const extra: PatchExtra = {}
  let estoque = false
  if (condicaoMudou) {
    if (agora === 'Extraviado' || agora === 'Sucata' || agora === 'Manutenção') extra.reembolso = true
    // Novo/Usado/Trocado: estoque é automático — ao mudar a condição já abre o
    // modal (quando precisa) e devolve ao estoque. Manutenção: continua manual
    // (só processa se o "voltou do técnico" já estava ligado).
    const autoStock = ['Novo', 'Usado', 'Trocado'].includes(agora)
    const manualStock = agora === 'Manutenção' && salvo.devolver_estoque
    if (autoStock || manualStock) {
      c.ocupado = 'salvando'
      const modal = await resolveStockModals(agora, salvo.sku)
      if (modal === null) {
        // Cancelado: nada foi salvo; o cartão fica como a pessoa deixou.
        c.ocupado = ''
        return
      }
      extra.modal = modal
      if (autoStock) extra.devolver_estoque = true
      estoque = true
    }
  }
  await enviarPatch(c, extra, estoque)
}

// "voltou do técnico" = o "devolver ao estoque" da Manutenção (toggleRowDevolverEstoque):
// abre os modais antes de salvar/chamar o Bling. Ligado, o mesmo botão desliga
// (o back estorna a entrada).
async function alternarVoltaDoTecnico(c: Cartao) {
  const salvo = c.salvo
  if (!props.canEdit || !salvo || c.ocupado || lancando.value) return
  c.erro = null
  const trava = estoqueTravado(salvo)
  if (trava) {
    c.erro = trava
    return
  }
  const msg = travaDoCartao(c)
  if (msg) {
    bloqueia(c, msg)
    return
  }
  const next = !salvo.devolver_estoque
  const extra: PatchExtra = { devolver_estoque: next }
  if (next && isStockTrigger(c.f.condicao_produto)) {
    c.ocupado = 'salvando'
    const modal = await resolveStockModals(c.f.condicao_produto, salvo.sku)
    if (modal === null) {
      c.ocupado = '' // cancelado → não liga
      return
    }
    extra.modal = modal
  }
  await enviarPatch(c, extra, true)
}

// ── fotos e vídeos do lançamento ─────────────────────────────────────────────
// Fotos e vídeos do pedido: quem manda é o bloco Provas da janela. O arquivo é
// guardado no 1º lançamento do pedido (o chamado junta os anexos de todos).
// Sem lançamento ainda, devolve false: o pai segura as fotos e elas sobem com
// o 1º lançamento (ver `lancar`).
async function anexarFotos(files: File[]): Promise<boolean> {
  const c = lancados.value[0]
  const row = c?.salvo
  if (!props.canEdit || !c || !row || !files.length) return false
  if (c.ocupado) {
    pushToast({ kind: 'warning', title: 'Espere terminar', lines: ['O lançamento está sendo salvo. Anexe em seguida.'] })
    return true
  }
  c.ocupado = 'foto'
  c.erro = null
  let subiram = 0
  try {
    for (const f of files) {
      const fd = new FormData()
      fd.append('file', f)
      const updated = await api<DevolutionRow>(`/api/devolutions/${encodeURIComponent(row.id)}/anexos`, { method: 'POST', body: fd })
      // A resposta traz as fotos e o estado do chamado; o que a pessoa digitou fica.
      receber(c, updated, true)
      subiram += 1
    }
    // Lançamento com o chamado do pedido já enviado: as fotos não vão pra contestação.
    if (subiram && c.salvo && contestacaoJaSaiu(c.salvo)) {
      pushToast({ kind: 'warning', title: `Pedido ${c.salvo.pedido_bling || ''}: fotos não foram pra contestação`, lines: avisoFotosDepois(c.salvo) }, 12000)
    }
  } catch (e: any) {
    bloqueia(c, apiError(e), 'Foto não anexada')
  } finally {
    c.ocupado = ''
    if (subiram) emit('mudou')
  }
  return true
}
async function removerFoto(anexoId: string): Promise<void> {
  const c = lancados.value.find((x) => (x.salvo?.anexos || []).some((a) => a.id === anexoId))
  if (!props.canEdit || !c?.salvo || c.ocupado) return
  try {
    await api(`/api/devolutions/anexos/${anexoId}`, { method: 'DELETE' })
    if (c.salvo) c.salvo.anexos = (c.salvo.anexos || []).filter((x) => x.id !== anexoId)
    pushToast({ kind: 'success', title: 'Foto removida', lines: [] }, 2500)
    emit('mudou')
  } catch (e: any) {
    bloqueia(c, apiError(e), 'Foto não removida')
  }
}

// Link colado em Provas (vídeo do envio ou da abertura): vale pro pedido
// inteiro. Grava em todos os lançamentos que já existem (só esse campo — o que
// estiver digitado e não salvo nos cartões fica como está); as peças por
// lançar pegam pela prop. Devolve a mensagem do problema, ou null se deu certo.
async function gravarLink(campo: 'link_envio' | 'link_abertura' | 'observacao', valor: string): Promise<string | null> {
  if (!props.canEdit) return 'Sem permissão para editar'
  const v = (valor || '').trim()
  if (campo === 'observacao') return gravarEmTodos(campo, v)
  if (v && linkEnvioInvalido(v)) {
    return campo === 'link_envio' ? MSG_LINK_ENVIO_INVALIDO : 'Link da abertura precisa ser um endereço http(s) — não digite texto aqui'
  }
  if (campo === 'link_envio') {
    const naoMega = linkEnvioNaoMega(v)
    if (naoMega) return `Vídeo do envio: ${naoMega}`
  }
  const alvos = lancados.value.filter((c) => c.salvo && (c.salvo[campo] || '') !== v)
  if (!v) {
    // Apagar o link de um lançamento que exige o link deixaria o chamado sem a prova.
    const exige = alvos.find((c) => campo === 'link_abertura'
      ? linkRequired(c.salvo!.condicao_produto)
      : linkEnvioRequired(c.salvo!.sku, c.salvo!.motivo_devolucao))
    if (exige) return `Não dá pra apagar: o lançamento de ${exige.salvo!.sku || 'uma peça'} exige esse link`
  }
  return gravarEmTodos(campo, v)
}
// Grava UM campo do pedido em todos os lançamentos que já existem (só esse
// campo — o que estiver digitado e não salvo nos cartões fica como está).
async function gravarEmTodos(campo: 'link_envio' | 'link_abertura' | 'observacao', v: string): Promise<string | null> {
  const alvos = lancados.value.filter((c) => c.salvo && (c.salvo[campo] || '') !== v)
  if (alvos.some((c) => !!c.ocupado)) return 'Tem lançamento sendo salvo. Tente de novo em seguida.'
  let erro: string | null = null
  let gravou = 0
  for (const c of alvos) {
    c.ocupado = 'salvando'
    try {
      const updated = await api<DevolutionRow>(`/api/devolutions/${encodeURIComponent(c.salvo!.id)}`, {
        method: 'PATCH', body: { [campo]: v || null },
      })
      receber(c, updated, true)
      gravou += 1
    } catch (e: any) {
      erro = apiError(e)
    } finally {
      c.ocupado = ''
    }
  }
  if (gravou) emit('mudou')
  return erro
}

// ── lixeira ──────────────────────────────────────────────────────────────────
// Resposta da lixeira (26/10, caso 300978): além do estorno, o que o DaVinci
// desfez sozinho — prejuízo automático, situação do pedido e chamado — e o que
// ficou porque alguém já tinha mexido.
type ExclusaoResult = {
  ok: boolean
  estoque_estornado: boolean
  mensagem: string | null
  prejuizos_apagados?: { qtd: number; total: number }
  prejuizos_mantidos?: { tipo: string | null; prejuizo: number | null; reembolso: number | null; valor?: number | null; motivo: string | null }[]
  situacao_revertida?: { de: string; para: number; nome: string; nome_de?: string | null }
  situacao_recalculada?: { para: number; nome: string }
  situacao_aviso?: string
  situacao_devia_ser?: { para: number; nome: string }
  chamado_apagado?: boolean
  chamado_mantido?: { id: string; motivo: string } | null
  chamado_reapontado?: boolean
  // O passo do chamado falhou depois de a situação já ter sido gravada.
  chamado_aviso?: string
}
const CONDICOES_COM_PREJUIZO = ['Extraviado', 'Sucata', 'Manutenção']
// Linhas neutras não decidem a situação do pedido (espelho de
// linhas_que_contam no back): sobrando só elas, a situação volta.
const CONDICOES_NEUTRAS = ['Entregue', 'Não devolvido']
// O que a exclusão desfaz, em português simples, pro diálogo de confirmação.
// A regra fina é do back, então o texto é condicional.
function textoDoQueDesfaz(row: DevolutionRow): string {
  const itens: string[] = []
  const cond = row.condicao_produto || ''
  if (CONDICOES_COM_PREJUIZO.includes(cond)) {
    itens.push(cond === 'Manutenção'
      ? '• o débito do conserto que o DaVinci lançou sozinho na tela Reembolso para esta linha (se o valor ainda for só o débito do conserto e ninguém conferiu, anotou protocolo ou observação)'
      : `• o prejuízo ${cond} que o DaVinci lançou sozinho na tela Reembolso para esta linha (se ninguém lançou reembolso, conferiu, anotou protocolo ou observação)`)
  }
  if (row.manutencao && cond !== 'Manutenção') {
    itens.push('• o débito do conserto (Manutenção) que o DaVinci lançou para esta linha, se ninguém mexeu nele')
  }
  if (row.pedido_bling) {
    const outras = lancados.value.some((c) =>
      !!c.salvo && c.salvo.id !== row.id && c.salvo.pedido_bling === row.pedido_bling
      && !CONDICOES_NEUTRAS.includes(c.salvo.condicao_produto || ''))
    if (outras) {
      itens.push('• a situação do pedido no Bling é recalculada pelas outras linhas do pedido (se elas não definirem a situação, volta para a de antes da devolução)')
    } else {
      const hoje = row.situacao_bling_nome ? ` (hoje: ${row.situacao_bling_nome})` : ''
      itens.push(`• se não sobrar outra linha que decida a situação: ela volta no Bling para a de antes da devolução${hoje}, se ninguém mudou à mão`)
    }
  }
  if (row.tem_chamado) {
    itens.push('• o chamado aberto automaticamente é apagado se nenhuma linha que ficar pedir chamado e ninguém tiver mexido nele (sem protocolo, sem resposta, sem observação)')
  }
  return itens.length ? `\n\nO DaVinci também desfaz:\n${itens.join('\n')}\n\nO que alguém já mexeu fica, e você recebe o aviso.` : ''
}
// Toast do resultado: o resumo some sozinho; o que NÃO foi desfeito fica até fechar.
function avisarExclusao(row: DevolutionRow, res: ExclusaoResult) {
  const partes: string[] = []
  if (res.situacao_revertida) partes.push(`Pedido voltou para ${res.situacao_revertida.nome}`)
  else if (res.situacao_recalculada) partes.push(`Pedido foi para ${res.situacao_recalculada.nome}`)
  const np = res.prejuizos_apagados?.qtd || 0
  if (np) partes.push(`${np} prejuízo${np > 1 ? 's' : ''} apagado${np > 1 ? 's' : ''} (${brl(res.prejuizos_apagados?.total)})`)
  if (res.chamado_apagado) partes.push('chamado apagado')
  const linhas = partes.length ? [partes.join(' · ')] : []
  if (res.estoque_estornado) linhas.push(res.mensagem || 'Estoque estornado no Bling.')
  pushToast({ kind: 'success', title: 'Lançamento excluído', lines: linhas })

  const avisos: string[] = []
  if (res.situacao_aviso) avisos.push(res.situacao_aviso)
  for (const m of res.prejuizos_mantidos || []) {
    const valor = m.valor ?? m.prejuizo
    const de = valor != null ? ` de ${brl(valor)}` : ''
    avisos.push(`O prejuízo ${m.tipo || ''}${de} ficou: ${m.motivo || 'alguém mexeu nele'}. Confira na tela Reembolso.`)
  }
  if (res.chamado_mantido) avisos.push(`O chamado ficou: ${res.chamado_mantido.motivo}. Confira na aba Chamados.`)
  if (res.chamado_aviso) avisos.push(res.chamado_aviso)
  if (avisos.length) {
    pushToast({
      kind: res.situacao_devia_ser ? 'error' : 'warning',
      title: row.pedido_bling ? `Pedido ${row.pedido_bling}: nem tudo foi desfeito` : 'Nem tudo foi desfeito',
      lines: avisos,
    }, 0)
  }
  // Situação nova (voltou ou recalculada): as outras peças do pedido refletem na
  // hora — o back já gravou o espelho. (O chamado apagado/reapontado das outras
  // peças vem na releitura que a lixeira dispara logo depois.)
  const nova = res.situacao_revertida
    ? { para: res.situacao_revertida.para, nome: res.situacao_revertida.nome }
    : res.situacao_recalculada
  if (nova && row.pedido_bling) {
    for (const c of lancados.value) {
      if (c.salvo && c.salvo.pedido_bling === row.pedido_bling) {
        c.salvo.situacao_bling_id = nova.para
        c.salvo.situacao_bling_nome = nova.nome
      }
    }
  }
}
async function excluir(c: Cartao) {
  const row = c.salvo
  if (!props.canDelete || !row || c.ocupado) return
  const label = row.sku || row.pedido_bling || 'esta devolução'
  if (row.estoque_status === 'conferindo') {
    pushToast({
      kind: 'warning',
      title: 'Espere a conferência do estoque',
      lines: ['O DaVinci ainda está conferindo no Bling se a entrada deste item entrou (até 10 min).'],
    }, 0)
    return
  }
  if (row.estoque_status === 'sem_confirmacao' && temEstornoPendente(row)) {
    pushToast({
      kind: 'warning',
      title: 'Confira o estorno antes de excluir',
      lines: ['O estorno anterior pode ter saído no Bling — use “Estorno já saiu” ou “Tentar de novo” no cartão da peça (excluir agora daria baixa 2×).'],
    }, 0)
    return
  }
  // Estoque já devolvido ao Bling: com o movimento registrado, o back dá BAIXA
  // da mesma quantidade antes de excluir (03/09, Eduardo). Lançamento antigo
  // sem registro não tem como ser estornado daqui.
  const estornavel = !!row.estoque_mov_sku && !row.estoque_mov_revertido_at
  const stockNote = estornavel
    ? ` O estoque devolvido no Bling (${row.estoque_mov_qty ?? 1} un. de ${row.estoque_mov_sku}) será ESTORNADO automaticamente (saída).`
    : row.estoque_status === 'falhou'
      ? '' // a entrada não aconteceu: nada a estornar
      : row.estoque_status === 'sem_confirmacao'
        ? ' A entrada deste item no Bling NÃO foi confirmada: confira o extrato do SKU — se ela entrou, ajuste direto no Bling.'
        : (row.data_devolvido_estoque
            ? ' O estoque já devolvido no Bling NÃO será estornado (lançamento antigo, sem registro do movimento) — ajuste-o direto no Bling.'
            : '')
  if (!confirm(`Remover o lançamento de "${label}"? Esta ação não pode ser desfeita.${stockNote}${textoDoQueDesfaz(row)}`)) return
  c.ocupado = 'excluindo'
  c.erro = null
  try {
    const res = await api<ExclusaoResult>(
      `/api/devolutions/${encodeURIComponent(row.id)}`,
      { method: 'DELETE' },
    )
    lancados.value = lancados.value.filter((x) => x.key !== c.key)
    fecharAvisoConferindo(row.id)
    if (res) avisarExclusao(row, res)
    emit('mudou')
    // Excluir para relançar (ex.: como Usado, 302146): a unidade excluída volta
    // na hora como peça a lançar — e as outras peças releem o selo do chamado.
    void carregar()
  } catch (e: any) {
    bloqueia(c, apiError(e), 'Lançamento não excluído')
    // O servidor pode ter marcado a linha (estorno que pode ter saído): relê
    // para os botões certos aparecerem.
    const code = e?.data?.detail?.code
    if (code === 'estoque_estorno_falhou' || code === 'estoque_estorno_sem_confirmacao' || code === 'estoque_conferindo') {
      try {
        aplicarEstoque(row.id, await api<DevolutionRow>(`/api/devolutions/${encodeURIComponent(row.id)}`))
        agendarConferencia()
      } catch {
        // a próxima releitura corrige
      }
    }
  } finally {
    c.ocupado = ''
  }
}

// ── ciclo de vida ────────────────────────────────────────────────────────────
onMounted(() => {
  window.addEventListener('keydown', aoTeclar, true)
  void carregar()
})
// Outro pedido: recomeça do zero (rascunho de um não vaza pro outro).
watch(() => props.pedido, () => {
  pararConferencia()
  lancados.value = []
  rascunhos.value = []
  erroPecas.value = null
  erroLancamentos.value = null
  carregou.value = false
  void carregar()
})
// Os links moram em Provas: as peças por lançar acompanham o que estiver lá
// (vídeo respondido pela equipe com a janela aberta, link colado, link apagado).
watch(() => [props.linkEnvio, props.linkAbertura, props.videoLink, props.observacao] as const, () => {
  for (const c of rascunhos.value) {
    c.f.link_envio = (props.linkEnvio ?? props.videoLink ?? '') || ''
    c.f.link_abertura = props.linkAbertura || ''
    c.f.observacao = props.observacao || ''
  }
})
onBeforeUnmount(() => {
  desmontado = true
  window.removeEventListener('keydown', aoTeclar, true)
  pararConferencia()
})

// ── mostrar / esconder (16/11/2026, Vinicius) ────────────────────────────────
// "Quando o pedido tiver mais de 1 produto… podemos trabalhar com mostrar e
// esconder: relógio lançado, estoque tal, verdinho; aparelho celular → mostrar,
// daí aparece o lançamento." Pedido com mais de uma peça: cada peça é uma
// linha-resumo (condição + estoque, na cor) e o cartão inteiro abre no "mostrar".
// Abre sozinha só a peça que pede ação agora — a primeira que falta lançar — e
// não dá pra esconder o que a pessoa perderia de vista: estoque que não entrou,
// erro, salvando, mudança não salva. Pedido de uma peça só fica sempre aberto.
// 16/11/2026 (Vinicius, de novo): "todos os produtos podem vir na tela fechados,
// daí se quiser clicamos em mostrar — não precisa vir nenhum aberto": vale também
// pro pedido de uma peça e pra peça que falta lançar.
const abertoManual = reactive<Record<string, boolean>>({})
const recolhivel = computed(() => cartoes.value.length > 0)
function prendeAberto(c: Cartao): boolean {
  if (c.erro || c.ocupado) return true
  return c.salvo ? temMudanca(c) || estoquePendente(c.salvo) : rascunhoMexido(c)
}
function aberto(c: Cartao): boolean {
  if (prendeAberto(c)) return true
  return abertoManual[c.key] ?? false
}
function alternar(c: Cartao) {
  abertoManual[c.key] = !aberto(c)
}
// Verde só quando a peça TERMINOU (Vinicius, 16/11: "se tiver em manutenção não
// pode estar lançado em verde, pois parece que resolveu. Resolvido/lançado é
// somente quando lança estoque ou dá perdimento etc."): entrou no estoque, ou é
// perdimento / extraviado / não devolvido / trocado. Com o técnico, sem condição
// ou com a entrada de estoque pendurada, a data fica cinza.
function noTecnico(c: Cartao): boolean {
  const s = c.salvo
  return !!s && s.condicao_produto === 'Manutenção' && !(s.devolver_estoque && s.manutencao_destino)
}
function terminou(c: Cartao): boolean {
  const s = c.salvo
  return !!s && !!s.condicao_produto && !noTecnico(c) && !estoquePendente(s)
}
// Etiqueta da condição na linha-resumo (a cor diz o destino da peça).
function resumoCondicao(c: Cartao): { texto: string; cor: string; dica: string } | null {
  const s = c.salvo
  if (!s) return null
  const cond = s.condicao_produto
  if (!cond) return { texto: 'sem condição', cor: COR.atencao, dica: 'Lançada sem condição: escolha Novo, Usado ou Perdimento' }
  if (cond === 'Manutenção') {
    if (s.devolver_estoque && s.manutencao_destino) {
      const d = rotuloCondicao(s.manutencao_destino)
      return { texto: `${d} · voltou do técnico`, cor: d === 'Novo' ? COR.ok : d === 'Usado' ? COR.atencao : COR.erro, dica: 'Voltou do técnico' }
    }
    const partes = ['no técnico', s.tecnico, s.prazo ? `até ${ddmm(s.prazo)}` : ''].filter(Boolean)
    return { texto: partes.join(' · '), cor: COR.tecnico, dica: 'A peça está com o técnico' }
  }
  const cor = cond === 'Novo' ? COR.ok : cond === 'Usado' ? COR.atencao : cond === 'Sucata' ? COR.erro : COR.neutro
  return { texto: rotuloCondicao(cond), cor, dica: s.motivo_devolucao || '' }
}

// O pai pode perguntar antes de fechar a janela se ficou algo por salvar/lançar.
function temMudancaNaoSalva(): boolean {
  return lancados.value.some(temMudanca) || rascunhos.value.some(rascunhoMexido)
}
// Lançando / salvando / mexendo no estoque: a janela não fecha nem resolve no meio.
function ocupado(): boolean {
  return lancando.value || cartoes.value.some((c) => !!c.ocupado)
}
// O que a prévia do texto do chamado precisa: as peças como estão AGORA na tela
// (lançadas e por lançar), com o motivo que a pessoa escolheu.
function linhasParaPrevia() {
  return cartoes.value.map((c) => ({
    sku: skuDe(c) || null,
    produtos: produtoDe(c) || null,
    quantidade: qtdDe(c) || 1,
    motivo_devolucao: c.f.motivo_devolucao || null,
    observacao: c.f.observacao || null,
  }))
}
// O prazo pra contestar mais próximo entre as peças lançadas (o bloco Chamado
// mostra uma vez só — antes vinha em vermelho em cada cartão).
function prazoContestacao(): string | null {
  const prazos = lancados.value
    .map((c) => c.salvo?.prazo_contestacao)
    .filter((v): v is string => !!v)
    .sort()
  return prazos[0] || null
}
defineExpose({ recarregar, temMudancaNaoSalva, ocupado, gravarLink, anexarFotos, removerFoto, linhasParaPrevia, prazoContestacao })
</script>

<template>
  <div class="space-y-2 text-xs">
    <div v-if="!carregou" class="flex items-center gap-1.5 py-2 text-muted-foreground">
      <Loader2 class="size-3.5 animate-spin" /> buscando as peças do pedido…
    </div>

    <template v-else>
      <div v-if="erroLancamentos" class="text-[11px] text-red-600 dark:text-red-400">
        Não deu pra ler os lançamentos deste pedido: {{ erroLancamentos }}
        <button type="button" class="ml-1 underline" :disabled="carregando" @click="carregar()">tentar de novo</button>
      </div>
      <div v-if="erroPecas" class="text-[11px] text-red-600 dark:text-red-400">
        Não deu pra buscar as peças do pedido: {{ erroPecas }}
        <button type="button" class="ml-1 underline" :disabled="carregando" @click="carregar()">tentar de novo</button>
      </div>

      <div v-if="cartoes.length" class="flex flex-wrap items-center justify-between gap-2">
        <span class="text-[11px] text-muted-foreground">
          {{ resumo }}
          <Loader2 v-if="carregando" class="ml-1 inline size-3 animate-spin" />
        </span>
        <button
          v-if="canEdit && rascunhos.length >= 2"
          type="button"
          :class="BOTAO_PRINCIPAL"
          class="inline-flex items-center gap-1"
          :disabled="lancando"
          title="Lança todas as peças que faltam, uma depois da outra (cada uma pode perguntar o destino do estoque)"
          @click="lancar(rascunhos)"
        >
          <Loader2 v-if="lancando" class="size-3.5 animate-spin" />
          <Plus v-else class="size-3.5" />
          lançar todas as peças
        </button>
      </div>

      <div
        v-for="c in cartoes"
        :key="c.key"
        class="space-y-2 rounded-md border p-2.5"
        :class="c.salvo ? (temMudanca(c) ? 'border-amber-400 dark:border-amber-600' : '') : 'border-dashed'"
        :data-lancamento="c.salvo ? c.salvo.id : 'rascunho'"
      >
        <!-- Cabeçalho: SKU · produto · ×qtd · lançada/falta lançar · estoque · lixeira -->
        <div class="flex items-center gap-2">
          <span class="shrink-0 font-mono text-[10.5px] text-muted-foreground">{{ skuDe(c) || '—' }}</span>
          <span class="min-w-0 flex-1 truncate font-semibold" :title="produtoDe(c) || ''">{{ produtoDe(c) || '—' }}</span>
          <span class="shrink-0 tabular-nums text-muted-foreground">×{{ qtdDe(c) }}</span>
          <template v-if="c.salvo">
            <span
              v-if="resumoCondicao(c)"
              class="max-w-[230px] shrink-0 truncate whitespace-nowrap"
              :class="[ETIQUETA, resumoCondicao(c)!.cor]"
              :title="resumoCondicao(c)!.dica"
              data-resumo-condicao
            >{{ resumoCondicao(c)!.texto }}</span>
            <span
              class="shrink-0 whitespace-nowrap"
              :class="[ETIQUETA, terminou(c) ? COR.ok : COR.neutro]"
              :title="`Lançada em ${fmtDateTime(c.salvo.created_at)}${isAdmin ? ` · última alteração em ${fmtDateTime(c.salvo.updated_at)}` : ''}${terminou(c) ? '' : ' · ainda não terminou: só fica verde quando entra no estoque ou é dado como perdido'}`"
              :data-terminou="terminou(c) ? 'sim' : 'nao'"
            >{{ noTecnico(c) ? 'desde' : 'lançada' }} {{ ddmm(c.salvo.created_at) }}</span>
            <span
              v-if="etiquetaEstoque(c.salvo).texto"
              class="inline-flex max-w-[220px] shrink-0 items-center gap-1 truncate whitespace-nowrap"
              :class="[ETIQUETA, etiquetaEstoque(c.salvo).cor]"
              :title="etiquetaEstoque(c.salvo).dica"
              :data-estoque-badge="c.salvo.estoque_status || ''"
            >
              <Loader2 v-if="etiquetaEstoque(c.salvo).girando" class="size-3 shrink-0 animate-spin" />
              {{ etiquetaEstoque(c.salvo).texto }}<template v-if="etiquetaEstoque(c.salvo).texto === 'estoque ok' && c.salvo.estoque_mov_sku"> · <span class="font-mono">{{ c.salvo.estoque_mov_sku }}</span></template>
            </span>
            <button
              v-if="canDelete"
              type="button"
              class="inline-flex size-6 shrink-0 items-center justify-center rounded text-muted-foreground hover:bg-red-100 hover:text-red-600 disabled:cursor-default disabled:opacity-50 dark:hover:bg-red-900/30 dark:hover:text-red-400"
              :disabled="!!c.ocupado"
              title="Remover lançamento (estoque devolvido ao Bling é estornado automaticamente quando o movimento está registrado)"
              @click="excluir(c)"
            >
              <Loader2 v-if="c.ocupado === 'excluindo'" class="size-3.5 animate-spin" />
              <Trash2 v-else class="size-3.5" />
            </button>
          </template>
          <span v-else class="shrink-0 whitespace-nowrap" :class="[ETIQUETA, COR.atencao]">falta lançar</span>
          <button
            v-if="recolhivel"
            type="button"
            class="order-last inline-flex shrink-0 items-center gap-0.5 whitespace-nowrap rounded border px-1.5 py-0.5 text-[11px] font-semibold hover:bg-muted disabled:cursor-default disabled:opacity-40"
            :disabled="prendeAberto(c)"
            :title="prendeAberto(c) ? 'Tem algo pendente nesta peça (não salvo, estoque que não entrou ou erro): fica aberta até resolver' : aberto(c) ? 'Esconder os campos desta peça' : 'Mostrar o lançamento desta peça'"
            data-mostrar
            @click="alternar(c)"
          >
            <template v-if="aberto(c)">esconder <ChevronUp class="size-3" /></template>
            <template v-else>mostrar <ChevronDown class="size-3" /></template>
          </button>
        </div>

        <div v-show="aberto(c)" class="space-y-2" data-corpo>

        <!-- Estoque que não entrou / em conferência -->
        <div
          v-if="c.salvo && estoquePendente(c.salvo)"
          class="space-y-1 rounded-md border px-2 py-1.5"
          :class="c.salvo.estoque_status === 'falhou'
            ? 'border-red-300 bg-red-50/60 dark:border-red-900 dark:bg-red-950/20'
            : 'border-amber-300 bg-amber-50/60 dark:border-amber-800 dark:bg-amber-950/20'"
          :data-estoque-status="c.salvo.estoque_status"
        >
          <div class="flex items-center gap-1 text-[11px] font-semibold">
            <Loader2 v-if="c.salvo.estoque_status === 'conferindo'" class="size-3 shrink-0 animate-spin" />
            <AlertCircle v-else-if="c.salvo.estoque_status === 'falhou'" class="size-3 shrink-0 text-red-600 dark:text-red-400" />
            <AlertTriangle v-else class="size-3 shrink-0 text-amber-600 dark:text-amber-400" />
            <template v-if="c.salvo.estoque_status === 'conferindo'">Conferindo no Bling…</template>
            <template v-else-if="c.salvo.estoque_status === 'falhou'">Estoque NÃO entrou</template>
            <template v-else>{{ temEstornoPendente(c.salvo) ? 'Estorno não confirmado' : 'Entrada não confirmada' }}</template>
          </div>
          <div v-if="c.salvo.estoque_status === 'conferindo'" class="text-[11px] leading-tight text-muted-foreground">
            O Bling respondeu com erro depois de receber: a entrada pode ter acontecido. O DaVinci confere sozinho em até 10 min — não lance de novo.
          </div>
          <div v-else-if="c.salvo.estoque_erro" class="text-[11px] leading-tight text-muted-foreground">{{ c.salvo.estoque_erro }}</div>
          <div
            v-if="tentandoSozinho(c.salvo)"
            class="inline-flex items-center gap-1 text-[11px] font-medium text-amber-700 dark:text-amber-300"
            data-estoque-tentando-sozinho
          >
            <Loader2 class="size-3 shrink-0 animate-spin" />
            tentando de novo sozinho ({{ c.salvo.estoque_auto_tentativas ?? 0 }} de 5)
          </div>
          <div class="text-[10px] text-muted-foreground">pedido em {{ fmtDateTime(c.salvo.estoque_tentativa_at || c.salvo.data_devolvido_estoque) }}</div>
          <div v-if="canEdit && c.salvo.estoque_status !== 'conferindo'" class="flex flex-wrap gap-1.5 pt-0.5">
            <button
              v-if="c.salvo.estoque_status === 'sem_confirmacao'"
              type="button"
              data-estoque-ja-entrou
              class="inline-flex items-center gap-1 rounded-md border border-emerald-300 bg-background px-2.5 py-1.5 text-xs font-semibold text-emerald-700 hover:bg-emerald-50 disabled:opacity-50 dark:border-emerald-800 dark:text-emerald-300 dark:hover:bg-emerald-950/30"
              :disabled="!!c.ocupado"
              :title="temEstornoPendente(c.salvo) ? 'Você viu a saída do estorno no extrato do Bling: o DaVinci só registra (não lança nada)' : 'Você viu a entrada no extrato do Bling: o DaVinci só registra (não lança nada)'"
              @click="estoqueJaEntrou(c)"
            >
              <CheckCircle2 class="size-3.5" />
              {{ temEstornoPendente(c.salvo) ? 'Estorno já saiu' : 'Já entrou' }}
            </button>
            <button
              type="button"
              data-estoque-tentar
              :class="BOTAO"
              class="inline-flex items-center gap-1 bg-background"
              :disabled="!!c.ocupado"
              @click="tentarEstoqueDeNovo(c)"
            >
              <Loader2 v-if="c.ocupado === 'estoque'" class="size-3.5 animate-spin" />
              <RotateCcw v-else class="size-3.5" />
              Tentar de novo
            </button>
          </div>
        </div>

        <!-- Campos -->
        <div class="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          <label class="block min-w-0">
            <span :class="ROTULO">Condição</span>
            <select
              :class="CAMPO"
              :value="condicaoDoSeletor(c)"
              :disabled="travado(c)"
              :title="vaiPraManutencao(c) ? 'A peça está na manutenção. Voltou do técnico? Escolha como voltou (Novo, Usado ou Perdimento) e salve: sai da manutenção e lança no estoque' : 'Perdimento = produto perdido, não volta pro estoque'"
              @change="mudarCondicao(c, $event)"
            >
              <option value="">—</option>
              <option v-if="vaiPraManutencao(c)" :value="NA_MANUTENCAO">{{ rotuloManutencao(c) }}</option>
              <option v-for="o in CONDICOES_PRINCIPAIS" :key="o.valor" :value="o.valor">{{ o.rotulo }}</option>
              <option disabled>──────────</option>
              <optgroup label="outros casos">
                <option v-for="o in CONDICOES_OUTROS" :key="o" :value="o">{{ o }}</option>
              </optgroup>
              <option v-for="o in condicoesAntigas(c)" :key="o" :value="o">{{ o }}</option>
            </select>
          </label>

          <label class="block min-w-0">
            <span :class="ROTULO">Motivo</span>
            <select v-model="c.f.motivo_devolucao" :class="CAMPO" :disabled="travado(c)" title="Bloqueado, Golpe, Item faltando, Não recebido e Danificado (Outros) abrem chamado sozinhos">
              <option value="">—</option>
              <option v-for="m in MOTIVOS_DEVOLUCAO" :key="m" :value="m">{{ m }}</option>
              <option v-for="m in motivosAntigos(c)" :key="m" :value="m">{{ m }}</option>
            </select>
          </label>

          <label v-if="isAdmin" class="block min-w-0">
            <span :class="ROTULO">Custo do produto</span>
            <input
              v-model="c.f.custo_produto"
              type="text"
              inputmode="decimal"
              class="text-right tabular-nums"
              :class="[CAMPO, lerNumero(c.f.custo_produto) === undefined ? CAMPO_ERRO : '']"
              :disabled="travado(c)"
            />
          </label>

          <div v-if="c.salvo" class="min-w-0">
            <span :class="ROTULO" title="SKU em que a unidade foi lançada no estoque do Bling">SKU no estoque</span>
            <div :class="CAMPO_LEITURA" :title="etiquetaEstoque(c.salvo).dica">
              <Undo2 v-if="skuLancado(c.salvo).riscado" class="size-3 shrink-0 text-muted-foreground" />
              <span class="truncate font-mono text-[10.5px]" :class="[skuLancado(c.salvo).cor, skuLancado(c.salvo).riscado ? 'line-through' : '']">{{ skuLancado(c.salvo).texto }}</span>
            </div>
          </div>

          <label class="block min-w-0 sm:col-start-1">
            <span :class="ROTULO" title="Vai pra manutenção?">Vai pra manutenção?</span>
            <select
              :class="CAMPO"
              :value="vaiPraManutencao(c) ? 'sim' : 'nao'"
              :disabled="travado(c)"
              title="Sim = a peça vai pro técnico (grava a condição Manutenção). Quando voltar, responda Não e escolha a condição."
              @change="mudarManutencao(c, $event)"
            >
              <option value="nao">Não</option>
              <option value="sim">Sim</option>
            </select>
          </label>

          <label class="block min-w-0">
            <span :class="ROTULO">Técnico</span>
            <select v-model="c.f.tecnico" :class="CAMPO" :disabled="travado(c) || !manutencaoHabilitada(c)">
              <option value="">—</option>
              <option v-for="t in TECNICOS" :key="t" :value="t">{{ t }}</option>
              <option v-for="t in tecnicosAntigos(c)" :key="t" :value="t">{{ t }}</option>
            </select>
          </label>

          <label class="block min-w-0">
            <span :class="ROTULO">Custo manutenção</span>
            <input
              v-model="c.f.custo_manutencao"
              type="text"
              inputmode="decimal"
              class="text-right tabular-nums"
              :class="[CAMPO, lerNumero(c.f.custo_manutencao) === undefined || faltaCustoManutencao(c) ? CAMPO_ERRO : '']"
              :disabled="travado(c) || !manutencaoHabilitada(c)"
              :placeholder="faltaCustoManutencao(c) ? 'obrigatório' : ''"
              title="Custo + técnico preenchidos mandam o lançamento pro Reembolso. Ao sair da Manutenção o custo é obrigatório."
            />
          </label>

          <div class="min-w-0">
            <span :class="ROTULO">Prazo</span>
            <div :class="CAMPO_LEITURA" title="Prazo: 30 dias da inserção">
              <template v-if="c.salvo && c.salvo.condicao_produto === 'Manutenção' && c.salvo.prazo">
                <span
                  class="truncate"
                  :class="prazoOverdue(c.salvo.prazo) ? 'font-semibold text-red-600 dark:text-red-400' : ''"
                  :title="`Prazo: 30 dias da inserção · ${fmtDate(c.salvo.prazo)} · ${prazoDiasLabel(c.salvo.prazo)}`"
                >{{ fmtDate(c.salvo.prazo) }} · {{ prazoDiasLabel(c.salvo.prazo) }}</span>
              </template>
              <span v-else-if="vaiPraManutencao(c)" class="truncate text-muted-foreground">30 dias do lançamento</span>
              <span v-else class="text-muted-foreground">—</span>
            </div>
          </div>

        </div>

        <!-- Manutenção: devolver ao estoque (só pra quem pode) -->
        <div
          v-if="c.salvo && c.salvo.condicao_produto === 'Manutenção' && canSeeStockToggle(c.f.condicao_produto)"
          class="flex flex-wrap items-center gap-2"
        >
          <template v-if="!c.salvo.devolver_estoque">
            <button
              type="button"
              :class="BOTAO"
              class="inline-flex items-center gap-1"
              :disabled="travado(c)"
              title="Devolver ao estoque: pergunta como a peça voltou (Novo / Usado / Perdimento) e lança no Bling"
              @click="alternarVoltaDoTecnico(c)"
            >
              <Wrench class="size-3.5" /> voltou do técnico
            </button>
            <span class="text-[11px] text-muted-foreground">pergunta como voltou e devolve ao estoque no Bling</span>
          </template>
          <template v-else>
            <span :class="[ETIQUETA, COR.ok]">voltou do técnico<template v-if="c.salvo.manutencao_destino"> · {{ rotuloCondicao(c.salvo.manutencao_destino) }}</template></span>
            <button
              type="button"
              :class="BOTAO"
              class="inline-flex items-center gap-1"
              :disabled="travado(c)"
              title="Desliga o devolver ao estoque: a entrada lançada no Bling é estornada"
              @click="alternarVoltaDoTecnico(c)"
            >
              <Undo2 class="size-3.5" /> desfazer a volta
            </button>
          </template>
        </div>
        <label
          v-else-if="!c.salvo && canSeeStockToggle(c.f.condicao_produto)"
          class="flex items-center gap-1.5 text-[11px]"
          title="Marcado: ao lançar, pergunta como a peça voltou (Novo / Usado / Perdimento) e já devolve ao estoque"
        >
          <input v-model="c.devolver_estoque" type="checkbox" :disabled="travado(c)" />
          já voltou do técnico: devolver ao estoque ao lançar
        </label>

        <!-- Chamado (16/11/2026, Vinicius: "tá muito sobrecarregado… pode tirar tudo"):
             o chamado, o prazo pra contestar e o andamento ficam UMA vez no bloco
             Chamado da janela. Aqui só o que pede ação nesta peça: o pedido de senha
             que não saiu. -->
        <div v-if="c.salvo && senhaComProblema(c.salvo)" class="text-[11px] leading-tight" :class="senhaClass(c.salvo)" data-senha-problema>
          {{ senhaLabel(c.salvo) }}
          <button
            v-if="podePedirSenhaDeNovo(c.salvo)"
            type="button"
            class="ml-1 inline-flex items-center rounded border border-current px-1 align-middle text-[10px] font-medium hover:bg-red-50 disabled:opacity-50 dark:hover:bg-red-900/20"
            :disabled="!!c.ocupado"
            title="Pedir de novo a senha ao comprador (resolva antes o que impediu, ex.: loja deslogada)"
            @click="pedirSenhaDeNovo(c)"
          >{{ c.ocupado === 'senha' ? '…' : '↻ pedir de novo' }}</button>
        </div>

        <!-- Erro do cartão + botões -->
        <div v-if="c.erro" class="text-[11px] text-red-600 dark:text-red-400">{{ c.erro }}</div>
        <div v-if="canEdit && (!c.salvo || temMudanca(c) || c.ocupado === 'salvando')" class="flex flex-wrap items-center justify-end gap-2">
          <template v-if="c.salvo">
            <span v-if="c.ocupado !== 'salvando'" :class="[ETIQUETA, COR.atencao]" title="Alterações não salvas neste lançamento">não salvo</span>
            <button type="button" :class="BOTAO" class="inline-flex items-center gap-1" :disabled="!!c.ocupado" title="Volta o cartão ao que está salvo" @click="desfazer(c)">
              <Undo2 class="size-3.5" /> desfazer
            </button>
            <button type="button" :class="BOTAO_PRINCIPAL" class="inline-flex items-center gap-1" :disabled="!!c.ocupado" @click="salvar(c)">
              <Loader2 v-if="c.ocupado === 'salvando'" class="size-3.5 animate-spin" />
              {{ c.ocupado === 'salvando' ? 'salvando…' : 'salvar' }}
            </button>
          </template>
          <button
            v-else
            type="button"
            :class="BOTAO_PRINCIPAL"
            class="inline-flex items-center gap-1"
            :disabled="lancando || !!c.ocupado"
            @click="lancar([c])"
          >
            <Loader2 v-if="c.ocupado === 'lancando'" class="size-3.5 animate-spin" />
            <Plus v-else class="size-3.5" />
            {{ c.ocupado === 'lancando' ? 'lançando…' : 'lançar' }}
          </button>
        </div>
        </div>
      </div>

      <div
        v-if="!cartoes.length && !erroPecas && !erroLancamentos"
        class="rounded-md border border-dashed px-3 py-3 text-center text-xs text-muted-foreground"
      >nada pra lançar neste pedido</div>
    </template>

    <!-- Modais que abrem ANTES das chamadas de estoque no Bling (os mesmos da tela
         antiga). No <body>: ficam por cima da janela, onde quer que o bloco esteja. -->
    <Teleport to="body">
      <DevolucaoManutencaoModal
        :open="manutencaoModal.open"
        :sku="manutencaoModal.sku"
        @confirm="onManutencaoConfirm"
        @cancel="onManutencaoCancel"
      />
      <DevolucaoTrocaSkuModal
        :open="trocaModal.open"
        :sold-sku="trocaModal.soldSku"
        @confirm="onTrocaConfirm"
        @cancel="onTrocaCancel"
      />
      <DevolucaoEstoqueModal
        :open="estoqueModal.open"
        :sku="estoqueModal.sku"
        :condicao="estoqueModal.condicao"
        @confirm="onEstoqueConfirm"
        @cancel="onEstoqueCancel"
      />
    </Teleport>
  </div>
</template>
