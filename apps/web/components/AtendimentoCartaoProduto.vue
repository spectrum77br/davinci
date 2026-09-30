<script setup lang="ts">
// Cartão de produto (Atendimento, 28/09/2026): foto, título e preço do
// anúncio, como o Duoke mostra quando o comprador manda um produto no chat
// (ou no painel, o anúncio da pergunta do ML). Vem da API da loja pelo
// backend (cache de 24 h); anexo antigo, só com o número do anúncio, vira um
// cartão só com o número.
import { ExternalLink, Package } from 'lucide-vue-next'
import { fmtDinheiro, type CartaoProduto } from '~/components/AtendimentoPlataforma.vue'

const props = withDefaults(defineProps<{
  cartao: CartaoProduto
  // `grande` = o do painel (foto maior, largura toda).
  grande?: boolean
}>(), { grande: false })

const fotoFalhou = ref(false)
watch(() => props.cartao.imagem, () => { fotoFalhou.value = false })
const temDesconto = computed(() => props.cartao.preco !== null && props.cartao.preco_original !== null && props.cartao.preco_original > props.cartao.preco)
</script>

<template>
  <component
    :is="cartao.link ? 'a' : 'div'"
    :href="cartao.link || undefined"
    :target="cartao.link ? '_blank' : undefined"
    :rel="cartao.link ? 'noopener noreferrer' : undefined"
    class="flex gap-2.5 rounded-md bg-background p-2 text-[13px] leading-5 text-foreground shadow-sm"
    :class="[grande ? 'w-full border' : 'w-[280px] max-w-full', cartao.link ? 'transition-colors hover:bg-muted/60' : '']"
    :title="cartao.link ? 'abrir o anúncio (outra aba)' : undefined"
  >
    <img
      v-if="cartao.imagem && !fotoFalhou"
      :src="cartao.imagem"
      alt=""
      loading="lazy"
      referrerpolicy="no-referrer"
      class="shrink-0 rounded border object-cover"
      :class="grande ? 'size-20' : 'size-16'"
      @error="fotoFalhou = true"
    />
    <span v-else class="flex shrink-0 items-center justify-center rounded border bg-muted text-muted-foreground" :class="grande ? 'size-20' : 'size-16'">
      <Package class="size-6" />
    </span>
    <span class="flex min-w-0 flex-1 flex-col">
      <span class="line-clamp-2 break-words" :title="cartao.titulo || ''">{{ cartao.titulo || `Anúncio ${cartao.item_id || ''}`.trim() }}</span>
      <span v-if="cartao.preco !== null" class="mt-auto flex flex-wrap items-baseline gap-x-1.5 pt-1">
        <span class="font-semibold text-primary tabular-nums">{{ fmtDinheiro(cartao.preco, cartao.moeda) }}</span>
        <s v-if="temDesconto" class="text-xs text-muted-foreground tabular-nums">{{ fmtDinheiro(cartao.preco_original, cartao.moeda) }}</s>
      </span>
      <span v-else-if="cartao.item_id" class="mt-auto pt-1 font-mono text-[11px] text-muted-foreground">#{{ cartao.item_id }}</span>
    </span>
    <ExternalLink v-if="cartao.link" class="size-3.5 shrink-0 text-muted-foreground" />
  </component>
</template>
