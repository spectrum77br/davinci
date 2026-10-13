<script lang="ts">
// "E-MAILS DA VENDA" no painel ④ (RF1/RF5, 09/10/2026): o resumo dos e-mails
// do Tuta que a ponte ligou à conversa aberta ou às conversas da mesma venda
// (a família das abas: o mesmo comprador e pedido, na mesma loja). Uma linha
// por CAIXA — o endereço que recebeu → a loja achada no cadastro —, com as
// PASTAS de origem e quantos e-mails. Só o resumo: o e-mail em si (texto
// protegido) fica no cartão da linha do tempo e na aba E-mail.
// Rota: GET /api/atendimento/email/conversas/{id}/emails-da-venda
// (routers/atendimento_email.py). Aparece só quando a conversa tem e-mail
// (`sinalDeEmails`, que a conversa calcula e o painel passa). Funções puras
// aqui em cima (tests/atendimento-mail-atendimento.cjs).
//
// NO CHAT (`chat`, 13/10/2026 — o dono: "o e-mail aparece só na aba de
// e-mail; a do chat aparece só no chat"): nada do e-mail (caixa, pasta,
// assunto, remetente, texto). A rota devolve só QUANTOS e-mails a venda tem
// nas conversas de e-mail, quantas esperam resposta e o id delas; o bloco diz
// "Esta venda tem N e-mails" e o "abrir na aba E-mail" emite `abrir` com o id
// (o painel sobe como a linha do tempo sobe outra conversa; a página leva a
// conversa de e-mail para a aba E-mail). Sem e-mail na venda, o bloco some.
// Teste: tests/atendimento-email-venda-link.cjs.
import type { Mensagem } from '~/components/AtendimentoPlataforma.vue'

export type PastaDaVenda = { pasta: string | null; finalidade: string | null; destaque: boolean; quantidade: number }
export type CaixaDaVenda = {
  caixa: string | null
  loja: string | null
  plataforma: string | null
  quantidade: number
  ultimo_em: string | null
  pastas: PastaDaVenda[]
}
// Uma conversa de e-mail da venda, vista do CHAT: só o id e números.
export type ConversaDeEmailDaVenda = { id: string; emails: number; aguardando: boolean }
// `aguardando` e `conversas` só vêm no chat (e só quando a venda tem e-mail).
export type EmailsDaVenda = {
  total: number
  caixas: CaixaDaVenda[]
  aguardando?: number
  conversas?: ConversaDeEmailDaVenda[]
}

export function urlEmailsDaVenda(conversaId: string): string {
  return `/api/atendimento/email/conversas/${encodeURIComponent(conversaId)}/emails-da-venda`
}

// A conversa tem e-mail? O número muda quando chega e-mail novo (a aba E-mail
// das abas, ou o e-mail recebido na própria conversa). 0 = sem bloco. Desde
// 13/10/2026 só a conversa de E-MAIL tem os dois — o chat mostra o bloco
// `chat` (o painel decide pelo `conversa.email`).
export function sinalDeEmails(
  abas: { abas?: { chave: string; total: number }[] } | null | undefined,
  mensagens: Pick<Mensagem, 'email'>[] | null | undefined,
): number {
  const daAba = (abas?.abas || []).find((a) => a.chave === 'email')?.total || 0
  const proprios = (mensagens || []).filter((m) => m.email?.tipo === 'recebido').length
  return daAba + proprios
}

// "problema ml (2) · vendas ml" — a pasta sem nome vira "sem pasta".
export function rotuloDasPastas(pastas: PastaDaVenda[]): string {
  return pastas.map((p) => `${p.pasta || 'sem pasta'}${p.quantidade > 1 ? ` (${p.quantidade})` : ''}`).join(' · ')
}

// As conversas de e-mail que o chat pode abrir (a rota já manda a que espera
// resposta primeiro). Resposta velha/sem a lista = nenhuma.
export function conversasDoChat(d: EmailsDaVenda | null | undefined): ConversaDeEmailDaVenda[] {
  return (d?.conversas || []).filter((c) => !!c?.id)
}

// "Esta venda tem 2 e-mails · 1 esperando resposta" — só números. null = a
// venda não tem conversa de e-mail (o bloco do chat não aparece).
export function fraseDoChat(d: EmailsDaVenda | null | undefined): string | null {
  if (!conversasDoChat(d).length) return null
  const total = d?.total || 0
  const base = total > 0 ? `Esta venda tem ${total} e-mail${total === 1 ? '' : 's'}` : 'Esta venda tem conversa de e-mail'
  const esperando = d?.aguardando || 0
  return esperando > 0 ? `${base} · ${esperando} esperando resposta` : base
}

// O botão de cada conversa quando são várias ("conversa 2 · 3 e-mails · esperando resposta").
export function rotuloDaConversa(c: ConversaDeEmailDaVenda, i: number): string {
  const partes = [`conversa ${i + 1}`, `${c.emails} e-mail${c.emails === 1 ? '' : 's'}`]
  if (c.aguardando) partes.push('esperando resposta')
  return partes.join(' · ')
}
</script>

<script setup lang="ts">
import { ExternalLink, Loader2, Mail } from 'lucide-vue-next'
import { fmtDataHora, plataformaInfo } from '~/components/AtendimentoPlataforma.vue'
import { destinoDaLoja } from '~/components/AtendimentoEmailCartao.vue'

const props = defineProps<{
  conversaId: string
  // `sinalDeEmails` da conversa: muda → relê (chegou e-mail).
  sinal: number
  // A conversa aberta é de CHAT: só os números e o link (nada do e-mail).
  chat?: boolean
}>()
const emit = defineEmits<{
  // Abrir a conversa de e-mail da venda (na aba E-mail) — só no chat.
  (e: 'abrir', conversaId: string): void
}>()

const { api } = useApi()
const dados = ref<EmailsDaVenda | null>(null)
const carregando = ref(false)
const erro = ref(false)
let geracao = 0

// No chat: a frase e as conversas que abrem (vazio = o bloco não aparece).
const fraseChat = computed(() => (props.chat ? fraseDoChat(dados.value) : null))
const conversasChat = computed(() => (props.chat ? conversasDoChat(dados.value) : []))

async function carregar() {
  const g = ++geracao
  carregando.value = true
  try {
    const r = await api<EmailsDaVenda>(urlEmailsDaVenda(props.conversaId))
    if (g !== geracao) return
    dados.value = r
    erro.value = false
  } catch {
    // O bloco é ajuda: falhou, fica o que já tinha (ou a frase curta).
    if (g === geracao) erro.value = true
  } finally {
    if (g === geracao) carregando.value = false
  }
}
onMounted(() => { void carregar() })
watch(() => [props.conversaId, props.sinal], () => { void carregar() })
</script>

<template>
  <!-- NO CHAT: só quantos e o link para a aba E-mail (nada do e-mail); sem e-mail, nada -->
  <section v-if="chat && fraseChat" class="space-y-1.5" data-painel-emails-venda data-emails-venda-chat>
    <div class="flex items-center gap-1.5 text-[13px] font-semibold">
      <Mail class="size-4 text-muted-foreground" /> E-mails da venda
    </div>
    <div class="rounded-md border px-2.5 py-1.5 text-xs">
      <p data-email-venda-frase>{{ fraseChat }}</p>
      <button
        v-if="conversasChat.length === 1"
        type="button"
        class="mt-1 inline-flex items-center gap-1 font-medium text-primary hover:underline"
        title="a conversa de e-mail abre na aba E-mail"
        data-email-venda-abrir
        @click="emit('abrir', conversasChat[0]!.id)"
      >
        abrir na aba E-mail <ExternalLink class="size-3" />
      </button>
      <ul v-else class="mt-1 space-y-0.5">
        <li v-for="(c, i) in conversasChat" :key="c.id">
          <button
            type="button"
            class="inline-flex items-center gap-1 text-left hover:underline"
            :class="c.aguardando ? 'font-medium text-primary' : 'text-muted-foreground'"
            title="a conversa de e-mail abre na aba E-mail"
            data-email-venda-abrir
            @click="emit('abrir', c.id)"
          >
            {{ rotuloDaConversa(c, i) }} — abrir na aba E-mail <ExternalLink class="size-3 shrink-0" />
          </button>
        </li>
      </ul>
    </div>
  </section>
  <section v-else-if="!chat" class="space-y-1.5" data-painel-emails-venda>
    <div class="flex items-center gap-1.5 text-[13px] font-semibold">
      <Mail class="size-4 text-muted-foreground" /> E-mails da venda
      <span v-if="dados?.total" class="ml-auto rounded-full bg-muted px-1.5 text-[10px] font-semibold tabular-nums text-muted-foreground">{{ dados.total }}</span>
      <Loader2 v-else-if="carregando" class="ml-auto size-3.5 animate-spin text-muted-foreground" />
    </div>
    <div v-if="erro && !dados" class="text-[11px] text-muted-foreground">Não consegui ler os e-mails da venda agora.</div>
    <div v-else-if="dados && !dados.caixas.length" class="text-[11px] text-muted-foreground">Nenhum e-mail das lojas ligado a esta venda.</div>
    <ul v-else-if="dados" class="space-y-1">
      <li v-for="(c, i) in dados.caixas" :key="`${c.caixa}-${c.loja}-${i}`" class="rounded-md border px-2.5 py-1.5 text-xs" data-email-venda-caixa>
        <div class="flex flex-wrap items-center gap-1.5">
          <span class="min-w-0 break-all font-medium">{{ destinoDaLoja(c.caixa, c.loja) }}</span>
          <span v-if="c.plataforma" class="text-muted-foreground">· {{ plataformaInfo(c.plataforma).curto }}</span>
          <span class="ml-auto shrink-0 tabular-nums text-muted-foreground" :title="c.ultimo_em ? `o último em ${fmtDataHora(c.ultimo_em)}` : ''">{{ c.quantidade }} e-mail{{ c.quantidade === 1 ? '' : 's' }}</span>
        </div>
        <div class="mt-0.5" :class="c.pastas.some((p) => p.destaque) ? 'text-red-700 dark:text-red-300' : 'text-muted-foreground'" :title="c.pastas.some((p) => p.destaque) ? 'veio de pasta de problema/reclamação' : ''" data-email-venda-pastas>{{ rotuloDasPastas(c.pastas) }}</div>
      </li>
    </ul>
  </section>
</template>
