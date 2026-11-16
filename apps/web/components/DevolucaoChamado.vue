<script setup lang="ts">
// Bloco "2 · Chamado" da janela do painel de Devoluções (2ª etapa, Vinicius
// 16/11/2026): "Abrir chamado: Sim ou Não", a conferência da IA antes de a
// disputa sair ("se não bate, segurar e perguntar"), a observação que vai no
// texto pra plataforma, a prévia desse texto e a "Instrução IA".
// Tudo é do PEDIDO (o chamado é um por pedido) — API em /api/devolucoes-painel.
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ExternalLink, Loader2 } from 'lucide-vue-next'

type Instrucao = { texto: string; autor_nome: string | null; em: string | null; lida: boolean; esperando_chamado: boolean }
type Conferencia = {
  resultado?: 'bate' | 'nao_bate' | 'aceito' | 'erro'
  motivo_sugerido?: string
  condicao_sugerida?: string
  explicacao?: string
  motivo_lancado?: string
  condicao_lancada?: string
  decidido_por?: string
  decisao?: string
}
type Janela = {
  pedido_bling: string
  pede_chamado: boolean
  motivo: string | null
  abrir_chamado: boolean | null
  chamado_id: string | null
  numero: string | null
  plataforma: string | null
  status_plataforma: string | null
  resolvido: boolean
  abertura_status: string | null
  abertura_erro: string | null
  enviada_at: string | null
  conferencia: Conferencia | null
  conferencia_ligada: boolean
  instrucoes: Instrucao[]
}
type LinhaPrevia = { sku: string | null; produtos: string | null; quantidade: number; motivo_devolucao: string | null; observacao: string | null }
type Previa = { abre: boolean; motivo: string | null; texto: string | null; avisos: string[] }

const props = defineProps<{
  pedido: string
  plataforma: string
  canEdit: boolean
  // As peças como estão na tela agora (lançadas e por lançar): a prévia usa.
  linhas: LinhaPrevia[]
  linkEnvio: string
  linkAbertura: string
  fotos: number
  // A observação que vai no texto do chamado (uma só pro pedido).
  observacao: string
  // Prazo da plataforma, pronto pra ler ("18/11 às 13:40 · responder").
  prazo: string
  // Os produtos do pedido tinham observações diferentes (juntadas aqui).
  obsMisturadas?: boolean
}>()
const emit = defineEmits<{
  (e: 'observacao', texto: string): void
  // Decidiu algo que muda o pedido (motivo trocado, chamado encerrado…): a janela relê.
  (e: 'mudou'): void
}>()

const { api } = useApi()
const toasts = useToasts()

const MOTIVOS_ABREM = ['bloqueado', 'mudou de ideia', 'golpe', 'item faltando', 'não recebido', 'danificado (outros)']
const BOTAO = 'rounded-md border bg-background px-3 py-1.5 text-xs font-semibold hover:bg-muted disabled:opacity-50'
const BOTAO_PRINCIPAL = 'rounded-md bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground disabled:opacity-50'

const janela = ref<Janela | null>(null)
const carregando = ref(false)
const ocupado = ref('')
const previa = ref<Previa | null>(null)
const previaCarregando = ref(false)
const instrucao = ref('')
const obs = ref(props.observacao || '')
watch(() => props.observacao, (v) => { obs.value = v || '' })

function apiError(e: any): string {
  const d = e?.data?.detail
  if (d && typeof d === 'object') return d.message || d.code || 'erro'
  return d || e?.message || 'erro'
}
const MSG: Record<string, string> = {
  chamado_ja_saiu: 'A contestação já saiu na plataforma — não dá pra desfazer daqui.',
  nada_segurado: 'Não há contestação segurada neste pedido (alguém já decidiu?).',
  motivo_nao_abre_chamado: 'Esse motivo não abre chamado.',
  chamado_concluido: 'O chamado já foi concluído: reabra na aba Chamados pra instruir a IA.',
  chamado_encerrado: 'O chamado já está encerrado: não há o que mandar.',
  chamado_sendo_enviado: 'A contestação está sendo enviada agora — espere alguns segundos e veja de novo.',
  link_envio_obrigatorio: 'Com esse motivo, mala e eletro precisam do vídeo do envio: cole o link em Provas (Vídeo do envio) e tente de novo.',
  video_obrigatorio: 'Pedido em Fraude: precisa do vídeo do envio. Cole o link em Provas e tente de novo.',
}

let carga = 0
async function recarregar() {
  if (!props.pedido) return
  const minha = ++carga
  carregando.value = true
  try {
    const j = await api<Janela>(`/api/devolucoes-painel/${encodeURIComponent(props.pedido)}/chamado`)
    if (minha === carga) janela.value = j
  } catch (e: any) {
    if (minha === carga) toasts.push({ kind: 'error', title: 'Não deu pra ler o chamado', lines: [apiError(e)] })
  } finally {
    if (minha === carga) carregando.value = false
  }
}

const saiu = computed(() => ['enviada', 'enviando'].includes(janela.value?.abertura_status || ''))
const segurada = computed(() => janela.value?.abertura_status === 'segurada')
const cancelada = computed(() => janela.value?.abertura_status === 'registrada' && janela.value?.abertura_erro === 'contestacao_cancelada')
const abrir = computed(() => janela.value?.abrir_chamado !== false)
const conf = computed(() => janela.value?.conferencia || null)
const motivoAtual = computed(() => previa.value?.motivo || janela.value?.motivo || null)
const podeTrocar = computed(() => {
  const m = (conf.value?.motivo_sugerido || '').toLowerCase()
  return !!m && MOTIVOS_ABREM.includes(m) && m !== (conf.value?.motivo_lancado || '').toLowerCase()
})

function quando(v: string | null): string {
  if (!v) return ''
  const d = new Date(v)
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }).replace(',', ' às')
}

async function chamar(chave: string, url: string, body: Record<string, unknown>, ok?: string): Promise<boolean> {
  if (ocupado.value) return false
  ocupado.value = chave
  try {
    janela.value = await api<Janela>(url, { method: 'POST', body })
    if (ok) toasts.push({ kind: 'success', title: ok, lines: [] }, 3000)
    return true
  } catch (e: any) {
    const d = e?.data?.detail
    const code = d && typeof d === 'object' ? d.code : ''
    toasts.push({ kind: 'error', title: 'Não foi', lines: [MSG[code] || apiError(e)] }, 8000)
    return false
  } finally {
    ocupado.value = ''
  }
}
const base = computed(() => `/api/devolucoes-painel/${encodeURIComponent(props.pedido)}`)

async function marcarAbrir(sim: boolean) {
  if (!props.canEdit || saiu.value || sim === abrir.value) return
  if (!sim && !window.confirm('Não abrir chamado neste pedido?\n\nA contestação não vai pra plataforma. Dá pra voltar pro Sim enquanto ela não sair.')) return
  if (await chamar(sim ? 'sim' : 'nao', `${base.value}/abrir-chamado`, { abrir: sim }, sim ? 'Chamado: Sim' : 'Chamado: Não — nada vai pra plataforma')) emit('mudou')
}
async function decidir(acao: 'mandar' | 'trocar' | 'nao_abrir') {
  if (!props.canEdit) return
  const motivo = conf.value?.motivo_sugerido || ''
  const pergunta = acao === 'trocar'
    ? `Trocar o motivo pra “${motivo}” e mandar a contestação?`
    : acao === 'mandar'
      ? 'Mandar a contestação com o motivo como está, mesmo com o aviso da IA?'
      : 'Não abrir chamado? A contestação não vai pra plataforma.'
  if (!window.confirm(pergunta)) return
  const ok = await chamar(acao, `${base.value}/chamado/decisao`, acao === 'trocar' ? { acao, motivo } : { acao },
    acao === 'nao_abrir' ? 'Chamado não aberto' : 'Contestação liberada')
  if (ok) emit('mudou')
}
async function mandarInstrucao() {
  const t = instrucao.value.trim()
  if (!t || !props.canEdit) return
  if (await chamar('instrucao', `${base.value}/instrucao`, { texto: t }, janela.value?.chamado_id ? 'Instrução mandada pra IA' : 'Instrução guardada: entra no chamado quando ele abrir')) {
    instrucao.value = ''
  }
}
function salvarObs() {
  const v = obs.value.trim()
  if (v === (props.observacao || '').trim()) return
  emit('observacao', v)
}

// Prévia do texto: relê quando muda o que vai nele (com um respiro pra não
// chamar a cada tecla).
let timer: ReturnType<typeof setTimeout> | null = null
let cargaPrevia = 0
async function lerPrevia() {
  if (!props.pedido || saiu.value || !abrir.value) {
    previa.value = null
    return
  }
  const minha = ++cargaPrevia
  previaCarregando.value = true
  try {
    const p = await api<Previa>(`${base.value}/chamado/previa`, {
      method: 'POST',
      body: {
        linhas: props.linhas.map((l) => ({ ...l, observacao: obs.value.trim() || l.observacao })),
        plataforma: props.plataforma,
        link_envio: props.linkEnvio || null,
        link_abertura: props.linkAbertura || null,
        fotos: props.fotos,
      },
    })
    if (minha === cargaPrevia) previa.value = p
  } catch {
    if (minha === cargaPrevia) previa.value = null
  } finally {
    if (minha === cargaPrevia) previaCarregando.value = false
  }
}
function agendarPrevia() {
  if (timer) clearTimeout(timer)
  timer = setTimeout(() => { void lerPrevia() }, 500)
}
watch(
  () => [JSON.stringify(props.linhas), props.linkEnvio, props.linkAbertura, props.fotos, props.plataforma, obs.value, saiu.value, abrir.value],
  agendarPrevia,
)
watch(() => props.pedido, () => { janela.value = null; previa.value = null; void recarregar(); agendarPrevia() })

onMounted(() => { void recarregar(); agendarPrevia() })
onBeforeUnmount(() => { if (timer) clearTimeout(timer) })
defineExpose({ recarregar })
</script>

<template>
  <div class="text-xs">
    <div class="mb-2 flex items-center text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
      <span class="mr-1.5 inline-flex size-[18px] items-center justify-center rounded-full bg-[#1f3a5f] text-[10.5px] font-bold text-white">2</span>Chamado
      <span v-if="segurada" class="ml-2 rounded bg-amber-100 px-2 py-0.5 text-[10.5px] normal-case tracking-normal text-amber-800 dark:bg-amber-950/50 dark:text-amber-300">segurado pela conferência</span>
      <span v-else-if="!janela?.chamado_id" class="ml-1 font-normal normal-case tracking-normal">· ainda não aberto</span>
      <Loader2 v-if="carregando" class="ml-1.5 size-3 animate-spin" />
    </div>

    <!-- Estado do chamado -->
    <div v-if="janela?.chamado_id" class="flex flex-wrap items-center gap-2">
      <span class="inline-block rounded px-2 py-0.5 text-[11px] font-semibold" :class="janela.resolvido || janela.status_plataforma === 'encerrado' ? 'bg-muted text-muted-foreground' : 'bg-blue-100 text-blue-800 dark:bg-blue-950/50 dark:text-blue-300'">
        {{ janela.resolvido ? 'Concluído' : janela.status_plataforma === 'encerrado' ? 'Encerrado' : 'Aberto' }}
      </span>
      <span v-if="janela.numero" class="font-mono text-muted-foreground">{{ janela.numero }}</span>
      <a :href="`/chamados?search=${encodeURIComponent(pedido)}`" target="_blank" rel="noopener" class="inline-flex items-center gap-1 rounded-md border px-2 py-1 text-[11px] font-semibold hover:bg-muted"><ExternalLink class="size-3" /> abrir na aba Chamados</a>
    </div>
    <div v-if="janela?.abertura_status === 'enviada'" class="mt-1.5 text-[11px] text-muted-foreground" data-chamado-saiu>
      Saiu pra {{ plataforma }} em <b>{{ quando(janela.enviada_at) }}</b><template v-if="motivoAtual"> · motivo {{ motivoAtual }}</template>
      <span v-if="conf?.resultado === 'bate'" class="text-emerald-700 dark:text-emerald-400"> · conferido pela IA ✓</span>
      <span v-else-if="conf?.resultado === 'aceito'"> · {{ conf.decidido_por || 'alguém' }} decidiu depois do aviso da IA</span>
    </div>
    <div v-else-if="cancelada" class="mt-1.5 text-[11px] text-muted-foreground">Encerrado sem contestar: nada foi pra plataforma.</div>
    <div v-else-if="janela?.abertura_status === 'pendente'" class="mt-1.5 text-[11px] text-muted-foreground">Na fila: a contestação sai sozinha assim que a plataforma liberar.</div>

    <!-- Sim / Não -->
    <div class="mt-2 flex flex-wrap items-center gap-2">
      <span class="font-semibold">Abrir chamado?</span>
      <span class="inline-flex overflow-hidden rounded-md border" :class="!canEdit || saiu ? 'opacity-60' : ''" data-abrir-chamado>
        <button type="button" class="px-3 py-1 text-[11px] font-semibold disabled:cursor-default" :class="abrir ? 'bg-[#1f3a5f] text-white' : 'text-muted-foreground hover:bg-muted'" :disabled="!canEdit || saiu || !!ocupado" @click="marcarAbrir(true)">Sim</button>
        <button type="button" class="border-l px-3 py-1 text-[11px] font-semibold disabled:cursor-default" :class="!abrir ? 'bg-[#1f3a5f] text-white' : 'text-muted-foreground hover:bg-muted'" :disabled="!canEdit || saiu || !!ocupado" @click="marcarAbrir(false)">Não</button>
      </span>
      <span class="text-[11px] text-muted-foreground">
        <template v-if="saiu">já saiu: não dá pra desfazer na plataforma</template>
        <template v-else-if="!abrir">nada vai pra plataforma neste pedido</template>
        <template v-else-if="previa?.abre && motivoAtual">automático: o motivo <b>{{ motivoAtual }}</b> abre chamado — dá pra mudar</template>
        <template v-else>só abre com um motivo que pede chamado (Bloqueado, Golpe, Item faltando, Não recebido, Danificado)</template>
      </span>
    </div>

    <!-- A IA segurou -->
    <div v-if="segurada && conf" class="mt-3 rounded-md border border-amber-300 bg-amber-50 p-2.5 dark:border-amber-800 dark:bg-amber-950/30" data-segurada>
      <div class="mb-1.5 font-semibold text-amber-900 dark:text-amber-200">A IA segurou o chamado: o motivo não bate com o que foi escrito</div>
      <dl class="grid grid-cols-[120px_minmax(0,1fr)] gap-x-2 gap-y-1">
        <dt class="text-muted-foreground">Lançado</dt>
        <dd>Condição <b>{{ conf.condicao_lancada || '—' }}</b> · Motivo <b>{{ conf.motivo_lancado || '—' }}</b></dd>
        <dt class="text-muted-foreground">O que ela viu</dt>
        <dd>{{ conf.explicacao || '—' }}</dd>
        <template v-if="conf.motivo_sugerido || conf.condicao_sugerida">
          <dt class="text-muted-foreground">O que bate</dt>
          <dd>
            <template v-if="conf.motivo_sugerido">Motivo <b>{{ conf.motivo_sugerido }}</b></template>
            <template v-if="conf.motivo_sugerido && conf.condicao_sugerida"> · </template>
            <template v-if="conf.condicao_sugerida">Condição <b>{{ conf.condicao_sugerida }}</b> <span class="text-muted-foreground">(a condição se troca no cartão do lançamento)</span></template>
          </dd>
        </template>
      </dl>
      <div v-if="canEdit" class="mt-2.5 flex flex-wrap gap-2">
        <button v-if="podeTrocar" type="button" :class="BOTAO_PRINCIPAL" :disabled="!!ocupado" data-decisao="trocar" @click="decidir('trocar')">trocar pra {{ conf.motivo_sugerido }} e mandar</button>
        <button type="button" :class="BOTAO" :disabled="!!ocupado" data-decisao="mandar" @click="decidir('mandar')">mandar como está</button>
        <button type="button" :class="BOTAO" :disabled="!!ocupado" data-decisao="nao_abrir" @click="decidir('nao_abrir')">não abrir chamado</button>
      </div>
      <div class="mt-2 text-[11px] text-amber-900 dark:text-amber-200">
        <template v-if="prazo">Prazo da plataforma: <b>{{ prazo }}</b>. </template>Nada vai pra plataforma até alguém decidir — e o caso aparece em “Onde agir hoje”.
      </div>
    </div>

    <!-- Observação que vai no texto -->
    <div class="mt-3">
      <div class="mb-1 text-[11px] font-semibold">
        Observação pro chamado
        <span class="font-normal text-muted-foreground">· {{ saiu ? 'já saiu: mudar aqui não muda o que a plataforma recebeu' : abrir ? 'vai no texto pra plataforma, como está' : 'fica só aqui (sem chamado)' }}</span>
      </div>
      <div v-if="obsMisturadas" class="mb-1 text-[11px] text-amber-700 dark:text-amber-400">Cada produto tinha uma observação: estão juntas aqui. Ao salvar, vale este texto pra todos.</div>
      <textarea v-model="obs" rows="2" class="w-full resize-y rounded-md border bg-background px-2 py-1.5 text-xs" placeholder="o que aconteceu com o produto, do jeito que a plataforma deve ler (ex.: abrimos a caixa e veio um sabonete no lugar do celular)" :disabled="!canEdit" data-obs-chamado @blur="salvarObs" />
    </div>

    <!-- Prévia -->
    <div v-if="!saiu && abrir && previa?.abre" class="mt-3 rounded-md border bg-muted/40 p-2.5" data-previa>
      <div class="mb-1 font-semibold">O que vai pra {{ plataforma || 'a plataforma' }} <span class="font-normal text-muted-foreground">· sai sozinho logo depois de lançar, uma vez só</span><Loader2 v-if="previaCarregando" class="ml-1 inline size-3 animate-spin" /></div>
      <div class="whitespace-pre-wrap break-words rounded border bg-background px-2 py-1.5 font-mono text-[10.5px] leading-snug">{{ previa.texto }}</div>
      <div class="mt-1.5 flex flex-wrap gap-1.5">
        <span class="inline-flex items-center gap-1 rounded px-2 py-0.5 text-[11px] font-semibold" :class="fotos ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/50 dark:text-emerald-300' : 'bg-muted text-muted-foreground'">{{ fotos ? '✓' : '—' }} {{ fotos }} foto(s)</span>
        <span class="inline-flex items-center gap-1 rounded px-2 py-0.5 text-[11px] font-semibold" :class="linkEnvio ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/50 dark:text-emerald-300' : 'bg-muted text-muted-foreground'">{{ linkEnvio ? '✓' : '—' }} vídeo do envio</span>
        <span class="inline-flex items-center gap-1 rounded px-2 py-0.5 text-[11px] font-semibold" :class="linkAbertura ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/50 dark:text-emerald-300' : 'bg-muted text-muted-foreground'">{{ linkAbertura ? '✓' : '—' }} vídeo da abertura</span>
      </div>
      <div v-for="a in previa.avisos" :key="a" class="mt-1 text-[11px] text-amber-700 dark:text-amber-400">{{ a }}</div>
      <div v-if="janela?.conferencia_ligada" class="mt-1.5 text-[11px] text-muted-foreground">Antes de sair, a IA confere se o motivo bate com a condição e a observação. Se não bater, ela <b>segura</b> e pergunta aqui — nada vai pra plataforma até alguém decidir.</div>
    </div>

    <!-- Instrução pra IA -->
    <div class="mt-3">
      <div class="mb-1 text-[11px] font-semibold">Instrução pra IA <span class="font-normal text-muted-foreground">· opcional · fica no chamado pra IA de Chamado seguir — não vai pra plataforma</span></div>
      <textarea v-model="instrucao" rows="2" class="w-full resize-y rounded-md border bg-background px-2 py-1.5 text-xs" placeholder="o que a IA de Chamado deve levar em conta (ex.: se a plataforma pedir mais prova, mandar o vídeo da abertura)" :disabled="!canEdit" data-instrucao />
      <div v-if="canEdit" class="mt-1 flex justify-end">
        <button type="button" :class="BOTAO_PRINCIPAL" :disabled="!instrucao.trim() || !!ocupado" data-mandar-instrucao @click="mandarInstrucao">
          <Loader2 v-if="ocupado === 'instrucao'" class="mr-1 inline size-3 animate-spin" />mandar instrução
        </button>
      </div>
      <div v-if="janela?.instrucoes?.length" class="mt-2 space-y-1 text-[11px]">
        <div class="font-semibold text-muted-foreground">Instruções já dadas</div>
        <div v-for="(it, k) in janela.instrucoes" :key="k" class="rounded border px-2 py-1">
          <b>{{ quando(it.em) }}<template v-if="it.autor_nome"> · {{ it.autor_nome }}</template>:</b> “{{ it.texto }}”
          <span v-if="it.esperando_chamado" class="text-amber-700 dark:text-amber-400"> · entra no chamado quando ele abrir</span>
          <span v-else-if="it.lida" class="text-emerald-700 dark:text-emerald-400"> · a IA já leu</span>
          <span v-else class="text-muted-foreground"> · a IA ainda não leu</span>
        </div>
      </div>
    </div>
  </div>
</template>
