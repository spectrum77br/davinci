<script setup lang="ts">
// Cartão de pedido dentro da conversa (Atendimento, 28/09/2026), o mesmo
// desenho do "Confirmação de pedido" do Duoke: "ID do Pedido#…" em negrito,
// selo de status colorido, data, foto + nome do produto e "Montante Total" em
// azul. Vem do anexo `{"tipo": "pedido", …}` que o sync preenche pela API da
// loja (só leitura). Anexo antigo, só com o número, usa o retrato do pedido
// da conversa quando é o mesmo pedido — e, sem nada, mostra só o número.
import { Copy, Package } from 'lucide-vue-next'
import {
  copiar,
  fmtDataPlataforma,
  fmtDinheiro,
  statusPedidoCls,
  type CartaoPedido,
  type PedidoMkt,
} from '~/components/AtendimentoPlataforma.vue'

const props = defineProps<{
  cartao: CartaoPedido
  plataforma?: string | null
  // Retrato do pedido da conversa (painel da direita): completa o cartão
  // quando o anexo chegou sem os detalhes.
  retrato?: PedidoMkt | null
}>()
const toasts = useToasts()

const mesmo = computed(() => !!props.retrato?.pedido && props.retrato.pedido === props.cartao.pedido)
const status = computed(() => props.cartao.status || (mesmo.value ? props.retrato?.status : null) || null)
const statusTexto = computed(() => props.cartao.status_texto || (mesmo.value ? props.retrato?.status_texto : null) || status.value)
const criado = computed(() => props.cartao.criado_em || (mesmo.value ? props.retrato?.criado_em : null) || null)
const total = computed(() => props.cartao.total ?? (mesmo.value ? (props.retrato?.total ?? null) : null))
const moeda = computed(() => props.cartao.moeda || (mesmo.value ? props.retrato?.moeda : null) || 'BRL')
const itens = computed(() => (props.cartao.itens.length ? props.cartao.itens : mesmo.value ? props.retrato?.itens || [] : []))
// O Duoke mostra só o primeiro item no cartão; os outros viram "+N".
const primeiro = computed(() => itens.value[0] || null)
const outros = computed(() => Math.max(0, itens.value.length - 1))
const fotoFalhou = ref(false)
watch(() => primeiro.value?.imagem, () => { fotoFalhou.value = false })

async function copiarNumero() {
  const n = props.cartao.pedido
  if (!n) return
  if (await copiar(n)) toasts.success('Nº do pedido copiado')
  else window.prompt('Nº do pedido', n)
}
</script>

<template>
  <div class="w-[300px] max-w-full rounded-md bg-background p-3 text-[13px] leading-5 text-foreground shadow-sm">
    <div class="flex items-start gap-1">
      <div class="min-w-0 flex-1 break-all">
        ID do Pedido<span class="font-semibold">#{{ cartao.pedido }}</span>
      </div>
      <button type="button" class="shrink-0 rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground" title="copiar nº do pedido" aria-label="copiar nº do pedido" @click="copiarNumero">
        <Copy class="size-3.5" />
      </button>
    </div>
    <span
      v-if="statusTexto"
      class="mt-1 inline-block rounded border px-1.5 py-px text-xs"
      :class="statusPedidoCls(status, statusTexto)"
    >{{ statusTexto }}</span>
    <div v-if="criado" class="mt-2">{{ fmtDataPlataforma(criado) }}</div>

    <div v-if="primeiro" class="mt-2 flex items-start gap-2">
      <img
        v-if="primeiro.imagem && !fotoFalhou"
        :src="primeiro.imagem"
        alt=""
        loading="lazy"
        referrerpolicy="no-referrer"
        class="size-12 shrink-0 rounded border object-cover"
        @error="fotoFalhou = true"
      />
      <span v-else class="flex size-12 shrink-0 items-center justify-center rounded border bg-muted text-muted-foreground"><Package class="size-5" /></span>
      <div class="min-w-0 flex-1">
        <div class="line-clamp-2 break-words" :title="primeiro.titulo || ''">
          <AtendimentoIconePlataforma v-if="plataforma" :plataforma="plataforma" :tamanho="13" decorativo class="-mt-0.5 mr-0.5" />
          {{ primeiro.titulo || 'Produto' }}
        </div>
        <div v-if="primeiro.variacao" class="truncate text-xs text-muted-foreground" :title="primeiro.variacao">{{ primeiro.variacao }}</div>
        <div v-if="outros" class="text-xs text-muted-foreground">+ {{ outros }} {{ outros === 1 ? 'outro item' : 'outros itens' }}</div>
      </div>
    </div>

    <div v-if="total !== null" class="mt-2 flex items-center justify-between gap-2 border-t border-dashed pt-2">
      <span>Montante Total</span>
      <span class="font-semibold text-primary tabular-nums">{{ fmtDinheiro(total, moeda) }}</span>
    </div>
    <div v-else-if="!primeiro" class="mt-1 text-xs text-muted-foreground">Detalhes do pedido no painel ao lado.</div>
  </div>
</template>
