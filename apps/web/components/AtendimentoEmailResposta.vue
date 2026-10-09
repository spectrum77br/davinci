<script lang="ts">
// A RESPOSTA DE E-MAIL pela conversa do /atendimento (RF5/RF6, 08/10/2026):
// "como vai sair" acima da caixa de resposta, antes do Enviar —
//
//   De      = o endereço da caixa que RECEBEU o e-mail (nunca o principal no
//             lugar dele); no chamado do site (RF6), o e-mail do TIPO na marca
//             (sac@, atacado@, duvidas@ — `marca_emails`);
//   Para    = o Reply-To válido, senão o remetente; no formulário do site, o
//             cliente lido do corpo (no servidor);
//   Assunto = "Re: " + o original (+ o [protocolo] no site);
//   e, embaixo do texto da pessoa, a ASSINATURA da loja/marca e a CITAÇÃO
//   curta "Em DD/MM/AAAA HH:MM, Fulano escreveu:".
//
// A resposta sai pela caixa de sempre (POST /conversas/{id}/responder, o
// caminho único, com `mail_message_id` e `confirmar_nao_responde`) e entra na
// FILA da Central de e-mail (a do outro dev), que o Mac envia — sempre por
// clique de pessoa, nunca pela IA. A prévia (GET …/email/conversas/{id}/previa)
// traz TODAS as travas de agora: as que impedem viram o motivo da caixa
// desligada; a "não responder" (o endereço de aviso da plataforma) é a única
// que se passa por cima, marcando "enviar mesmo assim".
// Funções puras aqui em cima (tests/atendimento-mail-atendimento.cjs).

export type BloqueioEmail = { codigo: string; texto: string; confirmavel: boolean }
export type PreviaEmail = {
  mail_message_id: string | null
  de: string | null
  para: string | null
  formulario: boolean
  assunto: string
  assinatura: string
  citacao_cabeca: string
  citacao: string
  // `caixa` = caixa privada (a regra da Central); `teste`/`real` = caixa da empresa
  modo: string
  pode_mexer: boolean
  bloqueios: BloqueioEmail[]
  avisos: string[]
  abrir_no_tuta: string | null
}

// O 409 que a pessoa pode passar por cima confirmando (responder.RECUSA_NAO_RESPONDE).
export const CODIGO_NAO_RESPONDE = 'remetente_nao_responde'
// O mesmo e-mail já tem resposta pela caixa crua da Central (responder.RECUSA_JA_RESPONDIDO).
export const CODIGO_JA_RESPONDIDO = 'ja_respondido_pela_caixa'
// Todas as que se passam confirmando (responder.CONFIRMAVEIS): o mesmo
// `confirmar_nao_responde` do corpo vale para elas.
export const CODIGOS_CONFIRMAVEIS = new Set([CODIGO_NAO_RESPONDE, CODIGO_JA_RESPONDIDO])
// As travas GERAIS que vêm no `envio` da conversa e valem para qualquer
// e-mail dela (as do e-mail em si dependem de QUAL e-mail se responde: quem
// diz é a prévia).
export const TRAVAS_GERAIS_DO_EMAIL = new Set([
  'envio_desligado',
  'conversa_bloqueada',
  'envio_em_andamento',
  'canal_sem_envio',
  'somente_leitura',
])

// A conversa é de e-mail da PONTE: canal `email`, a API diz que a conversa
// é da ponte (`email_da_ponte`: `dados.fonte='tuta'` + `dados.mail`) e há
// mensagem com o resumo do e-mail (`mensagem.email`). O e-mail do Tuta de
// antes da ponte (sem o resumo) e a conversa da Amazon que veio pelo Gmail
// (canal `email`, mas não da ponte) continuam sem envio por aqui.
export function ehEmailDaPonte(
  conversa: { canal?: string | null; email_da_ponte?: boolean | null } | null | undefined,
  mensagens: { email?: { tipo?: string } | null }[] | null | undefined,
): boolean {
  if (!conversa || conversa.canal !== 'email' || conversa.email_da_ponte !== true) return false
  return (mensagens || []).some((m) => !!m.email?.tipo)
}

// O que trava de verdade (a "não responder" passa com a confirmação).
export function travasQueImpedem(p: PreviaEmail | null | undefined, confirmouNaoResponde: boolean): BloqueioEmail[] {
  if (!p) return []
  return (p.bloqueios || []).filter((b) => !(b.confirmavel && confirmouNaoResponde))
}

// A frase que desliga a caixa ('' = pode enviar; null = a prévia não veio).
export function travaDaPrevia(p: PreviaEmail | null | undefined, confirmouNaoResponde: boolean): string | null {
  if (!p) return null
  const t = travasQueImpedem(p, confirmouNaoResponde)[0]
  if (!t) return ''
  return t.confirmavel ? `${t.texto} Para mandar mesmo assim, marque "enviar mesmo assim" acima.` : t.texto
}

export function confirmavelDa(p: PreviaEmail | null | undefined): BloqueioEmail | null {
  return p?.bloqueios?.find((b) => b.confirmavel) ?? null
}

// O texto da caixinha "enviar mesmo assim" (diz O QUE a pessoa está confirmando).
export function rotuloDaConfirmacao(p: PreviaEmail | null | undefined): string {
  const codigos = new Set((p?.bloqueios || []).filter((b) => b.confirmavel).map((b) => b.codigo))
  if (codigos.size > 1) return 'Enviar mesmo assim (conferi os avisos acima).'
  if (codigos.has(CODIGO_JA_RESPONDIDO)) return 'Enviar mesmo assim (conferi: a resposta que já saiu pela caixa da Central não basta).'
  return 'Enviar mesmo assim (sei que o endereço é "não responder" e que provavelmente ninguém lê).'
}

export function urlDaPrevia(conversaId: string, mailMessageId: string | null | undefined): string {
  const q = mailMessageId ? `?mail_message_id=${encodeURIComponent(mailMessageId)}` : ''
  return `/api/atendimento/email/conversas/${encodeURIComponent(conversaId)}/previa${q}`
}

// O pedaço do corpo do POST /responder que é do e-mail.
export function corpoDoEmail(mailMessageId: string | null | undefined, confirmouNaoResponde: boolean): { mail_message_id?: string; confirmar_nao_responde?: boolean } {
  const out: { mail_message_id?: string; confirmar_nao_responde?: boolean } = {}
  if (mailMessageId) out.mail_message_id = mailMessageId
  if (confirmouNaoResponde) out.confirmar_nao_responde = true
  return out
}

// O tamanho da resposta de e-mail (responder.RESPOSTA_MAX_CARACTERES): o
// `limite_caracteres` do envio da conversa é o do canal da plataforma.
export const LIMITE_RESPOSTA_EMAIL = 10_000

// Os avisos da prévia sem o que o selo "modo de teste" já diz.
export function avisosVisiveis(p: Pick<PreviaEmail, 'modo' | 'avisos'> | null | undefined): string[] {
  return (p?.avisos || []).filter((a) => !(p?.modo === 'teste' && /^modo de teste/i.test(a)))
}

export function rotuloDoModo(modo: string | null | undefined): string {
  if (modo === 'teste') return 'modo de teste: só os endereços de teste da caixa recebem'
  if (modo === 'real') return 'envio real'
  return ''
}
</script>

<script setup lang="ts">
import { ExternalLink, FlaskConical, Loader2, Lock, Mail, TriangleAlert } from 'lucide-vue-next'
import { erroDaApi } from '~/components/AtendimentoPlataforma.vue'

const props = defineProps<{
  conversaId: string
  // O e-mail que a caixa responde (null = o mais novo que não é nosso).
  mailMessageId: string | null
  // Muda quando a conversa é relida (mensagem nova, envio): a prévia acompanha.
  versao: number | string
}>()
const confirmouNaoResponde = defineModel<boolean>('confirmouNaoResponde', { default: false })
// A frase que desliga a caixa de baixo ('' = pode; null = sem prévia: vale o `envio` da conversa).
const trava = defineModel<string | null>('trava', { default: null })
const emit = defineEmits<{ (e: 'responderMaisNovo'): void }>()

const { api } = useApi()
const previa = ref<PreviaEmail | null>(null)
const carregando = ref(false)
const erro = ref<string | null>(null)
const verCitacao = ref(false)
let geracao = 0

async function carregar() {
  const g = ++geracao
  carregando.value = true
  erro.value = null
  try {
    const p = await api<PreviaEmail>(urlDaPrevia(props.conversaId, props.mailMessageId))
    if (g !== geracao) return
    previa.value = p
  } catch (e: any) {
    if (g !== geracao) return
    previa.value = null
    erro.value = erroDaApi(e, 'Não consegui montar a prévia do e-mail').texto
  } finally {
    if (g === geracao) carregando.value = false
  }
}
// Outra conversa ou outro e-mail: a confirmação do "não responder" não vale mais.
watch(() => [props.conversaId, props.mailMessageId], () => {
  confirmouNaoResponde.value = false
  previa.value = null
})
watch(() => [props.conversaId, props.mailMessageId, props.versao], () => { void carregar() }, { immediate: true })
watch(() => travaDaPrevia(previa.value, confirmouNaoResponde.value), (t) => { trava.value = t }, { immediate: true })

const travas = computed(() => travasQueImpedem(previa.value, confirmouNaoResponde.value))
const confirmavel = computed(() => confirmavelDa(previa.value))
const rotuloConfirmar = computed(() => rotuloDaConfirmacao(previa.value))
const avisos = computed(() => avisosVisiveis(previa.value))
</script>

<template>
  <div class="space-y-1 rounded-md border border-slate-300/70 bg-slate-50 px-2.5 py-1.5 text-xs dark:border-slate-700 dark:bg-slate-900/30" data-email-previa>
    <div class="flex flex-wrap items-center gap-1.5">
      <Mail class="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
      <span class="font-medium">Resposta por e-mail</span>
      <span v-if="previa?.modo === 'teste'" class="inline-flex items-center gap-0.5 rounded bg-violet-500/15 px-1.5 text-[11px] text-violet-800 dark:text-violet-300" :title="rotuloDoModo(previa.modo)"><FlaskConical class="size-3" />modo de teste</span>
      <Loader2 v-if="carregando" class="size-3 animate-spin" />
      <button v-if="mailMessageId" type="button" class="ml-auto underline" @click="emit('responderMaisNovo')">responder o mais novo</button>
      <a v-if="previa?.abrir_no_tuta" :href="previa.abrir_no_tuta" target="_blank" rel="noopener noreferrer" class="inline-flex items-center gap-0.5 underline" :class="mailMessageId ? '' : 'ml-auto'" title="abre o e-mail respondido no Tuta (lá ele fica lido)"><ExternalLink class="size-3" />Abrir no Tuta</a>
    </div>
    <div v-if="erro" class="text-red-700 dark:text-red-300">{{ erro }}</div>
    <template v-else-if="previa">
      <div v-if="previa.mail_message_id" class="grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5 text-[11px]">
        <span class="text-muted-foreground">De</span><span class="break-all" data-email-de>{{ previa.de || '—' }}</span>
        <span class="text-muted-foreground">Para</span><span class="break-all" data-email-para>{{ previa.para || '—' }}<template v-if="previa.formulario"> (o cliente do formulário do site)</template></span>
        <span class="text-muted-foreground">Assunto</span><span class="break-words">{{ previa.assunto }}</span>
      </div>
      <div v-if="previa.assinatura || previa.citacao_cabeca" class="text-[11px] text-muted-foreground">
        Vai junto, embaixo do seu texto: {{ previa.assinatura && previa.citacao_cabeca ? 'a assinatura e a citação do e-mail' : previa.assinatura ? 'a assinatura' : 'a citação do e-mail' }}
        · <button type="button" class="underline" :aria-expanded="verCitacao" @click="verCitacao = !verCitacao">{{ verCitacao ? 'esconder' : 'ver' }}</button>
      </div>
      <div v-if="verCitacao" class="whitespace-pre-wrap border-l-2 pl-2 text-[11px] text-muted-foreground" data-email-junto>{{ [previa.assinatura, previa.citacao_cabeca && `${previa.citacao_cabeca}\n${previa.citacao}`].filter(Boolean).join('\n\n') }}</div>
      <div v-for="t in travas" :key="t.codigo" class="flex items-start gap-1 text-amber-900 dark:text-amber-200" data-email-trava>
        <Lock class="mt-px size-3 shrink-0" aria-hidden="true" /><span>{{ t.texto }}</span>
      </div>
      <label v-if="confirmavel" class="flex items-start gap-1.5 text-amber-900 dark:text-amber-200" data-email-nao-responde>
        <input v-model="confirmouNaoResponde" type="checkbox" class="mt-0.5" />
        <span>{{ rotuloConfirmar }}</span>
      </label>
      <div v-for="(a, i) in avisos" :key="i" class="flex items-start gap-1 text-muted-foreground">
        <TriangleAlert class="mt-px size-3 shrink-0" aria-hidden="true" /><span>{{ a }}</span>
      </div>
    </template>
  </div>
</template>
