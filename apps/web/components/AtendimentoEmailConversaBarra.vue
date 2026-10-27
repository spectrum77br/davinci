<script lang="ts">
// A BARRA da conversa de e-mail aberta na aba E-mail (13/10/2026 — o dono: "o
// e-mail aparece só na aba de e-mail; a do chat aparece só no chat"). A
// conversa de e-mail da ponte não está mais na Caixa: as Filas e as Caixas da
// aba E-mail abrem ela na própria aba, pelo id (a página). Em cima dela:
// "voltar aos e-mails", o "voltar ao chat" quando ela foi aberta de dentro de
// um chat (o "abrir na aba E-mail" do painel: `comChat`; o chat continua
// aberto na Caixa) e, para quem mexe, o VINCULAR AO PEDIDO da conversa
// sem pedido (fora os chamados dos sites, que não têm pedido de plataforma) —
// POST /api/atendimento/email/conversas/{id}/vincular, o mesmo vínculo da fila
// "Sem vínculo". Responder é a caixa da própria conversa, embaixo; escolher a
// loja é da fila "Sem loja" (o e-mail sem loja não tem conversa). Funções
// puras aqui em cima (tests/atendimento-mail-atendimento.cjs).
import type { ConversaResumo } from '~/components/AtendimentoPlataforma.vue'

type ConversaDaBarra = Pick<ConversaResumo, 'plataforma' | 'pedido_marketplace' | 'email'>

// Dá para vincular ao pedido daqui? Quem mexe, na conversa de e-mail (a da
// ponte: `email`), sem pedido e fora do site (o chamado não tem pedido).
export function podeVincular(c: ConversaDaBarra | null | undefined, podeMexer: boolean): boolean {
  return !!c && podeMexer && !!c.email && c.plataforma !== 'site' && !(c.pedido_marketplace || '').trim()
    && !c.email.caso_id && !['informativo', 'venda'].includes(c.email.tipo_email || '')
}

export function urlVincularConversa(id: string): string {
  return `/api/atendimento/email/conversas/${encodeURIComponent(id)}/vincular`
}
</script>

<script setup lang="ts">
import { ArrowLeft, Link2, Loader2, Mail, MessageSquare } from 'lucide-vue-next'
import { erroDaApi } from '~/components/AtendimentoPlataforma.vue'

const props = defineProps<{
  // A conversa aberta (a linha que a conversa manda no `mudou`); null enquanto carrega.
  conversa: ConversaResumo | null
  podeMexer: boolean
  // Aberta de dentro de um chat: o "voltar ao chat" (a página sabe qual).
  comChat?: boolean
  // Aberta na CAIXA pelo ampliável de e-mails da barra de lojas (27/10/2026):
  // o aviso diz onde ela está (fora da lista e dos números do chat).
  naCaixa?: boolean
}>()
const emit = defineEmits<{
  (e: 'voltar'): void
  (e: 'voltarAoChat'): void
  // O vínculo pode MOVER as mensagens para a conversa de e-mail do pedido que já existia.
  (e: 'vinculado', id: string): void
}>()

const { api } = useApi()
const toasts = useToasts()
const pedido = ref('')
const salvando = ref(false)
const mostraVincular = computed(() => podeVincular(props.conversa, props.podeMexer))

async function vincular() {
  const c = props.conversa
  const numero = pedido.value.trim()
  if (!c || !numero || salvando.value) return
  salvando.value = true
  try {
    const r = await api<{ conversa_id: string; pedido: string }>(urlVincularConversa(c.id), { method: 'POST', body: { pedido: numero } })
    toasts.success(`E-mail vinculado ao pedido ${r.pedido}`)
    pedido.value = ''
    emit('vinculado', r.conversa_id)
  } catch (e: any) {
    const er = erroDaApi(e, 'Não consegui vincular ao pedido')
    toasts.error(er.texto, er.motivos)
  } finally {
    salvando.value = false
  }
}
</script>

<template>
  <div class="flex flex-wrap items-center gap-2 text-xs" data-barra-conversa-email>
    <button type="button" class="inline-flex items-center gap-1 rounded border px-2 py-1 hover:bg-muted" data-voltar-emails @click="emit('voltar')">
      <ArrowLeft class="size-3.5" /> voltar aos e-mails
    </button>
    <button v-if="comChat" type="button" class="inline-flex items-center gap-1 rounded border px-2 py-1 hover:bg-muted" title="volta ao chat de onde esta conversa de e-mail foi aberta" data-voltar-chat @click="emit('voltarAoChat')">
      <MessageSquare class="size-3.5" /> voltar ao chat
    </button>
    <span class="inline-flex min-w-0 items-center gap-1 text-muted-foreground">
      <Mail class="size-3.5 shrink-0" /><template v-if="naCaixa">E-mail da loja, aberto pela barra de lojas: fica fora da lista e dos números do chat.</template><template v-else>Conversa de e-mail: fica só aqui, na aba E-mail (não aparece na Caixa do chat).</template>
    </span>
    <form v-if="mostraVincular" class="ml-auto flex flex-wrap items-center gap-1.5" data-vincular-conversa-email @submit.prevent="vincular">
      <input v-model="pedido" class="h-7 w-48 rounded border bg-background px-1.5" placeholder="nº do pedido na plataforma" aria-label="número do pedido" :disabled="salvando" />
      <button type="submit" class="inline-flex items-center gap-1 rounded border px-2 py-1 hover:bg-muted disabled:opacity-50" :disabled="!pedido.trim() || salvando">
        <Loader2 v-if="salvando" class="size-3.5 animate-spin" /><Link2 v-else class="size-3.5" /> vincular ao pedido
      </button>
    </form>
  </div>
</template>
