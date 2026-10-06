<script setup lang="ts">
// App Uranyx › Exceções (06/10/2026): o que o catálogo não cobre.
//  - "Vendido e sem produto no catálogo" (GET skus-sem-mapa): quem comprou
//    esses códigos não vê nada no app. Ligar a um produto (PUT
//    catalogo/{id}/skus/{codigo}) ou ignorar com motivo (PUT skus-ignorados).
//  - Conflitos, linhas desconhecidas e SKUs do site que o DaVinci não
//    conhece: o relatório da última cópia do site.
//  - Ignorados, com "desfazer".
import { Ban, Link2, RefreshCw, Undo2 } from 'lucide-vue-next'
import { Button } from '~/components/ui/button'
import { useAppUranyx } from '~/composables/useAppUranyx'
import {
  CATEGORIA_LABEL, codigoNoCaminho, dataCurta, dataHora, erroAppUranyx, linhasDoErro, TABS_APP_URANYX,
  type Categoria, type ProdutoCatalogo, type RelatorioSync, type SkuIgnorado, type SkuSemMapa, type SkusSemMapa,
} from '~/lib/appUranyx'

definePageMeta({ middleware: ['admin', 'app-uranyx'] })

const { chamar, ultimoRelatorio, indisponivel } = useAppUranyx()
const toasts = useToasts()

const dias = ref(365)
const semMapa = ref<SkusSemMapa | null>(null)
const ignorados = ref<SkuIgnorado[]>([])
const produtos = ref<ProdutoCatalogo[]>([])
const relatorio = ref<RelatorioSync | null>(null)
const carregando = ref(false)
const erro = ref<string | null>(null)

let seq = 0
async function carregar() {
  const meu = ++seq
  carregando.value = true
  erro.value = null
  try {
    const [sm, ig, cat] = await Promise.all([
      chamar<SkusSemMapa>('skus-sem-mapa', { query: { dias: dias.value } }),
      chamar<SkuIgnorado[]>('skus-ignorados'),
      chamar<ProdutoCatalogo[]>('catalogo'),
    ])
    if (meu !== seq) return
    semMapa.value = sm
    ignorados.value = ig
    produtos.value = cat
    relatorio.value = await ultimoRelatorio().catch(() => null)
  } catch (e: any) {
    if (meu !== seq) return
    erro.value = erroAppUranyx(e, 'Não deu para carregar as exceções').texto
  } finally {
    if (meu === seq) carregando.value = false
  }
}
onMounted(carregar)
watch(dias, carregar)

// Produtos para o "ligar", agrupados por categoria (ativos primeiro).
const grupos = computed(() => {
  const ordem: Categoria[] = ['celular', 'acessorio', 'eletrodomestico']
  return ordem
    .map((c) => ({
      categoria: c,
      label: CATEGORIA_LABEL[c],
      itens: produtos.value
        .filter((p) => p.categoria === c)
        .sort((a, b) => Number(b.ativo) - Number(a.ativo) || a.nome.localeCompare(b.nome, 'pt-BR')),
    }))
    .filter((g) => g.itens.length)
})

// ─── ações por linha (uma aberta por vez) ───────────────────────────────────
const aberta = ref<{ codigo: string; acao: 'ligar' | 'ignorar' } | null>(null)
const produtoId = ref('')
const cor = ref('')
const motivo = ref('')
const salvando = ref(false)

function abrirAcao(item: SkuSemMapa, acao: 'ligar' | 'ignorar') {
  if (aberta.value?.codigo === item.codigo_base && aberta.value.acao === acao) {
    aberta.value = null
    return
  }
  aberta.value = { codigo: item.codigo_base, acao }
  produtoId.value = ''
  cor.value = ''
  motivo.value = ''
}

function tirarDaLista(codigo: string) {
  if (semMapa.value) semMapa.value.itens = semMapa.value.itens.filter((x) => x.codigo_base !== codigo)
  aberta.value = null
}

async function ligar(item: SkuSemMapa) {
  if (!produtoId.value || salvando.value) return
  salvando.value = true
  try {
    const c = cor.value.trim()
    const p = await chamar<ProdutoCatalogo>(`catalogo/${produtoId.value}/skus/${codigoNoCaminho(item.codigo_base)}`, {
      method: 'PUT',
      body: c ? { cor: c } : {},
    })
    const i = produtos.value.findIndex((x) => x.id === p.id)
    if (i >= 0) produtos.value.splice(i, 1, p)
    tirarDaLista(item.codigo_base)
    toasts.success('Código ligado', `${item.codigo_base} → ${p.nome}. Quem comprou já vê o produto no app.`)
  } catch (e: any) {
    const er = erroAppUranyx(e, 'Não deu para ligar')
    toasts.error('Não deu para ligar', [er.texto, ...linhasDoErro(er)])
  } finally {
    salvando.value = false
  }
}

async function ignorar(item: SkuSemMapa) {
  const m = motivo.value.trim()
  if (m.length < 2 || salvando.value) return
  salvando.value = true
  try {
    const r = await chamar<SkuIgnorado>(`skus-ignorados/${codigoNoCaminho(item.codigo_base)}`, {
      method: 'PUT',
      body: { motivo: m },
    })
    ignorados.value = [...ignorados.value.filter((x) => x.codigo_base !== r.codigo_base), r]
      .sort((a, b) => a.codigo_base.localeCompare(b.codigo_base))
    tirarDaLista(item.codigo_base)
    toasts.success('Código ignorado', `${item.codigo_base}: ${m}`)
  } catch (e: any) {
    const er = erroAppUranyx(e, 'Não deu para ignorar')
    toasts.error('Não deu para ignorar', [er.texto, ...linhasDoErro(er)])
  } finally {
    salvando.value = false
  }
}

const desfazendo = ref<string | null>(null)
async function desfazer(ig: SkuIgnorado) {
  if (!window.confirm(`Voltar a mostrar ${ig.codigo_base} em "vendido e sem produto"?`)) return
  desfazendo.value = ig.codigo_base
  try {
    await chamar(`skus-ignorados/${codigoNoCaminho(ig.codigo_base)}`, { method: 'DELETE' })
    ignorados.value = ignorados.value.filter((x) => x.codigo_base !== ig.codigo_base)
    toasts.success('Desfeito', `${ig.codigo_base} volta para a lista na próxima atualização.`)
  } catch (e: any) {
    toasts.error('Não deu para desfazer', erroAppUranyx(e, 'Não deu para desfazer').texto)
  } finally {
    desfazendo.value = null
  }
}
</script>

<template>
  <div class="space-y-4">
    <RouteTabs :tabs="TABS_APP_URANYX" />
    <PageHeader
      title="Exceções do catálogo"
      description="Códigos vendidos que não têm produto no app (quem comprou não vê nada), conflitos da cópia do site e o que foi ignorado."
    >
      <template #actions>
        <select v-model.number="dias" class="h-9 rounded-md border bg-background px-2 text-sm" aria-label="Período das vendas">
          <option :value="30">Vendas: 30 dias</option>
          <option :value="90">Vendas: 90 dias</option>
          <option :value="180">Vendas: 180 dias</option>
          <option :value="365">Vendas: 1 ano</option>
          <option :value="730">Vendas: 2 anos</option>
        </select>
        <Button size="sm" variant="ghost" :disabled="carregando" @click="carregar">
          <RefreshCw class="mr-1 size-4" :class="{ 'animate-spin': carregando }" /> atualizar
        </Button>
      </template>
    </PageHeader>

    <AppUranyxIndisponivel v-if="indisponivel" :mensagem="indisponivel" :carregando="carregando" @tentar="carregar" />

    <template v-else>
      <div v-if="erro" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300">{{ erro }}</div>

      <section class="space-y-2">
        <div class="flex flex-wrap items-baseline gap-2">
          <h2 class="text-sm font-semibold">Vendido e sem produto no catálogo</h2>
          <span v-if="semMapa" class="text-xs text-muted-foreground">
            {{ semMapa.itens.length }} {{ semMapa.itens.length === 1 ? 'código vendido' : 'códigos vendidos' }} desde {{ dataCurta(semMapa.desde) }}
          </span>
        </div>
        <p v-if="semMapa?.truncado" class="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300">
          Muitas vendas no período: a lista foi cortada. Diminua o período para ver tudo.
        </p>
        <div class="table-card overflow-x-auto">
          <table class="w-full text-sm">
            <thead>
              <tr>
                <th class="whitespace-nowrap">Código-base</th>
                <th>Exemplo vendido</th>
                <th class="text-right">Unidades</th>
                <th class="whitespace-nowrap">Última venda</th>
                <th />
              </tr>
            </thead>
            <tbody>
              <tr v-if="carregando && !semMapa">
                <td colspan="5" class="py-8 text-center text-muted-foreground">Carregando…</td>
              </tr>
              <tr v-else-if="semMapa && !semMapa.itens.length">
                <td colspan="5" class="py-8 text-center text-muted-foreground">Tudo o que foi vendido no período tem produto no app.</td>
              </tr>
              <template v-for="item in semMapa?.itens ?? []" :key="item.codigo_base">
                <tr>
                  <td class="whitespace-nowrap"><code class="font-mono text-xs">{{ item.codigo_base }}</code></td>
                  <td>
                    <div class="font-mono text-xs text-muted-foreground">{{ item.sku_exemplo }}</div>
                    <div class="max-w-[20rem] truncate" :title="item.descricao_exemplo || ''">{{ item.descricao_exemplo || '—' }}</div>
                  </td>
                  <td class="text-right tabular-nums">{{ item.quantidade.toLocaleString('pt-BR') }}</td>
                  <td class="whitespace-nowrap">{{ dataCurta(item.ultima_venda) }}</td>
                  <td>
                    <div class="flex flex-wrap justify-end gap-1">
                      <button class="btn btn-sm whitespace-nowrap" :class="{ 'btn-primary': aberta?.codigo === item.codigo_base && aberta.acao === 'ligar' }" @click="abrirAcao(item, 'ligar')">
                        <Link2 class="mr-1 size-3.5" /> Ligar a um produto
                      </button>
                      <button class="btn btn-sm whitespace-nowrap" :class="{ 'btn-primary': aberta?.codigo === item.codigo_base && aberta.acao === 'ignorar' }" @click="abrirAcao(item, 'ignorar')">
                        <Ban class="mr-1 size-3.5" /> Ignorar
                      </button>
                    </div>
                  </td>
                </tr>
                <tr v-if="aberta?.codigo === item.codigo_base">
                  <td colspan="5" class="bg-muted/30">
                    <form v-if="aberta.acao === 'ligar'" class="flex flex-wrap items-end gap-2" @submit.prevent="ligar(item)">
                      <label class="flex flex-col gap-1 text-xs">
                        <span class="text-muted-foreground">Produto do catálogo</span>
                        <select v-model="produtoId" class="h-9 w-72 max-w-full rounded-md border bg-background px-2 text-sm" required>
                          <option value="" disabled>Escolha o produto…</option>
                          <optgroup v-for="g in grupos" :key="g.categoria" :label="g.label">
                            <option v-for="p in g.itens" :key="p.id" :value="p.id">{{ p.nome }}{{ p.ativo ? '' : ' (inativo)' }}</option>
                          </optgroup>
                        </select>
                      </label>
                      <label class="flex flex-col gap-1 text-xs">
                        <span class="text-muted-foreground">Cor (opcional)</span>
                        <input v-model="cor" maxlength="60" class="h-9 w-36 rounded-md border bg-background px-2 text-sm" placeholder="ex.: Preto" />
                      </label>
                      <Button type="submit" size="sm" :disabled="!produtoId || salvando">{{ salvando ? 'Ligando…' : `Ligar ${item.codigo_base}` }}</Button>
                      <Button type="button" size="sm" variant="ghost" @click="aberta = null">Cancelar</Button>
                    </form>
                    <form v-else class="flex flex-wrap items-end gap-2" @submit.prevent="ignorar(item)">
                      <label class="flex flex-col gap-1 text-xs">
                        <span class="text-muted-foreground">Motivo (fica registrado)</span>
                        <input v-model="motivo" minlength="2" maxlength="200" required class="h-9 w-80 max-w-full rounded-md border bg-background px-2 text-sm" placeholder="ex.: brinde, não é produto Uranyx" />
                      </label>
                      <Button type="submit" size="sm" :disabled="motivo.trim().length < 2 || salvando">{{ salvando ? 'Salvando…' : 'Ignorar' }}</Button>
                      <Button type="button" size="sm" variant="ghost" @click="aberta = null">Cancelar</Button>
                    </form>
                  </td>
                </tr>
              </template>
            </tbody>
          </table>
        </div>
      </section>

      <AppUranyxRelatorioSync :relatorio="relatorio" so-pendencias />

      <section class="space-y-2">
        <h2 class="text-sm font-semibold">Ignorados ({{ ignorados.length }})</h2>
        <p v-if="!ignorados.length" class="text-sm text-muted-foreground">Nenhum código ignorado.</p>
        <ul v-else class="divide-y rounded-xl border bg-card">
          <li v-for="ig in ignorados" :key="ig.codigo_base" class="flex flex-wrap items-center gap-2 px-3 py-2 text-sm">
            <code class="font-mono text-xs">{{ ig.codigo_base }}</code>
            <span class="text-muted-foreground">{{ ig.motivo || 'sem motivo' }}</span>
            <span v-if="ig.criado_em" class="text-xs text-muted-foreground">· {{ dataHora(ig.criado_em) }}</span>
            <button class="btn btn-sm ml-auto" :disabled="desfazendo === ig.codigo_base" @click="desfazer(ig)">
              <Undo2 class="mr-1 size-3.5" /> desfazer
            </button>
          </li>
        </ul>
      </section>
    </template>
  </div>
</template>
