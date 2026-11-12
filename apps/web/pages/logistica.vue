<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { Plus, RefreshCw, X, Trash2, Search, Send, ImagePlus, ChevronLeft, ChevronRight, Copy, NotebookPen, ArrowLeftRight, UserRound, MessageCircle, Megaphone, MapPin, Mail, Ban, ExternalLink, Combine, Download } from 'lucide-vue-next'
import { isoToday } from '~/lib/date'

definePageMeta({
  middleware: ['permission'],
  permission: { resource: 'logistica', action: 'view' },
})

const { api } = useApi()
const canEdit = useCan('logistica', 'edit')
const isAdmin = useIsAdmin()
const toasts = useToasts()

// Botão "Mostrar tudo": ligado = inclui também os pedidos que o painel
// normalmente esconde (resolvidos/desconsiderados). Só muda a exibição.
const mostrarTudo = ref(false)
// Modal do botão INFORMAR (admin-only) — relatório via Threema.
const informarOpen = ref(false)
// Aba Amazon: Informar (lista de Envio próprio em trânsito + quem recebe os
// avisos do robô) e Mensagens ao cliente (textos dos e-mails ao comprador).
const informarAmazonOpen = ref(false)
const mensagensClienteOpen = ref(false)
// "Igual ao Margem": admins + o gerente. Espelha _EMAILS_EXTRAS['logistica_amazon']
// no backend (routers/informar.py) — mudou lá, muda aqui.
const _auth = useAuthStore()
const INFORMAR_AMAZON_USERS = ['sa.geral@tutamail.com']
const canInformarAmazon = computed(() => {
  const email = _auth.user?.email?.toLowerCase()
  return isAdmin.value || (!!email && INFORMAR_AMAZON_USERS.includes(email))
})

// Abas por marketplace + a aba Status (playbook único, compartilhado). A chave
// (Status Plataforma) é a mesma pra todas — só o ML enriquece a assinatura hoje.
// "Flex" (02/10/2026) não é um marketplace: é uma VISÃO A MAIS que junta os
// pedidos Flex do ML (Envios Flex) e da Shopee (Entrega Direta) — eles
// continuam nas abas ML/Shopee com o selo "Flex" (backend: ?envio=flex).
const PLATAFORMA_TABS = [
  { key: 'ml', label: 'Mercado Livre' },
  { key: 'shopee', label: 'Shopee' },
  { key: 'amazon', label: 'Amazon' },
  { key: 'tiktok', label: 'TikTok' },
  { key: 'flex', label: 'Flex' },
] as const
type PlataformaTab = (typeof PLATAFORMA_TABS)[number]['key']
// Quem vê o Flex: admin + cairo SA e churchill (05/11 e 11/11/2026; antes a lista `flex_usuarios` do .env).
// /api/flex/acesso responde 200 só para eles; para os outros a aba Flex não
// aparece e o selo/aviso "Flex" some dos pedidos (a API da Logística também já
// manda a linha sem o tipo de envio).
const podeVerFlex = ref(false)
// De onde o motoboy do Flex sai (aba Flex › Local de saída — editável por
// admin; São Bernardo do Campo / .sp de fábrica): os textos seguem o local.
const flexLocal = ref<{ cidade: string; lote: string }>({ cidade: 'São Bernardo do Campo', lote: 'sp' })
const ABAS_VISIVEIS = computed(() => PLATAFORMA_TABS.filter((t) => t.key !== 'flex' || podeVerFlex.value))
async function verificarFlex(): Promise<boolean> {
  try {
    const acesso = await api<{ cidade?: string; lote?: string }>('/api/flex/acesso')
    if (acesso?.cidade) flexLocal.value = { cidade: acesso.cidade, lote: acesso.lote || 'sp' }
    podeVerFlex.value = true
  } catch {
    podeVerFlex.value = false
  }
  return podeVerFlex.value
}
// A aba Flex mudou algo (aprovou, acertou estoque, trocou o local de saída):
// relê o resumo e o local — os textos da Logística seguem o local novo.
async function aoMudarFlex() {
  await Promise.all([carregarResumoFlex(), verificarFlex()])
}
const tab = ref<PlataformaTab | 'status'>('ml')

// Aba Amazon: a Amazon trata "Delivery by Amazon" (DBA) e "Envio próprio" como
// dois painéis (Seller Central › Gerenciar pedidos › Logística pelo vendedor),
// então aqui também. O canal vem do backend (`amazon_canal`: dba/proprio/fba;
// FBA fica junto do DBA — a Amazon entrega). Sem sinal nenhum é DBA, o normal
// da operação (Vinicius 15/09); Envio próprio é sempre identificado de forma
// positiva (MFN sem EasyShip ou serviço dos Correios no Bling).
type AmazonSub = 'dba' | 'proprio'
const amazonSub = ref<AmazonSub>('dba')
function amazonSubDe(c: { amazon_canal?: string | null }): AmazonSub {
  return c.amazon_canal === 'proprio' ? 'proprio' : 'dba'
}

// Aba Flex: duas sub-abas, no mesmo padrão da Amazon. "Pedidos Flex" é a
// lista de sempre (mesmas colunas, filtros e ações); "Anúncios Flex" é a tela
// do Flex por anúncio (components/LogisticaFlexAnuncios: modo, aprovação,
// sincronizar, emergência e os pedidos Flex sem peça em SP).
type FlexSub = 'pedidos' | 'anuncios'
const flexSub = ref<FlexSub>('pedidos')
const flexAnuncios = computed(() => tab.value === 'flex' && flexSub.value === 'anuncios')

type MeliStatus = Record<string, string>

// Uma linha do balãozinho da coluna "Status Plataforma".
// `em` = ISO-8601 UTC; `fonte` = plataforma (data oficial do canal) | aprox
// (melhor estimativa que o canal deu) | davinci (quando o sistema viu mudar).
type StatusDetalhe = {
  campo: string
  rotulo: string
  valor: string
  em: string | null
  fonte: string | null
}

type Logistica = {
  id: string
  data: string | null
  pedido_bling: string | null
  pedido_marketplace: string | null
  plataforma: string | null
  conta: string | null
  // Itens do pedido (nome + SKU) vindos do espelho do Bling — coluna "Produto".
  produtos?: Array<{ sku: string | null; nome: string | null; quantidade: number | null }>
  meli_status: MeliStatus
  status_plataforma: string
  // Assinatura aberta campo a campo + desde quando cada um está assim (backend).
  status_detalhe?: StatusDetalhe[]
  rastreio: string | null
  localizacao: string | null
  // Quando os CORREIOS se moveram (17track). Vazio = a localização exibida
  // ainda é o proxy do marketplace, não o rastreio físico.
  localizacao_at?: string | null
  // Quando PERGUNTAMOS os Correios (mesmo sem evento novo) — é isto que
  // prova que o robô está vivo enquanto o pacote está parado.
  rastreio_lido_em?: string | null
  status_lido_em?: string | null
  divergencia: string | null
  status_bling: string | null
  chamado: string | null
  // Abertura automática do chamado pelo motor (a cada 5 min): última tentativa
  // e motivo da recusa do ML (vazio = nunca tentou / abriu). Só leitura.
  chamado_auto_at: string | null
  chamado_auto_erro: string | null
  // Ticket do pedido na aba Chamados (o aberto mais recente; senão o encerrado
  // mais recente). Responde "já abriram chamado?" enquanto a linha não tem
  // protocolo — na Amazon o SAFE-T é aberto na mão e não tem API. Só leitura.
  chamado_aba: {
    id: string
    chamado: string | null
    canal: string
    origem: string
    resolvido: boolean
    data: string | null
    created_at: string
  } | null
  // ---- Amazon (projeto de 15/09/2026) ----
  amazon_canal?: 'dba' | 'proprio' | 'fba' | null
  amazon_canal_label?: string
  servico_envio?: string | null
  // ---- Flex (02/10/2026) ----
  // Tipo de envio cru da plataforma (ML `self_service`; Shopee "90022 · Shopee
  // Entrega Direta") e se é Flex. null = ainda não lido. Selo "Flex".
  envio_tipo?: string | null
  envio_flex?: boolean | null
  postagem_data?: string | null
  // Previsão dos Correios (Bling) e data máxima da Amazon (LatestDeliveryDate).
  previsao_correios?: string | null
  prazo_entrega_amazon?: string | null
  entregue_em?: string | null
  problema_correios?: string | null
  // Pacote de VOLTA (devolução) — as colunas Rastreio/Localização são da IDA.
  // Vem da aba Acompanhamento (manual vence o automático). `volta_grave` =
  // apreensão, extravio, roubo… (perda do produto).
  volta_rastreio?: string | null
  volta_localizacao?: string | null
  volta_localizacao_em?: string | null
  volta_grave?: boolean
  cliente_nome?: string | null
  cliente_email?: string | null
  // Última leitura do pedido no Bling (serviço, rastreio, contato). No Envio
  // próprio sem …BR é o "Bling lido há X" da Localização.
  bling_enriquecido_em?: string | null
  aviso_previsao_correios_at?: string | null
  aviso_prazo_amazon_3d_at?: string | null
  aviso_prazo_amazon_vencido_at?: string | null
  mensagens_cliente?: Array<{
    evento: string
    evento_label: string
    assunto: string
    enviado_em: string | null
    erro: string | null
    tentativas: number
  }>
  // Suspensão de entrega no Melhor Envio pelo robô: pendente | solicitada | falhou.
  suspensao_status?: string | null
  suspensao_em?: string | null
  suspensao_detalhe?: string | null
  observacao: string | null
  // Casador da aba Status (backend): regra que casa com a chave deste pedido.
  acao_match: boolean
  acao_status_id: string | null
  acao_resumo: string[]
  // acao_monitorar=alguma regra casada pede monitoramento; acao_resolvido=chegou
  // ao fim da cadeia de status. Ocultamos a linha quando resolvido E sem monitorar.
  acao_monitorar: boolean
  acao_resolvido: boolean
  created_by: string | null
  created_at: string
  updated_at: string
}

type Opcoes = {
  field_order: string[]
  field_labels: Record<string, string>
  field_options: Record<string, string[]>
  status_bling_options: string[]
}

type Candidato = { status_bling: string; matches: number }

const rows = ref<Logistica[]>([])
const loading = ref(false)
const error = ref<string | null>(null)

const opcoes = ref<Opcoes>({ field_order: [], field_labels: {}, field_options: {}, status_bling_options: [] })

// Duas cargas ao mesmo tempo (a do setup, sempre em ML, e a da aba que o link
// `?tab=…` pede no onMounted): vale só a ÚLTIMA pedida. Antes a resposta lenta
// do ML chegava depois e a aba Shopee mostrava pedidos do ML (visto em 11/11/2026).
let refreshSeq = 0
async function refresh() {
  const seq = ++refreshSeq
  loading.value = true
  error.value = null
  try {
    // Cada aba de marketplace filtra server-side pela plataforma. A aba Status
    // usa outro carregador (refreshStatus); aqui caímos em ML só por garantia.
    // A aba Flex pede só os pedidos Flex (ML + Shopee); o filtro Plataforma
    // dela é na tela, sobre a lista já carregada.
    const plat = tab.value === 'status' ? 'ml' : tab.value
    const url = plat === 'flex' ? '/api/logistica?envio=flex' : `/api/logistica?plataforma=${plat}`
    carregarResumoFlex()
    const dados = await api<Logistica[]>(url)
    if (seq === refreshSeq) rows.value = dados
  } catch (e: any) {
    if (seq === refreshSeq) error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    if (seq === refreshSeq) loading.value = false
  }
}

// Recarrega os pedidos E as chaves da aba Status ao mesmo tempo. Como o casador
// (acao_resumo/acao_match) e o destaque vermelho vêm do backend por pedido, ao
// cadastrar uma regra nova na aba Status basta recarregar aqui: as linhas já
// casadas somem do vermelho e ganham as ações, sem esperar o status do ML mudar.
//
// Com permissão de edição, o botão também DISPARA o motor em segundo plano:
// re-enriquece o Status Plataforma dos pedidos PENDENTES do painel (as 4 abas
// de marketplace) e aplica no Bling a mudança de situação dos que já têm regra.
// Roda em background (pode passar do timeout do Cloudflare) e a lista se
// atualiza sozinha no poll. O MESMO motor também roda sozinho a cada 5 min no
// servidor (cron) — o botão vale pra "quero agora, sem esperar o próximo tick".
// "rastreio Correios": registra no 17track o que ainda não está e puxa a
// localização física. O cron faz igual a cada 15 min — o botão é pra não
// esperar. `sem_quota` = a conta do 17track ficou sem saldo e NADA atualiza até
// recarregar lá; sem esse aviso o operador ficaria clicando sem entender.
const atualizandoRastreio = ref(false)
// Faixa fixa no topo: enquanto o 17track estiver sem saldo NADA de Correios
// atualiza, e um toast que some não daria conta de explicar isso.
const correiosSemSaldo = ref(false)
const SEM_SALDO_TEXTO =
  'Os Correios pararam de atualizar: a conta do 17track está sem saldo. Enquanto isso a coluna Localização mostra o que o marketplace informa, que costuma atrasar. Para voltar ao normal é preciso comprar créditos em 17track.net (entrar na conta → Quota).'

async function carregarStatusCorreios() {
  try {
    const s = await api<{ sem_quota: boolean }>('/api/logistica/17track/status')
    correiosSemSaldo.value = !!s?.sem_quota
  } catch {
    // status é só informativo — falhar aqui não pode atrapalhar a tela.
  }
}

async function atualizarRastreio() {
  if (!canEdit.value || atualizandoRastreio.value) return
  atualizandoRastreio.value = true
  try {
    const res = await api<{
      linhas: number
      pendentes: number
      registrados: number
      atualizados: number
      sem_quota: boolean
    }>('/api/logistica/17track/register', { method: 'POST' })
    correiosSemSaldo.value = !!res?.sem_quota
    if (res?.sem_quota) {
      toasts.error('Correios sem saldo no 17track', SEM_SALDO_TEXTO)
    } else if (!res?.registrados && !res?.atualizados) {
      toasts.info('Tudo em dia', 'Nenhum rastreio novo para registrar e nenhuma novidade dos Correios.')
    } else {
      toasts.info(
        'Correios atualizados',
        `${res?.registrados || 0} rastreio(s) começaram a ser seguidos, ${res?.atualizados || 0} localização(ões) nova(s).`,
      )
    }
    await refresh()
  } catch (e: any) {
    // O trabalho roda em lotes com teto; se a resposta demorar demais ele
    // continua no servidor — dizer "deu erro" seria mentira.
    toasts.info(
      'Ainda processando',
      'A atualização dos Correios continua rodando no servidor. Recarregue a página em alguns minutos.',
    )
  } finally {
    atualizandoRastreio.value = false
  }
}

const recarregando = ref(false)
async function recarregar() {
  // O motor (enriquece Status Plataforma + aplica status no Bling) roda pras 4
  // abas de marketplace (ML, Shopee, TikTok, Amazon). Na aba Status o botão só
  // repuxa os pedidos e as chaves da aba Status.
  if (!canEdit.value || tab.value === 'status') {
    await Promise.all([refresh(), refreshStatus()])
    return
  }
  if (
    !confirm(
      'Recarregar vai atualizar o Status Plataforma dos pedidos pendentes do painel (todas as abas) e aplicar no Bling a mudança de situação dos que já têm regra definida. Continuar?',
    )
  )
    return
  recarregando.value = true
  let jobId: string | null = null
  try {
    const res = await api<{ enqueued: boolean; job_id?: string | null }>(
      '/api/logistica/recarregar',
      { method: 'POST' },
    )
    jobId = res?.job_id || null
    toasts.info('Recarregando em segundo plano', 'Leva uns 2-3 minutos; a lista atualiza sozinha.')
  } catch (e: any) {
    toasts.error('Não foi possível recarregar', e?.data?.detail?.code || e?.message || 'erro')
    recarregando.value = false
    return
  }
  // Repuxa a lista a cada 10s e acompanha o job pelo job_id até ele REALMENTE
  // terminar (teto de 10 min). O poll fixo de 4 min de antes parava cedo em
  // recarga longa e sem nenhum aviso — o botão parecia "não fazer nada". Agora
  // o spinner dura o job e no fim sai um toast com o resumo (ou o erro).
  for (let i = 0; i < 60; i++) {
    await new Promise((r) => setTimeout(r, 10000))
    await Promise.all([refresh(), refreshStatus()])
    if (!jobId) {
      if (i >= 23) break // sem job_id pra acompanhar: mantém o teto antigo de 4 min
      continue
    }
    try {
      const st = await api<{ status: string; resumo?: Record<string, number> | null }>(
        `/api/logistica/recarregar/${jobId}`,
      )
      if (st.status === 'complete') {
        const r = st.resumo
        if (r && r.pulado_ja_rodando) {
          // Já havia uma recarga rodando (o cron dos 5 min ou outro clique) —
          // este clique pegou carona nela em vez de rodar tudo de novo à toa.
          toasts.info(
            'Já tinha uma recarga em andamento',
            'Aproveitei a que estava rodando — a lista continua atualizando sozinha.',
          )
        } else if (r) {
          const atualizados =
            (r.enrich_updated || 0) +
            (r.shopee_enrich_updated || 0) +
            (r.tiktok_enrich_updated || 0) +
            (r.amazon_enrich_updated || 0)
          toasts.success('Recarregado', [
            `${atualizados} pedidos com Status Plataforma atualizado`,
            `${r.status_aplicados || 0} mudanças de status aplicadas no Bling`,
            `${r.cleanup || 0} pedidos finalizados removidos da lista`,
          ])
        } else {
          toasts.success('Recarregado', 'Lista atualizada.')
        }
        break
      }
      if (st.status === 'failed') {
        toasts.error('O recarregar falhou no servidor', 'Tenta de novo; se repetir, me avisa.')
        break
      }
      if (st.status === 'not_found') {
        // O servidor perdeu o registro do job (reinício/atualização do sistema
        // no meio, por exemplo). Não dá pra afirmar que falhou — só que não dá
        // mais pra acompanhar. Aviso neutro: a lista continua se atualizando
        // sozinha (poll de 30s) e, se nada mudar, é só clicar de novo.
        toasts.info(
          'Perdi o acompanhamento da recarga',
          'O servidor reiniciou no meio. A lista continua atualizando sozinha; se nada mudar, clica em Recarregar de novo.',
        )
        break
      }
    } catch {
      // Consulta de andamento falhou (rede etc.) → segue o poll; a lista
      // continua atualizando de qualquer jeito.
    }
  }
  recarregando.value = false
}

// ---- Busca + filtros (client-side sobre as linhas já carregadas) ----
const search = ref('')
const contaFilter = ref('all')
const statusBlingFilter = ref('all')
const dataInicioFilter = ref('')
const dataFimFilter = ref('')
// Só na aba Flex (ela junta ML e Shopee): '' = as duas.
const flexPlataformaFilter = ref<'' | 'ml' | 'shopee'>('')

const contas = computed(() =>
  [...new Set(rows.value.map((c) => c.conta).filter((v): v is string => !!v))].sort((a, b) =>
    a.localeCompare(b, 'pt-BR', { sensitivity: 'base' }),
  ),
)
const statusBlings = computed(() =>
  [...new Set(rows.value.map((c) => c.status_bling).filter((v): v is string => !!v))].sort((a, b) =>
    a.localeCompare(b, 'pt-BR', { sensitivity: 'base' }),
  ),
)

// ---- Padrão de Chamados/Conciliação (11/11/2026) ----
// A lista passa por camadas: `baseRows` (tudo menos Status Bling e cartão),
// depois o Status Bling (etiquetas) e o cartão clicado. Os cartões contam sem o
// filtro de cartão e as etiquetas sem o de Status Bling — clicar num não zera
// o outro (mesma régua do resumo do Chamados).
type CartaoFiltro =
  | 'all' | 'sem_regra' | 'monitorar' | 'problemas' | 'chamado'
  | 'prazo' | 'previsao' | 'divergencia' | 'correios_parado' | 'problemas_sem_chamado'
const cartaoFiltro = ref<CartaoFiltro>('all')
// Chamado aberto = o ticket da aba Chamados ainda não encerrado (o backend manda
// o aberto mais recente; sem aberto, o encerrado) — inclui o "robô abrindo".
// Sem ticket na aba, vale o protocolo anotado na linha.
function temChamadoAberto(c: Logistica): boolean {
  if (c.chamado_aba) return !c.chamado_aba.resolvido
  return !!(c.chamado || '').trim()
}
// Vinicius, 11/11/2026: "coisa que está no status Problemas e não tem chamado
// não tem ninguém olhando, tá parado, perdido" — vai no topo do Onde agir hoje.
function problemaSemChamado(c: Logistica): boolean {
  return normKey(c.status_bling) === 'problemas' && !temChamadoAberto(c)
}
// Vinicius 12/11/2026 (303624 dizia "pendente: abrir chamado" com o chamado já
// aberto e até pago): a coluna Chamado só repete o painel de Chamados — "Aberto"
// (tem chamado aberto lá, alguém está cuidando), "Concluído" (fechado lá) ou nada.
// Se já foi aberto na plataforma e com qual número é com quem olha o painel de
// Chamados; o número fica no Raio-X.
function situacaoChamado(c: Logistica): 'Aberto' | 'Concluído' | null {
  if (temChamadoAberto(c)) return 'Aberto'
  if (c.chamado_aba?.resolvido) return 'Concluído'
  return null
}
const COR_SITUACAO_CHAMADO: Record<'Aberto' | 'Concluído', string> = {
  Aberto: 'bg-blue-100 text-blue-800 dark:bg-blue-950/50 dark:text-blue-300',
  Concluído: 'bg-muted text-muted-foreground',
}
// "A acompanhar" esconde o que chegou ao fim da cadeia (resolvido) sem pedir
// monitoramento — MENOS o Problemas sem chamado aberto: em 11/11 eram 4 do ML
// (chamado encerrado, Bling ainda em Problemas) e 1 do TikTok que ninguém via.
function escondido(c: Logistica): boolean {
  return c.acao_resolvido && !c.acao_monitorar && !problemaSemChamado(c)
}
const ehProprio = computed(() => tab.value === 'amazon' && amazonSub.value === 'proprio')
function noCartao(c: Logistica, k: CartaoFiltro): boolean {
  switch (k) {
    case 'sem_regra': return precisaAtencao(c)
    case 'monitorar': return !!c.acao_monitorar
    case 'problemas': return normKey(c.status_bling) === 'problemas'
    case 'chamado': return !!(c.chamado || c.chamado_aba)
    case 'prazo': return prazoVencido(c)
    case 'previsao': return previsaoVencida(c)
    case 'divergencia': return !!c.divergencia
    case 'correios_parado': return ehCorreios(c.rastreio) && !c.entregue_em && leituraVelha(c)
    case 'problemas_sem_chamado': return problemaSemChamado(c)
    default: return true
  }
}
const mostrarSel = computed({
  get: () => (mostrarTudo.value ? 'tudo' : 'acompanhar'),
  set: (v: string) => { mostrarTudo.value = v === 'tudo' },
})

const baseRows = computed(() => {
  const q = search.value.trim().toLowerCase()
  const di = dataInicioFilter.value
  const df = dataFimFilter.value
  return rows.value.filter((c) => {
    // Painel de pendências: some quando já chegou ao fim da cadeia de status no
    // Bling (resolvido) E a regra casada não pede monitoramento — nada a fazer.
    // "Mostrar tudo" ligado ignora essa regra e exibe também os escondidos.
    if (!mostrarTudo.value && escondido(c)) return false
    // Aba Amazon: só a sub-aba escolhida (DBA / Envio próprio / sem classificação).
    if (tab.value === 'amazon' && amazonSubDe(c) !== amazonSub.value) return false
    // Aba Flex: filtro Plataforma (mesmos rótulos que o backend aceita).
    if (tab.value === 'flex' && flexPlataformaFilter.value === 'ml' && !isMl(c)) return false
    if (tab.value === 'flex' && flexPlataformaFilter.value === 'shopee' && !isShopee(c)) return false
    if (contaFilter.value !== 'all' && (c.conta || '') !== contaFilter.value) return false
    if (di && (!c.data || c.data < di)) return false
    if (df && (!c.data || c.data > df)) return false
    if (q) {
      const hay = [
        c.pedido_bling,
        c.pedido_marketplace,
        c.conta,
        c.rastreio,
        c.chamado,
        c.localizacao,
        c.volta_rastreio,
        c.volta_localizacao,
        c.divergencia,
        c.status_bling,
        c.status_plataforma,
        c.cliente_nome,
        c.servico_envio,
        // Busca também por produto: nome e SKU dos itens do pedido.
        ...(c.produtos || []).flatMap((p) => [p.nome, p.sku]),
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase()
      if (!hay.includes(q)) return false
    }
    return true
  })
})
function noStatusBling(c: Logistica): boolean {
  return statusBlingFilter.value === 'all' || (c.status_bling || '') === statusBlingFilter.value
}
// Base dos cartões (sem o filtro de cartão) e das etiquetas (sem o de Status Bling).
const semCartaoRows = computed(() => baseRows.value.filter(noStatusBling))
const semStatusRows = computed(() => baseRows.value.filter((c) => noCartao(c, cartaoFiltro.value)))
const filteredRows = computed(() => semCartaoRows.value.filter((c) => noCartao(c, cartaoFiltro.value)))

const cartoes = computed(() => {
  const base = semCartaoRows.value
  const n = (k: CartaoFiltro) => base.filter((c) => noCartao(c, k)).length
  const lista: Array<{ k: CartaoFiltro; label: string; n: number; cor: string; dica: string }> = [
    { k: 'all', label: mostrarTudo.value ? 'Pedidos' : 'A acompanhar', n: base.length, cor: '', dica: 'mostrar todos' },
    { k: 'sem_regra', label: 'Sem regra (decidir)', n: n('sem_regra'), cor: 'text-amber-600', dica: 'combinação que nenhuma regra da aba Status cobre' },
    { k: 'monitorar', label: 'Monitorar', n: n('monitorar'), cor: 'text-blue-600', dica: 'a regra da aba Status pede acompanhar' },
    { k: 'problemas', label: 'Problemas no Bling', n: n('problemas'), cor: 'text-red-600', dica: 'Status Bling = Problemas' },
    { k: 'chamado', label: 'Com chamado', n: n('chamado'), cor: 'text-slate-700 dark:text-slate-300', dica: 'tem protocolo ou chamado na aba Chamados' },
  ]
  if (ehProprio.value) {
    lista.push({ k: 'prazo', label: 'Prazo Amazon vencido', n: n('prazo'), cor: 'text-red-600', dica: 'passou a data máxima da Amazon e não foi entregue' })
  }
  return lista
})
function filtrarCartao(k: CartaoFiltro) {
  cartaoFiltro.value = cartaoFiltro.value === k ? 'all' : k
}

// Cor do Status Bling (chip da tabela e bolinha das etiquetas).
const SB_COR: Record<string, string> = {
  problemas: 'bg-red-100 text-red-800 dark:bg-red-950/50 dark:text-red-300',
  entregue: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/50 dark:text-emerald-300',
  atendido: 'bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300',
  'em andamento': 'bg-blue-100 text-blue-800 dark:bg-blue-950/50 dark:text-blue-300',
  'aguardando devolução': 'bg-amber-100 text-amber-800 dark:bg-amber-950/50 dark:text-amber-300',
  'aguardando cancelamento': 'bg-amber-100 text-amber-800 dark:bg-amber-950/50 dark:text-amber-300',
  manutenção: 'bg-violet-100 text-violet-800 dark:bg-violet-950/50 dark:text-violet-300',
  'em aberto': 'bg-sky-100 text-sky-800 dark:bg-sky-950/50 dark:text-sky-300',
}
const SB_DOT: Record<string, string> = {
  problemas: 'bg-red-600', entregue: 'bg-emerald-600', atendido: 'bg-slate-400', 'em andamento': 'bg-blue-600',
  'aguardando devolução': 'bg-amber-500', 'aguardando cancelamento': 'bg-amber-500', manutenção: 'bg-violet-500', 'em aberto': 'bg-sky-500',
}
function corStatusBling(s: string | null | undefined): string {
  return SB_COR[normKey(s)] || 'bg-muted text-foreground'
}
const statusPills = computed(() => {
  const cont = new Map<string, number>()
  for (const r of semStatusRows.value) cont.set(r.status_bling || '', (cont.get(r.status_bling || '') || 0) + 1)
  return [...cont.entries()]
    .sort((a, b) => b[1] - a[1])
    .map(([s, n]) => ({ s, n, rotulo: s || 'sem status', dot: SB_DOT[normKey(s)] || 'bg-slate-300' }))
})
function filtrarStatusBling(s: string) {
  statusBlingFilter.value = statusBlingFilter.value === s ? 'all' : s
}

// Raio-X: a linha clicada abre à direita (antes abria direto o "Editar caso").
const raioId = ref<string | null>(null)
const raioRow = computed(() => (raioId.value ? rows.value.find((r) => r.id === raioId.value) || null : null))
function abrirRaio(c: Logistica) {
  raioId.value = raioId.value === c.id ? null : c.id
}
// Botão "Atualizar status" do Raio-X: o mesmo de antes, da plataforma da linha.
function podeAtualizarStatus(c: Logistica): boolean {
  return canEdit.value && !!c.pedido_marketplace && (isMl(c) || isShopee(c) || isTiktok(c) || isAmazon(c))
}
function atualizarStatusPlataforma(c: Logistica) {
  if (isMl(c)) return atualizarMeli(c)
  if (isShopee(c)) return atualizarShopee(c)
  if (isTiktok(c)) return atualizarTiktok(c)
  if (isAmazon(c)) return atualizarAmazon(c)
}
const RAIO_BTN =
  'inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-[11px] font-semibold hover:bg-muted disabled:opacity-50'

// "Onde agir hoje": o que é um a um (ocorrência grave, Flex sem peça,
// suspensão que falhou) abre o Raio-X; o resto vira grupo e filtra o cartão.
type AgirItem = {
  chave: string; dot: string; titulo: string; detalhe: string; acao: string
  cartao?: CartaoFiltro; linha?: Logistica; dica?: string; grave?: boolean
}
const agirItens = computed<AgirItem[]>(() => {
  const base = baseRows.value
  const out: AgirItem[] = []
  const pedidoDe = (c: Logistica) => c.pedido_bling || c.pedido_marketplace || 'pedido'
  // Primeiro de tudo: Problemas sem chamado aberto (ninguém olhando).
  const semChamado = base.filter(problemaSemChamado)
  if (semChamado.length) {
    out.push({
      chave: 'g-problemas_sem_chamado',
      dot: 'bg-red-600',
      titulo: `Problemas sem chamado · ${semChamado.length}`,
      detalhe: `ninguém olhando · ${semChamado.slice(0, 3).map(pedidoDe).join(' · ')}${semChamado.length > 3 ? ' …' : ''}`,
      dica: `Status Bling "Problemas" e nenhum chamado aberto:\n${semChamado.map((c) => `${pedidoDe(c)} · ${c.conta || '—'}`).join('\n')}`,
      acao: 'Ver',
      cartao: 'problemas_sem_chamado',
      grave: true,
    })
  }
  for (const c of base.filter((r) => r.volta_grave).slice(0, 3)) {
    out.push({ chave: `grave-${c.id}`, dot: 'bg-red-600', titulo: `${pedidoDe(c)} · ${c.volta_localizacao || 'ocorrência grave'}`, detalhe: `${c.conta || '—'} · pacote da devolução`, acao: 'Abrir', linha: c })
  }
  for (const c of base.filter((r) => flexAlerta(r)).slice(0, 2)) {
    out.push({ chave: `flex-${c.id}`, dot: 'bg-red-600', titulo: `${pedidoDe(c)} · Flex sem peça em ${flexLocal.value.cidade}`, detalhe: flexAlerta(c) || '', acao: 'Abrir', linha: c })
  }
  for (const c of base.filter((r) => r.suspensao_status === 'falhou').slice(0, 2)) {
    out.push({ chave: `susp-${c.id}`, dot: 'bg-red-600', titulo: `${pedidoDe(c)} · suspensão falhou`, detalhe: resumoDetalheRobo(c.suspensao_detalhe) || 'tentar de novo', acao: 'Abrir', linha: c })
  }
  const grupo = (k: CartaoFiltro, dot: string, titulo: string, detalhe: (rs: Logistica[]) => string) => {
    const rs = base.filter((c) => noCartao(c, k))
    if (rs.length) out.push({ chave: `g-${k}`, dot, titulo: `${titulo} · ${rs.length}`, detalhe: detalhe(rs), acao: 'Ver', cartao: k })
  }
  const lista = (rs: Logistica[]) => rs.slice(0, 3).map(pedidoDe).join(' · ') + (rs.length > 3 ? ' …' : '')
  if (ehProprio.value) {
    grupo('prazo', 'bg-red-600', 'Prazo da Amazon vencido', () => 'a Amazon pode reembolsar o cliente')
    grupo('previsao', 'bg-amber-500', 'Previsão da transportadora vencida', lista)
  }
  grupo('sem_regra', 'bg-amber-500', 'Sem regra', () => 'combinação nova: decidir na aba Status')
  // Problemas COM chamado aberto não entram: alguém já está no caso (aba
  // Chamados) — o cartão "Problemas no Bling" continua mostrando todos.
  grupo('divergencia', 'bg-amber-500', 'Divergência', () => 'a plataforma e o rastreio dizem coisas diferentes')
  grupo('correios_parado', 'bg-amber-500', 'Correios parados', () => `sem movimento há mais de ${LEITURA_VELHA_DIAS} dias`)
  grupo('monitorar', 'bg-blue-600', 'Monitorar', () => 'a regra pede acompanhar')
  return out
})
function agir(a: AgirItem) {
  if (a.linha) raioId.value = a.linha.id
  else if (a.cartao) filtrarCartao(a.cartao)
}
const tituloTabela = computed(() => {
  const nome = tab.value === 'flex' ? 'Pedidos Flex' : PLATAFORMA_TABS.find((t) => t.key === tab.value)?.label || ''
  const sub = tab.value === 'amazon' ? (amazonSub.value === 'proprio' ? ' · Envio próprio' : ' · Amazon DBA') : ''
  return `${nome}${sub} — ${mostrarTudo.value ? 'todos os pedidos' : 'casos a acompanhar'}`
})
function produtoResumo(c: Logistica): string {
  const ps = c.produtos || []
  if (!ps.length) return '—'
  const p = ps[0]
  const q = p.quantidade && p.quantidade > 1 ? ` ×${p.quantidade}` : ''
  return `${p.nome || '—'}${q}${ps.length > 1 ? ` +${ps.length - 1}` : ''}`
}
function produtoTitulo(c: Logistica): string {
  return (c.produtos || []).map((p) => `${p.nome || '—'}${p.quantidade && p.quantidade > 1 ? ` ×${p.quantidade}` : ''}${p.sku ? ` (${p.sku})` : ''}`).join('\n')
}

function limparFiltros() {
  search.value = ''
  contaFilter.value = 'all'
  statusBlingFilter.value = 'all'
  dataInicioFilter.value = ''
  dataFimFilter.value = ''
  flexPlataformaFilter.value = ''
  cartaoFiltro.value = 'all'
}

// Contadores das sub-abas da Amazon (mesma base do painel: respeita "Mostrar tudo").
const amazonCounts = computed(() => {
  const n: Record<AmazonSub, number> = { dba: 0, proprio: 0 }
  for (const c of rows.value) {
    if (!mostrarTudo.value && escondido(c)) continue
    n[amazonSubDe(c)]++
  }
  return n
})
// Contador da sub-aba "Pedidos Flex" (mesma base: respeita "Mostrar tudo").
const flexPedidosCount = computed(() =>
  tab.value !== 'flex'
    ? 0
    : rows.value.filter((c) => mostrarTudo.value || !escondido(c)).length,
)

// ---- Flex: avisos que vêm da tela de anúncios (02/10/2026) ----
// Pedido Flex sem peça em SP (o robô de prioridade não trocou o lote): a
// linha mostra o aviso embaixo do selo, em qualquer aba. E quantos anúncios
// esperam aprovação para ligar o Flex — vai no rótulo da sub-aba. Só leitura;
// se a API do Flex falhar, a Logística segue normal sem os avisos.
const flexAlertas = ref<Record<string, string>>({})
const flexAguardando = ref(0)
// Só depois de montar: o refresh() também roda no setup (SSR e de novo na
// hidratação) e um aviso chegando no meio da hidratação deixaria o HTML do
// servidor diferente do navegador. O onMounted liga e chama a primeira vez.
let flexMontado = false
async function carregarResumoFlex() {
  if (!flexMontado || !podeVerFlex.value || !['ml', 'shopee', 'flex'].includes(tab.value)) return
  try {
    const [pedidos, anuncios] = await Promise.all([
      api<Array<{ numero: string | null; alerta: string | null; acerto_pendente?: boolean; lote?: string }>>(
        '/api/flex/pedidos?so_alerta=true&abertos=true',
      ),
      tab.value === 'flex'
        ? api<{ resumo?: { aguardando: number } }>('/api/flex/anuncios?limit=1')
        : Promise.resolve(null),
    ])
    const mapa: Record<string, string> = {}
    for (const p of pedidos || []) {
      if (!p.numero) continue
      if (p.acerto_pendente)
        mapa[p.numero] =
          `Saiu pelo Flex sem passar pelo .${p.lote || flexLocal.value.lote}: o Bling baixou outro lote. ` +
          `Acerte o estoque no Bling (transferência para o .${p.lote || flexLocal.value.lote}) e marque em Flex › Anúncios Flex.`
      else if (p.alerta) mapa[p.numero] = p.alerta
    }
    flexAlertas.value = mapa
    if (anuncios) flexAguardando.value = anuncios.resumo?.aguardando || 0
  } catch {
    // informativo — sem os avisos a tela continua funcionando
  }
}
function flexAlerta(c: Logistica): string | null {
  return (c.pedido_bling && flexAlertas.value[c.pedido_bling]) || null
}
const flexSeloTituloBase = computed(
  () =>
    `Envio Flex: sai de ${flexLocal.value.cidade} (estoque .${flexLocal.value.lote}) e é entregue no mesmo dia ou no dia seguinte (Mercado Livre Envios Flex / Shopee Entrega Direta).`,
)
function flexSeloTitulo(c: Logistica): string {
  const base = flexSeloTituloBase.value
  return c.envio_tipo ? `${base}\nTipo de envio na plataforma: ${c.envio_tipo}` : base
}
// Clique num pedido da lista "sem peça em SP" (sub-aba Anúncios Flex): volta
// para "Pedidos Flex" já buscando o pedido.
function buscarPedidoFlex(numero: string) {
  flexSub.value = 'pedidos'
  search.value = numero
}

// ---- Prazos da Amazon (só leitura) ----
// Datas "YYYY-MM-DD" comparadas em dia local (sem fuso): quantos dias faltam.
function diasAte(dia: string | null | undefined): number | null {
  if (!dia) return null
  const [y, m, d] = dia.slice(0, 10).split('-').map(Number)
  if (!y || !m || !d) return null
  const alvo = new Date(y, m - 1, d)
  const hoje = new Date()
  const base = new Date(hoje.getFullYear(), hoje.getMonth(), hoje.getDate())
  return Math.round((alvo.getTime() - base.getTime()) / 86400000)
}
function fmtDia(dia: string | null | undefined): string {
  if (!dia) return ''
  const [y, m, d] = dia.slice(0, 10).split('-')
  return y && m && d ? `${d}/${m}/${y}` : dia
}
// ISO-datetime → "dd/mm" (fmtDate só entende data pura "YYYY-MM-DD").
function fmtQuando(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' })
}
function previsaoVencida(c: Logistica): boolean {
  if (c.entregue_em) return false
  const dias = diasAte(c.previsao_correios)
  return dias !== null && dias < 0
}
function prazoVencido(c: Logistica): boolean {
  if (c.entregue_em) return false
  const dias = diasAte(c.prazo_entrega_amazon)
  return dias !== null && dias < 0
}
// "faltam 3 dias" / "vence hoje" / "vencido há 2 dias" — depois disso a Amazon
// reembolsa o cliente, por isso o vermelho.
function prazoResumo(c: Logistica): string {
  const dias = diasAte(c.prazo_entrega_amazon)
  if (dias === null) return ''
  if (dias < 0) return dias === -1 ? 'vencido há 1 dia' : `vencido há ${-dias} dias`
  if (dias === 0) return 'vence hoje'
  return dias === 1 ? 'falta 1 dia' : `faltam ${dias} dias`
}
function avisosResumo(c: Logistica): string {
  const partes: string[] = []
  if (c.aviso_previsao_correios_at) partes.push(`previsão vencida ${fmtQuando(c.aviso_previsao_correios_at)}`)
  if (c.aviso_prazo_amazon_3d_at) partes.push(`3 dias ${fmtQuando(c.aviso_prazo_amazon_3d_at)}`)
  if (c.aviso_prazo_amazon_vencido_at) partes.push(`vencido ${fmtQuando(c.aviso_prazo_amazon_vencido_at)}`)
  return partes.length ? `Threema: ${partes.join(' · ')}` : ''
}
function mensagensResumo(c: Logistica): string {
  const ms = c.mensagens_cliente || []
  if (!ms.length) return ''
  return ms
    .map((m) => (m.enviado_em ? `${m.evento_label} ${fmtQuando(m.enviado_em)}` : `${m.evento_label} (falhou)`))
    .join(' · ')
}

// ---- Botão "Excel" ----
// Baixa as linhas que o painel está mostrando: TODAS as páginas, com os
// filtros da tela (Mostrar tudo, sub-aba DBA × Envio próprio, conta, status
// Bling, período, busca) e as mesmas colunas da tabela, na mesma ordem. O texto
// de cada célula sai daqui (mesmas funções da tabela); o servidor só escreve o
// .xlsx. Vazio na planilha = "—" na tela.
type ExcelColuna = {
  titulo: string
  tipo?: 'data'
  valor: (c: Logistica) => string | null | undefined
}
const exportandoExcel = ref(false)
function emLinhas(...partes: Array<string | null | undefined>): string {
  return partes.filter(Boolean).join('\n')
}
function excelColunas(): ExcelColuna[] {
  const proprio = tab.value === 'amazon' && amazonSub.value === 'proprio'
  const cols: ExcelColuna[] = [
    { titulo: 'Data', tipo: 'data', valor: (c) => c.data },
    { titulo: 'Pedido Bling', valor: (c) => c.pedido_bling },
    { titulo: 'Pedido Marketplace', valor: (c) => c.pedido_marketplace },
    { titulo: 'Plataforma', valor: (c) => `${c.plataforma || ''}${c.envio_flex && podeVerFlex.value ? ' · Flex' : ''}` },
    {
      titulo: 'Produto',
      valor: (c) =>
        emLinhas(...(c.produtos || []).map((p) => `${p.nome || '—'}${p.quantidade && p.quantidade > 1 ? ` ×${p.quantidade}` : ''}`)),
    },
    { titulo: 'SKU', valor: (c) => emLinhas(...(c.produtos || []).map((p) => p.sku || '—')) },
    { titulo: 'Conta', valor: (c) => c.conta },
    { titulo: 'Status Plataforma', valor: (c) => assinatura(c) },
    {
      // A data que aparece embaixo da chave (sem o "há X", que envelhece no arquivo).
      titulo: 'Status desde',
      valor: (c) => {
        const top = statusUltimaData(c)
        return top ? `${top.fonte && top.fonte !== 'plataforma' ? '~' : ''}${fmtDataHora(top.em)}` : ''
      },
    },
    { titulo: 'Rastreio', valor: (c) => c.rastreio },
    {
      titulo: 'Localização',
      valor: (c) => (proprioSemRastreio(c) && c.localizacao ? `Destino: ${c.localizacao}` : c.localizacao),
    },
    { titulo: 'Devolução (volta)', valor: (c) => voltaTexto(c) },
  ]
  if (proprio) {
    cols.push(
      {
        titulo: 'Previsão transportadora',
        valor: (c) => {
          if (c.entregue_em) return `entregue ${fmtDataHora(c.entregue_em)}`
          if (!c.previsao_correios) return ''
          return emLinhas(fmtDia(c.previsao_correios), previsaoVencida(c) ? 'previsão vencida' : null)
        },
      },
      {
        titulo: 'Entregar até (Amazon)',
        valor: (c) =>
          emLinhas(
            c.prazo_entrega_amazon ? fmtDia(c.prazo_entrega_amazon) : null,
            c.prazo_entrega_amazon && !c.entregue_em ? prazoResumo(c) : null,
            avisosResumo(c),
            mensagensResumo(c) ? `✉ cliente: ${mensagensResumo(c)}` : null,
          ),
      },
    )
  }
  cols.push(
    { titulo: 'Divergência', valor: (c) => c.divergencia },
    { titulo: 'Status Bling', valor: (c) => c.status_bling },
  )
  if (proprio) cols.push({ titulo: 'Suspensão', valor: (c) => suspensaoResumo(c) })
  cols.push({
    titulo: 'Chamado',
    valor: (c) => emLinhas(chamadoAbaResumo(c)?.texto || c.chamado, chamadoAutoAviso(c)),
  })
  return cols
}
function excelAbaNome(): string {
  if (tab.value === 'amazon') return amazonSub.value === 'proprio' ? 'Amazon Envio próprio' : 'Amazon DBA'
  return PLATAFORMA_TABS.find((t) => t.key === tab.value)?.label || 'Logística'
}
async function exportarExcel() {
  const linhas = filteredRows.value
  if (exportandoExcel.value || !linhas.length) return
  exportandoExcel.value = true
  try {
    const cols = excelColunas()
    const aba = excelAbaNome()
    const blob = await api<Blob>('/api/logistica/export.xlsx', {
      method: 'POST',
      body: {
        aba,
        colunas: cols.map((col) => ({ titulo: col.titulo, tipo: col.tipo || 'texto' })),
        linhas: linhas.map((c) => cols.map((col) => col.valor(c) || null)),
      },
      responseType: 'blob' as any,
    })
    const nome = aba
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '')
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
    const href = URL.createObjectURL(blob as any)
    const a = document.createElement('a')
    a.href = href
    a.download = `logistica_${nome}_${isoToday()}.xlsx`
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(href)
  } catch (e: any) {
    toasts.error('Não foi possível gerar o Excel', e?.data?.detail?.code || e?.message || 'erro')
  } finally {
    exportandoExcel.value = false
  }
}

// ---- Paginação (client-side, 50 por página; muitas linhas travam o DOM) ----
const PAGE_SIZE = 50
const page = ref(1)
const totalPages = computed(() => Math.max(1, Math.ceil(filteredRows.value.length / PAGE_SIZE)))
const pagedRows = computed(() =>
  filteredRows.value.slice((page.value - 1) * PAGE_SIZE, page.value * PAGE_SIZE),
)
const pageStart = computed(() =>
  filteredRows.value.length ? (page.value - 1) * PAGE_SIZE + 1 : 0,
)
const pageEnd = computed(() => Math.min(page.value * PAGE_SIZE, filteredRows.value.length))
function goToPage(p: number) {
  page.value = Math.min(Math.max(1, p), totalPages.value)
}
// Filtros mudaram → volta pra 1ª página.
watch([search, contaFilter, statusBlingFilter, dataInicioFilter, dataFimFilter, amazonSub, flexPlataformaFilter, cartaoFiltro, mostrarTudo], () => {
  page.value = 1
})
// Recarregou dados / página ficou fora do intervalo → corrige.
watch(totalPages, (tp) => {
  if (page.value > tp) page.value = tp
})

const filtrosAtivos = computed(
  () =>
    !!search.value.trim() ||
    contaFilter.value !== 'all' ||
    statusBlingFilter.value !== 'all' ||
    !!dataInicioFilter.value ||
    !!dataFimFilter.value ||
    cartaoFiltro.value !== 'all' ||
    (tab.value === 'flex' && !!flexPlataformaFilter.value),
)

async function loadOpcoes() {
  try {
    opcoes.value = await api<Opcoes>('/api/logistica/opcoes')
  } catch {
    // sem opções o formulário ainda funciona (selects vazios)
  }
}

await Promise.all([refresh(), loadOpcoes()])

// ---- Modal (create + edit compartilham o mesmo formulário) ----
const modalOpen = ref(false)
const editingId = ref<string | null>(null)
const saving = ref(false)
const formErr = ref<string | null>(null)

function emptyForm() {
  const meli: MeliStatus = {}
  for (const f of opcoes.value.field_order) meli[f] = ''
  return {
    data: '',
    pedido_bling: '',
    pedido_marketplace: '',
    plataforma: '',
    conta: '',
    meli_status: meli,
    rastreio: '',
    localizacao: '',
    status_bling: '',
    chamado: '',
    observacao: '',
  }
}

const form = reactive(emptyForm())

function resetForm(src?: Logistica) {
  const base = emptyForm()
  form.data = src?.data || ''
  form.pedido_bling = src?.pedido_bling || ''
  form.pedido_marketplace = src?.pedido_marketplace || ''
  form.plataforma = src?.plataforma || ''
  form.conta = src?.conta || ''
  form.rastreio = src?.rastreio || ''
  form.localizacao = src?.localizacao || ''
  form.status_bling = src?.status_bling || ''
  form.chamado = src?.chamado || ''
  form.observacao = src?.observacao || ''
  const meli = { ...base.meli_status }
  if (src?.meli_status) for (const k of Object.keys(meli)) meli[k] = src.meli_status[k] || ''
  form.meli_status = meli
}

function openNew() {
  editingId.value = null
  resetForm()
  formErr.value = null
  candidatos.value = []
  modalOpen.value = true
}

function openEdit(c: Logistica) {
  editingId.value = c.id
  resetForm(c)
  formErr.value = null
  modalOpen.value = true
  suggest()
}

function closeModal() {
  modalOpen.value = false
  editingId.value = null
}

// Linha aberta no "Editar caso": o pacote de VOLTA não é campo do formulário
// (vem da devolução, lido da plataforma) — o modal só mostra. Sem isto o
// 293379 abria com "Localização: Pedido entregue" e nada da apreensão (20/10).
const editingRow = computed(() =>
  editingId.value ? rows.value.find((r) => r.id === editingId.value) || null : null,
)
const editingTemVolta = computed(
  () => !!(editingRow.value?.volta_rastreio || editingRow.value?.volta_localizacao),
)
const rotuloLocalizacaoModal = computed(() => {
  const r = editingRow.value
  if (!r || !editingTemVolta.value) return 'Localização'
  return idaSeparada(r) || !r.volta_localizacao ? 'Localização (ida)' : 'Localização (devolução)'
})

function payload() {
  const meli: MeliStatus = {}
  for (const f of opcoes.value.field_order) {
    const v = (form.meli_status[f] || '').trim()
    if (v) meli[f] = v
  }
  return {
    data: form.data || null,
    pedido_bling: form.pedido_bling.trim() || null,
    pedido_marketplace: form.pedido_marketplace.trim() || null,
    plataforma: form.plataforma.trim() || null,
    conta: form.conta.trim() || null,
    meli_status: meli,
    rastreio: form.rastreio.trim() || null,
    localizacao: form.localizacao.trim() || null,
    status_bling: form.status_bling.trim() || null,
    chamado: form.chamado.trim() || null,
    observacao: form.observacao.trim() || null,
  }
}

async function save() {
  saving.value = true
  formErr.value = null
  try {
    if (editingId.value) {
      await api(`/api/logistica/${editingId.value}`, { method: 'PATCH', body: payload() })
    } else {
      await api('/api/logistica', { method: 'POST', body: payload() })
    }
    closeModal()
    await refresh()
  } catch (e: any) {
    formErr.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    saving.value = false
  }
}

async function remove() {
  if (!editingId.value) return
  if (!confirm('Remover este caso?')) return
  saving.value = true
  formErr.value = null
  try {
    await api(`/api/logistica/${editingId.value}`, { method: 'DELETE' })
    closeModal()
    await refresh()
  } catch (e: any) {
    formErr.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    saving.value = false
  }
}

// ---- Sugestão de Status Bling (ao vivo, conforme os status do Meli) ----
const candidatos = ref<Candidato[]>([])
let suggestTimer: any = null

async function suggest() {
  const meli: MeliStatus = {}
  for (const f of opcoes.value.field_order) {
    const v = (form.meli_status[f] || '').trim()
    if (v) meli[f] = v
  }
  if (Object.keys(meli).length === 0) {
    candidatos.value = []
    return
  }
  try {
    const r = await api<{ candidatos: Candidato[] }>('/api/logistica/sugestao', {
      method: 'POST',
      body: { meli_status: meli },
    })
    candidatos.value = r.candidatos
  } catch {
    candidatos.value = []
  }
}

watch(
  () => ({ ...form.meli_status }),
  () => {
    if (!modalOpen.value) return
    clearTimeout(suggestTimer)
    suggestTimer = setTimeout(suggest, 250)
  },
  { deep: true },
)

// ---- Helpers de exibição ----
function fmtDate(s: string | null) {
  if (!s) return '—'
  const [y, m, d] = s.split('-').map((n) => Number(n))
  if (!y || !m || !d) return s
  return new Date(y, m - 1, d).toLocaleDateString('pt-BR')
}

// "STATUS PLATAFORMA" = assinatura em PT vinda do backend (traduz os status do
// Meli). Fallback pro join local dos tokens crus se o backend não mandar.
function assinatura(c: Logistica): string {
  if (c.status_plataforma) return c.status_plataforma
  const order = opcoes.value.field_order.length
    ? opcoes.value.field_order
    : Object.keys(c.meli_status || {})
  const parts = order.map((f) => c.meli_status?.[f]).filter(Boolean)
  return parts.join(' | ')
}

// ---- Datas do Status Plataforma ------------------------------------------
// Cada campo da assinatura carrega DESDE QUANDO está naquele valor (o backend
// carimba em logistica.status_datas). "~" na frente = data aproximada: o canal
// não datou aquele campo específico, é a melhor estimativa que ele deu.

function fmtDataHora(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleString('pt-BR', {
    day: '2-digit', month: '2-digit', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}

// ---- Pacote de volta (devolução) -------------------------------------------
// 20/10, pedido 293379: a Localização mostrava "Pedido entregue" (a IDA) com o
// pacote da devolução apreendido havia 15 dias. A volta aparece em cima, e a
// ida passa a ser "Ida: …".
function voltaTexto(c: Logistica): string {
  if (!c.volta_localizacao && !c.volta_rastreio) return ''
  const partes = [c.volta_rastreio, c.volta_localizacao].filter(Boolean)
  const quando = c.volta_localizacao_em ? ` (${fmtDataHora(c.volta_localizacao_em)})` : ''
  return `${partes.join(' — ')}${quando}`
}
// Shopee com a ida entregue: o backend já escreve a volta na própria
// Localização (é ela que as regras "Localização contém" leem) — aí não há
// "Ida:" separada pra mostrar.
function idaSeparada(c: Logistica): boolean {
  return !!c.volta_localizacao && (c.localizacao || '').trim() !== c.volta_localizacao.trim()
}
function voltaTitulo(c: Logistica): string {
  const linhas = [`Pacote da devolução: ${c.volta_localizacao || 'sem evento ainda'}`]
  if (c.volta_localizacao_em) linhas.push(`Último evento em ${fmtDataHora(c.volta_localizacao_em)}`)
  if (c.volta_rastreio) linhas.push(`Código da volta: ${c.volta_rastreio}`)
  if (c.volta_grave) linhas.push('Ocorrência grave: o produto pode estar perdido — veja a devolução na plataforma.')
  return linhas.join('\n')
}

// Rastreio dos Correios termina em BR (ex. AD828496989BR) — mesma regra do
// backend (`logistica_track.is_correios`). Só esses têm leitura física.
function ehCorreios(rastreio: string | null | undefined): boolean {
  const r = (rastreio || '').trim().toUpperCase()
  return r.length >= 4 && r.endsWith('BR')
}

// Pacote a caminho cuja última leitura dos Correios é de mais de 3 dias atrás é
// justamente o que trava sem ninguém ver — pinta de âmbar, igual à Divergência.
const LEITURA_VELHA_DIAS = 3
function leituraVelha(c: Logistica): boolean {
  if (!c.localizacao_at) return false
  const d = new Date(c.localizacao_at)
  if (Number.isNaN(d.getTime())) return false
  const dias = (Date.now() - d.getTime()) / 86400000
  return dias > LEITURA_VELHA_DIAS
}

// "há 8 dias" / "há 3 h" / "há 12 min" — o que o operador realmente quer saber
// olhando pra um pedido parado.
function fmtDesde(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const seg = Math.floor((Date.now() - d.getTime()) / 1000)
  if (seg < 0) return ''
  if (seg < 60) return 'agora há pouco'
  if (seg < 3600) return `há ${Math.floor(seg / 60)} min`
  if (seg < 86400) return `há ${Math.floor(seg / 3600)} h`
  const dias = Math.floor(seg / 86400)
  return dias === 1 ? 'há 1 dia' : `há ${dias} dias`
}

// Data mais recente entre os campos = "quando esse status mudou pela última
// vez". É o que aparece embaixo da assinatura, sem precisar passar o mouse.
function statusUltimaData(c: Logistica): StatusDetalhe | null {
  const linhas = (c.status_detalhe || []).filter((l) => l.em)
  if (!linhas.length) return null
  return linhas.reduce((a, b) =>
    new Date(b.em as string).getTime() > new Date(a.em as string).getTime() ? b : a,
  )
}

function statusResumoData(c: Logistica): string {
  const top = statusUltimaData(c)
  if (!top) return ''
  const aprox = top.fonte && top.fonte !== 'plataforma' ? '~' : ''
  const desde = fmtDesde(top.em)
  return `${aprox}${fmtDataHora(top.em)}${desde ? ` · ${desde}` : ''}`
}

// Tooltip da coluna "Status Plataforma": uma linha por campo da assinatura, com
// o rótulo do campo, o valor em PT e desde quando está assim. O backend manda
// tudo pronto em `status_detalhe` (ele sabe os campos de cada plataforma);
// o fallback antigo cobre resposta velha de cache, sem data.
// Campo extra do ML que o backend manda cru (fora da assinatura).
const ROTULO_EXTRA: Record<string, string> = { return_destino: 'Destino da devolução' }
const VALOR_EXTRA: Record<string, Record<string, string>> = {
  return_destino: { seller_address: 'loja', warehouse: 'galpão do ML' },
}

interface StatusLinha {
  quando: string // "10/11/2026 11:33" ('' = sem data)
  aprox: boolean // "~": a plataforma não datou este campo
  rotulo: string
  valor: string
}

// Vinicius 12/11: no Raio-X o status vem com a data e a hora NA FRENTE e na
// ordem em que aconteceu (antes vinha na ordem dos campos: 10/11, 21/10, 09/11…).
// Mais antigo em cima; campo sem data vai pro fim.
function statusLinhas(c: Logistica): StatusLinha[] {
  const det = c.status_detalhe || []
  return det
    .map((l, i) => {
      const t = l.em ? new Date(l.em).getTime() : NaN
      return {
        i,
        t: Number.isNaN(t) ? Infinity : t,
        quando: fmtDataHora(l.em),
        aprox: !!(l.em && l.fonte && l.fonte !== 'plataforma'),
        rotulo: ROTULO_EXTRA[l.campo] || l.rotulo,
        valor: VALOR_EXTRA[l.campo]?.[l.valor] || l.valor,
      }
    })
    .sort((a, b) => a.t - b.t || a.i - b.i)
    .map(({ quando, aprox, rotulo, valor }) => ({ quando, aprox, rotulo, valor }))
}

function statusTooltip(c: Logistica): string {
  const det = statusLinhas(c)
  if (det.length) {
    const linhas = det.map((l) => `${l.quando ? `${l.aprox ? '~' : ''}${l.quando} — ` : ''}${l.rotulo}: ${l.valor}`)
    if (det.some((l) => l.aprox)) {
      linhas.push('', '~ data aproximada: a plataforma não informa a hora exata deste campo.')
    }
    return linhas.join('\n')
  }
  const fo = opcoes.value.field_order
  const labels = opcoes.value.field_labels
  const ms = c.meli_status || {}
  const naoVazios = fo.filter((f) => String(ms[f] ?? '').trim())
  if (!naoVazios.length) return ''
  const partesPt = (c.status_plataforma || '').split(' | ')
  return naoVazios
    .map((f, i) => `${labels[f] || f}: ${partesPt[i] ?? String(ms[f] ?? '')}`)
    .join('\n')
}

function isMl(c: Logistica): boolean {
  return ['mercado livre', 'mercadolivre', 'ml'].includes(
    (c.plataforma || '').trim().toLowerCase(),
  )
}

function isShopee(c: Logistica): boolean {
  return (c.plataforma || '').trim().toLowerCase() === 'shopee'
}

function isTiktok(c: Logistica): boolean {
  return ['tiktok', 'tik tok', 'tiktok shop'].includes(
    (c.plataforma || '').trim().toLowerCase(),
  )
}

function isAmazon(c: Logistica): boolean {
  return (c.plataforma || '').trim().toLowerCase() === 'amazon'
}

// Envio próprio ainda SEM o código dos Correios: a etiqueta sai no Bling
// (Melhor Envio) e o motor só relê cada linha de hora em hora, então logo
// depois de etiquetar a Localização mostra só a cidade de destino que a
// Amazon informou — que parece posição do pacote sem ser (Vinicius,
// 21/09/2026: 298196 e 298281 etiquetados juntos, um ganhou o código 8 min
// depois e o outro 33). Aqui a célula diz isso com todas as letras e o ⟳ lê
// o Bling na hora.
function proprioSemRastreio(c: Logistica): boolean {
  return isAmazon(c) && c.amazon_canal === 'proprio' && !ehCorreios(c.rastreio)
}
const DESTINO_TITLE =
  'A cidade é o endereço de entrega informado pela Amazon, não a posição do pacote: ' +
  'o código dos Correios desta etiqueta (que vem do Bling) ainda não chegou aqui.'
// Sem carimbo o DaVinci nunca leu o pedido no Bling (linha nova, ou fora da
// janela de 60 dias do motor) — não dá pra afirmar que o Bling não tem a etiqueta.
function blingLidoResumo(c: Logistica): string {
  const desde = fmtDesde(c.bling_enriquecido_em)
  return desde ? `sem etiqueta no Bling ainda · Bling lido ${desde}` : 'Bling ainda não lido'
}
function tituloAtualizarLocalizacao(c: Logistica): string {
  return ehCorreios(c.rastreio)
    ? 'Consultar os Correios agora pelo 17track (pode gastar 1 crédito)'
    : 'Buscar o código dos Correios no Bling e consultar o 17track'
}

// Copia a "chave" (assinatura do Status Plataforma, ex. "Pago | Pendente | Programado").
async function copiarChave(c: Logistica) {
  const chave = assinatura(c)
  if (!chave) return
  try {
    await navigator.clipboard.writeText(chave)
    toasts.success('Chave copiada', chave)
  } catch {
    toasts.error('Não foi possível copiar')
  }
}

// Puxa a assinatura do Meli (8 campos) da API do ML pra uma linha.
const refreshingMeli = ref<Set<string>>(new Set())
async function atualizarMeli(c: Logistica) {
  refreshingMeli.value = new Set(refreshingMeli.value).add(c.id)
  try {
    const updated = await api<Logistica>(`/api/logistica/${c.id}/atualizar-meli`, {
      method: 'POST',
    })
    const i = rows.value.findIndex((r) => r.id === c.id)
    if (i >= 0) rows.value[i] = updated
  } catch (e: any) {
    const code = e?.data?.detail?.code || e?.message || 'erro'
    error.value = code
  } finally {
    const s = new Set(refreshingMeli.value)
    s.delete(c.id)
    refreshingMeli.value = s
  }
}

// Puxa o order_status da Shopee (API v2) pra uma linha.
async function atualizarShopee(c: Logistica) {
  refreshingMeli.value = new Set(refreshingMeli.value).add(c.id)
  try {
    const updated = await api<Logistica>(`/api/logistica/${c.id}/atualizar-shopee`, {
      method: 'POST',
    })
    const i = rows.value.findIndex((r) => r.id === c.id)
    if (i >= 0) rows.value[i] = updated
  } catch (e: any) {
    const code = e?.data?.detail?.code || e?.message || 'erro'
    error.value = code
  } finally {
    const s = new Set(refreshingMeli.value)
    s.delete(c.id)
    refreshingMeli.value = s
  }
}

// Puxa o status do pedido TikTok (Order API 202309) + rastreio pra uma linha.
async function atualizarTiktok(c: Logistica) {
  refreshingMeli.value = new Set(refreshingMeli.value).add(c.id)
  try {
    const updated = await api<Logistica>(`/api/logistica/${c.id}/atualizar-tiktok`, {
      method: 'POST',
    })
    const i = rows.value.findIndex((r) => r.id === c.id)
    if (i >= 0) rows.value[i] = updated
  } catch (e: any) {
    const code = e?.data?.detail?.code || e?.message || 'erro'
    error.value = code
  } finally {
    const s = new Set(refreshingMeli.value)
    s.delete(c.id)
    refreshingMeli.value = s
  }
}

// Botão ⟳ da coluna Localização: consulta os Correios AGORA pelo 17track
// (registra o número se preciso, força a releitura e espera até ~40 s). O
// 17track sozinho só pergunta aos Correios de 6 em 6 a 12 em 12 horas.
const refreshingRastreio = ref<Set<string>>(new Set())
async function atualizarLocalizacao(c: Logistica) {
  refreshingRastreio.value = new Set(refreshingRastreio.value).add(c.id)
  try {
    const r = await api<{ resultado: string; detalhe: string | null; linha: Logistica }>(
      `/api/logistica/${c.id}/atualizar-rastreio`,
      { method: 'POST' },
    )
    const i = rows.value.findIndex((x) => x.id === c.id)
    if (i >= 0) rows.value[i] = r.linha
    switch (r.resultado) {
      case 'atualizado':
        toasts.success('Localização atualizada', r.linha.localizacao || '')
        break
      case 'consultando':
        toasts.info(
          'Consultando os Correios',
          'O 17track foi acionado agora. A linha atualiza sozinha em instantes.',
        )
        break
      case 'encerrado':
        toasts.info(
          'Rastreio encerrado',
          'O 17track já fechou este rastreio (entregue ou expirado); não virá leitura nova.',
        )
        break
      case 'sem_quota':
        correiosSemSaldo.value = true
        toasts.error('Correios sem saldo no 17track', SEM_SALDO_TEXTO)
        break
      case 'recusado':
        toasts.error('O 17track recusou este número', r.detalhe || 'Confira se o rastreio está certo.')
        break
      case 'sem_rastreio_no_bling':
        toasts.info(
          'Bling ainda sem código',
          'O Bling ainda não tem o código dos Correios deste pedido — confira a etiqueta no Melhor Envio.',
        )
        break
      default:
        toasts.error('17track fora do ar', r.detalhe || 'Tente de novo em instantes.')
    }
  } catch (e: any) {
    const code = e?.data?.detail?.code || e?.message || 'erro'
    if (code === 'logistica_bling_erro') {
      toasts.error('Bling não respondeu', 'Não deu pra ler o pedido no Bling agora. Tente de novo em instantes.')
    } else {
      toasts.error('Não foi possível atualizar a localização', code)
    }
  } finally {
    const s = new Set(refreshingRastreio.value)
    s.delete(c.id)
    refreshingRastreio.value = s
  }
}

// Botão "Suspender entrega" (Amazon Envio próprio, rastreio dos Correios,
// ainda não entregue): pede ao robô (executor local, perfil logado no Melhor
// Envio) que clique em Meus envios › Ações do envio › Suspender entrega.
// Irreversível no Melhor Envio e o frete não volta — por isso a confirmação.
const suspendendo = ref<Set<string>>(new Set())
function podeSuspender(c: Logistica): boolean {
  return (
    canEdit.value &&
    c.amazon_canal === 'proprio' &&
    ehCorreios(c.rastreio) &&
    !c.entregue_em &&
    !['pendente', 'solicitada'].includes(c.suspensao_status || '')
  )
}
function suspensaoResumo(c: Logistica): string {
  if (!c.suspensao_status) return ''
  if (c.suspensao_status === 'pendente') return 'suspensão: na fila do robô'
  if (c.suspensao_status === 'solicitada') return `suspensão solicitada ${fmtQuando(c.suspensao_em)}`
  return `suspensão falhou: ${resumoDetalheRobo(c.suspensao_detalhe)}`
}
// O robô devolve um JSON com o que viu; pra tela basta o motivo.
function resumoDetalheRobo(raw: string | null | undefined): string {
  if (!raw) return 'sem detalhe'
  try {
    const j = JSON.parse(raw)
    return String(j.reason || j.detail || raw).slice(0, 160)
  } catch {
    return raw.slice(0, 160)
  }
}
async function suspenderEntrega(c: Logistica) {
  const ok = window.confirm(
    `Pedir ao Melhor Envio a suspensão da entrega do pedido ${c.pedido_bling || ''} (rastreio ${c.rastreio})?\n\n` +
      'O pacote volta para você quando chegar na agência de destino. No Melhor Envio isso não pode ser desfeito e o frete não é devolvido.',
  )
  if (!ok) return
  suspendendo.value = new Set(suspendendo.value).add(c.id)
  try {
    const updated = await api<Logistica>(`/api/logistica/${c.id}/suspender-entrega`, { method: 'POST' })
    const i = rows.value.findIndex((x) => x.id === c.id)
    if (i >= 0) rows.value[i] = updated
    toasts.success('Suspensão enviada ao robô', 'O executor vai clicar no Melhor Envio e a linha mostra o resultado.')
  } catch (e: any) {
    const code = e?.data?.detail?.code || e?.message || 'erro'
    toasts.error('Não foi possível pedir a suspensão', code)
  } finally {
    const s = new Set(suspendendo.value)
    s.delete(c.id)
    suspendendo.value = s
  }
}

// Puxa o status do pedido Amazon (OrderStatus + EasyShip) pra uma linha.
async function atualizarAmazon(c: Logistica) {
  refreshingMeli.value = new Set(refreshingMeli.value).add(c.id)
  try {
    const updated = await api<Logistica>(`/api/logistica/${c.id}/atualizar-amazon`, {
      method: 'POST',
    })
    const i = rows.value.findIndex((r) => r.id === c.id)
    if (i >= 0) rows.value[i] = updated
  } catch (e: any) {
    const code = e?.data?.detail?.code || e?.message || 'erro'
    error.value = code
  } finally {
    const s = new Set(refreshingMeli.value)
    s.delete(c.id)
    refreshingMeli.value = s
  }
}

// ================= Aba Status =================
type LogisticaStatus = {
  id: string
  plataforma: string | null
  status_plataforma: string | null
  status_atual: string | null
  // Condição: palavras (separadas por ";") que precisam aparecer na Localização
  // do pedido. Vazio = sem condição. Com condição que casa, a regra passa na
  // frente das regras sem condição da mesma chave (backend: logistica_match).
  localizacao_contem: string | null
  alterar_status_bling: string | null
  monitoramento: boolean
  abrir_chamado: boolean
  abrir_reembolso: boolean
  mensagem_chamado: string | null
  mensagem_bling: string | null
  mensagem_threema: string | null
  threema_recipients: string | null
  anexos: Anexo[]
  created_by: string | null
  created_at: string
  updated_at: string
}

type Anexo = {
  id: string
  filename: string
  content_type: string
  size_bytes: number
  created_at: string
}

// Campos de texto editáveis inline (mensagem_chamado usa textarea).
type StatusTextField =
  | 'plataforma'
  | 'status_plataforma'
  | 'localizacao_contem'
  | 'alterar_status_bling'
  | 'mensagem_chamado'
  | 'mensagem_bling'
  | 'mensagem_threema'

const statusRows = ref<LogisticaStatus[]>([])
const statusLoading = ref(false)
const statusError = ref<string | null>(null)
let statusLoaded = false

async function refreshStatus() {
  statusLoading.value = true
  statusError.value = null
  try {
    statusRows.value = await api<LogisticaStatus[]>('/api/logistica/status')
    statusLoaded = true
  } catch (e: any) {
    statusError.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    statusLoading.value = false
  }
}

// Plataforma de uma regra: escolhida numa lista, com os MESMOS rótulos que o
// backend grava em `logistica.plataforma` (o casador compara sem maiúscula, então
// "mercado livre" antigo casa igual). Vazio = geral (vale pra todas).
// Só as abas que são marketplace: "Flex" é uma visão (ML + Shopee) e nunca vem
// em `logistica.plataforma` — uma regra com Plataforma = Flex nunca casaria.
const STATUS_PLATAFORMA_OPCOES: string[] = PLATAFORMA_TABS.filter((t) => t.key !== 'flex').map(
  (t) => t.label,
)
function plataformaCanonica(v: string | null | undefined): string {
  const p = (v || '').trim()
  return STATUS_PLATAFORMA_OPCOES.find((o) => o.toLowerCase() === p.toLowerCase()) || p
}
// Valor antigo digitado à mão que não é nenhuma das 4 continua na lista (não
// some da regra sem ninguém ver).
function plataformaOpcoesPara(v: string | null | undefined): string[] {
  const p = plataformaCanonica(v)
  return p && !STATUS_PLATAFORMA_OPCOES.includes(p)
    ? [...STATUS_PLATAFORMA_OPCOES, p]
    : STATUS_PLATAFORMA_OPCOES
}

// "Status Atual" pode ter VÁRIOS estados do Bling — a regra vale pra qualquer
// um deles. A API guarda/devolve separados por ";" (o mesmo separador do
// backend, `logistica_match.SEPARADOR_STATUS_ATUAL`); os nomes voltam na grafia
// do catálogo pra marcar certo no seletor.
function statusAtuaisDe(v: string | null | undefined): string[] {
  const base = opcoes.value.status_bling_options
  return (v || '')
    .split(';')
    .map((p) => p.trim())
    .filter(Boolean)
    .map((p) => base.find((b) => b.toLowerCase() === p.toLowerCase()) || p)
}

// Filtro por plataforma na aba Status ('' = todas; '__geral__' = regras sem
// plataforma, que valem pra todas). Só filtra a listagem — não muda as regras.
const statusFiltroPlataforma = ref('')
const statusPlataformas = computed(() => {
  const vistos = new Map<string, string>()
  let temGeral = false
  for (const s of statusRows.value) {
    const p = (s.plataforma || '').trim()
    if (!p) {
      temGeral = true
      continue
    }
    const k = p.toLowerCase()
    if (!vistos.has(k)) vistos.set(k, p)
  }
  return { nomes: [...vistos.values()].sort((a, b) => a.localeCompare(b)), temGeral }
})
// Cartões da aba Status (12/11, padrão de Chamados/Conciliação): quantas regras
// por plataforma; clicar filtra (clicar de novo tira o filtro).
const statusCartoes = computed(() => {
  const cont = new Map<string, number>()
  let geral = 0
  for (const s of statusRows.value) {
    const k = (s.plataforma || '').trim().toLowerCase()
    if (k) cont.set(k, (cont.get(k) || 0) + 1)
    else geral++
  }
  const out = [{ k: '', label: 'Todas as regras', n: statusRows.value.length }]
  for (const nome of statusPlataformas.value.nomes) {
    out.push({ k: nome.toLowerCase(), label: plataformaCanonica(nome) || nome, n: cont.get(nome.toLowerCase()) || 0 })
  }
  if (geral) out.push({ k: '__geral__', label: 'Geral (sem plataforma)', n: geral })
  return out
})
// Busca pela chave (Status Plataforma) — dá pra colar a chave copiada do painel
// e conferir se a regra existe cadastrada letra por letra.
const statusBusca = ref('')
const statusRowsFiltradas = computed(() => {
  const f = statusFiltroPlataforma.value
  const q = statusBusca.value.trim().toLowerCase()
  let rows = statusRows.value
  if (f === '__geral__') rows = rows.filter((s) => !(s.plataforma || '').trim())
  else if (f) rows = rows.filter((s) => (s.plataforma || '').trim().toLowerCase() === f)
  if (q) rows = rows.filter((s) => (s.status_plataforma || '').toLowerCase().includes(q))
  return rows
})

// Palavras da condição "Localização contém" (mesmo separador do backend).
function palavrasLocalizacao(v: string | null | undefined): string[] {
  return (v || '').split(';').map((p) => p.trim()).filter(Boolean)
}
const LOCALIZACAO_CONTEM_TITLE =
  'A regra só vale quando a Localização do pedido tem uma destas palavras (sem diferenciar maiúscula nem acento; separe por ";"). ' +
  'Quando casa, ela passa na frente das regras sem condição do mesmo Status Plataforma, só nos Status Atual que ela lista; ' +
  'nos outros, a regra sem condição continua valendo. Vazio = sem condição.'

watch(tab, (t) => {
  if (t === 'status') {
    if (!statusLoaded) refreshStatus()
  } else {
    // Troca de aba de marketplace → recarrega os pedidos daquela plataforma.
    page.value = 1
    refresh()
  }
  raioId.value = null
  cartaoFiltro.value = 'all'
})
// O cartão "Prazo Amazon vencido" só existe no Envio próprio.
watch(amazonSub, () => {
  raioId.value = null
  if (cartaoFiltro.value === 'prazo' || cartaoFiltro.value === 'previsao') cartaoFiltro.value = 'all'
})

// ---- Atualização automática (30s) ----
// A recarga e os crons atualizam o BANCO de todo mundo, mas cada navegador só
// mostrava a foto de quando a página abriu — quem não clicou em Recarregar
// ficava vendo dado velho. Este timer repuxa a aba ativa a cada 30s pra tela
// de TODOS ficar sempre atualizada sozinha. Ele NÃO roda o motor (isso segue
// só no botão Recarregar): apenas relê o que já está no banco. Pula quando a
// aba do navegador está em segundo plano, quando já tem busca/ação em
// andamento e quando há modal ou edição aberta, pra não atropelar o que a
// pessoa está digitando.
const AUTO_REFRESH_MS = 30000
let autoRefreshTimer: ReturnType<typeof setInterval> | null = null

function autoRefreshTick() {
  if (typeof document !== 'undefined' && document.visibilityState !== 'visible') return
  if (recarregando.value || modalOpen.value || informarOpen.value || informarAmazonOpen.value || mensagensClienteOpen.value) return
  // Alguma ação por linha em andamento (enviar chamado, aplicar no Bling,
  // atualizar status, salvar campo da aba Status)? Deixa pro próximo tick.
  if (
    refreshingMeli.value.size > 0 ||
    refreshingRastreio.value.size > 0 ||
    suspendendo.value.size > 0 ||
    sendingChamado.value.size > 0 ||
    aplicandoBling.value.size > 0 ||
    aplicandoStatus.value.size > 0 ||
    statusBusy.value.size > 0
  )
    return
  if (tab.value === 'status') {
    if (statusLoading.value || editing.value || showStatusForm.value) return
    refreshStatus()
  } else {
    if (loading.value) return
    refresh()
  }
}

// Pré-carrega a aba Status pra ela abrir instantânea (o vermelho do painel
// NÃO depende mais dela: vem pronto do backend em acao_match).
onMounted(() => {
  // `?tab=…&q=…` na URL: é assim que a ocorrência do robô "Ocorrência grave
  // nos Correios" (Ouvidoria › Robôs) abre a aba do pedido já filtrada, em vez
  // de largar a pessoa na aba ML sem filtro. Aba desconhecida (uma plataforma
  // que ainda não tem aba aqui) é ignorada — a busca vale do mesmo jeito.
  const query = useRoute().query
  const tabQuery = typeof query.tab === 'string' ? query.tab : ''
  const buscaQuery = typeof query.q === 'string' ? query.q : ''
  // A aba Flex só abre pelo link depois de saber se a pessoa vê o Flex.
  if (tabQuery === 'status' || PLATAFORMA_TABS.some((t) => t.key === tabQuery && t.key !== 'flex')) {
    tab.value = tabQuery as PlataformaTab | 'status'
  }
  if (buscaQuery) search.value = buscaQuery
  // `&sub=proprio`: a ocorrência do "Vigia Robô Melhor Envio" é de Envio
  // próprio — sem isto a aba Amazon abriria no DBA e o pedido não apareceria.
  if (query.sub === 'proprio' || query.sub === 'dba') amazonSub.value = query.sub
  // `?tab=flex&sub=anuncios`: abre direto a tela dos anúncios Flex.
  if (query.sub === 'anuncios' || query.sub === 'pedidos') flexSub.value = query.sub
  if (!statusLoaded) refreshStatus()
  carregarStatusCorreios()
  flexMontado = true
  verificarFlex().then((ve) => {
    if (!ve) return
    if (tabQuery === 'flex') tab.value = 'flex'
    else carregarResumoFlex()
  })
  autoRefreshTimer = setInterval(autoRefreshTick, AUTO_REFRESH_MS)
})

onBeforeUnmount(() => {
  if (autoRefreshTimer) clearInterval(autoRefreshTimer)
  autoRefreshTimer = null
})

function normKey(s: string | null | undefined): string {
  return (s || '').trim().toLowerCase()
}
// Vermelho: a linha tem chave (Status Plataforma) mas NENHUMA regra da aba
// Status vale pra COMBINAÇÃO real dela (chave + plataforma + situação atual no
// Bling) — regra de outra plataforma ou de outro estado não conta. É o backend
// quem decide (acao_match), com o MESMO casador que executa as ações. Linha já
// resolvida (só aparece no "Mostrar tudo") não pinta: não há decisão pendente.
function precisaAtencao(c: Logistica): boolean {
  return !!normKey(assinatura(c)) && !c.acao_match && !c.acao_resolvido
}

// ---- Edição inline (uma célula por vez) ----
const editing = ref<{ id: string; field: StatusTextField } | null>(null)
const editValue = ref('')
const statusBusy = ref<Set<string>>(new Set())

function isEditing(s: LogisticaStatus, field: StatusTextField) {
  return editing.value?.id === s.id && editing.value?.field === field
}

function startEdit(s: LogisticaStatus, field: StatusTextField) {
  if (!canEdit.value || isEditing(s, field)) return
  editing.value = { id: s.id, field }
  editValue.value =
    field === 'plataforma' ? plataformaCanonica(s.plataforma) : (s[field] as string | null) || ''
}

function cancelEdit() {
  editing.value = null
}

function setStatusBusy(id: string, on: boolean) {
  const next = new Set(statusBusy.value)
  if (on) next.add(id)
  else next.delete(id)
  statusBusy.value = next
}

async function patchStatusField(id: string, body: Record<string, unknown>) {
  setStatusBusy(id, true)
  statusError.value = null
  try {
    const updated = await api<LogisticaStatus>(`/api/logistica/status/${id}`, {
      method: 'PATCH',
      body,
    })
    const i = statusRows.value.findIndex((r) => r.id === id)
    if (i >= 0) statusRows.value[i] = updated
  } catch (e: any) {
    statusError.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    setStatusBusy(id, false)
  }
}

async function commitEdit(s: LogisticaStatus) {
  if (!editing.value) return
  const field = editing.value.field
  const val = editValue.value.trim()
  editing.value = null
  const cur = (s[field] as string | null) || ''
  if (val === cur) return
  await patchStatusField(s.id, { [field]: val || null })
}

async function toggleStatusBool(
  s: LogisticaStatus,
  field: 'monitoramento' | 'abrir_chamado' | 'abrir_reembolso',
) {
  if (!canEdit.value) return
  await patchStatusField(s.id, { [field]: !s[field] })
}

// ---- Modal "Novo status" ----
const showStatusForm = ref(false)
const statusSaving = ref(false)
const statusForm = ref({
  plataforma: '',
  status_plataforma: '',
  status_atual: [] as string[],
  localizacao_contem: '',
  alterar_status_bling: '',
  monitoramento: false,
  abrir_chamado: false,
  abrir_reembolso: false,
  mensagem_chamado: '',
  mensagem_bling: '',
  mensagem_threema: '',
})

function openStatusForm() {
  statusForm.value = {
    plataforma: '',
    status_plataforma: '',
    status_atual: [],
    localizacao_contem: '',
    alterar_status_bling: '',
    monitoramento: false,
    abrir_chamado: false,
    abrir_reembolso: false,
    mensagem_chamado: '',
    mensagem_bling: '',
    mensagem_threema: '',
  }
  showStatusForm.value = true
}

async function saveStatusForm() {
  statusSaving.value = true
  statusError.value = null
  try {
    const f = statusForm.value
    const created = await api<LogisticaStatus>('/api/logistica/status', {
      method: 'POST',
      body: {
        plataforma: f.plataforma.trim() || null,
        status_plataforma: f.status_plataforma.trim() || null,
        status_atual: f.status_atual,
        localizacao_contem: f.localizacao_contem.trim() || null,
        alterar_status_bling: f.alterar_status_bling.trim() || null,
        monitoramento: f.monitoramento,
        abrir_chamado: f.abrir_chamado,
        abrir_reembolso: f.abrir_reembolso,
        mensagem_chamado: f.mensagem_chamado.trim() || null,
        mensagem_bling: f.mensagem_bling.trim() || null,
        mensagem_threema: f.mensagem_threema.trim() || null,
      },
    })
    statusRows.value.unshift(created)
    showStatusForm.value = false
  } catch (e: any) {
    statusError.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    statusSaving.value = false
  }
}

// ---- Juntar linhas repetidas (mesma plataforma + chave + mesmas ações) ----
// A prévia vem do backend (só lê); confirmar manda exatamente os grupos que a
// pessoa viu — grupo que mudou no meio do caminho o backend pula.
type RepetidaLinha = { id: string; status_atual: string | null; acoes: string[] }
type RepetidaGrupo = {
  plataforma: string | null
  status_plataforma: string | null
  localizacao_contem: string | null
  manter_id: string
  apagar_ids: string[]
  status_atual_final: string
  acoes: string[]
  linhas: RepetidaLinha[]
}
type RepetidaConflito = {
  plataforma: string | null
  status_plataforma: string | null
  localizacao_contem: string | null
  linhas: RepetidaLinha[]
}
const juntarModal = ref<{
  open: boolean
  loading: boolean
  saving: boolean
  grupos: RepetidaGrupo[]
  conflitos: RepetidaConflito[]
  erro: string | null
}>({ open: false, loading: false, saving: false, grupos: [], conflitos: [], erro: null })

async function abrirJuntarRepetidas() {
  juntarModal.value = { open: true, loading: true, saving: false, grupos: [], conflitos: [], erro: null }
  try {
    const r = await api<{ grupos: RepetidaGrupo[]; conflitos: RepetidaConflito[] }>(
      '/api/logistica/status/repetidas',
    )
    juntarModal.value.grupos = r.grupos
    juntarModal.value.conflitos = r.conflitos
  } catch (e: any) {
    juntarModal.value.erro = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    juntarModal.value.loading = false
  }
}

async function confirmarJuntarRepetidas() {
  const m = juntarModal.value
  if (!m.grupos.length) return
  m.saving = true
  m.erro = null
  try {
    const r = await api<{ juntados: number; linhas_apagadas: number; pulados: number }>(
      '/api/logistica/status/juntar',
      {
        method: 'POST',
        body: { grupos: m.grupos.map((g) => ({ manter_id: g.manter_id, apagar_ids: g.apagar_ids })) },
      },
    )
    m.open = false
    await refreshStatus()
    const pulados = r.pulados ? ` ${r.pulados} grupo(s) mudaram desde a prévia e ficaram como estavam.` : ''
    toasts.success(
      'Regras juntadas',
      `${r.juntados} grupo(s) juntado(s), ${r.linhas_apagadas} linha(s) repetida(s) removida(s).${pulados}`,
    )
  } catch (e: any) {
    m.erro = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    m.saving = false
  }
}

async function removeStatusRow(s: LogisticaStatus) {
  if (!confirm('Remover este status?')) return
  setStatusBusy(s.id, true)
  statusError.value = null
  try {
    await api(`/api/logistica/status/${s.id}`, { method: 'DELETE' })
    statusRows.value = statusRows.value.filter((r) => r.id !== s.id)
  } catch (e: any) {
    statusError.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    setStatusBusy(s.id, false)
  }
}

// ---- Mensagem Threema (notifica as pessoas do problema) ----
// O modal serve pra dois modos: 'salvar' (ícone 👤 guarda QUEM recebe na regra,
// porque vai virar automático) e 'enviar' (dispara agora pros escolhidos).
type ThreemaDestinatario = { id: string; nome: string }
const threemaDestinatarios = ref<ThreemaDestinatario[]>([])
const threemaModal = reactive({
  open: false,
  mode: 'enviar' as 'enviar' | 'salvar',
  status: null as LogisticaStatus | null,
  selecionados: new Set<string>(),
  sending: false,
})

async function loadThreemaDestinatarios() {
  if (threemaDestinatarios.value.length) return
  try {
    threemaDestinatarios.value = await api<ThreemaDestinatario[]>(
      '/api/logistica/threema/destinatarios',
    )
  } catch {
    threemaDestinatarios.value = []
  }
}

// IDs salvos na regra (string "A,B" → Set). Se vazio, cai em "todos".
function parseThreemaIds(raw: string | null): Set<string> {
  if (!raw) return new Set()
  return new Set(
    raw
      .split(/[,;\s]+/)
      .map((x) => x.trim().toUpperCase())
      .filter(Boolean),
  )
}

function preselecionarThreema(s: LogisticaStatus): Set<string> {
  const salvos = parseThreemaIds(s.threema_recipients)
  if (salvos.size) return salvos
  return new Set(threemaDestinatarios.value.map((d) => d.id))
}

// 👤 — escolher e SALVAR na regra quem recebe (base do envio automático futuro).
async function escolherDestinatarios(s: LogisticaStatus) {
  await loadThreemaDestinatarios()
  threemaModal.mode = 'salvar'
  threemaModal.status = s
  threemaModal.selecionados = preselecionarThreema(s)
  threemaModal.open = true
}

async function enviarThreema(s: LogisticaStatus) {
  if (!s.mensagem_threema) return
  await loadThreemaDestinatarios()
  threemaModal.mode = 'enviar'
  threemaModal.status = s
  threemaModal.selecionados = preselecionarThreema(s)
  threemaModal.open = true
}

function toggleThreemaDest(id: string) {
  if (threemaModal.selecionados.has(id)) threemaModal.selecionados.delete(id)
  else threemaModal.selecionados.add(id)
}

async function confirmarThreemaModal() {
  const s = threemaModal.status
  if (!s) return
  const recipients = [...threemaModal.selecionados]
  if (!recipients.length) {
    toasts.error('Escolha ao menos um destinatário')
    return
  }
  threemaModal.sending = true
  setStatusBusy(s.id, true)
  statusError.value = null
  try {
    if (threemaModal.mode === 'salvar') {
      await patchStatusField(s.id, { threema_recipients: recipients.join(',') })
      if (statusError.value) {
        toasts.error('Não foi possível salvar', statusError.value)
        return
      }
      toasts.success('Destinatários salvos', `${recipients.length} contato(s)`)
      threemaModal.open = false
      return
    }
    const r = await api<{ sent: string[]; failed: string[] }>(
      `/api/logistica/status/${s.id}/enviar-threema`,
      { method: 'POST', body: { recipients } },
    )
    if (r.failed.length) {
      toasts.error(`Enviado a ${r.sent.length}, falhou ${r.failed.length}`, r.failed.join(', '))
    } else {
      toasts.success('Mensagem Threema enviada', `${r.sent.length} destinatário(s)`)
    }
    threemaModal.open = false
  } catch (e: any) {
    const code = e?.data?.detail?.code || e?.message || 'erro'
    statusError.value = code
    toasts.error(threemaModal.mode === 'salvar' ? 'Não foi possível salvar' : 'Não foi possível enviar', code)
  } finally {
    threemaModal.sending = false
    setStatusBusy(s.id, false)
  }
}

// Enviar Threema a partir de uma LINHA do marketplace (aba Amazon/Shopee/etc):
// o servidor acha a regra casada (coligação), injeta Pedido/Loja da própria
// linha e usa os destinatários salvos na regra.
async function enviarThreemaPedido(c: Logistica) {
  if (!confirm(`Enviar aviso Threema do pedido ${c.pedido_marketplace || c.pedido_bling || ''} (loja ${c.plataforma || '—'})?`)) return
  refreshingMeli.value = new Set(refreshingMeli.value).add(c.id)
  try {
    const r = await api<{ sent: string[]; failed: string[] }>(
      `/api/logistica/${c.id}/enviar-threema`,
      { method: 'POST' },
    )
    if (r.failed.length) {
      toasts.error(`Enviado a ${r.sent.length}, falhou ${r.failed.length}`, r.failed.join(', '))
    } else {
      toasts.success('Mensagem Threema enviada', `${r.sent.length} destinatário(s)`)
    }
    // Enviou → o aviso foi feito: o pedido resolve e some do painel.
    if (r.sent.length) await refresh()
  } catch (e: any) {
    const code = e?.data?.detail?.code || e?.message || 'erro'
    toasts.error('Não foi possível enviar', code)
  } finally {
    const s = new Set(refreshingMeli.value)
    s.delete(c.id)
    refreshingMeli.value = s
  }
}

// Botãozinho ⟳ ao lado de "✉ cliente: …" (coluna Entregar até): manda DE NOVO
// a mesma mensagem pro comprador daquele pedido — Vinicius, 22/09/2026: "vai
// que não enviou, eu aperto o botão". É o mesmo caminho do "Mandar para o
// comprador de um pedido" do modal Mensagens ao cliente: chega no cliente de
// verdade, por isso pergunta antes. O cartão de rastreio vai junto, remontado
// na hora (o histórico dos Correios pode ter andado desde a primeira vez).
const ERRO_MENSAGEM_CLIENTE: Record<string, string> = {
  admin_only: 'Só administradores mandam mensagem ao comprador.',
  envio_desligado: 'O envio está desligado no servidor: a Amazon descartaria a mensagem.',
  pedido_nao_encontrado: 'Não achei esse pedido Bling na aba Amazon.',
  pedido_nao_e_envio_proprio: 'Esse pedido não é de Envio próprio — só eles recebem mensagem nossa.',
  pedido_sem_email_do_comprador: 'Esse pedido não tem o endereço de retransmissão da Amazon.',
  email_falhou: 'O servidor de e-mail não respondeu. Tente de novo em instantes.',
  evento_desconhecido: 'Evento inválido.',
}
const reenviandoMensagem = ref<Set<string>>(new Set())
function chaveMensagem(c: Logistica, evento: string) {
  return `${c.id}:${evento}`
}
async function reenviarMensagemCliente(c: Logistica, m: { evento: string; evento_label: string }) {
  const pedido = (c.pedido_bling || '').trim()
  if (!pedido) {
    toasts.error('Sem número do pedido Bling', 'não dá pra reenviar por aqui')
    return
  }
  const ok = window.confirm(
    `Enviar de novo "${m.evento_label}" ao comprador do pedido ${c.pedido_marketplace || pedido}?\n\n` +
      'A mensagem chega no cliente de verdade, com o cartão de rastreio — não é teste. ' +
      'Para só conferir o texto, use "Enviar teste" no modal Mensagens ao cliente.',
  )
  if (!ok) return
  const chave = chaveMensagem(c, m.evento)
  reenviandoMensagem.value = new Set(reenviandoMensagem.value).add(chave)
  try {
    const r = await api<{ destinatario: string; cartao: boolean }>(
      `/api/logistica/mensagens-cliente/${m.evento}/enviar-agora`,
      { method: 'POST', body: { pedido_bling: pedido } },
    )
    toasts.success(
      'Mensagem enviada de novo',
      `${r.destinatario}${r.cartao ? ' · com o cartão de rastreio' : ' · sem o cartão (o 17track não tem evento desse rastreio)'}`,
    )
    await refresh()
  } catch (e: any) {
    const code = e?.data?.detail?.code || ''
    toasts.error(
      'Não foi possível enviar',
      ERRO_MENSAGEM_CLIENTE[code] || 'Não consegui enviar. Recarregue a página e tente de novo.',
    )
  } finally {
    const s = new Set(reenviandoMensagem.value)
    s.delete(chave)
    reenviandoMensagem.value = s
  }
}

// ---- Anexos de imagem na "Mensagem do Chamado" ----
function anexoUrl(id: string) {
  // Relativo → o cookie de sessão vai junto no <img src>/<a href>.
  return `/api/logistica/anexos/${id}`
}

async function uploadAnexo(s: LogisticaStatus, ev: Event) {
  const input = ev.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = '' // permite re-selecionar o mesmo arquivo
  if (!file) return
  setStatusBusy(s.id, true)
  statusError.value = null
  try {
    const fd = new FormData()
    fd.append('file', file)
    const anexo = await api<Anexo>(`/api/logistica/status/${s.id}/anexos`, {
      method: 'POST',
      body: fd,
    })
    if (!Array.isArray(s.anexos)) s.anexos = []
    s.anexos.push(anexo)
  } catch (e: any) {
    statusError.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    setStatusBusy(s.id, false)
  }
}

async function removeAnexo(s: LogisticaStatus, anexoId: string) {
  if (!confirm('Remover esta imagem?')) return
  setStatusBusy(s.id, true)
  statusError.value = null
  try {
    await api(`/api/logistica/anexos/${anexoId}`, { method: 'DELETE' })
    s.anexos = (s.anexos || []).filter((a) => a.id !== anexoId)
  } catch (e: any) {
    statusError.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    setStatusBusy(s.id, false)
  }
}

// ---- Enviar chamado direto pro ML (aba Mercado Livre) ----
const CHAMADO_ERROS: Record<string, string> = {
  logistica_sem_mensagem_chamado:
    'Sem regra na aba Status que case com o Status Plataforma deste pedido — cadastre a mensagem do chamado.',
  logistica_nao_ml: 'Só pedidos do Mercado Livre.',
  logistica_sem_pedido: 'Pedido sem número de marketplace.',
  logistica_sem_integracao: 'Conta sem integração ML configurada.',
  logistica_sem_reclamacao:
    'Pedido sem reclamação aberta pelo comprador — só dá pra abrir chamado no painel do Mercado Livre.',
  logistica_reclamacao_encerrada: 'Reclamação já encerrada no Mercado Livre.',
  logistica_reclamacao_sem_acao:
    'O Mercado Livre não liberou falar com o mediador neste chamado.',
  encaminhado_ao_robo:
    'Encaminhado ao robô (aba Chamados, canal robô): no Mercado Livre pelo formulário de ajuda, na TikTok e na Shopee pelo Seller Center. O protocolo volta pra cá quando abrir.',
  chamado_manual_na_aba:
    'Já existe chamado deste pedido registrado à mão na aba Chamados, ainda sem protocolo — preencha o nº lá e ele aparece aqui.',
  chamado_resolvido_na_aba: 'O chamado deste pedido foi resolvido na aba Chamados sem protocolo — nada a abrir.',
  chamado_de_outra_origem:
    'Já existe chamado aberto deste pedido em outra aba (Devoluções/Vendas/Margem) — o robô não abre um segundo.',
  robo_sem_protocolo:
    'O robô disse que abriu, mas não trouxe o nº do chamado — confira na aba Chamados e preencha o protocolo.',
}
const sendingChamado = ref<Set<string>>(new Set())
// Coluna Chamado sem protocolo na linha: o que a aba Chamados diz do pedido.
// Aberto sem nº = alguém ainda precisa abrir na plataforma (Amazon: SAFE-T no
// Seller Center); aberto com nº = já foi (o motor copia o nº pra linha em até
// 5 min); encerrado = fechado na aba. Vazio = nunca teve ticket.
function chamadoAbaResumo(c: Logistica): { texto: string; tom: 'pendente' | 'ok' | 'encerrado' } | null {
  if (c.chamado || !c.chamado_aba) return null
  const t = c.chamado_aba
  const quando = t.data ? fmtDia(t.data) : fmtQuando(t.created_at)
  const desde = quando ? ` desde ${quando}` : ''
  if (!t.resolvido) {
    if (t.chamado) return { texto: `${t.chamado} (aba Chamados)`, tom: 'ok' }
    if (t.canal === 'robo') return { texto: `pendente: robô abrindo${desde}`, tom: 'pendente' }
    const oque = isAmazon(c) ? 'abrir SAFE-T' : 'abrir chamado'
    return { texto: `pendente: ${oque}${desde} (aba Chamados)`, tom: 'pendente' }
  }
  if (t.chamado) return { texto: `${t.chamado} · encerrado na aba Chamados`, tom: 'encerrado' }
  return { texto: `encerrado na aba Chamados sem nº${desde}`, tom: 'encerrado' }
}
// O motor tentou abrir sozinho e o ML recusou: mostra o motivo (mesma tabela
// de erros do botão) e quando foi — o robô tenta de novo em 6 h.
function chamadoAutoAviso(c: Logistica): string | null {
  if (c.chamado || !c.chamado_auto_erro) return null
  const motivo = CHAMADO_ERROS[c.chamado_auto_erro] || c.chamado_auto_erro
  const quando = c.chamado_auto_at ? ` (robô tentou ${fmtDataHora(c.chamado_auto_at)})` : ''
  return `${motivo}${quando}`
}
async function enviarChamado(c: Logistica) {
  if (
    !confirm(
      'Isto abre a mediação do Mercado Livre nesta reclamação (o ML entra como mediador — ação irreversível) e manda a mensagem da regra pro ML. Continuar?',
    )
  )
    return
  sendingChamado.value = new Set(sendingChamado.value).add(c.id)
  error.value = null
  try {
    const updated = await api<Logistica>(`/api/logistica/${c.id}/enviar-chamado`, {
      method: 'POST',
    })
    const i = rows.value.findIndex((r) => r.id === c.id)
    if (i >= 0) rows.value[i] = updated
  } catch (e: any) {
    const code = e?.data?.detail?.code
    error.value =
      (code && CHAMADO_ERROS[code]) ||
      e?.data?.detail?.erro ||
      code ||
      e?.message ||
      'erro'
  } finally {
    const s = new Set(sendingChamado.value)
    s.delete(c.id)
    sendingChamado.value = s
  }
}

const MENSAGEM_BLING_ERROS: Record<string, string> = {
  logistica_sem_mensagem_bling:
    'Sem regra na aba Status com Mensagem Bling que case com o Status Plataforma deste pedido.',
  logistica_sem_pedido_bling: 'Linha sem número de pedido Bling.',
  logistica_pedido_bling_nao_achado: 'Pedido não encontrado no Bling.',
  logistica_sem_integracao_bling: 'Integração Bling não configurada.',
}
const aplicandoBling = ref<Set<string>>(new Set())
async function aplicarMensagemBling(c: Logistica) {
  aplicandoBling.value = new Set(aplicandoBling.value).add(c.id)
  error.value = null
  try {
    // 1) dry-run: mostra o que SERIA escrito nas Observações antes de confirmar.
    const prev = await api<{ observacoes_novo: string }>(
      `/api/logistica/${c.id}/mensagem-bling/preview`,
      { method: 'POST' },
    )
    if (
      !confirm(
        `Vai anexar nas Observações do pedido no Bling (linha nova no topo, nada é sobrescrito):\n\n${prev.observacoes_novo}\n\nConfirmar?`,
      )
    )
      return
    // 2) aplica de verdade (PUT do pedido inteiro sanitizado).
    await api(`/api/logistica/${c.id}/mensagem-bling`, { method: 'POST' })
    toasts.success('Mensagem Bling aplicada', c.pedido_bling || '')
  } catch (e: any) {
    const code = e?.data?.detail?.code
    const msg =
      (code && MENSAGEM_BLING_ERROS[code]) || e?.data?.detail?.erro || code || e?.message || 'erro'
    error.value = msg
    toasts.error('Não foi possível aplicar', msg)
  } finally {
    const s = new Set(aplicandoBling.value)
    s.delete(c.id)
    aplicandoBling.value = s
  }
}

// ---- Executor: Alterar Status Bling (muda a situação do pedido no Bling) ----
const STATUS_BLING_ERROS: Record<string, string> = {
  logistica_sem_status_bling:
    'Sem regra na aba Status com "Alterar Status Bling" que case com o Status Plataforma deste pedido.',
  logistica_status_bling_desconhecido: 'A situação alvo não existe no catálogo do Bling.',
  logistica_status_atual_divergente:
    'O pedido não está no "Status Atual" que a regra exige — a mudança não foi aplicada pra não regredir.',
  logistica_promocao_sem_envio_confirmado:
    'A plataforma ainda não confirmou que o pacote saiu (Amazon: EasyShip coletado ou, no Envio próprio, o rastreio dos Correios com movimentação) — não foi marcado como "Em andamento".',
  logistica_status_override_humano:
    'O sistema já levou este pedido a esse status e alguém o tirou de lá — não reaplica enquanto o Status Plataforma não mudar.',
  logistica_sem_pedido_bling: 'Linha sem número de pedido Bling.',
  logistica_pedido_bling_nao_achado: 'Pedido não encontrado no Bling.',
  logistica_sem_integracao_bling: 'Integração Bling não configurada.',
}
// A regra casada pede mudança de status quando o resumo tem "Status Bling → X".
function temStatusBlingAcao(c: Logistica): boolean {
  return c.acao_resumo.some((a) => a.startsWith('Status Bling'))
}
const aplicandoStatus = ref<Set<string>>(new Set())
async function aplicarStatusBling(c: Logistica) {
  aplicandoStatus.value = new Set(aplicandoStatus.value).add(c.id)
  error.value = null
  try {
    // 1) dry-run: mostra a transição da regra (Status Atual -> Alvo) antes de confirmar.
    const prev = await api<{
      situacao_de: string | null
      situacao_alvo: string
      situacao_atual_nome: string | null
      ja_no_alvo: boolean
      override_humano: boolean
      aplicavel: boolean
    }>(`/api/logistica/${c.id}/alterar-status-bling/preview`, { method: 'POST' })
    // O preview lê a situação viva do Bling e o backend já sincronizou o
    // status_bling da linha; reflete no painel em qualquer ramo (nada a fazer/fora
    // do fluxo/confirmar) pra a coluna se auto-corrigir sem esperar recarregar.
    if (prev.situacao_atual_nome) c.status_bling = prev.situacao_atual_nome
    if (prev.ja_no_alvo) {
      toasts.info('Nada a fazer', `Pedido já está em "${prev.situacao_alvo}".`)
      return
    }
    // O robô já levou o pedido ao alvo e alguém tirou dali: o robô não reaplica,
    // mas quem aperta o botão decide (01/10, TikTok 296856 — antes caía no
    // "Fora do fluxo", que não fazia sentido).
    if (prev.override_humano) {
      const atualNome = prev.situacao_atual_nome || '(desconhecida)'
      if (
        !confirm(
          `O robô já tinha mudado este pedido para "${prev.situacao_alvo}" e depois alguém voltou para "${atualNome}".\n\n` +
            `Mudar mesmo assim?\n\n${atualNome}  →  ${prev.situacao_alvo}`,
        )
      )
        return
      await api(`/api/logistica/${c.id}/alterar-status-bling`, {
        method: 'POST',
        query: { forcar: true },
      })
      c.status_bling = prev.situacao_alvo
      toasts.success('Status Bling alterado', `${c.pedido_bling || ''} → ${prev.situacao_alvo}`)
      return
    }
    // A regra tem "Status Atual" (o "de") mas o pedido não está nele → não muda
    // pra não regredir (ex.: já Entregue, regra "Em andamento → Entregue").
    if (!prev.aplicavel) {
      const atualNome = prev.situacao_atual_nome || '(desconhecida)'
      toasts.info(
        'Fora do fluxo',
        `A regra é "${prev.situacao_de} → ${prev.situacao_alvo}", mas o pedido está em "${atualNome}". Nada a fazer.`,
      )
      return
    }
    // "De" = o Status Atual da regra (o fluxo esperado); cai no atual do Bling se
    // a regra não exige um "de" específico.
    const de = prev.situacao_de || prev.situacao_atual_nome || '(desconhecida)'
    if (
      !confirm(
        `Vai mudar a situação do pedido no Bling:\n\n${de}  →  ${prev.situacao_alvo}\n\nConfirmar?`,
      )
    )
      return
    // 2) aplica de verdade (PATCH da situação, não reenvia o pedido).
    await api(`/api/logistica/${c.id}/alterar-status-bling`, { method: 'POST' })
    c.status_bling = prev.situacao_alvo
    toasts.success('Status Bling alterado', `${c.pedido_bling || ''} → ${prev.situacao_alvo}`)
  } catch (e: any) {
    const code = e?.data?.detail?.code
    const msg =
      (code && STATUS_BLING_ERROS[code]) || e?.data?.detail?.erro || code || e?.message || 'erro'
    error.value = msg
    toasts.error('Não foi possível alterar', msg)
  } finally {
    const s = new Set(aplicandoStatus.value)
    s.delete(c.id)
    aplicandoStatus.value = s
  }
}
</script>

<template>
  <!-- 11/11/2026: padrão de Chamados/Conciliação — cabeçalho numa linha, filtros em
       pílulas, cartões que filtram, etiquetas do Status Bling, tabela agrupada e,
       à direita, "Onde agir hoje" + Raio-X da linha clicada. -->
  <div class="min-w-0 space-y-2.5">
    <div class="flex flex-wrap items-center gap-3 rounded-md border bg-card px-3 py-1.5">
      <h1 class="text-[15px] font-bold">Logística</h1>
      <div class="flex flex-wrap items-center gap-3.5 text-xs font-semibold">
        <div class="flex flex-wrap gap-0.5">
          <button
            v-for="t in ABAS_VISIVEIS"
            :key="t.key"
            type="button"
            class="border-b-2 px-2.5 py-1"
            :class="tab === t.key ? 'border-primary text-primary' : 'border-transparent text-muted-foreground hover:text-foreground'"
            @click="tab = t.key"
          >
            {{ t.label }}
            <span v-if="tab === t.key && !flexAnuncios" class="font-medium text-muted-foreground">{{ baseRows.length }}</span>
          </button>
          <button
            type="button"
            class="border-b-2 px-2.5 py-1"
            :class="tab === 'status' ? 'border-primary text-primary' : 'border-transparent text-muted-foreground hover:text-foreground'"
            @click="tab = 'status'"
          >
            Status
          </button>
        </div>
        <!-- Amazon: DBA × Envio próprio (dois painéis, como no Seller Central) -->
        <div v-if="tab === 'amazon'" class="inline-flex rounded-md border p-px" role="tablist">
          <button
            type="button"
            class="rounded px-2.5 py-1"
            :class="amazonSub === 'dba' ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground'"
            title="Delivery by Amazon: a Amazon coleta e entrega"
            @click="amazonSub = 'dba'"
          >
            Amazon DBA <span class="font-medium opacity-70">{{ amazonCounts.dba }}</span>
          </button>
          <button
            type="button"
            class="rounded px-2.5 py-1"
            :class="amazonSub === 'proprio' ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground'"
            title="Envio próprio: postamos nos Correios e respondemos pela entrega"
            @click="amazonSub = 'proprio'"
          >
            Envio próprio <span class="font-medium opacity-70">{{ amazonCounts.proprio }}</span>
          </button>
        </div>
        <!-- Flex: Pedidos × Anúncios -->
        <div v-if="tab === 'flex'" class="inline-flex rounded-md border p-px" role="tablist">
          <button
            type="button"
            class="rounded px-2.5 py-1"
            :class="flexSub === 'pedidos' ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground'"
            title="Pedidos Flex do Mercado Livre e da Shopee (também aparecem nas abas de cada plataforma, com o selo Flex)"
            @click="flexSub = 'pedidos'"
          >
            Pedidos Flex <span class="font-medium opacity-70">{{ flexPedidosCount }}</span>
          </button>
          <button
            type="button"
            class="rounded px-2.5 py-1"
            :class="flexSub === 'anuncios' ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground'"
            title="Em quais anúncios o Flex está ligado, o que o sistema quer e o porquê — aprovar, sincronizar e emergência"
            @click="flexSub = 'anuncios'"
          >
            Anúncios Flex
            <span
              v-if="flexAguardando"
              class="ml-1 rounded-full bg-amber-400 px-1.5 text-[11px] font-semibold text-amber-950"
              :title="`${flexAguardando} anúncio(s) esperando sua aprovação para ligar o Flex`"
            >{{ flexAguardando }}</span>
          </button>
        </div>
        <!-- Pedido Flex sem peça em SP: aviso fixo (o robô NÃO trocou o lote). -->
        <button
          v-if="tab === 'flex' && Object.keys(flexAlertas).length && flexSub === 'pedidos'"
          type="button"
          class="inline-flex items-center gap-1 rounded-md border border-rose-300 bg-rose-50 px-2.5 py-1 text-rose-800 hover:bg-rose-100 dark:border-rose-700 dark:bg-rose-950/40 dark:text-rose-300"
          :title="`Ver a lista dos pedidos Flex sem peça em ${flexLocal.cidade} (na sub-aba Anúncios Flex)`"
          @click="flexSub = 'anuncios'"
        >
          {{ Object.keys(flexAlertas).length }} pedido(s) Flex sem peça em {{ flexLocal.cidade }}
        </button>
      </div>
      <div class="ml-auto flex flex-wrap items-center justify-end gap-1.5 text-xs font-semibold">
        <template v-if="tab !== 'status' && !flexAnuncios">
          <button
            type="button"
            class="inline-flex items-center gap-1 rounded-md border px-2.5 py-1 hover:bg-muted disabled:opacity-50"
            :disabled="loading || statusLoading || recarregando"
            title="Relê as plataformas e recarrega a lista (a tela também se atualiza sozinha a cada 30 s)"
            @click="recarregar"
          >
            <RefreshCw class="size-3.5" :class="loading || statusLoading || recarregando ? 'animate-spin' : ''" /> Atualizar
          </button>
          <!-- O robô já faz isso de 15 em 15 min; o botão é pra quando o operador
               não quer esperar. Some na aba Flex: o Flex não passa pelos Correios. -->
          <button
            v-if="canEdit && tab !== 'flex'"
            type="button"
            class="inline-flex items-center gap-1 rounded-md border px-2.5 py-1 hover:bg-muted disabled:opacity-50"
            :disabled="atualizandoRastreio"
            title="Busca agora a posição dos pacotes dos Correios. O sistema já faz isso sozinho a cada 15 minutos."
            @click="atualizarRastreio"
          >
            <MapPin class="size-3.5" :class="atualizandoRastreio ? 'animate-pulse' : ''" />
            {{ atualizandoRastreio ? 'Buscando…' : 'Correios' }}
          </button>
          <button
            type="button"
            class="inline-flex items-center gap-1 rounded-md border px-2.5 py-1 hover:bg-muted disabled:opacity-50"
            :disabled="exportandoExcel || loading || !filteredRows.length"
            :title="`Baixa em Excel os ${filteredRows.length} pedidos que estão aparecendo (todas as páginas, com os filtros da tela)`"
            @click="exportarExcel"
          >
            <Download class="size-3.5" :class="exportandoExcel ? 'animate-pulse' : ''" />
            {{ exportandoExcel ? 'Gerando…' : 'Excel' }}
          </button>
          <template v-if="tab === 'amazon' && canInformarAmazon">
            <button
              type="button"
              class="inline-flex items-center gap-1 rounded-md border px-2.5 py-1 hover:bg-muted"
              title="Quem recebe no Threema os avisos de prazo do Envio próprio (3 dias antes da data máxima da Amazon, previsão dos Correios vencida, prazo vencido) — e manda agora a lista dos pedidos em trânsito"
              @click="informarAmazonOpen = true"
            >
              <Megaphone class="size-3.5" /> Avisos Amazon
            </button>
            <button
              type="button"
              class="inline-flex items-center gap-1 rounded-md border px-2.5 py-1 hover:bg-muted"
              title="Textos dos e-mails que o robô manda ao comprador da Amazon (problema, atraso, entrega) e o envio de teste"
              @click="mensagensClienteOpen = true"
            >
              <Mail class="size-3.5" /> Mensagens ao cliente
            </button>
          </template>
        </template>
        <!-- Aba Status (12/11): os botões dela no cabeçalho, como nas outras abas. -->
        <template v-if="tab === 'status'">
          <button
            type="button"
            class="inline-flex items-center gap-1 rounded-md border px-2.5 py-1 hover:bg-muted disabled:opacity-50"
            :disabled="statusLoading"
            title="Recarrega as regras da aba Status"
            @click="refreshStatus"
          >
            <RefreshCw class="size-3.5" :class="statusLoading ? 'animate-spin' : ''" /> Atualizar
          </button>
          <button
            v-if="canEdit"
            type="button"
            class="inline-flex items-center gap-1 rounded-md border px-2.5 py-1 hover:bg-muted"
            title="Junta as regras repetidas (mesmo Status Plataforma e mesmas ações)"
            @click="abrirJuntarRepetidas"
          >
            <Combine class="size-3.5" /> Juntar repetidas
          </button>
        </template>
        <button
          v-if="isAdmin"
          type="button"
          class="inline-flex items-center gap-1 rounded-md border px-2.5 py-1 hover:bg-muted"
          title="Manda via Threema a lista dos pedidos acompanhados no painel (só admins)"
          @click="informarOpen = true"
        >
          <Megaphone class="size-3.5" /> Informar
        </button>
        <button
          v-if="canEdit && tab !== 'status' && !flexAnuncios"
          type="button"
          class="inline-flex items-center gap-1 whitespace-nowrap rounded-md bg-primary px-2.5 py-1 text-primary-foreground"
          :title="tab === 'flex' ? 'O caso criado à mão aparece na aba da plataforma (Mercado Livre ou Shopee), não na aba Flex.' : undefined"
          @click="openNew"
        >
          <Plus class="size-3.5" /> novo caso
        </button>
        <button
          v-if="canEdit && tab === 'status'"
          type="button"
          class="inline-flex items-center gap-1 whitespace-nowrap rounded-md bg-primary px-2.5 py-1 text-primary-foreground"
          @click="openStatusForm"
        >
          <Plus class="size-3.5" /> novo status
        </button>
      </div>
    </div>

    <LogisticaFlexAnuncios
      v-if="flexAnuncios"
      :can-edit="canEdit"
      @buscar-pedido="buscarPedidoFlex"
      @mudou="aoMudarFlex"
    />

    <!-- ============ ABAS DE MARKETPLACE (ML/Shopee/Amazon/TikTok/Flex) ============ -->
    <template v-if="tab !== 'status' && !flexAnuncios">
      <!-- Sem saldo no 17track a coluna Localização inteira congela. O aviso
           fica FIXO (não é toast) porque explica o estado atual do sistema e
           depende de uma ação fora do DaVinci: comprar créditos. -->
      <div
        v-if="correiosSemSaldo"
        class="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-700 dark:bg-amber-950/40 dark:text-amber-200"
      >
        <strong>Os Correios pararam de atualizar.</strong>
        A conta do 17track está sem saldo, então a coluna Localização mostra o que o
        marketplace informa, que costuma atrasar. Para voltar ao normal é preciso comprar
        créditos em
        <a href="https://api.17track.net" target="_blank" rel="noopener" class="underline">17track.net</a>
        (entrar na conta → Quota).
      </div>

      <!-- filtros (mesmo formato de Chamados/Conciliação) -->
      <div class="flex flex-wrap items-center gap-1.5 text-xs">
        <label v-if="tab === 'flex'" class="inline-flex h-7 items-center gap-1 rounded-md border bg-card px-2">
          <span class="font-semibold text-muted-foreground">Plataforma</span>
          <select v-model="flexPlataformaFilter" class="bg-transparent outline-none">
            <option value="">Mercado Livre e Shopee</option>
            <option value="ml">Mercado Livre</option>
            <option value="shopee">Shopee</option>
          </select>
        </label>
        <label class="inline-flex h-7 items-center gap-1 rounded-md border bg-card px-2">
          <span class="font-semibold text-muted-foreground">Conta</span>
          <select v-model="contaFilter" class="max-w-[150px] bg-transparent outline-none">
            <option value="all">Todas</option>
            <option v-for="c in contas" :key="c" :value="c">{{ c }}</option>
          </select>
        </label>
        <label class="inline-flex h-7 items-center gap-1 rounded-md border bg-card px-2" title="A acompanhar: esconde os pedidos que já chegaram ao fim (resolvidos) e não pedem monitoramento">
          <span class="font-semibold text-muted-foreground">Mostrar</span>
          <select v-model="mostrarSel" class="bg-transparent outline-none">
            <option value="acompanhar">A acompanhar</option>
            <option value="tudo">Tudo</option>
          </select>
        </label>
        <label class="inline-flex h-7 items-center gap-1 rounded-md border bg-card px-2">
          <span class="font-semibold text-muted-foreground">Status Bling</span>
          <select v-model="statusBlingFilter" class="bg-transparent outline-none">
            <option value="all">Todos</option>
            <option v-for="s in statusBlings" :key="s" :value="s">{{ s }}</option>
          </select>
        </label>
        <label class="inline-flex h-7 items-center gap-1 rounded-md border bg-card px-2" title="Período da data do pedido">
          <span class="font-semibold text-muted-foreground">Período</span>
          <input
            v-model="dataInicioFilter"
            type="date"
            :max="dataFimFilter || undefined"
            title="Data inicial"
            class="bg-transparent outline-none"
          />
          <span class="text-muted-foreground">até</span>
          <input
            v-model="dataFimFilter"
            type="date"
            :min="dataInicioFilter || undefined"
            title="Data final"
            class="bg-transparent outline-none"
          />
        </label>
        <label class="inline-flex h-7 min-w-0 items-center gap-1 rounded-md border bg-card px-2">
          <Search class="size-3.5 text-muted-foreground" />
          <input v-model="search" class="w-64 bg-transparent outline-none" placeholder="buscar pedido, produto, SKU, conta, rastreio" />
        </label>
        <button v-if="filtrosAtivos" type="button" class="text-muted-foreground underline" @click="limparFiltros">limpar</button>
        <span class="text-muted-foreground">{{ filteredRows.length }} de {{ rows.length }} pedidos</span>
      </div>

      <!-- cartões: clicar filtra a lista (clicar de novo tira o filtro) -->
      <div class="grid grid-cols-2 gap-2" :class="cartoes.length > 5 ? 'lg:grid-cols-6' : 'lg:grid-cols-5'">
        <button
          v-for="k in cartoes"
          :key="k.k"
          type="button"
          class="rounded-md border bg-card px-3 py-1.5 text-center hover:bg-muted/40 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"
          :class="k.k !== 'all' && cartaoFiltro === k.k ? 'ring-2 ring-primary' : ''"
          :title="k.k === 'all' ? 'mostrar todos' : cartaoFiltro === k.k ? 'tirar o filtro' : `filtrar: ${k.dica}`"
          @click="k.k === 'all' ? (cartaoFiltro = 'all') : filtrarCartao(k.k)"
        >
          <span class="block text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">{{ k.label }}</span>
          <span class="block text-xl font-bold leading-tight" :class="k.cor">{{ k.n }}</span>
        </button>
      </div>

      <!-- Status Bling dos casos: clicar numa etiqueta filtra -->
      <div v-if="statusPills.length" class="flex flex-wrap items-center gap-3 rounded-md border bg-card px-3 py-1.5 text-xs">
        <span class="text-[13px] font-bold">Status Bling dos casos</span>
        <button
          v-for="sp in statusPills"
          :key="sp.s"
          type="button"
          class="inline-flex items-center gap-1.5 rounded bg-muted px-2 py-0.5 font-semibold focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"
          :class="statusBlingFilter === sp.s ? 'ring-2 ring-primary' : ''"
          :title="statusBlingFilter === sp.s ? 'tirar o filtro' : `filtrar: ${sp.rotulo}`"
          @click="filtrarStatusBling(sp.s)"
        >
          <span class="size-[7px] rounded-full" :class="sp.dot" />
          {{ sp.n }} {{ sp.rotulo }}
        </button>
        <span class="ml-auto hidden text-[11px] text-muted-foreground lg:inline">clique numa etiqueta ou num cartão para filtrar</span>
      </div>

      <div v-if="error" class="text-sm text-red-500">erro: {{ error }}</div>

      <!-- Desktop: tabela + Onde agir hoje / Raio-X -->
      <div class="hidden items-stretch gap-3 md:grid xl:grid-cols-[minmax(0,1fr)_340px]">
        <div class="flex min-w-0 flex-col rounded-md border bg-card">
          <div class="relative flex items-center justify-center rounded-t-md bg-[#1f3a5f] px-3 py-1.5 text-xs font-semibold text-white">
            <span>{{ tituloTabela }}</span>
            <span class="absolute right-3 hidden text-[11px] font-normal text-slate-300 2xl:block">o que a plataforma diz × o que está no Bling</span>
          </div>
          <div class="overflow-x-auto">
            <table class="w-full table-fixed border-collapse text-[11.5px]" :class="ehProprio ? 'min-w-[1300px]' : 'min-w-[1110px]'">
              <colgroup>
                <col class="w-[46px]"><col :class="ehProprio ? 'w-[112px]' : 'w-[124px]'"><col :class="ehProprio ? 'w-[70px]' : 'w-[88px]'"><col>
                <col :class="ehProprio ? 'w-[130px]' : 'w-[200px]'"><col :class="ehProprio ? 'w-[160px]' : 'w-[210px]'">
                <template v-if="ehProprio"><col class="w-[80px]"><col class="w-[92px]"></template>
                <col :class="ehProprio ? 'w-[100px]' : 'w-[104px]'"><col :class="ehProprio ? 'w-[90px]' : 'w-[120px]'">
                <col :class="ehProprio ? 'w-[110px]' : 'w-[140px]'"><col :class="ehProprio ? 'w-[80px]' : 'w-[84px]'">
                <col v-if="ehProprio" class="w-[100px]">
              </colgroup>
              <thead class="bg-muted text-[10px] uppercase tracking-wide text-muted-foreground">
                <tr>
                  <th class="border-b px-1.5 py-1 text-center font-semibold" colspan="4">Pedido</th>
                  <th class="border-b border-l border-l-slate-300 px-1.5 py-1 text-center font-semibold dark:border-l-slate-600" :colspan="ehProprio ? 4 : 2">Plataforma</th>
                  <th class="border-b border-l border-l-slate-300 px-1.5 py-1 text-center font-semibold dark:border-l-slate-600" colspan="2">Bling</th>
                  <th class="border-b border-l border-l-slate-300 px-1.5 py-1 text-center font-semibold dark:border-l-slate-600" :colspan="ehProprio ? 3 : 2">Caso</th>
                </tr>
                <tr>
                  <th class="border-b px-1.5 py-1 text-center font-semibold">Data</th>
                  <th class="border-b px-1.5 py-1 text-center font-semibold" title="Pedido no Bling e, embaixo, o número na plataforma">Pedido</th>
                  <th class="border-b px-1.5 py-1 text-center font-semibold">Conta</th>
                  <th class="border-b px-1.5 py-1 text-left font-semibold">Produto</th>
                  <th class="border-b border-l border-l-slate-300 px-1.5 py-1 text-left font-semibold dark:border-l-slate-600" title="Status na plataforma (passe o mouse para ver campo a campo e desde quando)">Status</th>
                  <th class="border-b px-1.5 py-1 text-left font-semibold">Localização</th>
                  <template v-if="ehProprio">
                    <th class="border-b px-1.5 py-1 text-center font-semibold" title="Previsão de entrega da transportadora (objeto de postagem do Bling)">Previsão</th>
                    <th class="border-b px-1.5 py-1 text-center font-semibold" title="Data máxima de entrega da Amazon — depois dela a Amazon reembolsa o cliente">Entregar até</th>
                  </template>
                  <th class="border-b border-l border-l-slate-300 px-1.5 py-1 text-center font-semibold dark:border-l-slate-600">Status Bling</th>
                  <th class="border-b px-1.5 py-1 text-center font-semibold">Divergência</th>
                  <th class="border-b border-l border-l-slate-300 px-1.5 py-1 text-center font-semibold dark:border-l-slate-600">Chamado</th>
                  <th class="border-b px-1.5 py-1 text-center font-semibold" title="Regra da aba Status para a combinação deste pedido">Regra</th>
                  <th v-if="ehProprio" class="border-b px-1.5 py-1 text-center font-semibold" title="Suspensão da entrega no Melhor Envio (o pacote volta)">Suspensão</th>
                </tr>
              </thead>
              <tbody>
                <tr v-if="loading && !rows.length">
                  <td :colspan="ehProprio ? 13 : 10" class="py-8 text-center text-muted-foreground">
                    <RefreshCw class="mr-1.5 inline size-4 animate-spin" /> carregando…
                  </td>
                </tr>
                <tr
                  v-for="c in pagedRows"
                  :key="c.id"
                  class="min-h-10 cursor-pointer border-b align-middle hover:bg-muted/40"
                  :class="[
                    c.id === raioId ? 'bg-blue-50 shadow-[inset_3px_0_0_hsl(var(--primary))] dark:bg-blue-950/30' : '',
                    c.id !== raioId && precisaAtencao(c) ? 'bg-red-50/70 shadow-[inset_3px_0_0_#dc2626] dark:bg-red-950/30' : '',
                  ]"
                  :title="precisaAtencao(c) ? 'Sem regra na aba Status para esta combinação' : undefined"
                  @click="abrirRaio(c)"
                >
                  <td class="px-1.5 py-1.5 text-center text-muted-foreground" :title="fmtDate(c.data)">{{ fmtDate(c.data).slice(0, 5) }}</td>
                  <td class="px-1.5 py-1.5 text-center">
                    <div class="font-mono font-bold">{{ c.pedido_bling || '—' }}</div>
                    <!-- Um clique seleciona o nº da plataforma pra copiar, sem abrir o Raio-X. -->
                    <div class="truncate font-mono text-[10.5px] text-muted-foreground"><span class="cursor-text select-all" @click.stop>{{ c.pedido_marketplace || '' }}</span></div>
                  </td>
                  <td class="px-1.5 py-1.5 text-center">
                    <div class="truncate" :title="c.conta || ''">{{ c.conta || '—' }}</div>
                    <span
                      v-if="c.envio_flex && podeVerFlex && tab !== 'flex'"
                      class="rounded border border-violet-300 bg-violet-50 px-1 text-[10px] font-semibold text-violet-800 dark:border-violet-700 dark:bg-violet-950/40 dark:text-violet-300"
                      :title="flexSeloTitulo(c)"
                    >Flex</span>
                    <div v-if="flexAlerta(c)" class="text-[10.5px] font-medium text-rose-700 dark:text-rose-400" :title="flexAlerta(c) || ''">sem peça em {{ flexLocal.cidade }}</div>
                  </td>
                  <td class="truncate px-1.5 py-1.5" :title="produtoTitulo(c)">{{ produtoResumo(c) }}</td>
                  <td class="border-l border-l-slate-300 px-1.5 py-1.5 dark:border-l-slate-600" :title="statusTooltip(c) || assinatura(c)">
                    <!-- 12/11: o status inteiro (sem "…") e o botão de copiar logo depois,
                         pra colar na aba Status ao criar a regra. -->
                    <div class="break-words text-[11px] leading-tight">
                      {{ assinatura(c) || '—' }}
                      <button
                        v-if="assinatura(c)"
                        type="button"
                        class="ml-0.5 inline-flex align-[-2px] text-muted-foreground hover:text-foreground"
                        title="Copiar o status (chave) para criar a regra na aba Status"
                        aria-label="Copiar o status"
                        @click.stop="copiarChave(c)"
                      ><Copy class="size-3" /></button>
                    </div>
                    <div v-if="statusResumoData(c)" class="truncate text-[10px] text-muted-foreground">{{ statusResumoData(c) }}</div>
                  </td>
                  <td class="px-1.5 py-1.5">
                    <div
                      v-if="c.volta_localizacao"
                      class="truncate text-[11px]"
                      :class="c.volta_grave ? 'font-semibold text-rose-700 dark:text-rose-400' : ''"
                      :title="voltaTitulo(c)"
                    >↩ {{ c.volta_localizacao }}</div>
                    <div
                      v-else
                      class="truncate text-[11px]"
                      :title="proprioSemRastreio(c) ? DESTINO_TITLE : c.localizacao || ''"
                    >{{ proprioSemRastreio(c) && c.localizacao ? `Destino: ${c.localizacao}` : c.localizacao || '—' }}</div>
                    <div
                      v-if="ehCorreios(c.rastreio)"
                      class="truncate text-[10px]"
                      :class="leituraVelha(c) ? 'text-amber-700 dark:text-amber-400' : 'text-muted-foreground'"
                    >{{ c.localizacao_at ? `Correios · lido ${fmtDesde(c.rastreio_lido_em || c.localizacao_at)}` : 'sem leitura dos Correios ainda' }}</div>
                  </td>
                  <template v-if="ehProprio">
                    <td class="px-1.5 py-1.5 text-center text-[11px]">
                      <span v-if="c.entregue_em" class="text-emerald-700 dark:text-emerald-400" :title="`Entrega registrada em ${fmtDataHora(c.entregue_em)}`">entregue {{ fmtQuando(c.entregue_em) }}</span>
                      <span v-else-if="c.previsao_correios" :class="previsaoVencida(c) ? 'font-semibold text-rose-700 dark:text-rose-400' : ''">{{ fmtDia(c.previsao_correios).slice(0, 5) }}</span>
                      <span v-else class="text-muted-foreground">—</span>
                    </td>
                    <td class="px-1.5 py-1.5 text-center text-[11px]">
                      <template v-if="c.prazo_entrega_amazon">
                        <div :class="prazoVencido(c) ? 'font-semibold text-rose-700 dark:text-rose-400' : ''">{{ fmtDia(c.prazo_entrega_amazon).slice(0, 5) }}</div>
                        <div v-if="!c.entregue_em" class="text-[10px]" :class="prazoVencido(c) ? 'text-rose-700 dark:text-rose-400' : 'text-muted-foreground'">{{ prazoResumo(c) }}</div>
                      </template>
                      <span v-else class="text-muted-foreground">—</span>
                    </td>
                  </template>
                  <td class="border-l border-l-slate-300 px-1.5 py-1.5 text-center dark:border-l-slate-600">
                    <span v-if="c.status_bling" class="inline-block w-[92px] truncate rounded py-0.5 text-[11px] font-semibold" :class="corStatusBling(c.status_bling)" :title="c.status_bling">{{ c.status_bling }}</span>
                    <span v-else class="text-muted-foreground">—</span>
                  </td>
                  <td class="truncate px-1.5 py-1.5 text-center text-[11px]" :class="c.divergencia ? 'font-semibold text-amber-700 dark:text-amber-400' : 'text-muted-foreground'" :title="c.divergencia || ''">{{ c.divergencia || '—' }}</td>
                  <td class="border-l border-l-slate-300 px-1.5 py-1.5 text-center text-[11px] dark:border-l-slate-600">
                    <span
                      v-if="situacaoChamado(c)"
                      class="inline-block rounded px-2 py-0.5 text-[11px] font-semibold"
                      :class="COR_SITUACAO_CHAMADO[situacaoChamado(c)!]"
                    >{{ situacaoChamado(c) }}</span>
                    <span v-else-if="!problemaSemChamado(c)" class="text-muted-foreground">—</span>
                    <div v-if="problemaSemChamado(c)" class="text-[10.5px] font-bold text-red-600 dark:text-red-400" title="Status Bling Problemas e nenhum chamado aberto: ninguém está olhando este pedido">sem chamado aberto</div>
                  </td>
                  <td class="px-1.5 py-1.5 text-center">
                    <span v-if="precisaAtencao(c)" class="rounded bg-amber-100 px-1.5 py-0.5 text-[10.5px] font-semibold text-amber-800 dark:bg-amber-950/50 dark:text-amber-300">sem regra</span>
                    <span v-else-if="c.acao_monitorar" class="rounded bg-blue-100 px-1.5 py-0.5 text-[10.5px] font-semibold text-blue-800 dark:bg-blue-950/50 dark:text-blue-300">monitorar</span>
                    <span v-else class="text-muted-foreground">—</span>
                  </td>
                  <td v-if="ehProprio" class="truncate px-1.5 py-1.5 text-center text-[11px]" :title="c.suspensao_detalhe || ''">
                    <span
                      v-if="suspensaoResumo(c)"
                      :class="c.suspensao_status === 'solicitada' ? 'text-emerald-700 dark:text-emerald-400' : c.suspensao_status === 'falhou' ? 'text-rose-700 dark:text-rose-400' : 'text-muted-foreground'"
                    >{{ suspensaoResumo(c) }}</span>
                    <span v-else class="text-muted-foreground">—</span>
                  </td>
                </tr>
                <tr v-if="!loading && filteredRows.length === 0">
                  <td :colspan="ehProprio ? 13 : 10" class="px-3 py-6 text-center text-muted-foreground">
                    {{ rows.length === 0 ? 'nenhum caso' : 'nenhum caso com esses filtros' }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <div class="mt-auto border-t px-3 py-1.5 text-center text-[11px] text-muted-foreground">
            <template v-if="tab === 'flex'">Pedidos Flex saem de {{ flexLocal.cidade }} (estoque .{{ flexLocal.lote }}) e também aparecem nas abas Mercado Livre e Shopee, com o selo Flex. </template>
            Clique na linha abre o Raio-X do pedido à direita (rastreio, SKU, histórico do status, chamado e as ações). Vermelho = combinação sem regra na aba Status.
          </div>
        </div>

        <div class="flex min-w-0 flex-col gap-3">
          <!-- Raio-X da linha clicada -->
          <div v-if="raioRow" class="flex min-w-0 flex-1 flex-col rounded-md border bg-card">
            <div class="relative flex items-center justify-center rounded-t-md bg-[#1f3a5f] px-3 py-1.5 text-xs font-semibold text-white">
              <span>Raio-X · {{ raioRow.pedido_bling || raioRow.pedido_marketplace || 'pedido' }}</span>
              <button type="button" class="absolute right-2 rounded p-0.5 text-slate-300 hover:text-white" title="fechar o Raio-X" @click="raioId = null">
                <X class="size-3.5" />
              </button>
            </div>
            <div class="flex-1 space-y-3 p-3 text-xs">
              <div>
                <div class="flex flex-wrap items-center gap-1.5">
                  <b class="text-[13px]">{{ raioRow.conta || '—' }}</b>
                  <span class="text-muted-foreground">· {{ raioRow.plataforma || '—' }} · {{ fmtDate(raioRow.data) }}</span>
                  <span
                    v-if="raioRow.envio_flex && podeVerFlex"
                    class="rounded border border-violet-300 bg-violet-50 px-1 text-[10px] font-semibold text-violet-800 dark:border-violet-700 dark:bg-violet-950/40 dark:text-violet-300"
                    :title="flexSeloTitulo(raioRow)"
                  >Flex</span>
                </div>
                <div class="mt-0.5 text-muted-foreground">Pedido na plataforma: <span class="select-all font-mono text-foreground">{{ raioRow.pedido_marketplace || '—' }}</span></div>
                <div v-if="raioRow.cliente_nome" class="text-muted-foreground">Cliente: <span class="text-foreground">{{ raioRow.cliente_nome }}</span></div>
                <div v-if="flexAlerta(raioRow)" class="mt-0.5 font-medium text-rose-700 dark:text-rose-400">Flex sem peça em {{ flexLocal.cidade }}: {{ flexAlerta(raioRow) }}</div>
              </div>

              <section>
                <h3 class="mb-0.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Produtos</h3>
                <div v-for="(p, pi) in raioRow.produtos || []" :key="pi" class="flex gap-2">
                  <span class="min-w-0 flex-1">{{ p.nome || '—' }}<span v-if="p.quantidade && p.quantidade > 1"> ×{{ p.quantidade }}</span></span>
                  <span class="shrink-0 font-mono text-muted-foreground">{{ p.sku || '—' }}</span>
                </div>
                <div v-if="!(raioRow.produtos || []).length" class="text-muted-foreground">—</div>
              </section>

              <section>
                <h3 class="mb-0.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Status na plataforma</h3>
                <template v-if="statusLinhas(raioRow).length">
                  <div v-for="(l, li) in statusLinhas(raioRow)" :key="li" class="flex gap-2 leading-snug">
                    <span
                      class="w-[118px] shrink-0 whitespace-nowrap tabular-nums text-muted-foreground"
                      :title="l.aprox ? 'Data aproximada: a plataforma não informa a hora exata deste campo' : undefined"
                    ><span class="inline-block w-2">{{ l.aprox ? '~' : '' }}</span>{{ l.quando ? l.quando.replace(',', '') : '—' }}</span>
                    <span class="min-w-0"><span class="text-muted-foreground">{{ l.rotulo }}:</span> {{ l.valor }}</span>
                  </div>
                  <div v-if="statusLinhas(raioRow).some((l) => l.aprox)" class="mt-0.5 text-[10.5px] text-muted-foreground">~ data aproximada: a plataforma não informa a hora exata deste campo.</div>
                </template>
                <div v-else class="whitespace-pre-line">{{ statusTooltip(raioRow) || assinatura(raioRow) || '—' }}</div>
                <div v-if="assinatura(raioRow)" class="mt-1 break-words text-[11px] text-muted-foreground">Chave: {{ assinatura(raioRow) }}</div>
                <div v-if="raioRow.status_lido_em" class="text-[11px] text-muted-foreground" :title="'Última consulta ao marketplace: ' + fmtDataHora(raioRow.status_lido_em)">lido {{ fmtDesde(raioRow.status_lido_em) }}</div>
                <div class="mt-1 flex flex-wrap gap-1.5">
                  <button v-if="assinatura(raioRow)" type="button" :class="RAIO_BTN" title="Copiar a chave (Status Plataforma) para colar na aba Status" @click="copiarChave(raioRow)">
                    <Copy class="size-3" /> Copiar chave
                  </button>
                  <button
                    v-if="podeAtualizarStatus(raioRow)"
                    type="button"
                    :class="RAIO_BTN"
                    title="Consulta agora o status na plataforma"
                    :disabled="refreshingMeli.has(raioRow.id)"
                    @click="atualizarStatusPlataforma(raioRow)"
                  >
                    <RefreshCw class="size-3" :class="refreshingMeli.has(raioRow.id) ? 'animate-spin' : ''" /> Atualizar status
                  </button>
                </div>
              </section>

              <section>
                <h3 class="mb-0.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Envio</h3>
                <div
                  v-if="raioRow.volta_localizacao || raioRow.volta_rastreio"
                  :class="raioRow.volta_grave ? 'font-semibold text-rose-700 dark:text-rose-400' : ''"
                  :title="voltaTitulo(raioRow)"
                >↩ Devolução: {{ voltaTexto(raioRow) }}</div>
                <div><span class="text-muted-foreground">{{ raioRow.volta_rastreio || raioRow.volta_localizacao ? 'Rastreio (ida)' : 'Rastreio' }}:</span> <span class="select-all font-mono">{{ raioRow.rastreio || '—' }}</span></div>
                <div v-if="!raioRow.volta_localizacao || idaSeparada(raioRow)">
                  <span class="text-muted-foreground">{{ proprioSemRastreio(raioRow) ? 'Destino' : raioRow.volta_localizacao ? 'Localização (ida)' : 'Localização' }}:</span>
                  {{ raioRow.localizacao || '—' }}
                </div>
                <div
                  v-if="ehCorreios(raioRow.rastreio)"
                  class="text-[11px]"
                  :class="leituraVelha(raioRow) ? 'text-amber-700 dark:text-amber-400' : 'text-muted-foreground'"
                >{{ raioRow.localizacao_at ? `Correios · lido ${fmtDesde(raioRow.rastreio_lido_em || raioRow.localizacao_at)} · último movimento ${fmtDataHora(raioRow.localizacao_at)}` : 'sem leitura dos Correios ainda' }}</div>
                <div v-if="proprioSemRastreio(raioRow)" class="text-[11px] text-muted-foreground" :title="DESTINO_TITLE">{{ blingLidoResumo(raioRow) }}</div>
                <template v-if="isAmazon(raioRow) && raioRow.amazon_canal === 'proprio'">
                  <div>
                    <span class="text-muted-foreground">Previsão da transportadora:&nbsp;</span>
                    <span v-if="raioRow.entregue_em" class="text-emerald-700 dark:text-emerald-400">entregue {{ fmtQuando(raioRow.entregue_em) }}</span>
                    <span v-else :class="previsaoVencida(raioRow) ? 'font-semibold text-rose-700 dark:text-rose-400' : ''">{{ fmtDia(raioRow.previsao_correios) || '—' }}</span>
                  </div>
                  <div>
                    <span class="text-muted-foreground">Entregar até (Amazon):&nbsp;</span>
                    <span :class="prazoVencido(raioRow) ? 'font-semibold text-rose-700 dark:text-rose-400' : ''">{{ fmtDia(raioRow.prazo_entrega_amazon) || '—' }}</span>
                    <span v-if="raioRow.prazo_entrega_amazon && !raioRow.entregue_em" class="text-muted-foreground"> · {{ prazoResumo(raioRow) }}</span>
                  </div>
                  <div v-if="avisosResumo(raioRow)" class="text-[11px] text-muted-foreground">{{ avisosResumo(raioRow) }}</div>
                  <div v-if="(raioRow.mensagens_cliente || []).length" class="text-sky-700 dark:text-sky-400">
                    ✉ cliente:
                    <span v-for="(m, i) in raioRow.mensagens_cliente || []" :key="m.evento">
                      <span v-if="i" aria-hidden="true">· </span>{{ m.evento_label }}
                      <span class="whitespace-nowrap">{{ m.enviado_em ? fmtQuando(m.enviado_em) : '(falhou)' }}
                        <button
                          v-if="canEdit && canInformarAmazon"
                          type="button"
                          class="align-text-bottom px-0.5 text-sky-700/70 hover:text-sky-900 disabled:opacity-50 dark:text-sky-400/70 dark:hover:text-sky-200"
                          :title="`Enviar de novo ao comprador: ${m.evento_label}`"
                          :aria-label="`Enviar de novo ao comprador: ${m.evento_label}`"
                          :disabled="reenviandoMensagem.has(chaveMensagem(raioRow, m.evento))"
                          @click="reenviarMensagemCliente(raioRow, m)"
                        >
                          <RefreshCw class="inline size-3" :class="reenviandoMensagem.has(chaveMensagem(raioRow, m.evento)) ? 'animate-spin' : ''" />
                        </button>
                      </span>
                    </span>
                  </div>
                  <div
                    v-if="suspensaoResumo(raioRow)"
                    :class="raioRow.suspensao_status === 'solicitada' ? 'text-emerald-700 dark:text-emerald-400' : raioRow.suspensao_status === 'falhou' ? 'text-rose-700 dark:text-rose-400' : 'text-muted-foreground'"
                    :title="raioRow.suspensao_detalhe || ''"
                  >Suspensão: {{ suspensaoResumo(raioRow) }}</div>
                </template>
                <div class="mt-1 flex flex-wrap gap-1.5">
                  <button
                    v-if="canEdit && (ehCorreios(raioRow.rastreio) || (isAmazon(raioRow) && raioRow.amazon_canal === 'proprio'))"
                    type="button"
                    :class="RAIO_BTN"
                    :title="tituloAtualizarLocalizacao(raioRow)"
                    :disabled="refreshingRastreio.has(raioRow.id)"
                    @click="atualizarLocalizacao(raioRow)"
                  >
                    <RefreshCw class="size-3" :class="refreshingRastreio.has(raioRow.id) ? 'animate-spin' : ''" /> Atualizar localização
                  </button>
                  <button
                    v-if="podeSuspender(raioRow)"
                    type="button"
                    :class="[RAIO_BTN, 'border-rose-300 text-rose-700 hover:bg-rose-50 dark:border-rose-700 dark:text-rose-400 dark:hover:bg-rose-950/40']"
                    title="Pede ao robô que solicite no Melhor Envio a suspensão da entrega (o pacote volta). Irreversível; o frete não é devolvido."
                    :disabled="suspendendo.has(raioRow.id)"
                    @click="suspenderEntrega(raioRow)"
                  >
                    <Ban class="size-3" :class="suspendendo.has(raioRow.id) ? 'animate-pulse' : ''" />
                    {{ raioRow.suspensao_status === 'falhou' ? 'Suspender: tentar de novo' : 'Suspender entrega' }}
                  </button>
                </div>
              </section>

              <section>
                <h3 class="mb-0.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Bling</h3>
                <span class="inline-block rounded px-1.5 py-0.5 text-[11px] font-semibold" :class="corStatusBling(raioRow.status_bling)">{{ raioRow.status_bling || 'sem status' }}</span>
                <div v-if="raioRow.divergencia" class="mt-1 text-amber-700 dark:text-amber-400">Divergência: {{ raioRow.divergencia }}</div>
                <div class="mt-1">
                  <span class="text-muted-foreground">Regra da aba Status:&nbsp;</span>
                  <template v-if="precisaAtencao(raioRow)"><b class="text-amber-700 dark:text-amber-400">nenhuma</b> — combinação nova; decidir na aba Status.</template>
                  <template v-else-if="raioRow.acao_resumo.length">{{ raioRow.acao_resumo.join(' · ') }}</template>
                  <template v-else>—</template>
                  <span v-if="raioRow.acao_monitorar" class="ml-1 rounded bg-blue-100 px-1 text-[10.5px] font-semibold text-blue-800 dark:bg-blue-950/50 dark:text-blue-300">monitorar</span>
                </div>
                <div class="mt-1 flex flex-wrap gap-1.5">
                  <button
                    v-if="canEdit && raioRow.pedido_bling && temStatusBlingAcao(raioRow)"
                    type="button"
                    :class="RAIO_BTN"
                    title="Alterar a situação do pedido no Bling conforme a regra da aba Status"
                    :disabled="aplicandoStatus.has(raioRow.id)"
                    @click="aplicarStatusBling(raioRow)"
                  >
                    <ArrowLeftRight class="size-3" :class="aplicandoStatus.has(raioRow.id) ? 'animate-pulse' : ''" /> Aplicar status no Bling
                  </button>
                  <button
                    v-if="canEdit && raioRow.pedido_bling && raioRow.acao_resumo.includes('Mensagem Bling')"
                    type="button"
                    :class="RAIO_BTN"
                    title="Aplicar Mensagem Bling nas Observações do pedido"
                    :disabled="aplicandoBling.has(raioRow.id)"
                    @click="aplicarMensagemBling(raioRow)"
                  >
                    <NotebookPen class="size-3" :class="aplicandoBling.has(raioRow.id) ? 'animate-pulse' : ''" /> Mensagem Bling
                  </button>
                  <button
                    v-if="canEdit && raioRow.pedido_marketplace && raioRow.acao_resumo.includes('Mensagem Threema')"
                    type="button"
                    :class="RAIO_BTN"
                    title="Enviar aviso Threema deste pedido (com pedido e loja)"
                    :disabled="refreshingMeli.has(raioRow.id)"
                    @click="enviarThreemaPedido(raioRow)"
                  >
                    <MessageCircle class="size-3" :class="refreshingMeli.has(raioRow.id) ? 'animate-pulse' : ''" /> Avisar no Threema
                  </button>
                </div>
              </section>

              <section>
                <h3 class="mb-0.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Chamado</h3>
                <span
                  v-if="situacaoChamado(raioRow)"
                  class="inline-block rounded px-2 py-0.5 text-[11px] font-semibold"
                  :class="COR_SITUACAO_CHAMADO[situacaoChamado(raioRow)!]"
                >{{ situacaoChamado(raioRow) }}</span>
                <div v-if="raioRow.chamado || raioRow.chamado_aba?.chamado"><span class="text-muted-foreground">Protocolo:</span> <span class="select-all font-mono">{{ raioRow.chamado || raioRow.chamado_aba?.chamado }}</span></div>
                <div v-if="!situacaoChamado(raioRow)" class="text-muted-foreground">sem chamado</div>
                <div v-if="problemaSemChamado(raioRow)" class="mt-1 rounded border border-red-300 bg-red-50 px-2 py-1 font-semibold text-red-700 dark:border-red-800 dark:bg-red-950/40 dark:text-red-300">
                  Status Problemas e nenhum chamado aberto — ninguém está olhando este pedido.
                </div>
                <div
                  v-if="chamadoAutoAviso(raioRow)"
                  class="text-[11px]"
                  :class="raioRow.chamado_auto_erro === 'encaminhado_ao_robo' ? 'text-amber-700 dark:text-amber-400' : 'text-rose-700 dark:text-rose-400'"
                >{{ chamadoAutoAviso(raioRow) }}</div>
                <div class="mt-1 flex flex-wrap gap-1.5">
                  <a
                    v-if="raioRow.chamado_aba && raioRow.pedido_bling"
                    :href="`/chamados?search=${encodeURIComponent(raioRow.pedido_bling)}`"
                    target="_blank"
                    rel="noopener"
                    :class="RAIO_BTN"
                    title="Ver na aba Chamados (abre em nova guia)"
                  >
                    <ExternalLink class="size-3" /> Ver na aba Chamados
                  </a>
                  <button
                    v-if="canEdit && isMl(raioRow) && raioRow.pedido_marketplace && raioRow.acao_resumo.includes('Abrir chamado')"
                    type="button"
                    :class="RAIO_BTN"
                    title="Enviar chamado direto pro Mercado Livre"
                    :disabled="sendingChamado.has(raioRow.id)"
                    @click="enviarChamado(raioRow)"
                  >
                    <Send class="size-3" :class="sendingChamado.has(raioRow.id) ? 'animate-pulse' : ''" /> Enviar chamado ao ML
                  </button>
                </div>
              </section>

              <section v-if="raioRow.observacao">
                <h3 class="mb-0.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Observação</h3>
                <div class="whitespace-pre-line">{{ raioRow.observacao }}</div>
              </section>
            </div>
            <div class="flex items-center gap-1.5 border-t px-3 py-2">
              <button type="button" :class="RAIO_BTN" @click="openEdit(raioRow)">
                <NotebookPen class="size-3" /> {{ canEdit ? 'Editar caso' : 'Ver caso' }}
              </button>
              <button type="button" class="ml-auto text-[11px] text-muted-foreground underline" @click="raioId = null">fechar</button>
            </div>
          </div>

          <template v-else>
            <div class="flex min-w-0 flex-col rounded-md border bg-card">
              <div class="rounded-t-md bg-[#1f3a5f] px-3 py-1.5 text-center text-xs font-semibold text-white">Onde agir hoje</div>
              <div class="space-y-1.5 p-2">
                <div
                  v-for="a in agirItens"
                  :key="a.chave"
                  class="grid grid-cols-[12px_minmax(0,1fr)_58px] items-center gap-2 rounded-md border px-2.5 py-1.5 text-xs"
                  :class="a.grave ? 'border-red-300 bg-red-50 dark:border-red-800 dark:bg-red-950/40' : ''"
                >
                  <span class="size-[7px] rounded-full" :class="a.dot" />
                  <div class="min-w-0" :title="a.dica || undefined">
                    <div class="truncate font-bold" :class="a.grave ? 'text-red-700 dark:text-red-300' : ''" :title="a.dica ? undefined : a.titulo">{{ a.titulo }}</div>
                    <div class="truncate text-[11px] text-muted-foreground" :title="a.dica ? undefined : a.detalhe">{{ a.detalhe }}</div>
                  </div>
                  <button
                    type="button"
                    class="h-6 rounded-md border text-[11px] font-semibold hover:bg-muted"
                    :class="a.cartao && cartaoFiltro === a.cartao ? 'ring-2 ring-primary' : ''"
                    @click="agir(a)"
                  >{{ a.acao }}</button>
                </div>
                <div v-if="!agirItens.length" class="rounded-md border px-2.5 py-3 text-center text-xs text-muted-foreground">nada pra agir agora</div>
              </div>
            </div>
            <div class="flex min-w-0 flex-1 flex-col rounded-md border bg-card">
              <div class="rounded-t-md bg-[#1f3a5f] px-3 py-1.5 text-center text-xs font-semibold text-white">Raio-X</div>
              <div class="px-3 py-6 text-center text-xs text-muted-foreground">
                clique num pedido pra ver o Raio-X:<br />rastreio de ida e de volta, SKU, histórico do status, chamado e as ações
              </div>
            </div>
          </template>
        </div>
      </div>
      <!-- Mobile cards -->
      <div class="md:hidden space-y-2">
        <div
          v-for="c in pagedRows"
          :key="c.id"
          class="border rounded-md p-3 space-y-2 cursor-pointer"
          :class="precisaAtencao(c) ? 'border-red-300 bg-red-50 hover:bg-red-100 dark:bg-red-950/40' : 'hover:bg-muted/20'"
          @click="openEdit(c)"
        >
          <div class="flex items-start gap-2">
            <div class="flex-1 min-w-0">
              <div class="font-medium truncate">{{ c.pedido_bling || '—' }}</div>
              <div class="text-xs text-muted-foreground truncate">
                {{ c.plataforma || '—' }}
                <span
                  v-if="c.envio_flex && podeVerFlex && tab !== 'flex'"
                  class="text-[10px] px-1 py-px rounded border border-violet-300 bg-violet-50 font-semibold text-violet-800 dark:border-violet-700 dark:bg-violet-950/40 dark:text-violet-300"
                  :title="flexSeloTitulo(c)"
                >Flex</span>
                · {{ c.conta || '—' }}
              </div>
              <div v-if="flexAlerta(c)" class="text-[11px] font-medium text-rose-700 dark:text-rose-400">
                Flex sem peça em {{ flexLocal.cidade }}: {{ flexAlerta(c) }}
              </div>
              <div v-if="c.produtos && c.produtos.length" class="text-xs mt-0.5 space-y-0.5">
                <div v-for="(p, pi) in c.produtos" :key="pi" class="min-w-0">
                  <span class="block truncate" :title="p.nome || ''">
                    {{ p.nome || '—' }}<span v-if="p.quantidade && p.quantidade > 1"> ×{{ p.quantidade }}</span>
                  </span>
                  <span v-if="p.sku" class="block truncate text-[10px] font-mono text-muted-foreground">{{ p.sku }}</span>
                </div>
              </div>
            </div>
            <span v-if="c.status_bling" class="text-[10px] px-1.5 py-0.5 rounded border border-primary/50 shrink-0">{{ c.status_bling }}</span>
          </div>
          <div v-if="assinatura(c)" class="flex items-start gap-1.5 text-xs text-muted-foreground break-words">
            <span class="flex-1 break-words" :title="statusTooltip(c)">
              {{ assinatura(c) }}
              <span
                v-if="statusResumoData(c)"
                class="block text-[10px] text-muted-foreground/80 mt-0.5"
              >{{ statusResumoData(c) }}</span>
              <span
                v-if="c.status_lido_em"
                class="block text-[10px] text-muted-foreground/60 whitespace-nowrap"
              >lido {{ fmtDesde(c.status_lido_em) }}</span>
            </span>
            <button class="shrink-0 hover:text-foreground" title="Copiar chave" @click.stop="copiarChave(c)">
              <Copy class="size-3.5" />
            </button>
          </div>
          <div class="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
            <div><span class="text-muted-foreground">Data:</span> {{ fmtDate(c.data) }}</div>
            <div>
              <span class="text-muted-foreground">Rastreio:</span> {{ c.rastreio || '—' }}
              <span v-if="c.volta_rastreio" class="text-muted-foreground"> · volta: {{ c.volta_rastreio }}</span>
            </div>
            <div
              v-if="c.volta_localizacao"
              class="col-span-2"
              :class="c.volta_grave ? 'text-rose-700 dark:text-rose-400 font-medium' : ''"
            >
              <span class="text-muted-foreground">↩ Devolução:</span> {{ c.volta_localizacao }}
              <span v-if="c.volta_localizacao_em" class="text-muted-foreground"> · {{ fmtDataHora(c.volta_localizacao_em) }}</span>
            </div>
            <div v-if="!c.volta_localizacao || idaSeparada(c)">
              <span class="text-muted-foreground">{{ idaSeparada(c) ? 'Localização (ida):' : 'Localização:' }}</span>
              <template v-if="proprioSemRastreio(c) && c.localizacao">
                <span :title="DESTINO_TITLE">Destino: {{ c.localizacao }}</span>
              </template>
              <template v-else>{{ c.localizacao || '—' }}</template>
              <!-- Mesma informação de origem da tabela: no celular ela é ainda
                   mais necessária, porque não dá pra passar o mouse e ler o
                   balãozinho. -->
              <template v-if="ehCorreios(c.rastreio)">
                <span
                  v-if="c.localizacao_at"
                  :class="leituraVelha(c) ? 'text-amber-700 dark:text-amber-400' : 'text-muted-foreground'"
                >
                  · Correios, lido {{ fmtDesde(c.rastreio_lido_em || c.localizacao_at) }}
                </span>
                <span v-else class="text-muted-foreground">· sem leitura dos Correios ainda</span>
              </template>
              <button
                v-if="canEdit && (ehCorreios(c.rastreio) || (isAmazon(c) && c.amazon_canal === 'proprio'))"
                class="ml-1 inline-flex align-middle text-muted-foreground hover:text-foreground disabled:opacity-50"
                :title="tituloAtualizarLocalizacao(c)"
                :disabled="refreshingRastreio.has(c.id)"
                @click.stop="atualizarLocalizacao(c)"
              >
                <RefreshCw class="size-3.5" :class="refreshingRastreio.has(c.id) ? 'animate-spin' : ''" />
              </button>
              <div v-if="proprioSemRastreio(c)" class="text-[11px] text-muted-foreground" :title="DESTINO_TITLE">
                {{ blingLidoResumo(c) }}
              </div>
            </div>
            <div><span class="text-muted-foreground">Chamado:</span> {{ situacaoChamado(c) || '—' }}</div>
            <template v-if="tab === 'amazon' && amazonSub === 'proprio'">
              <div>
                <span class="text-muted-foreground">Previsão transportadora:</span>
                <span v-if="c.entregue_em" class="text-emerald-700 dark:text-emerald-400">entregue {{ fmtQuando(c.entregue_em) }}</span>
                <span v-else :class="previsaoVencida(c) ? 'text-rose-700 dark:text-rose-400 font-medium' : ''">{{ fmtDia(c.previsao_correios) || '—' }}</span>
              </div>
              <div>
                <span class="text-muted-foreground">Amazon até:</span>
                <span :class="prazoVencido(c) ? 'text-rose-700 dark:text-rose-400 font-medium' : ''">{{ fmtDia(c.prazo_entrega_amazon) || '—' }}</span>
                <span v-if="c.prazo_entrega_amazon && !c.entregue_em" class="text-muted-foreground"> · {{ prazoResumo(c) }}</span>
              </div>
              <div v-if="(c.mensagens_cliente || []).length" class="col-span-2 text-sky-700 dark:text-sky-400">
                ✉ cliente:
                <span v-for="(m, i) in c.mensagens_cliente || []" :key="m.evento">
                  <span v-if="i" aria-hidden="true">· </span>{{ m.evento_label }}
                  <span class="whitespace-nowrap">{{ m.enviado_em ? fmtQuando(m.enviado_em) : '(falhou)' }}
                    <button
                      v-if="canEdit && canInformarAmazon"
                      type="button"
                      class="align-text-bottom px-1 py-0.5 text-sky-700/70 hover:text-sky-900 dark:text-sky-400/70 dark:hover:text-sky-200 disabled:opacity-50"
                      :title="`Enviar de novo ao comprador: ${m.evento_label}`"
                      :aria-label="`Enviar de novo ao comprador: ${m.evento_label}`"
                      :disabled="reenviandoMensagem.has(chaveMensagem(c, m.evento))"
                      @click.stop="reenviarMensagemCliente(c, m)"
                    >
                      <RefreshCw class="size-3.5 inline" :class="reenviandoMensagem.has(chaveMensagem(c, m.evento)) ? 'animate-spin' : ''" />
                    </button>
                  </span>
                </span>
              </div>
              <div v-if="suspensaoResumo(c)" class="col-span-2" :class="c.suspensao_status === 'solicitada' ? 'text-emerald-700 dark:text-emerald-400' : c.suspensao_status === 'falhou' ? 'text-rose-700 dark:text-rose-400' : 'text-muted-foreground'">{{ suspensaoResumo(c) }}</div>
            </template>
          </div>
          <button
            v-if="tab === 'amazon' && amazonSub === 'proprio' && podeSuspender(c)"
            class="inline-flex items-center gap-1 text-xs px-2 py-1 rounded border border-rose-300 text-rose-700 hover:bg-rose-50 disabled:opacity-50 dark:border-rose-700 dark:text-rose-400"
            :disabled="suspendendo.has(c.id)"
            @click.stop="suspenderEntrega(c)"
          >
            <Ban class="size-3" :class="suspendendo.has(c.id) ? 'animate-pulse' : ''" />
            Suspender entrega
          </button>
          <div
            v-if="c.divergencia"
            class="text-xs text-amber-700 dark:text-amber-400 border border-amber-300 dark:border-amber-700 rounded px-2 py-1"
          >
            <span class="font-medium">Divergência:</span> {{ c.divergencia }}
          </div>
          <button
            v-if="canEdit && isMl(c) && c.pedido_marketplace && c.acao_resumo.includes('Abrir chamado')"
            class="inline-flex items-center gap-1 text-xs px-2 py-1 rounded border hover:bg-muted/40 disabled:opacity-50"
            :disabled="sendingChamado.has(c.id)"
            @click.stop="enviarChamado(c)"
          >
            <Send class="size-3" :class="sendingChamado.has(c.id) ? 'animate-pulse' : ''" />
            enviar chamado
          </button>
          <div v-if="chamadoAutoAviso(c)" class="text-[11px] text-rose-700 dark:text-rose-400">
            {{ chamadoAutoAviso(c) }}
          </div>
          <button
            v-if="canEdit && c.pedido_bling && temStatusBlingAcao(c)"
            class="inline-flex items-center gap-1 text-xs px-2 py-1 rounded border hover:bg-muted/40 disabled:opacity-50"
            :disabled="aplicandoStatus.has(c.id)"
            @click.stop="aplicarStatusBling(c)"
          >
            <ArrowLeftRight class="size-3" :class="aplicandoStatus.has(c.id) ? 'animate-pulse' : ''" />
            Status Bling
          </button>
          <button
            v-if="canEdit && c.pedido_bling && c.acao_resumo.includes('Mensagem Bling')"
            class="inline-flex items-center gap-1 text-xs px-2 py-1 rounded border hover:bg-muted/40 disabled:opacity-50"
            :disabled="aplicandoBling.has(c.id)"
            @click.stop="aplicarMensagemBling(c)"
          >
            <NotebookPen class="size-3" :class="aplicandoBling.has(c.id) ? 'animate-pulse' : ''" />
            Mensagem Bling
          </button>
          <button
            v-if="canEdit && c.pedido_marketplace && c.acao_resumo.includes('Mensagem Threema')"
            class="inline-flex items-center gap-1 text-xs px-2 py-1 rounded border hover:bg-muted/40 disabled:opacity-50"
            :disabled="refreshingMeli.has(c.id)"
            @click.stop="enviarThreemaPedido(c)"
          >
            <MessageCircle class="size-3" :class="refreshingMeli.has(c.id) ? 'animate-pulse' : ''" />
            Enviar Threema
          </button>
        </div>
        <div v-if="!loading && filteredRows.length === 0" class="text-center text-sm text-muted-foreground py-6 border rounded-md">
          {{ rows.length === 0 ? 'nenhum caso' : 'nenhum caso com esses filtros' }}
        </div>
      </div>

      <!-- Paginação -->
      <div v-if="filteredRows.length > PAGE_SIZE" class="flex items-center justify-between gap-3 pt-1">
        <span class="text-xs text-muted-foreground">
          {{ pageStart }}–{{ pageEnd }} de {{ filteredRows.length }}
        </span>
        <div class="flex items-center gap-1">
          <Button size="sm" variant="outline" :disabled="page <= 1" @click="goToPage(page - 1)">
            <ChevronLeft class="size-4" />
          </Button>
          <span class="text-xs text-muted-foreground px-2 whitespace-nowrap">
            página {{ page }} de {{ totalPages }}
          </span>
          <Button size="sm" variant="outline" :disabled="page >= totalPages" @click="goToPage(page + 1)">
            <ChevronRight class="size-4" />
          </Button>
        </div>
      </div>
    </template>

    <!-- ============ ABA STATUS ============ -->
    <!-- ============ ABA STATUS (12/11: padrão de Chamados/Conciliação, tela inteira —
         sem "Onde agir hoje" nem Raio-X) ============ -->
    <template v-else-if="tab === 'status'">
      <!-- filtros (mesmo formato das outras abas) -->
      <div class="flex flex-wrap items-center gap-1.5 text-xs">
        <label class="inline-flex h-7 items-center gap-1 rounded-md border bg-card px-2">
          <span class="font-semibold text-muted-foreground">Plataforma</span>
          <select v-model="statusFiltroPlataforma" class="bg-transparent outline-none">
            <option value="">Todas</option>
            <option v-for="p in statusPlataformas.nomes" :key="p" :value="p.toLowerCase()">{{ plataformaCanonica(p) || p }}</option>
            <option v-if="statusPlataformas.temGeral" value="__geral__">Geral (sem plataforma)</option>
          </select>
        </label>
        <label class="inline-flex h-7 min-w-0 items-center gap-1 rounded-md border bg-card px-2">
          <Search class="size-3.5 text-muted-foreground" />
          <input v-model="statusBusca" class="w-80 bg-transparent outline-none" placeholder="buscar status (cole o status copiado no pedido)…" />
        </label>
        <button
          v-if="statusFiltroPlataforma || statusBusca"
          type="button"
          class="text-muted-foreground underline"
          @click="statusFiltroPlataforma = ''; statusBusca = ''"
        >limpar</button>
        <span class="text-muted-foreground">{{ statusRowsFiltradas.length }} de {{ statusRows.length }} regras</span>
      </div>

      <!-- cartões: regras por plataforma; clicar filtra (clicar de novo tira o filtro) -->
      <div class="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
        <button
          v-for="k in statusCartoes"
          :key="k.k || 'todas'"
          type="button"
          class="rounded-md border bg-card px-3 py-1.5 text-center hover:bg-muted/40 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"
          :class="k.k && statusFiltroPlataforma === k.k ? 'ring-2 ring-primary' : ''"
          :title="!k.k ? 'mostrar todas' : statusFiltroPlataforma === k.k ? 'tirar o filtro' : `filtrar: ${k.label}`"
          @click="statusFiltroPlataforma = !k.k || statusFiltroPlataforma === k.k ? '' : k.k"
        >
          <span class="block text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">{{ k.label }}</span>
          <span class="block text-xl font-bold leading-tight">{{ k.n }}</span>
        </button>
      </div>

      <div v-if="statusError" class="text-sm text-red-500">erro: {{ statusError }}</div>

      <!-- Desktop: tabela inteira (edição inline por célula) -->
      <div class="hidden rounded-md border bg-card md:block">
        <div class="relative flex items-center justify-center rounded-t-md bg-[#1f3a5f] px-3 py-1.5 text-xs font-semibold text-white">
          <span>Regras da aba Status — o que o sistema faz em cada combinação</span>
          <span class="absolute right-3 hidden text-[11px] font-normal text-slate-300 xl:block">clique numa célula para editar</span>
        </div>
        <div class="overflow-x-auto">
        <table class="w-full min-w-[1100px] border-collapse text-[11.5px]">
          <thead class="bg-muted text-[10px] uppercase tracking-wide text-muted-foreground">
            <tr>
              <th class="border-b px-2 py-1 text-center font-semibold" colspan="4">Quando vale</th>
              <th class="border-b border-l border-l-slate-300 px-2 py-1 text-center font-semibold dark:border-l-slate-600" :colspan="canEdit ? 7 : 6">O que o sistema faz</th>
            </tr>
            <tr class="whitespace-nowrap text-left">
              <th class="border-b px-2 py-1 font-semibold">Plataforma</th>
              <th class="border-b px-2 py-1 font-semibold">Status Plataforma</th>
              <th class="border-b px-2 py-1 font-semibold">Status Atual (Bling)</th>
              <th class="border-b px-2 py-1 font-semibold" :title="LOCALIZACAO_CONTEM_TITLE">Localização contém</th>
              <th class="border-b border-l border-l-slate-300 px-2 py-1 font-semibold dark:border-l-slate-600">Alterar Status Bling</th>
              <th class="border-b px-2 py-1 font-semibold">Monitorar</th>
              <th class="border-b px-2 py-1 font-semibold">Abrir Chamado</th>
              <th class="border-b px-2 py-1 font-semibold">Mensagem do Chamado</th>
              <th class="border-b px-2 py-1 font-semibold">Mensagem Bling</th>
              <th class="border-b px-2 py-1 font-semibold">Mensagem Threema</th>
              <th v-if="canEdit" class="w-10 border-b px-2 py-1"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="s in statusRowsFiltradas" :key="s.id" class="border-b align-top hover:bg-muted/40">
              <!-- Plataforma (lista: as 4 plataformas ou geral) -->
              <td class="px-2 py-1 whitespace-nowrap align-top" @click="startEdit(s, 'plataforma')">
                <select
                  v-if="isEditing(s, 'plataforma')"
                  v-model="editValue"
                  autofocus
                  class="w-40 rounded border bg-background px-1.5 py-1 text-sm"
                  @change="commitEdit(s)"
                  @blur="commitEdit(s)"
                  @keydown.esc="cancelEdit"
                >
                  <option value="">Geral (todas)</option>
                  <option v-for="p in plataformaOpcoesPara(s.plataforma)" :key="p" :value="p">{{ p }}</option>
                </select>
                <span v-else :class="[canEdit ? 'cursor-pointer' : '', s.plataforma ? '' : 'text-muted-foreground']">{{ plataformaCanonica(s.plataforma) || 'Geral' }}</span>
              </td>
              <!-- Status Plataforma -->
              <td class="px-2 py-1 align-top" @click="startEdit(s, 'status_plataforma')">
                <input
                  v-if="isEditing(s, 'status_plataforma')"
                  v-model="editValue"
                  autofocus
                  class="w-64 rounded border bg-background px-1.5 py-1 text-sm"
                  @blur="commitEdit(s)"
                  @keydown.enter.prevent="commitEdit(s)"
                  @keydown.esc="cancelEdit"
                />
                <span v-else :class="[canEdit ? 'cursor-text' : '', s.status_plataforma ? 'font-medium' : 'text-muted-foreground']">{{ s.status_plataforma || '—' }}</span>
              </td>
              <!-- Status Atual (um ou mais status do Bling; a regra vale pra qualquer um) -->
              <td class="px-2 py-1 align-top">
                <StatusBlingMultiSelect
                  :model-value="statusAtuaisDe(s.status_atual)"
                  :opcoes="opcoes.status_bling_options"
                  :disabled="!canEdit || statusBusy.has(s.id)"
                  @save="(v) => patchStatusField(s.id, { status_atual: v })"
                />
              </td>
              <!-- Localização contém (condição opcional; palavras separadas por ";") -->
              <td class="px-2 py-1 align-top" :title="LOCALIZACAO_CONTEM_TITLE" @click="startEdit(s, 'localizacao_contem')">
                <input
                  v-if="isEditing(s, 'localizacao_contem')"
                  v-model="editValue"
                  autofocus
                  placeholder="ex.: apreendido; extraviado"
                  class="w-52 rounded border bg-background px-1.5 py-1 text-sm"
                  @blur="commitEdit(s)"
                  @keydown.enter.prevent="commitEdit(s)"
                  @keydown.esc="cancelEdit"
                />
                <div v-else-if="palavrasLocalizacao(s.localizacao_contem).length" class="flex flex-wrap gap-1" :class="canEdit ? 'cursor-text' : ''">
                  <span
                    v-for="w in palavrasLocalizacao(s.localizacao_contem)"
                    :key="w"
                    class="text-xs px-2 py-0.5 rounded border border-amber-400/70 bg-amber-50 text-amber-900 dark:border-amber-700 dark:bg-amber-950/40 dark:text-amber-200"
                  >{{ w }}</span>
                </div>
                <span v-else class="text-muted-foreground" :class="canEdit ? 'cursor-text' : ''">—</span>
              </td>
              <!-- Alterar Status Bling (dropdown com os status conhecidos do Bling) -->
              <td class="whitespace-nowrap border-l border-l-slate-300 px-2 py-1 align-top dark:border-l-slate-600" @click="startEdit(s, 'alterar_status_bling')">
                <select
                  v-if="isEditing(s, 'alterar_status_bling')"
                  v-model="editValue"
                  autofocus
                  class="w-44 rounded border bg-background px-1.5 py-1 text-sm"
                  @change="commitEdit(s)"
                  @blur="commitEdit(s)"
                  @keydown.esc="cancelEdit"
                >
                  <option value="">— não altera —</option>
                  <option v-for="opt in opcoes.status_bling_options" :key="opt" :value="opt">{{ opt }}</option>
                </select>
                <template v-else>
                  <span v-if="s.alterar_status_bling" class="text-xs px-2 py-0.5 rounded border border-primary/50" :class="canEdit ? 'cursor-pointer' : ''">{{ s.alterar_status_bling }}</span>
                  <span v-else class="text-muted-foreground" :class="canEdit ? 'cursor-pointer' : ''">—</span>
                </template>
              </td>
              <!-- Monitoramento (toggle direto) -->
              <td class="px-3 py-1 whitespace-nowrap align-top">
                <label class="inline-flex items-center gap-1.5" :class="canEdit ? 'cursor-pointer' : ''">
                  <input
                    type="checkbox"
                    :checked="s.monitoramento"
                    :disabled="!canEdit || statusBusy.has(s.id)"
                    class="size-4"
                    @change="toggleStatusBool(s, 'monitoramento')"
                  />
                  <span :class="s.monitoramento ? 'text-emerald-500' : 'text-muted-foreground'">{{ s.monitoramento ? 'Sim' : 'Não' }}</span>
                </label>
              </td>
              <!-- Abrir Chamado (toggle direto) -->
              <td class="px-3 py-1 whitespace-nowrap align-top">
                <label class="inline-flex items-center gap-1.5" :class="canEdit ? 'cursor-pointer' : ''">
                  <input
                    type="checkbox"
                    :checked="s.abrir_chamado"
                    :disabled="!canEdit || statusBusy.has(s.id)"
                    class="size-4"
                    @change="toggleStatusBool(s, 'abrir_chamado')"
                  />
                  <span :class="s.abrir_chamado ? 'text-emerald-500' : 'text-muted-foreground'">{{ s.abrir_chamado ? 'Sim' : 'Não' }}</span>
                </label>
              </td>
              <!-- Mensagem do Chamado (textarea inline) -->
              <td class="px-2 py-1 text-xs max-w-[340px] align-top" @click="startEdit(s, 'mensagem_chamado')">
                <textarea
                  v-if="isEditing(s, 'mensagem_chamado')"
                  v-model="editValue"
                  autofocus
                  rows="3"
                  placeholder="texto, link ou foto (cole a URL)"
                  class="w-80 rounded border bg-background px-1.5 py-1 text-sm resize-y"
                  @blur="commitEdit(s)"
                  @keydown.esc="cancelEdit"
                />
                <span v-else class="break-words whitespace-pre-line" :class="[canEdit ? 'cursor-text' : '', s.mensagem_chamado ? 'text-muted-foreground' : 'text-muted-foreground/60']">{{ s.mensagem_chamado || '—' }}</span>
                <!-- Anexos de imagem -->
                <div class="mt-1.5 flex flex-wrap items-center gap-1.5" @click.stop>
                  <div v-for="a in s.anexos || []" :key="a.id" class="relative group">
                    <a :href="anexoUrl(a.id)" target="_blank" rel="noopener" :title="a.filename">
                      <img :src="anexoUrl(a.id)" :alt="a.filename" class="size-10 rounded border object-cover" />
                    </a>
                    <button
                      v-if="canEdit"
                      class="absolute -right-1 -top-1 hidden rounded-full bg-red-500 p-0.5 text-white group-hover:block disabled:opacity-50"
                      title="Remover imagem"
                      :disabled="statusBusy.has(s.id)"
                      @click="removeAnexo(s, a.id)"
                    >
                      <X class="size-3" />
                    </button>
                  </div>
                  <label
                    v-if="canEdit"
                    class="inline-flex size-10 cursor-pointer items-center justify-center rounded border border-dashed text-muted-foreground hover:text-foreground"
                    :class="statusBusy.has(s.id) ? 'pointer-events-none opacity-50' : ''"
                    title="Anexar imagem"
                  >
                    <ImagePlus class="size-4" />
                    <input type="file" accept="image/*" class="hidden" :disabled="statusBusy.has(s.id)" @change="uploadAnexo(s, $event)" />
                  </label>
                </div>
              </td>
              <!-- Mensagem Bling (textarea inline) -->
              <td class="px-2 py-1 text-xs max-w-[300px] align-top" @click="startEdit(s, 'mensagem_bling')">
                <textarea
                  v-if="isEditing(s, 'mensagem_bling')"
                  v-model="editValue"
                  autofocus
                  rows="3"
                  placeholder="texto a colar no Bling"
                  class="w-72 rounded border bg-background px-1.5 py-1 text-sm resize-y"
                  @blur="commitEdit(s)"
                  @keydown.esc="cancelEdit"
                />
                <span v-else class="break-words whitespace-pre-line" :class="[canEdit ? 'cursor-text' : '', s.mensagem_bling ? 'text-muted-foreground' : 'text-muted-foreground/60']">{{ s.mensagem_bling || '—' }}</span>
              </td>
              <!-- Mensagem Threema (textarea inline) -->
              <td class="px-2 py-1 text-xs max-w-[300px] align-top" @click="startEdit(s, 'mensagem_threema')">
                <textarea
                  v-if="isEditing(s, 'mensagem_threema')"
                  v-model="editValue"
                  autofocus
                  rows="3"
                  placeholder="mensagem p/ notificar as pessoas"
                  class="w-72 rounded border bg-background px-1.5 py-1 text-sm resize-y"
                  @blur="commitEdit(s)"
                  @keydown.esc="cancelEdit"
                />
                <span v-else class="break-words whitespace-pre-line" :class="[canEdit ? 'cursor-text' : '', s.mensagem_threema ? 'text-muted-foreground' : 'text-muted-foreground/60']">{{ s.mensagem_threema || '—' }}</span>
                <div v-if="canEdit && s.mensagem_threema" class="mt-1 flex items-center gap-1">
                  <button
                    class="inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] text-primary hover:bg-primary/10 disabled:opacity-50"
                    title="Enviar Mensagem Threema aos destinatários"
                    :disabled="statusBusy.has(s.id)"
                    @click.stop="enviarThreema(s)"
                  >
                    <Send class="size-3" /> Enviar
                  </button>
                  <button
                    class="inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] text-muted-foreground hover:bg-muted disabled:opacity-50"
                    :title="s.threema_recipients ? `Destinatários salvos: ${s.threema_recipients}` : 'Escolher quem recebe (salvar na regra)'"
                    :disabled="statusBusy.has(s.id)"
                    @click.stop="escolherDestinatarios(s)"
                  >
                    <UserRound class="size-3" />
                  </button>
                </div>
              </td>
              <!-- Ação -->
              <td v-if="canEdit" class="px-2 py-1 align-top text-center">
                <button
                  class="text-muted-foreground hover:text-red-500 disabled:opacity-50"
                  title="Remover"
                  :disabled="statusBusy.has(s.id)"
                  @click="removeStatusRow(s)"
                >
                  <Trash2 class="size-4" />
                </button>
              </td>
            </tr>
            <tr v-if="!statusLoading && statusRowsFiltradas.length === 0">
              <td :colspan="canEdit ? 11 : 10" class="px-3 py-6 text-center text-muted-foreground">
                {{ statusRows.length ? 'nenhum status bate com o filtro' : 'nenhum status' }}
              </td>
            </tr>
          </tbody>
        </table>
        </div>
        <div class="border-t px-3 py-1.5 text-center text-[11px] text-muted-foreground">
          Clique numa célula para editar só aquele campo. Na Mensagem do Chamado dá para anexar imagem (botão de foto) ou colar uma URL.
          "Localização contém" é opcional — passe o mouse no título da coluna para ver como funciona.
        </div>
      </div>

      <!-- Mobile cards (campos editáveis diretos) -->
      <div class="md:hidden space-y-2">
        <div v-for="s in statusRowsFiltradas" :key="s.id" class="border rounded-md p-3 space-y-2">
          <div class="flex items-start gap-2">
            <div class="flex-1 min-w-0 text-xs text-muted-foreground">Status</div>
            <button
              v-if="canEdit"
              class="text-muted-foreground hover:text-red-500 disabled:opacity-50 shrink-0"
              :disabled="statusBusy.has(s.id)"
              @click="removeStatusRow(s)"
            >
              <Trash2 class="size-4" />
            </button>
          </div>
          <div>
            <label class="text-xs text-muted-foreground">Plataforma</label>
            <select
              :value="plataformaCanonica(s.plataforma)"
              :disabled="!canEdit || statusBusy.has(s.id)"
              class="w-full rounded border bg-background px-2 py-1 text-sm"
              @change="patchStatusField(s.id, { plataforma: ($event.target as HTMLSelectElement).value || null })"
            >
              <option value="">Geral (todas)</option>
              <option v-for="p in plataformaOpcoesPara(s.plataforma)" :key="p" :value="p">{{ p }}</option>
            </select>
          </div>
          <div>
            <label class="text-xs text-muted-foreground">Status Plataforma</label>
            <input
              :value="s.status_plataforma || ''"
              :disabled="!canEdit || statusBusy.has(s.id)"
              class="w-full rounded border bg-background px-2 py-1 text-sm"
              @change="patchStatusField(s.id, { status_plataforma: ($event.target as HTMLInputElement).value.trim() || null })"
            />
          </div>
          <div>
            <label class="text-xs text-muted-foreground">Status Atual</label>
            <StatusBlingMultiSelect
              :model-value="statusAtuaisDe(s.status_atual)"
              :opcoes="opcoes.status_bling_options"
              :disabled="!canEdit || statusBusy.has(s.id)"
              bloco
              @save="(v) => patchStatusField(s.id, { status_atual: v })"
            />
          </div>
          <div>
            <label class="text-xs text-muted-foreground" :title="LOCALIZACAO_CONTEM_TITLE">Localização contém</label>
            <input
              :value="s.localizacao_contem || ''"
              :disabled="!canEdit || statusBusy.has(s.id)"
              placeholder="ex.: apreendido; extraviado"
              class="w-full rounded border bg-background px-2 py-1 text-sm"
              @change="patchStatusField(s.id, { localizacao_contem: ($event.target as HTMLInputElement).value.trim() || null })"
            />
          </div>
          <div>
            <label class="text-xs text-muted-foreground">Alterar Status Bling</label>
            <select
              :value="s.alterar_status_bling || ''"
              :disabled="!canEdit || statusBusy.has(s.id)"
              class="w-full rounded border bg-background px-2 py-1 text-sm"
              @change="patchStatusField(s.id, { alterar_status_bling: ($event.target as HTMLSelectElement).value || null })"
            >
              <option value="">— não altera —</option>
              <option v-for="opt in opcoes.status_bling_options" :key="opt" :value="opt">{{ opt }}</option>
            </select>
          </div>
          <div class="flex flex-wrap items-center gap-4">
            <label class="flex items-center gap-2 text-sm">
              <input type="checkbox" :checked="s.monitoramento" :disabled="!canEdit || statusBusy.has(s.id)" class="size-4" @change="toggleStatusBool(s, 'monitoramento')" />
              Monitoramento
            </label>
            <label class="flex items-center gap-2 text-sm">
              <input type="checkbox" :checked="s.abrir_chamado" :disabled="!canEdit || statusBusy.has(s.id)" class="size-4" @change="toggleStatusBool(s, 'abrir_chamado')" />
              Abrir chamado
            </label>
          </div>
          <div>
            <label class="text-xs text-muted-foreground">Mensagem do Chamado</label>
            <textarea
              :value="s.mensagem_chamado || ''"
              :disabled="!canEdit || statusBusy.has(s.id)"
              rows="2"
              placeholder="texto, link ou foto (cole a URL)"
              class="w-full rounded border bg-background px-2 py-1 text-sm resize-y"
              @change="patchStatusField(s.id, { mensagem_chamado: ($event.target as HTMLTextAreaElement).value.trim() || null })"
            />
            <div class="mt-1.5 flex flex-wrap items-center gap-1.5">
              <div v-for="a in s.anexos || []" :key="a.id" class="relative">
                <a :href="anexoUrl(a.id)" target="_blank" rel="noopener" :title="a.filename">
                  <img :src="anexoUrl(a.id)" :alt="a.filename" class="size-12 rounded border object-cover" />
                </a>
                <button
                  v-if="canEdit"
                  class="absolute -right-1 -top-1 rounded-full bg-red-500 p-0.5 text-white disabled:opacity-50"
                  title="Remover imagem"
                  :disabled="statusBusy.has(s.id)"
                  @click="removeAnexo(s, a.id)"
                >
                  <X class="size-3" />
                </button>
              </div>
              <label
                v-if="canEdit"
                class="inline-flex size-12 cursor-pointer items-center justify-center rounded border border-dashed text-muted-foreground"
                :class="statusBusy.has(s.id) ? 'pointer-events-none opacity-50' : ''"
                title="Anexar imagem"
              >
                <ImagePlus class="size-5" />
                <input type="file" accept="image/*" class="hidden" :disabled="statusBusy.has(s.id)" @change="uploadAnexo(s, $event)" />
              </label>
            </div>
          </div>
          <div>
            <label class="text-xs text-muted-foreground">Mensagem Bling</label>
            <textarea
              :value="s.mensagem_bling || ''"
              :disabled="!canEdit || statusBusy.has(s.id)"
              rows="2"
              placeholder="texto a colar no Bling"
              class="w-full rounded border bg-background px-2 py-1 text-sm resize-y"
              @change="patchStatusField(s.id, { mensagem_bling: ($event.target as HTMLTextAreaElement).value.trim() || null })"
            />
          </div>
          <div>
            <label class="text-xs text-muted-foreground">Mensagem Threema</label>
            <textarea
              :value="s.mensagem_threema || ''"
              :disabled="!canEdit || statusBusy.has(s.id)"
              rows="2"
              placeholder="mensagem p/ notificar as pessoas"
              class="w-full rounded border bg-background px-2 py-1 text-sm resize-y"
              @change="patchStatusField(s.id, { mensagem_threema: ($event.target as HTMLTextAreaElement).value.trim() || null })"
            />
            <div v-if="canEdit && s.mensagem_threema" class="mt-1 flex items-center gap-1">
              <button
                class="inline-flex items-center gap-1 rounded border px-2 py-1 text-xs text-primary hover:bg-primary/10 disabled:opacity-50"
                :disabled="statusBusy.has(s.id)"
                @click="enviarThreema(s)"
              >
                <Send class="size-3.5" /> Enviar Threema
              </button>
              <button
                class="inline-flex items-center gap-1 rounded border px-2 py-1 text-xs text-muted-foreground hover:bg-muted disabled:opacity-50"
                :title="s.threema_recipients ? `Destinatários salvos: ${s.threema_recipients}` : 'Escolher quem recebe (salvar na regra)'"
                :disabled="statusBusy.has(s.id)"
                @click="escolherDestinatarios(s)"
              >
                <UserRound class="size-3.5" />
              </button>
            </div>
          </div>
        </div>
        <div v-if="!statusLoading && statusRowsFiltradas.length === 0" class="text-center text-sm text-muted-foreground py-6 border rounded-md">
          {{ statusRows.length ? 'nenhum status bate com o filtro' : 'nenhum status' }}
        </div>
      </div>
    </template>

    <!-- Modal "Novo status" (aba Status) -->
    <div v-if="showStatusForm" class="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" @click.self="showStatusForm = false">
      <div class="bg-background border rounded-lg w-full max-w-lg p-5 space-y-4 max-h-[90vh] overflow-y-auto">
        <div class="flex items-center">
          <h2 class="text-lg font-semibold">Novo status</h2>
          <Button class="ml-auto" size="sm" variant="ghost" @click="showStatusForm = false">
            <X class="size-4" />
          </Button>
        </div>

        <div>
          <Label>Plataforma</Label>
          <select
            v-model="statusForm.plataforma"
            class="w-full rounded-md border bg-background px-2 py-1.5 text-sm"
          >
            <option value="">Geral (todas as plataformas)</option>
            <option v-for="p in STATUS_PLATAFORMA_OPCOES" :key="p" :value="p">{{ p }}</option>
          </select>
        </div>
        <div>
          <Label>Status Plataforma</Label>
          <Input v-model="statusForm.status_plataforma" />
        </div>
        <div>
          <Label>Status Atual</Label>
          <StatusBlingMultiSelect
            v-model="statusForm.status_atual"
            :opcoes="opcoes.status_bling_options"
            bloco
          />
          <p class="mt-1 text-xs text-muted-foreground">
            Marque um ou mais: a regra vale pra qualquer um deles. Nenhum = qualquer status.
          </p>
        </div>
        <div>
          <Label>Localização contém</Label>
          <Input v-model="statusForm.localizacao_contem" placeholder="ex.: apreendido; extraviado" />
          <p class="mt-1 text-xs text-muted-foreground">
            Opcional. A regra só vale quando a Localização do pedido tem uma dessas palavras (separe por ";";
            maiúscula e acento não importam). Quando vale, ela passa na frente das regras sem condição deste
            Status Plataforma nos Status Atual que ela lista; nos outros, a regra sem condição continua valendo.
          </p>
        </div>
        <div>
          <Label>Alterar Status Bling</Label>
          <select
            v-model="statusForm.alterar_status_bling"
            class="w-full rounded-md border bg-background px-2 py-1.5 text-sm"
          >
            <option value="">— não altera —</option>
            <option v-for="opt in opcoes.status_bling_options" :key="opt" :value="opt">{{ opt }}</option>
          </select>
        </div>
        <div class="flex flex-wrap items-center gap-4">
          <label class="flex items-center gap-2 text-sm">
            <input v-model="statusForm.monitoramento" type="checkbox" class="size-4" />
            Monitoramento
          </label>
          <label class="flex items-center gap-2 text-sm">
            <input v-model="statusForm.abrir_chamado" type="checkbox" class="size-4" />
            Abrir chamado
          </label>
        </div>
        <div>
          <Label>Mensagem do Chamado</Label>
          <textarea
            v-model="statusForm.mensagem_chamado"
            rows="3"
            placeholder="texto ou link"
            class="w-full rounded-md border bg-background px-2 py-1.5 text-sm resize-y"
          />
          <p class="mt-1 text-xs text-muted-foreground">Depois de salvar, dá pra anexar imagens direto na linha.</p>
        </div>
        <div>
          <Label>Mensagem Bling</Label>
          <textarea
            v-model="statusForm.mensagem_bling"
            rows="3"
            placeholder="texto a colar no Bling"
            class="w-full rounded-md border bg-background px-2 py-1.5 text-sm resize-y"
          />
        </div>
        <div>
          <Label>Mensagem Threema</Label>
          <textarea
            v-model="statusForm.mensagem_threema"
            rows="3"
            placeholder="mensagem p/ notificar as pessoas"
            class="w-full rounded-md border bg-background px-2 py-1.5 text-sm resize-y"
          />
        </div>

        <div class="flex justify-end gap-2">
          <Button variant="ghost" @click="showStatusForm = false">Cancelar</Button>
          <Button :disabled="statusSaving" @click="saveStatusForm">
            {{ statusSaving ? 'Salvando…' : 'Salvar' }}
          </Button>
        </div>
      </div>
    </div>

    <!-- Modal: juntar linhas repetidas da aba Status (prévia + confirmar) -->
    <div v-if="juntarModal.open" class="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" @click.self="juntarModal.open = false">
      <div class="bg-background border rounded-lg w-full max-w-3xl p-5 space-y-4 max-h-[90vh] overflow-y-auto">
        <div class="flex items-center">
          <h2 class="text-lg font-semibold">Juntar regras repetidas</h2>
          <Button class="ml-auto" size="sm" variant="ghost" @click="juntarModal.open = false">
            <X class="size-4" />
          </Button>
        </div>
        <p class="text-sm text-muted-foreground">
          Só junta linhas com a mesma plataforma, o mesmo Status Plataforma, a mesma condição de Localização e exatamente as mesmas ações
          (Alterar Status Bling, Monitoramento, Abrir chamado, mensagens, destinatários e imagens). Cada grupo
          vira uma linha só com todos os Status Atual. Linha sem Status Atual nunca entra.
        </p>

        <div v-if="juntarModal.loading" class="text-sm text-muted-foreground">Procurando…</div>
        <div v-else-if="juntarModal.erro" class="text-sm text-red-500">erro: {{ juntarModal.erro }}</div>
        <template v-else>
          <div v-if="!juntarModal.grupos.length" class="text-sm">Nenhuma linha repetida pra juntar.</div>
          <div v-else class="space-y-2">
            <div class="text-sm font-medium">
              {{ juntarModal.grupos.length }} grupo(s) —
              {{ juntarModal.grupos.reduce((n, g) => n + g.linhas.length, 0) }} linhas viram
              {{ juntarModal.grupos.length }}
            </div>
            <div v-for="g in juntarModal.grupos" :key="g.manter_id" class="border rounded-md p-3 space-y-1.5">
              <div class="text-sm">
                <span class="text-muted-foreground">{{ plataformaCanonica(g.plataforma) || 'Geral' }} ·</span>
                <span class="font-medium">{{ g.status_plataforma }}</span>
                <span v-if="g.localizacao_contem" class="text-muted-foreground"> · Localização contém: {{ g.localizacao_contem }}</span>
              </div>
              <div class="flex flex-wrap items-center gap-1 text-xs">
                <template v-for="(l, i) in g.linhas" :key="l.id">
                  <span v-if="i" class="text-muted-foreground">+</span>
                  <span class="px-2 py-0.5 rounded border border-border">{{ l.status_atual }}</span>
                </template>
                <span class="text-muted-foreground px-1">→ uma linha:</span>
                <span v-for="st in statusAtuaisDe(g.status_atual_final)" :key="st" class="px-2 py-0.5 rounded border border-primary/60 font-medium">{{ st }}</span>
              </div>
              <div class="text-xs text-muted-foreground">
                Faz: {{ g.acoes.length ? g.acoes.join(' · ') : 'nada (regra sem ação)' }}
              </div>
            </div>
          </div>

          <div v-if="juntarModal.conflitos.length" class="space-y-2">
            <div class="text-sm font-medium text-amber-600">
              Não mexi em {{ juntarModal.conflitos.length }} caso(s): o mesmo Status Atual aparece em outra linha
              da mesma chave que faz outra coisa. Vale conferir à mão.
            </div>
            <div v-for="(c, ci) in juntarModal.conflitos" :key="ci" class="border border-amber-500/40 rounded-md p-3 space-y-1">
              <div class="text-sm">
                <span class="text-muted-foreground">{{ plataformaCanonica(c.plataforma) || 'Geral' }} ·</span>
                <span class="font-medium">{{ c.status_plataforma }}</span>
                <span v-if="c.localizacao_contem" class="text-muted-foreground"> · Localização contém: {{ c.localizacao_contem }}</span>
              </div>
              <div v-for="l in c.linhas" :key="l.id" class="text-xs">
                <span class="px-2 py-0.5 rounded border border-border">{{ l.status_atual }}</span>
                <span class="text-muted-foreground ml-1">{{ l.acoes.length ? l.acoes.join(' · ') : 'sem ação' }}</span>
              </div>
            </div>
          </div>
        </template>

        <div class="flex justify-end gap-2">
          <Button variant="ghost" @click="juntarModal.open = false">Cancelar</Button>
          <Button
            :disabled="juntarModal.loading || juntarModal.saving || !juntarModal.grupos.length"
            @click="confirmarJuntarRepetidas"
          >
            {{ juntarModal.saving ? 'Juntando…' : `Juntar ${juntarModal.grupos.length} grupo(s)` }}
          </Button>
        </div>
      </div>
    </div>

    <!-- Modal: escolher destinatários da Mensagem Threema -->
    <div v-if="threemaModal.open" class="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" @click.self="threemaModal.open = false">
      <div class="bg-background border rounded-lg w-full max-w-sm p-5 space-y-4">
        <div class="flex items-center">
          <h2 class="text-lg font-semibold">{{ threemaModal.mode === 'salvar' ? 'Destinatários da regra' : 'Enviar Threema' }}</h2>
          <Button class="ml-auto" size="sm" variant="ghost" @click="threemaModal.open = false">
            <X class="size-4" />
          </Button>
        </div>
        <p class="text-sm text-muted-foreground">{{ threemaModal.mode === 'salvar' ? 'Escolha quem recebe o aviso desta regra (usado no envio e no automático).' : 'Escolha quem recebe a mensagem.' }}</p>
        <div v-if="!threemaDestinatarios.length" class="text-sm text-muted-foreground">
          Nenhum destinatário configurado.
        </div>
        <div v-else class="space-y-2">
          <label
            v-for="d in threemaDestinatarios"
            :key="d.id"
            class="flex items-center gap-2 text-sm rounded-md border px-3 py-2 cursor-pointer hover:bg-muted/50"
          >
            <input
              type="checkbox"
              class="size-4"
              :checked="threemaModal.selecionados.has(d.id)"
              @change="toggleThreemaDest(d.id)"
            />
            {{ d.nome }}
          </label>
        </div>
        <div class="flex justify-end gap-2">
          <Button variant="ghost" @click="threemaModal.open = false">Cancelar</Button>
          <Button
            :disabled="threemaModal.sending || !threemaModal.selecionados.size"
            @click="confirmarThreemaModal"
          >
            <component :is="threemaModal.mode === 'salvar' ? UserRound : Send" class="size-4 mr-1" />
            {{ threemaModal.sending
              ? (threemaModal.mode === 'salvar' ? 'Salvando…' : 'Enviando…')
              : (threemaModal.mode === 'salvar' ? 'Salvar' : 'Enviar') }}
          </Button>
        </div>
      </div>
    </div>

    <!-- Modal create/edit caso -->
    <div v-if="modalOpen" class="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" @click.self="closeModal">
      <div class="bg-background border rounded-lg w-full max-w-2xl p-5 space-y-4 max-h-[90vh] overflow-y-auto">
        <div class="flex items-center">
          <h2 class="text-lg font-semibold">{{ editingId ? 'Editar caso' : 'Novo caso' }}</h2>
          <Button class="ml-auto" size="sm" variant="ghost" @click="closeModal">
            <X class="size-4" />
          </Button>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div>
            <Label>Data</Label>
            <Input v-model="form.data" type="date" :disabled="!canEdit" />
          </div>
          <div>
            <Label>Pedido Bling</Label>
            <Input v-model="form.pedido_bling" :disabled="!canEdit" />
          </div>
          <div>
            <Label>Pedido Marketplace</Label>
            <Input v-model="form.pedido_marketplace" :disabled="!canEdit" />
          </div>
          <div>
            <Label>Plataforma</Label>
            <Input v-model="form.plataforma" placeholder="ex: Mercado Livre" :disabled="!canEdit" />
          </div>
          <div>
            <Label>Conta</Label>
            <Input v-model="form.conta" :disabled="!canEdit" />
          </div>
          <div>
            <Label>{{ editingTemVolta ? 'Rastreio (ida)' : 'Rastreio' }}</Label>
            <Input v-model="form.rastreio" placeholder="número de rastreio" :disabled="!canEdit" />
          </div>
          <div>
            <Label>{{ rotuloLocalizacaoModal }}</Label>
            <Input v-model="form.localizacao" placeholder="ex: em trânsito / Correios" :disabled="!canEdit" />
          </div>
          <div>
            <Label>Chamado</Label>
            <Input v-model="form.chamado" placeholder="nº / referência do chamado" :disabled="!canEdit" />
          </div>
          <!-- Pacote da devolução: só leitura (lido da plataforma pelo robô das devoluções). -->
          <div
            v-if="editingTemVolta && editingRow"
            class="md:col-span-2 rounded-md border p-3 space-y-1"
            :class="editingRow.volta_grave ? 'border-rose-300 bg-rose-50 dark:border-rose-700 dark:bg-rose-950/40' : ''"
          >
            <div class="text-sm font-medium">↩ Devolução (pacote de volta)</div>
            <div
              class="text-sm"
              :class="editingRow.volta_grave ? 'text-rose-700 dark:text-rose-400 font-medium' : ''"
            >{{ editingRow.volta_localizacao || 'sem movimentação ainda' }}</div>
            <div class="text-xs text-muted-foreground">
              <span v-if="editingRow.volta_rastreio">Código da volta: {{ editingRow.volta_rastreio }}</span>
              <span v-if="editingRow.volta_localizacao_em">
                {{ editingRow.volta_rastreio ? ' · ' : '' }}último evento em {{ fmtDataHora(editingRow.volta_localizacao_em) }}</span>
            </div>
            <div v-if="editingRow.volta_grave" class="text-xs text-rose-700 dark:text-rose-400">
              Ocorrência grave: o produto pode estar perdido — veja a devolução na plataforma.
            </div>
          </div>
        </div>

        <!-- Status do Meli -->
        <div class="border rounded-md p-3 space-y-3">
          <div class="text-sm font-medium">Status do Meli</div>
          <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div v-for="f in opcoes.field_order" :key="f">
              <Label class="text-xs">{{ opcoes.field_labels[f] || f }}</Label>
              <select
                v-model="form.meli_status[f]"
                :disabled="!canEdit"
                class="w-full rounded-md border bg-background px-2 py-1.5 text-sm"
              >
                <option value="">—</option>
                <option v-for="opt in opcoes.field_options[f] || []" :key="opt" :value="opt">{{ opt }}</option>
              </select>
            </div>
          </div>
        </div>

        <!-- Sugestão -->
        <div class="border rounded-md p-3 space-y-2">
          <div class="text-sm font-medium">Sugestão de Status Bling</div>
          <div v-if="candidatos.length === 0" class="text-xs text-muted-foreground">
            preencha os status do Meli acima pra ver os candidatos
          </div>
          <div v-else class="flex flex-wrap gap-2">
            <button
              v-for="c in candidatos"
              :key="c.status_bling"
              type="button"
              :disabled="!canEdit"
              class="text-xs px-2 py-1 rounded border hover:bg-muted/40"
              :class="form.status_bling === c.status_bling ? 'border-primary bg-primary/10' : 'border-border'"
              @click="form.status_bling = c.status_bling"
            >
              {{ c.status_bling }} <span class="text-muted-foreground">({{ c.matches }})</span>
            </button>
          </div>
          <div>
            <Label class="text-xs">Status Bling (final)</Label>
            <Input v-model="form.status_bling" placeholder="clique num candidato ou digite" :disabled="!canEdit" />
          </div>
        </div>

        <div>
          <Label>Observação</Label>
          <textarea
            v-model="form.observacao"
            rows="2"
            :disabled="!canEdit"
            class="w-full rounded-md border bg-background px-2 py-1.5 text-sm resize-y"
          />
        </div>

        <div v-if="formErr" class="text-sm text-red-500">erro: {{ formErr }}</div>
        <div v-if="canEdit" class="flex justify-end gap-2">
          <Button v-if="editingId" variant="ghost" :disabled="saving" class="text-red-500 mr-auto" @click="remove">
            <Trash2 class="size-4 mr-1" /> remover
          </Button>
          <Button variant="ghost" :disabled="saving" @click="closeModal">cancelar</Button>
          <Button :disabled="saving" @click="save">
            {{ saving ? 'salvando…' : 'Salvar' }}
          </Button>
        </div>
      </div>
    </div>

    <!-- Aba Amazon: Informar (avisos de prazo do robô + lista sob demanda) -->
    <InformarThreemaModal
      :open="informarAmazonOpen"
      contexto="logistica_amazon"
      contexto-auto="logistica_amazon_auto"
      enviar-com-auto
      descricao="Quem está marcado recebe no Threema os avisos automáticos do Envio próprio da Amazon: previsão dos Correios vencida, 3 dias antes da data máxima da Amazon e data máxima vencida sem entrega. 'Enviar agora' manda a lista dos pedidos de Envio próprio em trânsito. A seleção fica salva."
      @close="informarAmazonOpen = false"
    />
    <MensagensClienteModal
      :open="mensagensClienteOpen"
      :pode-editar="canInformarAmazon"
      @close="mensagensClienteOpen = false"
    />

    <!-- Modal do botão INFORMAR (admin-only) -->
    <InformarThreemaModal
      :open="informarOpen"
      contexto="logistica"
      descricao="Manda via Threema a lista dos pedidos acompanhados no painel, no formato: pedido marketplace - conta - status plataforma - status bling. A seleção de destinatários fica salva."
      @close="informarOpen = false"
    />

  </div>
</template>
