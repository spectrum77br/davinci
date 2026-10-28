<script setup lang="ts">
// Denúncia (30/09/2026): as provas (prints, PDFs, vídeos) de um anúncio,
// denúncia ou caso. Os ARQUIVOS ficam no Mac mini e no MEGA da empresa (conta
// sac@makisa), não no DaVinci (disco curto).
// 01/10 (Vinicius: "quando clica nas provas não abre nada, eu precisava ver as
// imagens, vídeos"): clicar abre a prova aqui mesmo. Se o arquivo ainda não
// está no DaVinci, a tela pede ao mini (POST …/preparar), ele manda em uns
// segundos e a janela mostra. O caminho do MEGA continua no botão de copiar.
import { computed, onUnmounted, ref } from 'vue'
import { DialogContent, DialogDescription, DialogOverlay, DialogPortal, DialogRoot, DialogTitle } from 'reka-ui'
import {
  Copy, Check, CloudOff, ExternalLink, Download, FileText, FileVideo, FileCode, Image as ImageIcon,
  File as FileIcon, ChevronLeft, ChevronRight, Loader2, X,
} from 'lucide-vue-next'
import { type Prova, dataBr, tamanho, urlProva } from '~/lib/denuncia'

// 28/10 (Vinicius: "essa lista de prova enorme… muito print, muita besteira"): em cima só o que vale
// (último print, o que foi à Anatel, provas do caso); os registros das denúncias e o histórico (prints
// antigos e inválidos) ficam fechados. `agrupar=false` mostra tudo junto (lista de uma denúncia só).
const props = withDefaults(defineProps<{ provas: Prova[]; inicial?: number; agrupar?: boolean }>(), {
  inicial: 15,
  agrupar: true,
})

const { api } = useApi()
const todas = ref(false)
const copiado = ref<number | null>(null)
const abertos = ref<Record<string, boolean>>({})

const atuais = computed(() =>
  props.agrupar ? props.provas.filter((p) => (p.grupo ?? 'atual') === 'atual') : props.provas,
)
const visiveis = computed(() => (todas.value ? atuais.value : atuais.value.slice(0, props.inicial)))
const fechados = computed(() =>
  props.agrupar
    ? [
        { k: 'denuncias', titulo: 'Registros das denúncias na loja', itens: props.provas.filter((p) => p.grupo === 'denuncias') },
        { k: 'historico', titulo: 'Histórico — prints antigos e inválidos', itens: props.provas.filter((p) => p.grupo === 'historico') },
      ].filter((g) => g.itens.length)
    : [],
)
// ordem do visualizador (← →): o que vale primeiro, depois os grupos fechados
const ordem = computed(() => [...atuais.value, ...fechados.value.flatMap((g) => g.itens)])
const blocos = computed(() => [{ k: 'atual', titulo: '', itens: visiveis.value }, ...fechados.value])

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

// ── visualizador ───────────────────────────────────────────────────────────
// O mini olha os pedidos a cada 5 s e sobe o arquivo; 90 s sem chegar = mini
// desligado ou sem internet.
const ESPERA_MAX_S = 90
const aberta = ref<Prova | null>(null)
const estado = ref<'pedindo' | 'pronto' | 'erro'>('pedindo')
const esperando = ref(0)
const versao = ref(0)
let timer: ReturnType<typeof setTimeout> | null = null

const tipo = computed(() => {
  const m = aberta.value?.mime || ''
  if (m.startsWith('image/') && m !== 'image/svg+xml') return 'imagem'
  if (m.startsWith('video/')) return 'video'
  if (m === 'application/pdf') return 'pdf'
  return 'baixar'
})
const indice = computed(() => (aberta.value ? ordem.value.findIndex((p) => p.id === aberta.value!.id) : -1))

function parar() {
  if (timer) clearTimeout(timer)
  timer = null
}

async function preparar(p: Prova, inicio: number) {
  if (aberta.value?.id !== p.id) return
  try {
    const r = await api<{ pronto: boolean; esperando_s?: number }>(`/api/denuncia/provas/${p.id}/preparar`, { method: 'POST' })
    if (aberta.value?.id !== p.id) return
    if (r.pronto) {
      estado.value = 'pronto'
      versao.value++
      return
    }
  } catch {
    // rede instável: tenta de novo no próximo giro
  }
  esperando.value = Math.round((Date.now() - inicio) / 1000)
  if (esperando.value >= ESPERA_MAX_S) {
    estado.value = 'erro'
    return
  }
  timer = setTimeout(() => preparar(p, inicio), 2000)
}

function abrir(p: Prova) {
  parar()
  aberta.value = p
  estado.value = 'pedindo'
  esperando.value = 0
  void preparar(p, Date.now())
}

function andar(passo: number) {
  const i = indice.value + passo
  if (i >= 0 && i < ordem.value.length) abrir(ordem.value[i])
}

function fechar() {
  parar()
  aberta.value = null
}

const visualizadorAberto = computed({
  get: () => aberta.value !== null,
  set: (v: boolean) => {
    if (!v) fechar()
  },
})

onUnmounted(parar)
</script>

<template>
  <div>
    <div v-if="provas.length === 0" class="text-sm text-muted-foreground">Nenhuma prova.</div>
    <div v-else-if="!atuais.length" class="text-sm text-muted-foreground">Nenhuma prova atual — as outras estão abaixo.</div>
    <template v-for="b in blocos" :key="b.k">
      <button
        v-if="b.titulo"
        type="button"
        class="mt-2 flex items-center gap-1 text-xs text-muted-foreground hover:text-primary"
        @click="abertos[b.k] = !abertos[b.k]"
      >
        <ChevronRight class="size-3.5 transition-transform" :class="{ 'rotate-90': abertos[b.k] }" />
        {{ b.titulo }} ({{ b.itens.length }})
      </button>
      <ul v-if="b.itens.length && (!b.titulo || abertos[b.k])" class="divide-y rounded-lg border" :class="{ 'mt-1': b.titulo }">
        <li v-for="p in b.itens" :key="p.id" class="flex items-center gap-2 px-3 py-2 min-w-0">
          <button
            type="button"
            class="flex min-w-0 flex-1 items-center gap-2 text-left hover:text-primary"
            :title="`abrir ${p.nome}`"
            @click="abrir(p)"
          >
            <component :is="icone(p)" class="size-4 shrink-0 text-muted-foreground" />
            <div class="min-w-0 flex-1">
              <div class="flex items-center gap-2 min-w-0">
                <span class="text-xs font-medium shrink-0">{{ p.tipo || 'Prova' }}</span>
                <span class="text-xs text-muted-foreground truncate underline-offset-2 hover:underline">{{ p.nome }}</span>
              </div>
              <div class="text-[11px] text-muted-foreground truncate" :title="p.mega_caminho || ''">
                {{ dataBr(p.enviado_em) }}<span v-if="p.tamanho"> · {{ tamanho(p.tamanho) }}</span>
                <template v-if="p.mega_caminho"> · MEGA: {{ pasta(p.mega_caminho) }}</template>
                <span v-else class="text-amber-700 dark:text-amber-400"> · ainda não está no MEGA</span>
              </div>
            </div>
          </button>
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
    </template>
    <div class="mt-2 flex flex-wrap items-center gap-3">
      <Button v-if="atuais.length > inicial" size="sm" variant="ghost" @click="todas = !todas">
        {{ todas ? 'mostrar menos' : `ver todas (${atuais.length})` }}
      </Button>
      <p v-if="provas.length" class="text-[11px] text-muted-foreground">
        Clique na prova para ver aqui. Os originais ficam no MEGA da empresa (conta sac@makisa), pasta Fiscalização.
      </p>
    </div>

    <DialogRoot v-model:open="visualizadorAberto">
      <DialogPortal>
        <DialogOverlay class="fixed inset-0 z-[60] bg-black/70" />
        <DialogContent
          class="fixed left-1/2 top-1/2 z-[60] flex max-h-[94vh] w-[min(1100px,96vw)] -translate-x-1/2 -translate-y-1/2 flex-col rounded-lg border bg-background shadow-xl focus:outline-none"
          @keydown.left.prevent="andar(-1)"
          @keydown.right.prevent="andar(1)"
        >
          <div v-if="aberta" class="flex items-center gap-2 border-b px-4 py-2.5">
            <div class="min-w-0 flex-1">
              <DialogTitle class="truncate text-sm font-semibold">{{ aberta.tipo || 'Prova' }} · {{ aberta.nome }}</DialogTitle>
              <DialogDescription class="text-[11px] text-muted-foreground">
                {{ dataBr(aberta.enviado_em) }}<span v-if="aberta.tamanho"> · {{ tamanho(aberta.tamanho) }}</span>
                · {{ indice + 1 }} de {{ ordem.length }}
              </DialogDescription>
            </div>
            <Button size="icon" variant="ghost" class="size-8" :disabled="indice <= 0" title="anterior (←)" @click="andar(-1)">
              <ChevronLeft class="size-4" />
            </Button>
            <Button size="icon" variant="ghost" class="size-8" :disabled="indice >= ordem.length - 1" title="próxima (→)" @click="andar(1)">
              <ChevronRight class="size-4" />
            </Button>
            <template v-if="estado === 'pronto'">
              <a :href="urlProva(aberta.id)" target="_blank" rel="noopener" class="p-1.5 text-muted-foreground hover:text-primary" title="abrir em outra aba">
                <ExternalLink class="size-4" />
              </a>
              <a :href="urlProva(aberta.id, true)" class="p-1.5 text-muted-foreground hover:text-primary" title="baixar">
                <Download class="size-4" />
              </a>
            </template>
            <Button size="icon" variant="ghost" class="size-8" aria-label="fechar" @click="fechar">
              <X class="size-4" />
            </Button>
          </div>

          <div v-if="aberta" class="flex min-h-[300px] flex-1 items-center justify-center overflow-auto bg-muted/30 p-3">
            <div v-if="estado === 'pedindo'" class="flex flex-col items-center gap-2 text-sm text-muted-foreground">
              <Loader2 class="size-6 animate-spin" />
              <span>Pedindo o arquivo ao Mac mini… <span class="tabular-nums">{{ esperando }} s</span></span>
              <span v-if="esperando >= 15" class="text-xs">Vídeo grande demora um pouco mais.</span>
            </div>
            <div v-else-if="estado === 'erro'" class="max-w-md text-center text-sm">
              <p class="font-medium text-red-700 dark:text-red-400">O Mac mini não mandou o arquivo em {{ ESPERA_MAX_S }} s.</p>
              <p class="mt-1 text-muted-foreground">Pode estar desligado ou sem internet (veja a aba Robô).</p>
              <p v-if="aberta.mega_caminho" class="mt-3 text-xs text-muted-foreground">
                No MEGA: <span class="font-mono break-all">{{ aberta.mega_caminho }}</span>
              </p>
              <div class="mt-3 flex justify-center gap-2">
                <Button size="sm" variant="outline" @click="abrir(aberta)">tentar de novo</Button>
                <Button v-if="aberta.mega_caminho" size="sm" variant="ghost" @click="copiar(aberta)">copiar caminho do MEGA</Button>
              </div>
            </div>
            <template v-else>
              <img v-if="tipo === 'imagem'" :key="`i${aberta.id}-${versao}`" :src="urlProva(aberta.id)" :alt="aberta.nome" class="max-h-[80vh] max-w-full object-contain">
              <video v-else-if="tipo === 'video'" :key="`v${aberta.id}-${versao}`" :src="urlProva(aberta.id)" controls autoplay class="max-h-[80vh] max-w-full" />
              <iframe v-else-if="tipo === 'pdf'" :key="`p${aberta.id}-${versao}`" :src="urlProva(aberta.id)" :title="aberta.nome" class="h-[80vh] w-full rounded border bg-white" />
              <div v-else class="text-center text-sm">
                <p class="text-muted-foreground">Este tipo de arquivo (página salva do marketplace) não abre aqui por segurança.</p>
                <a :href="urlProva(aberta.id, true)" class="mt-3 inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 hover:border-primary">
                  <Download class="size-4" /> baixar
                </a>
              </div>
            </template>
          </div>
        </DialogContent>
      </DialogPortal>
    </DialogRoot>
  </div>
</template>
