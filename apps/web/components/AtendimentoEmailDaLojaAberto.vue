<script lang="ts">
// O E-MAIL DA LOJA ABERTO NA ÁREA PRINCIPAL da Caixa (27/10/2026) — o Eduardo:
// "o e-mail tem que abrir ali na box principal, não ali na esquerda; se clicar
// ali amplia pra cá". O e-mail que NÃO é conversa (a linha informativa e o que a
// ponte segurou como segurança — o código de postagem da devolução) clicado na
// lista da loja (AtendimentoLojaEmails, no meio) ou na lista curta de uma caixa
// da barra abre AQUI, grande: o assunto, quem mandou, a data, a pasta e o "com
// código", o texto inteiro (sem os links de acesso: GET
// /api/atendimento/email/lojas/emails/{id}, a mesma leitura de antes) e os
// anexos pela rota da caixa. A linha da lista fica acesa; o "fechar" volta para
// o "Escolha um e-mail na lista". A conversa de e-mail continua abrindo na
// AtendimentoConversa. Funções puras aqui em cima (tests/atendimento-email-lojas.cjs).
import type { EmailDaCaixa } from '~/components/AtendimentoLojas.vue'

export type EmailDaLojaDetalhe = {
  id: string
  estado?: string | null
  recebido_em?: string | null
  pasta?: string | null
  loja?: string | null
  seguranca?: boolean
  de: string | null
  de_nome?: string | null
  assunto?: string | null
  texto: string | null
  links_removidos?: number
  anexos?: { id: string; filename?: string; content_type?: string; tamanho?: number }[]
}
type Linha = Pick<EmailDaCaixa, 'id' | 'assunto' | 'de' | 'recebido_em' | 'pasta'> & Partial<Pick<EmailDaCaixa, 'de_nome' | 'seguranca' | 'comprador_nome'>>

// "Nome <endereço>"; sem nome (ou o nome é o próprio endereço), só o endereço.
export function remetenteDoEmail(e: { de?: string | null; de_nome?: string | null } | null | undefined): string {
  const de = (e?.de || '').trim()
  const nome = (e?.de_nome || '').trim()
  if (!nome || nome === de) return de
  return de ? `${nome} <${de}>` : nome
}
export function textoDosLinksRemovidos(n: number | null | undefined): string {
  const k = Number(n) || 0
  return k > 0 ? `${k} link(s) de acesso removido(s)` : ''
}
// O cabeçalho: o e-mail lido manda; enquanto carrega (ou no que faltar), a
// linha da lista que já estava na tela.
export function cabecalhoDoEmail(d: EmailDaLojaDetalhe | null | undefined, linha: Linha | null | undefined) {
  const assunto = d?.assunto || linha?.assunto || ''
  return {
    assunto: assunto || linha?.comprador_nome || '(sem assunto)',
    de: remetenteDoEmail(d && (d.de || d.de_nome) ? d : linha),
    recebido_em: d?.recebido_em || linha?.recebido_em || null,
    pasta: d?.pasta || linha?.pasta || null,
    seguranca: !!(d ? d.seguranca : linha?.seguranca),
  }
}
</script>

<script setup lang="ts">
import { Download, Loader2, Mail, Paperclip, RefreshCw, X } from 'lucide-vue-next'
import { erroDaApi, fmtDataHora } from '~/components/AtendimentoPlataforma.vue'
import { tamanhoLegivel } from '~/components/AtendimentoEmailCartao.vue'
import { urlDoAnexoDaCaixa, urlDoEmailDaCaixa } from '~/components/AtendimentoLojaEmails.vue'

const props = defineProps<{
  // O e-mail (sem conversa) aberto.
  emailId: string
  // A linha da lista que foi clicada (o cabeçalho já aparece enquanto carrega).
  linha?: EmailDaCaixa | null
  // A loja escolhida na barra (o nome, para o aria).
  loja?: string | null
}>()
const emit = defineEmits<{ (e: 'fechar'): void }>()

const { api, url } = useApi()

const detalhe = ref<EmailDaLojaDetalhe | null>(null)
const carregando = ref(false)
const erro = ref<string | null>(null)
let geracao = 0

async function carregar() {
  const id = props.emailId
  const g = ++geracao
  detalhe.value = null
  erro.value = null
  if (!id) return
  carregando.value = true
  try {
    const d = await api<EmailDaLojaDetalhe>(urlDoEmailDaCaixa(id))
    if (g === geracao) detalhe.value = d
  } catch (x: any) {
    if (g === geracao) erro.value = erroDaApi(x, 'Não consegui abrir o e-mail').texto
  } finally {
    if (g === geracao) carregando.value = false
  }
}
// Outro e-mail clicado com este aberto: lê o novo (o de antes nunca chega por cima).
watch(() => props.emailId, () => { void carregar() })
onMounted(() => { void carregar() })

const cabecalho = computed(() => cabecalhoDoEmail(detalhe.value, props.linha))
const linksRemovidos = computed(() => textoDosLinksRemovidos(detalhe.value?.links_removidos))
</script>

<template>
  <article class="flex min-h-0 min-w-0 flex-1 flex-col" :aria-label="`E-mail${loja ? ` da loja ${loja}` : ''}: ${cabecalho.assunto}`" data-email-da-loja-aberto>
    <div class="flex shrink-0 flex-wrap items-center gap-2 border-b p-2 text-xs">
      <button
        type="button"
        class="inline-flex items-center gap-1 rounded border px-2 py-1 hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        title="Fechar o e-mail (a lista da loja continua)"
        data-fechar-email-da-loja
        @click="emit('fechar')"
      ><X class="size-3.5" aria-hidden="true" /> fechar o e-mail</button>
      <span class="inline-flex min-w-0 items-center gap-1 text-muted-foreground">
        <Mail class="size-3.5 shrink-0" aria-hidden="true" />E-mail da loja{{ loja ? ` ${loja}` : '' }}, aberto pela barra de lojas: fica fora da lista e dos números do chat.
      </span>
      <button type="button" class="ml-auto rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-50" :disabled="carregando" title="ler de novo" aria-label="ler o e-mail de novo" @click="carregar">
        <RefreshCw class="size-3.5" :class="{ 'animate-spin': carregando }" />
      </button>
    </div>

    <div class="min-h-0 flex-1 overflow-y-auto">
      <div class="mx-auto max-w-3xl space-y-4 p-4">
        <header class="space-y-2 border-b pb-3">
          <div class="flex flex-wrap items-center gap-1.5 text-[11px]">
            <span v-if="cabecalho.pasta" class="rounded bg-muted px-1.5 py-px text-muted-foreground" data-pasta-do-email>{{ cabecalho.pasta }}</span>
            <span v-if="cabecalho.seguranca" class="rounded bg-amber-500/15 px-1.5 py-px text-amber-800 dark:text-amber-300" title="A ponte segurou este e-mail como de segurança (tem código): nesta pasta ele aparece para quem vê a loja" data-com-codigo>com código</span>
            <time v-if="cabecalho.recebido_em" class="ml-auto text-muted-foreground" :datetime="cabecalho.recebido_em">{{ fmtDataHora(cabecalho.recebido_em) }}</time>
          </div>
          <h2 class="break-words text-lg font-semibold leading-snug" data-assunto-do-email>{{ cabecalho.assunto }}</h2>
          <div v-if="cabecalho.de" class="break-all text-sm" data-de-do-email><span class="text-muted-foreground">De</span> {{ cabecalho.de }}</div>
        </header>

        <div v-if="erro" class="rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-700 dark:text-red-300" role="alert">{{ erro }}</div>
        <div v-else-if="!detalhe" class="flex justify-center py-10"><Loader2 class="size-5 animate-spin" /></div>
        <template v-else>
          <p v-if="linksRemovidos" class="text-xs text-muted-foreground" data-links-removidos>{{ linksRemovidos }}</p>
          <pre class="whitespace-pre-wrap break-words font-sans text-sm leading-relaxed" data-texto-do-email>{{ detalhe.texto || '(e-mail sem texto)' }}</pre>
          <section v-if="detalhe.anexos?.length" class="space-y-1 border-t pt-3" aria-label="anexos" data-anexos-do-email>
            <div class="inline-flex items-center gap-1 text-[11px] uppercase tracking-wider text-muted-foreground"><Paperclip class="size-3.5" aria-hidden="true" />Anexos</div>
            <a
              v-for="an in detalhe.anexos"
              :key="an.id"
              :href="url(urlDoAnexoDaCaixa(an.id))"
              class="flex max-w-full items-center gap-1.5 rounded border px-2 py-1 text-sm hover:bg-muted"
              rel="noopener noreferrer"
              :data-anexo-do-email="an.id"
            >
              <Download class="size-3.5 shrink-0" aria-hidden="true" /><span class="truncate underline">{{ an.filename || 'anexo' }}</span><span class="ml-auto shrink-0 text-xs text-muted-foreground">{{ tamanhoLegivel(an.tamanho) }}</span>
            </a>
          </section>
        </template>
      </div>
    </div>
  </article>
</template>
