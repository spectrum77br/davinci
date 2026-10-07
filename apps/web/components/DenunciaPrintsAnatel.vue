<script setup lang="ts">
// Denúncia (07/10/2026, Vinicius: "uma forma muito eficaz de não mandar nada errado para a Anatel"): o que FOI para o SEI
// num processo — por anúncio, a miniatura do print que virou o anexo "captura do anúncio" e o link do PDF exato que foi
// anexado. Serve para uma pessoa bater o olho: print em vermelho = hoje consta como inválido (tela de captcha ou
// catálogo de outro vendedor), e aí o processo precisa de conserto (peticionamento intercorrente).
// Os arquivos ficam no Mac mini: a tela pede (POST …/preparar) e o mini manda em uns segundos, 3 de cada vez.
// Conserto (07/10): print novo juntado no MESMO processo por peticionamento intercorrente — a miniatura passa a ser a do
// print novo e o anúncio deixa de contar como "precisa de conserto".
import { onUnmounted, reactive, ref, watch } from 'vue'
import { CircleCheck, ExternalLink, FileText, Loader2, TriangleAlert } from 'lucide-vue-next'
import { type Prova, dataBr, urlProva } from '~/lib/denuncia'

type Item = {
  anuncio_id: string | null
  titulo: string | null
  loja: string | null
  marketplace: string | null
  print: Prova | null
  invalido: string | null
  pdf: Prova | null
  conserto: { pdf: Prova; print: Prova | null; em: string | null } | null
}

const props = defineProps<{ protocolo: string }>()
const { api } = useApi()

const itens = ref<Item[]>([])
const data = ref<string | null>(null)
const carregando = ref(true)
const erro = ref<string | null>(null)
// id da prova → 'pedindo' | 'pronto' | 'erro'
const estado = reactive<Record<number, string>>({})

const ESPERA_MAX_S = 90
const SIMULTANEOS = 3
let fila: number[] = []
let ativos = 0
let vivo = true

async function preparar(id: number) {
  const inicio = Date.now()
  while (vivo) {
    try {
      const r = await api<{ pronto: boolean }>(`/api/denuncia/provas/${id}/preparar`, { method: 'POST' })
      if (r.pronto) {
        estado[id] = 'pronto'
        return
      }
    } catch {
      // rede instável: tenta de novo no próximo giro
    }
    if (Date.now() - inicio > ESPERA_MAX_S * 1000) {
      estado[id] = 'erro'
      return
    }
    await new Promise((r) => setTimeout(r, 2000))
  }
}

function andarFila() {
  while (vivo && ativos < SIMULTANEOS && fila.length) {
    const id = fila.shift()!
    ativos++
    void preparar(id).finally(() => {
      ativos--
      andarFila()
    })
  }
}

async function carregar() {
  carregando.value = true
  erro.value = null
  fila = []
  try {
    const r = await api<{ data: string | null; itens: Item[] }>(
      `/api/denuncia/anatel/prints?${new URLSearchParams({ protocolo: props.protocolo })}`,
    )
    itens.value = r.itens
    data.value = r.data
    for (const it of r.itens) {
      for (const p of [it.print, it.pdf, it.conserto?.print, it.conserto?.pdf]) {
        if (p && !(p.id in estado)) {
          estado[p.id] = 'pedindo'
          fila.push(p.id)
        }
      }
    }
    andarFila()
  } catch (e: any) {
    erro.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    carregando.value = false
  }
}

watch(() => props.protocolo, carregar, { immediate: true })
onUnmounted(() => {
  vivo = false
})

const invalidos = () => itens.value.filter((i) => (i.invalido || !i.print) && !i.conserto).length
const consertados = () => itens.value.filter((i) => i.conserto).length
const miniatura = (i: Item) => i.conserto?.print || i.print
</script>

<template>
  <div class="space-y-2">
    <div v-if="carregando" class="text-sm text-muted-foreground">carregando os prints…</div>
    <div v-else-if="erro" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">{{ erro }}</div>
    <template v-else>
      <p class="text-xs text-muted-foreground">
        O que foi anexado no processo {{ protocolo }}<template v-if="data"> em {{ dataBr(data) }}</template>:
        {{ itens.length }} anúncio{{ itens.length === 1 ? '' : 's' }}.
        <span v-if="invalidos()" class="font-medium text-red-700 dark:text-red-400">
          {{ invalidos() }} com print errado ou sem print — este processo precisa de conserto.
        </span>
        <span v-else class="text-emerald-700 dark:text-emerald-400">Todos os prints constam como válidos — confira pela imagem.</span>
        <span v-if="consertados()" class="text-emerald-700 dark:text-emerald-400">
          {{ consertados() }} corrigido{{ consertados() === 1 ? '' : 's' }} por peticionamento intercorrente.
        </span>
      </p>
      <ul class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2">
        <li
          v-for="it in itens"
          :key="it.anuncio_id || ''"
          class="rounded-lg border overflow-hidden flex flex-col min-w-0"
          :class="it.conserto ? 'border-emerald-400 ring-1 ring-emerald-300' : it.invalido || !it.print ? 'border-red-400 ring-1 ring-red-300' : ''"
        >
          <a
            v-if="miniatura(it) && estado[miniatura(it)!.id] === 'pronto'"
            :href="urlProva(miniatura(it)!.id)"
            target="_blank"
            rel="noopener"
            class="block bg-muted/30"
            :title="`abrir o print de ${it.anuncio_id}`"
          >
            <img :src="urlProva(miniatura(it)!.id)" :alt="`print do anúncio ${it.anuncio_id}`" loading="lazy" class="h-40 w-full object-cover object-top">
          </a>
          <div v-else class="h-40 flex items-center justify-center bg-muted/30 text-xs text-muted-foreground text-center px-2">
            <template v-if="!miniatura(it)">sem print encontrado</template>
            <template v-else-if="estado[miniatura(it)!.id] === 'erro'">o Mac mini não mandou o arquivo (desligado?)</template>
            <span v-else class="inline-flex items-center gap-1.5"><Loader2 class="size-4 animate-spin" /> pedindo ao Mac mini…</span>
          </div>
          <div class="p-2 space-y-1 text-[11px] min-w-0">
            <div class="font-mono text-muted-foreground truncate">{{ it.anuncio_id }} · {{ it.marketplace || '' }}</div>
            <div class="truncate" :title="it.titulo || ''">{{ it.titulo || '—' }}</div>
            <div v-if="it.conserto" class="flex items-center gap-1 font-medium text-emerald-700 dark:text-emerald-400">
              <CircleCheck class="size-3.5 shrink-0" />
              <span class="truncate">corrigido por intercorrente<template v-if="it.conserto.em"> em {{ dataBr(it.conserto.em) }}</template></span>
            </div>
            <div v-if="it.invalido" class="flex items-center gap-1 font-medium" :class="it.conserto ? 'text-muted-foreground line-through' : 'text-red-700 dark:text-red-400'">
              <TriangleAlert class="size-3.5 shrink-0" /> <span class="truncate" :title="it.invalido">{{ it.invalido.replace('Captura inválida ', 'inválido ') }}</span>
            </div>
            <div class="flex items-center gap-2">
              <a
                v-if="it.pdf && estado[it.pdf.id] === 'pronto'"
                :href="urlProva(it.pdf.id)"
                target="_blank"
                rel="noopener"
                class="inline-flex items-center gap-1 text-primary hover:underline"
                title="o PDF exato que foi anexado no SEI"
              >
                <FileText class="size-3.5" /> PDF enviado <ExternalLink class="size-3" />
              </a>
              <span v-else-if="it.pdf" class="text-muted-foreground">PDF enviado…</span>
              <span v-else class="text-muted-foreground">sem a cópia do PDF</span>
              <a
                v-if="it.conserto && estado[it.conserto.pdf.id] === 'pronto'"
                :href="urlProva(it.conserto.pdf.id)"
                target="_blank"
                rel="noopener"
                class="inline-flex items-center gap-1 text-primary hover:underline"
                title="o PDF juntado no processo pelo peticionamento intercorrente"
              >
                <FileText class="size-3.5" /> PDF do conserto <ExternalLink class="size-3" />
              </a>
            </div>
          </div>
        </li>
      </ul>
    </template>
  </div>
</template>
