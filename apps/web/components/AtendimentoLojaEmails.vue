<script lang="ts">
// Os E-MAILS DE UMA LOJA na Caixa (27/10/2026) — o Eduardo: "botar um
// AMPLIÁVEL que vai abrir os e-mails embaixo de cada loja … então poderá
// ampliar e visualizar os e-mails ali pelo atendimento mesmo de cada loja".
// O clique num tipo embaixo da loja, na barra de lojas (AtendimentoLojas: os
// tipos da barra — hoje Informativo e Chamado; a escolha do dono é a
// constante TIPOS_EMAIL_NA_BARRA_DE_LOJAS da API, que chega em `tipos`),
// troca a lista do meio da Caixa por esta: as conversas de e-mail DAQUELA
// loja e DAQUELE tipo. Quem não é admin vê e-mail SÓ por aqui (a aba E-mail
// é só de admin; a API recusa a lista sem a loja ou fora dos tipos da barra). A conversa abre na coluna da conversa da
// própria Caixa, com a barra do e-mail (AtendimentoEmailConversaBarra: voltar,
// vincular ao pedido) — a mesma visão da aba E-mail.
//
// Sem rota nova: a lista é a de "Todas as conversas" da aba E-mail (GET
// /api/atendimento/conversas?caixa=email — `urlDaFila`), com a loja
// (`integration_id`, ou `store_info_id` na loja sem integração) e o tipo; as
// abertas ou, no chip, as fechadas; a busca pelo nome, pedido ou protocolo.
// No Informativo, para quem MEXE, vêm em cima as linhas das pastas
// informativas da loja (sem conversa — GET …/email/emails?fila=informativo
// com a loja; "ver o e-mail" = GET …/email/emails/{id}, texto PROTEGIDO, nunca
// HTML), como no chip Informativos da aba E-mail. Nada aqui muda a Caixa do
// chat: a lista e os números dela continuam sem e-mail (`separa_email`).
// Funções puras aqui em cima (tests/atendimento-email-lojas.cjs).
import { urlDaFila } from '~/components/AtendimentoMailFilas.vue'
import { TIPOS_EMAIL_DA_LOJA, numeroDoTipo, tiposDaBarra, type EscolhaEmail, type TipoEmailDaLoja } from '~/components/AtendimentoLojas.vue'

type DaLoja = Pick<EscolhaEmail, 'integration_id' | 'store_info_id' | 'tipo'>

function lojaDaEscolha(e: DaLoja): URLSearchParams | null {
  const q = new URLSearchParams()
  if (e.integration_id) q.set('integration_id', e.integration_id)
  else if (e.store_info_id) q.set('store_info_id', e.store_info_id)
  else return null
  return q
}

// A lista da loja: a de "Todas as conversas" da aba E-mail + a loja. Sem loja
// nenhuma (nunca devia acontecer): null — NUNCA a lista de todas as lojas.
export function urlDosEmailsDaLoja(e: DaLoja, o: { fechadas?: boolean; busca?: string | null; antesDe?: string | null } = {}): string | null {
  const loja = lojaDaEscolha(e)
  if (!loja) return null
  return `${urlDaFila('todas', o.antesDe || null, null, o.busca || null, !!o.fechadas, e.tipo)}&${loja.toString()}`
}

// As linhas das pastas informativas da loja (só quem mexe; a rota recusa os outros).
export function urlDasLinhasDaLoja(e: DaLoja, antesDe?: string | null): string | null {
  const loja = lojaDaEscolha(e)
  if (!loja) return null
  const q = new URLSearchParams({ fila: 'informativo' })
  for (const [k, v] of loja) q.set(k, v)
  if (antesDe) q.set('antes_de', antesDe)
  return `/api/atendimento/email/emails?${q.toString()}`
}

// As linhas vêm só no Informativo, para quem mexe, nas abertas e sem busca
// (a fila das linhas não tem busca nem "fechada").
export function mostraLinhas(tipo: TipoEmailDaLoja, podeMexer: boolean, fechadas: boolean, busca: string): boolean {
  return podeMexer && tipo === 'informativo' && !fechadas && !busca
}

export function chaveDaEscolha(e: Pick<EscolhaEmail, 'chave' | 'tipo' | 'integration_id' | 'store_info_id'> | null | undefined): string {
  return e ? [e.chave, e.tipo, e.integration_id, e.store_info_id].join('|') : ''
}

// A lista vazia: o porquê, pelo tipo (e pela busca / pelo chip).
export function vazioDaLoja(tipo: TipoEmailDaLoja, fechadas: boolean, busca: string): string {
  const rotulo = (TIPOS_EMAIL_DA_LOJA.find((t) => t.value === tipo)?.label || tipo).toLowerCase()
  if (busca) return 'Nada encontrado para essa busca nesta loja.'
  return fechadas ? `Nenhum e-mail ${rotulo} fechado desta loja.` : `Nenhum e-mail ${rotulo} aberto desta loja.`
}

export { TIPOS_EMAIL_DA_LOJA, numeroDoTipo, tiposDaBarra }
</script>

<script setup lang="ts">
import { ArrowLeft, ChevronDown, ChevronUp, Download, Loader2, Mail, RefreshCw } from 'lucide-vue-next'
import type { EmailsDaLoja } from '~/components/AtendimentoLojas.vue'
import type { ItemFila } from '~/components/AtendimentoMailFilas.vue'
import { erroDaApi, fmtDataHora, plataformaInfo, prazoDe, type ConversaResumo } from '~/components/AtendimentoPlataforma.vue'
import { avisosDeProtecao, dicaTipoEmail, remetenteLegivel, rotuloTipoEmail, tamanhoLegivel, urlDoAnexo, type CartaoEmail } from '~/components/AtendimentoEmailCartao.vue'

const props = defineProps<{
  // A loja e o tipo escolhidos na barra (a página guarda).
  escolha: EscolhaEmail
  // Os números da loja (o mesmo GET …/email/contagens?por_loja=true da barra).
  contagem?: EmailsDaLoja | null
  // Os tipos da barra (`tipos_na_barra` da API): os chips.
  tipos?: string[] | null
  podeMexer?: boolean
  // A conversa de e-mail aberta na coluna da conversa.
  selecionada?: string | null
  // Sobe a cada tique do polling de 30 s da página (e quando a conversa aberta
  // muda o que a lista mostra — `sinal`): relê a 1ª página, sem "carregar mais".
  tique?: number
  sinal?: number
}>()
const emit = defineEmits<{
  (e: 'selecionar', id: string): void
  (e: 'fechar'): void
  (e: 'tipo', tipo: TipoEmailDaLoja): void
}>()

const { api, url } = useApi()
const toasts = useToasts()

const conversas = ref<ConversaResumo[]>([])
const proximo = ref<string | null>(null)
const carregando = ref(false)
const erro = ref<string | null>(null)
const fechadas = ref(false)
const buscaTexto = ref('')
const busca = ref('')
let paginasExtras = false
let geracao = 0
// As linhas das pastas informativas (quem mexe) e o e-mail aberto de uma delas.
const linhas = ref<ItemFila[]>([])
const proximoLinhas = ref<string | null>(null)
const carregandoLinhas = ref(false)
const erroLinhas = ref<string | null>(null)
const linhaAberta = ref<string | null>(null)
const detalhe = ref<CartaoEmail | null>(null)
let geracaoLinhas = 0

// Os chips: os tipos da barra (o escolhido sempre aparece, mesmo se a lista mudou).
const chips = computed(() => {
  const lista = tiposDaBarra(props.tipos)
  const escolhido = TIPOS_EMAIL_DA_LOJA.find((t) => t.value === props.escolha.tipo)
  return escolhido && !lista.some((t) => t.value === escolhido.value) ? [...lista, escolhido] : lista
})
const comLinhas = computed(() => mostraLinhas(props.escolha.tipo, !!props.podeMexer, fechadas.value, busca.value))

// `automatico` (o polling): a falha não mostra erro — a lista fica e o próximo tique tenta de novo.
async function carregar(mais = false, automatico = false) {
  const g = ++geracao
  const endereco = urlDosEmailsDaLoja(props.escolha, { fechadas: fechadas.value, busca: busca.value, antesDe: mais ? proximo.value : null })
  if (!endereco) {
    conversas.value = []
    erro.value = 'Esta loja não tem como filtrar os e-mails.'
    return
  }
  carregando.value = true
  if (!automatico) erro.value = null
  if (!mais) paginasExtras = false
  try {
    const r = await api<{ itens: ConversaResumo[]; proximo: string | null }>(endereco)
    if (g !== geracao) return
    const novos = r?.itens || []
    conversas.value = mais ? [...conversas.value, ...novos.filter((x) => !conversas.value.some((y) => y.id === x.id))] : novos
    proximo.value = r?.proximo || null
    if (mais) paginasExtras = true
    if (automatico) erro.value = null
  } catch (e: any) {
    if (g === geracao && !automatico) erro.value = erroDaApi(e, 'Não consegui carregar os e-mails desta loja').texto
  } finally {
    if (g === geracao) carregando.value = false
  }
}

async function carregarLinhas(mais = false) {
  const g = ++geracaoLinhas
  if (!comLinhas.value) {
    linhas.value = []
    proximoLinhas.value = null
    erroLinhas.value = null
    carregandoLinhas.value = false
    return
  }
  const endereco = urlDasLinhasDaLoja(props.escolha, mais ? proximoLinhas.value : null)
  if (!endereco) return
  carregandoLinhas.value = true
  erroLinhas.value = null
  try {
    const r = await api<{ itens: ItemFila[]; proximo: string | null }>(endereco)
    if (g !== geracaoLinhas) return
    const novas = r?.itens || []
    linhas.value = mais ? [...linhas.value, ...novas.filter((x) => !linhas.value.some((y) => y.id === x.id))] : novas
    proximoLinhas.value = r?.proximo || null
  } catch (e: any) {
    if (g === geracaoLinhas) erroLinhas.value = erroDaApi(e, 'Não consegui carregar as pastas informativas desta loja').texto
  } finally {
    if (g === geracaoLinhas) carregandoLinhas.value = false
  }
}

function recomecar() {
  conversas.value = []
  proximo.value = null
  linhaAberta.value = null
  detalhe.value = null
  void Promise.all([carregar(), carregarLinhas()])
}
// Outra loja ou outro tipo: do começo, nas abertas, sem busca.
watch(() => chaveDaEscolha(props.escolha), () => {
  fechadas.value = false
  buscaTexto.value = ''
  busca.value = ''
  linhas.value = []
  recomecar()
})
function verFechadas(v: boolean) {
  if (fechadas.value === v) return
  fechadas.value = v
  recomecar()
}
function buscar() {
  busca.value = buscaTexto.value.trim().slice(0, 200)
  recomecar()
}
function limparBusca() {
  buscaTexto.value = ''
  buscar()
}
// O polling da página e a conversa aberta que mudou: a 1ª página de novo — só
// sem o "carregar mais" (reler voltaria para o começo) e sem leitura no meio.
function aoTique() {
  if (paginasExtras || carregando.value) return
  void carregar(false, true)
}
watch(() => props.tique, aoTique)
watch(() => props.sinal, aoTique)
onMounted(() => { void Promise.all([carregar(), carregarLinhas()]) })

async function verLinha(e: ItemFila) {
  if (linhaAberta.value === e.id) {
    linhaAberta.value = null
    detalhe.value = null
    return
  }
  linhaAberta.value = e.id
  detalhe.value = null
  try {
    const d = await api<CartaoEmail>(`/api/atendimento/email/emails/${encodeURIComponent(e.id)}`)
    if (linhaAberta.value === e.id) detalhe.value = d
  } catch (x: any) {
    if (linhaAberta.value === e.id) {
      linhaAberta.value = null
      const er = erroDaApi(x, 'Não consegui abrir o e-mail')
      toasts.error(er.texto, er.motivos)
    }
  }
}

const rotuloDoTipo = computed(() => TIPOS_EMAIL_DA_LOJA.find((t) => t.value === props.escolha.tipo)?.label || props.escolha.tipo)
function nomeDaLinha(c: ConversaResumo): string {
  return c.comprador_nome || (c.pedido_marketplace ? `Pedido ${c.pedido_marketplace}` : 'Cliente')
}
</script>

<template>
  <div class="flex min-h-0 flex-1 flex-col" :aria-label="`E-mails ${rotuloDoTipo} da loja ${escolha.conta}`" data-emails-da-loja>
    <div class="shrink-0 space-y-1.5 border-b p-2">
      <div class="flex items-center gap-1.5">
        <button
          type="button"
          class="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
          title="Voltar às conversas do chat (a lista da Caixa)"
          data-voltar-conversas
          @click="emit('fechar')"
        ><ArrowLeft class="size-3.5" /> conversas</button>
        <span class="ml-auto inline-flex items-center gap-1 text-[11px] uppercase tracking-wider text-muted-foreground"><Mail class="size-3.5" aria-hidden="true" />E-mails</span>
        <button type="button" class="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-50" :disabled="carregando" title="atualizar" aria-label="atualizar os e-mails da loja" @click="recomecar">
          <RefreshCw class="size-3.5" :class="{ 'animate-spin': carregando }" />
        </button>
      </div>
      <div class="flex min-w-0 items-center gap-1.5 text-sm font-semibold">
        <AtendimentoIconePlataforma :plataforma="escolha.plataforma" :tamanho="15" decorativo />
        <span class="min-w-0 truncate" :title="`${plataformaInfo(escolha.plataforma).nome} · ${escolha.conta}`">{{ escolha.conta }}</span>
      </div>
      <!-- os tipos da loja (os mesmos da barra) -->
      <div class="flex flex-wrap items-center gap-1 text-[11px]" role="group" aria-label="tipo de e-mail" data-tipos-da-loja>
        <button
          v-for="t in chips"
          :key="t.value"
          type="button"
          class="inline-flex items-center gap-1 rounded-full border px-2 py-0.5"
          :class="escolha.tipo === t.value ? 'border-primary bg-primary/10 text-primary' : 'text-muted-foreground hover:bg-muted'"
          :aria-pressed="escolha.tipo === t.value"
          :data-tipo-da-loja="t.value"
          @click="emit('tipo', t.value)"
        >
          {{ t.label }}<span v-if="contagem" class="tabular-nums">{{ numeroDoTipo(contagem, t.value) }}</span>
        </button>
      </div>
      <div class="flex flex-wrap items-center gap-1 text-[11px]" role="group" aria-label="abertas ou fechadas" data-situacao-da-loja>
        <button
          v-for="op in [{ v: false, label: 'Abertas' }, { v: true, label: 'Fechadas' }]"
          :key="op.label"
          type="button"
          class="rounded-full border px-2 py-0.5"
          :class="fechadas === op.v ? 'border-primary bg-primary/10 text-primary' : 'text-muted-foreground hover:bg-muted'"
          :aria-pressed="fechadas === op.v"
          :data-situacao="op.v ? 'fechadas' : 'abertas'"
          @click="verFechadas(op.v)"
        >{{ op.label }}</button>
      </div>
      <form class="flex items-center gap-1 text-xs" role="search" data-busca-emails-da-loja @submit.prevent="buscar">
        <input v-model="buscaTexto" type="search" maxlength="200" class="h-7 min-w-0 flex-1 rounded border bg-background px-1.5" placeholder="buscar: nome, pedido ou protocolo" aria-label="buscar nos e-mails desta loja" />
        <button type="submit" class="rounded border px-2 py-0.5 hover:bg-muted disabled:opacity-50" :disabled="carregando">buscar</button>
        <button v-if="busca" type="button" class="text-muted-foreground underline" @click="limparBusca">limpar</button>
      </form>
    </div>

    <div class="min-h-0 flex-1 space-y-2 overflow-y-auto p-2">
      <div v-if="erro" class="rounded-md border border-red-500/40 bg-red-500/10 px-2 py-1.5 text-xs text-red-700 dark:text-red-300" role="alert">{{ erro }}</div>

      <!-- as linhas das pastas INFORMATIVAS da loja (quem mexe; sem conversa) -->
      <section v-if="comLinhas && (linhas.length || carregandoLinhas || erroLinhas)" class="space-y-1.5" aria-label="pastas informativas da loja" data-linhas-da-loja>
        <h4 class="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Pastas informativas</h4>
        <div v-if="erroLinhas" class="text-xs text-red-700 dark:text-red-300">{{ erroLinhas }}</div>
        <div v-else-if="carregandoLinhas && !linhas.length" class="flex justify-center py-3"><Loader2 class="size-4 animate-spin" /></div>
        <ul v-else class="space-y-1.5">
          <li v-for="e in linhas" :key="e.id" class="space-y-1 rounded-lg border p-2 text-xs" :data-linha-da-loja="e.id">
            <div class="flex flex-wrap items-center gap-1.5">
              <span class="rounded bg-muted px-1.5 py-px text-[11px] text-muted-foreground">{{ e.pasta || 'sem pasta' }}</span>
              <span class="ml-auto text-muted-foreground">{{ fmtDataHora(e.recebido_em) }}</span>
            </div>
            <div class="break-words font-medium">{{ e.assunto || '(sem assunto)' }}</div>
            <div class="break-all text-muted-foreground">De {{ e.de || '—' }}</div>
            <button type="button" class="inline-flex items-center gap-0.5 underline" :aria-expanded="linhaAberta === e.id" @click="verLinha(e)">
              <component :is="linhaAberta === e.id ? ChevronUp : ChevronDown" class="size-3" />{{ linhaAberta === e.id ? 'fechar o e-mail' : 'ver o e-mail' }}
            </button>
            <div v-if="linhaAberta === e.id" class="space-y-1.5 rounded-md border bg-background p-2" data-linha-da-loja-aberta>
              <div v-if="!detalhe" class="flex justify-center py-3"><Loader2 class="size-4 animate-spin" /></div>
              <template v-else>
                <div class="break-all text-[11px]"><span class="text-muted-foreground">De</span> {{ remetenteLegivel(detalhe) }}</div>
                <div v-for="(p, i) in avisosDeProtecao(detalhe)" :key="i" class="text-[11px] text-muted-foreground">{{ p }}</div>
                <pre class="max-h-[280px] overflow-y-auto whitespace-pre-wrap break-words font-sans text-xs leading-relaxed">{{ detalhe.texto || '(e-mail sem texto)' }}</pre>
                <div v-if="detalhe.anexos?.length" class="space-y-0.5">
                  <a v-for="an in detalhe.anexos" :key="an.id" :href="url(urlDoAnexo(an.id))" class="flex max-w-full items-center gap-1 underline" rel="noopener noreferrer">
                    <Download class="size-3 shrink-0" aria-hidden="true" /><span class="truncate">{{ an.filename || 'anexo' }}</span><span class="shrink-0 text-muted-foreground">{{ tamanhoLegivel(an.tamanho) }}</span>
                  </a>
                </div>
              </template>
            </div>
          </li>
        </ul>
        <div v-if="proximoLinhas" class="flex justify-center">
          <button type="button" class="rounded border px-3 py-1 text-xs hover:bg-muted disabled:opacity-50" :disabled="carregandoLinhas" @click="carregarLinhas(true)">carregar mais</button>
        </div>
        <h4 class="pt-1 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Conversas de e-mail</h4>
      </section>

      <!-- as conversas de e-mail da loja, do tipo escolhido -->
      <div v-if="carregando && !conversas.length" class="flex justify-center py-8"><Loader2 class="size-5 animate-spin" /></div>
      <div v-else-if="!conversas.length && !erro" class="py-8 text-center text-xs text-muted-foreground" data-emails-da-loja-vazio>
        {{ vazioDaLoja(escolha.tipo, fechadas, busca) }}
        <button type="button" class="ml-1 underline hover:text-foreground" data-ver-a-outra-metade @click="verFechadas(!fechadas)">ver as {{ fechadas ? 'abertas' : 'fechadas' }}</button>
      </div>
      <ul v-else class="space-y-1.5">
        <li v-for="c in conversas" :key="c.id">
          <button
            type="button"
            class="w-full space-y-0.5 rounded-lg border p-2 text-left text-xs hover:bg-muted/50"
            :class="selecionada === c.id ? 'border-primary bg-primary/5' : ''"
            :aria-current="selecionada === c.id ? 'true' : undefined"
            :data-email-da-loja="c.id"
            @click="emit('selecionar', c.id)"
          >
            <div class="flex flex-wrap items-center gap-1.5">
              <span v-if="rotuloTipoEmail(c.email?.tipo_email)" class="rounded bg-muted px-1.5 py-px text-[11px] text-muted-foreground" :title="dicaTipoEmail(c.email?.tipo_email_inferido)">{{ rotuloTipoEmail(c.email?.tipo_email) }}</span>
              <span v-if="c.email?.caso_id" class="rounded bg-teal-500/15 px-1.5 py-px text-[11px] text-teal-800 dark:text-teal-300">Chamado {{ c.email.caso_id }}</span>
              <span v-if="c.email?.protocolo && !c.email?.caso_id" class="rounded bg-teal-500/15 px-1.5 py-px text-[11px] text-teal-800 dark:text-teal-300">{{ c.email.protocolo }}</span>
              <span v-if="prazoDe(c)" class="rounded px-1.5 py-px text-[11px]" :class="prazoDe(c)!.cls" :title="prazoDe(c)!.titulo">{{ prazoDe(c)!.texto }}</span>
              <span v-else-if="c.aguardando_resposta" class="rounded bg-red-500/15 px-1.5 py-px text-[11px] text-red-700 dark:text-red-300">falta responder</span>
              <span v-if="c.situacao === 'fechada'" class="rounded bg-muted px-1.5 py-px text-[11px] text-muted-foreground">fechada</span>
              <span class="ml-auto text-muted-foreground">{{ fmtDataHora(c.ultima_mensagem_em) }}</span>
            </div>
            <div class="break-words font-medium">{{ nomeDaLinha(c) }}</div>
            <div v-if="c.ultima_mensagem_resumo" class="line-clamp-2 break-words text-muted-foreground">{{ c.ultima_mensagem_resumo }}</div>
            <div v-if="c.email?.alias" class="break-all text-[11px] text-muted-foreground">para {{ c.email.alias }}</div>
          </button>
        </li>
      </ul>
      <div v-if="proximo" class="flex justify-center">
        <button type="button" class="rounded border px-3 py-1 text-xs hover:bg-muted disabled:opacity-50" :disabled="carregando" @click="carregar(true)">carregar mais</button>
      </div>
    </div>
  </div>
</template>
