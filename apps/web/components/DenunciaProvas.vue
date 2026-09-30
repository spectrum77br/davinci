<script setup lang="ts">
// Denúncia (30/09/2026): as provas (prints, PDFs, vídeos) de um anúncio,
// denúncia ou caso. Imagem vira miniatura; o resto vira um cartão com ícone.
// Clicar abre o arquivo numa aba nova. Prova cuja linha já chegou do mini mas
// o arquivo ainda não (a primeira carga sobe aos poucos) aparece esmaecida.
import { computed, ref } from 'vue'
import { FileText, FileVideo, FileCode, File as FileIcon, Download } from 'lucide-vue-next'
import { type Prova, dataBr, tamanho, urlProva } from '~/lib/denuncia'

const props = withDefaults(defineProps<{ provas: Prova[]; inicial?: number }>(), { inicial: 12 })

const todas = ref(false)
const visiveis = computed(() => (todas.value ? props.provas : props.provas.slice(0, props.inicial)))

function ehImagem(p: Prova) {
  return (p.mime || '').startsWith('image/')
}
function icone(p: Prova) {
  const m = p.mime || ''
  if (m.startsWith('video/')) return FileVideo
  if (m === 'application/pdf') return FileText
  if (m.includes('html')) return FileCode
  return FileIcon
}
</script>

<template>
  <div>
    <div v-if="provas.length === 0" class="text-sm text-muted-foreground">Nenhuma prova.</div>
    <div v-else class="grid grid-cols-2 sm:grid-cols-3 gap-2">
      <component
        :is="p.tem_arquivo ? 'a' : 'div'"
        v-for="p in visiveis"
        :key="p.id"
        :href="p.tem_arquivo ? urlProva(p.id) : undefined"
        target="_blank"
        rel="noopener"
        class="group rounded-lg border bg-card overflow-hidden flex flex-col text-left"
        :class="p.tem_arquivo ? 'hover:border-primary/50' : 'opacity-60'"
        :title="p.tem_arquivo ? p.nome : 'O arquivo ainda não chegou do Mac mini'"
      >
        <div class="aspect-[4/3] bg-muted/40 flex items-center justify-center overflow-hidden">
          <img
            v-if="p.tem_arquivo && ehImagem(p)"
            :src="urlProva(p.id)"
            :alt="p.nome"
            loading="lazy"
            class="h-full w-full object-cover object-top"
          >
          <component :is="icone(p)" v-else class="size-8 text-muted-foreground" />
        </div>
        <div class="px-2 py-1.5 space-y-0.5 min-w-0">
          <div class="text-[11px] font-medium truncate">{{ p.tipo || 'Prova' }}</div>
          <div class="text-[10px] text-muted-foreground truncate">
            {{ dataBr(p.enviado_em) }}<span v-if="p.tamanho"> · {{ tamanho(p.tamanho) }}</span>
            <span v-if="!p.tem_arquivo"> · aguardando arquivo</span>
          </div>
        </div>
      </component>
    </div>
    <div v-if="provas.length > inicial" class="mt-2 flex items-center gap-3">
      <Button size="sm" variant="ghost" @click="todas = !todas">
        {{ todas ? 'mostrar menos' : `ver todas (${provas.length})` }}
      </Button>
    </div>
    <p v-if="provas.some((p) => p.tem_arquivo && !ehImagem(p))" class="mt-2 text-[11px] text-muted-foreground flex items-center gap-1">
      <Download class="size-3" /> PDF e vídeo abrem numa aba nova; página salva do marketplace (HTML) é baixada.
    </p>
  </div>
</template>
