<script lang="ts">
// Cartão da conversa de CARRINHO ABANDONADO (RF9, 02/10/2026) — no TOPO da
// conversa (prop `topo`, como o da reclamação; o site não tem pedido no
// Bling, e o painel da direita não abre nessa conversa).
//   GET /api/atendimento/conversas/{id}/carrinho (routers/atendimento_carrinhos.py)
//   → o carrinho aberto (ou o último) do lojista: itens (foto, produto, cor,
//   quantidade, preço) com o ESTOQUE ATUAL do DaVinci pelo SKU (lido na hora,
//   pela mesma regra do estoque do site: SKU exato ou a soma dos lotes de
//   venda de "base.*"; SKU que o DaVinci não tem = desconhecido, nunca zero),
//   o aviso de item sem estoque, o lojista (nome, empresa, e-mail, WhatsApp,
//   cidade), quando parou, a situação (parado / recuperado / não recuperado /
//   resolvido), os carrinhos anteriores, a taxa de recuperação do site e a
//   saúde da leitura do site (a rota ainda não publicada aparece aqui).
//   "Marcar como resolvido" (POST /carrinhos/{id}/resolvido) emite `mudou` —
//   a conversa é relida (a etiqueta sai de Carrinho e ela sai da fila).
// Nada é mandado ao lojista daqui (sem lembrete por Zap/e-mail por enquanto):
// o e-mail e o telefone são texto para copiar, não link de envio.
//
// Este bloco (não o setup) é o módulo dos ajudantes puros, testados em
// tests/atendimento-carrinho.cjs — por isso só importa TIPO.

export type LojistaCarrinho = {
  nome: string | null
  empresa: string | null
  email: string | null
  telefone: string | null
  cidade: string | null
  estado: string | null
  status: string | null
  cnpj: string | null
}
export type SkuEstoque = {
  sku: string
  existe: boolean
  saldo: number | null
  nome?: string | null
  lote?: string | null
  kit?: boolean
  atualizado_em?: string | null
}
// ok | acima | zero | desconhecido | sem_mapa (o `EstoqueItemOut` do backend).
export type EstoqueItemCarrinho = {
  disponivel: number | null
  demanda: number
  status: string
  texto: string
  outros_lotes: number
  falhou: boolean
  skus: SkuEstoque[]
}
export type ItemCarrinho = {
  produto_id: string | null
  titulo: string | null
  cor: string | null
  cor_rotulo: string | null
  quantidade: number
  skus: string[]
  url: string | null
  imagem: string | null
  preco: number | null
  estoque: EstoqueItemCarrinho | null
}
export type ItemEnviado = { produto_id: string | null; cor: string | null; quantidade: number; skus: string[] }
export type Carrinho = {
  id: string
  site: string
  site_nome: string
  site_url: string | null
  conversa_id: string | null
  situacao: string
  situacao_rotulo: string
  motivo_fim: string | null
  motivo_fim_rotulo: string | null
  lojista_id: string
  lojista: LojistaCarrinho
  itens: ItemCarrinho[]
  itens_total: number
  quantidade_total: number
  valor_total: number | null
  itens_sem_estoque: number
  parado_desde: string
  detectado_em: string | null
  visto_em: string | null
  fora_da_lista_desde: string | null
  // O lojista voltou a mexer no carrinho (o site o lista em `ativos`).
  mexido_em?: string | null
  prazo_em: string | null
  encerrado_em: string | null
  recuperado_em: string | null
  itens_enviados: ItemEnviado[]
  restantes: number | null
  tratado_em: string | null
  tratado_por_nome: string | null
  resolvido_motivo: string | null
  pode_resolver: boolean
}
export type CarrinhoResumo = {
  id: string
  situacao: string
  situacao_rotulo: string
  motivo_fim_rotulo: string | null
  quantidade_total: number
  itens_total: number
  valor_total: number | null
  parado_desde: string
  detectado_em: string | null
  encerrado_em: string | null
  recuperado_em: string | null
}
export type TaxaCarrinho = {
  dias: number
  detectados: number
  abertos: number
  recuperados: number
  nao_recuperados: number
  resolvidos: number
  encerrados: number
  taxa: number | null
}
export type LeituraSite = { status: string; ultimo_ok_em: string | null; ultimo_erro_em: string | null; ultimo_erro: string | null }
export type CarrinhoDaConversa = {
  carrinho: Carrinho | null
  anteriores: CarrinhoResumo[]
  taxa: TaxaCarrinho | null
  leitura: LeituraSite | null
  estoque_lido_em: string
  aviso: string
}

// A régua do backend (constantes.CARRINHO_DIAS_RECUPERACAO): a finalização
// depois disto não conta como recuperado. O teste confere com o backend.
export const DIAS_RECUPERACAO = 7
export const AVISO_SEM_ENVIO = 'Nada é mandado ao lojista daqui (sem lembrete por WhatsApp ou e-mail por enquanto).'

// A situação do episódio: o selo do cartão.
export const SITUACAO_CARRINHO: Record<string, { rotulo: string; dica: string; cls: string }> = {
  aberto: {
    rotulo: 'Carrinho parado',
    dica: 'o lojista deixou itens no carrinho e não finalizou pelo WhatsApp — fica com a etiqueta Carrinho até o desfecho',
    cls: 'bg-teal-500/15 text-teal-800 dark:text-teal-300',
  },
  recuperado: {
    rotulo: 'Recuperado',
    dica: 'o lojista finalizou o pedido pelo WhatsApp depois de o carrinho parar',
    cls: 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300',
  },
  nao_recuperado: {
    rotulo: 'Não recuperado',
    dica: `o lojista esvaziou o carrinho, ou passaram ${DIAS_RECUPERACAO} dias sem finalizar`,
    cls: 'bg-muted text-muted-foreground',
  },
  resolvido: {
    rotulo: 'Resolvido',
    dica: 'marcado como resolvido no DaVinci (nada foi mandado ao lojista)',
    cls: 'bg-sky-500/15 text-sky-700 dark:text-sky-300',
  },
}
export function situacaoCarrinho(situacao: string | null | undefined): { rotulo: string; dica: string; cls: string } {
  return SITUACAO_CARRINHO[situacao || ''] ?? { rotulo: situacao || '—', dica: '', cls: 'bg-muted text-muted-foreground' }
}

// O estoque do DaVinci de cada item (a cor do selo e o porquê).
export const ESTOQUE_CLS: Record<string, string> = {
  ok: 'bg-emerald-500/15 text-emerald-800 dark:text-emerald-300',
  acima: 'bg-amber-500/20 text-amber-800 dark:text-amber-300',
  zero: 'bg-red-500/15 text-red-700 dark:text-red-300',
  desconhecido: 'bg-muted text-muted-foreground',
  sem_mapa: 'bg-muted text-muted-foreground',
}
export function semEstoque(e: Pick<EstoqueItemCarrinho, 'status'> | null | undefined): boolean {
  return !!e && (e.status === 'zero' || e.status === 'acima')
}
// O selo: "5 em estoque", "Sem estoque", "2 disp." — o texto inteiro vai no title.
export function seloEstoque(e: EstoqueItemCarrinho | null | undefined): { texto: string; cls: string; titulo: string } {
  if (!e) return { texto: '—', cls: ESTOQUE_CLS.desconhecido, titulo: 'estoque não lido' }
  const skus = (e.skus || [])
    .map((s) => `${s.sku}: ${s.existe ? (s.saldo ?? '—') : 'fora do catálogo'}`)
    .join(' · ')
  const titulo = [e.texto, skus && `SKUs: ${skus}`, e.demanda > 1 ? `o carrinho pede ${e.demanda}` : ''].filter(Boolean).join('\n')
  const cls = ESTOQUE_CLS[e.status] || ESTOQUE_CLS.desconhecido
  if (e.status === 'ok') return { texto: `${e.disponivel} em estoque`, cls, titulo }
  if (e.status === 'acima') return { texto: `${e.disponivel} disp.`, cls, titulo }
  if (e.status === 'zero') return { texto: 'Sem estoque', cls, titulo }
  if (e.status === 'sem_mapa') return { texto: 'sem SKU', cls, titulo }
  return { texto: e.falhou ? 'não lido' : '?', cls, titulo }
}
// O aviso do topo (só no carrinho ainda aberto): quantos itens o DaVinci não
// tem para atender.
export function avisoEstoque(c: Pick<Carrinho, 'itens_sem_estoque' | 'situacao'> | null | undefined): string {
  const n = Number(c?.itens_sem_estoque) || 0
  if (!c || c.situacao !== 'aberto' || n <= 0) return ''
  return n === 1 ? '1 item sem estoque suficiente no DaVinci' : `${n} itens sem estoque suficiente no DaVinci`
}

// Telefone brasileiro como se lê: (11) 99999-8888; o resto, como veio.
export function fmtTelefone(t: string | null | undefined): string {
  const bruto = (t || '').trim()
  let d = bruto.replace(/\D/g, '')
  if (d.length > 11 && d.startsWith('55')) d = d.slice(2)
  if (d.length === 11) return `(${d.slice(0, 2)}) ${d.slice(2, 7)}-${d.slice(7)}`
  if (d.length === 10) return `(${d.slice(0, 2)}) ${d.slice(2, 6)}-${d.slice(6)}`
  return bruto
}
// O cadastro do lojista no site.
const STATUS_LOJISTA: Record<string, string> = { aprovado: 'aprovado', em_analise: 'em análise', recusado: 'recusado', pendente: 'pendente' }
export function statusLojista(s: string | null | undefined): string {
  const k = (s || '').trim().toLowerCase()
  return STATUS_LOJISTA[k] || k
}
export function cidadeUf(l: Pick<LojistaCarrinho, 'cidade' | 'estado'> | null | undefined): string {
  return [l?.cidade, l?.estado].filter((x) => !!x && String(x).trim()).join('/')
}

// "45 min", "3 h", "2 d 4 h" (o mesmo jeito da lista e das avaliações).
export function tempo(min: number): string {
  const m = Math.max(0, Math.round(min))
  if (m < 60) return `${m} min`
  const h = Math.floor(m / 60)
  if (h < 24) return m % 60 ? `${h} h ${m % 60} min` : `${h} h`
  const d = Math.floor(h / 24)
  return h % 24 ? `${d} d ${h % 24} h` : `${d} d`
}
// Enquanto aberto: até quando a finalização ainda conta como "recuperado".
export function prazoRecuperacao(
  c: Pick<Carrinho, 'situacao' | 'prazo_em'> | null | undefined,
  agora = Date.now(),
): { texto: string; cls: string } | null {
  if (!c || c.situacao !== 'aberto' || !c.prazo_em) return null
  const fim = new Date(c.prazo_em).getTime()
  if (Number.isNaN(fim)) return null
  const falta = fim - agora
  if (falta <= 0) return { texto: `${DIAS_RECUPERACAO} dias passaram: vira não recuperado na próxima leitura`, cls: 'text-muted-foreground' }
  const cls = falta < 24 * 3600 * 1000 ? 'text-amber-800 dark:text-amber-300' : 'text-muted-foreground'
  return { texto: `conta como recuperado se finalizar em até ${tempo(falta / 60000)}`, cls }
}

// A taxa do site nos últimos 30 dias, numa linha.
export function taxaTexto(t: TaxaCarrinho | null | undefined, site = ''): string {
  if (!t || !t.detectados) return ''
  const onde = site ? `${site} · ` : ''
  const abertos = t.abertos ? ` · ${t.abertos} aberto${t.abertos > 1 ? 's' : ''}` : ''
  if (!t.encerrados || t.taxa === null || t.taxa === undefined) {
    return `${onde}últimos ${t.dias} dias: ${t.detectados} carrinho${t.detectados > 1 ? 's' : ''} parado${t.detectados > 1 ? 's' : ''}, nenhum encerrado ainda`
  }
  const pct = Math.round(t.taxa * 100)
  return `${onde}últimos ${t.dias} dias: ${t.recuperados} de ${t.encerrados} recuperado${t.encerrados > 1 ? 's' : ''} (${pct}%)${abertos}`
}

// A leitura do site com problema (a rota não publicada, o token): o porquê
// aparece no cartão, além da aba Lojas. ok/novo = nada.
const LEITURA_PROBLEMA: Record<string, string> = {
  sem_endpoint: 'O site ainda não tem a rota de carrinhos publicada (falta publicar o pacote do carrinho na Hostinger)',
  sem_escopo: 'O site recusou o token do DaVinci',
  desligado: 'A leitura do site está desligada',
  erro: 'A última leitura do site falhou',
}
// Na rota não publicada a frase já diz o que fazer (o `ultimo_erro` inteiro
// fica no title); nos outros, o porquê técnico vai junto.
export function avisoLeitura(l: LeituraSite | null | undefined): string {
  if (!l || l.status === 'ok' || l.status === 'novo') return ''
  const base = LEITURA_PROBLEMA[l.status] || 'A leitura do site não está em dia'
  const erro = l.status === 'sem_endpoint' ? '' : (l.ultimo_erro || '').trim()
  return `${base}${erro ? `: ${erro}` : ''}. O que aparece aqui é da última leitura que deu certo.`
}

export function podeResolver(c: Pick<Carrinho, 'pode_resolver' | 'situacao'> | null | undefined, canEdit: boolean): boolean {
  return !!c && canEdit && c.pode_resolver && c.situacao === 'aberto'
}
export function perguntaResolver(c: Pick<Carrinho, 'quantidade_total' | 'site_nome'>): string {
  const pecas = Number(c.quantidade_total) || 0
  return `Marcar este carrinho como resolvido?\n\n${pecas} peça${pecas === 1 ? '' : 's'} no site ${c.site_nome}. A conversa sai da fila e a etiqueta sai de Carrinho.\nNada é mandado ao lojista.`
}

// Foto/link do site: só https (ou o localhost do ensaio) — nunca `javascript:`.
export function urlDoSite(v: unknown): string | null {
  if (typeof v !== 'string') return null
  const s = v.trim()
  if (/^https:\/\/[^\s"'<>]+$/i.test(s)) return s
  if (/^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?\/[^\s"'<>]*$/i.test(s)) return s
  return null
}
</script>

<script setup lang="ts">
import { ChevronDown, ChevronRight, CircleCheck, Copy, ExternalLink, ImageOff, Loader2, MapPin, PackageX, RotateCcw, ShoppingCart, Store, TriangleAlert, X } from 'lucide-vue-next'
import { erroDaApi, fmtDataHora, fmtDinheiro, haQuanto, useRelogio, type ConversaDetalhe } from '~/components/AtendimentoPlataforma.vue'

const props = defineProps<{
  conversa: ConversaDetalhe
  canEdit: boolean
  // No topo da conversa (largo e baixo): os itens em duas colunas na tela
  // larga, e o X recolhe o cartão.
  topo?: boolean
}>()
const emit = defineEmits<{
  (e: 'fechar'): void
  (e: 'mudou'): void
  (e: 'abrirImagem', i: { url: string; nome: string }): void
}>()
const { api } = useApi()
const toasts = useToasts()
const agora = useRelogio()

const dados = ref<CarrinhoDaConversa | null>(null)
const carregando = ref(false)
const erro = ref('')
const c = computed(() => dados.value?.carrinho ?? null)
const sit = computed(() => situacaoCarrinho(c.value?.situacao))
const prazo = computed(() => prazoRecuperacao(c.value, agora.value))
const aviso = computed(() => avisoEstoque(c.value))
const leitura = computed(() => avisoLeitura(dados.value?.leitura))
const taxa = computed(() => taxaTexto(dados.value?.taxa, c.value?.site_nome || ''))
const verAnteriores = ref(false)
const fotoQuebrada = reactive<Record<number, boolean>>({})

let pedidoAtual = 0
async function carregar() {
  const id = props.conversa?.id
  if (!id) return
  const meu = ++pedidoAtual
  carregando.value = true
  erro.value = ''
  try {
    const r = await api<CarrinhoDaConversa>(`/api/atendimento/conversas/${encodeURIComponent(id)}/carrinho`)
    if (meu !== pedidoAtual) return // trocou de conversa no meio
    dados.value = { ...r, anteriores: Array.isArray(r?.anteriores) ? r.anteriores : [] }
  } catch (e) {
    if (meu !== pedidoAtual) return
    erro.value = erroDaApi(e, 'Não consegui ler o carrinho agora.').texto
  } finally {
    if (meu === pedidoAtual) carregando.value = false
  }
}
onMounted(carregar)
// A leitura do site (de 30 em 30 min) fechou o carrinho, ou alguém mexeu na
// conversa: o cartão acompanha.
watch(() => [props.conversa?.ultima_mensagem_em, props.conversa?.etiqueta], (agoraV, antes) => {
  if (antes && (agoraV[0] !== antes[0] || agoraV[1] !== antes[1])) void carregar()
})

// ─── marcar como resolvido ──────────────────────────────────────────────────
const motivo = ref('')
const resolvendo = ref(false)
async function resolver() {
  const atual = c.value
  if (!atual || !podeResolver(atual, props.canEdit) || resolvendo.value) return
  if (!confirm(perguntaResolver(atual))) return
  resolvendo.value = true
  try {
    const r = await api<{ carrinho: Carrinho }>(`/api/atendimento/carrinhos/${encodeURIComponent(atual.id)}/resolvido`, {
      method: 'POST',
      body: { motivo: motivo.value.trim() || null },
    })
    if (dados.value && r?.carrinho) dados.value = { ...dados.value, carrinho: r.carrinho }
    motivo.value = ''
    toasts.success('Carrinho marcado como resolvido.')
    emit('mudou')
    void carregar()
  } catch (e) {
    const { texto } = erroDaApi(e, 'Não consegui marcar como resolvido.')
    toasts.error(texto)
    void carregar()
  } finally {
    resolvendo.value = false
  }
}

async function copiar(texto: string | null | undefined, oque: string) {
  if (!texto) return
  try {
    await navigator.clipboard.writeText(texto)
    toasts.success(`${oque} copiado.`)
  } catch {
    toasts.error('Não consegui copiar.')
  }
}

function foto(i: ItemCarrinho): string | null {
  return urlDoSite(i.imagem)
}
function abrirFoto(i: ItemCarrinho) {
  const url = foto(i)
  if (url) emit('abrirImagem', { url, nome: i.titulo || 'Produto' })
}
function quando(iso: string | null | undefined): string {
  if (!iso) return '—'
  return `${fmtDataHora(iso)} (${haQuanto(iso, agora.value)})`
}
</script>

<template>
  <div class="flex min-h-0 flex-1 flex-col" data-painel-carrinho>
    <div class="flex shrink-0 items-center gap-2 border-b px-3 py-2.5">
      <ShoppingCart class="size-4 text-teal-600 dark:text-teal-400" />
      <span class="text-sm font-medium">Carrinho</span>
      <span v-if="c" class="rounded px-1.5 py-0.5 text-[11px] font-medium" :class="sit.cls" :title="sit.dica">{{ sit.rotulo }}</span>
      <button type="button" class="ml-auto rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-50" :disabled="carregando" title="ler de novo (o estoque é lido na hora)" aria-label="ler de novo" @click="carregar">
        <RotateCcw class="size-3.5" :class="{ 'animate-spin': carregando }" />
      </button>
      <button type="button" class="rounded p-1 hover:bg-muted" :title="topo ? 'recolher o cartão' : 'esconder o painel'" :aria-label="topo ? 'recolher o cartão' : 'esconder o painel'" @click="emit('fechar')">
        <X class="size-4" />
      </button>
    </div>

    <div class="min-h-0 flex-1 space-y-3 overflow-y-auto px-3 py-3 text-sm">
      <div v-if="carregando && !dados" class="flex items-center gap-1.5 text-[11px] text-muted-foreground">
        <Loader2 class="size-3.5 animate-spin" /> lendo o carrinho e o estoque…
      </div>
      <div v-else-if="erro && !dados" class="flex items-center gap-1.5 rounded-md border border-dashed px-2.5 py-1.5 text-[11px] text-muted-foreground">
        <TriangleAlert class="size-3.5 shrink-0" />
        <span class="flex-1">{{ erro }}</span>
        <button type="button" class="underline hover:text-foreground" @click="carregar">tentar de novo</button>
      </div>

      <!-- a leitura do site com problema (a rota ainda não publicada, o token) -->
      <div v-if="leitura" class="flex gap-1.5 rounded-md border border-amber-500/40 bg-amber-500/10 px-2.5 py-1.5 text-[11px] text-amber-900 dark:text-amber-200" :title="dados?.leitura?.ultimo_erro || ''" data-carrinho-leitura>
        <TriangleAlert class="mt-px size-3.5 shrink-0" />
        <span>{{ leitura }}</span>
      </div>

      <div v-if="dados && !c" class="text-[12px] text-muted-foreground">
        Ainda não há carrinho parado ligado a esta conversa ({{ conversa.comprador_nome || 'lojista' }} no site {{ conversa.conta || '' }}).
      </div>

      <template v-if="c">
        <!-- quando parou e o desfecho -->
        <section class="space-y-1 text-[12px]" data-carrinho-situacao>
          <div class="flex items-center gap-1.5">
            <Store class="size-3.5 text-muted-foreground" />
            <span class="font-medium">Site {{ c.site_nome }}</span>
            <a v-if="urlDoSite(c.site_url)" :href="urlDoSite(c.site_url)!" target="_blank" rel="noopener noreferrer" class="ml-auto inline-flex items-center gap-0.5 text-[11px] text-muted-foreground hover:text-foreground hover:underline" title="abrir o site (outra aba)">abrir <ExternalLink class="size-3" /></a>
          </div>
          <div class="text-muted-foreground">Parado desde <span class="text-foreground">{{ quando(c.parado_desde) }}</span></div>
          <div v-if="c.detectado_em" class="text-[11px] text-muted-foreground">visto pelo DaVinci em {{ fmtDataHora(c.detectado_em) }}</div>
          <div v-if="prazo" class="text-[11px]" :class="prazo.cls" :title="`depois de ${DIAS_RECUPERACAO} dias sem finalizar, o carrinho conta como não recuperado`">{{ prazo.texto }}</div>
          <div v-if="c.mexido_em && c.situacao === 'aberto'" class="text-[11px] text-sky-800 dark:text-sky-300" title="o site diz que o lojista mexeu no carrinho depois de ele ter parado: não foi abandonado nem esvaziado. Continua aberto até ele finalizar pelo WhatsApp, esvaziar ou o prazo acabar." data-carrinho-mexido>
            O lojista voltou a mexer no carrinho em {{ fmtDataHora(c.mexido_em) }}.
          </div>
          <div v-else-if="c.fora_da_lista_desde && c.situacao === 'aberto'" class="text-[11px] text-muted-foreground" title="o carrinho não aparece mais como parado no site: o lojista voltou a mexer nele, ou ele passou de 30 dias. Continua aberto até ele finalizar, esvaziar ou o prazo acabar.">
            Não aparece mais como parado no site desde {{ fmtDataHora(c.fora_da_lista_desde) }}.
          </div>
          <div v-if="c.situacao === 'recuperado'" class="flex items-start gap-1 text-emerald-800 dark:text-emerald-300">
            <CircleCheck class="mt-px size-3.5 shrink-0" />
            <span>Virou pedido: finalizou pelo WhatsApp em {{ fmtDataHora(c.recuperado_em) }}<template v-if="c.itens_enviados.length"> ({{ c.itens_enviados.reduce((n, i) => n + (Number(i.quantidade) || 0), 0) }} peças na mensagem)</template><template v-if="c.restantes">; {{ c.restantes }} {{ c.restantes === 1 ? 'item ficou' : 'itens ficaram' }} no carrinho</template>.</span>
          </div>
          <div v-else-if="c.situacao !== 'aberto'" class="text-muted-foreground">
            {{ sit.rotulo }}<template v-if="c.motivo_fim_rotulo">: {{ c.motivo_fim_rotulo }}</template><template v-if="c.encerrado_em"> em {{ fmtDataHora(c.encerrado_em) }}</template><template v-if="c.tratado_por_nome"> por {{ c.tratado_por_nome }}</template>.
            <div v-if="c.resolvido_motivo" class="mt-0.5 text-[11px] italic">“{{ c.resolvido_motivo }}”</div>
          </div>
        </section>

        <!-- aviso de item sem estoque -->
        <div v-if="aviso" class="flex items-center gap-1.5 rounded-md border border-red-500/40 bg-red-500/10 px-2.5 py-1.5 text-[12px] font-medium text-red-700 dark:text-red-300" data-carrinho-sem-estoque>
          <PackageX class="size-4 shrink-0" />
          <span>{{ aviso }}</span>
        </div>

        <!-- itens com o estoque ATUAL do DaVinci -->
        <section class="space-y-1.5" data-carrinho-itens>
          <div class="flex items-center gap-1.5 text-[13px] font-semibold">
            Itens
            <span class="text-[11px] font-normal text-muted-foreground">· {{ c.quantidade_total }} peça{{ c.quantidade_total === 1 ? '' : 's' }}<template v-if="c.valor_total !== null"> · {{ fmtDinheiro(c.valor_total) }}</template></span>
            <span class="ml-auto text-[10px] font-normal text-muted-foreground" title="estoque do DaVinci (o disponível no Bling), lido agora — o mesmo número que o site mostra ao lojista logado">estoque lido agora</span>
          </div>
          <ul :class="topo ? 'grid gap-1.5 lg:grid-cols-2' : 'space-y-1.5'">
            <li v-for="(i, n) in c.itens" :key="`${i.produto_id}-${i.cor}-${n}`" class="flex gap-2 rounded-md border px-2 py-1.5 text-xs" :class="semEstoque(i.estoque) ? 'border-red-500/40' : ''">
              <button v-if="foto(i) && !fotoQuebrada[n]" type="button" class="size-12 shrink-0 overflow-hidden rounded border bg-muted" title="ver a foto" @click="abrirFoto(i)">
                <img :src="foto(i)!" :alt="i.titulo || 'produto'" loading="lazy" referrerpolicy="no-referrer" class="size-full object-cover" @error="fotoQuebrada[n] = true">
              </button>
              <div v-else class="flex size-12 shrink-0 items-center justify-center rounded border bg-muted text-muted-foreground"><ImageOff class="size-4" /></div>
              <div class="min-w-0 flex-1">
                <a v-if="urlDoSite(i.url)" :href="urlDoSite(i.url)!" target="_blank" rel="noopener noreferrer" class="line-clamp-2 text-[12px] hover:underline" :title="`${i.titulo || 'Produto'} — abrir no site (outra aba)`">{{ i.titulo || 'Produto' }}</a>
                <div v-else class="line-clamp-2 text-[12px]">{{ i.titulo || 'Produto' }}</div>
                <div class="mt-0.5 flex flex-wrap items-center gap-x-1.5 text-muted-foreground">
                  <span v-if="i.cor_rotulo || i.cor">{{ i.cor_rotulo || i.cor }}</span>
                  <span class="tabular-nums">× {{ i.quantidade }}</span>
                  <span v-if="i.preco !== null" class="tabular-nums">{{ fmtDinheiro(i.preco) }}</span>
                </div>
                <div v-if="i.skus.length" class="truncate font-mono text-[10px] text-muted-foreground" :title="i.skus.join(', ')">{{ i.skus.join(', ') }}</div>
                <div v-if="semEstoque(i.estoque) && i.estoque && i.estoque.outros_lotes > 0" class="mt-0.5 text-[11px] text-emerald-800 dark:text-emerald-300">Há {{ i.estoque.outros_lotes }} em outros lotes de venda.</div>
              </div>
              <div class="shrink-0 text-right">
                <span class="inline-block whitespace-nowrap rounded px-1.5 py-0.5 text-[11px] font-semibold tabular-nums" :class="seloEstoque(i.estoque).cls" :title="seloEstoque(i.estoque).titulo">{{ seloEstoque(i.estoque).texto }}</span>
              </div>
            </li>
          </ul>
          <div v-if="c.itens_total > c.itens.length" class="text-[11px] text-muted-foreground">e mais {{ c.itens_total - c.itens.length }} itens no carrinho.</div>
        </section>

        <!-- o lojista (texto para copiar: nada é mandado daqui) -->
        <section class="space-y-1 text-[12px]" data-carrinho-lojista>
          <div class="text-[13px] font-semibold">Lojista</div>
          <div class="font-medium">{{ c.lojista.empresa || c.lojista.nome || `Lojista ${c.lojista_id}` }}</div>
          <div v-if="c.lojista.empresa && c.lojista.nome" class="text-muted-foreground">{{ c.lojista.nome }}</div>
          <div v-if="c.lojista.status" class="text-[11px] text-muted-foreground">cadastro {{ statusLojista(c.lojista.status) }} no site</div>
          <div v-if="c.lojista.email" class="flex items-center gap-1">
            <span class="min-w-0 truncate select-all" :title="c.lojista.email">{{ c.lojista.email }}</span>
            <button type="button" class="shrink-0 rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground" title="copiar o e-mail" aria-label="copiar o e-mail" @click="copiar(c.lojista.email, 'E-mail')"><Copy class="size-3" /></button>
          </div>
          <div v-if="c.lojista.telefone" class="flex items-center gap-1">
            <span class="select-all tabular-nums" title="WhatsApp / telefone do cadastro">{{ fmtTelefone(c.lojista.telefone) }}</span>
            <button type="button" class="shrink-0 rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground" title="copiar o telefone" aria-label="copiar o telefone" @click="copiar(c.lojista.telefone, 'Telefone')"><Copy class="size-3" /></button>
          </div>
          <div v-if="cidadeUf(c.lojista)" class="flex items-center gap-1 text-muted-foreground"><MapPin class="size-3" /> {{ cidadeUf(c.lojista) }}</div>
          <div v-if="c.lojista.cnpj" class="text-muted-foreground">CNPJ <span class="select-all tabular-nums">{{ c.lojista.cnpj }}</span></div>
        </section>

        <!-- marcar como resolvido (sem lembrete: nada vai ao lojista) -->
        <section v-if="c.situacao === 'aberto'" class="space-y-1.5" data-carrinho-resolver>
          <template v-if="podeResolver(c, canEdit)">
            <textarea v-model="motivo" rows="2" maxlength="300" class="w-full resize-none rounded-md border bg-background px-2 py-1 text-[12px]" placeholder="O que foi feito (opcional, fica só no DaVinci)" />
            <button type="button" class="inline-flex w-full items-center justify-center gap-1.5 rounded-md border px-2.5 py-1.5 text-[12px] font-medium hover:bg-muted disabled:opacity-50" :disabled="resolvendo" @click="resolver">
              <Loader2 v-if="resolvendo" class="size-3.5 animate-spin" />
              <CircleCheck v-else class="size-3.5" />
              Marcar como resolvido
            </button>
          </template>
          <div v-else class="text-[11px] text-muted-foreground">Você pode ver, mas não marcar como resolvido: falta a permissão de editar o Atendimento.</div>
        </section>

        <!-- os carrinhos anteriores do mesmo lojista -->
        <section v-if="dados && dados.anteriores.length" class="space-y-1 text-[12px]">
          <button type="button" class="flex items-center gap-1 text-[12px] font-medium text-muted-foreground hover:text-foreground" @click="verAnteriores = !verAnteriores">
            <component :is="verAnteriores ? ChevronDown : ChevronRight" class="size-3.5" />
            Carrinhos anteriores ({{ dados.anteriores.length }})
          </button>
          <ul v-if="verAnteriores" class="space-y-1">
            <li v-for="a in dados.anteriores" :key="a.id" class="flex items-center gap-1.5 rounded border px-2 py-1 text-[11px]">
              <span class="rounded px-1 py-px font-medium" :class="situacaoCarrinho(a.situacao).cls" :title="a.motivo_fim_rotulo || ''">{{ a.situacao_rotulo }}</span>
              <span class="text-muted-foreground">parado {{ fmtDataHora(a.parado_desde) }}</span>
              <span class="ml-auto tabular-nums text-muted-foreground">{{ a.quantidade_total }} pç</span>
            </li>
          </ul>
        </section>

        <div v-if="taxa" class="text-[11px] text-muted-foreground" data-carrinho-taxa>{{ taxa }}</div>
      </template>

      <div class="border-t pt-2 text-[11px] text-muted-foreground">{{ dados?.aviso || AVISO_SEM_ENVIO }}</div>
    </div>
  </div>
</template>
