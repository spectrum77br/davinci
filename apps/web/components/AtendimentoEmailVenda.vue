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
export type EmailsDaVenda = { total: number; caixas: CaixaDaVenda[] }

export function urlEmailsDaVenda(conversaId: string): string {
  return `/api/atendimento/email/conversas/${encodeURIComponent(conversaId)}/emails-da-venda`
}

// A conversa tem e-mail? O número muda quando chega e-mail novo (a aba E-mail
// das abas, ou o e-mail recebido na própria conversa — o aviso da plataforma
// que a ponte grava no chat/pós-venda também conta). 0 = sem bloco.
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
</script>

<script setup lang="ts">
import { Loader2, Mail } from 'lucide-vue-next'
import { fmtDataHora, plataformaInfo } from '~/components/AtendimentoPlataforma.vue'
import { destinoDaLoja } from '~/components/AtendimentoEmailCartao.vue'

const props = defineProps<{
  conversaId: string
  // `sinalDeEmails` da conversa: muda → relê (chegou e-mail).
  sinal: number
}>()

const { api } = useApi()
const dados = ref<EmailsDaVenda | null>(null)
const carregando = ref(false)
const erro = ref(false)
let geracao = 0

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
  <section class="space-y-1.5" data-painel-emails-venda>
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
