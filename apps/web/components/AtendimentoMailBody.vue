<script setup lang="ts">
import { loadInlineImages, MAIL_FRAME_SANDBOX, renderMailHtml, type MailAttachment } from '~/lib/mailHtml'

const props = defineProps<{ html: string; text: string; attachments: MailAttachment[] }>()
const { api } = useApi()
const srcdoc = ref('')
const hasRemoteImages = ref(false)
const showRemoteImages = ref(false)
const showText = ref(false)
const loading = ref(true)
const failed = ref(false)
const expanded = ref(false)
let generation = 0
let inlineImages: Record<string, string> = {}
let mounted = false

function build() {
  const rendered = renderMailHtml(props.html, { images: inlineImages, showRemoteImages: showRemoteImages.value })
  srcdoc.value = rendered.srcdoc
  hasRemoteImages.value = rendered.hasRemoteImages
  return rendered
}

async function prepare() {
  const current = ++generation
  showRemoteImages.value = false
  showText.value = false
  failed.value = false
  loading.value = true
  inlineImages = {}
  try {
    const rendered = build()
    const images = await loadInlineImages(props.attachments, rendered.contentIds, (id) =>
      api<Blob>(`/api/mail/attachments/${encodeURIComponent(id)}`, { responseType: 'blob', timeout: 15_000, retry: 0 }),
    )
    if (current !== generation) return
    inlineImages = images
    build()
  } catch {
    if (current === generation) failed.value = true
  } finally {
    if (current === generation) loading.value = false
  }
}

function allowRemoteImages() {
  showRemoteImages.value = true
  build()
}

watch(() => props.html, () => { if (mounted) void prepare() })
onMounted(() => { mounted = true; void prepare() })
onBeforeUnmount(() => { mounted = false; ++generation })
</script>

<template>
  <section class="min-w-0 space-y-2" aria-label="Conteúdo do e-mail" data-mail-body>
    <div class="flex flex-wrap items-center justify-between gap-2 text-xs">
      <div v-if="hasRemoteImages && !showRemoteImages && !showText" class="flex flex-wrap items-center gap-2 text-muted-foreground">
        <span>Imagens externas ocultas.</span>
        <button type="button" class="rounded border px-2 py-1 text-foreground hover:bg-muted" @click="allowRemoteImages">Mostrar imagens externas</button>
      </div>
      <span v-else-if="loading" class="text-muted-foreground">Carregando mensagem…</span>
      <span v-else />
      <div class="flex items-center gap-2">
        <button v-if="!failed" type="button" class="rounded border px-2 py-1 hover:bg-muted" @click="showText = !showText">{{ showText ? 'Ver mensagem formatada' : 'Ver texto' }}</button>
        <button v-if="!showText && !failed" type="button" class="rounded border px-2 py-1 hover:bg-muted" :aria-expanded="expanded" @click="expanded = !expanded">{{ expanded ? 'Reduzir' : 'Ampliar mensagem' }}</button>
      </div>
    </div>
    <p v-if="failed" class="text-xs text-muted-foreground">A formatação não pôde ser aberta. O texto da mensagem está abaixo.</p>
    <pre v-if="showText || failed" class="max-h-[70dvh] overflow-auto whitespace-pre-wrap break-words font-sans text-sm leading-relaxed">{{ text || '(Mensagem sem texto)' }}</pre>
    <iframe
      v-else-if="srcdoc"
      :srcdoc="srcdoc"
      :sandbox="MAIL_FRAME_SANDBOX"
      title="Mensagem de e-mail"
      referrerpolicy="no-referrer"
      class="mail-content-frame w-full rounded border bg-white"
      :class="{ 'mail-content-frame-expanded': expanded }"
    />
  </section>
</template>

<style scoped>
.mail-content-frame { display: block; height: min(68dvh, 720px); min-height: 360px; }
.mail-content-frame-expanded { height: 85dvh; }
@media (max-width: 640px) {
  .mail-content-frame { height: 65dvh; min-height: 320px; }
  .mail-content-frame-expanded { height: 85dvh; }
}
</style>
