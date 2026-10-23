<script setup lang="ts">
// Contabilidade › Compra › "Compras feitas" (23/10/2026): as compras registradas,
// por mês e situação. Daqui sai a planilha do Bling (uma compra ou várias da
// MESMA vendedora — cada planilha é importada no Bling de uma empresa), anota-se
// o nº da NF que saiu lá, edita-se (sem NF), cancela, reabre e exclui.
import { computed, ref, watch } from 'vue'
import {
  Ban, ClipboardList, Download, FilePen, FileX2, Hash, Loader2, RotateCcw, Trash2, Undo2,
} from 'lucide-vue-next'
import { Button } from '~/components/ui/button'
import { useToasts } from '~/composables/useToasts'
import {
  erroApi, fmtBrl, fmtDoc, fmtMes, mesParaData, plural, useNfseApi, useNfseTela, type MenuItem,
} from '~/lib/nfse'
import {
  ROTULO_STATUS, fmtNcm, fmtQtd, nomeDaPlanilha, type Compra, type StatusCompra,
} from '~/lib/compra'

const props = defineProps<{ versao: number; destacar: string | null }>()
const emit = defineEmits<{ (e: 'editar', c: Compra): void }>()

const tela = useNfseTela()
const { api, baixarArquivo } = useNfseApi()
const toasts = useToasts()

const mes = ref('') // '' = todos os meses
const status = ref<'todas' | StatusCompra>('todas')
const compras = ref<Compra[]>([])
const carregando = ref(false)
const carregou = ref(false)
const erro = ref<string | null>(null)
const ocupado = ref<string | null>(null) // id da compra com ação rodando ('lote' = planilha das marcadas)
const marcadas = ref<Set<string>>(new Set())

let seq = 0
async function carregar() {
  const s = ++seq
  carregando.value = true
  erro.value = null
  try {
    const query: Record<string, string> = {}
    if (mes.value) query.competencia = mesParaData(mes.value)
    if (status.value !== 'todas') query.status = status.value
    const r = await api<Compra[]>('/api/nfse/compras', { query })
    if (s !== seq) return
    compras.value = r
    carregou.value = true
    const ids = new Set(r.map((c) => c.id))
    marcadas.value = new Set([...marcadas.value].filter((id) => ids.has(id)))
  } catch (e) {
    if (s === seq) erro.value = erroApi(e)
  } finally {
    if (s === seq) carregando.value = false
  }
}

watch([mes, status, () => props.versao], carregar, { immediate: true })

const OPCOES_STATUS = [
  { id: 'todas', rotulo: 'Todas' },
  { id: 'registrada', rotulo: 'Sem NF' },
  { id: 'nf_emitida', rotulo: 'NF emitida' },
  { id: 'cancelada', rotulo: 'Canceladas' },
]
const statusModel = computed({
  get: () => status.value as string,
  set: (v: string) => {
    status.value = (['registrada', 'nf_emitida', 'cancelada'].includes(v) ? v : 'todas') as typeof status.value
  },
})

const totalVisivel = computed(() =>
  compras.value.filter((c) => c.status !== 'cancelada').reduce((s, c) => s + Number(c.total), 0),
)

// --- marcar várias para uma planilha só ------------------------------------------------

// só compra sem NF: a com NF já está no Bling (número repetido = o Bling recusa o arquivo todo)
const marcaveis = computed(() => compras.value.filter((c) => c.status === 'registrada'))
const listaMarcadas = computed(() => compras.value.filter((c) => marcadas.value.has(c.id)))
const vendedorasMarcadas = computed(() => new Set(listaMarcadas.value.map((c) => c.vendedor_cnpj)))
const marcacao = computed(() => {
  const n = listaMarcadas.value.length
  if (!n) return 'nenhuma'
  return n === marcaveis.value.length ? 'todas' : 'algumas'
})

function alternar(c: Compra) {
  const s = new Set(marcadas.value)
  if (s.has(c.id)) s.delete(c.id)
  else s.add(c.id)
  marcadas.value = s
}
function marcarTodas() {
  marcadas.value = marcacao.value === 'todas' ? new Set() : new Set(marcaveis.value.map((c) => c.id))
}

async function planilhaDasMarcadas() {
  const lista = listaMarcadas.value
  if (!lista.length || ocupado.value) return
  if (vendedorasMarcadas.value.size > 1) {
    toasts.warning('Marque compras de uma empresa que vende só', [
      'Cada planilha é importada no Bling de uma empresa: baixe uma por vendedora.',
    ])
    return
  }
  ocupado.value = 'lote'
  try {
    const ok = await baixarArquivo('/api/nfse/compras/planilha', nomeDaPlanilha(lista), {
      method: 'POST',
      body: { ids: lista.map((c) => c.id) },
    })
    if (ok) toasts.success(`Planilha com ${plural(lista.length, 'compra', 'compras')} baixada`)
  } finally {
    ocupado.value = null
  }
}

// --- ações de uma compra ------------------------------------------------------------------

function itensDoMenu(c: Compra): MenuItem[] {
  const itens: MenuItem[] = []
  if (c.status !== 'cancelada' && tela.canEdit.value) {
    itens.push({ id: 'planilha', rotulo: 'baixar planilha do Bling', icone: Download })
  }
  if (tela.canEdit.value) {
    if (c.status === 'registrada') {
      itens.push({ id: 'nf', rotulo: 'anotar a NF que saiu', icone: Hash })
      itens.push({ id: 'editar', rotulo: 'editar', icone: FilePen })
      itens.push({ id: 'cancelar', rotulo: 'cancelar a compra', icone: Ban, perigo: true, separar: true })
    } else if (c.status === 'nf_emitida') {
      itens.push({ id: 'nf', rotulo: 'trocar o nº da NF', icone: Hash })
      itens.push({ id: 'desfazer', rotulo: 'desfazer a NF', icone: Undo2, separar: true })
    } else {
      itens.push({ id: 'reabrir', rotulo: 'reabrir', icone: RotateCcw })
    }
  }
  if (tela.canDelete.value && c.status !== 'nf_emitida') {
    itens.push({ id: 'excluir', rotulo: 'excluir', icone: Trash2, perigo: true, separar: true })
  }
  return itens
}

function trocar(c: Compra) {
  const i = compras.value.findIndex((x) => x.id === c.id)
  if (i >= 0) compras.value.splice(i, 1, c)
  if (c.status !== 'registrada' && marcadas.value.has(c.id)) {
    const s = new Set(marcadas.value)
    s.delete(c.id)
    marcadas.value = s
  }
  if (status.value !== 'todas' && c.status !== status.value) carregar()
}

async function rodar(c: Compra, fazer: () => Promise<void>, erroTitulo: string) {
  if (ocupado.value) return
  ocupado.value = c.id
  try {
    await fazer()
  } catch (e) {
    toasts.error(erroTitulo, erroApi(e))
  } finally {
    ocupado.value = null
  }
}

async function escolher(c: Compra, acao: string) {
  const nome = `a compra nº ${c.numero}`
  if (acao === 'planilha') {
    await rodar(c, async () => {
      await baixarArquivo(`/api/nfse/compras/${c.id}/planilha`, nomeDaPlanilha([c]))
    }, 'Não deu para baixar a planilha')
  } else if (acao === 'nf') {
    abrirNf(c)
  } else if (acao === 'editar') {
    emit('editar', c)
  } else if (acao === 'cancelar') {
    const ok = await tela.confirmar({
      titulo: `Cancelar ${nome}?`,
      texto: 'Ela continua na lista como cancelada e não gera mais planilha. Dá para reabrir depois.',
      tom: 'perigo',
      botao: 'Cancelar a compra',
    })
    if (ok) await rodar(c, async () => trocar(await api<Compra>(`/api/nfse/compras/${c.id}/cancelar`, { method: 'POST' })), 'Não deu para cancelar')
  } else if (acao === 'reabrir') {
    await rodar(c, async () => trocar(await api<Compra>(`/api/nfse/compras/${c.id}/reabrir`, { method: 'POST' })), 'Não deu para reabrir')
  } else if (acao === 'desfazer') {
    const ok = await tela.confirmar({
      titulo: `Desfazer a NF ${c.nf_numero} da compra nº ${c.numero}?`,
      texto: 'A compra volta para "sem NF" (aqui no DaVinci). Se a NF foi emitida de verdade, cancele-a no Bling de quem vendeu.',
      botao: 'Desfazer',
    })
    if (ok) await rodar(c, async () => trocar(await api<Compra>(`/api/nfse/compras/${c.id}/desfazer-nf`, { method: 'POST' })), 'Não deu para desfazer a NF')
  } else if (acao === 'excluir') {
    const ok = await tela.confirmar({
      titulo: `Excluir ${nome}?`,
      texto: 'Ela some da lista. Não dá para desfazer.',
      tom: 'perigo',
      botao: 'Excluir',
    })
    if (ok) {
      await rodar(c, async () => {
        await api(`/api/nfse/compras/${c.id}`, { method: 'DELETE' })
        compras.value = compras.value.filter((x) => x.id !== c.id)
        toasts.success(`Compra nº ${c.numero} excluída`)
      }, 'Não deu para excluir')
    }
  }
}

// --- anotar a NF ----------------------------------------------------------------------------

const nfAberta = ref<Compra | null>(null)
const nfNumero = ref('')
const nfSalvando = ref(false)
const nfErro = ref<string | null>(null)

function abrirNf(c: Compra) {
  nfAberta.value = c
  nfNumero.value = c.nf_numero ?? ''
  nfErro.value = null
}

async function salvarNf() {
  const c = nfAberta.value
  if (!c) return
  const n = nfNumero.value.replace(/\D/g, '')
  if (!n) {
    nfErro.value = 'Digite o número da NF (só números).'
    return
  }
  nfSalvando.value = true
  try {
    const r = await api<Compra>(`/api/nfse/compras/${c.id}/nf`, { method: 'POST', body: { nf_numero: n } })
    trocar(r)
    toasts.success(`Compra nº ${r.numero}: NF ${r.nf_numero} anotada`)
    nfAberta.value = null
  } catch (e) {
    nfErro.value = erroApi(e)
  } finally {
    nfSalvando.value = false
  }
}

function resumoItens(c: Compra): string {
  return c.itens.map((i) => `${fmtQtd(i.quantidade)} × ${i.nome}`).join(' + ')
}
function fmtData(iso: string): string {
  const [y, m, d] = iso.split('-')
  return d && m && y ? `${d}/${m}/${y}` : iso
}
</script>

<template>
  <div class="space-y-4">
    <div class="flex flex-wrap items-end gap-3">
      <NfseMesPicker v-model="mes" nome="Mês do faturamento" permitir-todos />
      <NfseSegmentado v-model="statusModel" :opcoes="OPCOES_STATUS" tamanho="sm" />
      <div class="ml-auto flex flex-wrap items-center gap-2">
        <span v-if="listaMarcadas.length" class="text-sm text-muted-foreground">
          {{ plural(listaMarcadas.length, 'marcada', 'marcadas') }}
        </span>
        <Button
          v-if="tela.canEdit.value"
          size="sm"
          variant="outline"
          :disabled="!listaMarcadas.length || !!ocupado"
          :title="vendedorasMarcadas.size > 1 ? 'marque compras de uma vendedora só' : 'uma planilha com as compras marcadas'"
          @click="planilhaDasMarcadas"
        >
          <Loader2 v-if="ocupado === 'lote'" class="mr-1.5 size-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
          <Download v-else class="mr-1.5 size-4" aria-hidden="true" />
          planilha das marcadas
        </Button>
      </div>
    </div>

    <NfseAviso v-if="erro" tom="perigo" titulo="Não carregou as compras">
      {{ erro }}
      <template #acoes>
        <Button size="sm" variant="outline" :disabled="carregando" @click="carregar()">tentar de novo</Button>
      </template>
    </NfseAviso>

    <NfseSkeletonTabela v-if="!carregou && carregando" :linhas="4" :colunas="6" />

    <EmptyState
      v-else-if="carregou && !compras.length"
      :icon="ClipboardList"
      title="Nenhuma compra aqui"
      :description="mes ? `Nada registrado com o faturamento de ${fmtMes(mes)}.` : 'As compras registradas em Fazer compra aparecem aqui.'"
    />

    <div v-else-if="carregou" class="table-card tabela-nfse">
      <div class="relative overflow-x-auto">
        <table class="w-full">
          <thead>
            <tr>
              <th class="w-10">
                <input
                  type="checkbox"
                  class="size-4 rounded align-middle accent-primary disabled:opacity-40 dark:[color-scheme:dark]"
                  :checked="marcacao === 'todas'"
                  :indeterminate="marcacao === 'algumas'"
                  :disabled="!marcaveis.length"
                  aria-label="marcar todas as compras da lista"
                  @change="marcarTodas"
                />
              </th>
              <th class="whitespace-nowrap">Nº</th>
              <th>Quem compra → de quem</th>
              <th class="hidden lg:table-cell">O que</th>
              <th class="hidden whitespace-nowrap xl:table-cell">Faturamento</th>
              <th class="!text-right">Total</th>
              <th>Situação</th>
              <th class="col-acoes w-px"><span class="sr-only">Ações</span></th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="c in compras"
              :key="c.id"
              :class="[
                marcadas.has(c.id) && 'linha-marcada bg-primary/5',
                destacar === c.id && 'bg-emerald-50/60 dark:bg-emerald-950/20',
              ]"
            >
              <td class="w-10">
                <input
                  type="checkbox"
                  class="size-4 rounded align-middle accent-primary disabled:opacity-40 dark:[color-scheme:dark]"
                  :checked="marcadas.has(c.id)"
                  :disabled="c.status !== 'registrada'"
                  :title="c.status === 'nf_emitida' ? 'já tem NF: o pedido já está no Bling' : undefined"
                  :aria-label="`marcar a compra nº ${c.numero}`"
                  @change="alternar(c)"
                />
              </td>
              <td class="whitespace-nowrap">
                <div class="font-medium tabular-nums">{{ c.numero }}</div>
                <div class="text-xs text-muted-foreground tabular-nums">{{ fmtData(c.data_pedido) }}</div>
              </td>
              <td class="min-w-[220px]">
                <div class="font-medium" :class="c.status === 'cancelada' && 'text-muted-foreground line-through'">
                  {{ c.comprador.apelido || c.comprador.razao_social }}
                  <span class="font-normal text-muted-foreground">→ {{ c.vendedor_nome }}</span>
                </div>
                <div class="text-xs text-muted-foreground">
                  {{ fmtDoc(c.comprador.cnpj) }} · CFOP {{ c.cfop }}
                  <template v-if="c.criado_por"> · por {{ c.criado_por }}</template>
                </div>
              </td>
              <td class="hidden min-w-[180px] lg:table-cell">
                <div class="text-sm">{{ resumoItens(c) }}</div>
                <div class="text-xs text-muted-foreground">
                  NCM {{ [...new Set(c.itens.map((i) => fmtNcm(i.ncm)))].join(', ') }}
                </div>
              </td>
              <td class="hidden whitespace-nowrap xl:table-cell">
                <div class="text-sm tabular-nums">{{ c.faturamento != null ? fmtBrl(c.faturamento) : '—' }}</div>
                <div class="text-xs text-muted-foreground">
                  {{ fmtMes(c.competencia.slice(0, 7)) }}<template v-if="c.modo === 'percentual' && c.percentual">
                    · {{ String(Number(c.percentual)).replace('.', ',') }}%</template>
                </div>
              </td>
              <td class="whitespace-nowrap !text-right font-medium tabular-nums">{{ fmtBrl(c.total) }}</td>
              <td class="whitespace-nowrap">
                <span :class="ROTULO_STATUS[c.status].classe">{{ ROTULO_STATUS[c.status].texto }}</span>
                <div v-if="c.nf_numero" class="mt-0.5 text-xs text-muted-foreground tabular-nums">NF {{ c.nf_numero }}</div>
              </td>
              <td class="col-acoes">
                <Loader2 v-if="ocupado === c.id" class="mx-2 size-4 animate-spin text-muted-foreground motion-reduce:animate-none" aria-label="trabalhando" />
                <NfseMenu v-else :itens="itensDoMenu(c)" :disabled="!!ocupado" :rotulo="`ações da compra nº ${c.numero}`" @escolher="(a: string) => escolher(c, a)" />
              </td>
            </tr>
          </tbody>
          <tfoot>
            <tr>
              <td colspan="8" class="text-right text-sm text-muted-foreground">
                {{ plural(compras.filter((c) => c.status !== 'cancelada').length, 'compra', 'compras') }} (sem as canceladas):
                <span class="ml-1 text-base font-semibold text-foreground tabular-nums">{{ fmtBrl(totalVisivel) }}</span>
              </td>
            </tr>
          </tfoot>
        </table>
      </div>
    </div>

    <NfseDialog
      :open="!!nfAberta"
      :titulo="nfAberta ? `NF da compra nº ${nfAberta.numero}` : 'NF da compra'"
      descricao="O número da NF que saiu no Bling de quem vendeu."
      tamanho="sm"
      :icone="nfAberta?.nf_numero ? FileX2 : Hash"
      :fechavel="!nfSalvando"
      foco-inicial="#compra-nf-numero"
      @update:open="(v: boolean) => { if (!v) nfAberta = null }"
    >
      <form id="compra-nf" class="space-y-2" @submit.prevent="salvarNf">
        <label for="compra-nf-numero" class="text-sm font-medium">Número da NF</label>
        <input
          id="compra-nf-numero"
          v-model="nfNumero"
          type="text"
          inputmode="numeric"
          autocomplete="off"
          maxlength="20"
          class="h-9 w-full rounded-md border bg-background px-3 text-sm tabular-nums focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          :class="nfErro && 'border-red-500 dark:border-red-400'"
        />
        <p v-if="nfErro" class="text-xs text-red-600 dark:text-red-400">{{ nfErro }}</p>
      </form>
      <template #rodape>
        <Button type="button" variant="outline" size="sm" :disabled="nfSalvando" @click="nfAberta = null">Voltar</Button>
        <Button type="submit" form="compra-nf" size="sm" :disabled="nfSalvando">
          <Loader2 v-if="nfSalvando" class="mr-1.5 size-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
          salvar
        </Button>
      </template>
    </NfseDialog>
  </div>
</template>
