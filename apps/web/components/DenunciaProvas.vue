<script setup lang="ts">
// Denúncia (30/09/2026): as provas (prints, PDFs, vídeos) de um anúncio,
// denúncia ou caso. Os ARQUIVOS ficam no MEGA da empresa (conta sac@makisa),
// não no DaVinci — Vinicius: "as provas não estão ficando salvas no MEGA?
// continuamos assim". Cada linha diz a pasta e copia o caminho pra abrir no
// MEGA. Se um dia o arquivo também estiver no DaVinci (`tem_arquivo`), abre
// aqui mesmo.
import { computed, ref } from 'vue'
import { Copy, Check, CloudOff, ExternalLink, FileText, FileVideo, FileCode, Image as ImageIcon, File as FileIcon } from 'lucide-vue-next'
import { type Prova, dataBr, tamanho, urlProva } from '~/lib/denuncia'

const props = withDefaults(defineProps<{ provas: Prova[]; inicial?: number }>(), { inicial: 15 })

const todas = ref(false)
const copiado = ref<number | null>(null)
const visiveis = computed(() => (todas.value ? props.provas : props.provas.slice(0, props.inicial)))

function icone(p: Prova) {
  const m = p.mime || ''
  if (m.startsWith('image/')) return ImageIcon
  if (m.startsWith('video/')) return FileVideo
  if (m === 'application/pdf') return FileText
  if (m.includes('html')) return FileCode
  return FileIcon
}

/** "/Fiscalização/Denuncias/123 - Loja/arq.png" → "Denuncias/123 - Loja". */
function pasta(caminho: string): string {
  const partes = caminho.split('/').filter(Boolean)
  return partes.slice(1, -1).join('/') || caminho
}

async function copiar(p: Prova) {
  if (!p.mega_caminho) return
  try {
    await navigator.clipboard.writeText(p.mega_caminho)
    copiado.value = p.id
    setTimeout(() => {
      if (copiado.value === p.id) copiado.value = null
    }, 1500)
  } catch {
    useToasts().push({ kind: 'warning', title: 'Não deu para copiar', lines: p.mega_caminho })
  }
}
</script>

<template>
  <div>
    <div v-if="provas.length === 0" class="text-sm text-muted-foreground">Nenhuma prova.</div>
    <ul v-else class="divide-y rounded-lg border">
      <li v-for="p in visiveis" :key="p.id" class="flex items-center gap-2 px-3 py-2 min-w-0">
        <component :is="icone(p)" class="size-4 shrink-0 text-muted-foreground" />
        <div class="min-w-0 flex-1">
          <div class="flex items-center gap-2 min-w-0">
            <span class="text-xs font-medium shrink-0">{{ p.tipo || 'Prova' }}</span>
            <span class="text-xs text-muted-foreground truncate" :title="p.nome">{{ p.nome }}</span>
          </div>
          <div class="text-[11px] text-muted-foreground truncate" :title="p.mega_caminho || ''">
            {{ dataBr(p.enviado_em) }}<span v-if="p.tamanho"> · {{ tamanho(p.tamanho) }}</span>
            <template v-if="p.mega_caminho"> · MEGA: {{ pasta(p.mega_caminho) }}</template>
            <span v-else class="text-amber-700 dark:text-amber-400"> · ainda não está no MEGA</span>
          </div>
        </div>
        <a
          v-if="p.tem_arquivo"
          :href="urlProva(p.id)"
          target="_blank"
          rel="noopener"
          class="shrink-0 text-muted-foreground hover:text-primary"
          title="abrir"
        >
          <ExternalLink class="size-4" />
        </a>
        <Button
          v-if="p.mega_caminho"
          size="icon"
          variant="ghost"
          class="size-7 shrink-0"
          :title="`copiar o caminho no MEGA:\n${p.mega_caminho}`"
          @click="copiar(p)"
        >
          <Check v-if="copiado === p.id" class="size-3.5 text-emerald-600" />
          <Copy v-else class="size-3.5" />
        </Button>
        <CloudOff v-else class="size-4 shrink-0 text-amber-600" />
      </li>
    </ul>
    <div class="mt-2 flex flex-wrap items-center gap-3">
      <Button v-if="provas.length > inicial" size="sm" variant="ghost" @click="todas = !todas">
        {{ todas ? 'mostrar menos' : `ver todas (${provas.length})` }}
      </Button>
      <p v-if="provas.some((p) => p.mega_caminho)" class="text-[11px] text-muted-foreground">
        Os arquivos ficam no MEGA da empresa (conta sac@makisa), pasta Fiscalização — copie o caminho e abra lá.
      </p>
    </div>
  </div>
</template>
