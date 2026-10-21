<script lang="ts">
// Na conversa de e-mail: resumo de caixas/pastas no painel do pedido.
// Nas demais conversas: botão no cabeçalho, com lista e leitura protegida em
// modal. O servidor cruza plataforma + loja + pedido; nunca o comprador ou
// a caixa inteira. O detalhe revalida cada e-mail na venda antes de ler o
// texto. A linha do tempo do chat não recebe corpos de e-mail.
import type { Mensagem } from '~/components/AtendimentoPlataforma.vue'
import type { CartaoEmail, EmailDaMensagem } from '~/components/AtendimentoEmailCartao.vue'
import { TIPOS_EMAIL } from '~/components/AtendimentoEmailCartao.vue'

export type PastaDaVenda = { pasta: string | null; finalidade: string | null; destaque: boolean; quantidade: number }
export type CaixaDaVenda = {
  caixa: string | null
  loja: string | null
  plataforma: string | null
  quantidade: number
  ultimo_em: string | null
  pastas: PastaDaVenda[]
}
export type EmailsDaVenda = {
  total: number
  caixas: CaixaDaVenda[]
}

export function urlEmailsDaVenda(conversaId: string): string {
  return `/api/atendimento/email/conversas/${encodeURIComponent(conversaId)}/emails-da-venda`
}

export type LeituraDaVenda = {
  estado: 'ok' | 'sem_pedido' | 'sem_loja' | 'indisponivel'
  pedido: string | null
  total: number
  itens: CartaoEmail[]
}
export function urlLeituraDaVenda(conversaId: string, messageId?: string): string {
  const base = `/api/atendimento/email/venda/${encodeURIComponent(conversaId)}`
  return messageId ? `${base}/emails/${encodeURIComponent(messageId)}` : base
}
export function emailDoCartao(c: CartaoEmail): EmailDaMensagem {
  return {
    tipo: 'recebido', message_id: c.id, pasta: c.pasta, finalidade: c.finalidade,
    tipo_email: c.tipo_email, tipo_email_inferido: c.tipo_email_inferido, tipo_pela_pasta: c.tipo_pela_pasta, caso_id: c.caso_id,
    alias: c.alias, assunto: c.assunto, suspeito: c.suspeito, protocolo: c.protocolo,
  }
}

// A categoria pertence ao e-mail, não à pasta nem ao chamado inteiro.
// A API atual sempre informa um dos três tipos; uma resposta inesperada
// continua visível sem receber uma classificação inventada pela interface.
export function categoriasDaVenda(itens: CartaoEmail[]) {
  const categorias = TIPOS_EMAIL.map((t) => ({ ...t, itens: itens.filter((e) => e.tipo_email === t.value) }))
  const semTipo = itens.filter((e) => !TIPOS_EMAIL.some((t) => t.value === e.tipo_email))
  return [
    ...categorias,
    ...(semTipo.length ? [{ value: 'sem_tipo', label: 'Sem classificação', itens: semTipo }] : []),
  ]
}

// Sinal para reler quando chegar e-mail à conversa de E-mail. No chat a
// consulta por pedido é independente deste sinal, inclusive quando ele é 0.
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
import { Loader2, Mail, X } from 'lucide-vue-next'
import { DialogContent, DialogDescription, DialogOverlay, DialogPortal, DialogRoot, DialogTitle, DialogTrigger, TabsContent, TabsList, TabsRoot, TabsTrigger } from 'reka-ui'
import { fmtDataHora, plataformaInfo } from '~/components/AtendimentoPlataforma.vue'
import { destinoDaLoja, rotuloTipoEmail, dicaTipoEmail } from '~/components/AtendimentoEmailCartao.vue'
import AtendimentoEmailCartao from '~/components/AtendimentoEmailCartao.vue'

const props = defineProps<{
  conversaId: string
  // `sinalDeEmails` da conversa: muda → relê (chegou e-mail).
  sinal: number
  // Botão do cabeçalho. O pedido é apenas chave de invalidação da seleção;
  // o servidor resolve e valida o pedido pela conversa.
  toolbar?: boolean
  pedido?: string | null
}>()

const { api } = useApi()
const dados = ref<EmailsDaVenda | null>(null)
const venda = ref<LeituraDaVenda | null>(null)
const carregando = ref(false)
const erro = ref(false)
const aberto = ref(false)
const selecionado = ref<string | null>(null)
const cartao = ref<CartaoEmail | null>(null)
const lendo = ref(false)
const erroLeitura = ref(false)
const categoria = ref('informativo')
let geracao = 0
let leitura = 0
const temEmails = computed(() => venda.value?.estado === 'ok' && !!venda.value.pedido && venda.value.total > 0 && venda.value.itens.length > 0)
const categorias = computed(() => categoriasDaVenda(venda.value?.itens || []))

function limparLeitura() {
  selecionado.value = null
  cartao.value = null
  lendo.value = false
  erroLeitura.value = false
  ++leitura
}
function fechar() {
  aberto.value = false
  limparLeitura()
}
function abrir() {
  categoria.value = categorias.value.find((c) => c.itens.length)?.value || 'informativo'
  limparLeitura()
  aberto.value = true
}

async function carregar() {
  const g = ++geracao
  const id = props.conversaId
  const toolbar = props.toolbar
  fechar()
  dados.value = null
  venda.value = null
  erro.value = false
  if (!id) {
    carregando.value = false
    return
  }
  carregando.value = true
  try {
    if (toolbar) {
      const r = await api<LeituraDaVenda>(urlLeituraDaVenda(id))
      if (g !== geracao || id !== props.conversaId) return
      // A conversa pode ter sido vinculada a outro pedido durante a leitura.
      // Até o cabeçalho atualizar, não apresentar a venda nova sob o pedido antigo.
      if (props.pedido?.trim() && r.pedido?.trim() !== props.pedido.trim()) return
      venda.value = r
    } else {
      const r = await api<EmailsDaVenda>(urlEmailsDaVenda(id))
      if (g !== geracao || id !== props.conversaId) return
      dados.value = r
    }
  } catch {
    if (g === geracao) erro.value = true
  } finally {
    if (g === geracao) carregando.value = false
  }
}
async function verEmail(item: CartaoEmail) {
  if (!aberto.value || !temEmails.value || !categorias.value.find((c) => c.value === categoria.value)?.itens.some((e) => e.id === item.id)) return
  const g = geracao
  const l = ++leitura
  const id = props.conversaId
  selecionado.value = item.id
  cartao.value = null
  lendo.value = true
  erroLeitura.value = false
  try {
    const r = await api<CartaoEmail>(urlLeituraDaVenda(id, item.id))
    if (g !== geracao || l !== leitura || id !== props.conversaId || !aberto.value) return
    // Não mostrar uma resposta de outro item, mesmo em uma API inconsistente.
    if (r.id !== item.id) throw new Error('email_divergente')
    cartao.value = r
  } catch {
    if (g === geracao && l === leitura) erroLeitura.value = true
  } finally {
    if (g === geracao && l === leitura) lendo.value = false
  }
}
watch(() => [props.conversaId, props.pedido, props.sinal, props.toolbar], () => { void carregar() }, { immediate: true, flush: 'sync' })
watch(categoria, limparLeitura, { flush: 'sync' })
onBeforeUnmount(() => { ++geracao; ++leitura })
defineExpose({ carregar })
</script>

<template>
  <DialogRoot v-if="toolbar" :open="aberto" @update:open="(v) => v ? abrir() : fechar()">
    <DialogTrigger v-if="temEmails" as-child>
      <button
      type="button"
      class="inline-flex h-7 items-center gap-1 rounded-md border bg-background px-2 text-xs font-medium hover:bg-accent"
      :title="`ver os ${venda!.total} e-mails do pedido ${venda!.pedido}`"
      aria-label="E-mails desta venda"
      :aria-expanded="aberto"
      data-emails-venda-botao
      ><Mail class="size-3.5" />E-mails <span class="tabular-nums">{{ venda!.total }}</span></button>
    </DialogTrigger>
    <DialogPortal v-if="temEmails">
      <DialogOverlay class="fixed inset-0 z-[75] bg-black/60" data-emails-venda-fundo />
      <div class="pointer-events-none fixed inset-0 z-[75] flex items-end justify-center sm:items-center sm:p-4">
        <DialogContent aria-modal="true" class="pointer-events-auto flex max-h-[100dvh] w-full max-w-3xl flex-col overflow-hidden rounded-t-xl border bg-background text-sm shadow-xl sm:max-h-[92dvh] sm:rounded-xl" data-emails-venda-dialog>
        <div class="flex shrink-0 items-start gap-2 border-b px-4 py-3">
          <div class="min-w-0 flex-1">
            <DialogTitle class="text-base font-semibold">E-mails desta venda</DialogTitle>
            <DialogDescription class="break-all text-xs text-muted-foreground">Pedido {{ venda!.pedido }} · {{ venda!.total }} e-mail{{ venda!.total === 1 ? '' : 's' }}</DialogDescription>
          </div>
          <button type="button" class="rounded p-1.5 hover:bg-accent" aria-label="Fechar e-mails" data-emails-venda-fechar @click="fechar"><X class="size-4" /></button>
        </div>
        <TabsRoot v-model="categoria" class="flex min-h-0 flex-1 flex-col">
          <TabsList aria-label="Categorias dos e-mails desta venda" class="flex shrink-0 gap-1 overflow-x-auto border-b px-4 py-2">
            <TabsTrigger v-for="grupo in categorias" :key="grupo.value" :value="grupo.value" class="inline-flex shrink-0 items-center gap-1.5 rounded-md px-3 py-2 text-xs font-medium text-muted-foreground hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring data-[state=active]:bg-primary/10 data-[state=active]:text-primary" :data-email-venda-categoria="grupo.value">
              {{ grupo.label }} <span class="rounded-full bg-muted px-1.5 tabular-nums">{{ grupo.itens.length }}</span>
            </TabsTrigger>
          </TabsList>
          <TabsContent v-for="grupo in categorias" :key="grupo.value" :value="grupo.value" class="min-h-0 flex-1 overflow-y-auto p-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring" :data-email-venda-painel="grupo.value">
            <p v-if="!grupo.itens.length" class="py-4 text-center text-xs text-muted-foreground" role="status">Nenhum e-mail nesta categoria para este pedido.</p>
            <ul v-else class="space-y-3">
              <li v-for="item in grupo.itens" :key="item.id" class="space-y-2 rounded-md border p-3" :data-email-venda-item="item.id">
                <div class="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
                  <span v-if="rotuloTipoEmail(item.tipo_email)" :title="dicaTipoEmail(item.tipo_email_inferido, item.tipo_pela_pasta, item.pasta)">{{ rotuloTipoEmail(item.tipo_email) }}</span>
                  <span v-if="item.caso_id">Chamado {{ item.caso_id }}</span>
                  <span v-if="item.recebido_em">{{ fmtDataHora(item.recebido_em) }}</span>
                </div>
                <p class="break-words font-medium">{{ item.assunto || 'E-mail recebido' }}</p>
                <p class="break-all text-xs text-muted-foreground">{{ destinoDaLoja(item.alias, item.loja) }}</p>
                <button type="button" class="text-xs font-medium text-primary underline" :aria-expanded="selecionado === item.id" data-email-venda-ver @click="verEmail(item)">Ver e-mail</button>
                <template v-if="selecionado === item.id">
                  <p v-if="lendo" class="flex items-center gap-1 text-xs text-muted-foreground"><Loader2 class="size-3 animate-spin" />Lendo e-mail…</p>
                  <p v-else-if="erroLeitura" class="text-xs text-muted-foreground" role="status">Não consegui ler este e-mail. Tente novamente.</p>
                  <AtendimentoEmailCartao v-else-if="cartao" :key="cartao.id" :email="emailDoCartao(cartao)" :cartao="cartao" :mostrar-texto="true" :texto="cartao.texto" :pode-responder="false" />
                </template>
              </li>
            </ul>
          </TabsContent>
        </TabsRoot>
        </DialogContent>
      </div>
    </DialogPortal>
  </DialogRoot>
  <section v-else class="space-y-1.5" data-painel-emails-venda>
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
