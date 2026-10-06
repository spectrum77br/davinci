<script setup lang="ts">
// App Uranyx › Fila do SAC (06/10/2026, spec 7.6): chamados abertos no app
// que ainda não chegaram ao SAC do site (na fila, enviando ou que falharam).
// "Reenviar" põe de volta na fila com tentativa agora e uma janela nova de
// 24 h; a data que o cliente vê no app não muda.
import { Inbox, RefreshCw, Send } from 'lucide-vue-next'
import { Button } from '~/components/ui/button'
import { useAppUranyx } from '~/composables/useAppUranyx'
import {
  CATEGORIA_LABEL, dataHora, erroAppUranyx, ESTADO_FILA_LABEL, rotulo, TABS_APP_URANYX,
  type Categoria, type ItemFila,
} from '~/lib/appUranyx'

definePageMeta({ middleware: ['admin', 'app-uranyx'] })

const { chamar, indisponivel } = useAppUranyx()
const toasts = useToasts()

const itens = ref<ItemFila[]>([])
const carregando = ref(false)
const erro = ref<string | null>(null)
const estado = ref<'' | 'na_fila' | 'enviando' | 'falhou'>('')
const atrasados = ref(false)
const reenviando = ref<string | null>(null)

let seq = 0
async function carregar() {
  const meu = ++seq
  carregando.value = true
  erro.value = null
  try {
    const query: Record<string, string | boolean> = {}
    if (estado.value) query.estado = estado.value
    if (atrasados.value) query.atrasados = true
    const r = await chamar<ItemFila[]>('fila', { query })
    if (meu === seq) itens.value = r
  } catch (e: any) {
    if (meu === seq) erro.value = erroAppUranyx(e, 'Não deu para carregar a fila').texto
  } finally {
    if (meu === seq) carregando.value = false
  }
}
onMounted(carregar)
watch([estado, atrasados], carregar)

const contagem = computed(() => ({
  falhou: itens.value.filter((i) => i.estado === 'falhou').length,
  atrasados: itens.value.filter((i) => i.atrasado_24h).length,
}))

function corEstado(e: string) {
  if (e === 'falhou') return 'pill-danger'
  if (e === 'enviando') return 'pill-info'
  return 'pill-warning'
}

async function reenviar(item: ItemFila) {
  if (reenviando.value) return
  reenviando.value = item.id
  try {
    const r = await chamar<ItemFila>(`fila/${item.id}/reenviar`, { method: 'POST' })
    itens.value = itens.value.map((x) => (x.id === r.id ? r : x))
    toasts.success('De volta à fila', `${item.produto_nome || 'Chamado'}: a próxima tentativa é agora.`)
  } catch (e: any) {
    toasts.error('Não deu para reenviar', erroAppUranyx(e, 'Não deu para reenviar').texto)
  } finally {
    reenviando.value = null
  }
}
</script>

<template>
  <div class="space-y-4">
    <RouteTabs :tabs="TABS_APP_URANYX" />
    <PageHeader
      title="Fila do SAC"
      description="Chamados abertos no app que ainda não chegaram ao SAC do site. Os que passam de 24 h sem chegar ficam marcados."
    >
      <template #actions>
        <Button size="sm" variant="ghost" :disabled="carregando" @click="carregar">
          <RefreshCw class="mr-1 size-4" :class="{ 'animate-spin': carregando }" /> atualizar
        </Button>
      </template>
    </PageHeader>

    <AppUranyxIndisponivel v-if="indisponivel" :mensagem="indisponivel" :carregando="carregando" @tentar="carregar" />

    <template v-else>
      <div class="flex flex-wrap items-center gap-2">
        <select v-model="estado" class="h-9 rounded-md border bg-background px-2 text-sm" aria-label="Situação">
          <option value="">Situação: todas</option>
          <option value="na_fila">Na fila</option>
          <option value="enviando">Enviando</option>
          <option value="falhou">Falhou</option>
        </select>
        <label class="flex items-center gap-2 text-sm">
          <input v-model="atrasados" type="checkbox" class="size-4" /> só os atrasados (mais de 24 h)
        </label>
        <span class="ml-auto text-sm text-muted-foreground">
          {{ itens.length }} na lista
          <template v-if="contagem.falhou"> · {{ contagem.falhou }} {{ contagem.falhou === 1 ? 'falhou' : 'falharam' }}</template>
          <template v-if="contagem.atrasados"> · {{ contagem.atrasados }} {{ contagem.atrasados === 1 ? 'atrasado' : 'atrasados' }}</template>
        </span>
      </div>

      <div v-if="erro" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300">{{ erro }}</div>

      <div class="table-card overflow-x-auto">
        <table class="w-full text-sm">
          <thead>
            <tr>
              <th>Chamado</th>
              <th>Situação</th>
              <th class="whitespace-nowrap">Último erro</th>
              <th />
            </tr>
          </thead>
          <tbody>
            <tr v-if="carregando && !itens.length">
              <td colspan="4" class="py-10 text-center text-muted-foreground">Carregando…</td>
            </tr>
            <tr v-else-if="!itens.length">
              <td colspan="4" class="py-10 text-center text-muted-foreground">
                <Inbox class="mx-auto mb-2 size-6 opacity-50" />
                Nada na fila: todos os chamados chegaram ao SAC.
              </td>
            </tr>
            <tr v-for="i in itens" :key="i.id">
              <td>
                <div class="font-medium">{{ i.produto_nome || 'Produto' }}</div>
                <div class="text-xs text-muted-foreground">
                  {{ i.categoria ? (CATEGORIA_LABEL[i.categoria as Categoria] ?? i.categoria) : '' }}
                  <template v-if="i.pedido_bling"> · pedido {{ i.pedido_bling }}</template>
                  <template v-if="i.protocolo"> · {{ i.protocolo }}</template>
                  · enviado {{ dataHora(i.enviado_em || i.criado_em) }}
                </div>
                <NuxtLink v-if="i.usuario_id" :to="{ path: '/app-uranyx/contas', query: { conta: i.usuario_id } }" class="text-xs text-primary underline-offset-2 hover:underline">
                  ver conta
                </NuxtLink>
              </td>
              <td>
                <div class="flex flex-wrap gap-1">
                  <span :class="corEstado(i.estado)">{{ rotulo(ESTADO_FILA_LABEL, i.estado) }}</span>
                  <span v-if="i.atrasado_24h" class="pill-danger" title="Mais de 24 h sem chegar ao SAC">+24 h</span>
                  <span v-if="i.conta_teste" class="pill-info">teste</span>
                </div>
                <div class="mt-0.5 whitespace-nowrap text-xs text-muted-foreground">
                  {{ i.tentativas }} {{ i.tentativas === 1 ? 'tentativa' : 'tentativas' }}
                  <template v-if="i.proxima_tentativa_em"> · próxima {{ dataHora(i.proxima_tentativa_em) }}</template>
                </div>
              </td>
              <td class="min-w-[10rem] max-w-[22rem]">
                <span class="line-clamp-2 break-all text-xs text-muted-foreground" :title="i.ultimo_erro || ''">{{ i.ultimo_erro || '—' }}</span>
              </td>
              <td class="whitespace-nowrap text-right">
                <button
                  class="btn btn-sm"
                  :disabled="i.estado === 'enviando' || reenviando === i.id"
                  :title="i.estado === 'enviando' ? 'Está sendo enviado agora. Espere um pouco.' : 'Pôr de volta na fila, com tentativa agora'"
                  @click="reenviar(i)"
                >
                  <Send class="mr-1 size-3.5" /> {{ reenviando === i.id ? 'Reenviando…' : 'Reenviar' }}
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</template>
