<script lang="ts">
// A devolução do TikTok INTEIRA na aba Pedido (18/11/2026). O dono comparou o
// mesmo pedido no Duoke e no DaVinci e quer o nosso "tão completo quanto o
// Duoke": o produto em devolução (foto, variação, SKU, preço com o cheio
// riscado, quantidade e o selo do que o comprador pede), o montante do
// reembolso, a logística da volta, o motivo e o HISTÓRICO DE REEMBOLSO com data
// e hora — o texto do comprador ("outras informações"), as fotos e o vídeo, que
// abrem grandes. E, no mesmo dia, "se der p eles responder a devolução por ali
// tbm" (escolheu tudo, inclusive o pacote): os botões de resposta, com a
// confirmação na janela AtendimentoDevolucaoResponder.
//
// Lido NA HORA no TikTok (GET .../reclamacoes/{id}/detalhe; o link do vídeo
// vence em ~1 h, o servidor guarda só 5 min). Sem a leitura (TikTok fora do
// ar), o bloco fica com o que o cartão já tem e diz por quê, com "tentar de
// novo". Nada do comprador é guardado no banco.
//
// ACESSIBILIDADE (18/11/2026, o dono: "acessibilidade"): botão travado continua
// no Tab (`aria-disabled`) e diz o porquê em texto ligado a ele; cada foto e o
// vídeo têm nome ("abrir vídeo 1 do comprador, 0:22"); o histórico é uma lista
// com `aria-controls` no botão que abre e fecha; datas em <time>; o preço cheio
// riscado é lido como "preço cheio"; "lendo…" e a falha em `role="status"`.
//
// Este bloco (não o setup) é o módulo dos ajudantes puros, testados em
// tests/atendimento-devolucao-tiktok.cjs — por isso não importa nada.

export interface DevolucaoAcao {
  codigo: string
  rotulo: string
  recusa: boolean
  exige_foto: boolean
  efeito: string
  bloqueio: string | null
  prazo: string | null
}
export interface DevolucaoItem {
  nome: string | null
  variacao: string | null
  sku: string | null
  foto: string | null
  preco: number | null
  preco_original: number | null
  moeda: string | null
  quantidade: number
}
export interface DevolucaoMidia { url: string; capa?: string | null; duracao_s?: number | null }
export interface DevolucaoEvento {
  quando: string | null
  papel: string | null
  quem: string | null
  evento: string | null
  titulo: string
  motivo: string | null
  texto: string | null
  fotos: DevolucaoMidia[]
  videos: DevolucaoMidia[]
}
export interface DevolucaoDetalhe {
  reclamacao_id: string
  ao_vivo: boolean
  erro: string | null
  erro_texto: string | null
  lido_em: string | null
  status: string | null
  status_rotulo: string | null
  tipo_caso: string | null
  solucao: string | null
  motivo: string | null
  reembolso: { total: number; subtotal: number | null; frete: number | null; moeda: string | null } | null
  reembolso_rapido: boolean
  reembolso_pago_em: string | null
  volta: { rastreio: string | null; transportadora: string | null; frete_loja: number | null; moeda: string | null } | null
  itens: DevolucaoItem[]
  acao_pendente: string | null
  prazo: string | null
  arbitragem: string | null
  aberto_em: string | null
  acoes: DevolucaoAcao[]
  acoes_ligadas: boolean
  historico: DevolucaoEvento[]
}
export type MidiaAberta = { tipo: 'imagem' | 'video'; url: string; nome: string }

// O selo do Duoke ao lado do produto: o que o comprador pediu.
export function seloDaSolucao(tipoCaso: string | null | undefined): string | null {
  const t = String(tipoCaso || '').toUpperCase()
  if (t === 'RETURN_AND_REFUND') return 'Devolução e Reembolso'
  if (t === 'REFUND') return 'Só reembolso'
  if (t === 'REPLACEMENT') return 'Troca'
  return null
}

// 21,8 s → "0:22"; sem duração, vazio.
export function duracaoVideo(s: number | null | undefined): string {
  if (typeof s !== 'number' || !Number.isFinite(s) || s <= 0) return ''
  const total = Math.round(s)
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`
}

// As fotos e os vídeos do evento, na ordem do Duoke (fotos, depois o vídeo),
// com o nome que o visor mostra e o que o leitor de tela fala no botão. Só
// https (o servidor já filtra; aqui é a rede).
export function midiasDoEvento(e: Pick<DevolucaoEvento, 'fotos' | 'videos' | 'quem'>): (MidiaAberta & { capa: string | null; duracao: string; rotulo: string })[] {
  const quem = (e.quem || 'comprador').toLowerCase()
  const https = (u: unknown) => typeof u === 'string' && /^https:\/\//.test(u)
  const fotos = (e.fotos || []).filter((f) => https(f?.url)).map((f, i) => {
    const nome = `foto ${i + 1} do ${quem}`
    return { tipo: 'imagem' as const, url: f.url, nome, capa: f.url, duracao: '', rotulo: `abrir ${nome}` }
  })
  const videos = (e.videos || []).filter((v) => https(v?.url)).map((v, i) => {
    const nome = `vídeo ${i + 1} do ${quem}`
    const duracao = duracaoVideo(v.duracao_s)
    return {
      tipo: 'video' as const, url: v.url, nome, capa: https(v.capa) ? (v.capa as string) : null, duracao,
      rotulo: `abrir ${nome}${duracao ? `, ${duracao}` : ''}`,
    }
  })
  return [...fotos, ...videos]
}

// Por que o botão da resposta não pode ser clicado (null = pode).
export function porQueTravada(
  a: Pick<DevolucaoAcao, 'bloqueio'>,
  { acoesLigadas, podeResponder }: { acoesLigadas: boolean; podeResponder: boolean },
): string | null {
  if (!podeResponder) return 'Só quem responde no Atendimento decide a devolução.'
  if (a.bloqueio) return a.bloqueio
  if (!acoesLigadas) return 'As respostas de devolução pelo DaVinci ainda estão desligadas (o dono liga quando for acompanhar a primeira).'
  return null
}

// O reembolso já saiu? Pela linha do tempo (REFUND_SUCCESS) — é o que o Duoke
// mostra como "Reembolso concluído", mesmo com o caso ainda aberto pelo pacote.
export function reembolsoJaPago(d: Pick<DevolucaoDetalhe, 'reembolso_pago_em' | 'status'> | null | undefined): boolean {
  if (!d) return false
  if (d.reembolso_pago_em) return true
  return ['RETURN_OR_REFUND_REQUEST_SUCCESS', 'RETURN_OR_REFUND_REQUEST_COMPLETE'].includes(String(d.status || '').toUpperCase())
}

// "06/11/2026 21:50" (com o ano, como o Duoke) no fuso de quem vê.
export function dataHoraComAno(iso: string | null | undefined): string {
  if (!iso) return ''
  const t = Date.parse(iso)
  if (!Number.isFinite(t)) return ''
  return new Date(t).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' }).replace(',', '')
}
</script>

<script setup lang="ts">
import { ChevronDown, ChevronRight, Copy, ExternalLink, Loader2, Play, RotateCcw, Undo2 } from 'lucide-vue-next'
import type { PerfilAdsPower } from '~/components/AtendimentoAdsPower.vue'
import { copiar, erroDaApi, fmtDataHora, fmtDinheiro, useRelogio } from '~/components/AtendimentoPlataforma.vue'
import {
  abrirEm,
  corDoTipo,
  motivoLegivel,
  prazoReclamacao,
  statusLegivel,
  type Reclamacao,
} from '~/components/AtendimentoReclamacao.vue'

const props = withDefaults(defineProps<{
  r: Reclamacao
  conversaId: string
  // O perfil do AdsPower da loja: "Abrir no TikTok" abre nele.
  perfil?: PerfilAdsPower | null
  // Quem responde no Atendimento (o `canEdit` da caixa): sem isso, os botões travam.
  podeResponder?: boolean
}>(), { perfil: null, podeResponder: false })
const emit = defineEmits<{
  // A foto ou o vídeo do comprador, grande (o visor mora na conversa).
  (e: 'abrirMidia', m: MidiaAberta): void
  // A resposta saiu: a conversa relê o cartão e as reclamações.
  (e: 'respondido'): void
}>()
const { api } = useApi()
const toasts = useToasts()
const agora = useRelogio()

const detalhe = ref<DevolucaoDetalhe | null>(null)
const carregando = ref(false)
const falhou = ref('')
let geracao = 0
const url = computed(() => `/api/atendimento/conversas/${encodeURIComponent(props.conversaId)}/reclamacoes/${encodeURIComponent(props.r.id)}`)

async function carregar(atualizar = false) {
  const minha = ++geracao
  carregando.value = true
  falhou.value = ''
  try {
    const d = await api<DevolucaoDetalhe>(`${url.value}/detalhe${atualizar ? '?atualizar=1' : ''}`)
    if (minha !== geracao) return
    detalhe.value = d
    if (!d.ao_vivo) falhou.value = d.erro_texto || 'Não deu para ler o histórico no TikTok agora.'
  } catch (e) {
    if (minha !== geracao) return
    falhou.value = erroDaApi(e, 'Não deu para ler o histórico no TikTok agora.').texto
  } finally {
    if (minha === geracao) carregando.value = false
  }
}
watch(() => `${props.conversaId}:${props.r.id}`, () => {
  detalhe.value = null
  void carregar()
}, { immediate: true })

// O que vale: a leitura de agora; sem ela, o que o cartão já tem.
const d = computed(() => (detalhe.value?.ao_vivo ? detalhe.value : null))
const status = computed(() => d.value?.status_rotulo || statusLegivel(props.r))
const motivo = computed(() => d.value?.motivo || (props.r.motivo ? motivoLegivel(props.r.motivo) : ''))
const solucao = computed(() => d.value?.solucao || props.r.solucao || '')
const selo = computed(() => seloDaSolucao(d.value?.tipo_caso))
const prazoIso = computed(() => (d.value ? d.value.prazo : props.r.prazo_em))
const prazo = computed(() => prazoReclamacao({ aberta: !!prazoIso.value, prazo_em: prazoIso.value }, agora.value))
const restante = computed(() => (prazo.value?.texto || '').replace(/^faltam /, ''))
const acaoPendente = computed(() => (d.value ? d.value.acao_pendente : props.r.acao_pendente))
const reembolso = computed(() => {
  const v = d.value?.reembolso
  return v && v.total > 0 ? `-${fmtDinheiro(v.total, v.moeda)}` : ''
})
const reembolsoDetalhe = computed(() => {
  const v = d.value?.reembolso
  if (!v) return ''
  const partes = []
  if (v.subtotal) partes.push(`produto ${fmtDinheiro(v.subtotal, v.moeda)}`)
  if (v.frete) partes.push(`frete ${fmtDinheiro(v.frete, v.moeda)}`)
  return partes.join(' + ')
})
const rastreio = computed(() => d.value?.volta?.rastreio || props.r.rastreio_volta || '')
const pago = computed(() => reembolsoJaPago(d.value))
const acoes = computed(() => d.value?.acoes || [])
const historico = computed(() => d.value?.historico || [])
const historicoAberto = ref(true)
// Ids para ligar botão ↔ porquê e o botão do histórico ↔ a lista (acessibilidade).
const uid = `dev-tt-${useId()}`
const idHistorico = `${uid}-historico`
const idPorque = (codigo: string) => `${uid}-porque-${codigo}`

async function copiarTexto(t: string, rotulo: string) {
  if (await copiar(t)) toasts.success(`${rotulo} copiado`)
  else window.prompt(rotulo, t)
}
function travada(a: DevolucaoAcao): string | null {
  return porQueTravada(a, { acoesLigadas: !!d.value?.acoes_ligadas, podeResponder: props.podeResponder })
}

// A janela da resposta (confirmação, motivo, comentário e fotos).
const respondendo = ref<DevolucaoAcao | null>(null)
function abrirResposta(a: DevolucaoAcao) {
  if (travada(a)) return
  respondendo.value = a
}
function aoResponder(novo: DevolucaoDetalhe) {
  const a = respondendo.value
  respondendo.value = null
  detalhe.value = novo
  falhou.value = novo.ao_vivo ? '' : (novo.erro_texto || '')
  toasts.success(`${a?.rotulo || 'Resposta'}: enviado ao TikTok`)
  emit('respondido')
}
</script>

<template>
  <section
    class="space-y-2 rounded-md border p-2.5 text-xs"
    :class="corDoTipo(r.tipo).borda"
    :aria-label="`${r.tipo_rotulo} deste pedido no TikTok`"
    :data-devolucao-pedido="r.id"
    data-devolucao-tiktok
  >
    <div class="flex flex-wrap items-center gap-1.5">
      <span class="inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-px font-semibold" :class="corDoTipo(r.tipo).selo">
        <Undo2 class="size-3.5" aria-hidden="true" />
        {{ r.tipo_rotulo }}
      </span>
      <span v-if="status" class="min-w-0 text-[13px] font-semibold" data-devolucao-status>{{ status }}</span>
      <span v-if="!r.aberta" class="rounded bg-muted px-1.5 py-px text-muted-foreground">encerrada</span>
    </div>
    <div class="flex items-center gap-1">
      <span class="text-muted-foreground">Nº do reembolso</span>
      <span class="min-w-0 break-all font-mono">{{ r.numero }}</span>
      <button v-if="r.numero" type="button" class="shrink-0 rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground" title="copiar o nº do reembolso" aria-label="copiar o nº do reembolso" @click="copiarTexto(r.numero!, 'Nº do reembolso')"><Copy class="size-3" /></button>
    </div>

    <!-- o reembolso já saiu (mesmo com o caso aberto pelo pacote) -->
    <div v-if="pago" class="rounded-md border border-emerald-600/30 bg-emerald-500/10 px-2 py-1.5 text-emerald-800 dark:text-emerald-200" data-reembolso-pago>
      <span class="font-semibold">Reembolso concluído</span>
      <template v-if="d?.reembolso_pago_em"> em {{ dataHoraComAno(d.reembolso_pago_em) }}</template>
      <template v-if="d?.reembolso_rapido"> — reembolso rápido do TikTok: o comprador recebeu assim que postou o pacote</template>.
      <template v-if="r.aberta && prazo && acaoPendente"> O caso segue aberto até a loja responder o pacote ({{ restante }}).</template>
    </div>

    <!-- o produto em devolução (a linha do Duoke) -->
    <ul v-if="d?.itens?.length" class="space-y-1.5" aria-label="produto em devolução">
      <li v-for="(it, i) in d.itens" :key="`${it.sku}-${i}`" class="flex gap-2 rounded-md border bg-muted/30 p-1.5" data-devolucao-item>
        <button
          v-if="it.foto"
          type="button"
          class="size-12 shrink-0 overflow-hidden rounded border bg-background"
          :title="`ver a foto de ${it.nome || 'produto'}`"
          :aria-label="`ver a foto do produto ${it.variacao || ''}`.trim()"
          @click="emit('abrirMidia', { tipo: 'imagem', url: it.foto!, nome: it.nome || 'produto' })"
        >
          <img :src="it.foto" alt="" loading="lazy" referrerpolicy="no-referrer" class="size-full object-cover" />
        </button>
        <div class="min-w-0 flex-1 space-y-0.5">
          <div class="line-clamp-2 font-medium leading-snug" :title="it.nome || ''">{{ it.nome || 'Produto' }}</div>
          <div v-if="it.variacao" class="text-muted-foreground">Variação: <span class="text-foreground">{{ it.variacao }}</span></div>
          <div class="flex items-center gap-2">
            <span v-if="it.sku" class="min-w-0 truncate text-muted-foreground">SKU: <span class="font-mono text-foreground">{{ it.sku }}</span></span>
            <span class="ml-auto shrink-0 tabular-nums">× {{ it.quantidade }}</span>
          </div>
          <div class="flex flex-wrap items-center gap-x-1.5">
            <span v-if="it.preco !== null" class="font-semibold tabular-nums">{{ fmtDinheiro(it.preco, it.moeda) }}</span>
            <s v-if="it.preco_original" class="tabular-nums text-muted-foreground" title="preço cheio, antes dos descontos"><span class="sr-only">preço cheio: </span>{{ fmtDinheiro(it.preco_original, it.moeda) }}</s>
            <span v-if="selo" class="ml-auto text-red-600 dark:text-red-400" data-devolucao-selo>{{ selo }}</span>
          </div>
        </div>
      </li>
    </ul>

    <dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5">
      <template v-if="reembolso">
        <dt class="text-muted-foreground">Montante do reembolso</dt>
        <dd class="text-right font-semibold tabular-nums text-red-600 dark:text-red-400" :title="reembolsoDetalhe || undefined" data-devolucao-montante>{{ reembolso }}</dd>
      </template>
      <template v-if="rastreio || d?.volta?.transportadora">
        <dt class="text-muted-foreground">Logística da volta</dt>
        <!-- O rastreio inteiro (é o que a equipe copia); a transportadora embaixo. -->
        <dd class="min-w-0 text-right">
          <span v-if="rastreio" class="inline-flex items-center gap-1">
            <span class="break-all font-mono text-primary" data-devolucao-rastreio>{{ rastreio }}</span>
            <button type="button" class="shrink-0 rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground" title="copiar o rastreio da volta" aria-label="copiar o rastreio da volta" @click="copiarTexto(rastreio, 'Rastreio')"><Copy class="size-3" /></button>
          </span>
          <span v-if="d?.volta?.transportadora" class="block text-muted-foreground">{{ d.volta.transportadora }}</span>
        </dd>
      </template>
      <template v-if="d?.volta?.frete_loja">
        <dt class="text-muted-foreground">Frete da volta (loja paga)</dt>
        <dd class="text-right tabular-nums">{{ fmtDinheiro(d.volta.frete_loja, d.volta.moeda) }}</dd>
      </template>
      <template v-if="motivo">
        <dt class="text-muted-foreground">Motivo</dt>
        <dd class="text-right">{{ motivo }}</dd>
      </template>
      <template v-if="solucao">
        <dt class="text-muted-foreground">Pede</dt>
        <dd class="text-right">{{ solucao }}</dd>
      </template>
      <template v-if="r.aberta && prazo">
        <dt class="text-muted-foreground">Restante</dt>
        <dd class="text-right">
          <span class="rounded px-1.5 py-px font-medium tabular-nums" :class="prazo.cls">{{ restante }}</span>
          <span class="ml-1 whitespace-nowrap text-muted-foreground">até {{ fmtDataHora(prazoIso).replace(',', '') }}</span>
        </dd>
      </template>
    </dl>
    <div v-if="r.aberta && acaoPendente" class="font-medium">{{ acaoPendente }}</div>

    <!-- as respostas da loja (18/11/2026) -->
    <div v-if="acoes.length" class="space-y-1" data-devolucao-acoes>
      <div class="flex flex-wrap gap-1.5" role="group" aria-label="Responder a devolução no TikTok">
        <!-- Travado continua no Tab (`aria-disabled`) e diz o porquê, ligado a ele. -->
        <button
          v-for="a in acoes"
          :key="a.codigo"
          type="button"
          class="rounded-md border px-2 py-1 font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
          :class="[a.recusa ? 'border-red-500/50 text-red-700 hover:bg-red-500/10 dark:text-red-300' : 'border-emerald-600/50 text-emerald-700 hover:bg-emerald-500/10 dark:text-emerald-300', travada(a) ? 'cursor-not-allowed opacity-50' : '']"
          :aria-disabled="!!travada(a)"
          :aria-describedby="travada(a) ? idPorque(a.codigo) : undefined"
          :title="travada(a) || a.efeito"
          :data-acao="a.codigo"
          @click="abrirResposta(a)"
        >{{ a.rotulo }}</button>
      </div>
      <p v-for="a in acoes.filter((x) => travada(x))" :id="idPorque(a.codigo)" :key="`p-${a.codigo}`" class="text-[11px] text-muted-foreground" :data-acao-porque="a.codigo">{{ a.rotulo }}: {{ travada(a) }}</p>
    </div>

    <!-- histórico de reembolso (a linha do tempo do Duoke) -->
    <div v-if="historico.length" class="rounded-md border" data-devolucao-historico>
      <button type="button" class="flex w-full items-center gap-1 px-2 py-1.5 text-left font-semibold hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary" :aria-expanded="historicoAberto" :aria-controls="idHistorico" @click="historicoAberto = !historicoAberto">
        <ChevronDown v-if="historicoAberto" class="size-3.5" aria-hidden="true" />
        <ChevronRight v-else class="size-3.5" aria-hidden="true" />
        Histórico de reembolso
        <span class="ml-auto font-normal text-muted-foreground">{{ historico.length }} {{ historico.length === 1 ? 'evento' : 'eventos' }}</span>
      </button>
      <ol v-show="historicoAberto" :id="idHistorico" class="relative mx-2 mb-2 ml-3.5 border-l border-primary/40 pl-3" aria-label="Histórico de reembolso">
        <li v-for="(e, i) in historico" :key="`${e.quando}-${i}`" class="relative pb-2.5 last:pb-0" data-evento>
          <span class="absolute -left-[17px] top-1 size-2 rounded-full bg-primary ring-2 ring-card" aria-hidden="true" />
          <div class="font-semibold leading-snug">{{ e.titulo }}</div>
          <div v-if="e.motivo" class="mt-0.5"><span class="text-muted-foreground">Motivo do reembolso: </span>{{ e.motivo }}</div>
          <div v-if="e.texto" class="mt-0.5 whitespace-pre-wrap break-words" data-evento-texto><span class="text-muted-foreground">{{ e.papel === 'BUYER' ? 'Outras informações' : 'Comentário' }}: </span>{{ e.texto }}</div>
          <div v-if="midiasDoEvento(e).length" class="mt-1 flex flex-wrap gap-1" data-evento-midias>
            <button
              v-for="m in midiasDoEvento(e)"
              :key="m.url"
              type="button"
              class="relative size-12 overflow-hidden rounded border bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
              :title="m.rotulo"
              :aria-label="m.rotulo"
              :data-midia="m.tipo"
              @click="emit('abrirMidia', { tipo: m.tipo, url: m.url, nome: m.nome })"
            >
              <img v-if="m.capa" :src="m.capa" alt="" loading="lazy" referrerpolicy="no-referrer" class="size-full object-cover" />
              <span v-if="m.tipo === 'video'" class="absolute inset-0 flex flex-col items-center justify-center bg-black/35 text-white" aria-hidden="true">
                <Play class="size-4" aria-hidden="true" />
                <span v-if="m.duracao" class="text-[10px] tabular-nums">{{ m.duracao }}</span>
              </span>
            </button>
          </div>
          <div class="mt-0.5 tabular-nums text-muted-foreground"><time v-if="e.quando" :datetime="e.quando">{{ dataHoraComAno(e.quando) }}</time><template v-if="e.quem"> · {{ e.quem }}</template></div>
        </li>
      </ol>
    </div>

    <!-- lendo / falhou / lido em -->
    <div role="status" aria-live="polite">
      <div v-if="carregando && !detalhe" class="flex items-center gap-1.5 text-[11px] text-muted-foreground">
        <Loader2 class="size-3.5 animate-spin" aria-hidden="true" /> lendo o histórico no TikTok…
      </div>
      <div v-else-if="falhou" class="flex items-center gap-1.5 rounded-md border border-dashed px-2 py-1.5 text-[11px] text-muted-foreground" data-devolucao-falhou>
        <span class="min-w-0 flex-1">{{ falhou }}</span>
        <button type="button" class="shrink-0 rounded border px-1.5 py-0.5 hover:bg-muted" :disabled="carregando" @click="carregar(true)">tentar de novo</button>
      </div>
    </div>
    <div class="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
      <span v-if="d?.lido_em">lido no TikTok às {{ fmtDataHora(d.lido_em).split(', ').pop() }}</span>
      <button v-if="d" type="button" class="inline-flex items-center gap-1 rounded border px-1.5 py-0.5 hover:bg-muted disabled:opacity-50" :disabled="carregando" @click="carregar(true)">
        <RotateCcw class="size-3" :class="{ 'animate-spin': carregando }" aria-hidden="true" /> atualizar
      </button>
      <AtendimentoAbrirPlataforma
        v-if="r.url_plataforma"
        :href="r.url_plataforma"
        :perfil="perfil"
        :conversa-id="conversaId"
        class="ml-auto inline-flex items-center gap-1 rounded border bg-background px-2 py-0.5 text-foreground hover:bg-muted"
      >
        <ExternalLink class="size-3.5" aria-hidden="true" />
        {{ abrirEm(r.plataforma, r.plataforma_nome) }}
      </AtendimentoAbrirPlataforma>
    </div>

    <AtendimentoDevolucaoResponder
      v-if="respondendo"
      :acao="respondendo"
      :url="url"
      :numero="r.numero || ''"
      @fechar="respondendo = null"
      @respondido="aoResponder"
    />
  </section>
</template>
