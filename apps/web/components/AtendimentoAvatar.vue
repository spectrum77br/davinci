<script setup lang="ts">
// Avatar do comprador no Atendimento (28/09/2026), no desenho do Duoke:
// círculo com a foto (só a que a API de chat da plataforma entrega — Shopee
// `to_avatar`, TikTok) ou as iniciais numa cor fixa por nome; o mini-ícone da
// plataforma no canto de baixo e a bolinha VERMELHA de não lidas em cima. Foto
// que não abre (link vencido da plataforma) cai nas iniciais sem quebrar a
// linha da lista.
import { computed, ref, watch } from 'vue'
import { corDoNome, iniciais, urlSegura } from '~/components/AtendimentoPlataforma.vue'

const props = withDefaults(defineProps<{
  nome: string | null | undefined
  foto?: string | null
  plataforma?: string | null
  naoLidas?: number | null
  tamanho?: number
}>(), { foto: null, plataforma: null, naoLidas: 0, tamanho: 40 })

const falhou = ref(false)
watch(() => props.foto, () => { falhou.value = false })
const url = computed(() => (falhou.value ? null : urlSegura(props.foto)))
const letras = computed(() => iniciais(props.nome))
const cor = computed(() => corDoNome(props.nome))
const icone = computed(() => Math.max(12, Math.round(props.tamanho * 0.4)))
const contador = computed(() => {
  const n = props.naoLidas || 0
  return n > 99 ? '99+' : n > 0 ? String(n) : ''
})
</script>

<template>
  <span class="relative inline-flex shrink-0" :style="{ width: `${tamanho}px`, height: `${tamanho}px` }">
    <img
      v-if="url"
      :src="url"
      alt=""
      loading="lazy"
      referrerpolicy="no-referrer"
      class="size-full rounded-full border object-cover"
      @error="falhou = true"
    />
    <span
      v-else
      class="flex size-full select-none items-center justify-center rounded-full font-semibold text-white"
      :class="cor"
      :style="{ fontSize: `${Math.round(tamanho * 0.36)}px` }"
      aria-hidden="true"
    >{{ letras }}</span>
    <span
      v-if="plataforma"
      class="absolute -bottom-0.5 -right-0.5 flex items-center justify-center rounded-full bg-background shadow-sm ring-1 ring-border"
      :style="{ width: `${icone + 4}px`, height: `${icone + 4}px` }"
    >
      <AtendimentoIconePlataforma :plataforma="plataforma" :tamanho="icone" decorativo />
    </span>
    <span
      v-if="contador"
      class="absolute -right-1.5 -top-1 flex h-[18px] min-w-[18px] items-center justify-center rounded-full bg-red-500 px-1 text-[10px] font-semibold leading-none text-white ring-2 ring-background tabular-nums"
      :title="`${naoLidas} mensagem(ns) do comprador sem resposta`"
    >{{ contador }}</span>
  </span>
</template>
