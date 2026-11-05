<script lang="ts">
// Os botões do brinde do pedido não pago (o dono, 05/11/2026): "Adicionar AirTag
// ao pedido" (ou chaveiro) e "Não adicionar", com a confirmação e o resultado DE
// VERDADE. Usados no painel do pedido (AtendimentoPedido) e na lista "Brindes a
// adicionar" (AtendimentoBrindesLista). Quem confere tudo é o servidor, ao vivo no
// Bling (`services/atendimento/brinde.py`): `ok=false` = o Bling não respondeu ou o
// total/as parcelas não bateram (a NF segue segura), e o clique seguinte começa pelo
// GET. Este bloco é o módulo dos ajudantes puros,
// testados em tests/atendimento-brinde.cjs.
export const URL_BRINDES = '/api/atendimento/brindes'

export type ResultadoBrinde = {
  ok: boolean
  estado: string | null
  sku: string | null
  numero_bling: string | null
  mensagem: string
  ja_estava: boolean
}

export function textoDoBotao(rotulo: string | null | undefined): string {
  return rotulo ? `Adicionar ${rotulo} ao pedido` : 'Adicionar brinde ao pedido'
}

export function urlDaAcao(integrationId: string, pedido: string, acao: 'adicionar' | 'dispensar'): string {
  return `${URL_BRINDES}/${encodeURIComponent(integrationId)}/${encodeURIComponent(pedido)}/${acao}`
}

// Verde só com o brinde confirmado; âmbar no `incerto`; vermelho na recusa.
export function nivelDoResultado(r: Pick<ResultadoBrinde, 'ok'> | null, erro: string | null): 'ok' | 'incerto' | 'erro' | null {
  if (erro) return 'erro'
  if (!r) return null
  return r.ok ? 'ok' : 'incerto'
}
</script>

<script setup lang="ts">
import { Check, Gift, Loader2, X } from 'lucide-vue-next'
import { erroDaApi } from '~/components/AtendimentoPlataforma.vue'

const props = defineProps<{
  integrationId: string
  pedido: string
  rotulo?: string | null
  sku?: string | null
  podeAdicionar: boolean
  // O porquê de não poder (a frase do servidor).
  motivo?: string | null
}>()
const emit = defineEmits<{ (e: 'mudou'): void }>()
const { api } = useApi()

const confirmando = ref<'adicionar' | 'dispensar' | null>(null)
const enviando = ref(false)
const resultado = ref<ResultadoBrinde | null>(null)
const erro = ref<string | null>(null)
const nivel = computed(() => nivelDoResultado(resultado.value, erro.value))
// Depois do brinde confirmado (ou dispensado) os botões somem; o resultado fica.
const fechado = computed(() => resultado.value?.ok === true)

async function executar() {
  const acao = confirmando.value
  if (!acao || enviando.value) return
  enviando.value = true
  erro.value = null
  resultado.value = null
  try {
    resultado.value = await api<ResultadoBrinde>(urlDaAcao(props.integrationId, props.pedido, acao), { method: 'POST' })
    confirmando.value = null
    emit('mudou')
  } catch (e) {
    // A frase do servidor (a recusa do Bling, a trava), como veio.
    const d = (e as { data?: { detail?: { detail?: unknown } } })?.data?.detail
    erro.value = typeof d?.detail === 'string' && d.detail
      ? d.detail
      : erroDaApi(e, 'Não consegui falar com o servidor: não sei se o brinde entrou.').texto
    confirmando.value = null
  } finally {
    enviando.value = false
  }
}
</script>

<template>
  <div class="space-y-1.5" data-brinde-acoes>
    <p v-if="!podeAdicionar && motivo && !fechado" class="text-[11px] text-muted-foreground" data-brinde-motivo>{{ motivo }}</p>
    <div v-if="!fechado && !confirmando" class="flex flex-wrap items-center gap-1.5">
      <button
        v-if="podeAdicionar"
        type="button"
        class="inline-flex items-center gap-1 rounded-md bg-primary px-2 py-1 text-xs font-medium text-primary-foreground hover:bg-primary/90"
        data-brinde-adicionar
        @click="confirmando = 'adicionar'"
      >
        <Gift class="size-3.5" />{{ textoDoBotao(rotulo) }}
      </button>
      <button
        v-if="podeAdicionar"
        type="button"
        class="inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs text-muted-foreground hover:bg-muted"
        data-brinde-dispensar
        @click="confirmando = 'dispensar'"
      >
        <X class="size-3.5" />Não adicionar
      </button>
    </div>
    <div v-if="confirmando" class="rounded-md border bg-muted/40 p-2 text-xs" data-brinde-confirmar>
      <p v-if="confirmando === 'adicionar'">
        Põe {{ rotulo || 'o brinde' }}<template v-if="sku"> ({{ sku }}, ou o lote com saldo)</template> no pedido do Bling por R$ 0,01. O total do pedido não muda e só a linha do brinde fica aprovada na Margem, no seu nome: o resto do pedido continua sendo julgado.
      </p>
      <p v-else>O pedido segue sem o brinde e a NF é liberada.</p>
      <div class="mt-1.5 flex items-center gap-1.5">
        <button
          type="button"
          class="inline-flex items-center gap-1 rounded-md bg-primary px-2 py-1 font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
          :disabled="enviando"
          data-brinde-confirmar-sim
          @click="executar"
        >
          <Loader2 v-if="enviando" class="size-3.5 animate-spin" /><Check v-else class="size-3.5" />Confirmar
        </button>
        <button type="button" class="rounded-md border px-2 py-1 text-muted-foreground hover:bg-muted disabled:opacity-50" :disabled="enviando" @click="confirmando = null">Voltar</button>
      </div>
    </div>
    <p
      v-if="nivel"
      class="text-xs"
      :class="{
        'text-emerald-700 dark:text-emerald-400': nivel === 'ok',
        'text-amber-700 dark:text-amber-400': nivel === 'incerto',
        'text-destructive': nivel === 'erro',
      }"
      data-brinde-resultado
    >{{ erro || resultado?.mensagem }}</p>
  </div>
</template>
