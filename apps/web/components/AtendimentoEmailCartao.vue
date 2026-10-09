<script lang="ts">
// O CARTÃO DO E-MAIL na conversa do /atendimento (RF5/RF6, 08/10/2026).
//
// A Central de e-mail (a do outro dev: /api/mail, aba E-mail › Caixas) guarda
// a caixa inteira; a PONTE (services/mail_atendimento/ponte.py) leva à
// conversa da loja só o e-mail das lojas, já PROTEGIDO (links de acesso
// tirados, códigos mascarados, nunca HTML). Cada e-mail vira uma mensagem
// com o resumo em `mensagem.email` (pasta, alias, assunto, suspeito); o
// resto — remetente, loja, data, o texto inteiro, os anexos, os motivos do
// golpe, "Abrir no Tuta" — vem de GET /api/atendimento/email/conversas/{id}/emails
// (no escopo da equipe), lido pela conversa e passado aqui em `cartao`.
//
//   - a PASTA (problema/reclamação em destaque; vendas = "só histórico");
//   - o DESTINATÁRIO → a LOJA ("16tr@tuta.com → JLAS2");
//   - a faixa vermelha "Remetente pode ser falso" (o aviso de golpe) e o
//     Reply-To diferente do remetente;
//   - "ver o e-mail inteiro": o texto protegido e os anexos (só baixar);
//   - "responder este e-mail": a caixa de baixo passa a responder ESTE
//     e-mail (pelo endereço que recebeu — a prévia mostra antes do Enviar).
//
// A nossa resposta (`email.tipo = 'resposta'`) mostra De → Para, o assunto e
// em que pé está na fila do Mac. Funções puras aqui em cima
// (tests/atendimento-mail-atendimento.cjs).

export type EmailDaMensagem = {
  tipo: 'recebido' | 'resposta'
  // o e-mail da Central (no recebido, ele mesmo; na resposta, o respondido)
  message_id?: string | null
  mailbox_id?: string | null
  pasta?: string | null
  finalidade?: string | null
  destaque?: boolean
  alias?: string | null
  assunto?: string | null
  suspeito?: boolean
  anexos?: number
  codigo_mascarado?: boolean
  links_removidos?: number
  protocolo?: string | null
  // o aviso da plataforma com API, na conversa da API do pedido
  aviso_api?: boolean
  // respondido direto no Tuta (fora do DaVinci)
  fora_do_davinci?: boolean
  // só na resposta (a fila da Central)
  de?: string | null
  para?: string | null
  modo?: string | null
  job_id?: string | null
  status?: string | null
  codigo?: string | null
}

export type AnexoDoEmail = { id: string; tamanho: number | null; filename?: string | null; content_type?: string | null }
export type CartaoEmail = {
  id: string
  estado: string
  direcao: string | null
  recebido_em: string | null
  pasta: string | null
  plataforma: string | null
  finalidade: string | null
  finalidade_rotulo: string | null
  destaque: boolean
  so_historico: boolean
  alias: string | null
  loja: string | null
  integration_id: string | null
  // A loja é a ficha do cadastro, sem integração (a conversa fica sem a API).
  sem_integracao?: boolean
  motivo: string | null
  motivo_texto: string | null
  sugestoes: { store_info_id: string | null; nome: string | null; plataforma: string; integration_id: string | null; sugestao: boolean }[]
  suspeito: boolean
  suspeito_motivos: string[]
  conversa_id: string | null
  pedido: string | null
  pedidos_citados: { pedido: string; existe: boolean; plataforma: string | null }[]
  protocolo: string | null
  tipo_caixa: string | null
  tipo_caixa_rotulo: string | null
  vinculado_por: string | null
  alertas: { codigo: string; texto: string }[]
  codigo_mascarado: boolean
  abrir_no_tuta: string | null
  // com o texto (o cartão da conversa e o detalhe da fila)
  de?: string | null
  de_nome?: string | null
  reply_to?: string[]
  assunto?: string | null
  texto?: string | null
  links_removidos?: number
  anexos?: AnexoDoEmail[]
}
export type Chamado = {
  conversa_id: string
  protocolo: string | null
  marca: string | null
  marca_nome: string | null
  tipo: string | null
  tipo_rotulo: string | null
  situacao: string
  ultima_mensagem_em: string | null
}
export type CartoesDaConversa = { emails: CartaoEmail[]; chamado: Chamado | null; outros_chamados: Chamado[] }

// A mensagem tem CARTÃO de e-mail? A nossa resposta, ou um e-mail recebido de
// verdade (com o id da Central). As notas de sistema da ponte ("vinculado ao
// pedido", "chamado agrupado") também levam `mensagem.email`, sem e-mail:
// essas continuam a linha do meio de sempre.
export function ehCartaoDeEmail(e: { tipo?: string; message_id?: string | null } | null | undefined): boolean {
  if (!e) return false
  return e.tipo === 'resposta' || (e.tipo === 'recebido' && !!e.message_id)
}

// Em que pé está a nossa resposta na fila da Central (o status do job dela).
export const STATUS_DA_RESPOSTA: Record<string, string> = {
  queued: 'na fila do Mac',
  leased: 'saindo pelo Mac',
  sent: 'aceita pelo Tuta',
  failed: 'não saiu',
  uncertain: 'pode ter saído — confira no Tuta',
}

export function rotuloDaPasta(e: Pick<EmailDaMensagem, 'pasta' | 'finalidade'> | null | undefined): { texto: string; destaque: boolean; soHistorico: boolean } {
  const pasta = (e?.pasta || '').trim()
  const destaque = e?.finalidade === 'problema' || e?.finalidade === 'reclamacao'
  return { texto: pasta || 'sem pasta', destaque, soHistorico: e?.finalidade === 'vendas' }
}

// "16tr@tuta.com → JLAS2" (sem loja: só o endereço).
export function destinoDaLoja(alias: string | null | undefined, loja: string | null | undefined): string {
  const a = (alias || '').trim()
  const l = (loja || '').trim()
  if (a && l) return `${a} → ${l}`
  return a || l || 'sem endereço da caixa'
}

export function remetenteLegivel(c: Pick<CartaoEmail, 'de' | 'de_nome'> | null | undefined): string {
  const de = (c?.de || '').trim()
  const nome = (c?.de_nome || '').trim()
  if (de && nome && nome.toLowerCase() !== de.toLowerCase()) return `${nome} <${de}>`
  return de || nome || '—'
}

export function tamanhoLegivel(bytes: number | null | undefined): string {
  const n = Number(bytes) || 0
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`
  return `${(n / (1024 * 1024)).toFixed(1).replace('.', ',')} MB`
}

// O anexo SÓ baixa (octet-stream, nosniff, CSP sandbox: nunca abre aqui).
export function urlDoAnexo(id: string): string {
  return `/api/atendimento/email/anexos/${encodeURIComponent(id)}`
}

// A nossa resposta: "sac@marca → cliente@x · modo de teste · na fila do Mac".
export function resumoDaResposta(e: EmailDaMensagem | null | undefined): string {
  if (!e || e.tipo !== 'resposta') return ''
  const partes = [`${e.de || '?'} → ${e.para || '?'}`]
  if (e.modo === 'teste') partes.push('modo de teste')
  if (e.status) partes.push(STATUS_DA_RESPOSTA[e.status] || e.status)
  return partes.join(' · ')
}

// O que a pessoa lê sobre a proteção do texto (nada some sem aviso).
export function avisosDeProtecao(c: { codigo_mascarado?: boolean; links_removidos?: number } | null | undefined): string[] {
  const out: string[] = []
  if (c?.codigo_mascarado) out.push('O código de verificação deste e-mail foi mascarado (••••).')
  const n = Number(c?.links_removidos) || 0
  if (n > 0) out.push(n === 1 ? 'Um link de acesso (login, senha, confirmação) foi tirado do texto.' : `${n} links de acesso (login, senha, confirmação) foram tirados do texto.`)
  return out
}

// As conversas cujos cartões a tela precisa agora: as dos e-mails RECEBIDOS
// que estão na linha do tempo (a aberta e as outras da aba), com quantos
// e-mails cada uma tem — mudou o número, relê.
export function conversasComEmail(
  linhas: { tipo: string; m?: { email?: EmailDaMensagem | null }; de?: string | null }[],
  propria: string | null | undefined,
): Record<string, number> {
  const out: Record<string, number> = {}
  for (const l of linhas) {
    if (l.tipo !== 'msg' || l.m?.email?.tipo !== 'recebido' || !l.m.email.message_id) continue
    const id = l.de || propria
    if (id) out[id] = (out[id] || 0) + 1
  }
  return out
}

export function urlDosCartoes(conversaId: string): string {
  return `/api/atendimento/email/conversas/${encodeURIComponent(conversaId)}/emails`
}
</script>

<script setup lang="ts">
import { ChevronDown, ChevronUp, Download, ExternalLink, Loader2, Mail, Paperclip, Reply, ShieldAlert, TriangleAlert } from 'lucide-vue-next'
import { fmtDataHora } from '~/components/AtendimentoPlataforma.vue'

const props = defineProps<{
  email: EmailDaMensagem
  // O cartão completo (GET …/conversas/{id}/emails); null enquanto lê.
  cartao?: CartaoEmail | null
  // O texto da mensagem aqui dentro (o aviso da plataforma é a linha do meio, sem balão).
  mostrarTexto?: boolean
  texto?: string | null
  // A caixa de baixo pode responder este e-mail (quem mexe, conversa de e-mail).
  podeResponder?: boolean
  // Este é o e-mail que a caixa vai responder agora.
  respondendo?: boolean
}>()
const emit = defineEmits<{ (e: 'responder', messageId: string): void }>()
const { url } = useApi()

const aberto = ref(false)
const pasta = computed(() => rotuloDaPasta(props.cartao ?? props.email))
const suspeito = computed(() => !!(props.email.suspeito || props.cartao?.suspeito))
const replyTo = computed(() => props.cartao?.reply_to || [])
const protecao = computed(() => avisosDeProtecao(props.cartao ?? props.email))
const anexos = computed(() => props.cartao?.anexos || [])
// Nunca no aviso da plataforma que veio para a conversa da API (o texto iria
// para o comprador pelo chat da plataforma, não para o e-mail).
const respondivel = computed(() => !!props.podeResponder && !!props.email.message_id && !props.email.aviso_api && !suspeito.value && !pasta.value.soHistorico)
</script>

<template>
  <div class="w-full space-y-1 text-xs" data-email-cartao>
    <!-- a nossa resposta pela fila da Central -->
    <div v-if="email.tipo === 'resposta'" class="flex flex-wrap items-center gap-1 text-[11px] text-muted-foreground" data-email-resposta>
      <Mail class="size-3 shrink-0" aria-hidden="true" />
      <span class="break-all">{{ resumoDaResposta(email) }}</span>
      <span v-if="email.assunto" class="truncate" :title="email.assunto">· {{ email.assunto }}</span>
    </div>

    <template v-else>
      <div class="flex flex-wrap items-center gap-1">
        <Mail class="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
        <span
          class="rounded px-1.5 py-px text-[11px] font-medium"
          :class="pasta.destaque ? 'bg-red-500/15 text-red-700 dark:text-red-300' : 'bg-muted text-muted-foreground'"
          :title="`pasta: ${pasta.texto}`"
        >{{ pasta.texto }}</span>
        <span v-if="pasta.soHistorico" class="rounded bg-sky-500/15 px-1.5 py-px text-[11px] text-sky-800 dark:text-sky-300" title="e-mail de vendas da plataforma: fica como histórico, sem pendência — não é para responder" data-email-historico>só histórico</span>
        <span v-if="email.aviso_api" class="rounded bg-muted px-1.5 py-px text-[11px] text-muted-foreground" title="a plataforma tem API: o aviso por e-mail fica aqui, na conversa da API do pedido">aviso por e-mail</span>
        <span v-if="email.protocolo || cartao?.protocolo" class="rounded bg-teal-500/15 px-1.5 py-px text-[11px] font-medium text-teal-800 dark:text-teal-300" title="protocolo do chamado do site">{{ email.protocolo || cartao?.protocolo }}</span>
        <span class="min-w-0 truncate text-[11px] text-muted-foreground" :title="`recebido por ${email.alias || cartao?.alias || '?'}`">para {{ destinoDaLoja(cartao?.alias ?? email.alias, cartao?.loja) }}</span>
        <span v-if="cartao?.sem_integracao" class="rounded bg-muted px-1.5 py-px text-[11px] text-muted-foreground" title="a loja do cadastro não está ligada a uma integração: o pedido é procurado só no espelho do Bling (ligue em Cadastros › Lojas)" data-email-sem-integracao>loja sem integração</span>
        <span v-if="email.fora_do_davinci" class="rounded bg-amber-500/15 px-1.5 py-px text-[11px] text-amber-800 dark:text-amber-300" title="alguém respondeu direto no Tuta, fora do DaVinci">respondido no Tuta</span>
        <span v-if="email.anexos" class="inline-flex items-center gap-0.5 text-[11px] text-muted-foreground"><Paperclip class="size-3" />{{ email.anexos }}</span>
      </div>

      <!-- aviso de golpe: o e-mail pode não ser de quem diz ser -->
      <div v-if="suspeito" class="space-y-0.5 rounded border border-red-500/50 bg-red-500/10 px-1.5 py-1 text-red-700 dark:text-red-300" data-email-suspeito>
        <div class="flex items-start gap-1 font-medium">
          <ShieldAlert class="mt-px size-3.5 shrink-0" aria-hidden="true" />
          <span>Remetente pode ser falso — não clique em links, não responda e confira no Tuta.</span>
        </div>
        <ul v-if="cartao?.suspeito_motivos?.length" class="list-inside list-disc pl-4">
          <li v-for="(mo, i) in cartao.suspeito_motivos" :key="i">{{ mo }}</li>
        </ul>
      </div>
      <div v-if="replyTo.length" class="flex items-start gap-1 text-amber-800 dark:text-amber-300" data-email-reply-to>
        <TriangleAlert class="mt-px size-3 shrink-0" aria-hidden="true" />
        <span>Pede a resposta para outro endereço: <span class="break-all font-medium">{{ replyTo.join(', ') }}</span> — confira antes de responder.</span>
      </div>

      <div class="flex flex-wrap gap-x-2 text-[11px]">
        <span class="min-w-0 break-all"><span class="text-muted-foreground">De:</span> {{ cartao ? remetenteLegivel(cartao) : '…' }}</span>
        <span v-if="cartao?.recebido_em" class="text-muted-foreground">{{ fmtDataHora(cartao.recebido_em) }}</span>
      </div>
      <div class="break-words font-medium" :title="email.assunto || cartao?.assunto || ''">{{ email.assunto || cartao?.assunto || '(sem assunto)' }}</div>
      <div v-if="mostrarTexto && texto" class="whitespace-pre-wrap break-words text-sm leading-relaxed">{{ texto }}</div>

      <div class="flex flex-wrap items-center gap-2 pt-0.5">
        <button v-if="cartao" type="button" class="inline-flex items-center gap-0.5 underline hover:text-foreground" :aria-expanded="aberto" @click="aberto = !aberto">
          <component :is="aberto ? ChevronUp : ChevronDown" class="size-3" />{{ aberto ? 'fechar o e-mail' : 'ver o e-mail inteiro' }}
        </button>
        <Loader2 v-else class="size-3 animate-spin text-muted-foreground" aria-label="lendo o e-mail" />
        <button
          v-if="respondivel"
          type="button"
          class="inline-flex items-center gap-0.5 underline hover:text-foreground"
          :class="respondendo ? 'font-semibold text-primary' : ''"
          title="a caixa de baixo responde ESTE e-mail (pelo endereço que recebeu)"
          data-email-responder-este
          @click="emit('responder', email.message_id || '')"
        ><Reply class="size-3" />{{ respondendo ? 'respondendo este' : 'responder este e-mail' }}</button>
        <a
          v-if="cartao?.abrir_no_tuta"
          :href="cartao.abrir_no_tuta"
          target="_blank"
          rel="noopener noreferrer"
          class="inline-flex items-center gap-0.5 underline hover:text-foreground"
          title="abre o e-mail no Tuta (lá ele fica marcado como lido)"
          data-email-abrir-tuta
        ><ExternalLink class="size-3" />Abrir no Tuta</a>
      </div>

      <div v-if="aberto && cartao" class="space-y-1.5 rounded-md border bg-background p-2" data-email-aberto>
        <div class="grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5 text-[11px]">
          <span class="text-muted-foreground">De</span><span class="break-all">{{ remetenteLegivel(cartao) }}</span>
          <span class="text-muted-foreground">Para</span><span class="break-all">{{ destinoDaLoja(cartao.alias, cartao.loja) }}</span>
          <span class="text-muted-foreground">Pasta</span><span>{{ cartao.pasta || '—' }}<template v-if="cartao.finalidade_rotulo"> ({{ cartao.finalidade_rotulo }})</template></span>
          <span class="text-muted-foreground">Recebido</span><span>{{ fmtDataHora(cartao.recebido_em) }}</span>
          <template v-if="cartao.pedido"><span class="text-muted-foreground">Pedido</span><span>{{ cartao.pedido }}</span></template>
          <template v-if="cartao.protocolo"><span class="text-muted-foreground">Protocolo</span><span>{{ cartao.protocolo }}<template v-if="cartao.tipo_caixa_rotulo"> · {{ cartao.tipo_caixa_rotulo }}</template></span></template>
        </div>
        <div v-for="a in cartao.alertas" :key="a.codigo" class="flex items-start gap-1 text-amber-800 dark:text-amber-300">
          <TriangleAlert class="mt-px size-3 shrink-0" aria-hidden="true" /><span>{{ a.texto }}</span>
        </div>
        <div v-for="(p, i) in protecao" :key="i" class="text-[11px] text-muted-foreground" data-email-protecao>{{ p }}</div>
        <!-- texto só (nunca HTML), já protegido no servidor -->
        <pre class="max-h-[360px] overflow-y-auto whitespace-pre-wrap break-words font-sans text-xs leading-relaxed">{{ cartao.texto || '(e-mail sem texto)' }}</pre>
        <div v-if="anexos.length" class="space-y-0.5">
          <div class="font-medium">Anexos (só baixar)</div>
          <a
            v-for="an in anexos"
            :key="an.id"
            :href="url(urlDoAnexo(an.id))"
            class="flex max-w-full items-center gap-1 underline"
            rel="noopener noreferrer"
            data-email-anexo
          >
            <Download class="size-3 shrink-0" aria-hidden="true" />
            <span class="truncate">{{ an.filename || 'anexo' }}</span>
            <span class="shrink-0 text-muted-foreground no-underline">{{ tamanhoLegivel(an.tamanho) }}</span>
          </a>
        </div>
      </div>
    </template>
  </div>
</template>
