<script lang="ts">
// Etiqueta = status atual da conversa (RF1 do Comunicador, 01/10/2026): o selo
// colorido da lista e do cabeçalho da conversa. UMA etiqueta por conversa, que
// muda sozinha quando o status muda (Pós-venda → Reclamação → Pós-venda); as
// outras abertas ao mesmo tempo viram o indicador pequeno (bolinhas). Quem
// calcula é o backend (apps/api/app/services/atendimento/etiqueta.py); os
// códigos são os de constantes.ETIQUETAS.
//
// Cores (decididas em 01/10/2026): Reclamação vermelho, Devolução roxo,
// Pré-venda azul, Ag. cancelamento laranja; Pós-venda SEM destaque (é a
// maioria — destacar tudo é não destacar nada). Avaliação (RF8, 02/10/2026)
// amarela, com as estrelas da pior nota pendente (`avaliacao_estrelas` da
// lista) e a nota 1–3 em destaque (amarelo cheio, estrelas em vermelho).
// Carrinho (RF9, 02/10/2026) verde-azulado (o laranja já é do Ag.
// cancelamento): carrinho abandonado do lojista no site, entre Avaliação e
// Pré-venda. Mídia (RF7, 02/10/2026) ROSA: a etiqueta própria das conversas
// de comentário, menção e Direct das redes — é uma BASE, como pré/pós-venda
// (nunca vira indicador). Etiqueta nova (SAC…): acrescente aqui, no lugar
// certo da PRIORIDADE.
//
// Canal nunca é etiqueta: e-mail, Zap e o próprio canal "reclamação" são o
// CANAL da conversa (AtendimentoPlataforma.canalLabel); a etiqueta é o status.
export type EtiquetaInfo = {
  value: string
  label: string
  hint: string
  // Selo (fundo + texto), faixa à esquerda da linha e bolinha do indicador.
  cls: string
  faixa: string
  ponto: string
  // Pós-venda não tem destaque: sem faixa e, na lista, sem selo.
  destaque: boolean
}
export const ETIQUETAS_INFO: Record<string, EtiquetaInfo> = {
  reclamacao: {
    value: 'reclamacao',
    label: 'Reclamação',
    hint: 'reclamação ou mediação aberta na plataforma',
    cls: 'bg-red-500/15 text-red-700 dark:text-red-300',
    faixa: 'bg-red-500',
    ponto: 'bg-red-500',
    destaque: true,
  },
  ag_cancelamento: {
    value: 'ag_cancelamento',
    label: 'Ag. cancelamento',
    hint: 'pedido em Aguardando Cancelamento no Bling (fora a trava do robô da Margem)',
    cls: 'bg-orange-500/15 text-orange-700 dark:text-orange-300',
    faixa: 'bg-orange-500',
    ponto: 'bg-orange-500',
    destaque: true,
  },
  devolucao: {
    value: 'devolucao',
    label: 'Devolução',
    hint: 'devolução aberta na plataforma ou pedido em Aguardando Devolução no Bling',
    cls: 'bg-purple-500/15 text-purple-700 dark:text-purple-300',
    faixa: 'bg-purple-500',
    ponto: 'bg-purple-500',
    destaque: true,
  },
  avaliacao: {
    value: 'avaliacao',
    label: 'Avaliação',
    hint: 'avaliação de venda sem resposta da loja (Shopee) ou com nota 1–3 sem tratar (Mercado Livre)',
    cls: 'bg-yellow-400/25 text-yellow-800 dark:text-yellow-300',
    faixa: 'bg-yellow-400',
    ponto: 'bg-yellow-400',
    destaque: true,
  },
  carrinho: {
    value: 'carrinho',
    label: 'Carrinho',
    hint: 'carrinho abandonado do lojista no site (parado há mais de 24 h, sem finalizar pelo WhatsApp)',
    cls: 'bg-teal-500/15 text-teal-700 dark:text-teal-300',
    faixa: 'bg-teal-500',
    ponto: 'bg-teal-500',
    destaque: true,
  },
  pre_venda: {
    value: 'pre_venda',
    label: 'Pré-venda',
    hint: 'pergunta ou conversa sem pedido ligado',
    cls: 'bg-blue-500/15 text-blue-700 dark:text-blue-300',
    faixa: 'bg-blue-500',
    ponto: 'bg-blue-500',
    destaque: true,
  },
  pos_venda: {
    value: 'pos_venda',
    label: 'Pós-venda',
    hint: 'conversa de um pedido, sem nada aberto',
    cls: 'bg-muted text-muted-foreground',
    faixa: '',
    ponto: 'bg-muted-foreground/50',
    destaque: false,
  },
  midia: {
    value: 'midia',
    label: 'Mídia',
    hint: 'comentário, menção ou Direct nas redes sociais da marca',
    cls: 'bg-pink-500/15 text-pink-700 dark:text-pink-300',
    faixa: 'bg-pink-500',
    ponto: 'bg-pink-500',
    destaque: true,
  },
}
// Da MAIS urgente para a menos (constantes.PRIORIDADE_ETIQUETAS): duas abertas
// ao mesmo tempo, vale a primeira; a outra vira o indicador.
export const PRIORIDADE_ETIQUETAS = ['reclamacao', 'ag_cancelamento', 'devolucao', 'avaliacao', 'carrinho', 'pre_venda', 'pos_venda', 'midia']
// Toda conversa tem uma delas (pré/pós-venda; Mídia nas das redes): nunca
// entram no indicador.
const BASE = new Set(['pre_venda', 'pos_venda', 'midia'])

// ─── as estrelas do selo Avaliação (RF8) ────────────────────────────────────
// Nota "baixa" = 1–3, a mesma régua do backend (constantes.NOTA_BAIXA_AVALIACAO).
export const NOTA_BAIXA_AVALIACAO = 3
// A nota como estrelas cheias e vazias ("★★☆☆☆"); fora de 1–5 (ou sem nota), ''.
export function estrelasDaNota(n: number | null | undefined): string {
  const v = Number(n)
  if (!Number.isInteger(v) || v < 1 || v > 5) return ''
  return '★'.repeat(v) + '☆'.repeat(5 - v)
}
export function notaBaixa(n: number | null | undefined): boolean {
  return !!estrelasDaNota(n) && Number(n) <= NOTA_BAIXA_AVALIACAO
}
// Nota 1–3 em destaque: amarelo cheio com contorno vermelho (o resto da
// Avaliação fica no amarelo translúcido).
export const CLS_AVALIACAO_NOTA_BAIXA = 'bg-yellow-400 text-yellow-950 ring-1 ring-red-500/70 dark:bg-yellow-400/80'
// O fundo/texto do selo: o da etiqueta; na Avaliação com nota baixa, o destaque.
export function clsDoSelo(etiqueta: string | null | undefined, estrelas?: number | null): string {
  const info = etiquetaInfo(etiqueta)
  if (!info) return ''
  return info.value === 'avaliacao' && notaBaixa(estrelas) ? CLS_AVALIACAO_NOTA_BAIXA : info.cls
}

export function etiquetaInfo(etiqueta: string | null | undefined): EtiquetaInfo | null {
  const e = (etiqueta || '').trim().toLowerCase()
  if (!e) return null
  // Código que a tela ainda não conhece (etiqueta nova no backend): aparece
  // neutro, com o código — melhor que sumir.
  return ETIQUETAS_INFO[e] || { value: e, label: e, hint: '', cls: 'bg-muted text-muted-foreground', faixa: '', ponto: 'bg-muted-foreground/50', destaque: false }
}

// A faixa colorida à esquerda da linha da lista ('' = sem faixa).
export function faixaDaEtiqueta(etiqueta: string | null | undefined): string {
  const info = etiquetaInfo(etiqueta)
  return info?.destaque ? info.faixa : ''
}

// O indicador pequeno: as secundárias que valem mostrar, da mais urgente para
// a menos, sem a própria etiqueta, sem repetir e sem a de base.
export function secundariasDe(etiqueta: string | null | undefined, secundarias: string[] | null | undefined): EtiquetaInfo[] {
  const vistas = new Set<string>()
  const ordem = (e: string) => {
    const i = PRIORIDADE_ETIQUETAS.indexOf(e)
    return i < 0 ? PRIORIDADE_ETIQUETAS.length : i
  }
  return (Array.isArray(secundarias) ? secundarias : [])
    .map((e) => String(e || '').trim().toLowerCase())
    .filter((e) => e && e !== etiqueta && !BASE.has(e) && !vistas.has(e) && !!vistas.add(e))
    .sort((a, b) => ordem(a) - ordem(b))
    .map((e) => etiquetaInfo(e)!)
}

// As opções da troca à mão, na ordem da prioridade.
export const OPCOES_ETIQUETA: EtiquetaInfo[] = PRIORIDADE_ETIQUETAS.map((e) => ETIQUETAS_INFO[e])

// O title do selo: o que é, desde quando e se foi trocada à mão.
export function tituloDaEtiqueta(
  etiqueta: string | null | undefined,
  { desde, manual, secundarias, estrelas }: { desde?: string | null; manual?: boolean; secundarias?: string[] | null; estrelas?: number | null } = {},
): string {
  const info = etiquetaInfo(etiqueta)
  if (!info) return ''
  const partes = [info.hint ? `${info.label}: ${info.hint}` : info.label]
  if (info.value === 'avaliacao' && estrelasDaNota(estrelas)) {
    partes.push(`pior nota sem resposta: ${estrelas} de 5${notaBaixa(estrelas) ? ' (nota baixa)' : ''}`)
  }
  if (desde) {
    const d = new Date(desde)
    if (!Number.isNaN(d.getTime())) partes.push(`desde ${d.toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })}`)
  }
  if (manual) partes.push('trocada à mão — volta ao automático no próximo acontecimento (reclamação, devolução, Bling)')
  const outras = secundariasDe(etiqueta, secundarias)
  if (outras.length) partes.push(`também aberta: ${outras.map((o) => o.label).join(', ')}`)
  return partes.join(' · ')
}
</script>

<script setup lang="ts">
// O selo. Na lista (`esconderPosVenda`) a Pós-venda não aparece — só as que
// pedem atenção; no cabeçalho aparece sempre. `editavel` (cabeçalho da
// conversa) abre a troca à mão: POST /conversas/{id}/etiqueta, com motivo
// opcional; a resposta volta em `trocada` (conversa + linha do tempo) para
// quem usa juntar ao detalhe. A troca fica no histórico com o nome de quem
// trocou e vale até o próximo acontecimento automático.
import { onClickOutside } from '@vueuse/core'
import { Check, Hand, History, Loader2 } from 'lucide-vue-next'
import { erroDaApi, type EtiquetaHistorico, type EtiquetaTroca } from '~/components/AtendimentoPlataforma.vue'

const props = withDefaults(defineProps<{
  etiqueta: string | null | undefined
  secundarias?: string[] | null
  manual?: boolean
  desde?: string | null
  // A linha escolhida (fundo azul): o selo vira translúcido.
  selecionada?: boolean
  esconderPosVenda?: boolean
  editavel?: boolean
  conversaId?: string | null
  historico?: EtiquetaHistorico[] | null
  // Avaliação (RF8): a pior nota pendente (`avaliacao_estrelas`) — as
  // estrelas no selo; 1–3 em destaque. Só vale quando a etiqueta é Avaliação.
  estrelas?: number | null
}>(), {
  secundarias: () => [],
  manual: false,
  desde: null,
  selecionada: false,
  esconderPosVenda: false,
  editavel: false,
  conversaId: null,
  historico: null,
  estrelas: null,
})
const emit = defineEmits<{ (e: 'trocada', r: EtiquetaTroca): void }>()

const info = computed(() => etiquetaInfo(props.etiqueta))
const mostrarSelo = computed(() => !!info.value && !(props.esconderPosVenda && !info.value.destaque))
const outras = computed(() => secundariasDe(props.etiqueta, props.secundarias))
const titulo = computed(() => tituloDaEtiqueta(props.etiqueta, { desde: props.desde, manual: props.manual, secundarias: props.secundarias, estrelas: props.estrelas }))
const clsSelo = computed(() => (props.selecionada ? 'bg-white/20 text-current' : clsDoSelo(props.etiqueta, props.estrelas)))
// As estrelas só no selo da Avaliação (com outra etiqueta na frente, a
// Avaliação é o indicador pequeno e a nota vai no title dele).
const estrelasSelo = computed(() => (info.value?.value === 'avaliacao' ? estrelasDaNota(props.estrelas) : ''))
const seloNotaBaixa = computed(() => !!estrelasSelo.value && notaBaixa(props.estrelas))
function tituloSecundaria(o: EtiquetaInfo) {
  const nota = o.value === 'avaliacao' && estrelasDaNota(props.estrelas) ? ` (pior nota ${props.estrelas} de 5)` : ''
  return `também aberta: ${o.label}${nota}`
}
const podeEditar = computed(() => props.editavel && !!props.conversaId)

// ─── troca à mão ────────────────────────────────────────────────────────────
const { api } = useApi()
const aberto = ref(false)
const escolhida = ref<string>('')
const motivo = ref('')
const salvando = ref(false)
const erro = ref<string | null>(null)
const caixaRef = ref<HTMLElement | null>(null)
onClickOutside(caixaRef, () => { if (!salvando.value) aberto.value = false })

function abrir() {
  if (!podeEditar.value) return
  escolhida.value = info.value?.value || ''
  motivo.value = ''
  erro.value = null
  aberto.value = !aberto.value
}

async function salvar() {
  if (!podeEditar.value || !escolhida.value || salvando.value) return
  salvando.value = true
  erro.value = null
  try {
    const r = await api<EtiquetaTroca>(`/api/atendimento/conversas/${encodeURIComponent(props.conversaId!)}/etiqueta`, {
      method: 'POST',
      body: { etiqueta: escolhida.value, motivo: motivo.value.trim() || null },
    })
    emit('trocada', r)
    aberto.value = false
  } catch (e) {
    const er = erroDaApi(e, 'Não consegui trocar a etiqueta')
    erro.value = [er.texto, ...er.motivos].join(' — ')
  } finally {
    salvando.value = false
  }
}

function quando(em: string | null) {
  if (!em) return ''
  const d = new Date(em)
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
}
const linhaDoTempo = computed(() => (props.historico || []).slice().reverse())
</script>

<template>
  <span v-if="mostrarSelo || outras.length || podeEditar" ref="caixaRef" class="relative inline-flex shrink-0 items-center gap-1">
    <component
      :is="podeEditar ? 'button' : 'span'"
      v-if="mostrarSelo || podeEditar"
      :type="podeEditar ? 'button' : undefined"
      class="inline-flex items-center gap-0.5 rounded px-1.5 py-px text-[10px] font-medium leading-4"
      :class="[info ? clsSelo : 'border border-dashed text-muted-foreground', podeEditar ? 'hover:ring-1 hover:ring-primary/40' : '']"
      :title="podeEditar ? `${titulo || 'sem etiqueta'} — clique para trocar à mão` : titulo"
      :aria-haspopup="podeEditar ? 'dialog' : undefined"
      :aria-expanded="podeEditar ? aberto : undefined"
      data-etiqueta
      @click.stop="abrir"
    >
      {{ info ? info.label : 'sem etiqueta' }}
      <span
        v-if="estrelasSelo"
        class="tracking-tighter"
        :class="seloNotaBaixa && !selecionada ? 'font-semibold text-red-700 dark:text-red-800' : ''"
        :aria-label="`nota ${estrelas} de 5`"
        data-estrelas
      >{{ estrelasSelo }}</span>
      <Hand v-if="manual" class="size-3" aria-label="trocada à mão" />
    </component>
    <!-- O indicador pequeno: as outras abertas ao mesmo tempo. -->
    <span
      v-for="o in outras"
      :key="o.value"
      class="inline-block size-2 rounded-full ring-1 ring-background"
      :class="o.ponto"
      :title="tituloSecundaria(o)"
      :aria-label="tituloSecundaria(o)"
      data-secundaria
    />

    <div
      v-if="aberto && podeEditar"
      role="dialog"
      aria-label="trocar a etiqueta"
      class="absolute left-0 top-full z-40 mt-1 w-72 space-y-2 rounded-md border bg-background p-2 text-xs text-foreground shadow-lg"
      @click.stop
      @keydown.esc="aberto = false"
    >
      <div class="font-medium">Trocar a etiqueta</div>
      <p class="text-[11px] text-muted-foreground">
        Vale até o próximo acontecimento automático (reclamação, devolução, mudança no Bling). Fica na linha do tempo com o seu nome. Nada é enviado à plataforma.
      </p>
      <div class="flex flex-wrap gap-1" role="radiogroup" aria-label="etiqueta">
        <button
          v-for="o in OPCOES_ETIQUETA"
          :key="o.value"
          type="button"
          role="radio"
          :aria-checked="escolhida === o.value"
          class="inline-flex items-center gap-0.5 rounded px-1.5 py-0.5 text-[11px]"
          :class="[o.cls, escolhida === o.value ? 'ring-2 ring-primary' : 'opacity-80 hover:opacity-100']"
          :title="o.hint"
          @click="escolhida = o.value"
        >
          <Check v-if="escolhida === o.value" class="size-3" /> {{ o.label }}
        </button>
      </div>
      <input
        v-model="motivo"
        maxlength="200"
        class="h-7 w-full rounded border bg-background px-2 text-xs"
        placeholder="por quê? (opcional — vai para a linha do tempo)"
        aria-label="motivo da troca"
        @keydown.enter.prevent="salvar"
      />
      <div v-if="erro" class="rounded border border-red-500/40 bg-red-500/10 px-2 py-1 text-[11px] text-red-600 dark:text-red-400">{{ erro }}</div>
      <div class="flex items-center justify-end gap-1.5">
        <button type="button" class="rounded px-2 py-1 hover:bg-muted" :disabled="salvando" @click="aberto = false">cancelar</button>
        <button
          type="button"
          class="inline-flex items-center gap-1 rounded bg-primary px-2 py-1 text-primary-foreground disabled:opacity-60"
          :disabled="salvando || !escolhida"
          @click="salvar"
        >
          <Loader2 v-if="salvando" class="size-3 animate-spin" /> trocar
        </button>
      </div>
      <div v-if="linhaDoTempo.length" class="border-t pt-1.5">
        <div class="mb-1 flex items-center gap-1 text-[11px] font-medium text-muted-foreground"><History class="size-3" /> Linha do tempo da etiqueta</div>
        <ol class="max-h-40 space-y-1 overflow-y-auto">
          <li v-for="h in linhaDoTempo" :key="h.id" class="text-[11px] leading-4" data-historico>
            <span class="tabular-nums text-muted-foreground">{{ quando(h.em) }}</span>
            {{ h.de_rotulo || 'sem etiqueta' }} → <span class="font-medium">{{ h.para_rotulo }}</span>
            <span v-if="h.por_nome" class="text-muted-foreground"> · {{ h.por_nome }}</span>
            <div v-if="h.motivo" class="text-muted-foreground">{{ h.motivo }}</div>
          </li>
        </ol>
      </div>
    </div>
  </span>
</template>
