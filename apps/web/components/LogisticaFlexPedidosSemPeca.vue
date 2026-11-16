<script lang="ts">
export type FlexPedidoSemPeca = {
  bling_id: number
  numero: string | null
  lote?: string
  plataforma: string
  conta: string | null
  numeroloja: string | null
  prazo: string | null
  detectado_em: string
  alerta: string | null
  situacao: string | null
  skus: string[]
  // Saiu sem passar pelo .sp: o saldo Flex segue descontando até alguém
  // marcar que acertou o estoque no Bling.
  acerto_pendente: boolean
}
</script>

<script setup lang="ts">
// Quadro "Pedidos Flex sem peça" — na sub-aba Pedidos Flex da Logística.
//
// Vinicius 16/11/2026: "fica aparecendo pedido flex sem peça em São Bernardo
// na subaba anúncios flex. não consegue deixar apenas em pedidos flex essa
// informação?" — o quadro (e o "Estoque acertado") saiu de
// LogisticaFlexAnuncios e veio para cá. A lista vem da página (a mesma
// leitura que marca o aviso nas linhas: /api/flex/pedidos?so_alerta=true).
import { ref } from 'vue'
import { Check, TriangleAlert } from 'lucide-vue-next'


const props = defineProps<{
  pedidos: FlexPedidoSemPeca[]
  canEdit: boolean
  cidade: string
  lote: string
}>()
const emit = defineEmits<{
  // Mostrar este pedido na lista Pedidos Flex (busca pelo número).
  (e: 'buscar-pedido', numero: string): void
  // Marcou "Estoque acertado": a página relê a lista e os avisos.
  (e: 'mudou'): void
}>()

const { api } = useApi()
const toasts = useToasts()

function nomePlataforma(p: string | null | undefined): string {
  if (p === 'ml') return 'Mercado Livre'
  if (p === 'shopee') return 'Shopee'
  return p || '—'
}
function fmtDataHora(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' })
}
function fmtCurto(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
}
function fmtDesde(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const seg = Math.floor((Date.now() - d.getTime()) / 1000)
  if (seg < 60) return 'agora há pouco'
  if (seg < 3600) return `há ${Math.floor(seg / 60)} min`
  if (seg < 86400) return `há ${Math.floor(seg / 3600)} h`
  const dias = Math.floor(seg / 86400)
  return dias === 1 ? 'há 1 dia' : `há ${dias} dias`
}
function prazoVencido(p: FlexPedidoSemPeca): boolean {
  if (!p.prazo) return false
  const d = new Date(p.prazo)
  return !Number.isNaN(d.getTime()) && d.getTime() < Date.now()
}

const acertando = ref<Set<number>>(new Set())
async function marcarAcertado(p: FlexPedidoSemPeca) {
  if (!props.canEdit || acertando.value.has(p.bling_id)) return
  const lote = p.lote || props.lote
  const ok = confirm(
    `Pedido ${p.numero || p.bling_id}: você já acertou o estoque no Bling?\n\n` +
      `Ele saiu pelo Flex (estoque .${lote}), mas o Bling baixou outro lote. Confirme só depois ` +
      `de transferir a peça para o .${lote} no Bling — até lá o sistema desconta o pedido das peças livres de lá.`,
  )
  if (!ok) return
  acertando.value = new Set([...acertando.value, p.bling_id])
  try {
    await api(`/api/flex/pedidos/${p.bling_id}/acertado`, { method: 'POST' })
    toasts.success('Estoque acertado', `Pedido ${p.numero || p.bling_id}.`)
  } catch (e: any) {
    const d = e?.data?.detail
    toasts.error(
      'Não foi possível marcar',
      (typeof d === 'object' && d ? d.detalhe || d.code : null) || e?.message || 'erro',
    )
  } finally {
    const s = new Set(acertando.value)
    s.delete(p.bling_id)
    acertando.value = s
    emit('mudou')
  }
}
</script>

<template>
  <!-- Pedidos Flex sem peça no local de saída (aviso do robô de prioridade, etapa 2) -->
  <div id="flex-pedidos-sem-sp" class="flex min-w-0 flex-col rounded-md border bg-card">
    <div class="flex items-center justify-center gap-1.5 rounded-t-md bg-[#1f3a5f] px-3 py-1.5 text-xs font-semibold text-white">
      <TriangleAlert class="size-3.5 text-rose-300" />
      Pedidos Flex sem peça em {{ cidade }} · {{ pedidos.length }}
    </div>
    <p class="border-b px-3 py-1.5 text-center text-[11px] text-muted-foreground">
      Pedidos Flex saem de {{ cidade }}. Nestes, o estoque .{{ lote }} não tinha a peça e o sistema NÃO trocou o lote.
      Decida: separar em {{ cidade }}, transferir a peça para o .{{ lote }} ou cancelar o pedido.
      Os que já <strong>saíram</strong> sem passar pelo .{{ lote }} ficam aqui até alguém acertar o estoque no Bling
      (o Bling baixou outro lote): até lá o sistema desconta o pedido das peças livres em {{ cidade }}.
    </p>
    <div class="overflow-x-auto">
      <table class="w-full min-w-[860px] border-collapse text-[11.5px] [&_td]:text-center [&_th]:text-center">
        <thead class="bg-muted text-[10px] uppercase tracking-wide text-muted-foreground">
          <tr>
            <th class="border-b px-2 py-1 font-semibold">Pedido Bling</th>
            <th class="border-b px-2 py-1 font-semibold">Pedido na plataforma</th>
            <th class="border-b px-2 py-1 font-semibold">Conta</th>
            <th class="border-b px-2 py-1 font-semibold">SKU</th>
            <th class="border-b px-2 py-1 font-semibold" title="Prazo para despachar dado pela plataforma">Despachar até</th>
            <th class="border-b px-2 py-1 font-semibold">O que falta</th>
            <th v-if="canEdit" class="border-b px-2 py-1 font-semibold">Ação</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="p in pedidos" :key="p.bling_id" class="border-b bg-rose-50/50 align-middle dark:bg-rose-950/20">
            <td class="whitespace-nowrap px-2 py-1.5">
              <button
                v-if="p.numero"
                type="button"
                class="font-mono font-bold underline-offset-2 hover:underline"
                title="Mostrar este pedido na lista Pedidos Flex"
                @click="emit('buscar-pedido', p.numero)"
              >{{ p.numero }}</button>
              <span v-else class="text-muted-foreground">{{ p.bling_id }}</span>
              <div class="text-[10px] text-muted-foreground" :title="fmtDataHora(p.detectado_em)">visto {{ fmtDesde(p.detectado_em) }}</div>
            </td>
            <td class="whitespace-nowrap px-2 py-1.5 font-mono">{{ p.numeroloja || '—' }}</td>
            <td class="whitespace-nowrap px-2 py-1.5">{{ p.conta || '—' }}<div class="text-[10px] text-muted-foreground">{{ nomePlataforma(p.plataforma) }}</div></td>
            <td class="px-2 py-1.5 font-mono">
              <div v-for="s in p.skus" :key="s">{{ s }}</div>
              <span v-if="!p.skus.length" class="text-muted-foreground">—</span>
            </td>
            <td class="whitespace-nowrap px-2 py-1.5" :class="prazoVencido(p) ? 'font-medium text-rose-700 dark:text-rose-400' : ''">
              {{ p.prazo ? fmtCurto(p.prazo) : '—' }}
              <div v-if="prazoVencido(p)" class="text-[10px]">prazo vencido</div>
            </td>
            <td class="max-w-[380px] px-2 py-1.5">
              <div v-if="p.acerto_pendente" class="font-medium text-rose-700 dark:text-rose-400">
                Já saiu sem passar pelo .{{ p.lote || lote }} — acerte o estoque no Bling (transferência para o .{{ p.lote || lote }}).
              </div>
              <div v-if="p.alerta">{{ p.alerta }}</div>
              <span v-if="!p.alerta && !p.acerto_pendente">—</span>
            </td>
            <td v-if="canEdit" class="whitespace-nowrap px-2 py-1.5">
              <button
                v-if="p.acerto_pendente"
                type="button"
                class="inline-flex items-center gap-1 rounded border border-emerald-400 px-2 py-0.5 font-medium text-emerald-800 hover:bg-emerald-50 disabled:opacity-50 dark:border-emerald-700 dark:text-emerald-300 dark:hover:bg-emerald-950/40"
                :disabled="acertando.has(p.bling_id)"
                :title="`Já transferi a peça para o .${p.lote || lote} no Bling: parar de descontar este pedido`"
                @click="marcarAcertado(p)"
              >
                <Check class="size-3.5" /> Estoque acertado
              </button>
              <span v-else class="text-muted-foreground">—</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
