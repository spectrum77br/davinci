<script setup lang="ts">
// App Uranyx: escolher e subir uma foto (produto, receita, ícone de app).
// Sobe na hora para /api/app-uranyx/arquivos e devolve o arquivo para o
// formulário, que manda o `id` (foto_arquivo_id / icone_arquivo_id) ao salvar.
// Com `comprimir`, a foto vira JPEG de no máximo 1600 px (qualidade 0,8)
// aqui no navegador antes de subir.
import { ImageOff, Loader2, Upload } from 'lucide-vue-next'
import { Button } from '~/components/ui/button'
import { useAppUranyx } from '~/composables/useAppUranyx'
import {
  comprimirFoto, erroAppUranyx, formatarTamanho, FOTO_MAX_BYTES, FOTO_TIPOS, problemaFoto,
  type ArquivoEnviado,
} from '~/lib/appUranyx'

const props = withDefaults(defineProps<{
  url?: string | null
  comprimir?: boolean
  rotulo?: string
  quadrada?: boolean
  desabilitado?: boolean
}>(), { url: null, comprimir: false, rotulo: 'Foto', quadrada: false, desabilitado: false })
const emit = defineEmits<{ (e: 'enviado', arquivo: ArquivoEnviado): void }>()

const { subirArquivo } = useAppUranyx()
const toasts = useToasts()
const entrada = ref<HTMLInputElement | null>(null)
const enviando = ref(false)
const erro = ref<string | null>(null)
const enviado = ref<ArquivoEnviado | null>(null)

// Quando o formulário troca de item, a prévia volta a ser a do item.
watch(() => props.url, () => { enviado.value = null; erro.value = null })

const previa = computed(() => enviado.value?.url || props.url || '')

function escolher() {
  if (enviando.value || props.desabilitado) return
  entrada.value?.click()
}

async function aoEscolher(ev: Event) {
  const alvo = ev.target as HTMLInputElement
  const arquivo = alvo.files?.[0]
  alvo.value = '' // permite escolher o mesmo arquivo de novo
  if (!arquivo) return
  const problema = problemaFoto(arquivo, props.comprimir)
  if (problema) { erro.value = problema; return }
  enviando.value = true
  erro.value = null
  let naApi = false
  try {
    const final = props.comprimir ? await comprimirFoto(arquivo) : arquivo
    if (final.size > FOTO_MAX_BYTES) throw new Error('Mesmo reduzida, a foto passa de 5 MB. Escolha outra.')
    naApi = true
    const r = await subirArquivo(final)
    naApi = false
    enviado.value = r
    emit('enviado', r)
  } catch (e: any) {
    // Erro de reduzir a foto (navegador) já vem em português; o do envio passa
    // pela frase comum, inclusive o de rede, que chega sem status nem corpo.
    const texto = naApi ? erroAppUranyx(e, 'Não deu para subir a foto').texto : (e?.message || 'Não deu para subir a foto')
    erro.value = texto
    toasts.error('A foto não subiu', texto)
  } finally {
    enviando.value = false
  }
}
</script>

<template>
  <div class="flex items-start gap-3">
    <div
      class="grid shrink-0 place-items-center overflow-hidden rounded-lg border bg-muted/30"
      :class="quadrada ? 'size-16' : 'h-24 w-32'"
    >
      <img v-if="previa" :src="previa" :alt="rotulo" class="size-full object-contain" loading="lazy" />
      <ImageOff v-else class="size-5 text-muted-foreground" aria-hidden="true" />
    </div>
    <div class="min-w-0 space-y-1.5">
      <input
        ref="entrada"
        type="file"
        :accept="FOTO_TIPOS.join(',')"
        class="hidden"
        aria-hidden="true"
        tabindex="-1"
        @change="aoEscolher"
      />
      <Button type="button" size="sm" variant="outline" :disabled="enviando || desabilitado" @click="escolher">
        <Loader2 v-if="enviando" class="mr-1 size-4 animate-spin" />
        <Upload v-else class="mr-1 size-4" />
        {{ enviando ? 'Subindo…' : previa ? `Trocar ${rotulo.toLowerCase()}` : `Escolher ${rotulo.toLowerCase()}` }}
      </Button>
      <p class="text-xs text-muted-foreground">
        <template v-if="comprimir">JPG, PNG ou WebP. Reduzimos para 1600 px antes de subir.</template>
        <template v-else>JPG, PNG ou WebP até 5 MB.</template>
      </p>
      <p v-if="enviado" class="text-xs text-emerald-700 dark:text-emerald-400">
        Nova {{ rotulo.toLowerCase() }} pronta ({{ formatarTamanho(enviado.tamanho) }}). Vale quando salvar.
      </p>
      <p v-if="erro" class="text-xs text-red-600 dark:text-red-400">{{ erro }}</p>
    </div>
  </div>
</template>
