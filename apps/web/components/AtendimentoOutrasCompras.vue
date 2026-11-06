<script lang="ts">
import { numero, retratoDe, type PedidoMkt } from '~/components/AtendimentoPlataforma.vue'
import type { PerfilAdsPower } from '~/components/AtendimentoAdsPower.vue'

// Hora do último "atualizar" por cartão, fora do componente: a aba e o painel são
// remontados, e a trava de 60 s (a mesma do backend) não pode zerar por isso.
const ultimaAtualizacao = new Map<string, number>()
const TRAVA_MS = 60_000

// As outras compras do comprador (06/11/2026). GET .../pedidos-comprador: o índice e
// o Bling, na hora. `OutraCompraOut` (schemas/atendimento.py).
export type BlingDaCompra = {
  numero: string
  situacao_id: string | null
  situacao: string | null
  link: string | null
  // Os SKUs dos itens no espelho: é neles que a busca da lista casa.
  skus?: string[]
}
export type OutraCompra = {
  pedido: string
  status: string | null
  status_texto: string | null
  criado_em: string | null
  total: number | null
  itens_resumo: string | null
  bling: BlingDaCompra | null
  link_plataforma: { url: string; rotulo: string } | null
}
// O retrato da plataforma (o mesmo do pedido da conversa) e o motivo do cancelamento.
export type RetratoDaCompra = PedidoMkt & { cancelamento: string | null }

// Situação do Bling "Aguardando Cancelamento" (`bling_situacoes`).
const SITUACAO_AG_CANCELAMENTO = '83955'
export function emAgCancelamento(b: BlingDaCompra | null | undefined): boolean {
  return b?.situacao_id === SITUACAO_AG_CANCELAMENTO
}

// Só o que a tela usa de um retrato cru: vira null se não for de um pedido.
export function retratoDaCompraDe(bruto: unknown): RetratoDaCompra | null {
  const r = retratoDe(bruto)
  if (!r?.pedido) return null
  const motivo = (bruto as Record<string, unknown>).cancelamento
  return { ...r, cancelamento: typeof motivo === 'string' && motivo.trim() ? motivo.trim() : null }
}

export function outraCompraDe(bruto: OutraCompra): OutraCompra {
  return { ...bruto, total: numero(bruto.total) }
}

// ─── quais cartões aparecem (06/11/2026) ────────────────────────────────────
// O dono: "se tiver muitos pedidos talvez os 3 últimos e um carregar mais" e "só a
// questão da pesquisa do pedido que tem que cuidar nessa disposição nova". A lista vem
// inteira do banco: daqui sai só o que entra na tela (e o que dela pede retrato).
export const COMPRAS_NA_ABERTURA = 3
export const COMPRAS_POR_CLIQUE = 5
// O mesmo piso do pedaço de SKU na busca da lista (`MIN_BUSCA_SKU`).
const MIN_PEDACO_DE_SKU = 3

const normalizado = (v: unknown) => String(v ?? '').trim().toLowerCase()

// O termo é um destes números, inteiro? Em minúscula também vale: o order_sn é maiúsculo.
export function termoEhDoPedido(termo: string | null | undefined, numeros: unknown[]): boolean {
  const t = normalizado(termo)
  return !!t && numeros.some((n) => normalizado(n) === t)
}

// Um código de compra, e não um nome: com dígito e sem espaço (o nº do Bling tem 6).
export function pareceCodigoDeCompra(termo: string | null | undefined): boolean {
  const t = normalizado(termo)
  return /^[0-9a-z.-]{5,}$/.test(t) && /\d/.test(t)
}

// O nº na plataforma ou o do Bling, inteiros; ou um pedaço de SKU. O resumo dos itens fica
// de fora: é o nome do anúncio, sem SKU (`indice.pedido_shopee`; medido em 06/11/2026:
// nenhum dos 3.314 pedidos da Shopee com SKU no Bling, em 30 dias, o tinha no resumo), e a
// busca pelo nome do comprador destacaria a "Capa Rosa". O SKU vem da linha do Bling, que
// chega com a lista, e do retrato, o que o cartão mostra, quando ele já veio.
type RetratoComSku = { itens?: { sku?: string | null }[] | null }
export function compraPesquisada(
  compra: Pick<OutraCompra, 'pedido' | 'bling'>,
  termo: string | null | undefined,
  retrato?: RetratoComSku | null,
): boolean {
  const t = normalizado(termo)
  if (!t) return false
  if (termoEhDoPedido(t, [compra.pedido, compra.bling?.numero])) return true
  if (t.length < MIN_PEDACO_DE_SKU) return false
  const ondeProcurar = [...(compra.bling?.skus || []), ...(retrato?.itens || []).map((i) => i.sku)]
  return ondeProcurar.some((v) => normalizado(v).includes(t))
}

// `itens` na ordem do backend (a mais nova primeiro) e `limite` = até onde dela se mostra.
// No topo, as pesquisadas; depois, na ordem de data, as `limite` mais novas, as em
// Aguardando Cancelamento (o caso real: a 13ª de 16) e as `fixas`. `retratos` = os que já
// vieram: uma compra sem linha no Bling, fora da tela, não tem de onde casar pelo SKU.
export function disposicaoDasCompras<T extends OutraCompra>(
  itens: T[],
  limite: number,
  termo: string | null | undefined,
  fixas: { has(pedido: string): boolean } = new Set<string>(),
  retratos: Record<string, RetratoComSku> = {},
): { visiveis: T[]; pesquisadas: Set<string>; ocultas: number } {
  const noTopo = itens.filter((i) => compraPesquisada(i, termo, retratos[i.pedido]))
  const pesquisadas = new Set(noTopo.map((i) => i.pedido))
  const demais = itens.filter((i, n) => !pesquisadas.has(i.pedido)
    && (n < limite || emAgCancelamento(i.bling) || fixas.has(i.pedido)))
  const visiveis = [...noTopo, ...demais]
  return { visiveis, pesquisadas, ocultas: itens.length - visiveis.length }
}

// O limite depois do clique: até entrarem mais 5 que NÃO estavam na tela (uma em
// Aguardando Cancelamento, que já aparecia no meio do caminho, não conta como nova).
export function limiteDepoisDoClique(
  itens: OutraCompra[],
  limite: number,
  termo: string | null | undefined,
  fixas?: { has(pedido: string): boolean },
  retratos?: Record<string, RetratoComSku>,
): number {
  const naTela = new Set(disposicaoDasCompras(itens, limite, termo, fixas, retratos).visiveis.map((i) => i.pedido))
  let novo = limite
  let novas = 0
  while (novo < itens.length && novas < COMPRAS_POR_CLIQUE) {
    if (!naTela.has(itens[novo]!.pedido)) novas += 1
    novo += 1
  }
  return novo
}
</script>

<script setup lang="ts">
// "Outras compras (N)" na aba Pedido (06/11/2026, o dono com o print do Duoke: "uma
// pessoa cancelou 2 pedidos, no nosso aparece o código só do último e não o histórico
// inteiro"). O chat da Shopee e do TikTok é UM por comprador na loja, ligado ao último
// pedido: aqui ficam os outros dele, da mais nova para a mais antiga, sem o da conversa.
// - SÓ VER. Nenhuma ação: nada de trocar, oferecer, NF ou mudar o pedido da conversa.
// - Dois passos, nessa ordem: a lista (o índice e o Bling, na hora) e os retratos em
//   lote, à parte (foto, variação, valor pago, cancelamento, logística). Enquanto o
//   retrato não chega, ou se falhar, o cartão fica com o do índice, sem erro na tela.
//   Nada aqui segura o bloco de cima: é um componente à parte, com as próprias idas.
// - Os cartões (`disposicaoDasCompras`): as 3 mais novas, as em Aguardando Cancelamento
//   e, no topo e com destaque, as que casam com a busca da lista (`compraPesquisada`);
//   "Carregar mais" traz mais 5. Retrato só do que está na tela, e cada pedido uma vez: o
//   clique e a busca pedem só os que apareceram agora, sem reler a lista.
// - Quando busca: ao abrir a conversa ou a aba (a aba remonta) e quando o pedido da
//   conversa muda. O tique de 5 s da conversa não relê nada: o `watch` é só do nº.
// - O "atualizar" do cartão relê aquele pedido sem o cache: a mesma trava (e os mesmos
//   avisos) do botão do pedido da conversa. No TikTok não aparece: não há retrato.
import {
  CheckCircle2,
  Copy,
  ExternalLink,
  Package,
  RotateCcw,
  TriangleAlert,
  Truck,
} from 'lucide-vue-next'
import {
  copiar,
  erroDaApi,
  fmtDataHora,
  fmtDataPlataforma,
  fmtDinheiro,
  nivelLogistica,
  statusPedidoCls,
} from '~/components/AtendimentoPlataforma.vue'

const props = withDefaults(defineProps<{
  conversaId: string
  plataforma: string
  // O pedido da conversa: a lista não o traz, e relê quando ele muda.
  pedidoDaConversa?: string | null
  // As mesmas condições do "atualizar" do pedido da conversa (AtendimentoPedido).
  podeAtualizar?: boolean
  perfil?: PerfilAdsPower | null
  // O termo da busca da lista de conversas (`filtros.q` da página).
  termoBusca?: string | null
}>(), { pedidoDaConversa: null, podeAtualizar: false, perfil: null, termoBusca: null })
const emit = defineEmits<{
  // Quantas compras a lista tem: com alguma, o painel tira o "Conversa sem pedido ligado".
  (e: 'compras', total: number): void
}>()

const { api } = useApi()
const toasts = useToasts()

const itens = ref<OutraCompra[]>([])
const limite = ref(COMPRAS_NA_ABERTURA)
// A que saiu de Aguardando Cancelamento no "atualizar": não some na mão da pessoa.
const fixas = reactive(new Set<string>())
const retratos = ref<Record<string, RetratoDaCompra>>({})
const disposicao = computed(() => disposicaoDasCompras(itens.value, limite.value, props.termoBusca, fixas, retratos.value))
const base = computed(() => `/api/atendimento/conversas/${encodeURIComponent(props.conversaId)}/pedidos-comprador`)

function guardarRetratos(brutos: Record<string, unknown> | null | undefined) {
  const novos: Record<string, RetratoDaCompra> = {}
  for (const [pedido, bruto] of Object.entries(brutos || {})) {
    const r = retratoDaCompraDe(bruto)
    if (r) novos[pedido] = r
  }
  retratos.value = { ...retratos.value, ...novos }
}

// A resposta de uma leitura antiga (o pedido da conversa mudou no meio) não vale.
let rodada = 0
// Os pedidos que já foram pedidos nesta leitura: só os novos vão no POST.
const retratosPedidos = new Set<string>()
async function pedirRetratos() {
  const minha = rodada
  const novos = disposicao.value.visiveis.map((i) => i.pedido).filter((p) => !retratosPedidos.has(p))
  if (!novos.length) return
  for (const p of novos) retratosPedidos.add(p)
  try {
    const r = await api<{ retratos?: Record<string, unknown> }>(`${base.value}/retratos`, {
      method: 'POST',
      body: { pedidos: novos },
    })
    if (minha === rodada) guardarRetratos(r?.retratos)
  } catch {
    // Sem retrato o cartão fica com o do índice: sem erro na cara da pessoa.
  }
}

// A tela vai até a primeira pesquisada, e não a cada tique: só ao terminar a leitura e
// quando o termo muda. Nem o "Carregar mais" nem o "atualizar" mexem na rolagem.
const raiz = ref<HTMLElement | null>(null)
// A pessoa já mexeu (rolou, clicou, digitou) com o retrato ou a lista a caminho: a tela
// fica onde ela está. O termo novo volta a valer, é ela pedindo para ir até a compra.
let pessoaMexeu = false
const marcarQueMexeu = () => { pessoaMexeu = true }
const GESTOS = ['wheel', 'touchmove', 'pointerdown', 'keydown']
async function rolarAtePesquisada() {
  if (!disposicao.value.pesquisadas.size) return
  await nextTick()
  raiz.value?.querySelector('[data-compra-pesquisada]')?.scrollIntoView({ block: 'start', behavior: 'smooth' })
}

async function carregar() {
  const minha = ++rodada
  retratosPedidos.clear()
  try {
    const r = await api<{ itens?: OutraCompra[] }>(base.value)
    if (minha !== rodada) return
    itens.value = (r?.itens || []).map(outraCompraDe)
  } catch {
    // A lista é ajuda: sem ela, a seção some e o painel segue.
    if (minha === rodada) {
      itens.value = []
      emit('compras', 0)
    }
    return
  }
  emit('compras', itens.value.length)
  if (!pessoaMexeu) void rolarAtePesquisada()
  await pedirRetratos()
  // O painel do pedido, acima, costuma chegar nesse meio-tempo e empurrar o cartão.
  if (minha === rodada && !pessoaMexeu) void rolarAtePesquisada()
}
onMounted(() => {
  for (const g of GESTOS) window.addEventListener(g, marcarQueMexeu, { capture: true, passive: true })
  return carregar()
})
onBeforeUnmount(() => {
  for (const g of GESTOS) window.removeEventListener(g, marcarQueMexeu, { capture: true })
})
watch(() => props.pedidoDaConversa, carregar)
// O termo só muda quem aparece: a lista já está aqui, vão só os retratos do que entrou.
watch(() => props.termoBusca, () => {
  pessoaMexeu = false
  void pedirRetratos()
  void rolarAtePesquisada()
})

function carregarMais() {
  pessoaMexeu = true
  limite.value = limiteDepoisDoClique(itens.value, limite.value, props.termoBusca, fixas, retratos.value)
  void pedirRetratos()
}

// ─── os cartões ─────────────────────────────────────────────────────────────
const cartoes = computed(() => disposicao.value.visiveis.map((item) => {
  const r = retratos.value[item.pedido] ?? null
  const status = r?.status || item.status
  return {
    item,
    r,
    pesquisada: disposicao.value.pesquisadas.has(item.pedido),
    status,
    statusTexto: r?.status_texto || item.status_texto || status,
    criadoEm: r?.criado_em || item.criado_em,
    moeda: r?.moeda || 'BRL',
    produtos: r?.itens || [],
    log: r?.logistica || null,
    total: r?.total ?? item.total,
  }
}))
const LOG_ICONE = { entregue: CheckCircle2, caminho: Truck, problema: TriangleAlert, preparando: Package } as const
const LOG_COR = {
  entregue: 'text-emerald-600 dark:text-emerald-400',
  caminho: 'text-sky-600 dark:text-sky-400',
  problema: 'text-red-600 dark:text-red-400',
  preparando: 'text-orange-500 dark:text-orange-400',
} as const
const fotosFalhas = reactive(new Set<string>())

async function copiarTexto(t: string | null | undefined, rotulo: string) {
  if (!t) return
  if (await copiar(t)) toasts.success(`${rotulo} copiado`)
  else window.prompt(rotulo, t)
}

// ─── atualizar um cartão (POST, trava de 60 s) ──────────────────────────────
const atualizando = reactive(new Set<string>())
// Leitura desligada descoberta no clique: o botão some, como no pedido da conversa.
const recusada = ref(false)
const mostraAtualizar = computed(() => props.podeAtualizar && !recusada.value)
async function atualizar(pedido: string) {
  if (atualizando.has(pedido)) return
  const chave = `${props.conversaId}:${pedido}`
  const falta = TRAVA_MS - (Date.now() - (ultimaAtualizacao.get(chave) || 0))
  if (falta > 0) {
    toasts.info('Pedido atualizado há pouco', `Dá para buscar de novo em ${Math.ceil(falta / 1000)} s.`)
    return
  }
  atualizando.add(pedido)
  ultimaAtualizacao.set(chave, Date.now())
  try {
    const r = await api<{ retrato?: unknown; item?: OutraCompra | null; atualizado?: boolean; motivo?: string | null }>(
      `${base.value}/${encodeURIComponent(pedido)}/atualizar`,
      { method: 'POST' },
    )
    if (r?.retrato) guardarRetratos({ [pedido]: r.retrato })
    if (r?.item) {
      const novo = outraCompraDe(r.item)
      if (emAgCancelamento(itens.value.find((i) => i.pedido === pedido)?.bling) && !emAgCancelamento(novo.bling)) fixas.add(pedido)
      itens.value = itens.value.map((i) => (i.pedido === pedido ? novo : i))
    }
    if (r?.atualizado) toasts.success('Pedido atualizado da plataforma')
    else if (r?.motivo === 'recente') toasts.info('Pedido atualizado há pouco', 'Espere um minuto para buscar de novo.')
    else if (r?.motivo === 'limite') toasts.info('Muitas atualizações desta loja agora', 'Para não sobrecarregar a API da loja, tente de novo em 1 minuto.')
    else if (r?.motivo === 'falhou') toasts.warning('Não consegui falar com a loja agora', 'O cartão continua com o que já tinha — tente de novo em instantes.')
    else toasts.warning('A plataforma não devolveu o pedido agora', 'O cartão continua com o que já tinha — tente de novo em instantes.')
  } catch (e: any) {
    const er = erroDaApi(e, 'Não consegui atualizar o pedido')
    // Recusado antes de ir à loja: pode tentar de novo sem esperar o minuto.
    ultimaAtualizacao.delete(chave)
    const code = e?.data?.detail?.code
    if (code === 'leitura_desligada' || code === 'canal_desligado') {
      recusada.value = true
      toasts.info(er.texto)
    } else {
      toasts.error(er.texto, er.motivos)
    }
  } finally {
    atualizando.delete(pedido)
  }
}
</script>

<template>
  <section v-if="itens.length" ref="raiz" class="space-y-2" data-outras-compras>
    <h4 class="text-[13px] font-semibold">Outras compras ({{ itens.length }})</h4>
    <ul class="space-y-2">
      <li
        v-for="c in cartoes"
        :key="c.item.pedido"
        class="scroll-mt-2 space-y-2 rounded-md border px-2.5 py-2"
        :class="c.pesquisada ? 'ring-2 ring-primary/70' : ''"
        :data-outra-compra="c.item.pedido"
        :data-com-retrato="c.r ? '' : undefined"
        :data-compra-pesquisada="c.pesquisada ? '' : undefined"
      >
        <div class="space-y-1">
          <div class="flex items-center gap-2">
            <span v-if="c.statusTexto" class="rounded border px-1.5 py-px text-xs" :class="statusPedidoCls(c.status, c.statusTexto)">{{ c.statusTexto }}</span>
            <button
              v-if="mostraAtualizar"
              type="button"
              class="ml-auto rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-50"
              :disabled="atualizando.has(c.item.pedido)"
              :title="`atualizar da plataforma${c.r?.atualizado_em ? ` (retrato de ${fmtDataHora(c.r.atualizado_em)})` : ''}`"
              aria-label="atualizar este pedido da plataforma"
              @click="atualizar(c.item.pedido)"
            >
              <RotateCcw class="size-3.5" :class="{ 'animate-spin': atualizando.has(c.item.pedido) }" />
            </button>
          </div>
          <div class="flex items-center gap-1 font-medium">
            <span class="min-w-0 truncate font-mono text-[13px]" :title="c.item.pedido">{{ c.item.pedido }}</span>
            <button type="button" class="shrink-0 rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground" title="copiar nº do pedido" aria-label="copiar nº do pedido" @click="copiarTexto(c.item.pedido, 'Nº do pedido')"><Copy class="size-3.5" /></button>
          </div>
          <div v-if="c.criadoEm" class="text-xs text-muted-foreground">{{ fmtDataPlataforma(c.criadoEm) }} (UTC-03:00)</div>
        </div>

        <!-- os produtos, com o retrato; sem ele, o resumo do índice -->
        <ul v-if="c.produtos.length" class="space-y-3">
          <li v-for="(it, i) in c.produtos" :key="i" class="flex gap-2.5">
            <img
              v-if="it.imagem && !fotosFalhas.has(`${c.item.pedido}:${i}`)"
              :src="it.imagem"
              alt=""
              loading="lazy"
              referrerpolicy="no-referrer"
              class="size-14 shrink-0 rounded border object-cover"
              @error="fotosFalhas.add(`${c.item.pedido}:${i}`)"
            />
            <span v-else class="flex size-14 shrink-0 items-center justify-center rounded border bg-muted text-muted-foreground"><Package class="size-5" /></span>
            <div class="min-w-0 flex-1 text-xs">
              <div class="flex items-start gap-2">
                <div class="line-clamp-2 min-w-0 flex-1 text-[13px] font-medium leading-5" :title="it.titulo || ''">
                  <AtendimentoIconePlataforma :plataforma="plataforma" :tamanho="13" decorativo class="-mt-0.5 mr-0.5" />{{ it.titulo || 'Produto' }}
                </div>
                <div v-if="it.preco !== null && it.preco !== undefined" class="shrink-0 text-right text-[13px] tabular-nums">{{ fmtDinheiro(it.preco, c.moeda) }}</div>
              </div>
              <div v-if="it.variacao" class="mt-0.5 truncate text-muted-foreground" :title="it.variacao">Variação: {{ it.variacao }}</div>
              <div class="mt-0.5 flex items-center gap-2 text-muted-foreground">
                <span v-if="it.sku" class="min-w-0 truncate">SKU: <span class="font-mono">{{ it.sku }}</span></span>
                <span v-if="it.quantidade !== null && it.quantidade !== undefined" class="ml-auto shrink-0 tabular-nums">× {{ it.quantidade }}</span>
              </div>
            </div>
          </li>
        </ul>
        <div v-else-if="c.item.itens_resumo" class="line-clamp-3 text-xs" :title="c.item.itens_resumo" data-resumo-do-indice>{{ c.item.itens_resumo }}</div>

        <!-- valores, pagamento, cancelamento e logística -->
        <dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
          <template v-if="c.r?.valor_pago !== null && c.r?.valor_pago !== undefined">
            <dt class="text-muted-foreground">Valor pago pelo comprador</dt>
            <dd class="text-right font-semibold tabular-nums">{{ fmtDinheiro(c.r.valor_pago, c.moeda) }}</dd>
          </template>
          <template v-else-if="c.total !== null && c.total !== undefined">
            <dt class="text-muted-foreground">Total do pedido</dt>
            <dd class="text-right tabular-nums">{{ fmtDinheiro(c.total, c.moeda) }}</dd>
          </template>
          <template v-if="c.r">
            <dt class="text-muted-foreground">Método de pagamento</dt>
            <dd class="text-right">{{ c.r.pagamento_metodo || '—' }}</dd>
          </template>
          <template v-if="c.r?.cancelamento">
            <dt class="text-muted-foreground">Motivo do cancelamento</dt>
            <dd class="text-right" :title="c.r.cancelamento"><span class="line-clamp-3">{{ c.r.cancelamento }}</span></dd>
          </template>
          <template v-if="c.log?.transportadora">
            <dt class="text-muted-foreground">Provedor de transporte</dt>
            <dd class="truncate text-right" :title="c.log.transportadora">{{ c.log.transportadora }}</dd>
          </template>
          <template v-if="c.log?.status_texto || c.log?.status">
            <dt class="text-muted-foreground">Estado da logística</dt>
            <dd class="flex items-center justify-end gap-1 text-right">
              <component :is="LOG_ICONE[nivelLogistica(c.log.status, c.log.status_texto)]" class="size-4 shrink-0" :class="LOG_COR[nivelLogistica(c.log.status, c.log.status_texto)]" />
              <span>{{ c.log.status_texto || c.log.status }}</span>
            </dd>
          </template>
        </dl>

        <!-- a linha do Bling, quando o pedido existe lá, e os links; o da plataforma, em todo cartão que tiver -->
        <div v-if="c.item.bling || c.item.link_plataforma" class="flex flex-wrap items-center gap-1.5 border-t pt-2 text-xs">
          <span v-if="c.item.bling" class="inline-flex min-w-0 flex-wrap items-center gap-1" :title="c.item.bling.situacao ? `situação no Bling: ${c.item.bling.situacao}` : ''" data-bling-da-compra>
            <span class="text-muted-foreground">Bling</span>
            <span class="font-mono">{{ c.item.bling.numero }}</span>
            <button type="button" class="rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground" title="copiar nº do Bling" aria-label="copiar nº do Bling" @click="copiarTexto(c.item.bling!.numero, 'Nº do Bling')"><Copy class="size-3" /></button>
            <span
              v-if="emAgCancelamento(c.item.bling)"
              class="truncate rounded border border-orange-500/60 bg-orange-500/15 px-1.5 py-px text-[11px] font-semibold text-orange-700 dark:text-orange-300"
              data-compra-ag-cancelamento
            >{{ c.item.bling.situacao || 'Aguardando Cancelamento' }}</span>
            <span v-else-if="c.item.bling.situacao" class="truncate rounded bg-muted px-1.5 py-px text-[11px]">{{ c.item.bling.situacao }}</span>
          </span>
          <span class="ml-auto flex flex-wrap items-center gap-1">
            <a
              v-if="c.item.bling?.link"
              :href="c.item.bling.link"
              target="_blank"
              rel="noopener noreferrer"
              class="inline-flex items-center gap-0.5 rounded border px-1.5 py-0.5 hover:bg-muted"
              title="abrir o pedido no Bling (outra aba) — aqui é só leitura"
            >Abrir no Bling<ExternalLink class="size-3" /></a>
            <AtendimentoAbrirPlataforma
              v-if="c.item.link_plataforma"
              :href="c.item.link_plataforma.url"
              :perfil="perfil"
              :conversa-id="conversaId"
              titulo="abrir o pedido no painel do vendedor (outra aba)"
              class="inline-flex items-center gap-0.5 rounded border px-1.5 py-0.5 hover:bg-muted"
            >{{ c.item.link_plataforma.rotulo }}<ExternalLink class="size-3" /></AtendimentoAbrirPlataforma>
          </span>
        </div>
      </li>
    </ul>
    <button
      v-if="disposicao.ocultas > 0"
      type="button"
      class="w-full rounded-md border px-2 py-1.5 text-xs hover:bg-muted"
      data-carregar-mais-compras
      @click="carregarMais"
    >Carregar mais</button>
  </section>
</template>
