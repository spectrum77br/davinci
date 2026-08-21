<script setup lang="ts">
import { Loader2, X } from 'lucide-vue-next'

// Espelho de SaldoRaioXOut / SaldoRaioXItem (apps/api/app/schemas/margens.py).
type RaioXItem = {
  sku: string | null
  produto: string | null
  quantidade: number | null
  proporcao: number | null
  bling_valorbase: number | null
  bling_custofrete: number | null
  bling_taxacomissao: number | null
  saldo_bling: number | null
  saldo_plataforma: number | null
}

type RaioX = {
  pedido_bling: string
  pedido_marketplace: string | null
  plataforma: string | null
  conta: string | null
  data: string | null
  situacao: string | null
  bling_valorbase: number | null
  bling_custofrete: number | null
  bling_taxacomissao: number | null
  saldo_bling: number | null
  mp_valor_bruto: number | null
  mp_taxas: number | null
  mp_frete: number | null
  mp_rebate: number | null
  mp_desconto: number | null
  mp_reembolso: number | null
  mp_imposto: number | null
  mp_ajuste: number | null
  mp_liquido: number | null
  mp_atualizado_em: string | null
  saldo_plataforma: number | null
  projecao_amazon: boolean
  proj_frete_projetado: number | null
  proj_comissao_frac: number | null
  reembolso_ajustes: number | null
  diferenca: number | null
  itens: RaioXItem[]
}

const props = defineProps<{
  open: boolean
  pedido: string
}>()

const emit = defineEmits<{ (e: 'close'): void }>()

const { api } = useApi()

const loading = ref(false)
const errorMsg = ref<string | null>(null)
const data = ref<RaioX | null>(null)

watch(() => props.open, (open) => {
  if (open) {
    data.value = null
    errorMsg.value = null
    fetchDetalhe()
  }
})

async function fetchDetalhe() {
  loading.value = true
  try {
    data.value = await api<RaioX>(
      `/api/margens/marketplace/saldo-detalhe/${encodeURIComponent(props.pedido)}`,
    )
  } catch (e: any) {
    errorMsg.value = e?.data?.detail?.code === 'pedido_nao_encontrado'
      ? 'pedido não encontrado no snapshot da margem'
      : (e?.data?.detail?.message || e?.message || 'erro ao carregar o raio-x do saldo')
  } finally {
    loading.value = false
  }
}

function brl(v: number | null | undefined) {
  if (v == null) return '—'
  return v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })
}

function fmtDate(v: string | null) {
  if (!v) return '—'
  const [y, m, d] = v.slice(0, 10).split('-')
  if (!y || !m || !d) return v
  return `${d}/${m}/${y}`
}

function fmtDateTime(v: string | null) {
  if (!v) return null
  const dt = new Date(v)
  if (Number.isNaN(dt.getTime())) return v
  return dt.toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' })
}

function pct(frac: number | null | undefined) {
  if (frac == null) return '—'
  return `${(frac * 100).toLocaleString('pt-BR', { maximumFractionDigits: 2 })}%`
}

// Componentes do repasse, na mesma ordem da fórmula da view:
// bruto − taxas − frete + rebate − desconto − reembolso − imposto + ajuste = líquido.
// Taxas e frete aparecem sempre que informados (zero é informação); os demais
// só quando têm valor, para não poluir.
const mpComponentes = computed(() => {
  const d = data.value
  if (!d || d.mp_valor_bruto == null) return []
  const linhas: { label: string; valor: number }[] = [
    { label: 'Valor bruto da venda', valor: d.mp_valor_bruto },
  ]
  if (d.mp_taxas != null) linhas.push({ label: '− Taxas da plataforma', valor: d.mp_taxas })
  if (d.mp_frete != null) linhas.push({ label: '− Frete cobrado', valor: d.mp_frete })
  if (d.mp_rebate) linhas.push({ label: '+ Rebate (subsídio)', valor: d.mp_rebate })
  if (d.mp_desconto) linhas.push({ label: '− Desconto', valor: d.mp_desconto })
  if (d.mp_reembolso) linhas.push({ label: '− Reembolso', valor: d.mp_reembolso })
  if (d.mp_imposto) linhas.push({ label: '− Imposto retido', valor: d.mp_imposto })
  if (d.mp_ajuste) linhas.push({ label: '+ Ajuste', valor: d.mp_ajuste })
  return linhas
})

// Quando a plataforma manda o líquido pronto (net_amount), ele pode não bater
// exatamente com a soma dos componentes — o resto vira "outros lançamentos".
const mpOutros = computed(() => {
  const d = data.value
  if (!d || d.mp_liquido == null || d.mp_valor_bruto == null) return null
  const soma = d.mp_valor_bruto
    - (d.mp_taxas ?? 0)
    - (d.mp_frete ?? 0)
    + (d.mp_rebate ?? 0)
    - (d.mp_desconto ?? 0)
    - (d.mp_reembolso ?? 0)
    - (d.mp_imposto ?? 0)
    + (d.mp_ajuste ?? 0)
  const resto = d.mp_liquido - soma
  return Math.abs(resto) > 0.01 ? resto : null
})

const comissaoProjetada = computed(() => {
  const d = data.value
  if (!d || !d.projecao_amazon || d.bling_valorbase == null || d.proj_comissao_frac == null) {
    return null
  }
  return d.bling_valorbase * d.proj_comissao_frac
})

const diverge = computed(() => {
  const d = data.value
  return d?.diferenca != null && Math.abs(d.diferenca) > 0.01
})
</script>

<template>
  <div
    v-if="open"
    class="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4"
    @click.self="emit('close')"
  >
    <div class="bg-background border rounded-lg w-full max-w-2xl p-5 space-y-4 max-h-[90vh] overflow-y-auto">
      <div class="flex items-start">
        <div>
          <h2 class="text-lg font-semibold">Raio-X do saldo — pedido {{ pedido }}</h2>
          <p v-if="data" class="text-xs text-muted-foreground">
            <span class="uppercase">{{ data.plataforma || '—' }}</span>
            <template v-if="data.conta"> · {{ data.conta }}</template>
            <template v-if="data.pedido_marketplace"> · mkt {{ data.pedido_marketplace }}</template>
            · {{ fmtDate(data.data) }}
            <template v-if="data.situacao"> · {{ data.situacao }}</template>
          </p>
        </div>
        <Button class="ml-auto" size="sm" variant="ghost" @click="emit('close')">
          <X class="size-4" />
        </Button>
      </div>

      <div v-if="loading" class="py-8 text-center text-sm text-muted-foreground">
        <Loader2 class="size-4 inline animate-spin mr-1.5" /> carregando…
      </div>
      <div v-else-if="errorMsg" class="py-8 text-center text-sm text-red-400">{{ errorMsg }}</div>

      <template v-else-if="data">
        <div class="grid gap-3 sm:grid-cols-2 text-sm">
          <!-- Lado Bling: o pedido de venda como está gravado lá. -->
          <div class="rounded-md border p-3 space-y-1.5">
            <p class="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
              Pedido no Bling
            </p>
            <div class="flex justify-between gap-3">
              <span>Valor base da venda</span>
              <span class="tabular-nums">{{ brl(data.bling_valorbase) }}</span>
            </div>
            <div class="flex justify-between gap-3 text-muted-foreground">
              <span>− Frete lançado</span>
              <span class="tabular-nums">{{ brl(data.bling_custofrete ?? 0) }}</span>
            </div>
            <div class="flex justify-between gap-3 text-muted-foreground">
              <span>− Taxa / comissão lançada</span>
              <span class="tabular-nums">{{ brl(data.bling_taxacomissao ?? 0) }}</span>
            </div>
            <div class="flex justify-between gap-3 border-t pt-1.5 font-semibold">
              <span>= Saldo Bling</span>
              <span class="tabular-nums text-emerald-700 dark:text-emerald-400">{{ brl(data.saldo_bling) }}</span>
            </div>
          </div>

          <!-- Lado plataforma: o repasse que o marketplace informou. -->
          <div class="rounded-md border p-3 space-y-1.5">
            <p class="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
              Repasse da plataforma
            </p>

            <!-- Repasse real informado. -->
            <template v-if="data.mp_liquido != null && mpComponentes.length">
              <div
                v-for="(l, i) in mpComponentes"
                :key="l.label"
                class="flex justify-between gap-3"
                :class="i > 0 ? 'text-muted-foreground' : ''"
              >
                <span>{{ l.label }}</span>
                <span class="tabular-nums">{{ brl(l.valor) }}</span>
              </div>
              <div v-if="mpOutros != null" class="flex justify-between gap-3 text-muted-foreground">
                <span>± Outros lançamentos</span>
                <span class="tabular-nums">{{ brl(mpOutros) }}</span>
              </div>
              <div class="flex justify-between gap-3 border-t pt-1.5 font-semibold">
                <span>= Saldo Plataforma</span>
                <span class="tabular-nums text-emerald-700 dark:text-emerald-400">{{ brl(data.saldo_plataforma) }}</span>
              </div>
              <p v-if="fmtDateTime(data.mp_atualizado_em)" class="text-[11px] text-muted-foreground">
                financeiro atualizado em {{ fmtDateTime(data.mp_atualizado_em) }}
              </p>
            </template>

            <!-- Só o líquido, sem abertura de componentes. -->
            <template v-else-if="data.mp_liquido != null">
              <p class="text-xs text-muted-foreground">
                A plataforma informou só o valor líquido, sem detalhar taxas e frete.
              </p>
              <div class="flex justify-between gap-3 border-t pt-1.5 font-semibold">
                <span>= Saldo Plataforma</span>
                <span class="tabular-nums text-emerald-700 dark:text-emerald-400">{{ brl(data.saldo_plataforma) }}</span>
              </div>
            </template>

            <!-- Amazon pré-envio: número da tela é projeção. -->
            <template v-else-if="data.projecao_amazon">
              <p class="inline-block rounded bg-amber-100 dark:bg-amber-900/40 px-1.5 py-0.5 text-[11px] font-medium text-amber-700 dark:text-amber-300">
                Projeção — a Amazon ainda não informou o repasse real
              </p>
              <div class="flex justify-between gap-3">
                <span>Valor base (Bling)</span>
                <span class="tabular-nums">{{ brl(data.bling_valorbase) }}</span>
              </div>
              <div class="flex justify-between gap-3 text-muted-foreground">
                <span>− Frete projetado</span>
                <span class="tabular-nums">{{ brl(data.proj_frete_projetado) }}</span>
              </div>
              <div class="flex justify-between gap-3 text-muted-foreground">
                <span>− Comissão da conta ({{ pct(data.proj_comissao_frac) }})</span>
                <span class="tabular-nums">{{ brl(comissaoProjetada) }}</span>
              </div>
              <div class="flex justify-between gap-3 border-t pt-1.5 font-semibold">
                <span>= Saldo projetado</span>
                <span class="tabular-nums text-emerald-700 dark:text-emerald-400">{{ brl(data.saldo_plataforma) }}</span>
              </div>
            </template>

            <!-- Nada ainda. -->
            <p v-else class="text-sm text-amber-600 dark:text-amber-400">
              A plataforma ainda não informou o repasse deste pedido. Assim que o
              financeiro chegar, o Saldo Plataforma aparece aqui e na tela.
            </p>
          </div>
        </div>

        <!-- Comparação dos dois lados. -->
        <div class="rounded-md border p-3 text-sm space-y-1">
          <div v-if="data.diferenca != null" class="flex justify-between gap-3 font-medium">
            <span>Diferença (Bling − Plataforma)</span>
            <span
              class="tabular-nums"
              :class="diverge ? 'text-amber-600 dark:text-amber-400' : 'text-emerald-700 dark:text-emerald-400'"
            >
              {{ brl(data.diferenca) }}<template v-if="!diverge"> · os dois lados batem</template>
            </span>
          </div>
          <p v-else class="text-muted-foreground">
            Sem os dois lados ainda não dá para comparar.
          </p>
          <div v-if="data.reembolso_ajustes" class="flex justify-between gap-3 text-muted-foreground">
            <span>Reembolsos / ajustes já lançados (coluna Reembolsos)</span>
            <span class="tabular-nums">{{ brl(data.reembolso_ajustes) }}</span>
          </div>
        </div>

        <!-- Pack: rateio por item. -->
        <div v-if="data.itens.length > 1" class="space-y-1.5">
          <p class="text-xs text-muted-foreground">
            Pedido com {{ data.itens.length }} itens — o repasse chega por pedido e é
            rateado pelo custo de cada item (se faltar custo cadastrado, pelo preço):
          </p>
          <table class="w-full text-xs">
            <thead>
              <tr class="border-b text-muted-foreground">
                <th class="py-1 pr-2 text-left font-semibold">SKU</th>
                <th class="py-1 px-2 text-right font-semibold">Qtd</th>
                <th class="py-1 px-2 text-right font-semibold">Proporção</th>
                <th class="py-1 px-2 text-right font-semibold">Saldo Bling</th>
                <th class="py-1 pl-2 text-right font-semibold">Saldo Plataforma</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="it in data.itens" :key="it.sku ?? ''" class="border-b last:border-0">
                <td class="py-1 pr-2 font-mono" :title="it.produto || ''">{{ it.sku || '—' }}</td>
                <td class="py-1 px-2 text-right tabular-nums">{{ it.quantidade ?? '—' }}</td>
                <td class="py-1 px-2 text-right tabular-nums">{{ pct(it.proporcao) }}</td>
                <td class="py-1 px-2 text-right tabular-nums">{{ brl(it.saldo_bling) }}</td>
                <td class="py-1 pl-2 text-right tabular-nums">{{ brl(it.saldo_plataforma) }}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <p class="text-[11px] text-muted-foreground">
          Mesmos números da tela — a foto vem do snapshot da margem, sem consultar
          o Bling nem a plataforma agora.
        </p>
      </template>
    </div>
  </div>
</template>
