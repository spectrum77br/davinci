<script lang="ts">
// Os E-MAILS DE UMA LOJA numa PASTA, na Caixa (27/10/2026) — o Eduardo: "botar
// um AMPLIÁVEL que vai abrir os e-mails embaixo de cada loja … então poderá
// ampliar e visualizar os e-mails ali pelo atendimento mesmo de cada loja". As
// caixas embaixo de cada loja são as pastas do Tuta que ele unificou
// (Problemas · Envios · Devolução · Informativo — a constante
// GRUPOS_EMAIL_NA_BARRA_DE_LOJAS da API, que chega em `grupos`). O "ver todos"
// (ou o clique num e-mail) da caixa troca a lista do meio da Caixa por esta:
// TODO e-mail da pasta ligado àquela loja, o mais novo primeiro — a conversa de
// e-mail (abre na coluna da conversa, com a barra do e-mail: voltar, vincular
// ao pedido), a linha informativa e o que a ponte segurou como segurança (o
// código de postagem da devolução) —, dos últimos `dias` dias (o número da
// caixa). O e-mail que não é conversa abre na ÁREA PRINCIPAL, não aqui
// (27/10/2026, o Eduardo: "o e-mail tem que abrir ali na box principal, não ali
// na esquerda; se clicar ali amplia pra cá"): a linha só fica acesa e o texto
// inteiro vai para AtendimentoEmailDaLojaAberto. Quem não é admin
// vê e-mail SÓ por aqui (a aba E-mail é só de admin). Nada aqui muda a Caixa do
// chat: a lista e os números dela continuam sem e-mail (`separa_email`).
// Funções puras aqui em cima (tests/atendimento-email-lojas.cjs).
import { urlDosEmailsDaCaixa } from '~/components/AtendimentoLojaGrupoEmails.vue'
import { gruposDaBarra, motivosDaCaixaVazia, numeroDoGrupo, type EmailDaCaixa, type EscolhaEmail, type GrupoDaBarra } from '~/components/AtendimentoLojas.vue'

export function urlDoEmailDaCaixa(id: string): string {
  return `/api/atendimento/email/lojas/emails/${encodeURIComponent(id)}`
}
export function urlDoAnexoDaCaixa(id: string): string {
  return `/api/atendimento/email/lojas/anexos/${encodeURIComponent(id)}`
}
export function chaveDaEscolha(e: Pick<EscolhaEmail, 'chave' | 'grupo' | 'integration_id' | 'store_info_id' | 'site'> | null | undefined): string {
  return e ? [e.chave, e.grupo, e.integration_id, e.store_info_id, e.site || ''].join('|') : ''
}
// O nome da linha: o assunto; sem ele, o cliente ou o remetente.
export function tituloDaLinha(e: Pick<EmailDaCaixa, 'assunto' | 'comprador_nome' | 'de'>): string {
  return e.assunto || e.comprador_nome || e.de || '(sem assunto)'
}

export { gruposDaBarra, motivosDaCaixaVazia, numeroDoGrupo, urlDosEmailsDaCaixa }
</script>

<script setup lang="ts">
import { ArrowLeft, Loader2, Mail, RefreshCw } from 'lucide-vue-next'
import type { EmailsDaLoja } from '~/components/AtendimentoLojas.vue'
import { erroDaApi, fmtDataHora, plataformaInfo } from '~/components/AtendimentoPlataforma.vue'

const props = defineProps<{
  // A loja e o grupo escolhidos na barra (a página guarda).
  escolha: EscolhaEmail
  // Os números da loja (o mesmo GET …/email/contagens?por_loja=true da barra).
  contagem?: EmailsDaLoja | null
  // Os grupos da barra (`grupos_na_barra` da API): os chips.
  grupos?: GrupoDaBarra[] | null
  dias?: number
  // A conversa de e-mail aberta na coluna da conversa.
  selecionada?: string | null
  // O e-mail (sem conversa) aberto na área principal: a linha dele acesa.
  aberto?: string | null
  // Sobe a cada tique do polling de 30 s da página (e quando a conversa aberta
  // muda o que a lista mostra — `sinal`): relê a 1ª página, sem "carregar mais".
  tique?: number
  sinal?: number
}>()
const emit = defineEmits<{
  (e: 'selecionar', id: string, email?: EmailDaCaixa): void
  // O e-mail sem conversa: abre na área principal (a página guarda qual).
  (e: 'ver', email: EmailDaCaixa): void
  (e: 'fechar'): void
  (e: 'grupo', grupo: string): void
}>()

const { api } = useApi()

const itens = ref<EmailDaCaixa[]>([])
const proximo = ref<string | null>(null)
const carregando = ref(false)
const erro = ref<string | null>(null)
let paginasExtras = false
let geracao = 0

const chips = computed(() => gruposDaBarra(props.grupos))
const grupoAtual = computed(() => chips.value.find((g) => g.chave === props.escolha.grupo) || null)
const diasDaBarra = computed(() => Number(props.dias) || 30)
const motivos = computed(() => motivosDaCaixaVazia(grupoAtual.value, props.contagem, diasDaBarra.value))

// `automatico` (o polling): a falha não mostra erro — a lista fica e o próximo tique tenta de novo.
async function carregar(mais = false, automatico = false) {
  const g = ++geracao
  const endereco = urlDosEmailsDaCaixa(props.escolha, { antesDe: mais ? proximo.value : null })
  if (!endereco) {
    itens.value = []
    erro.value = 'Esta loja não tem como filtrar os e-mails.'
    return
  }
  carregando.value = true
  if (!automatico) erro.value = null
  if (!mais) paginasExtras = false
  try {
    const r = await api<{ itens: EmailDaCaixa[]; proximo: string | null }>(endereco)
    if (g !== geracao) return
    const novos = r?.itens || []
    itens.value = mais ? [...itens.value, ...novos.filter((x) => !itens.value.some((y) => y.id === x.id))] : novos
    proximo.value = r?.proximo || null
    if (mais) paginasExtras = true
    if (automatico) erro.value = null
  } catch (e: any) {
    if (g === geracao && !automatico) erro.value = erroDaApi(e, 'Não consegui carregar os e-mails desta loja').texto
  } finally {
    if (g === geracao) carregando.value = false
  }
}

function recomecar() {
  itens.value = []
  proximo.value = null
  void carregar()
}
// Outra loja ou outro grupo: do começo.
watch(() => chaveDaEscolha(props.escolha), recomecar)
// O polling da página e a conversa aberta que mudou: a 1ª página de novo — só
// sem o "carregar mais" (reler voltaria para o começo) e sem leitura no meio.
function aoTique() {
  if (paginasExtras || carregando.value) return
  void carregar(false, true)
}
watch(() => props.tique, aoTique)
watch(() => props.sinal, aoTique)
onMounted(() => { void carregar() })

// A conversa abre na coluna da conversa; o e-mail sem conversa, na área principal.
function abrir(e: EmailDaCaixa) {
  // A linha vai junto (27/10/2026): a página apaga a bolinha do site na hora.
  if (e.conversa_id) emit('selecionar', e.conversa_id, e)
  else emit('ver', e)
}
function acesa(e: EmailDaCaixa): boolean {
  return e.conversa_id ? props.selecionada === e.conversa_id : props.aberto === e.id
}
const rotuloDoGrupo = computed(() => grupoAtual.value?.rotulo || props.escolha.grupo)
</script>

<template>
  <div class="flex min-h-0 flex-1 flex-col" :aria-label="`E-mails ${rotuloDoGrupo} ${escolha.site ? 'do site' : 'da loja'} ${escolha.conta}`" data-emails-da-loja>
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
      <!-- as pastas da loja (as mesmas caixas da barra) -->
      <div class="flex flex-wrap items-center gap-1 text-[11px]" role="group" aria-label="pasta de e-mail" data-grupos-da-loja>
        <button
          v-for="g in chips"
          :key="g.chave"
          type="button"
          class="inline-flex items-center gap-1 rounded-full border px-2 py-0.5"
          :class="escolha.grupo === g.chave ? 'border-primary bg-primary/10 text-primary' : 'text-muted-foreground hover:bg-muted'"
          :aria-pressed="escolha.grupo === g.chave"
          :data-grupo-da-loja="g.chave"
          @click="emit('grupo', g.chave)"
        >
          {{ g.rotulo }}<span v-if="contagem" class="tabular-nums">{{ numeroDoGrupo(contagem, g.chave) }}</span>
        </button>
      </div>
      <p class="text-[11px] text-muted-foreground">{{ escolha.site ? `Os e-mails do site para o ${escolha.grupo === 'sac' ? 'sac@' : 'admin@ (e os outros endereços)'} dos últimos ${diasDaBarra} dias, o mais novo primeiro — a resposta sai pelo mesmo endereço.` : `Os e-mails da pasta dos últimos ${diasDaBarra} dias, o mais novo primeiro.` }}</p>
    </div>

    <div class="min-h-0 flex-1 space-y-2 overflow-y-auto p-2">
      <div v-if="erro" class="rounded-md border border-red-500/40 bg-red-500/10 px-2 py-1.5 text-xs text-red-700 dark:text-red-300" role="alert">{{ erro }}</div>
      <div v-if="carregando && !itens.length" class="flex justify-center py-8"><Loader2 class="size-5 animate-spin" /></div>
      <div v-else-if="!itens.length && !erro" class="space-y-1 py-8 text-center text-xs text-muted-foreground" data-emails-da-loja-vazio>
        <p v-for="(m, i) in motivos" :key="i">{{ m }}</p>
      </div>
      <ul v-else class="space-y-1.5">
        <li v-for="e in itens" :key="e.id">
          <button
            type="button"
            class="w-full space-y-0.5 rounded-lg border p-2 text-left text-xs hover:bg-muted/50"
            :class="acesa(e) ? 'border-primary bg-primary/5' : ''"
            :aria-current="acesa(e) ? 'true' : undefined"
            :data-email-da-loja="e.id"
            @click="abrir(e)"
          >
            <div class="flex flex-wrap items-center gap-1.5">
              <span v-if="e.pasta" class="rounded bg-muted px-1.5 py-px text-[11px] text-muted-foreground">{{ e.pasta }}</span>
              <span v-if="e.seguranca" class="rounded bg-amber-500/15 px-1.5 py-px text-[11px] text-amber-800 dark:text-amber-300" title="A ponte segurou este e-mail como de segurança (tem código): nesta pasta ele aparece para quem vê a loja">com código</span>
              <span v-if="e.conversa_id && e.aguardando_resposta" class="rounded bg-red-500/15 px-1.5 py-px text-[11px] text-red-700 dark:text-red-300">falta responder</span>
              <span v-else-if="e.conversa_id" class="rounded bg-teal-500/15 px-1.5 py-px text-[11px] text-teal-800 dark:text-teal-300">conversa</span>
              <span v-if="e.situacao === 'fechada'" class="rounded bg-muted px-1.5 py-px text-[11px] text-muted-foreground">fechada</span>
              <span class="ml-auto text-muted-foreground">{{ fmtDataHora(e.recebido_em) }}</span>
            </div>
            <div class="break-words font-medium">{{ tituloDaLinha(e) }}</div>
            <div v-if="e.de" class="break-all text-muted-foreground">De {{ e.de }}</div>
            <div v-if="!e.conversa_id" class="text-[11px]" :class="aberto === e.id ? 'font-medium text-primary' : 'text-muted-foreground underline'">{{ aberto === e.id ? 'aberto ao lado' : 'ver o e-mail' }}</div>
          </button>
        </li>
      </ul>
      <div v-if="proximo" class="flex justify-center">
        <button type="button" class="rounded border px-3 py-1 text-xs hover:bg-muted disabled:opacity-50" :disabled="carregando" @click="carregar(true)">carregar mais</button>
      </div>
    </div>
  </div>
</template>
