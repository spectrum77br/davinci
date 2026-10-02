<script lang="ts">
// Cartão da reclamação no topo da conversa (Atendimento, RF2, 01/10/2026).
// A reclamação, mediação ou devolução da PLATAFORMA ligada à conversa —
// pela conversa ou pelo pedido: a conversa do pack e a conversa da
// reclamação do mesmo pedido mostram o mesmo cartão. Vem de
// GET /api/atendimento/conversas/{id}/reclamacoes (ML pelas reclamações da
// conta; Shopee e TikTok pela Logística).
//
// SÓ LEITURA: nº, tipo, status, o motivo do COMPRADOR, o que ele pede
// ("Pede: Devolução + reembolso", Shopee/TikTok) e o PRAZO com contagem
// regressiva (até quando a loja tem de agir na plataforma), o que a
// plataforma espera e o "Abrir na plataforma". Status e motivo chegam em
// português do backend; as tabelas daqui (as mesmas de
// services/atendimento/reclamacoes_devolucoes.py) são a rede para o código
// cru que escapar — nunca aparece "JUDGING" nem "CHANGE_MIND" na tela.
// Nenhuma ação na plataforma sai daqui (aceitar
// devolução, oferecer solução, pedir mediação ficam para depois, com
// confirmação e auditoria).
//
// Cores (as da etiqueta): Reclamação e Mediação em vermelho, Devolução em
// roxo. As encerradas ficam recolhidas embaixo, numa linha cada.
//
// Este bloco (não o setup) é o módulo dos ajudantes puros, testados em
// tests/atendimento-reclamacao.cjs — por isso não importa nada.

export type TipoReclamacao = 'reclamacao' | 'mediacao' | 'devolucao'

export interface Reclamacao {
  id: string
  plataforma: string
  plataforma_nome: string
  numero: string | null
  tipo: TipoReclamacao | string
  tipo_rotulo: string
  status: string | null
  status_rotulo: string | null
  aberta: boolean
  // O que o comprador alegou (em português).
  motivo: string | null
  // O que ele pediu na Shopee/TikTok ("Devolução + reembolso", "Só
  // reembolso (produto fica com o cliente)"); opcional: a resposta antiga
  // não traz.
  solucao?: string | null
  pedido_marketplace: string | null
  prazo_em: string | null
  aberta_em: string | null
  encerrada_em: string | null
  acao_pendente: string | null
  mediacao: boolean
  reputacao_afetada: boolean | null
  devolucao_status: string | null
  conversa_id: string | null
  url_plataforma: string | null
}

export interface ReclamacoesResposta {
  itens: Reclamacao[]
  abertas: number
  prazo_mais_curto: string | null
}

// Os prazos de reclamação são de dias (o ML dá ~5 dias para responder ao
// comprador): "vencendo" é faltar menos de 24 h — o selo de 2 h da lista é
// para a mensagem de chat.
export const PRAZO_VENCENDO_MS = 24 * 3600 * 1000

// O motivo do ML vem como código ("not_working_item"); o `detail` do próprio
// ML às vezes não serve ("Chegou bem" para produto que não funciona). Medido
// em produção em 02/10/2026: estes são os que aparecem. Código desconhecido
// vira texto legível ("wrong item" → "Wrong item"), nunca some.
export const MOTIVOS_ML: Record<string, string> = {
  repentant_buyer: 'Desistiu da compra (chegou bem, não quer mais)',
  bought_by_mistake: 'Comprou por engano',
  different_than_published: 'Diferente do anúncio',
  different_color_or_size: 'Cor, tamanho ou modelo diferente',
  different_item_other: 'Recebeu outro produto',
  not_working_item: 'Produto não funciona',
  broken_item: 'Produto quebrado ou com defeito',
  damaged_package_broken_item: 'Embalagem danificada e produto quebrado',
  missing_accessories: 'Faltam acessórios',
  missing_item: 'Falta produto no pacote',
  delivered_but_not_receive_package: 'Consta entregue, mas não recebeu',
  not_received: 'Não recebeu o produto',
}
// O motivo da Shopee (`reason` do detalhe da devolução), medido em produção
// em 02/10/2026 (os seis primeiros) + os da documentação. A TikTok já manda
// o rótulo em português ("Item com defeito"): passa como veio.
export const MOTIVOS_SHOPEE: Record<string, string> = {
  CHANGE_MIND: 'Mudou de ideia (desistiu da compra)',
  FUNCTIONAL_DMG: 'Produto com defeito (não funciona)',
  WRONG_ITEM: 'Recebeu produto errado',
  ITEM_MISSING: 'Falta produto no pacote',
  NOT_RECEIPT: 'Não recebeu o produto',
  SUSPICIOUS_PARCEL: 'Pacote vazio ou violado',
  MISSING_ITEM: 'Falta produto no pacote',
  PHYSICAL_DMG: 'Produto danificado (avaria)',
  ITEM_DAMAGED: 'Produto danificado',
  DIFFERENT_DESCRIPTION: 'Diferente do anúncio',
  NOT_AS_DESCRIBED: 'Diferente do anúncio',
  ITEM_FAKE: 'Produto falsificado',
  EXPECTATION_FAILED: 'Não atendeu à expectativa',
  ITEM_NOT_FIT: 'Não serviu (tamanho ou modelo)',
  MUTUAL_AGREE: 'Acordo entre comprador e loja',
  OTHER: 'Outro motivo',
}
// Parece código da plataforma (não texto de gente)? "not_working_item",
// "CHANGE_MIND", "opened".
function ehCodigo(t: string): boolean {
  if (/^[a-z0-9]+$/.test(t)) return true
  return /^[A-Za-z0-9_]+$/.test(t) && (t.includes('_') || (/[A-Z]/.test(t) && t === t.toUpperCase()))
}
// "ITEM_NOT_FIT" / "wrong_size" → "Item not fit" / "Wrong size".
function legivel(codigo: string): string {
  const texto = codigo.replace(/_/g, ' ').trim().toLowerCase()
  return texto.charAt(0).toUpperCase() + texto.slice(1)
}
export function motivoLegivel(motivo: string | null | undefined): string {
  const m = String(motivo ?? '').trim()
  if (!m) return ''
  if (MOTIVOS_ML[m]) return MOTIVOS_ML[m]
  if (MOTIVOS_SHOPEE[m.toUpperCase()]) return MOTIVOS_SHOPEE[m.toUpperCase()]
  if (ehCodigo(m)) return legivel(m)
  return m
}

// O status do caso em português, por plataforma (a mesma tabela de
// reclamacoes_devolucoes.STATUS_TELA; o ML pelo status do claim). Conferido
// com a Logística: Shopee JUDGING = a Shopee julgando a disputa,
// SELLER_DISPUTE = a loja contestou; TikTok AWAITING_BUYER_SHIP = aprovada,
// esperando o comprador postar; REJECT_RECEIVE_PACKAGE = a loja recusou o
// pacote recebido.
export const STATUS_PLATAFORMA: Record<string, Record<string, string>> = {
  shopee: {
    REQUESTED: 'Pedido de devolução aberto',
    PROCESSING: 'Em andamento',
    JUDGING: 'Em análise pela Shopee (disputa)',
    SELLER_DISPUTE: 'Loja contestou',
    ACCEPTED: 'Aceita — reembolso pago ao comprador',
    REFUND_PAID: 'Reembolso pago ao comprador',
    CANCELLED: 'Cancelada',
    CLOSED: 'Encerrada pela Shopee',
  },
  tiktok: {
    RETURN_OR_REFUND_REQUEST_PENDING: 'Pedido de devolução/reembolso pendente',
    AWAITING_BUYER_SHIP: 'Esperando o comprador enviar',
    BUYER_SHIPPED_ITEM: 'Comprador enviou o produto',
    AWAITING_BUYER_RESPONSE: 'Esperando resposta do comprador',
    REJECT_RECEIVE_PACKAGE: 'Recebimento recusado',
    RETURN_OR_REFUND_REQUEST_SUCCESS: 'Concluída — reembolso pago',
    RETURN_OR_REFUND_REQUEST_COMPLETE: 'Concluída — reembolso pago',
    RETURN_OR_REFUND_REQUEST_CANCEL: 'Cancelada pelo comprador',
    RETURN_OR_REFUND_REQUEST_REJECT: 'Recusada pela loja',
    REFUND_OR_RETURN_REQUEST_REJECT: 'Recusada pela loja',
  },
  ml: {
    opened: 'Aberta',
    closed: 'Encerrada',
  },
}
// O status que o cartão mostra: o do backend (já em português, e mais rico
// no ML: "Em mediação no Mercado Livre"); se ele vier vazio ou cru
// ("JUDGING"), a tabela; código desconhecido, legível.
export function statusLegivel(r: Pick<Reclamacao, 'plataforma' | 'status' | 'status_rotulo'>): string {
  const rotulo = String(r.status_rotulo ?? '').trim()
  const cru = String(r.status ?? '').trim()
  if (rotulo && rotulo !== cru && !ehCodigo(rotulo)) return rotulo
  const codigo = rotulo || cru
  if (!codigo) return ''
  const tabela = STATUS_PLATAFORMA[String(r.plataforma ?? '').trim().toLowerCase()] || {}
  return tabela[codigo] || tabela[codigo.toUpperCase()] || tabela[codigo.toLowerCase()] || legivel(codigo)
}

// Reclamação/Mediação = vermelho; Devolução = roxo (as cores da etiqueta).
export function corDoTipo(tipo: string | null | undefined): { selo: string; borda: string } {
  if (tipo === 'devolucao') {
    return {
      selo: 'bg-purple-500/15 text-purple-700 dark:text-purple-300',
      borda: 'border-purple-500/40 bg-purple-500/5',
    }
  }
  return {
    selo: 'bg-red-500/15 text-red-700 dark:text-red-300',
    borda: 'border-red-500/40 bg-red-500/5',
  }
}

// "45 min", "3 h 20 min", "2 d 4 h" (o mesmo jeito da lista).
export function tempo(min: number): string {
  const m = Math.max(0, Math.round(min))
  if (m < 60) return `${m} min`
  const h = Math.floor(m / 60)
  if (h < 24) return m % 60 ? `${h} h ${m % 60} min` : `${h} h`
  const d = Math.floor(h / 24)
  return h % 24 ? `${d} d ${h % 24} h` : `${d} d`
}

export type PrazoReclamacao = { nivel: 'vencida' | 'vencendo' | 'ok'; texto: string; cls: string }

// A contagem regressiva do prazo da plataforma; null sem prazo ou encerrada.
export function prazoReclamacao(
  r: Pick<Reclamacao, 'aberta' | 'prazo_em'>,
  agora = Date.now(),
): PrazoReclamacao | null {
  if (!r.aberta || !r.prazo_em) return null
  const t = new Date(r.prazo_em).getTime()
  if (Number.isNaN(t)) return null
  const falta = t - agora
  if (falta <= 0) {
    return { nivel: 'vencida', texto: `prazo vencido há ${tempo(-falta / 60000)}`, cls: 'bg-red-600 text-white' }
  }
  if (falta < PRAZO_VENCENDO_MS) {
    return { nivel: 'vencendo', texto: `faltam ${tempo(falta / 60000)}`, cls: 'bg-amber-500/20 text-amber-800 dark:text-amber-300' }
  }
  return { nivel: 'ok', texto: `faltam ${tempo(falta / 60000)}`, cls: 'bg-muted text-muted-foreground' }
}

// "Abrir no Mercado Livre" / "Abrir na Shopee" / "Abrir no TikTok".
export function abrirEm(plataforma: string | null | undefined, nome: string): string {
  return `Abrir ${(plataforma || '').trim().toLowerCase() === 'shopee' ? 'na' : 'no'} ${nome}`
}

// "Reclamação nº 5582543195"; sem o nº da plataforma (a Logística ainda não o
// tem), pelo pedido.
export function tituloReclamacao(r: Pick<Reclamacao, 'tipo_rotulo' | 'numero' | 'pedido_marketplace'>): string {
  if (r.numero) return `${r.tipo_rotulo} nº ${r.numero}`
  if (r.pedido_marketplace) return `${r.tipo_rotulo} do pedido ${r.pedido_marketplace}`
  return r.tipo_rotulo
}

// Abertas primeiro (prazo mais curto antes; sem prazo no fim), depois as
// encerradas, da mais recente para a mais antiga. O backend já manda assim;
// a tela não confia na ordem.
export function ordenarReclamacoes(itens: Reclamacao[]): Reclamacao[] {
  const t = (iso: string | null) => (iso ? new Date(iso).getTime() : Number.NaN)
  return itens.slice().sort((a, b) => {
    if (a.aberta !== b.aberta) return a.aberta ? -1 : 1
    if (a.aberta) {
      const pa = t(a.prazo_em)
      const pb = t(b.prazo_em)
      if (Number.isNaN(pa) !== Number.isNaN(pb)) return Number.isNaN(pa) ? 1 : -1
      if (!Number.isNaN(pa) && pa !== pb) return pa - pb
      return (t(b.aberta_em) || 0) - (t(a.aberta_em) || 0)
    }
    return (t(b.encerrada_em) || 0) - (t(a.encerrada_em) || 0)
  })
}
</script>

<script setup lang="ts">
import { ChevronDown, ChevronRight, Copy, ExternalLink, Scale, TriangleAlert, Undo2 } from 'lucide-vue-next'
import type { PerfilAdsPower } from '~/components/AtendimentoAdsPower.vue'
import { copiar, fmtDataHora, useRelogio } from '~/components/AtendimentoPlataforma.vue'

const props = withDefaults(defineProps<{
  conversaId: string
  // O perfil do AdsPower da loja (painel da conversa): "Abrir no …" abre nele.
  perfil?: PerfilAdsPower | null
}>(), { perfil: null })
const emit = defineEmits<{
  // Para quem monta a conversa: quantas abertas e o prazo mais curto (o selo
  // do cabeçalho, se quiser).
  (e: 'carregado', r: ReclamacoesResposta): void
}>()
const { api } = useApi()
const toasts = useToasts()
const agora = useRelogio()

const dados = ref<ReclamacoesResposta | null>(null)
const erro = ref(false)
const verEncerradas = ref(false)

const itens = computed(() => ordenarReclamacoes(dados.value?.itens || []))
const abertas = computed(() => itens.value.filter((r) => r.aberta))
const encerradas = computed(() => itens.value.filter((r) => !r.aberta))

let pedidoAtual = 0
async function carregar() {
  const id = props.conversaId
  if (!id) return
  const meu = ++pedidoAtual
  erro.value = false
  try {
    const r = await api<ReclamacoesResposta>(`/api/atendimento/conversas/${encodeURIComponent(id)}/reclamacoes`)
    if (meu !== pedidoAtual) return // trocou de conversa no meio
    dados.value = r
    emit('carregado', r)
  } catch {
    if (meu !== pedidoAtual) return
    // O cartão é complemento da conversa: sem ele, a conversa segue.
    dados.value = null
    erro.value = true
  }
}
watch(() => props.conversaId, () => {
  dados.value = null
  verEncerradas.value = false
  carregar()
}, { immediate: true })
defineExpose({ carregar })

async function copiarNumero(r: Reclamacao) {
  if (!r.numero) return
  if (await copiar(r.numero)) toasts.success('Nº copiado')
  else window.prompt('Nº', r.numero)
}
</script>

<template>
  <div v-if="itens.length" class="space-y-2" aria-label="Reclamações e devoluções da plataforma">
    <div
      v-for="r in abertas"
      :key="r.id"
      class="rounded-md border p-3 text-[13px] leading-5"
      :class="corDoTipo(r.tipo).borda"
    >
      <div class="flex flex-wrap items-center gap-1.5">
        <span class="inline-flex items-center gap-1 rounded px-1.5 py-px text-xs font-semibold" :class="corDoTipo(r.tipo).selo">
          <Undo2 v-if="r.tipo === 'devolucao'" class="size-3.5" aria-hidden="true" />
          <Scale v-else-if="r.mediacao" class="size-3.5" aria-hidden="true" />
          <TriangleAlert v-else class="size-3.5" aria-hidden="true" />
          {{ r.tipo_rotulo }}
        </span>
        <span class="min-w-0 break-all font-semibold">{{ r.numero ? `nº ${r.numero}` : tituloReclamacao(r) }}</span>
        <button
          v-if="r.numero"
          type="button"
          class="shrink-0 rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
          title="copiar o nº"
          aria-label="copiar o nº"
          @click="copiarNumero(r)"
        >
          <Copy class="size-3.5" />
        </button>
        <span class="text-xs text-muted-foreground">· {{ r.plataforma_nome }}</span>
        <span
          v-if="prazoReclamacao(r, agora)"
          class="ml-auto rounded px-1.5 py-px text-xs font-medium tabular-nums"
          :class="prazoReclamacao(r, agora)!.cls"
          :title="`agir até ${fmtDataHora(r.prazo_em)}`"
        >{{ prazoReclamacao(r, agora)!.texto }}</span>
      </div>

      <div v-if="statusLegivel(r)" class="mt-1" :title="r.status || undefined">{{ statusLegivel(r) }}</div>
      <div v-if="r.motivo" class="text-muted-foreground" :title="r.motivo">Motivo: {{ motivoLegivel(r.motivo) }}</div>
      <div v-if="r.solucao" class="text-muted-foreground">Pede: {{ r.solucao }}</div>
      <div v-if="r.acao_pendente" class="mt-1 font-medium">
        {{ r.acao_pendente }}<span v-if="r.prazo_em" class="font-normal text-muted-foreground"> — até {{ fmtDataHora(r.prazo_em) }}</span>
      </div>
      <div class="mt-1 flex flex-wrap items-center gap-1.5 text-xs">
        <span v-if="r.reputacao_afetada" class="rounded border border-red-500/50 px-1.5 py-px text-red-700 dark:text-red-300">Reputação afetada</span>
        <span v-if="r.aberta_em" class="text-muted-foreground">aberta em {{ fmtDataHora(r.aberta_em) }}</span>
        <AtendimentoAbrirPlataforma
          v-if="r.url_plataforma"
          :href="r.url_plataforma"
          :perfil="perfil"
          :conversa-id="conversaId"
          class="ml-auto inline-flex items-center gap-1 rounded border bg-background px-2 py-0.5 hover:bg-muted"
        >
          <ExternalLink class="size-3.5" aria-hidden="true" />
          {{ abrirEm(r.plataforma, r.plataforma_nome) }}
        </AtendimentoAbrirPlataforma>
      </div>
      <div class="mt-1 text-xs text-muted-foreground">Só leitura aqui: responda e aja pela plataforma.</div>
    </div>

    <div v-if="encerradas.length" class="rounded-md border text-xs">
      <button
        type="button"
        class="flex w-full items-center gap-1 px-2 py-1.5 text-left text-muted-foreground hover:bg-muted"
        :aria-expanded="verEncerradas"
        @click="verEncerradas = !verEncerradas"
      >
        <ChevronDown v-if="verEncerradas" class="size-3.5" />
        <ChevronRight v-else class="size-3.5" />
        {{ encerradas.length === 1 ? '1 encerrada' : `${encerradas.length} encerradas` }}
      </button>
      <ul v-show="verEncerradas" class="divide-y border-t">
        <li v-for="r in encerradas" :key="r.id" class="flex flex-wrap items-center gap-1.5 px-2 py-1.5">
          <span class="rounded px-1 py-px font-medium" :class="corDoTipo(r.tipo).selo">{{ r.tipo_rotulo }}</span>
          <span class="break-all">{{ r.numero ? `nº ${r.numero}` : tituloReclamacao(r) }}</span>
          <span v-if="statusLegivel(r)" class="text-muted-foreground">· {{ statusLegivel(r) }}</span>
          <span v-if="r.encerrada_em" class="text-muted-foreground">· {{ fmtDataHora(r.encerrada_em) }}</span>
          <AtendimentoAbrirPlataforma
            v-if="r.url_plataforma"
            :href="r.url_plataforma"
            :perfil="perfil"
            :conversa-id="conversaId"
            :titulo="abrirEm(r.plataforma, r.plataforma_nome)"
            class="ml-auto inline-flex items-center gap-1 hover:underline"
          >
            <ExternalLink class="size-3.5" aria-hidden="true" />
          </AtendimentoAbrirPlataforma>
        </li>
      </ul>
    </div>
  </div>
  <div v-else-if="erro" class="text-xs text-muted-foreground">Não deu para carregar as reclamações da plataforma agora.</div>
</template>
