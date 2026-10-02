<script setup lang="ts">
// Denúncia (30/09/2026): gaveta da ficha do anúncio — usada nas telas (clicar num anúncio abre
// aqui). 01/10: o conteúdo mora em DenunciaAnuncioFicha (também usado dentro da ficha da loja);
// aqui só a gaveta e o cabeçalho. "caso" = ir direto ao caso (aba Casos).
import { computed, ref } from 'vue'
import { type InfoAnuncio, nomeGrupo, pillGrupo } from '~/lib/denuncia'

const props = defineProps<{ anuncioId: string | null; focoDenuncia?: number | null }>()
const emit = defineEmits<{ (e: 'fechar'): void; (e: 'caso', id: number): void }>()

const info = ref<InfoAnuncio | null>(null)
const aberta = computed({
  get: () => !!props.anuncioId,
  set: (v: boolean) => {
    if (!v) {
      info.value = null
      emit('fechar')
    }
  },
})
</script>

<template>
  <DenunciaGaveta
    v-model:open="aberta"
    :titulo="info?.titulo || (anuncioId ? `Anúncio ${anuncioId}` : 'Anúncio')"
    :subtitulo="info?.subtitulo"
  >
    <template #cabecalho-extra>
      <span v-if="info?.grupo" :class="pillGrupo(info.grupo)" :title="info.grupo">certificado: {{ nomeGrupo(info.grupo).toLowerCase() }}</span>
    </template>
    <DenunciaAnuncioFicha
      :anuncio-id="anuncioId"
      :foco-denuncia="focoDenuncia"
      @info="(v) => (info = v)"
      @caso="(id) => emit('caso', id)"
    />
  </DenunciaGaveta>
</template>
