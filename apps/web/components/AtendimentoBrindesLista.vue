<script lang="ts">
// "Brindes a adicionar (N)" (o dono, 05/11/2026): o comprador que paga não escreve
// no chat, então ninguém abriria a conversa para ver o botão. O indicador fica na
// barra da Caixa e abre a lista: loja, pedido, brinde, há quanto tempo, os botões
// (AtendimentoBrindeAcoes) e o link para a conversa quando houver. Em cima, os
// alertas dos que expiraram nas últimas 48 h. Sem nada pendente, nada aparece.
//
// GET /api/atendimento/brindes/pendentes (routers/atendimento_brinde.py): só as
// lojas da pessoa, sem Bling. Relê a cada 2 min. Este bloco é o módulo dos
// ajudantes puros, testados em tests/atendimento-brinde.cjs.
export type BrindePendente = {
  integration_id: string
  pedido: string
  numero_bling: string | null
  plataforma: string | null
  loja: string | null
  tipo: string | null
  rotulo: string | null
  sku_sugerido: string | null
  horas: number | null
  horas_restantes: number | null
  conversa_id: string | null
  estado: string | null
  erro: string | null
  pode_adicionar: boolean
  motivo: string | null
}
export type BrindeAlerta = {
  integration_id: string
  pedido: string
  plataforma: string | null
  loja: string | null
  rotulo: string | null
  horas: number | null
  conversa_id: string | null
  mensagem: string
}
export type ListaDeBrindes = { total: number; pendentes: BrindePendente[]; alertas: BrindeAlerta[] }

export const RELER_BRINDES_MS = 120_000

export function textoDoIndicador(total: number, alertas: number): string | null {
  if (total > 0) return `Brindes a adicionar (${total})`
  if (alertas > 0) return `Brindes: ${alertas} ${alertas === 1 ? 'alerta' : 'alertas'}`
  return null
}

// 0,4 → "há menos de 1 h"; 3,2 → "há 3 h".
export function haQuantoTempo(horas: number | null | undefined): string {
  if (horas === null || horas === undefined) return ''
  return horas < 1 ? 'há menos de 1 h' : `há ${Math.floor(horas)} h`
}

export function prazoDaNf(restantes: number | null | undefined): string {
  if (restantes === null || restantes === undefined) return ''
  return restantes <= 0 ? 'a NF já pode seguir' : `a NF segue em ${Math.ceil(restantes)} h`
}
</script>

<script setup lang="ts">
import { Gift, Loader2, MessagesSquare, TriangleAlert, X } from 'lucide-vue-next'
import { URL_BRINDES } from '~/components/AtendimentoBrindeAcoes.vue'

const emit = defineEmits<{ (e: 'abrirConversa', id: string): void }>()
const { api } = useApi()

const lista = ref<ListaDeBrindes>({ total: 0, pendentes: [], alertas: [] })
const aberto = ref(false)
const carregando = ref(false)
const erro = ref(false)
const texto = computed(() => textoDoIndicador(lista.value.total, lista.value.alertas.length))

async function carregar() {
  carregando.value = true
  try {
    lista.value = await api<ListaDeBrindes>(`${URL_BRINDES}/pendentes`)
    erro.value = false
  } catch {
    // Mantém o que já tinha: o indicador não pisca numa falha de rede.
    erro.value = true
  } finally {
    carregando.value = false
  }
}

let relogio: ReturnType<typeof setInterval> | null = null
onMounted(() => {
  void carregar()
  relogio = setInterval(() => {
    // Com a lista aberta não relê: o resultado do clique sumiria com a linha.
    if (!aberto.value) void carregar()
  }, RELER_BRINDES_MS)
})
onBeforeUnmount(() => {
  if (relogio) clearInterval(relogio)
})

function fechar() {
  aberto.value = false
  void carregar()
}
function abrirConversa(id: string) {
  fechar()
  emit('abrirConversa', id)
}
</script>

<template>
  <button
    v-if="texto"
    type="button"
    class="inline-flex h-8 items-center gap-1 rounded-md border px-2 text-xs font-medium"
    :class="lista.total > 0 ? 'border-amber-500/50 bg-amber-500/15 text-amber-800 hover:bg-amber-500/25 dark:text-amber-300' : 'text-muted-foreground hover:bg-muted'"
    data-brindes-indicador
    @click="aberto = true"
  >
    <Gift class="h-3.5 w-3.5" />{{ texto }}
  </button>
  <Teleport to="body">
    <div
      v-if="aberto"
      class="fixed inset-0 z-[70] flex items-stretch justify-center bg-black/50 sm:items-center sm:p-4"
      data-brindes-lista
      @click.self="fechar"
      @keydown.esc="fechar"
    >
      <div role="dialog" aria-modal="true" aria-label="Brindes a adicionar" class="flex max-h-full w-full flex-col bg-background shadow-2xl sm:max-h-[88vh] sm:max-w-2xl sm:rounded-lg sm:border">
        <header class="flex shrink-0 items-start gap-2 border-b px-4 py-3">
          <Gift class="mt-0.5 size-4 shrink-0 text-amber-600 dark:text-amber-400" />
          <div class="min-w-0 flex-1">
            <h2 class="text-sm font-semibold">Brindes a adicionar</h2>
            <p class="text-[11px] text-muted-foreground">Pedidos pagos depois da oferta. A NF espera até 12 h pelo botão; depois segue sem o brinde.</p>
          </div>
          <Loader2 v-if="carregando" class="size-4 animate-spin text-muted-foreground" />
          <button type="button" class="rounded p-1 hover:bg-muted" aria-label="fechar" @click="fechar"><X class="size-4" /></button>
        </header>
        <div class="min-h-0 flex-1 space-y-2 overflow-y-auto p-3">
          <p v-if="erro" class="text-xs text-destructive">Não consegui reler a lista agora: pode estar desatualizada.</p>
          <div
            v-for="a in lista.alertas"
            :key="`alerta-${a.integration_id}-${a.pedido}`"
            class="flex items-start gap-2 rounded-md border border-red-500/40 bg-red-500/10 p-2 text-xs"
            data-brinde-alerta
          >
            <TriangleAlert class="mt-0.5 size-3.5 shrink-0 text-red-600 dark:text-red-400" />
            <div class="min-w-0 flex-1">
              <p class="font-medium">{{ a.loja || a.plataforma || 'Loja' }} · pedido {{ a.pedido }}<template v-if="a.rotulo"> · {{ a.rotulo }}</template></p>
              <p>{{ a.mensagem }}</p>
            </div>
            <button v-if="a.conversa_id" type="button" class="inline-flex shrink-0 items-center gap-1 underline hover:text-foreground" @click="abrirConversa(a.conversa_id)">
              <MessagesSquare class="size-3.5" />Conversa
            </button>
          </div>
          <article
            v-for="p in lista.pendentes"
            :key="`${p.integration_id}-${p.pedido}`"
            class="space-y-1.5 rounded-md border p-2"
            data-brinde-linha
          >
            <div class="flex items-start gap-2 text-xs">
              <div class="min-w-0 flex-1">
                <p class="font-medium">{{ p.loja || p.plataforma || 'Loja' }} · pedido {{ p.pedido }}</p>
                <p class="text-muted-foreground">
                  {{ p.rotulo || 'Brinde' }}<template v-if="p.sku_sugerido"> ({{ p.sku_sugerido }})</template>
                  · {{ haQuantoTempo(p.horas) }} · {{ prazoDaNf(p.horas_restantes) }}
                </p>
                <p v-if="p.estado === 'incerto' && p.erro" class="text-amber-700 dark:text-amber-400">{{ p.erro }}</p>
              </div>
              <button v-if="p.conversa_id" type="button" class="inline-flex shrink-0 items-center gap-1 text-muted-foreground underline hover:text-foreground" @click="abrirConversa(p.conversa_id)">
                <MessagesSquare class="size-3.5" />Conversa
              </button>
            </div>
            <AtendimentoBrindeAcoes
              :integration-id="p.integration_id"
              :pedido="p.pedido"
              :rotulo="p.rotulo"
              :sku="p.sku_sugerido"
              :pode-adicionar="p.pode_adicionar"
              :motivo="p.motivo"
            />
          </article>
          <p v-if="!lista.pendentes.length && !lista.alertas.length" class="py-6 text-center text-xs text-muted-foreground">Nenhum brinde esperando.</p>
        </div>
      </div>
    </div>
  </Teleport>
</template>
