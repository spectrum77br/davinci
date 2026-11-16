<script setup lang="ts">
// Bloco "2 · Chamado" da janela do painel de Devoluções (2ª etapa, Vinicius
// 16/11/2026): "Abrir chamado: Sim ou Não", a conferência da IA antes de a
// disputa sair ("se não bate, segurar e perguntar"), a observação que vai no
// texto pra plataforma, a prévia desse texto e a "Instrução IA".
// Tudo é do PEDIDO (o chamado é um por pedido) — API em /api/devolucoes-painel.
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ChevronDown, ChevronUp, ExternalLink, Loader2 } from 'lucide-vue-next'

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
  ia_nao_abriu?: string | null
  tem_lancamento?: boolean
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
  // Prazo pra contestar (o mais próximo entre as peças lançadas; ISO).
  prazoContestacao?: string | null
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
// Por que a contestação ainda não saiu / não saiu (vinha em cada cartão do
// lançamento; desde 16/11/2026 aparece uma vez, aqui).
const ERROS_ABERTURA: Record<string, string> = {
  devolucao_sem_foto: 'aguardando foto — anexe pela câmera',
  devolucao_motivo_sem_chamado: 'esse motivo não abre chamado (Item Incorreto saiu da lista em 07/09)',
  return_review_indisponivel: 'ML ainda não liberou a revisão (o pacote precisa constar entregue) — tenta de novo a cada hora',
  devolucao_sem_claim: 'sem devolução aberta na plataforma pra esse pedido — tenta de novo a cada hora',
  devolucao_sem_return: 'sem devolução aberta na plataforma pra esse pedido — tenta de novo a cada hora',
  devolucao_sem_pedido_marketplace: 'linha sem nº do pedido da plataforma',
  devolucao_prazo_esgotado: 'ficou 45 dias pendente — abrir na mão',
  devolucao_nao_encontrada: 'lançamento não encontrado',
  chamado_sem_integracao_ml: 'conta sem integração ML no DaVinci',
  chamado_sem_integracao_tiktok: 'conta sem integração TikTok no DaVinci',
  chamado_sem_integracao_shopee: 'conta sem integração Shopee no DaVinci',
  tiktok_aguardando_pacote: 'TikTok ainda não liberou a recusa (pacote precisa constar enviado/entregue) — tenta a cada hora',
  tiktok_arbitragem: 'em arbitragem na TikTok — aguardando',
  tiktok_quick_refund: 'TikTok já reembolsou (quick refund) — só apelação no Seller Center',
  tiktok_ja_recusada: 'já recusada na TikTok (feita no Seller Center — veja o histórico do chamado)',
  tiktok_devolucao_encerrada: 'devolução já encerrada na TikTok (reembolsada/decidida) — nada mais a abrir pela API',
  ml_claim_encerrada: 'reclamação já encerrada no ML — nada mais a abrir pela API (veja o histórico)',
  tiktok_motivo_indisponivel: 'TikTok não aceita esse motivo nesse estado',
  // só reembolso (18/09): o lançamento responde o caso que o vigia achou
  devolucao_sem_video: 'aguardando o vídeo da expedição (Vídeo do envio, aqui na janela, ou o Link envio) — tenta a cada hora',
  tiktok_reembolso_nao_pendente: 'o pedido de só reembolso já foi decidido na TikTok — veja o desfecho no histórico do chamado',
  tiktok_reembolso_ja_contestado: 'só reembolso já contestado na TikTok (recusado / em arbitragem) — veja o histórico do chamado',
  shopee_aguardando_pacote: 'Shopee ainda não liberou a disputa — tenta a cada hora',
  shopee_ja_contestada: 'já contestada na Shopee (disputa feita no Seller Center — veja o histórico)',
  shopee_devolucao_encerrada: 'devolução já encerrada na Shopee',
  shopee_motivo_indisponivel: 'Shopee não oferece esse motivo pra essa devolução',
  shopee_sem_email: 'sem e-mail do operador pra Shopee (DEVOLUCAO_DISPUTE_EMAIL)',
  plataforma_sem_api: 'sem API — abrir na mão na plataforma',
  // chamado encerrado pelo motivo (15/09): o que ainda falta fazer na mão
  retirar_contestacao: 'chamado encerrado aqui — retire a contestação no painel da plataforma',
  contestacao_cancelada: 'contestação cancelada (chamado encerrado antes de ela sair)',
}
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
// 16/11/2026 (Vinicius: "ele já vem ticado como sim… não pode vir ticado"): o Sim
// só aparece marcado quando alguém marcou ou quando o motivo lançado abre chamado.
// Sem motivo que abra e sem ninguém marcar, nenhum dos dois vem marcado.
const pedePeloMotivo = computed(() => !!(previa.value?.abre || janela.value?.pede_chamado))
const escolha = computed<'sim' | 'nao' | null>(() => {
  const a = janela.value?.abrir_chamado
  if (a === false) return 'nao'
  if (a === true || saiu.value || pedePeloMotivo.value) return 'sim'
  return null
})
// 16/11/2026 (Vinicius: "eu preciso de um painel limpo… só chamado aberto em
// verde, na fila em amarelo"): o estado do chamado é UMA etiqueta colorida; os
// detalhes (por que está na fila, prazo pra contestar, quando saiu) ficam no
// passar do mouse. Vermelho = precisa de alguém.
const VERDE = 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/50 dark:text-emerald-300'
const AMARELO = 'bg-amber-100 text-amber-800 dark:bg-amber-950/50 dark:text-amber-300'
const VERMELHO = 'bg-red-100 text-red-800 dark:bg-red-950/50 dark:text-red-300'
const CINZA = 'bg-muted text-muted-foreground'
const estado = computed<{ texto: string; cor: string; dica: string } | null>(() => {
  const j = janela.value
  if (!j) return null
  const prazoTxt = props.prazoContestacao ? `Prazo pra contestar: ${quando(props.prazoContestacao)} (${faltam(props.prazoContestacao)}).` : ''
  if (segurada.value) return { texto: 'Segurado — decidir', cor: VERMELHO, dica: `A IA segurou: o motivo não bate com o que foi escrito. ${prazoTxt}`.trim() }
  if (j.abertura_status === 'falhou') return { texto: 'Não saiu', cor: VERMELHO, dica: porQueNaFila.value || j.abertura_erro || 'erro' }
  if (j.resolvido) return { texto: 'Concluído', cor: CINZA, dica: 'Chamado concluído na aba Chamados' }
  if (cancelada.value || j.status_plataforma === 'encerrado') return { texto: 'Encerrado', cor: CINZA, dica: 'Encerrado sem contestar: nada foi pra plataforma' }
  if (saiu.value) {
    const partes = [`Saiu pra ${props.plataforma || 'a plataforma'}${j.enviada_at ? ` em ${quando(j.enviada_at)}` : ''}`]
    if (motivoAtual.value) partes.push(`motivo ${motivoAtual.value}`)
    if (conf.value?.resultado === 'bate') partes.push('conferido pela IA')
    return { texto: 'Aberto', cor: VERDE, dica: `${partes.join(' · ')}. Foto anexada agora não vai junto: a contestação só sai uma vez.` }
  }
  if (j.abertura_status === 'pendente') return { texto: 'Na fila', cor: AMARELO, dica: `${porQueNaFila.value || 'Sai sozinho assim que a plataforma liberar'}. ${prazoTxt}`.trim() }
  if (escolha.value === 'nao') return { texto: 'Não abrir', cor: CINZA, dica: 'Nada vai pra plataforma neste pedido' }
  if (escolha.value === 'sim' && pedePeloMotivo.value) return { texto: 'Abre ao lançar', cor: AMARELO, dica: 'Sai sozinho logo depois de lançar' }
  // "Sim" marcado à mão sem motivo que abra chamado: quem abre é a IA, lendo a
  // instrução (services/devolucao_chamado_instrucao, a cada 10 min).
  if (escolha.value === 'sim') {
    if (j.ia_nao_abriu) return { texto: 'IA não abriu', cor: VERMELHO, dica: `${j.ia_nao_abriu}. Escreva uma instrução nova (ou escolha o motivo no lançamento) que a IA olha de novo.` }
    if (!j.conferencia_ligada) return { texto: 'IA desligada', cor: CINZA, dica: 'A IA está desligada: escolha um motivo que abra chamado no lançamento.' }
    if (!j.tem_lancamento) return { texto: 'Lance as peças primeiro', cor: AMARELO, dica: 'A IA abre o chamado pela instrução depois que as peças forem lançadas.' }
    if (temInstrucaoEsperando.value) return { texto: 'IA vai abrir', cor: AMARELO, dica: 'A IA lê a instrução, escolhe o motivo, escreve o texto e abre o chamado (em até 10 min). A conferência olha antes de sair.' }
    return { texto: 'Falta instrução IA', cor: AMARELO, dica: 'Nenhum motivo lançado abre chamado: escreva a instrução pra IA dizendo o que contestar — ela abre o chamado.' }
  }
  return { texto: 'Sem chamado', cor: CINZA, dica: 'Nenhum motivo lançado abre chamado' }
})
// "Às vezes mensagem do chamado deixa um mostrar também": observação, prévia e
// instrução pra IA ficam fechadas até alguém pedir.
const mostrarMensagem = ref(false)
const mostrarInstrucao = ref(false)
const temInstrucaoEsperando = computed(() => !!janela.value?.instrucoes?.some((i) => i.esperando_chamado))
const BOTAO_ABRE = 'inline-flex items-center gap-0.5 rounded border px-1.5 py-0.5 text-[11px] font-semibold hover:bg-muted'
const porQueNaFila = computed(() => {
  const e = janela.value?.abertura_erro || ''
  return ERROS_ABERTURA[e] || ''
})
function faltam(v: string | null | undefined): string {
  if (!v) return ''
  const ms = new Date(v).getTime() - Date.now()
  if (Number.isNaN(ms)) return ''
  if (ms < 0) return 'vencido'
  const h = Math.floor(ms / 3600000)
  if (h < 24) return `faltam ${h} h`
  const d = Math.floor(h / 24)
  return `faltam ${d} dia${d === 1 ? '' : 's'}`
}
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
  if (!props.canEdit || saiu.value || (sim ? escolha.value === 'sim' && janela.value?.abrir_chamado === true : escolha.value === 'nao')) return
  if (!sim && !window.confirm('Não abrir chamado neste pedido?\n\nA contestação não vai pra plataforma. Dá pra voltar pro Sim enquanto ela não sair.')) return
  if (await chamar(sim ? 'sim' : 'nao', `${base.value}/abrir-chamado`, { abrir: sim }, sim ? 'Chamado: Sim' : 'Chamado: Não — nada vai pra plataforma')) {
    // Sim sem motivo que abra chamado: quem abre é a IA pela instrução — já mostra a caixa.
    if (sim && !pedePeloMotivo.value) mostrarInstrucao.value = true
    emit('mudou')
  }
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
    <div class="flex flex-wrap items-center gap-2">
      <span class="flex items-center text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
        <span class="mr-1.5 inline-flex size-[18px] items-center justify-center rounded-full bg-[#1f3a5f] text-[10.5px] font-bold text-white">2</span>Chamado
      </span>
      <span v-if="estado" class="cursor-default rounded px-2 py-0.5 text-[11px] font-semibold" :class="estado.cor" :title="estado.dica" data-estado-chamado>{{ estado.texto }}</span>
      <a v-if="janela?.chamado_id" :href="`/chamados?search=${encodeURIComponent(pedido)}`" target="_blank" rel="noopener" class="inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground hover:underline" title="Abrir o chamado na aba Chamados"><ExternalLink class="size-3" />ver na aba Chamados</a>
      <Loader2 v-if="carregando" class="size-3 animate-spin text-muted-foreground" />
    </div>

    <!-- Sim / Não -->
    <div class="mt-2 flex flex-wrap items-center gap-2">
      <span class="font-semibold">Abrir chamado?</span>
      <span class="inline-flex overflow-hidden rounded-md border" :class="!canEdit || saiu ? 'opacity-60' : ''" data-abrir-chamado>
        <button type="button" class="px-3 py-1 text-[11px] font-semibold disabled:cursor-default" :class="escolha === 'sim' ? 'bg-[#1f3a5f] text-white' : 'text-muted-foreground hover:bg-muted'" :disabled="!canEdit || saiu || !!ocupado" data-sim @click="marcarAbrir(true)">Sim</button>
        <button type="button" class="border-l px-3 py-1 text-[11px] font-semibold disabled:cursor-default" :class="escolha === 'nao' ? 'bg-[#1f3a5f] text-white' : 'text-muted-foreground hover:bg-muted'" :disabled="!canEdit || saiu || !!ocupado" data-nao @click="marcarAbrir(false)">Não</button>
      </span>
      <span class="text-[11px] text-muted-foreground">
        <template v-if="saiu">já saiu</template>
        <template v-else-if="escolha === 'nao'" />
        <template v-else-if="pedePeloMotivo && motivoAtual">pelo motivo {{ motivoAtual }}</template>
        <template v-else-if="escolha === 'sim'">marcado à mão — a IA abre pela instrução</template>
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

    <!-- Mensagem do chamado e Instrução IA: cada uma fechada até pedir (16/11/2026,
         Vinicius: "do lado de mensagem do chamado coloca Instrução IA… se eu quiser
         clicar em sim e dar uma instrução eu consigo"). -->
    <div class="mt-2 flex flex-wrap gap-1.5">
      <button type="button" :class="[BOTAO_ABRE, mostrarMensagem ? 'bg-muted' : '']" data-mostrar-mensagem @click="mostrarMensagem = !mostrarMensagem">
        mensagem do chamado
        <template v-if="mostrarMensagem"><ChevronUp class="size-3" /></template>
        <template v-else><ChevronDown class="size-3" /></template>
      </button>
      <button type="button" :class="[BOTAO_ABRE, mostrarInstrucao ? 'bg-muted' : '']" data-mostrar-instrucao @click="mostrarInstrucao = !mostrarInstrucao">
        instrução IA<template v-if="janela?.instrucoes?.length"> ({{ janela.instrucoes.length }})</template>
        <template v-if="mostrarInstrucao"><ChevronUp class="size-3" /></template>
        <template v-else><ChevronDown class="size-3" /></template>
      </button>
    </div>
    <div v-show="mostrarMensagem" data-mensagem>
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

    </div>
    <div v-show="mostrarInstrucao" data-instrucao-ia>
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
  </div>
</template>
